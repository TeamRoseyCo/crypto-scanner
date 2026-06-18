"""
compose.py — Stitch frames + audio into final MP4 with Ken Burns zoom.

Copies all assets to temp dir with safe names to handle Windows paths
with spaces. Applies subtle zoom-out effect on each frame.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("video_pipeline.compose")

FPS = 30


def _get_ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path:
            return path
    except ImportError:
        pass
    return "ffmpeg"


def compose_video(
    frame_paths:  list[Path],
    audio_path:   Path,
    script:       dict,
    output_path:  Path,
    size:         tuple[int, int] = (1080, 1920),
    static:       bool = False,
) -> None:
    """
    Compose final video from frames + audio.
    
    If static=False (default): Ken Burns zoom-out effect on each frame.
    If static=True: frames shown at full resolution, no zoom. Better for
    real TradingView chart screenshots where zoom distorts the image.
    """
    ffmpeg = _get_ffmpeg()
    w, h = size
    n_frames = len(frame_paths)

    if n_frames == 0:
        raise ValueError("No frames to compose")

    total_duration = _get_audio_duration(ffmpeg, audio_path)

    # Look for the per-segment durations sidecar written by voiceover.py.
    # If present and the count matches our frame count, use the real
    # per-segment timings so each image stays on screen for its actual
    # narration duration (fixes the "image flips while talking" bug).
    sidecar = audio_path.with_suffix(".durations.json")
    per_segment_durations = None
    if sidecar.exists():
        try:
            import json as _json
            data = _json.loads(sidecar.read_text(encoding="utf-8"))
            sd = data.get("segment_durations") or []
            if len(sd) == n_frames:
                per_segment_durations = sd
                log.info(f"  Using per-segment durations from "
                         f"{sidecar.name}: {[round(x,1) for x in sd]}")
            else:
                log.warning(f"  {sidecar.name} has {len(sd)} segments but "
                            f"video has {n_frames} frames — falling back "
                            f"to even split")
        except Exception as e:
            log.warning(f"  Could not read {sidecar.name}: {e}")

    if per_segment_durations is None:
        frame_duration = total_duration / n_frames
        per_segment_durations = [frame_duration] * n_frames
        log.info(f"  Audio: {total_duration:.1f}s, {n_frames} frames, "
                 f"{frame_duration:.1f}s each (even split)")
    else:
        log.info(f"  Audio: {total_duration:.1f}s, {n_frames} frames "
                 f"(per-segment timing)")

    # Copy everything to safe temp dir
    tmp = Path(tempfile.mkdtemp(prefix="vid_"))
    try:
        # Copy frames
        safe_frames = []
        for i, fp in enumerate(frame_paths):
            safe = tmp / f"f{i:03d}.png"
            shutil.copy2(str(fp), str(safe))
            safe_frames.append(safe)
            log.info(f"  Copied frame {i}: {fp.name} → {safe.name} ({safe.stat().st_size:,} bytes)")

        # Copy audio
        safe_audio = tmp / "audio.mp3"
        shutil.copy2(str(audio_path), str(safe_audio))

        # Build individual segment videos.
        # static=True: full-res frames, no zoom (for long-form with real charts)
        # static=False: Ken Burns zoom-out effect (for Shorts)
        seg_videos = []
        for i, frame in enumerate(safe_frames):
            seg_out = tmp / f"seg_{i:03d}.mp4"
            this_duration = per_segment_durations[i]

            if static:
                # Static mode: show frame at exact resolution, no zoom
                cmd = [
                    ffmpeg, "-y",
                    "-loop", "1", "-i", str(frame),
                    "-t", f"{this_duration:.3f}",
                    "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                           f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
                           f"format=yuv420p",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
                    "-an",
                    str(seg_out),
                ]
                subprocess.run(cmd, capture_output=True, check=True)
            else:
                # Zoom mode: Ken Burns zoom-out effect
                total_seg_frames = int(this_duration * FPS)
                zoom_expr = f"max(1.08-0.08*(on/{max(1, total_seg_frames)}),1.0)"

                vf = (
                    f"scale={w*3}:{h*3}:force_original_aspect_ratio=decrease,"
                    f"pad={w*3}:{h*3}:(ow-iw)/2:(oh-ih)/2:color=black,"
                    f"zoompan=z='{zoom_expr}'"
                    f":x='iw/2-(iw/zoom/2)'"
                    f":y='ih/2-(ih/zoom/2)'"
                    f":d=1:s={w}x{h}:fps={FPS},"
                    f"format=yuv420p"
                )

                cmd = [
                    ffmpeg, "-y",
                    "-loop", "1", "-i", str(frame),
                    "-t", f"{this_duration:.3f}",
                    "-vf", vf,
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
                    "-an",
                    str(seg_out),
                ]

                result = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if result.returncode != 0:
                    log.warning(f"  Zoom failed on frame {i}, using static fallback")
                    cmd_fb = [
                        ffmpeg, "-y",
                        "-loop", "1", "-i", str(frame),
                        "-t", f"{this_duration:.3f}",
                        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                               f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
                               f"format=yuv420p",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
                        "-an",
                        str(seg_out),
                    ]
                    subprocess.run(cmd_fb, capture_output=True, check=True)

            seg_videos.append(seg_out)

        # Concatenate all segment videos
        concat_path = tmp / "concat.txt"
        with open(concat_path, "w", encoding="utf-8") as f:
            for sv in seg_videos:
                f.write(f"file '{sv.name}'\n")

        video_only = tmp / "video_only.mp4"
        subprocess.run([
            ffmpeg, "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_path),
            "-c", "copy",
            str(video_only),
        ], capture_output=True, check=True, cwd=str(tmp))

        # Mux video + audio
        safe_output = tmp / "final.mp4"
        subprocess.run([
            ffmpeg, "-y",
            "-i", str(video_only),
            "-i", str(safe_audio),
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            str(safe_output),
        ], capture_output=True, check=True)

        # Copy to final location
        shutil.copy2(str(safe_output), str(output_path))
        log.info(f"  Done: {output_path.name}")

    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


def _get_audio_duration(ffmpeg: str, audio_path: Path) -> float:
    try:
        result = subprocess.run(
            [ffmpeg, "-i", str(audio_path)],
            capture_output=True, text=True, check=False,
        )
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", result.stderr)
        if m:
            h, mi, s, cs = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            return h * 3600 + mi * 60 + s + cs / 100.0
    except Exception:
        pass
    try:
        return audio_path.stat().st_size / (192 * 1000 / 8)
    except Exception:
        return 30.0
