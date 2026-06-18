"""
visuals.py — Professional-grade visual frames for video segments.

Renders cinematic trading-terminal aesthetic frames:
  - Dark gradient backgrounds with subtle noise
  - Proper typography hierarchy
  - Neon accent glows and data overlays
  - Candlestick-style price charts
  - Signal stack displays with animated-look bars
  - Market heatmap grids

All frames rendered at target resolution via matplotlib + PIL.
"""

from __future__ import annotations

import logging
import math
import random
from pathlib import Path

log = logging.getLogger("video_pipeline.visuals")

# ─────────────────────────────────────────────────────────────────────────────
# COLOR PALETTE — cinematic trading terminal
# ─────────────────────────────────────────────────────────────────────────────
C = {
    "bg_dark":     "#060610",
    "bg_mid":      "#0c0c1a",
    "bg_card":     "#111125",
    "grid":        "#16162e",
    "text_bright": "#f0f0f5",
    "text_mid":    "#a0a0b8",
    "text_dim":    "#555570",
    "green":       "#00e87b",
    "green_dim":   "#0a5a3a",
    "red":         "#ff3b5c",
    "red_dim":     "#5a1525",
    "cyan":        "#00d4ff",
    "purple":      "#9966ff",
    "yellow":      "#ffcc00",
    "orange":      "#ff8844",
}

# Channel branding — used on the SUBSCRIBE card and footers
CHANNEL_NAME = "ALPHA SIGNALS CRYPTO"


def render_all_frames(
    script:     dict,
    summary:    dict,
    output_dir: Path,
    size:       tuple[int, int] = (1080, 1920),
) -> list[Path]:
    """Render one professional frame per script segment."""
    import matplotlib
    matplotlib.use("Agg")

    frame_paths: list[Path] = []
    segments = script.get("segments", [])

    # Build coin lookup
    coin_data = {}
    for src in ("top_coins", "extended_coins", "ignition_watch_now"):
        for coin in summary.get(src, []):
            coin_data[coin.get("symbol", "")] = coin

    for i, seg in enumerate(segments):
        visual_type = seg.get("visual_type", "stat_card")
        coin_sym    = seg.get("coin", "").upper()
        coin_info   = coin_data.get(coin_sym, {})

        log.info(f"  Frame {i+1}/{len(segments)}: {coin_sym} ({visual_type})")

        try:
            if visual_type == "price_chart":
                img = _render_price_chart(seg, coin_info, summary, size)
            elif visual_type == "signal_stack":
                img = _render_signal_stack(seg, coin_info, summary, size)
            elif visual_type == "heatmap":
                img = _render_heatmap(seg, summary, size)
            else:
                img = _render_stat_card(seg, coin_info, summary, size)
        except Exception as e:
            log.warning(f"  Frame {i} error: {e} — using stat_card fallback")
            img = _render_stat_card(seg, coin_info, summary, size)

        path = output_dir / f"frame_{i:03d}.png"
        img.save(str(path), "PNG", quality=95)
        frame_paths.append(path)

    return frame_paths


# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _clean_stat(stat: str) -> str:
    """
    Clean stat text for display — keep only short data like percentages,
    prices, confluence scores. Remove any sentence-like text.
    """
    if not stat:
        return ""

    import re

    # If it's already short (under 20 chars), it's probably fine
    stat = stat.strip()

    # Extract percentage if present (e.g. "+4.5% 24h" → "+4.5%")
    pct_match = re.search(r'[+-]?\d+\.?\d*%', stat)
    if pct_match:
        return pct_match.group(0) + " 24h"

    # Extract confluence/conviction score
    conf_match = re.search(r'(?:conf|conv)[a-z]*[\s:=]*(\d+\.?\d*)', stat, re.IGNORECASE)
    if conf_match:
        return f"CONV {conf_match.group(1)}"

    # Extract price
    price_match = re.search(r'\$[\d,.]+', stat)
    if price_match:
        return price_match.group(0)

    # If under 20 chars, use as-is
    if len(stat) <= 20:
        return stat

    # Too long — take first 18 chars
    return stat[:18].strip()


