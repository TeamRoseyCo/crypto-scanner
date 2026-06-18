"""
patch_phase1_titles.py — Fix title overflow and stat overlap on meta segments.

PHASE 1 FIXES:

visuals.py:
  1. Auto-shrink hero font when the title text is too wide for the screen.
     Fixes "WHAT KILLS THES…" and "WATCHING THIS W…" cutoffs.
  2. Increase vertical gap between title and stat (was 13%, now 20%) so
     they never overlap regardless of font choice.
  3. Suppress the "stat" line on stat_card meta segments (RISK,
     INVALIDATION, CTA) — these don't have a real % stat and the placeholder
     "-0.62% 24h" was being drawn on top of the title.

No audio regen needed. Apply, rebuild with --skip-voice --no-upload, verify,
then we move to Phase 2 (trade-plan subtitle card).

Run from PowerShell:
    cd "C:\\Users\\bruno\\OneDrive\\Ambiente de Trabalho\\Workspace\\crypto scanner\\crypto-scanner"
    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
    & "C:\\Program Files\\Python312\\python.exe" .\\patch_phase1_titles.py

Idempotent — safe to re-run.
"""

from __future__ import annotations
import shutil
import sys
from pathlib import Path

VIDEO_PIPELINE = Path(r"C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\crypto scanner\crypto-scanner\YOUTUBE - faceless channel\video_pipeline")
VISUALS_PATH   = VIDEO_PIPELINE / "visuals.py"


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
    content = path.read_text(encoding="utf-8")
    if already_patched_marker in content:
        info(f"[{label}] already patched — skipping")
        return False
    if old not in content:
        warn(f"[{label}] expected block not found — skipping.")
        warn(f"        File may already be modified. Check {path.name} manually.")
        return False
    patched = content.replace(old, new, 1)
    path.write_text(patched, encoding="utf-8")
    ok(f"[{label}] patched")
    return True


