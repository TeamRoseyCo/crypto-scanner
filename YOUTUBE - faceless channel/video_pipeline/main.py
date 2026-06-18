"""
================================================================================
VIDEO PIPELINE  v2.1  —  Scanner Data → YouTube Video (fully automated)
================================================================================
Upgrades vs v2.0:
  - Owner comment posted automatically after upload (auto-highlighted)
  - Summary is now passed through to upload step so the comment can
    reference today's regime, BTC, and confluence setups

Run:
  python main.py                          # full pipeline → YouTube
  python main.py --no-upload              # generate video, skip upload
  python main.py --preview                # script only, no video
  python main.py --landscape              # 1920×1080 instead of 9:16 Shorts
  python main.py --bgm path/to/music.mp3  # custom background music

Env vars:
  GEMINI_API_KEY        — script generation (free)
  ELEVEN_API_KEY        — voice clone TTS
  YOUTUBE_CLIENT_SECRET — path to OAuth2 client_secret.json
================================================================================
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')
import time
from datetime import datetime
from pathlib import Path

from ingest import load_scanner_data, build_market_summary
from scriptgen import generate_script
from voiceover import generate_voiceover
from visuals import render_all_frames
from compose import compose_video
from thumbnail import generate_thumbnail
from upload import upload_to_youtube, get_youtube_credentials

# ─────────────────────────────────────────────────────────────────────────────
# Resilience helpers (added by patch_upload_resilience.py)
# ─────────────────────────────────────────────────────────────────────────────
import json as _json_resilience
import subprocess as _subprocess_resilience
from datetime import datetime as _datetime_resilience
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

# Upload hard timeout. Yesterday's successful upload completed in ~80s
# end-to-end. 5 minutes is generous headroom for slow connections without
# letting a hang waste an entire morning.
_UPLOAD_TIMEOUT_SECONDS = 300

# Status sentinel location — outputs/last_upload_status.json relative to
# the project root. Resolved from main.py's location:
#   .../crypto-scanner/YOUTUBE - faceless channel/video_pipeline/main.py
#   .../crypto-scanner/outputs/last_upload_status.json
_STATUS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "outputs"
    / "last_upload_status.json"
)


def _write_status_sentinel(
    success: bool,
    step_failed: str = "",
    video_url: str = "",
    error_message: str = "",
) -> None:
    """
    Drop a JSON sentinel describing how the most recent pipeline run ended.
    Survives across runs (overwritten each time), so a quick PowerShell
    check tells you instantly whether yesterday's task worked:

        Get-Content outputs\\last_upload_status.json | ConvertFrom-Json
    """
    payload = {
        "success":       success,
        "timestamp":     _datetime_resilience.now().isoformat(timespec="seconds"),
        "step_failed":   step_failed,
        "video_url":     video_url,
        "error_message": error_message,
    }
    try:
        _STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATUS_PATH.write_text(
            _json_resilience.dumps(payload, indent=2),
            encoding="utf-8",
        )
    except Exception:
        # Never let sentinel-write failure mask the real error
        pass


def _notify_desktop(title: str, body: str) -> None:
    """
    Fire a Windows desktop notification. Tries BurntToast (modern toast),
    falls back to msg.exe (built-in, ugly but reliable). Silent if both
    fail — sentinel file is the authoritative source of truth.
    """
    # Sanitize for PowerShell single-quote string — only quote escaping matters
    safe_title = title.replace("'", "''")
    safe_body  = body.replace("'", "''")

    # BurntToast (preferred — looks like a real Windows notification)
    ps_burnt = (
        "if (Get-Module -ListAvailable -Name BurntToast) { "
        "Import-Module BurntToast; "
        f"New-BurntToastNotification -Text '{safe_title}', '{safe_body}'; "
        "exit 0 } else { exit 1 }"
    )
    try:
        result = _subprocess_resilience.run(
            ["powershell", "-NoProfile", "-Command", ps_burnt],
            capture_output=True, timeout=10, check=False,
        )
        if result.returncode == 0:
            return
    except Exception:
        pass

    # Fallback: msg.exe (built into Windows, always present)
    try:
        _subprocess_resilience.run(
            ["msg", "*", "/TIME:60", f"{title}\n{body}"],
            capture_output=True, timeout=5, check=False,
        )
    except Exception:
        pass  # nothing more we can do — sentinel file still got written


def _run_upload_with_timeout(
    upload_fn,
    video_path,
    script,
    credentials,
    thumbnail_path,
    timeout_seconds: int = _UPLOAD_TIMEOUT_SECONDS,
):
    """
    Run upload_to_youtube() with a hard timeout. Windows can't use
    signal.alarm(), so a worker thread + future.result(timeout=...) is
    the portable approach. NOTE: the worker thread can't be killed if it
    hangs (Python has no thread-kill primitive), but the main process
    exits cleanly and the OS reaps it on task termination.
    """
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            upload_fn,
            video_path=video_path,
            script=script,
            credentials=credentials,
            thumbnail_path=thumbnail_path,
        )
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeoutError:
            raise TimeoutError(
                f"Upload exceeded {timeout_seconds}s — likely a hung OAuth "
                f"consent prompt or stalled network transfer."
            )
# ─────────────────────────────────────────────────────────────────────────────
# End resilience helpers
# ─────────────────────────────────────────────────────────────────────────────



# ─────────────────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────────────────
_THIS_DIR     = Path(__file__).resolve().parent
_YT_DIR       = _THIS_DIR.parent                         # YOUTUBE - faceless channel/
_PROJECT_ROOT = _YT_DIR.parent                            # crypto-scanner/

_SCANNER_OUT  = _PROJECT_ROOT / "outputs" / "scanner-results"
_VIDEO_OUT    = _YT_DIR / "Videos"
_VOICE_OUT    = _YT_DIR / "Voice-Overs"
_SCRIPT_OUT   = _YT_DIR / "Video Scripts"
_FRAMES_OUT   = _YT_DIR / "Images for Videos"
_BGM_DIR      = _YT_DIR / "Content"   # look for background music here
_LOG_DIR      = _PROJECT_ROOT / "outputs" / "logs"

for d in (_VIDEO_OUT, _VOICE_OUT, _SCRIPT_OUT, _FRAMES_OUT, _LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
log = logging.getLogger("video_pipeline")
if not log.handlers:
    hf = logging.FileHandler(
        _LOG_DIR / f"video_pipeline_{datetime.now().strftime('%Y%m%d')}.log",
        encoding="utf-8",
    )
    hf.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    hs = logging.StreamHandler(sys.stdout)
    hs.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(hf)
    log.addHandler(hs)
    log.setLevel(logging.INFO)


def _find_bgm(explicit_path: str = None) -> Path | None:
    """Find background music file."""
    if explicit_path:
        p = Path(explicit_path)
        if p.exists():
            return p

    # Auto-detect from Content folder
    for ext in ("*.mp3", "*.wav", "*.m4a"):
        candidates = list(_BGM_DIR.glob(ext)) if _BGM_DIR.exists() else []
        # Look for files with 'bgm', 'background', 'music', 'lofi' in name
        for c in candidates:
            name_lower = c.stem.lower()
            if any(kw in name_lower for kw in ("bgm", "background", "music", "lofi", "ambient")):
                return c
        # If no keyword match, use first audio file found
        if candidates:
            return candidates[0]

    return None


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run(
    no_upload:  bool  = False,
    preview:    bool  = False,
    landscape:  bool  = False,
    voice_id:   str   = None,
    bgm_path:   str   = None,
) -> dict:
    t0 = time.time()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    size = (1920, 1080) if landscape else (1080, 1920)

    log.info("=" * 64)
    log.info("VIDEO PIPELINE v2.1")
    log.info(f"  Format: {'landscape 16:9' if landscape else 'Shorts 9:16'}")
    log.info(f"  Upload: {'disabled' if no_upload else 'enabled'}")
    log.info("=" * 64)

    # ── Step 1: Ingest ───────────────────────────────────────────────────────
    log.info("\n[1/6] Ingesting scanner data...")
    raw_data = load_scanner_data(_SCANNER_OUT)
    summary  = build_market_summary(raw_data)
    log.info(f"  Regime: {summary.get('regime', '?')}")
    btc_p = summary.get('btc_price')
    log.info(f"  BTC: ${btc_p:,.0f}" if btc_p else "  BTC: unknown")
    log.info(f"  Top movers: {len(summary.get('top_coins', []))}")

    if not summary.get("top_coins"):
        log.warning("  No coins to feature. Aborting.")
        return {"error": "no_data"}

    # ── Step 2: Script ───────────────────────────────────────────────────────
    log.info("\n[2/6] Generating narration script...")
    script = generate_script(summary, landscape=landscape)
    log.info(f"  Title: {script.get('title', '?')}")
    log.info(f"  Segments: {len(script.get('segments', []))}")
    log.info(f"  Tags: {', '.join(script.get('tags', [])[:5])}")

    if preview:
        log.info("\n── PREVIEW MODE ──")
        print(json.dumps(script, indent=2))
        return {"script": script}

    script_path = _SCRIPT_OUT / f"script_{ts}.json"
    script_path.write_text(json.dumps(script, indent=2), encoding="utf-8")

    # ── Step 3: Voiceover ────────────────────────────────────────────────────
    log.info("\n[3/6] Generating voiceover (voice clone)...")
    audio_path = _VOICE_OUT / f"audio_{ts}.mp3"

    # Find background music
    bgm = _find_bgm(bgm_path)
    if bgm:
        log.info(f"  Background music: {bgm.name}")
    else:
        log.info("  No background music found (add an MP3 to Content/ folder)")

    audio_duration = generate_voiceover(
        script["segments"],
        output_path=audio_path,
        voice_id=voice_id,
        bgm_path=bgm,
    )
    log.info(f"  Audio: {audio_path.name} ({audio_duration:.1f}s)")

    # ── Step 4: Frames ───────────────────────────────────────────────────────
    log.info("\n[4/6] Rendering visual frames...")
    frames_dir = _FRAMES_OUT / f"frames_{ts}"
    frames_dir.mkdir(exist_ok=True)
    frame_paths = render_all_frames(
        script=script,
        summary=summary,
        output_dir=frames_dir,
        size=size,
    )
    log.info(f"  Frames: {len(frame_paths)} images")

    # Verify frames exist and have content
    for fp in frame_paths:
        if not fp.exists() or fp.stat().st_size < 1000:
            log.error(f"  Frame missing or empty: {fp}")

    # ── Step 5: Compose ──────────────────────────────────────────────────────
    log.info("\n[5/7] Composing final video...")
    video_path = _VIDEO_OUT / f"crypto_recap_{ts}.mp4"
    compose_video(
        frame_paths=frame_paths,
        audio_path=audio_path,
        script=script,
        output_path=video_path,
        size=size,
    )
    log.info(f"  Video: {video_path.name}")

    # ── Step 6: Thumbnail ────────────────────────────────────────────────────
    log.info("\n[6/7] Generating thumbnail...")
    thumb_path = _VIDEO_OUT / f"thumb_{ts}.png"
    try:
        generate_thumbnail(
            script=script,
            summary=summary,
            output_path=thumb_path,
        )
        log.info(f"  Thumbnail: {thumb_path.name}")
    except Exception as e:
        log.warning(f"  Thumbnail generation failed: {e}")
        thumb_path = None

    # ── Step 7: Upload (wrapped by patch_upload_resilience.py) ──────────────
    upload_result = None
    if not no_upload:
        log.info("\n[7/7] Uploading to YouTube...")
        try:
            # Pass the summary along so upload.py can build a data-rich
            # owner comment (regime, BTC price, confluence counts, etc.)
            script["_summary"] = summary

            creds = get_youtube_credentials()

            # Timeout-wrapped upload — prevents silent hangs (OAuth prompts,
            # stalled transfers). Raises TimeoutError after 5 minutes.
            upload_result = _run_upload_with_timeout(
                upload_to_youtube,
                video_path=video_path,
                script=script,
                credentials=creds,
                thumbnail_path=thumb_path,
            )
            video_url = f"https://youtu.be/{upload_result.get('id', '?')}"
            log.info(f"  Uploaded: {video_url}")

            _write_status_sentinel(
                success=True,
                video_url=video_url,
            )
        except TimeoutError as e:
            log.error(f"  Upload TIMEOUT: {e}")
            log.error("  Video saved locally — run retry_upload_today.py to re-attempt")
            _write_status_sentinel(
                success=False,
                step_failed="upload",
                error_message=f"TIMEOUT: {e}",
            )
            _notify_desktop(
                "Daily Crypto Video — UPLOAD TIMEOUT",
                "Upload exceeded 5 minutes. Video rendered OK. "
                "Run retry_upload_today.py to ship it.",
            )
        except Exception as e:
            log.error(f"  Upload failed: {e}")
            log.error("  Video saved locally — upload manually or fix credentials")
            _write_status_sentinel(
                success=False,
                step_failed="upload",
                error_message=str(e),
            )
            _notify_desktop(
                "Daily Crypto Video — UPLOAD FAILED",
                f"{type(e).__name__}: {str(e)[:120]}",
            )
    else:
        log.info("\n[7/7] Upload skipped (--no-upload)")
        # Don't overwrite a real status with a skipped-upload sentinel

    elapsed = time.time() - t0
    log.info(f"\n{'=' * 64}")
    log.info(f"Done in {elapsed:.1f}s")
    log.info(f"  Video:  {video_path}")
    log.info(f"  Script: {script_path}")
    log.info(f"{'=' * 64}")

    return {
        "video_path":  str(video_path),
        "script_path": str(script_path),
        "audio_path":  str(audio_path),
        "upload":      upload_result,
        "elapsed_s":   round(elapsed, 1),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Video Pipeline v2.1")
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--landscape", action="store_true")
    parser.add_argument("--voice-id", type=str, default=None)
    parser.add_argument("--bgm", type=str, default=None,
                        help="Path to background music MP3")
    args = parser.parse_args()

    run(
        no_upload=args.no_upload,
        preview=args.preview,
        landscape=args.landscape,
        voice_id=args.voice_id,
        bgm_path=args.bgm,
    )
