"""
voiceover.py — Voice clone TTS + background music mixing.

Uses ElevenLabs REST API directly (no pydub dependency).
Hardcoded to Bruno's voice clone. Mixes in background music track.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger("video_pipeline.voiceover")

# ─────────────────────────────────────────────────────────────────────────────
# Bruno's voice clone - hardcoded
VOICE_ID     = "LuwDkt2zTAKrsCGMW681"
DEFAULT_MODEL = "eleven_turbo_v2_5"
VOICE_SPEED   = 0.95     # Shorts: slightly faster, more energy
PAUSE_MS      = 400
QUOTA_BUFFER  = 2000

# Background music volume relative to voice (0.0 = silent, 1.0 = same level)
BGM_VOLUME = 0.12   # 12% — subtle, doesn't compete with voice


def _get_ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def check_quota(api_key: str) -> dict:
    import requests
    resp = requests.get(
        "https://api.elevenlabs.io/v1/user/subscription",
        headers={"xi-api-key": api_key}, timeout=10,
    )
    resp.raise_for_status()
    d = resp.json()
    limit = d.get("character_limit", 0)
    used  = d.get("character_count", 0)
    return {
        "tier": d.get("tier", "?"),
        "remaining": max(0, limit - used),
        "limit": limit,
    }


def generate_voiceover(
    segments:    list[dict],
    output_path: Path,
    voice_id:    Optional[str] = None,
    model_id:    str           = DEFAULT_MODEL,
    bgm_path:    Optional[Path] = None,
) -> float:
    """
    Generate voiceover with voice clone + optional background music.
    Returns duration in seconds.
    """
    import requests

    api_key = os.environ.get("ELEVEN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        log.warning("  No ElevenLabs key — falling back to gTTS")
        return _generate_gtts(segments, output_path)

    vid = voice_id or VOICE_ID
    est_chars = sum(len(s.get("narration", "")) for s in segments)

    # Quota check
    try:
        quota = check_quota(api_key)
        remaining = quota["remaining"]
        log.info(f"  ElevenLabs quota: {remaining:,} chars remaining "
                 f"(need ~{est_chars:,}, tier={quota['tier']})")
        if remaining < est_chars + QUOTA_BUFFER:
            log.warning("  Quota too low — falling back to gTTS")
            return _generate_gtts(segments, output_path)
    except Exception as e:
        log.warning(f"  Quota check failed ({e})")

    ffmpeg = _get_ffmpeg()
    tmp = Path(tempfile.mkdtemp(prefix="tts_"))
    seg_files = []

    # Create silence for pauses
    silence = tmp / "silence.mp3"
    subprocess.run([
        ffmpeg, "-y", "-f", "lavfi", "-i",
        f"anullsrc=r=44100:cl=mono",
        "-t", f"{PAUSE_MS / 1000:.3f}",
        "-c:a", "libmp3lame", "-b:a", "128k", str(silence),
    ], capture_output=True, check=True)

    # Generate each segment
    for i, seg in enumerate(segments):
        text = seg.get("narration", "").strip()
        if not text:
            continue

        log.info(f"  TTS segment {i+1}/{len(segments)}: "
                 f"{seg.get('coin', '?')} ({len(text)} chars)")

        seg_path = tmp / f"seg_{i:03d}.mp3"
        try:
            resp = requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
                headers={
                    "xi-api-key": api_key,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                json={
                    "text": text,
                    "model_id": model_id,
                    "voice_settings": {
                        "stability": 0.55,
                        "similarity_boost": 0.75,
                        "style": 0.0,
                        "use_speaker_boost": True,
                        "speed": VOICE_SPEED,
                    },
                },
                timeout=60,
            )
            resp.raise_for_status()
            seg_path.write_bytes(resp.content)
            seg_files.append(seg_path)
            log.info(f"    ✓ {len(resp.content):,} bytes")
        except Exception as e:
            log.error(f"    ✗ {e}")

    if not seg_files:
        raise ValueError("No audio segments generated")

    # Concatenate voice segments
    voice_only = tmp / "voice_only.mp3"
    concat_file = tmp / "concat.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for i, sf in enumerate(seg_files):
            f.write(f"file '{sf.name}'\n")
            if i < len(seg_files) - 1:
                f.write(f"file '{silence.name}'\n")

    subprocess.run([
        ffmpeg, "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(voice_only),
    ], capture_output=True, check=True, cwd=str(tmp))

    # Mix with background music if provided
    if bgm_path and bgm_path.exists():
        log.info(f"  Mixing background music ({bgm_path.name} at {BGM_VOLUME*100:.0f}%)...")
        _mix_bgm(ffmpeg, voice_only, bgm_path, output_path)
    else:
        # Just copy voice-only as final output
        import shutil
        shutil.copy2(str(voice_only), str(output_path))

    duration = _get_duration(ffmpeg, output_path)
    log.info(f"  Total audio: {duration:.1f}s ({len(seg_files)} segments)")

    # ── Write per-segment durations sidecar (for compose.py sync) ───────────
    # Maps the audio file to a JSON list of per-segment durations so that
    # compose.py can keep each image on screen for its actual narration time
    # instead of dividing total duration evenly across all frames.
    try:
        import json as _json
        seg_durations = []
        for sf in seg_files:
            d = _get_duration(ffmpeg, sf)
            seg_durations.append(round(d, 3))
        sidecar_path = output_path.with_suffix(".durations.json")
        sidecar_path.write_text(_json.dumps({
            "total_duration":      round(duration, 3),
            "segment_durations":   seg_durations,
            "segment_count":       len(seg_files),
        }, indent=2), encoding="utf-8")
        log.info(f"  Per-segment durations: {sidecar_path.name} "
                 f"({len(seg_durations)} segments)")
    except Exception as e:
        log.warning(f"  Could not write durations sidecar: {e}")

    # Cleanup
    import shutil
    shutil.rmtree(str(tmp), ignore_errors=True)

    return duration


def _mix_bgm(ffmpeg: str, voice_path: Path, bgm_path: Path, output_path: Path):
    """Mix voice with background music, looping BGM to match voice length."""
    voice_dur = _get_duration(ffmpeg, voice_path)

    # Mix: voice at full volume, BGM at BGM_VOLUME, loop BGM, trim to voice length
    subprocess.run([
        ffmpeg, "-y",
        "-i", str(voice_path),
        "-stream_loop", "-1", "-i", str(bgm_path),
        "-filter_complex",
        f"[0:a]volume=1.0[voice];"
        f"[1:a]volume={BGM_VOLUME},afade=t=in:st=0:d=2,afade=t=out:st={voice_dur-3}:d=3[music];"
        f"[voice][music]amix=inputs=2:duration=first:dropout_transition=3[out]",
        "-map", "[out]",
        "-c:a", "libmp3lame", "-b:a", "192k",
        "-t", str(voice_dur),
        str(output_path),
    ], capture_output=True, check=True)


def _get_duration(ffmpeg: str, path: Path) -> float:
    result = subprocess.run(
        [ffmpeg, "-i", str(path)],
        capture_output=True, text=True, check=False,
    )
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", result.stderr)
    if m:
        h, mi, s, cs = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        return h * 3600 + mi * 60 + s + cs / 100.0
    return 30.0


def _generate_gtts(segments, output_path):
    """Fallback: Google TTS."""
    from gtts import gTTS
    ffmpeg = _get_ffmpeg()
    tmp = Path(tempfile.mkdtemp(prefix="gtts_"))
    seg_files = []
    for i, seg in enumerate(segments):
        text = seg.get("narration", "").strip()
        if not text:
            continue
        p = tmp / f"seg_{i:03d}.mp3"
        gTTS(text=text, lang="en", slow=False).save(str(p))
        seg_files.append(p)
    if not seg_files:
        raise ValueError("No segments")
    concat_file = tmp / "concat.txt"
    with open(concat_file, "w") as f:
        for sf in seg_files:
            f.write(f"file '{sf.name}'\n")
    subprocess.run([
        ffmpeg, "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c:a", "libmp3lame", "-b:a", "128k",
        str(output_path),
    ], capture_output=True, check=True, cwd=str(tmp))
    dur = _get_duration(ffmpeg, output_path)
    import shutil
    shutil.rmtree(str(tmp), ignore_errors=True)
    return dur
