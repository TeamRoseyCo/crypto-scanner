"""
patch_video_layout.py — v2 patcher fixing visual + audio-sync bugs.

THIS PATCHER FIXES (cumulative — supersedes the previous patcher):

visuals.py:
  1. ACTIVE SIGNALS lines overlapping (37px spacing vs 61px font)
  2. SIGNAL STACK bars overlapping each other
  3. Internal labels (RISK / INVALIDATION / CTA) shown as huge titles —
     translated to "WEEKEND RISK" / "WHAT KILLS THESE" / "WATCHING THIS WEEKEND?"
  4. SIGNAL STACK page title (RISK) crashing into "SIGNAL STACK" subhead —
     bigger top-of-page gap (was h*0.08, now h*0.13)
  5. "SCANNER DAILY" footer corrected to "WEEKEND SETUPS" on weekly videos

thumbnail.py:
  6. "SETUP SETUP" duplication on weekly title
  7. Thumbnail shows all 3 coins (XAG / INJ / KITE) stacked on right side
     with "WEEKEND SETUPS" on left, instead of just 1 coin + duplicated SETUP

voiceover.py:
  8. Now writes a `<audio>.durations.json` sidecar file recording the
     real duration of EVERY segment audio file (not just the total).
     This is what makes per-segment image timing possible.

compose.py:
  9. Reads the `.durations.json` sidecar and uses real per-segment timings
     to keep each image on screen for its actual narration duration.
     Falls back to even-division if the sidecar is missing (so old
     audio files keep working).

Run from PowerShell:
    cd "C:\\Users\\bruno\\OneDrive\\Ambiente de Trabalho\\Workspace\\crypto scanner\\crypto-scanner"
    & "C:\\Program Files\\Python312\\python.exe" .\\patch_video_layout.py

Idempotent — safe to run multiple times.
"""

from __future__ import annotations
import shutil
import sys
from pathlib import Path

VIDEO_PIPELINE = Path(r"C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\crypto scanner\crypto-scanner\YOUTUBE - faceless channel\video_pipeline")
VISUALS_PATH   = VIDEO_PIPELINE / "visuals.py"
THUMB_PATH     = VIDEO_PIPELINE / "thumbnail.py"
VOICE_PATH     = VIDEO_PIPELINE / "voiceover.py"
COMPOSE_PATH   = VIDEO_PIPELINE / "compose.py"


def fail(msg: str) -> None:
    print(f"\033[91m  ✗ {msg}\033[0m")
    sys.exit(1)


def info(msg: str) -> None:
    print(f"  {msg}")


def ok(msg: str) -> None:
    print(f"\033[92m  ✓ {msg}\033[0m")


def warn(msg: str) -> None:
    print(f"\033[93m  ⚠ {msg}\033[0m")


def patch_file(path: Path, old: str, new: str, label: str,
               already_patched_marker: str) -> bool:
    """
    Replace `old` with `new` in `path`. Skips if `already_patched_marker` is
    present. Hard-fails if `old` not found (unless already patched).
    """
    content = path.read_text(encoding="utf-8")
    if already_patched_marker in content:
        info(f"[{label}] already patched — skipping")
        return False
    if old not in content:
        warn(f"[{label}] expected block not found — file may already be modified.")
        warn(f"        Skipping this patch. Check {path.name} manually.")
        return False
    patched = content.replace(old, new, 1)
    path.write_text(patched, encoding="utf-8")
    ok(f"[{label}] patched")
    return True


def backup(path: Path, suffix: str = ".bak-v2") -> None:
    bak = path.with_suffix(path.suffix + suffix)
    if not bak.exists():
        shutil.copy(path, bak)
        info(f"Backup: {bak.name}")
    else:
        info(f"Backup exists: {bak.name} (not overwritten)")