def main() -> None:
    if not VISUALS_PATH.exists():
        fail(f"visuals.py not found at {VISUALS_PATH}")

    print()
    print("=" * 68)
    print("PHASE 1 PATCHER — title autoshrink + stat-overlap fix")
    print("=" * 68)
    print()

    # Backup
    bak = VISUALS_PATH.with_suffix(VISUALS_PATH.suffix + ".bak-phase1")
    if not bak.exists():
        shutil.copy(VISUALS_PATH, bak)
        info(f"Backup: {bak.name}")
    else:
        info(f"Backup exists: {bak.name}")

    print()
    print("─" * 68)
    print("Patching visuals.py")
    print("─" * 68)

    # ── FIX #1: Add a font-autoshrink helper ──────────────────────────────
    # Insert it right before the _render_stat_card function.
    patch_file(VISUALS_PATH,
        old='''# ─────────────────────────────────────────────────────────────────────────────
# Internal-segment-label → human-friendly display title
# ─────────────────────────────────────────────────────────────────────────────''',
        new='''# ─────────────────────────────────────────────────────────────────────────────
# Auto-shrink font so long titles fit within a max width
# ─────────────────────────────────────────────────────────────────────────────
def _fit_font(draw, text: str, max_width_px: int, base_font, min_ratio: float = 0.45):
    """
    Return a font sized to make `text` fit within max_width_px. Starts at
    the base font's size, shrinks down to min_ratio * base size if needed.
    Falls back to the base font if it already fits or if no shrink helps.

    Why we need this: hero font is sized for short labels like "MARKET" or
    a 4-letter ticker. Long phrases like "WATCHING THIS WEEKEND?" overflow
    the screen edge. This shrinks the font on the fly to fit.
    """
    try:
        bbox = draw.textbbox((0, 0), text, font=base_font)
        w_px = bbox[2] - bbox[0]
    except Exception:
        return base_font

    if w_px <= max_width_px:
        return base_font

    # Try progressively smaller sizes
    from PIL import ImageFont
    base_size = getattr(base_font, "size", 100)
    min_size = max(12, int(base_size * min_ratio))

    # Get the font path so we can re-instantiate at different sizes
    font_path = None
    try:
        font_path = base_font.path
    except AttributeError:
        try:
            font_path = base_font.getname()[0]
        except Exception:
            return base_font

    # Try shrinking by 10% increments
    for size in range(base_size, min_size - 1, -max(1, int(base_size * 0.05))):
        try:
            test_font = ImageFont.truetype(font_path, size) if font_path else base_font
            bbox = draw.textbbox((0, 0), text, font=test_font)
            if bbox[2] - bbox[0] <= max_width_px:
                return test_font
        except Exception:
            continue

    # Even at min size doesn't fit — return min-size font anyway
    try:
        return ImageFont.truetype(font_path, min_size) if font_path else base_font
    except Exception:
        return base_font


# ─────────────────────────────────────────────────────────────────────────────
# Internal-segment-label → human-friendly display title
# ─────────────────────────────────────────────────────────────────────────────''',
        label="font autoshrink helper",
        already_patched_marker="def _fit_font")

    # ── FIX #2: Use autoshrink + suppress stat for meta segments in stat_card ─
    patch_file(VISUALS_PATH,
        old='''    # Main title (translate internal labels like RISK/INVALIDATION/CTA)
    y += int(h * 0.08)
    coin_text = _segment_display_label(seg.get("coin", "MARKET"))
    draw.text((int(w * 0.08), y), coin_text,
              font=fonts["hero"], fill=C["text_bright"])

    # Stat (big number — clean to short display only)
    y += int(h * 0.13)
    stat = _clean_stat(seg.get("stat", ""))
    if stat:
        stat_color = C["green"] if "+" in stat else C["red"] if "-" in stat else C["cyan"]
        draw.text((int(w * 0.08), y), stat,
                  font=fonts["stat"], fill=stat_color)''',
        new='''    # Main title (translate internal labels like RISK/INVALIDATION/CTA).
    # For long phrases, auto-shrink the font so it fits within the screen.
    y += int(h * 0.08)
    raw_coin = seg.get("coin", "MARKET").upper()
    coin_text = _segment_display_label(raw_coin)
    is_meta_segment = raw_coin in ("RISK", "INVALIDATION", "CTA",
                                    "MARKET", "MARKET REGIME")

    # Available width = screen width minus left margin (8%) and right margin (8%)
    max_title_w = int(w * 0.84)
    title_font = _fit_font(draw, coin_text, max_title_w, fonts["hero"])
    draw.text((int(w * 0.08), y), coin_text,
              font=title_font, fill=C["text_bright"])

    # Stat (big number — only for real coin segments).
    # Skip on meta segments (RISK/INVALIDATION/CTA/MARKET) where the % data
    # is bogus (e.g. coin.change_24h is 0 → renders "+0.0%" or a stale value).
    # Bigger vertical gap (0.20 vs 0.13) so stat never overlaps title even
    # when title is at full hero font size.
    y += int(h * 0.20)
    if not is_meta_segment:
        stat = _clean_stat(seg.get("stat", ""))
        if stat:
            stat_color = C["green"] if "+" in stat else C["red"] if "-" in stat else C["cyan"]
            draw.text((int(w * 0.08), y), stat,
                      font=fonts["stat"], fill=stat_color)''',
        label="stat_card autoshrink + meta-segment stat suppression",
        already_patched_marker="max_title_w = int(w * 0.84)")

    print()
    print("=" * 68)
    print("\033[92m  PHASE 1 PATCHES APPLIED\033[0m")
    print("=" * 68)
    print()
    print("Next steps:")
    print()
    print("  1. Rebuild the weekly using existing audio (FREE):")
    print('     cd "C:\\Users\\bruno\\OneDrive\\Ambiente de Trabalho\\Workspace\\crypto scanner\\crypto-scanner\\YOUTUBE - faceless channel"')
    print('     & "C:\\Program Files\\Python312\\python.exe" video_pipeline\\weekly_pipeline.py --skip-voice --no-upload')
    print()
    print("  2. Watch the new MP4 in VLC and check:")
    print("     ✓ Long titles fit: 'WHAT KILLS THESE', 'WATCHING THIS WEEKEND?'")
    print("       no longer cut off — auto-shrunk to fit screen width")
    print("     ✓ MARKET REGIME screen no longer shows '-0.62% 24h'")
    print("       overlapping the title")
    print("     ✓ All previously-fixed issues still good (signal spacing,")
    print("       weekly thumbnail, footer labels)")
    print()
    print("  3. Paste screenshots and we'll move to Phase 2:")
    print("     trade-plan subtitle card with entry/stop/TP1/TP2/TP3 on")
    print("     each coin segment.")


if __name__ == "__main__":
    main()