def _create_bg(w: int, h: int) -> "Image.Image":
    """Create a dark gradient background with subtle noise."""
    from PIL import Image, ImageDraw
    import numpy as np

    # Vertical gradient: dark top to slightly lighter bottom
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    top = _hex_to_rgb(C["bg_dark"])
    bot = _hex_to_rgb(C["bg_mid"])
    for y in range(h):
        t = y / h
        for c in range(3):
            arr[y, :, c] = int(top[c] * (1 - t) + bot[c] * t)

    # Add subtle noise for texture
    noise = np.random.randint(-4, 5, (h, w, 3), dtype=np.int16)
    arr = np.clip(arr.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    return Image.fromarray(arr, "RGB")


def _draw_glow_line(draw, y: int, w: int, color: str, thickness: int = 2):
    """Draw a horizontal glowing accent line."""
    from PIL import ImageDraw as ID
    rgb = _hex_to_rgb(color)
    # Outer glow (dim)
    for offset in range(4, 0, -1):
        alpha_color = tuple(max(0, c - offset * 30) for c in rgb)
        draw.line([(0, y - offset), (w, y - offset)], fill=alpha_color, width=1)
        draw.line([(0, y + offset), (w, y + offset)], fill=alpha_color, width=1)
    # Core line
    draw.line([(0, y), (w, y)], fill=color, width=thickness)


def _get_fonts(w: int, h: int | None = None) -> dict:
    """Load fonts at various sizes.

    Sizing is based on the *shorter* screen dimension, not width. Width alone
    makes landscape long-form (1920x1080) fonts ~78% bigger than portrait
    Shorts (1080x1920) for the same visual weight. Using min(w, h) gives both
    orientations a consistent reference (1080), so text reads the same size
    regardless of orientation.
    """
    from PIL import ImageFont

    ref = min(w, h) if h else w

    candidates = [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/impact.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]

    base_font = None
    for path in candidates:
        try:
            from PIL import ImageFont
            base_font = path
            ImageFont.truetype(path, 20)  # test it
            break
        except (OSError, IOError):
            continue

    def make(size):
        if base_font:
            try:
                return ImageFont.truetype(base_font, size)
            except Exception:
                pass
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    return {
        "hero":    make(int(ref * 0.095)),  # huge coin symbol
        "title":   make(int(ref * 0.062)),  # section titles
        "stat":    make(int(ref * 0.050)),  # big numbers
        "body":    make(int(ref * 0.032)),  # body text
        "label":   make(int(ref * 0.025)),  # small labels
        "tiny":    make(int(ref * 0.018)),  # watermarks
    }


def _draw_badge(draw, x: int, y: int, text: str, color: str, font, padding: int = 8):
    """Draw a rounded badge with text."""
    bbox = font.getbbox(text)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    rx, ry = x, y
    rw, rh = tw + padding * 2, th + padding * 2
    # Background
    bg_rgb = _hex_to_rgb(color)
    dim_bg = tuple(max(0, c // 4) for c in bg_rgb)
    draw.rounded_rectangle(
        [rx, ry, rx + rw, ry + rh],
        radius=6, fill=dim_bg, outline=color,
    )
    draw.text((rx + padding, ry + padding - 2), text, font=font, fill=color)
    return rw


# ─────────────────────────────────────────────────────────────────────────────
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
# ─────────────────────────────────────────────────────────────────────────────
_SEGMENT_DISPLAY_LABELS = {
    "MARKET":          "MARKET REGIME",
    "MARKET REGIME":   "MARKET REGIME",
    "RISK":            "WEEKEND RISK",
    "INVALIDATION":    "WHAT KILLS THESE",
    "CTA":             "SUBSCRIBE",
    "WHAT_DIDNT_WORK": "HONEST MISS",
    "WHAT DIDNT WORK": "HONEST MISS",
    "MISSES":          "HONEST MISS",
    "MISS":            "HONEST MISS",
    "WIN_RATE":        "WIN RATE",
    "WINRATE":         "WIN RATE",
    "STATS":           "RESULTS",
    "RESULTS":         "RESULTS",
    "CONCEPT":         "THE CONCEPT",
    "MISTAKE":         "COMMON MISTAKE",
    "FAILURE":         "WHEN IT FAILS",
}


def _segment_display_label(coin_field: str) -> str:
    """Translate internal segment label to viewer-friendly title."""
    upper = (coin_field or "").upper()
    return _SEGMENT_DISPLAY_LABELS.get(upper, upper)
# -----------------------------------------------------------------------------
# STAT CARD SUB-RENDERERS (special segment layouts)
# -----------------------------------------------------------------------------

def _stat_card_market(draw, summary, fonts, w, h):
    """MARKET REGIME card: regime badge + live BTC price + 7-day move."""
    regime = summary.get("regime", "UNKNOWN").upper()
    regime_color = {"BULL": C["green"], "BEAR": C["red"]}.get(regime, C["yellow"])

    y = int(h * 0.13)
    _draw_badge(draw, int(w * 0.08), y, f"  {regime} MARKET  ",
                regime_color, fonts["body"])

    y += int(h * 0.10)
    title = "MARKET REGIME"
    tf = _fit_font(draw, title, int(w * 0.84), fonts["title"])
    draw.text((int(w * 0.08), y), title, font=tf, fill=C["text_bright"])

    # BTC price block
    btc = summary.get("btc_price")
    y += int(h * 0.16)
    draw.text((int(w * 0.08), y), "BITCOIN", font=fonts["label"], fill=C["text_dim"])
    y += int(h * 0.06)
    if btc:
        price_txt = f"${btc:,.0f}"
        pf = _fit_font(draw, price_txt, int(w * 0.84), fonts["hero"])
        draw.text((int(w * 0.08), y), price_txt, font=pf, fill=C["cyan"])
    else:
        draw.text((int(w * 0.08), y), "price unavailable",
                  font=fonts["stat"], fill=C["text_dim"])

    # 7-day move badge
    btc_7d = summary.get("btc_7d_pct")
    if btc_7d is not None:
        try:
            v = float(btc_7d)
            c = C["green"] if v >= 0 else C["red"]
            y += int(h * 0.14)
            _draw_badge(draw, int(w * 0.08), y, f"  {v:+.1f}%  /  7D  ",
                        c, fonts["body"])
        except (ValueError, TypeError):
            pass


def _stat_card_subscribe(draw, summary, fonts, w, h):
    """SUBSCRIBE / CTA card: big SUBSCRIBE + channel name + tagline."""
    y = int(h * 0.24)
    sub = "SUBSCRIBE"
    sf = _fit_font(draw, sub, int(w * 0.84), fonts["hero"])
    draw.text((int(w * 0.08), y), sub, font=sf, fill=C["green"])

    y += int(h * 0.16)
    name = CHANNEL_NAME
    nf = _fit_font(draw, name, int(w * 0.84), fonts["title"])
    draw.text((int(w * 0.08), y), name, font=nf, fill=C["text_bright"])

    y += int(h * 0.10)
    draw.text((int(w * 0.08), y),
              "New scanner reports & breakdowns every week",
              font=fonts["label"], fill=C["text_mid"])

    y += int(h * 0.07)
    draw.text((int(w * 0.08), y), "Hit the bell so you don't miss a setup",
              font=fonts["label"], fill=C["text_dim"])


def _stat_card_results(draw, summary, fonts, w, h):
    """RESULTS / STATS card: win rate + average return + per-coin breakdown."""
    perf = summary.get("performance", {}) or {}
    rows = summary.get("results_breakdown") or []

    y = int(h * 0.10)
    draw.text((int(w * 0.08), y), "RESULTS", font=fonts["title"], fill=C["text_bright"])

    # Win rate (big)
    wr = perf.get("win_rate_pct")
    y += int(h * 0.10)
    if wr is not None:
        wr_txt = f"{float(wr):.0f}% WIN RATE"
        wf = _fit_font(draw, wr_txt, int(w * 0.84), fonts["stat"])
        draw.text((int(w * 0.08), y), wr_txt, font=wf, fill=C["green"])

    # Average return
    avg = perf.get("avg_return_pct")
    if avg is not None:
        try:
            av = float(avg)
            y += int(h * 0.075)
            draw.text((int(w * 0.08), y),
                      f"avg return  {av:+.1f}%",
                      font=fonts["label"],
                      fill=C["green"] if av >= 0 else C["red"])
        except (ValueError, TypeError):
            pass

    # Per-coin breakdown
    y += int(h * 0.085)
    _draw_glow_line(draw, y, w, C["grid"], thickness=1)
    y += int(h * 0.035)
    draw.text((int(w * 0.08), y), "PAST PICKS", font=fonts["label"], fill=C["text_dim"])
    y += int(h * 0.06)

    if not rows:
        # Fall back to best / worst pick if a full breakdown wasn't supplied
        for key, label_c in (("best_pick", C["green"]), ("worst_pick", C["red"])):
            sym = perf.get(key)
            if sym:
                ret = perf.get(key.replace("pick", "return_pct"))
                ret_txt = f"{float(ret):+.1f}%" if ret is not None else ""
                draw.text((int(w * 0.08), y), sym, font=fonts["body"], fill=C["text_bright"])
                draw.text((int(w * 0.55), y), ret_txt, font=fonts["body"], fill=label_c)
                y += int(h * 0.065)
        return

    for r in rows[:5]:
        sym = str(r.get("symbol", "?")).upper()
        try:
            ret = float(r.get("return_pct", 0))
        except (ValueError, TypeError):
            ret = 0.0
        outcome = (r.get("outcome") or ("win" if ret >= 0 else "loss")).lower()
        row_c = C["green"] if outcome == "win" else C["red"]
        mark = "WIN" if outcome == "win" else "LOSS"
        draw.text((int(w * 0.08), y), sym, font=fonts["body"], fill=C["text_bright"])
        draw.text((int(w * 0.45), y), f"{ret:+.1f}%", font=fonts["body"], fill=row_c)
        draw.text((int(w * 0.74), y), mark, font=fonts["label"], fill=row_c)
        y += int(h * 0.065)




# ─────────────────────────────────────────────────────────────────────────────
# STAT CARD — opening/closing market overview
# ─────────────────────────────────────────────────────────────────────────────

def _render_stat_card(seg, coin, summary, size):
    from PIL import Image, ImageDraw

    w, h = size
    img = _create_bg(w, h)
    draw = ImageDraw.Draw(img)
    fonts = _get_fonts(w, h)

    # Top accent line
    _draw_glow_line(draw, int(h * 0.06), w, C["cyan"])

    raw_coin = seg.get("coin", "MARKET").upper()
    coin_text = _segment_display_label(raw_coin)

    # Dispatch to a dedicated layout for the special meta cards, otherwise
    # fall through to the generic regime + title + stat layout.
    if raw_coin in ("MARKET", "MARKET REGIME"):
        _stat_card_market(draw, summary, fonts, w, h)

    elif raw_coin in ("CTA", "SUBSCRIBE"):
        _stat_card_subscribe(draw, summary, fonts, w, h)

    elif raw_coin in ("STATS", "RESULTS", "WIN_RATE", "WINRATE"):
        _stat_card_results(draw, summary, fonts, w, h)

    else:
        # Regime badge
        regime = summary.get("regime", "unknown").upper()
        regime_color = {"BULL": C["green"], "BEAR": C["red"]}.get(regime, C["yellow"])
        y = int(h * 0.12)
        _draw_badge(draw, int(w * 0.08), y, f"  {regime}  ", regime_color, fonts["body"])

        # Main title (translate internal labels like RISK/INVALIDATION).
        # For long phrases, auto-shrink the font so it fits within the screen.
        y += int(h * 0.10)
        max_title_w = int(w * 0.84)
        title_font = _fit_font(draw, coin_text, max_title_w, fonts["hero"])
        draw.text((int(w * 0.08), y), coin_text,
                  font=title_font, fill=C["text_bright"])

        # Stat (big number) — only for real coin segments, never the remaining
        # meta segments (RISK / INVALIDATION) where % data would be bogus.
        is_meta_segment = raw_coin in ("RISK", "INVALIDATION")
        y += int(h * 0.22)
        if not is_meta_segment:
            stat = _clean_stat(seg.get("stat", ""))
            if stat:
                stat_color = C["green"] if "+" in stat else C["red"] if "-" in stat else C["cyan"]
                draw.text((int(w * 0.08), y), stat,
                          font=fonts["stat"], fill=stat_color)

    # Bottom bar with date
    _draw_glow_line(draw, int(h * 0.88), w, C["purple"], thickness=1)
    draw.text((int(w * 0.08), int(h * 0.90)),
              f"{'WEEKEND SETUPS' if summary.get('_video_type') == 'weekly_friday' else 'SCANNER DAILY'}  •  {summary.get('date', '')}",
              font=fonts["label"], fill=C["text_dim"])

    # Branding
    draw.text((int(w * 0.08), int(h * 0.94)),
              "CRYPTO ALPHA SCANNER", font=fonts["tiny"], fill=C["text_dim"])

    return img


# ─────────────────────────────────────────────────────────────────────────────
# PRICE CHART — candlestick-style with signal overlays
# ─────────────────────────────────────────────────────────────────────────────

def _render_price_chart(seg, coin, summary, size):
    from PIL import Image, ImageDraw
    import numpy as np

    w, h = size
    img = _create_bg(w, h)
    draw = ImageDraw.Draw(img)
    fonts = _get_fonts(w, h)

    coin_sym = seg.get("coin", "?").upper()
    change = coin.get("change_24h", 0) or 0
    is_bullish = change >= 0
    accent = C["green"] if is_bullish else C["red"]

    # ── Header ───────────────────────────────────────────────────────────────
    _draw_glow_line(draw, int(h * 0.04), w, accent)

    y = int(h * 0.06)
    draw.text((int(w * 0.08), y), coin_sym,
              font=fonts["title"], fill=C["text_bright"])

    # Change badge
    chg_text = f" {change:+.1f}% "
    badge_x = int(w * 0.55)
    _draw_badge(draw, badge_x, y + 5, chg_text, accent, fonts["body"])

    # Confluence score
    conv = coin.get("confluence", 0) or coin.get("conviction", 0) or 0
    if conv:
        _draw_badge(draw, badge_x + int(w * 0.22), y + 5,
                    f" CONV {conv:.0f} ", C["cyan"], fonts["body"])

    # ── Candlestick chart area ───────────────────────────────────────────────
    chart_top    = int(h * 0.16)
    chart_bottom = int(h * 0.62)
    chart_left   = int(w * 0.08)
    chart_right  = int(w * 0.92)
    chart_h = chart_bottom - chart_top
    chart_w = chart_right - chart_left

    # Draw grid lines
    for gy in range(5):
        gy_pos = chart_top + int(chart_h * gy / 4)
        draw.line([(chart_left, gy_pos), (chart_right, gy_pos)],
                  fill=C["grid"], width=1)

    # Generate synthetic candles
    np.random.seed(hash(coin_sym) % 2**31)
    n_candles = 36
    base_price = coin.get("price", 1.0) or 1.0

    opens, highs, lows, closes = [], [], [], []
    price = base_price * (1 - change / 100)
    for j in range(n_candles):
        volatility = abs(change / 100) * 1.5 + 0.01
        delta = np.random.normal(change / 100 / n_candles, volatility / n_candles)
        o = price
        c = price * (1 + delta)
        hi = max(o, c) * (1 + abs(np.random.normal(0, 0.003)))
        lo = min(o, c) * (1 - abs(np.random.normal(0, 0.003)))
        opens.append(o); highs.append(hi); lows.append(lo); closes.append(c)
        price = c

    all_prices = highs + lows
    p_min, p_max = min(all_prices), max(all_prices)
    p_range = p_max - p_min or 1

    candle_w = max(4, int(chart_w / n_candles * 0.7))
    gap_w = max(2, int(chart_w / n_candles * 0.3))

    def price_to_y(p):
        return chart_bottom - int((p - p_min) / p_range * chart_h * 0.9) - int(chart_h * 0.05)

    for j in range(n_candles):
        x = chart_left + j * (candle_w + gap_w)
        if x + candle_w > chart_right:
            break

        o_y = price_to_y(opens[j])
        c_y = price_to_y(closes[j])
        h_y = price_to_y(highs[j])
        l_y = price_to_y(lows[j])

        color = C["green"] if closes[j] >= opens[j] else C["red"]
        dim_color = C["green_dim"] if closes[j] >= opens[j] else C["red_dim"]

        # Wick
        mid_x = x + candle_w // 2
        draw.line([(mid_x, h_y), (mid_x, l_y)], fill=color, width=1)

        # Body
        body_top = min(o_y, c_y)
        body_bot = max(o_y, c_y)
        if body_bot - body_top < 2:
            body_bot = body_top + 2
        draw.rectangle([x, body_top, x + candle_w, body_bot], fill=color)

    # Current price line
    cur_y = price_to_y(closes[-1])
    draw.line([(chart_left, cur_y), (chart_right, cur_y)],
              fill=accent, width=1)
    draw.text((chart_right - int(w * 0.18), cur_y - 18),
              f"${closes[-1]:.4f}" if closes[-1] < 1 else f"${closes[-1]:.2f}",
              font=fonts["label"], fill=accent)

    # ── Signals section ──────────────────────────────────────────────────────
    y = int(h * 0.66)
    _draw_glow_line(draw, y, w, C["purple"], thickness=1)
    y += int(h * 0.02)

    draw.text((int(w * 0.08), y), "ACTIVE SIGNALS",
              font=fonts["label"], fill=C["text_dim"])
    y += int(h * 0.055)  # was 0.035 — body font is taller, needs more space

    signals = coin.get("signals", [])
    signal_colors = [C["cyan"], C["green"], C["purple"], C["yellow"], C["orange"]]
    for si, sig_name in enumerate(signals[:5]):
        sc = signal_colors[si % len(signal_colors)]
        clean = sig_name.replace("_", " ").upper()
        draw.text((int(w * 0.08), y), f"●", font=fonts["body"], fill=sc)
        draw.text((int(w * 0.13), y), clean, font=fonts["body"], fill=C["text_mid"])
        y += int(h * 0.055)  # was 0.035 — fixes line overlap

    # Footer
    draw.text((int(w * 0.08), int(h * 0.95)),
              "CRYPTO ALPHA SCANNER", font=fonts["tiny"], fill=C["text_dim"])

    return img


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL STACK — fired signals with strength bars
# ─────────────────────────────────────────────────────────────────────────────

def _render_signal_stack(seg, coin, summary, size):
    from PIL import Image, ImageDraw

    w, h = size
    img = _create_bg(w, h)
    draw = ImageDraw.Draw(img)
    fonts = _get_fonts(w, h)

    raw_coin = seg.get("coin", "?").upper()
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
              font=fonts["body"], fill=C["cyan"])

    y += int(h * 0.06)
    _draw_glow_line(draw, y, w, C["grid"], thickness=1)
    y += int(h * 0.03)

    # Signal bars
    signals = coin.get("signals", [])
    bar_x = int(w * 0.08)
    bar_max_w = int(w * 0.65)
    bar_h = int(h * 0.022)
    signal_colors = [C["cyan"], C["green"], C["purple"], C["yellow"], C["orange"],
                     C["green"], C["cyan"], C["purple"]]

    for si, sig_name in enumerate(signals[:8]):
        sc = signal_colors[si % len(signal_colors)]
        clean = sig_name.replace("_", " ").upper()

        # Signal name
        draw.text((bar_x, y), clean, font=fonts["label"], fill=C["text_mid"])
        y += int(h * 0.045)  # was 0.028 — fixes signal-stack overlap

        # Bar background
        draw.rounded_rectangle(
            [bar_x, y, bar_x + bar_max_w, y + bar_h],
            radius=3, fill=C["grid"],
        )

        # Bar fill (randomized strength for visual appeal)
        random.seed(hash(sig_name))
        fill_pct = 0.5 + random.random() * 0.45
        fill_w = int(bar_max_w * fill_pct)
        draw.rounded_rectangle(
            [bar_x, y, bar_x + fill_w, y + bar_h],
            radius=3, fill=sc,
        )

        # Percentage label
        draw.text((bar_x + bar_max_w + 10, y - 2),
                  f"{fill_pct * 100:.0f}%", font=fonts["tiny"], fill=sc)

        y += int(h * 0.04)

    # Conviction total
    conv = coin.get("confluence", 0) or coin.get("conviction", 0) or 0
    if conv:
        y = max(y + int(h * 0.03), int(h * 0.72))
        draw.text((bar_x, y), f"CONVICTION SCORE",
                  font=fonts["label"], fill=C["text_dim"])
        y += int(h * 0.03)
        draw.text((bar_x, y), f"{conv:.0f}",
                  font=fonts["hero"], fill=C["cyan"])

    # Footer
    draw.text((int(w * 0.08), int(h * 0.95)),
              "CRYPTO ALPHA SCANNER", font=fonts["tiny"], fill=C["text_dim"])

    return img


# ─────────────────────────────────────────────────────────────────────────────
# HEATMAP — market overview grid
# ─────────────────────────────────────────────────────────────────────────────

def _render_heatmap(seg, summary, size):
    from PIL import Image, ImageDraw

    w, h = size
    img = _create_bg(w, h)
    draw = ImageDraw.Draw(img)
    fonts = _get_fonts(w, h)

    # Header
    _draw_glow_line(draw, int(h * 0.04), w, C["cyan"])
    y = int(h * 0.07)
    draw.text((int(w * 0.08), y), "MARKET OVERVIEW",
              font=fonts["title"], fill=C["text_bright"])
    y += int(h * 0.08)

    coins = summary.get("top_coins", []) + summary.get("extended_coins", [])
    if not coins:
        draw.text((int(w * 0.08), y), "No data", font=fonts["body"], fill=C["text_dim"])
        return img

    # Grid
    cols = 3
    rows = min(6, (len(coins) + cols - 1) // cols)
    cell_w = int((w * 0.84) / cols)
    cell_h = int(min(cell_w * 0.9, (h * 0.70) / rows))
    start_x = int(w * 0.08)
    pad = 4

    for idx, coin_d in enumerate(coins[:cols * rows]):
        col = idx % cols
        row = idx // cols
        cx = start_x + col * cell_w
        cy = y + row * cell_h

        change = coin_d.get("change_24h", 0) or 0

        if change > 8:
            bg, fg = "#0d5535", C["green"]
        elif change > 3:
            bg, fg = "#0a3a25", "#66dd99"
        elif change > 0:
            bg, fg = "#152218", "#88bb88"
        elif change > -3:
            bg, fg = "#221518", "#bb8888"
        elif change > -8:
            bg, fg = "#3a1520", "#dd6688"
        else:
            bg, fg = "#551525", C["red"]

        draw.rounded_rectangle(
            [cx + pad, cy + pad, cx + cell_w - pad, cy + cell_h - pad],
            radius=8, fill=bg, outline=C["grid"],
        )

        # Symbol
        draw.text((cx + pad + 10, cy + pad + 8),
                  coin_d.get("symbol", "?"), font=fonts["body"], fill=fg)

        # Change
        draw.text((cx + pad + 10, cy + cell_h - pad - 22),
                  f"{change:+.1f}%", font=fonts["label"], fill=fg)

    # Footer
    draw.text((int(w * 0.08), int(h * 0.95)),
              "CRYPTO ALPHA SCANNER", font=fonts["tiny"], fill=C["text_dim"])

    return img


# ─────────────────────────────────────────────────────────────────────────────
# TEXT WRAPPING
# ─────────────────────────────────────────────────────────────────────────────

def _draw_wrapped_text(draw, text, x, y, font, color, max_width, max_lines=4, line_height=30):
    """Draw word-wrapped text."""
    words = text.split()
    lines = []
    current = ""

    for word in words:
        test = f"{current} {word}".strip()
        bbox = font.getbbox(test)
        tw = bbox[2] - bbox[0]
        if tw > max_width and current:
            lines.append(current)
            current = word
        else:
            current = test
    if current:
        lines.append(current)

    for line in lines[:max_lines]:
        draw.text((x, y), line, font=font, fill=color)
        y += line_height