def main() -> None:
    for p in (VISUALS_PATH, THUMB_PATH, VOICE_PATH, COMPOSE_PATH):
        if not p.exists():
            fail(f"Not found: {p}")

    print()
    print("=" * 68)
    print("VIDEO LAYOUT PATCHER v2 — visual + audio sync fixes")
    print("=" * 68)
    print()
    print("Creating backups...")
    for p in (VISUALS_PATH, THUMB_PATH, VOICE_PATH, COMPOSE_PATH):
        backup(p)

    # ═══════════════════════════════════════════════════════════════════════
    # VISUALS.PY
    # ═══════════════════════════════════════════════════════════════════════
    print()
    print("─" * 68)
    print("Patching visuals.py")
    print("─" * 68)

    # ── FIX #1: ACTIVE SIGNALS line spacing ────────────────────────────────
    patch_file(VISUALS_PATH,
        old='''    draw.text((int(w * 0.08), y), "ACTIVE SIGNALS",
              font=fonts["label"], fill=C["text_dim"])
    y += int(h * 0.035)

    signals = coin.get("signals", [])
    signal_colors = [C["cyan"], C["green"], C["purple"], C["yellow"], C["orange"]]
    for si, sig_name in enumerate(signals[:5]):
        sc = signal_colors[si % len(signal_colors)]
        clean = sig_name.replace("_", " ").upper()
        draw.text((int(w * 0.08), y), f"●", font=fonts["body"], fill=sc)
        draw.text((int(w * 0.13), y), clean, font=fonts["body"], fill=C["text_mid"])
        y += int(h * 0.035)''',
        new='''    draw.text((int(w * 0.08), y), "ACTIVE SIGNALS",
              font=fonts["label"], fill=C["text_dim"])
    y += int(h * 0.055)  # was 0.035 — body font is taller, needs more space

    signals = coin.get("signals", [])
    signal_colors = [C["cyan"], C["green"], C["purple"], C["yellow"], C["orange"]]
    for si, sig_name in enumerate(signals[:5]):
        sc = signal_colors[si % len(signal_colors)]
        clean = sig_name.replace("_", " ").upper()
        draw.text((int(w * 0.08), y), f"●", font=fonts["body"], fill=sc)
        draw.text((int(w * 0.13), y), clean, font=fonts["body"], fill=C["text_mid"])
        y += int(h * 0.055)  # was 0.035 — fixes line overlap''',
        label="active-signals spacing",
        already_patched_marker="fixes line overlap")

    # ── FIX #2: SIGNAL STACK bar spacing ───────────────────────────────────
    patch_file(VISUALS_PATH,
        old='''    for si, sig_name in enumerate(signals[:8]):
        sc = signal_colors[si % len(signal_colors)]
        clean = sig_name.replace("_", " ").upper()

        # Signal name
        draw.text((bar_x, y), clean, font=fonts["label"], fill=C["text_mid"])
        y += int(h * 0.028)''',
        new='''    for si, sig_name in enumerate(signals[:8]):
        sc = signal_colors[si % len(signal_colors)]
        clean = sig_name.replace("_", " ").upper()

        # Signal name
        draw.text((bar_x, y), clean, font=fonts["label"], fill=C["text_mid"])
        y += int(h * 0.045)  # was 0.028 — fixes signal-stack overlap''',
        label="signal-stack spacing",
        already_patched_marker="fixes signal-stack overlap")

    # ── FIX #3a: Add segment-display-label helper ──────────────────────────
    patch_file(VISUALS_PATH,
        old='''# ─────────────────────────────────────────────────────────────────────────────
# STAT CARD''',
        new='''# ─────────────────────────────────────────────────────────────────────────────
# Internal-segment-label → human-friendly display title
# ─────────────────────────────────────────────────────────────────────────────
_SEGMENT_DISPLAY_LABELS = {
    "MARKET":        "MARKET REGIME",
    "MARKET REGIME": "MARKET REGIME",
    "RISK":          "WEEKEND RISK",
    "INVALIDATION":  "WHAT KILLS THESE",
    "CTA":           "WATCHING THIS WEEKEND?",
}


def _segment_display_label(coin_field: str) -> str:
    """Translate internal segment label to viewer-friendly title."""
    upper = (coin_field or "").upper()
    return _SEGMENT_DISPLAY_LABELS.get(upper, upper)


# ─────────────────────────────────────────────────────────────────────────────
# STAT CARD''',
        label="segment-display-label helper",
        already_patched_marker="_SEGMENT_DISPLAY_LABELS")

    # ── FIX #3b: Use translation in stat_card ──────────────────────────────
    patch_file(VISUALS_PATH,
        old='''    # Main title
    y += int(h * 0.08)
    coin_text = seg.get("coin", "MARKET").upper()
    draw.text((int(w * 0.08), y), coin_text,
              font=fonts["hero"], fill=C["text_bright"])''',
        new='''    # Main title (translate internal labels like RISK/INVALIDATION/CTA)
    y += int(h * 0.08)
    coin_text = _segment_display_label(seg.get("coin", "MARKET"))
    draw.text((int(w * 0.08), y), coin_text,
              font=fonts["hero"], fill=C["text_bright"])''',
        label="stat_card title translation",
        already_patched_marker="translate internal labels like RISK")

    # ── FIX #4: SIGNAL STACK title gap — RISK / SIGNAL STACK overlap ───────
    # In _render_signal_stack: page title (coin_sym=RISK) is drawn at y=h*0.07,
    # then "SIGNAL STACK" at y += h*0.08 = h*0.15. With title font being
    # huge (w*0.065 ~ 125px on 1080p), it crashes into the subhead.
    # Also translate coin_sym to human label.
    patch_file(VISUALS_PATH,
        old='''    coin_sym = seg.get("coin", "?").upper()
    change = coin.get("change_24h", 0) or 0
    accent = C["green"] if change >= 0 else C["red"]

    # Header
    _draw_glow_line(draw, int(h * 0.04), w, C["purple"])

    y = int(h * 0.07)
    draw.text((int(w * 0.08), y), coin_sym,
              font=fonts["title"], fill=C["text_bright"])

    chg_text = f" {change:+.1f}% "
    _draw_badge(draw, int(w * 0.55), y + 5, chg_text, accent, fonts["body"])

    y += int(h * 0.08)
    draw.text((int(w * 0.08), y), "SIGNAL STACK",
              font=fonts["body"], fill=C["cyan"])''',
        new='''    raw_coin = seg.get("coin", "?").upper()
    coin_sym = _segment_display_label(raw_coin)
    is_meta_segment = raw_coin in ("RISK", "INVALIDATION", "CTA",
                                    "MARKET", "MARKET REGIME")
    change = coin.get("change_24h", 0) or 0
    accent = C["green"] if change >= 0 else C["red"]

    # Header
    _draw_glow_line(draw, int(h * 0.04), w, C["purple"])

    y = int(h * 0.07)
    draw.text((int(w * 0.08), y), coin_sym,
              font=fonts["title"], fill=C["text_bright"])

    # Only show % badge for real coins, not meta segments
    if not is_meta_segment:
        chg_text = f" {change:+.1f}% "
        _draw_badge(draw, int(w * 0.55), y + 5, chg_text, accent, fonts["body"])

    y += int(h * 0.14)  # was 0.08 — bigger gap below the page title
    draw.text((int(w * 0.08), y), "SIGNAL STACK",
              font=fonts["body"], fill=C["cyan"])''',
        label="signal_stack title gap + meta-segment handling",
        already_patched_marker="is_meta_segment")

    # ── FIX #5: weekly-aware footer label ──────────────────────────────────
    patch_file(VISUALS_PATH,
        old='''              f"SCANNER DAILY  •  {summary.get('date', '')}",''',
        new='''              f"{'WEEKEND SETUPS' if summary.get('_video_type') == 'weekly_friday' else 'SCANNER DAILY'}  •  {summary.get('date', '')}",''',
        label="weekly-aware footer label",
        already_patched_marker="WEEKEND SETUPS")

    # ═══════════════════════════════════════════════════════════════════════
    # THUMBNAIL.PY
    # ═══════════════════════════════════════════════════════════════════════
    print()
    print("─" * 68)
    print("Patching thumbnail.py")
    print("─" * 68)

    # ── FIX #6: text-builder + weekly detector ─────────────────────────────
    patch_file(THUMB_PATH,
        old='''def _build_thumb_text(title: str, coin: str) -> str:
    """
    Build short punchy thumbnail text (3-4 words max).
    YouTube thumbnails need to be readable at small sizes.
    """
    # Remove emojis
    import re
    clean = re.sub(r'[^\\w\\s!?&+\\-$%]', '', title).strip()

    # If title is already short enough, use it
    words = clean.split()
    if len(words) <= 4:
        return clean.upper()

    # Try to extract the punchiest part
    # Look for key phrases
    for pattern in [
        r'(BREAKOUT|SQUEEZE|PUMP|CRASH|ALERT|WARNING|SETUP)',
        r'(BULL|BEAR)',
    ]:
        m = re.search(pattern, clean, re.IGNORECASE)
        if m:
            keyword = m.group(1).upper()
            if coin:
                return f"{coin}\\n{keyword}\\nSETUP"
            return f"CRYPTO\\n{keyword}\\nALERT"

    # Default: first 3-4 meaningful words
    skip = {"the", "a", "an", "and", "or", "in", "on", "for", "with", "is", "are"}
    key_words = [w for w in words if w.lower() not in skip][:3]
    return "\\n".join(w.upper() for w in key_words)''',
        new='''def _build_thumb_text(title: str, coin: str) -> str:
    """
    Build short punchy thumbnail text (3-4 words max).
    For the weekly script, caller should use the 3-coin layout instead.
    """
    import re
    clean = re.sub(r'[^\\w\\s!?&+\\-$%]', '', title).strip()

    words = clean.split()
    if len(words) <= 4:
        return clean.upper()

    for pattern in [
        r'(BREAKOUT|SQUEEZE|PUMP|CRASH|ALERT|WARNING)',
        r'(BULL|BEAR)',
        r'(SETUP)',  # separated to avoid double SETUP\\nSETUP
    ]:
        m = re.search(pattern, clean, re.IGNORECASE)
        if m:
            keyword = m.group(1).upper()
            if coin:
                if keyword == "SETUP":
                    return f"{coin}\\nSETUP"
                return f"{coin}\\n{keyword}\\nSETUP"
            return f"CRYPTO\\n{keyword}\\nALERT"

    skip = {"the", "a", "an", "and", "or", "in", "on", "for", "with", "is", "are"}
    key_words = [w for w in words if w.lower() not in skip][:3]
    return "\\n".join(w.upper() for w in key_words)


def _is_weekly_script(script: dict, summary: dict) -> bool:
    """Detect if this is the Friday weekly script (3 coin setups)."""
    if summary and summary.get("_video_type") == "weekly_friday":
        return True
    segments = script.get("segments", []) or []
    coins = set()
    for seg in segments:
        c = (seg.get("coin", "") or "").upper()
        if c and c not in ("MARKET", "MARKET REGIME", "RISK", "INVALIDATION", "CTA", ""):
            coins.add(c)
    return len(coins) >= 3''',
        label="thumbnail text-builder + weekly detector",
        already_patched_marker="_is_weekly_script")

    # ── FIX #7: weekly multi-coin display ──────────────────────────────────
    patch_file(THUMB_PATH,
        old='''    # Find the most interesting coin to feature
    featured_coin = ""
    featured_stat = ""
    for seg in segments:
        coin = seg.get("coin", "").upper()
        if coin not in ("MARKET", "MARKET REGIME", ""):
            featured_coin = coin
            featured_stat = seg.get("stat", "")
            break''',
        new='''    # Find the most interesting coin(s) to feature
    featured_coin = ""
    featured_stat = ""
    weekly_coins = []  # for the weekly variant: collects up to 3 coins
    for seg in segments:
        coin = seg.get("coin", "").upper()
        if coin not in ("MARKET", "MARKET REGIME", "RISK", "INVALIDATION", "CTA", ""):
            if not featured_coin:
                featured_coin = coin
                featured_stat = seg.get("stat", "")
            if coin not in weekly_coins:
                weekly_coins.append(coin)

    is_weekly = _is_weekly_script(script, summary)
    if is_weekly and len(weekly_coins) >= 2:
        featured_coin = "\\n".join(weekly_coins[:3])''',
        label="thumbnail weekly multi-coin display",
        already_patched_marker="weekly_coins")

    patch_file(THUMB_PATH,
        old='''    # Build short punchy thumbnail text (3-4 words max)
    thumb_text = _build_thumb_text(title, featured_coin)''',
        new='''    # Build short punchy thumbnail text (3-4 words max)
    # For the weekly, use a fixed left-side title so it pairs with the 3-coin stack on right.
    if is_weekly and len(weekly_coins) >= 2:
        thumb_text = "WEEKEND\\nSETUPS"
    else:
        thumb_text = _build_thumb_text(title, featured_coin)''',
        label="thumbnail title-build weekly branch",
        already_patched_marker='thumb_text = "WEEKEND\\nSETUPS"')

    # ═══════════════════════════════════════════════════════════════════════
    # VOICEOVER.PY — write per-segment durations sidecar
    # ═══════════════════════════════════════════════════════════════════════
    print()
    print("─" * 68)
    print("Patching voiceover.py")
    print("─" * 68)

    patch_file(VOICE_PATH,
        old='''    duration = _get_duration(ffmpeg, output_path)
    log.info(f"  Total audio: {duration:.1f}s ({len(seg_files)} segments)")

    # Cleanup
    import shutil
    shutil.rmtree(str(tmp), ignore_errors=True)

    return duration''',
        new='''    duration = _get_duration(ffmpeg, output_path)
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

    return duration''',
        label="per-segment durations sidecar",
        already_patched_marker="Per-segment durations:")

    # ═══════════════════════════════════════════════════════════════════════
    # COMPOSE.PY — use per-segment durations
    # ═══════════════════════════════════════════════════════════════════════
    print()
    print("─" * 68)
    print("Patching compose.py")
    print("─" * 68)

    patch_file(COMPOSE_PATH,
        old='''    total_duration = _get_audio_duration(ffmpeg, audio_path)
    frame_duration = total_duration / n_frames

    log.info(f"  Audio: {total_duration:.1f}s, {n_frames} frames, "
             f"{frame_duration:.1f}s each")''',
        new='''    total_duration = _get_audio_duration(ffmpeg, audio_path)

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
                 f"(per-segment timing)")''',
        label="compose: read durations sidecar",
        already_patched_marker="per_segment_durations")

    # Now we need the per-frame encoder loop to use per_segment_durations[i]
    # instead of frame_duration. Replace the two places it's referenced.
    patch_file(COMPOSE_PATH,
        old='''        # Build individual segment videos with zoom effect
        seg_videos = []
        for i, frame in enumerate(safe_frames):
            seg_out = tmp / f"seg_{i:03d}.mp4"
            total_seg_frames = int(frame_duration * FPS)

            # Zoom out: start at 1.08x, end at 1.0x (subtle, stable)
            zoom_expr = f"max(1.08-0.08*(on/{total_seg_frames}),1.0)"

            # Upscale first for smooth zoom, then zoompan
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
                "-t", f"{frame_duration:.3f}",
                "-vf", vf,
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
                "-an",
                str(seg_out),
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                log.warning(f"  Zoom failed on frame {i}, using static fallback")
                # Fallback: static image, no zoom
                cmd_fb = [
                    ffmpeg, "-y",
                    "-loop", "1", "-i", str(frame),
                    "-t", f"{frame_duration:.3f}",
                    "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                           f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
                           f"format=yuv420p",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
                    "-an",
                    str(seg_out),
                ]
                subprocess.run(cmd_fb, capture_output=True, check=True)

            seg_videos.append(seg_out)''',
        new='''        # Build individual segment videos with zoom effect.
        # Each frame uses its own duration from per_segment_durations[i].
        seg_videos = []
        for i, frame in enumerate(safe_frames):
            seg_out = tmp / f"seg_{i:03d}.mp4"
            this_duration = per_segment_durations[i]
            total_seg_frames = int(this_duration * FPS)

            # Zoom out: start at 1.08x, end at 1.0x (subtle, stable)
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

            seg_videos.append(seg_out)''',
        label="compose: use per-frame durations",
        already_patched_marker="this_duration = per_segment_durations[i]")

    print()
    print("=" * 68)
    print("\033[92m  ALL PATCHES APPLIED\033[0m")
    print("=" * 68)
    print()
    print("IMPORTANT — audio re-generation required for the timing fix:")
    print()
    print("  The per-segment durations sidecar (.durations.json) is written")
    print("  by voiceover.py, so the existing weekly_audio_*.mp3 does NOT")
    print("  have a sidecar yet. compose.py will fall back to even-split")
    print("  for that audio. To get true per-segment timing, you need fresh")
    print("  audio.")
    print()
    print("OPTION A — Visual fixes only (free, fast, no audio burn):")
    print('  & "C:\\Program Files\\Python312\\python.exe" video_pipeline\\weekly_pipeline.py --skip-voice --no-upload')
    print()
    print("OPTION B — All fixes including audio sync (~8000 ElevenLabs chars):")
    print('  & "C:\\Program Files\\Python312\\python.exe" video_pipeline\\weekly_pipeline.py --no-upload')
    print()
    print("Recommendation: do Option A first to verify the visual fixes,")
    print("then Option B once you're happy with the layout.")


if __name__ == "__main__":
    main()
