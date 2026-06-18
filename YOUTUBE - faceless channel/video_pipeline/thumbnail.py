"""
thumbnail.py — Professional YouTube thumbnail generator.

Creates eye-catching 1280×720 thumbnails with:
  - Bold 3-4 word title text
  - Coin symbol with neon glow
  - Key stat (percentage, confluence score)
  - Dark cinematic background with accent colors
  - Chart-like visual elements

USAGE:
  Integrated into pipeline (auto-generates with each video):
    from thumbnail import generate_thumbnail
    generate_thumbnail(script, summary, output_path)

  Standalone:
    python thumbnail.py --title "ORCA Breakout Setup" --coin ORCA --stat "+23%" --output thumb.png
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# THUMBNAIL CONFIG
# ─────────────────────────────────────────────────────────────────────────────

WIDTH  = 1280
HEIGHT = 720

# Color schemes — rotates per video for variety
COLOR_SCHEMES = [
    {   # Neon green (bullish)
        "bg_top": (6, 6, 16),
        "bg_bot": (10, 20, 15),
        "accent": (0, 255, 120),
        "accent_dim": (0, 80, 40),
        "stat_color": (0, 255, 120),
        "text": (240, 240, 245),
        "glow": (0, 255, 120),
    },
    {   # Electric cyan
        "bg_top": (4, 4, 18),
        "bg_bot": (8, 15, 25),
        "accent": (0, 200, 255),
        "accent_dim": (0, 60, 80),
        "stat_color": (0, 220, 255),
        "text": (240, 240, 245),
        "glow": (0, 200, 255),
    },
    {   # Hot red (bearish/warning)
        "bg_top": (16, 4, 4),
        "bg_bot": (20, 8, 12),
        "accent": (255, 50, 80),
        "accent_dim": (80, 15, 25),
        "stat_color": (255, 60, 90),
        "text": (240, 240, 245),
        "glow": (255, 50, 80),
    },
    {   # Gold/amber
        "bg_top": (12, 8, 2),
        "bg_bot": (18, 14, 6),
        "accent": (255, 200, 0),
        "accent_dim": (80, 60, 0),
        "stat_color": (255, 210, 30),
        "text": (240, 240, 245),
        "glow": (255, 200, 0),
    },
    {   # Purple
        "bg_top": (8, 4, 16),
        "bg_bot": (14, 8, 22),
        "accent": (160, 100, 255),
        "accent_dim": (50, 30, 80),
        "stat_color": (170, 110, 255),
        "text": (240, 240, 245),
        "glow": (160, 100, 255),
    },
]


def generate_thumbnail(
    script:      dict,
    summary:     dict,
    output_path: Path,
    scheme_idx:  Optional[int] = None,
) -> Path:
    """
    Generate a professional YouTube thumbnail from script data.

    Args:
        script:      Script dict from scriptgen.py
        summary:     Market summary from ingest.py
        output_path: Where to save the PNG (1280×720)
        scheme_idx:  Color scheme index (None = auto based on content)

    Returns:
        Path to generated thumbnail
    """
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np

    # ── Style rotation (added by patch_thumbnail_rotation.py) ────────────
    # Pick a style based on signal data + recency rotation.
    chosen_style, ctx = _pick_thumbnail_style(script, summary)

    # ── Pick color scheme (sentiment-aware, patched by patch_color_sentiment.py) ─
    if scheme_idx is not None:
        colors = COLOR_SCHEMES[scheme_idx % len(COLOR_SCHEMES)]
    else:
        colors = _pick_sentiment_color(script, ctx)

    # ── Dispatch to new styles if not classic (added by patch) ───────────
    if chosen_style != STYLE_CLASSIC:
        result = _dispatch_style_render(chosen_style, ctx, colors, output_path)
        if result is not None:
            _log_thumbnail_choice(chosen_style, ctx,
                                   scheme_name=str(COLOR_SCHEMES.index(colors)))
            return result
        # Fall through to classic rendering on any style failure
        chosen_style = STYLE_CLASSIC

    # ── Create gradient background ───────────────────────────────────────
    arr = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    for y in range(HEIGHT):
        t = y / HEIGHT
        for c in range(3):
            arr[y, :, c] = int(colors["bg_top"][c] * (1 - t) + colors["bg_bot"][c] * t)

    # Add subtle noise
    noise = np.random.randint(-3, 4, (HEIGHT, WIDTH, 3), dtype=np.int16)
    arr = np.clip(arr.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    img = Image.fromarray(arr, "RGB")
    draw = ImageDraw.Draw(img)

    # ── Load fonts ───────────────────────────────────────────────────────
    fonts = _load_fonts()

    # ── Extract content from script ──────────────────────────────────────
    title = script.get("title", "Crypto Scanner Alert")
    segments = script.get("segments", [])

    # Find the most interesting coin(s) to feature
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
        featured_coin = "\n".join(weekly_coins[:3])

    # Build short punchy thumbnail text (3-4 words max)
    # For the weekly, use a fixed left-side title so it pairs with the 3-coin stack on right.
    if is_weekly and len(weekly_coins) >= 2:
        thumb_text = "WEEKEND\nSETUPS"
    else:
        thumb_text = _build_thumb_text(title, featured_coin)

    # ── Draw chart-like background elements ──────────────────────────────
    _draw_chart_bg(draw, colors)

    # ── Draw accent glow bar at top ──────────────────────────────────────
    for offset in range(8, 0, -1):
        alpha = max(0, colors["accent"][1] - offset * 30)
        glow_color = (
            max(0, colors["glow"][0] // (offset + 1)),
            max(0, colors["glow"][1] // (offset + 1)),
            max(0, colors["glow"][2] // (offset + 1)),
        )
        draw.line([(0, offset), (WIDTH, offset)], fill=glow_color, width=1)

    # ── Draw featured coin symbol (large, right side) ────────────────────
    if featured_coin:
        # Coin auto-fit (added by patch_color_sentiment.py)
        # Previously hardcoded at font size 140 + x=WIDTH-380, which clipped
        # tickers >5 chars. Now binary-search the largest font that fits
        # within a 460px box (WIDTH-380 to WIDTH-20), preserving original
        # position but preventing overflow.
        coin_y = 80
        coin_max_w = 460
        # Handle multi-line coin (weekly variant uses "\n".join())
        if "\n" in featured_coin:
            # Fall back to original behavior for multi-line — fits fine since
            # each line is one ticker. Use a slightly smaller fixed size.
            coin_font = fonts["coin"]
            coin_x = WIDTH - 380
        else:
            coin_font = _fit_font(draw, featured_coin, max_width=coin_max_w,
                                   max_size=140, min_size=80)
            # Right-align: position coin so it ends at WIDTH - 30
            cw, _ = _text_size(draw, featured_coin, coin_font)
            coin_x = WIDTH - 30 - cw

        # Glow effect behind coin text
        for offset in range(12, 0, -2):
            glow_c = tuple(max(0, c // (offset + 1)) for c in colors["glow"])
            draw.text((coin_x - offset, coin_y), featured_coin, font=coin_font, fill=glow_c)
            draw.text((coin_x + offset, coin_y), featured_coin, font=coin_font, fill=glow_c)
            draw.text((coin_x, coin_y - offset), featured_coin, font=coin_font, fill=glow_c)
            draw.text((coin_x, coin_y + offset), featured_coin, font=coin_font, fill=glow_c)

        # Main coin text
        draw.text((coin_x, coin_y), featured_coin, font=coin_font, fill=colors["accent"])

    # ── Draw stat (big percentage or score) ──────────────────────────────
    if featured_stat:
        stat_font = fonts["stat"]
        stat_y = 250

        # Clean up stat for display
        stat_display = featured_stat.split(",")[0].strip()  # Take first part if multiple
        if len(stat_display) > 15:
            stat_display = stat_display[:15]

        # Right-align stat under the (now right-aligned) coin for consistency
        if "\n" in (featured_coin or ""):
            stat_x = WIDTH - 380  # original position for multi-line coin layout
        else:
            sw, _ = _text_size(draw, stat_display, stat_font)
            stat_x = WIDTH - 30 - sw

        draw.text((stat_x, stat_y), stat_display,
                  font=stat_font, fill=colors["stat_color"])

    # ── Draw main title text (left side, large bold) ─────────────────────
    title_lines = _wrap_text(thumb_text, max_chars=14)
    title_font = fonts["title"]
    y = 120

    for line in title_lines[:3]:
        # Text shadow
        draw.text((42, y + 4), line, font=title_font, fill=(0, 0, 0))
        draw.text((38, y + 4), line, font=title_font, fill=(0, 0, 0))
        # Main text
        draw.text((40, y), line, font=title_font, fill=colors["text"])
        y += 140

    # ── Draw regime badge ────────────────────────────────────────────────
    regime = summary.get("regime", "").upper() if summary else ""
    if regime:
        badge_font = fonts["badge"]
        badge_color = {
            "BULL": (0, 200, 80),
            "BEAR": (255, 50, 80),
            "SIDEWAYS": (255, 200, 0),
        }.get(regime, (150, 150, 170))

        badge_x = 40
        badge_y = HEIGHT - 120

        # Badge background
        bbox = badge_font.getbbox(f"  {regime} MARKET  ")
        bw = bbox[2] - bbox[0]
        bh = bbox[3] - bbox[1]
        draw.rounded_rectangle(
            [badge_x, badge_y, badge_x + bw + 20, badge_y + bh + 16],
            radius=8,
            fill=(badge_color[0] // 5, badge_color[1] // 5, badge_color[2] // 5),
            outline=badge_color,
        )
        draw.text((badge_x + 10, badge_y + 6), f"  {regime} MARKET  ",
                  font=badge_font, fill=badge_color)

    # ── Draw branding ────────────────────────────────────────────────────
    brand_font = fonts["brand"]
    draw.text((WIDTH - 340, HEIGHT - 50), "ALPHA SIGNALS CRYPTO",
              font=brand_font, fill=(80, 80, 100))

    # ── Draw bottom accent line ──────────────────────────────────────────
    draw.rectangle([0, HEIGHT - 6, WIDTH, HEIGHT], fill=colors["accent"])

    # ── Save ─────────────────────────────────────────────────────────────
    img.save(str(output_path), "PNG", quality=95)
    # Log classic choice (new styles log inside _dispatch_style_render path)
    _log_thumbnail_choice(STYLE_CLASSIC, ctx,
                          scheme_name=str(COLOR_SCHEMES.index(colors)))
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _load_fonts() -> dict:
    """Load fonts at various sizes for thumbnail elements."""
    from PIL import ImageFont

    candidates_bold = [
        "C:/Windows/Fonts/impact.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]

    candidates_regular = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    def load(candidates, size):
        for path in candidates:
            try:
                return ImageFont.truetype(path, size)
            except (OSError, IOError):
                continue
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    return {
        "title":  load(candidates_bold, 120),
        "coin":   load(candidates_bold, 140),
        "stat":   load(candidates_bold, 72),
        "badge":  load(candidates_bold, 36),
        "brand":  load(candidates_regular, 22),
    }


def _build_thumb_text(title: str, coin: str) -> str:
    """
    Build short punchy thumbnail text (3-4 words max).
    For the weekly script, caller should use the 3-coin layout instead.
    """
    import re
    clean = re.sub(r'[^\w\s!?&+\-$%]', '', title).strip()

    words = clean.split()
    if len(words) <= 4:
        return clean.upper()

    for pattern in [
        r'(BREAKOUT|SQUEEZE|PUMP|CRASH|ALERT|WARNING)',
        r'(BULL|BEAR)',
        r'(SETUP)',  # separated to avoid double SETUP\nSETUP
    ]:
        m = re.search(pattern, clean, re.IGNORECASE)
        if m:
            keyword = m.group(1).upper()
            if coin:
                if keyword == "SETUP":
                    return f"{coin}\nSETUP"
                return f"{coin}\n{keyword}\nSETUP"
            return f"CRYPTO\n{keyword}\nALERT"

    skip = {"the", "a", "an", "and", "or", "in", "on", "for", "with", "is", "are"}
    key_words = [w for w in words if w.lower() not in skip][:3]
    return "\n".join(w.upper() for w in key_words)


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
    return len(coins) >= 3


def _wrap_text(text: str, max_chars: int = 14) -> list[str]:
    """Wrap text into lines of max_chars."""
    if "\n" in text:
        return text.split("\n")

    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        if len(test) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = test
    if current:
        lines.append(current)
    return lines


def _draw_chart_bg(draw, colors):
    """Draw subtle chart-like lines in the background."""
    # Horizontal grid lines
    for y in range(100, HEIGHT, 80):
        alpha = random.randint(8, 20)
        line_color = (alpha, alpha, alpha + 10)
        draw.line([(0, y), (WIDTH, y)], fill=line_color, width=1)

    # Fake candlestick silhouettes (right side, behind coin text)
    random.seed(42)
    x = WIDTH - 500
    for i in range(25):
        candle_x = x + i * 18
        candle_h = random.randint(20, 120)
        candle_y = random.randint(200, 500)
        is_green = random.random() > 0.4

        c = (
            colors["accent_dim"][0] // 2,
            colors["accent_dim"][1] // 2,
            colors["accent_dim"][2] // 2,
        ) if is_green else (30, 10, 15)

        # Wick
        draw.line(
            [(candle_x + 4, candle_y - 15), (candle_x + 4, candle_y + candle_h + 15)],
            fill=c, width=1,
        )
        # Body
        draw.rectangle(
            [candle_x, candle_y, candle_x + 8, candle_y + candle_h],
            fill=c,
        )

    # Diagonal price line
    points = []
    x_start = WIDTH - 480
    for i in range(20):
        px = x_start + i * 24
        py = 450 - int(math.sin(i * 0.4 + 1) * 80) - i * 8
        points.append((px, py))

    if len(points) >= 2:
        line_color = (
            colors["accent"][0] // 4,
            colors["accent"][1] // 4,
            colors["accent"][2] // 4,
        )
        draw.line(points, fill=line_color, width=2)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

# ═════════════════════════════════════════════════════════════════════════════
# THUMBNAIL STYLE ROTATION (added by patch_thumbnail_rotation.py)
# ═════════════════════════════════════════════════════════════════════════════

STYLE_CLASSIC  = "classic"   # original layout, kept as fallback
STYLE_NUMBER   = "number"    # huge coin + huge score
STYLE_REACTION = "reaction"  # tilted urgent headline
STYLE_CHART    = "chart"     # rising chart visualization
STYLE_LIST     = "list"      # 3-4 coin leaderboard

ALL_STYLES = [STYLE_NUMBER, STYLE_REACTION, STYLE_CHART, STYLE_LIST, STYLE_CLASSIC]

# History file — sibling of other outputs
_THUMBNAIL_HISTORY_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "outputs"
    / "thumbnail_history.json"
)
_HISTORY_KEEP_LAST_N = 14  # enough to see 2-week patterns


# Urgent voice markers — if title contains any, STYLE_REACTION is eligible
_URGENT_MARKERS = (
    "watch", "missed", "before", "wait", "right now", "cannot ignore",
    "almost", "not waiting", "look at", "happening", "just did",
)


def _pick_thumbnail_style(script: dict, summary: dict) -> tuple[str, dict]:
    """
    Pick which style to render based on signal data + recency rotation.

    Returns: (style_name, context_dict)
      context_dict carries pre-computed values the chosen renderer needs
      (e.g. for STYLE_LIST it's the list of (coin, score) tuples).
    """
    import json

    # ── Inspect script content ───────────────────────────────────────────────
    title = (script.get("title") or "").lower()
    segments = script.get("segments") or []

    # Collect the non-meta coins with their stats and scores
    eligible_coins = []
    for seg in segments:
        coin = (seg.get("coin") or "").upper().strip()
        if coin in ("", "MARKET", "MARKET REGIME", "RISK", "INVALIDATION", "CTA"):
            continue
        stat = (seg.get("stat") or "").strip()
        eligible_coins.append((coin, stat))

    # Featured coin: prefer the coin mentioned in the title (case-insensitive
    # substring match), otherwise fall back to the highest-impact segment.
    # The BTC overview is technically a real segment but is rarely the actual
    # subject of the video — the title coin is.
    featured_coin = None
    featured_stat = None
    title_upper = title.upper()
    for coin, stat in eligible_coins:
        if coin in title_upper and coin != "BTC":
            featured_coin = coin
            featured_stat = stat
            break

    # Fallback: pick the first non-BTC segment if any, else BTC, else default
    if featured_coin is None:
        non_btc = [(c, s) for c, s in eligible_coins if c != "BTC"]
        if non_btc:
            featured_coin, featured_stat = non_btc[0]
        elif eligible_coins:
            featured_coin, featured_stat = eligible_coins[0]
        else:
            featured_coin, featured_stat = "BTC", "9.0"

    # ── Determine eligibility ────────────────────────────────────────────────
    eligible = []
    eligible.append(STYLE_NUMBER)    # always
    eligible.append(STYLE_CHART)     # always
    eligible.append(STYLE_CLASSIC)   # always (fallback)

    # STYLE_REACTION eligible only on urgent-voice titles
    if any(marker in title for marker in _URGENT_MARKERS):
        eligible.append(STYLE_REACTION)

    # STYLE_LIST eligible only when 2+ NON-BTC coins fired
    # (BTC overview segments shouldn't trigger the leaderboard style)
    non_btc_coins = [c for c, _ in eligible_coins if c != "BTC"]
    if len(non_btc_coins) >= 2:
        eligible.append(STYLE_LIST)

    # ── Rotation: pick the eligible style used LEAST recently ───────────────
    history = []
    try:
        if _THUMBNAIL_HISTORY_PATH.exists():
            history = json.loads(_THUMBNAIL_HISTORY_PATH.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                history = []
    except Exception:
        history = []

    # Count recent usage of each eligible style (last 7 entries)
    recent = history[-7:] if history else []
    recent_styles = [entry.get("style") for entry in recent if isinstance(entry, dict)]
    usage = {style: recent_styles.count(style) for style in eligible}

    # Pick least-used, alphabetical tie-break for determinism
    chosen = min(eligible, key=lambda s: (usage[s], s))

    # ── Build context for the renderer ──────────────────────────────────────
    # all_coins excludes BTC for STYLE_LIST (leaderboard shouldn't show BTC overview)
    list_coins = [(c, s) for c, s in eligible_coins if c != "BTC"]
    ctx = {
        "coin":      featured_coin,
        "stat":      featured_stat,
        "title":     script.get("title", ""),
        "all_coins": list_coins,  # [(coin, stat), ...] non-BTC only
        "regime":    (summary.get("regime") if summary else "") or "",
    }

    return chosen, ctx


# ─────────────────────────────────────────────────────────────────────────────
# Sentiment-aware color picker (added by patch_color_sentiment.py)
# ─────────────────────────────────────────────────────────────────────────────

# Indices into COLOR_SCHEMES list. If COLOR_SCHEMES ordering ever changes,
# these constants need updating. Verified against current thumbnail.py:
#   0=green, 1=cyan, 2=red, 3=gold, 4=purple
_COLOR_IDX_GREEN  = 0
_COLOR_IDX_CYAN   = 1
_COLOR_IDX_RED    = 2
_COLOR_IDX_GOLD   = 3
_COLOR_IDX_PURPLE = 4

_BULLISH_COLORS = [_COLOR_IDX_GREEN, _COLOR_IDX_CYAN, _COLOR_IDX_GOLD]
_BEARISH_COLORS = [_COLOR_IDX_RED]
# Purple kept as neutral/wildcard fallback


def _pick_sentiment_color(script: dict, ctx: dict) -> dict:
    """
    Choose a color scheme that matches the sentiment of the featured move.

    Decision order:
      1. Title contains explicit bear/crash words  → red
      2. Title contains explicit bull/pump words   → green
      3. Featured stat starts with '-'             → red (negative %)
      4. Featured stat contains '+'                → random bullish (green/cyan/gold)
      5. No clear signal                           → random across all

    Bullish picks rotate across 3 colors for visual variety; bearish only
    has 1 (red) because muted-red is the universal "down" color in trading.
    """
    title = (script.get("title") or "").lower()
    stat  = (ctx.get("stat") or "")

    # Layer 1: explicit title sentiment keywords (strongest signal)
    if any(w in title for w in ["bear", "warning", "crash", "dump", "red", "down"]):
        return COLOR_SCHEMES[_COLOR_IDX_RED]

    if any(w in title for w in ["bull", "pump", "breakout", "moon", "green", "explode"]):
        return COLOR_SCHEMES[random.choice(_BULLISH_COLORS)]

    # Layer 2: featured stat direction
    stat_clean = stat.strip()
    if stat_clean.startswith("-"):
        return COLOR_SCHEMES[_COLOR_IDX_RED]
    if "+" in stat_clean:
        return COLOR_SCHEMES[random.choice(_BULLISH_COLORS)]

    # Layer 3: fall back to existing random behavior
    return COLOR_SCHEMES[random.randint(0, len(COLOR_SCHEMES) - 1)]



def _log_thumbnail_choice(style: str, ctx: dict, scheme_name: str = "") -> None:
    """Append today's choice to thumbnail_history.json (truncated to keep last N)."""
    import json
    from datetime import datetime

    entry = {
        "timestamp":   datetime.now().isoformat(timespec="seconds"),
        "style":       style,
        "coin":        ctx.get("coin", ""),
        "stat":        ctx.get("stat", ""),
        "title":       ctx.get("title", ""),
        "scheme":      scheme_name,
        "n_coins":     len(ctx.get("all_coins", [])),
    }

    try:
        history = []
        if _THUMBNAIL_HISTORY_PATH.exists():
            history = json.loads(_THUMBNAIL_HISTORY_PATH.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                history = []
        history.append(entry)
        # Keep only the last N
        history = history[-_HISTORY_KEEP_LAST_N:]
        _THUMBNAIL_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _THUMBNAIL_HISTORY_PATH.write_text(
            json.dumps(history, indent=2),
            encoding="utf-8",
        )
    except Exception:
        # Never let history-write failure mask a successful render
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Shared rendering helpers (used by all new styles)
# ─────────────────────────────────────────────────────────────────────────────

def _text_size(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _fit_font(draw, text, max_width, max_size, min_size=40):
    """Binary-search the largest bold font size that fits text within max_width."""
    from PIL import ImageFont

    candidates = [
        "C:/Windows/Fonts/impact.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]

    def _load(size):
        for path in candidates:
            try:
                return ImageFont.truetype(path, size)
            except (OSError, IOError):
                continue
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    lo, hi = min_size, max_size
    best = min_size
    while lo <= hi:
        mid = (lo + hi) // 2
        f = _load(mid)
        w, _ = _text_size(draw, text, f)
        if w <= max_width:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return _load(best)


def _draw_glow_text(draw, xy, text, font, fill, glow_color, glow_radius=8):
    """Multi-pass glow effect behind text. Mutates the draw target."""
    x, y = xy
    for offset in range(glow_radius, 0, -2):
        gc = tuple(max(0, c // (offset // 2 + 1)) for c in glow_color)
        for dx, dy in [(-offset, 0), (offset, 0), (0, -offset), (0, offset),
                       (-offset, -offset), (offset, -offset),
                       (-offset, offset), (offset, offset)]:
            draw.text((x + dx, y + dy), text, font=font, fill=gc)
    draw.text(xy, text, font=font, fill=fill)


def _draw_brand(draw, brand_y_offset=25):
    """Place brand watermark bottom-right without clipping. brand_y_offset
    lets list-style push it slightly higher to avoid bottom row collision."""
    from PIL import ImageFont
    candidates = [
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    font = None
    for p in candidates:
        try:
            font = ImageFont.truetype(p, 22)
            break
        except (OSError, IOError):
            continue
    if font is None:
        font = ImageFont.load_default()

    text = "ALPHA SIGNALS CRYPTO"
    w, h = _text_size(draw, text, font)
    draw.text((WIDTH - w - 30, HEIGHT - h - brand_y_offset), text,
              font=font, fill=(100, 100, 120))


def _make_gradient_bg(colors, noise=True):
    """Same gradient pattern as the existing _render_classic_style."""
    from PIL import Image
    import numpy as np

    arr = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    for y in range(HEIGHT):
        t = y / HEIGHT
        for c in range(3):
            arr[y, :, c] = int(colors["bg_top"][c] * (1 - t) + colors["bg_bot"][c] * t)
    if noise:
        n = np.random.randint(-3, 4, (HEIGHT, WIDTH, 3), dtype=np.int16)
        arr = np.clip(arr.astype(np.int16) + n, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


# ─────────────────────────────────────────────────────────────────────────────
# STYLE 1 — THE NUMBER
# ─────────────────────────────────────────────────────────────────────────────
def _render_style_number(ctx: dict, colors: dict, output_path: Path) -> Path:
    from PIL import ImageDraw

    coin = ctx["coin"]
    # Extract score from stat — typical format "+9.0% 24h" or "9.0"
    # Prefer the bare score if we can derive it from the title
    score = _extract_score_display(ctx)

    img = _make_gradient_bg(colors)
    draw = ImageDraw.Draw(img)

    # Subtle grid
    for y in range(100, HEIGHT, 100):
        draw.line([(0, y), (WIDTH, y)], fill=(15, 15, 20), width=1)

    # Top accent strip
    for i in range(6):
        gc = tuple(c // (i + 2) for c in colors["accent"])
        draw.line([(0, i), (WIDTH, i)], fill=gc, width=1)

    # Bottom accent strip
    draw.rectangle([0, HEIGHT - 6, WIDTH, HEIGHT], fill=colors["accent"])

    # Coin — auto-fit
    coin_font = _fit_font(draw, coin, max_width=1080, max_size=260, min_size=120)
    cw, _ = _text_size(draw, coin, coin_font)
    coin_y = 90
    _draw_glow_text(draw, ((WIDTH - cw) // 2, coin_y), coin, coin_font,
                    fill=colors["accent"], glow_color=colors["accent"], glow_radius=14)

    # Score — auto-fit, white center for contrast against accent glow
    score_font = _fit_font(draw, score, max_width=800, max_size=320, min_size=120)
    sw, _ = _text_size(draw, score, score_font)
    score_y = 360
    _draw_glow_text(draw, ((WIDTH - sw) // 2, score_y), score, score_font,
                    fill=(255, 255, 255), glow_color=colors["accent"], glow_radius=16)

    _draw_brand(draw)
    img.save(str(output_path), "PNG", quality=95)
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# STYLE 2 — THE REACTION
# ─────────────────────────────────────────────────────────────────────────────
def _render_style_reaction(ctx: dict, colors: dict, output_path: Path) -> Path:
    from PIL import Image, ImageDraw

    coin = ctx["coin"]
    headline = _pick_reaction_headline(ctx)

    img = _make_gradient_bg(colors)
    draw = ImageDraw.Draw(img)

    # Diagonal accent banner top-right corner
    banner_layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(banner_layer)
    bdraw.polygon(
        [(WIDTH - 380, 0), (WIDTH, 0), (WIDTH, 130), (WIDTH - 270, 130)],
        fill=(*colors["accent"], 230)
    )
    bf = _fit_font(bdraw, "LIVE NOW", max_width=250, max_size=46, min_size=28)
    bw, _ = _text_size(bdraw, "LIVE NOW", bf)
    bdraw.text((WIDTH - 65 - bw, 42), "LIVE NOW", font=bf, fill=(0, 0, 0))
    img.paste(banner_layer, (0, 0), banner_layer)
    draw = ImageDraw.Draw(img)

    # Coin top-left
    coin_font = _fit_font(draw, coin, max_width=700, max_size=180, min_size=100)
    _draw_glow_text(draw, (60, 180), coin, coin_font,
                    fill=colors["accent"], glow_color=colors["accent"], glow_radius=10)

    # Tilted headline (rendered to layer, then rotated)
    headline_font = _fit_font(draw, headline, max_width=1100, max_size=180, min_size=80)
    hw, hh = _text_size(draw, headline, headline_font)
    pad = 60
    headline_layer = Image.new("RGBA", (hw + pad * 2, hh + pad * 2), (0, 0, 0, 0))
    hl_draw = ImageDraw.Draw(headline_layer)
    # Shadow
    hl_draw.text((pad + 6, pad + 6), headline, font=headline_font, fill=(0, 0, 0, 220))
    # Glow
    for offset in range(10, 0, -2):
        gc = tuple(max(0, c // (offset // 2 + 1)) for c in colors["accent"]) + (200,)
        for dx, dy in [(-offset, 0), (offset, 0), (0, -offset), (0, offset)]:
            hl_draw.text((pad + dx, pad + dy), headline, font=headline_font, fill=gc)
    # Yellow main
    hl_draw.text((pad, pad), headline, font=headline_font, fill=(255, 230, 50, 255))
    rotated = headline_layer.rotate(-4, resample=Image.BICUBIC, expand=True)
    rx = (WIDTH - rotated.width) // 2
    img.paste(rotated, (rx, 400), rotated)
    draw = ImageDraw.Draw(img)

    # Bottom strip
    draw.rectangle([0, HEIGHT - 6, WIDTH, HEIGHT], fill=colors["accent"])
    _draw_brand(draw)
    img.save(str(output_path), "PNG", quality=95)
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# STYLE 3 — THE CHART
# ─────────────────────────────────────────────────────────────────────────────
def _render_style_chart(ctx: dict, colors: dict, output_path: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFilter
    import math

    coin = ctx["coin"]
    pct = _extract_pct_display(ctx)

    img = _make_gradient_bg(colors)
    draw = ImageDraw.Draw(img)

    # Grid
    for y in range(100, HEIGHT, 80):
        draw.line([(0, y), (WIDTH, y)], fill=(12, 12, 18), width=1)
    for x in range(100, WIDTH, 100):
        draw.line([(x, 0), (x, HEIGHT)], fill=(12, 12, 18), width=1)

    # Chart on right half, shifted down so coin text has clearance
    points = []
    chart_left = WIDTH // 2 + 60
    chart_right = WIDTH - 100
    chart_bottom = HEIGHT - 130
    chart_top = 280
    n = 30
    for i in range(n):
        t = i / (n - 1)
        x = chart_left + int(t * (chart_right - chart_left))
        y_norm = t ** 2.2
        y = chart_bottom - int(y_norm * (chart_bottom - chart_top))
        y += int(math.sin(i * 1.3) * 8)
        points.append((x, y))

    # Glow underneath
    line_layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    ld = ImageDraw.Draw(line_layer)
    fill_pts = points + [(chart_right, chart_bottom), (chart_left, chart_bottom)]
    ld.polygon(fill_pts, fill=(*colors["accent"], 40))
    line_layer = line_layer.filter(ImageFilter.GaussianBlur(radius=8))
    img.paste(line_layer, (0, 0), line_layer)
    draw = ImageDraw.Draw(img)

    # Chart line — multi-pass for thickness + glow
    for w in [10, 7, 4]:
        af = w / 10
        c = tuple(int(ch * af) for ch in colors["accent"])
        draw.line(points, fill=c, width=w)
    draw.line(points, fill=(255, 255, 255), width=2)

    # Arrow at peak
    last_x, last_y = points[-1]
    arrow_font = _fit_font(draw, "▲", max_width=200, max_size=80, min_size=40)
    draw.text((last_x + 5, last_y - 70), "▲", font=arrow_font, fill=colors["accent"])

    # Coin left side, fit within 540px
    coin_font = _fit_font(draw, coin, max_width=540, max_size=180, min_size=100)
    _draw_glow_text(draw, (60, 210), coin, coin_font,
                    fill=(255, 255, 255), glow_color=colors["accent"], glow_radius=12)

    # % under coin
    pct_font = _fit_font(draw, pct, max_width=540, max_size=130, min_size=80)
    _draw_glow_text(draw, (60, 440), pct, pct_font,
                    fill=colors["accent"], glow_color=colors["accent"], glow_radius=10)

    draw.rectangle([0, HEIGHT - 6, WIDTH, HEIGHT], fill=colors["accent"])
    _draw_brand(draw)
    img.save(str(output_path), "PNG", quality=95)
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# STYLE 4 — THE LIST
# ─────────────────────────────────────────────────────────────────────────────
def _render_style_list(ctx: dict, colors: dict, output_path: Path) -> Path:
    from PIL import ImageDraw

    coins_with_stats = ctx.get("all_coins", [])
    # Convert stats to short scores. Prefer numeric score if obvious, else use stat as-is.
    rows = []
    for coin, stat in coins_with_stats[:4]:
        score = _extract_row_score(stat)
        rows.append((coin, score))

    if len(rows) < 2:
        # Should not happen — eligibility check upstream prevents this
        rows = [(ctx.get("coin", "BTC"), "9.0")]

    img = _make_gradient_bg(colors)
    draw = ImageDraw.Draw(img)

    # Banner text: match what's actually shown in the rows.
    # If row values look like percentages (contain %), use "TODAY'S MOVERS"
    # otherwise it's confluence scores → "TODAY'S 9.0s"
    has_pct = any("%" in score for _, score in rows)
    if len(rows) > 1:
        banner_text = "TODAY'S MOVERS" if has_pct else "TODAY'S 9.0s"
    else:
        banner_text = "TODAY'S PICK"
    banner_font = _fit_font(draw, banner_text, max_width=720, max_size=64, min_size=36)
    bw, bh = _text_size(draw, banner_text, banner_font)
    banner_x = (WIDTH - bw) // 2
    banner_y = 35
    draw.rounded_rectangle(
        [banner_x - 30, banner_y - 8, banner_x + bw + 30, banner_y + bh + 24],
        radius=12,
        fill=(colors["accent"][0] // 4, colors["accent"][1] // 4, colors["accent"][2] // 4),
        outline=colors["accent"],
        width=3,
    )
    draw.text((banner_x, banner_y), banner_text, font=banner_font, fill=colors["accent"])

    # Row colors — vibrant variety
    row_colors = [
        (0, 255, 120),    # green
        (0, 200, 255),    # cyan
        (255, 200, 0),    # gold
        (255, 100, 200),  # pink
    ]

    n = len(rows)
    # Reserve more bottom space when 4 rows so brand watermark doesn't collide
    bottom_reserve = 100 if n >= 4 else 60
    available_h = HEIGHT - 170 - bottom_reserve
    row_h = available_h // n
    max_coin_size = min(120, int(row_h * 0.62))

    start_y = 170
    for i, (coin, score) in enumerate(rows):
        row_center = start_y + i * row_h + row_h // 2
        color = row_colors[i % len(row_colors)]

        coin_font = _fit_font(draw, coin, max_width=700, max_size=max_coin_size, min_size=60)
        _, ch = _text_size(draw, coin, coin_font)
        coin_y = row_center - ch // 2

        score_size = int(max_coin_size * 0.75)
        score_font = _fit_font(draw, score, max_width=350, max_size=score_size, min_size=40)
        sw, sh = _text_size(draw, score, score_font)
        score_y = row_center - sh // 2

        _draw_glow_text(draw, (80, coin_y), coin, coin_font,
                        fill=color, glow_color=color, glow_radius=6)
        _draw_glow_text(draw, (WIDTH - 80 - sw, score_y), score, score_font,
                        fill=(255, 255, 255), glow_color=color, glow_radius=5)

    draw.rectangle([0, HEIGHT - 6, WIDTH, HEIGHT], fill=colors["accent"])
    # Push brand higher when 4 rows to avoid collision with bottom row text
    _draw_brand(draw, brand_y_offset=25 if n < 4 else 70)
    img.save(str(output_path), "PNG", quality=95)
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# Stat parsing helpers — extract clean display strings from scanner data
# ─────────────────────────────────────────────────────────────────────────────

def _extract_score_display(ctx: dict) -> str:
    """For STYLE_NUMBER. Prefer a confluence score (e.g. '9.0') over a % move.
    Falls back to whatever stat we have."""
    import re
    title = ctx.get("title", "")
    # Look for pattern like "9.0" or "8.5" in the title
    m = re.search(r"\b(\d+\.\d)\b", title)
    if m:
        return m.group(1)
    # Fall back to the segment stat
    stat = ctx.get("stat", "")
    if stat:
        # Strip parenthetical, take first token
        cleaned = stat.split(",")[0].strip()
        return cleaned[:8] if len(cleaned) > 8 else cleaned
    return "9.0"


def _extract_pct_display(ctx: dict) -> str:
    """For STYLE_CHART. Find the % move from the stat. Format like '+9%'."""
    import re
    stat = ctx.get("stat", "")
    m = re.search(r"([+\-]?\d+(?:\.\d)?)\s*%", stat)
    if m:
        n = m.group(1)
        # Add + sign if no sign present and likely positive
        if not n.startswith(("+", "-")):
            n = "+" + n
        return f"{n}%"
    # Fall back to score-style
    return _extract_score_display(ctx)


def _extract_row_score(stat: str) -> str:
    """For STYLE_LIST rows. Short numeric. Prefer score over %."""
    import re
    # Match pattern like "+13.4% 24h" — take the percentage
    m = re.search(r"([+\-]?\d+(?:\.\d)?)\s*%", stat or "")
    if m:
        n = m.group(1)
        if not n.startswith(("+", "-")):
            n = "+" + n
        return f"{n}%"
    # Fall back: any number
    m = re.search(r"(\d+\.\d)", stat or "")
    if m:
        return m.group(1)
    return stat[:6] if stat else "9.0"


def _pick_reaction_headline(ctx: dict) -> str:
    """For STYLE_REACTION. Pick a short urgent phrase based on title voice."""
    title = (ctx.get("title") or "").lower()
    # Map title vibes to short reaction phrases
    if "missed" in title or "almost" in title:
        return "I MISSED IT"
    if "watch" in title:
        return "WATCH NOW"
    if "wait" in title or "right now" in title:
        return "RIGHT NOW"
    if "before" in title:
        return "BEFORE IT MOVES"
    if "cannot ignore" in title or "can't ignore" in title:
        return "DON'T MISS"
    return "WATCH NOW"


# ─────────────────────────────────────────────────────────────────────────────
# Master dispatcher used by generate_thumbnail() — see patched body above
# ─────────────────────────────────────────────────────────────────────────────
def _dispatch_style_render(
    style: str,
    ctx: dict,
    colors: dict,
    output_path: Path,
) -> Path:
    """Route to the appropriate renderer. Falls back to classic on any failure."""
    renderers = {
        STYLE_NUMBER:   _render_style_number,
        STYLE_REACTION: _render_style_reaction,
        STYLE_CHART:    _render_style_chart,
        STYLE_LIST:     _render_style_list,
    }
    renderer = renderers.get(style)
    if renderer is None:
        return None  # signals caller to use classic
    try:
        return renderer(ctx, colors, output_path)
    except Exception as e:
        # If a new style throws for any reason, fall back to classic silently.
        # Classic is the proven-working renderer.
        import sys as _sys
        print(f"  [thumbnail] style {style!r} failed ({e}), falling back to classic",
              file=_sys.stderr)
        return None

# ═════════════════════════════════════════════════════════════════════════════
# End of rotation block
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate YouTube thumbnail")
    parser.add_argument("--title", type=str, default="Crypto Scanner Alert",
                        help="Video title")
    parser.add_argument("--coin", type=str, default="BTC",
                        help="Featured coin symbol")
    parser.add_argument("--stat", type=str, default="+12.5%",
                        help="Key stat to display")
    parser.add_argument("--regime", type=str, default="BULL",
                        help="Market regime (BULL/BEAR/SIDEWAYS)")
    parser.add_argument("--scheme", type=int, default=None,
                        help="Color scheme index (0-4)")
    parser.add_argument("--output", type=str, default="thumbnail.png",
                        help="Output path")
    args = parser.parse_args()

    script = {
        "title": args.title,
        "segments": [
            {"coin": args.coin, "stat": args.stat, "narration": ""},
        ],
    }
    summary = {"regime": args.regime}

    out = generate_thumbnail(script, summary, Path(args.output), args.scheme)
    print(f"Thumbnail saved: {out}")
