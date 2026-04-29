"""
================================================================================
SIGNAL LAYER  v3.0
================================================================================
Pure-function signal library. Every signal that any v3 scanner can fire is
defined here, ONCE, with one canonical implementation.

Scanners do not implement signals. They:
  1. Fetch data (data.py)
  2. Call signals.py functions
  3. Combine signal outputs into conviction scores

Conventions:
  - Every signal is `def sig_<name>(df, **params) -> SignalResult`
  - SignalResult bundles bool fired + numeric strength + diagnostic value
  - Signals are pure: no caching, no IO, no side effects
  - Signals never raise — return SignalResult.empty() on insufficient data

The 6 contested signals (per spec) use the canonical definitions agreed upon:
  bb_squeeze       : TTM (BB inside KC AND bottom-20% width over 120 bars)
  rsi_divergence   : pivot-based, RSI(7), bullish + hidden bullish
  obv_*            : split into 3 distinct signals (slope/stealth/divergence)
  vol_*            : split into 2 distinct signals (expansion/in_window)
  whale_candle     : ATR-normalized, lookback param, close-in-upper-30%
  rsi_*            : split into 2 (rsi_reset, rsi_in_zone)

Defaults assume 1h bars unless otherwise noted. Pass `bars_per_day` if you
want a signal to scale to a different timeframe.
================================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from indicators import (
    compute_rsi, compute_atr, compute_macd, compute_adx,
    compute_obv, compute_cmf, compute_bb, compute_keltner,
    compute_supertrend, compute_ema, compute_slope, find_pivots,
)


# ─────────────────────────────────────────────────────────────────────────────
# RESULT TYPE
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SignalResult:
    """
    Standard result for every signal.

    fired    : bool — does the signal fire right now?
    strength : float in [0, 1] — how strongly (0 = barely, 1 = textbook).
               Use this for weighting, not just the boolean.
    value    : Optional[float] — the underlying numeric measure
               (e.g. RSI value, BB width %, OI change %). For diagnostics.
    extras   : dict — any extra data the scanner may want for reporting.
    """
    fired:    bool                = False
    strength: float               = 0.0
    value:    Optional[float]     = None
    extras:   dict                = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "SignalResult":
        return cls(fired=False, strength=0.0, value=None)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _safe_float(x) -> float:
    """Convert anything to float, returning nan on failure."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def _pct_change(a: float, b: float) -> float:
    """(a - b) / b safely. Returns nan if b is 0."""
    return (a - b) / b if b != 0 else float("nan")


def _enough_bars(df: pd.DataFrame, n: int) -> bool:
    return df is not None and len(df) >= n


# ═════════════════════════════════════════════════════════════════════════════
# CONTESTED SIGNAL #1 — BB SQUEEZE (TTM canonical)
# ═════════════════════════════════════════════════════════════════════════════

def sig_bb_squeeze(
    df:          pd.DataFrame,
    bb_period:   int   = 20,
    bb_std:      float = 2.0,
    kc_period:   int   = 20,
    kc_atr:      int   = 10,
    kc_mult:     float = 1.5,
    width_lookback: int = 120,
    width_pct:    float = 20.0,   # bottom 20th percentile (TTM with KC)
    deep_pct:     float = 5.0,    # bottom 5th percentile (sufficient alone)
) -> SignalResult:
    """
    Compression signal — fires under either of two conditions:

      (A) TTM Squeeze:  BB bands inside Keltner bands AND BB width
                        in the bottom `width_pct` of recent history.
      (B) Deep compression alone: BB width in the bottom `deep_pct` of
                                  recent history, regardless of KC.

    Why both: pure TTM is mathematically clean but too strict on volatile
    crypto 1h data — coins genuinely coiled at the 1st-5th percentile of
    their recent BB-width history are filtered out because BB happens to
    poke just above/below KC. Adding the deep-compression alternative
    catches these. The two conditions are NOT additive (no double-count)
    — fired = A OR B.

    fired    : True if either condition holds.
    strength : 0..1, scaled by how deep into the compression we are.
    value    : current BB width as fraction of mid.
    extras   : trigger='ttm' or 'deep' shows which condition fired.
    """
    if not _enough_bars(df, max(bb_period, kc_period, width_lookback) + 5):
        return SignalResult.empty()

    closes = df["close"]
    bb_u, bb_m, bb_l = compute_bb(closes, bb_period, bb_std)
    kc_u, _, kc_l    = compute_keltner(df, kc_period, kc_atr, kc_mult)

    if bb_m.iloc[-1] == 0 or pd.isna(bb_m.iloc[-1]):
        return SignalResult.empty()

    bb_width = (bb_u - bb_l) / bb_m.replace(0, np.nan)
    cur_w    = _safe_float(bb_width.iloc[-1])
    if np.isnan(cur_w):
        return SignalResult.empty()

    # BB inside KC?
    inside_kc = (
        bb_u.iloc[-1] <= kc_u.iloc[-1]
        and bb_l.iloc[-1] >= kc_l.iloc[-1]
    )

    # Width thresholds
    recent_widths = bb_width.dropna().iloc[-width_lookback:]
    if len(recent_widths) < 20:
        return SignalResult.empty()
    threshold_ttm  = float(np.percentile(recent_widths.values, width_pct))
    threshold_deep = float(np.percentile(recent_widths.values, deep_pct))

    # Two trigger paths
    ttm_fire  = inside_kc and (cur_w <= threshold_ttm)
    deep_fire = cur_w <= threshold_deep
    fired     = bool(ttm_fire or deep_fire)

    # How deep is the current width vs all of the lookback window?
    rank_pct = float(
        (recent_widths < cur_w).sum() / len(recent_widths) * 100
    )

    # Strength: linear in [0..width_pct] for TTM, more aggressive for deep
    if fired:
        if deep_fire and not ttm_fire:
            # Pure deep compression — strength scales with how deep
            strength = max(0.5, (deep_pct - rank_pct) / deep_pct)
            trigger  = "deep"
        elif ttm_fire and deep_fire:
            # Both — full strength
            strength = max(0.7, (width_pct - rank_pct) / width_pct)
            trigger  = "ttm+deep"
        else:
            # TTM only
            strength = max(0.0, (width_pct - rank_pct) / width_pct)
            trigger  = "ttm"
    else:
        strength = 0.0
        trigger  = None

    return SignalResult(
        fired    = fired,
        strength = float(min(strength, 1.0)),
        value    = cur_w,
        extras   = {
            "bb_width_pct":         round(cur_w * 100, 3),
            "ttm_threshold_pct":    round(threshold_ttm  * 100, 3),
            "deep_threshold_pct":   round(threshold_deep * 100, 3),
            "rank_pct":             round(rank_pct, 1),
            "inside_kc":            inside_kc,
            "trigger":              trigger,
        },
    )


# ═════════════════════════════════════════════════════════════════════════════
# CONTESTED SIGNAL #2 — RSI DIVERGENCE (pivot-based, RSI(7))
# ═════════════════════════════════════════════════════════════════════════════

def sig_rsi_divergence(
    df:        pd.DataFrame,
    rsi_period: int = 7,
    pivot_left:  int = 3,
    pivot_right: int = 3,
    lookback:    int = 60,
    min_price_drop: float = 0.005,   # second low must be 0.5% below first
    min_rsi_gap:    float = 3.0,     # RSI at 2nd low must be 3pts higher
) -> SignalResult:
    """
    Bullish RSI divergence detected via swing-low pivots.
    Fires when: most recent two confirmed pivot LOWS in price both have
    corresponding RSI pivot lows where price2 < price1 (lower low) but
    rsi2 > rsi1 + min_rsi_gap (higher low). Canonical bullish divergence.

    Also detects HIDDEN bullish (price higher low + RSI lower low) — flagged
    in extras as 'kind'='hidden_bullish' (rare but legitimate signal).

    Uses RSI(7) per the agreed spec.
    """
    min_required = max(rsi_period, lookback) + pivot_right + 5
    if not _enough_bars(df, min_required):
        return SignalResult.empty()

    closes = df["close"]
    rsi    = compute_rsi(closes, rsi_period)

    # Limit search window to the recent `lookback` bars
    window_closes = closes.iloc[-lookback:]
    window_rsi    = rsi.iloc[-lookback:]

    # Find swing-low pivots in price within the window
    _, price_low_idx = find_pivots(window_closes, pivot_left, pivot_right)
    if len(price_low_idx) < 2:
        return SignalResult.empty()

    # Take the two most recent confirmed pivot lows
    i1, i2 = price_low_idx[-2], price_low_idx[-1]
    p1 = float(window_closes.iloc[i1])
    p2 = float(window_closes.iloc[i2])
    r1 = _safe_float(window_rsi.iloc[i1])
    r2 = _safe_float(window_rsi.iloc[i2])

    if any(np.isnan([p1, p2, r1, r2])) or p1 == 0:
        return SignalResult.empty()

    price_change_pct = (p2 - p1) / p1
    rsi_gap          = r2 - r1

    # Classic bullish divergence: lower low in price, higher low in RSI
    is_bullish = (price_change_pct <= -min_price_drop) and (rsi_gap >= min_rsi_gap)
    # Hidden bullish: higher low in price, lower low in RSI (continuation)
    is_hidden  = (price_change_pct >= +min_price_drop) and (rsi_gap <= -min_rsi_gap)

    fired = bool(is_bullish or is_hidden)
    if not fired:
        return SignalResult(
            fired = False, strength = 0.0, value = rsi_gap,
            extras = {"kind": None, "price_change_pct": round(price_change_pct * 100, 2)},
        )

    # Strength: how big is the gap in standard divergence units?
    # Normalize: rsi_gap of 5pts + price drop of 2% ≈ strength 0.5
    if is_bullish:
        gap_score   = min(rsi_gap / 10.0, 1.0)         # 10pt RSI gap caps at 1.0
        price_score = min(abs(price_change_pct) / 0.05, 1.0)  # 5% drop caps at 1.0
        strength    = (gap_score + price_score) / 2.0
        kind        = "bullish"
    else:
        gap_score   = min(abs(rsi_gap) / 10.0, 1.0)
        price_score = min(price_change_pct / 0.05, 1.0)
        strength    = (gap_score + price_score) / 2.0
        kind        = "hidden_bullish"

    return SignalResult(
        fired    = True,
        strength = float(strength),
        value    = rsi_gap,
        extras   = {
            "kind":             kind,
            "price_change_pct": round(price_change_pct * 100, 2),
            "rsi_gap":          round(rsi_gap, 2),
            "rsi1":             round(r1, 1),
            "rsi2":             round(r2, 1),
        },
    )


# ═════════════════════════════════════════════════════════════════════════════
# CONTESTED SIGNAL #3 — OBV (split into 3 distinct signals)
# ═════════════════════════════════════════════════════════════════════════════

def sig_obv_slope(
    df:    pd.DataFrame,
    bars:  int = 12,
    min_slope_norm: float = 0.0,
) -> SignalResult:
    """
    OBV trend: linear regression slope over `bars` bars is positive.
    Normalizes slope by mean OBV magnitude so the threshold is comparable
    across coins.

    fired    : slope/|mean OBV| > min_slope_norm AND OBV[-1] > OBV[-bars].
    strength : magnitude of normalized slope, capped at 1.0.
    """
    if not _enough_bars(df, bars + 5) or "volume" not in df.columns:
        return SignalResult.empty()

    obv = compute_obv(df)
    slope = compute_slope(obv, bars)
    if np.isnan(slope):
        return SignalResult.empty()

    obv_window = obv.iloc[-bars:].dropna()
    if len(obv_window) < bars:
        return SignalResult.empty()

    # Normalize slope by typical OBV magnitude in the window
    mean_abs = float(obv_window.abs().mean())
    if mean_abs == 0:
        return SignalResult.empty()

    norm_slope = slope / mean_abs
    direction_ok = float(obv.iloc[-1]) > float(obv.iloc[-bars])
    fired = norm_slope > min_slope_norm and direction_ok

    return SignalResult(
        fired    = bool(fired),
        strength = float(min(abs(norm_slope) * 10, 1.0)) if fired else 0.0,
        value    = float(norm_slope),
        extras   = {"slope": float(slope), "obv_now": float(obv.iloc[-1])},
    )


def sig_obv_stealth_accum(
    df:           pd.DataFrame,
    obv_lookback: int   = 12,
    min_obv_pct:  float = 0.015,   # OBV must rise ≥ 1.5%
    max_price_move: float = 0.03,  # while price stays within ±3%
) -> SignalResult:
    """
    Stealth accumulation: OBV rises significantly while price is flat.
    Indicates buying pressure not yet reflected in price.
    """
    if not _enough_bars(df, obv_lookback + 5) or "volume" not in df.columns:
        return SignalResult.empty()

    obv    = compute_obv(df)
    closes = df["close"]

    p_now    = float(closes.iloc[-1])
    p_prior  = float(closes.iloc[-obv_lookback])
    o_now    = float(obv.iloc[-1])
    o_prior  = float(obv.iloc[-obv_lookback])

    if any(np.isnan([p_now, p_prior, o_now, o_prior])) or o_prior == 0 or p_prior == 0:
        return SignalResult.empty()

    obv_pct   = (o_now - o_prior) / abs(o_prior)
    price_pct = (p_now - p_prior) / p_prior

    is_flat       = abs(price_pct) <= max_price_move
    obv_advancing = obv_pct >= min_obv_pct
    fired         = bool(is_flat and obv_advancing)

    strength = 0.0
    if fired:
        # Stronger when OBV is rising more AND price is more pinned
        obv_score   = min(obv_pct / (min_obv_pct * 4), 1.0)
        flat_score  = 1.0 - min(abs(price_pct) / max_price_move, 1.0)
        strength    = (obv_score + flat_score) / 2.0

    return SignalResult(
        fired    = fired,
        strength = float(strength),
        value    = float(obv_pct),
        extras   = {
            "obv_pct":   round(obv_pct * 100, 2),
            "price_pct": round(price_pct * 100, 2),
        },
    )


def sig_obv_divergence(
    df:           pd.DataFrame,
    pivot_left:   int = 3,
    pivot_right:  int = 3,
    lookback:     int = 40,
) -> SignalResult:
    """
    Bullish OBV divergence: price makes lower low, OBV makes higher low.
    Pivot-based for proper swing-low detection.
    """
    if not _enough_bars(df, lookback + pivot_right + 5) or "volume" not in df.columns:
        return SignalResult.empty()

    obv    = compute_obv(df)
    closes = df["close"]

    win_close = closes.iloc[-lookback:]
    win_obv   = obv.iloc[-lookback:]

    _, price_lows = find_pivots(win_close, pivot_left, pivot_right)
    _, obv_lows   = find_pivots(win_obv,   pivot_left, pivot_right)
    if len(price_lows) < 2 or len(obv_lows) < 2:
        return SignalResult.empty()

    p1, p2 = float(win_close.iloc[price_lows[-2]]), float(win_close.iloc[price_lows[-1]])
    o1, o2 = float(win_obv.iloc[obv_lows[-2]]),     float(win_obv.iloc[obv_lows[-1]])

    if p1 == 0:
        return SignalResult.empty()

    price_lower = p2 < p1 * 0.995          # ≥ 0.5% lower low
    obv_higher  = o2 > o1                   # any higher low
    fired       = bool(price_lower and obv_higher)

    strength = 0.0
    if fired:
        price_drop = (p1 - p2) / p1
        obv_gap    = (o2 - o1) / max(abs(o1), 1.0)
        strength   = min((price_drop * 10 + obv_gap) / 2.0, 1.0)
        strength   = max(strength, 0.0)

    return SignalResult(
        fired    = fired,
        strength = float(strength),
        value    = float((o2 - o1) / max(abs(o1), 1.0)) if o1 != 0 else 0.0,
        extras   = {
            "price_drop_pct": round((p1 - p2) / p1 * 100, 2) if p1 else 0.0,
        },
    )


# ═════════════════════════════════════════════════════════════════════════════
# CONTESTED SIGNAL #4 — VOLUME (split into 2 distinct signals)
# ═════════════════════════════════════════════════════════════════════════════

def sig_vol_expansion(
    df:        pd.DataFrame,
    recent:    int   = 6,
    base_start: int  = 7,
    base_end:   int  = 42,
    mult:       float = 1.5,
) -> SignalResult:
    """
    Volume expansion: recent N bars vs baseline window has strong elevation.
    "Fresh capital arriving" — fires when ratio >= mult.

    Default windows are 1h-bar centric: 6 recent (~6h) vs 7-42 bars back (~36h baseline).
    """
    if not _enough_bars(df, base_end + recent) or "volume" not in df.columns:
        return SignalResult.empty()

    vols = df["volume"].dropna()
    if len(vols) < base_end + recent:
        return SignalResult.empty()

    recent_avg = float(vols.iloc[-recent:].mean())
    base_slice = vols.iloc[-base_end:-base_start] if base_start > 0 else vols.iloc[-base_end:]
    base_avg   = float(base_slice.mean())
    if base_avg <= 0:
        return SignalResult.empty()

    ratio = recent_avg / base_avg
    fired = ratio >= mult
    return SignalResult(
        fired    = bool(fired),
        # Strength: 1.5x = 0.0, 4.5x = 1.0
        strength = float(min(max((ratio - mult) / (3.0), 0.0), 1.0)) if fired else 0.0,
        value    = float(ratio),
        extras   = {"recent_avg": recent_avg, "base_avg": base_avg},
    )


def sig_vol_in_window(
    df:        pd.DataFrame,
    recent:    int   = 6,
    base_back: int   = 30,
    low_mult:  float = 1.2,
    high_mult: float = 4.5,
) -> SignalResult:
    """
    Volume "building but not yet pumping": recent vol/baseline in [low_mult, high_mult].
    Distinct from vol_expansion — this one EXCLUDES already-pumping coins.
    Useful for prepump-style detection where you want to catch it before it's loud.
    """
    if not _enough_bars(df, base_back + recent) or "volume" not in df.columns:
        return SignalResult.empty()

    vols = df["volume"].dropna()
    if len(vols) < base_back + recent:
        return SignalResult.empty()

    recent_avg = float(vols.iloc[-recent:].mean())
    base_avg   = float(vols.iloc[-(base_back + recent):-recent].mean())
    if base_avg <= 0:
        return SignalResult.empty()

    ratio = recent_avg / base_avg
    fired = low_mult <= ratio <= high_mult

    # Strength peaks in middle of window
    strength = 0.0
    if fired:
        mid = (low_mult + high_mult) / 2.0
        # 1.0 at exactly mid, falling linearly to 0 at edges
        dist = abs(ratio - mid) / ((high_mult - low_mult) / 2.0)
        strength = max(0.0, 1.0 - dist)

    return SignalResult(
        fired    = bool(fired),
        strength = float(strength),
        value    = float(ratio),
    )


# ═════════════════════════════════════════════════════════════════════════════
# CONTESTED SIGNAL #5 — WHALE CANDLE (ATR-normalized, lookback param)
# ═════════════════════════════════════════════════════════════════════════════

def sig_whale_candle(
    df:        pd.DataFrame,
    lookback:  int   = 6,
    atr_mult:  float = 1.8,
    atr_period: int  = 14,
    close_upper_pct: float = 0.30,
) -> SignalResult:
    """
    Bullish whale candle in the last `lookback` bars.
    Definition: body >= ATR(14) * atr_mult, AND close in upper N% of range,
                AND bullish (close > open).

    Single canonical version — replaces 3 different definitions across scanners.
    """
    if not _enough_bars(df, atr_period + lookback + 2):
        return SignalResult.empty()

    atr = compute_atr(df, atr_period)
    atr_now = _safe_float(atr.iloc[-1])
    if np.isnan(atr_now) or atr_now <= 0:
        return SignalResult.empty()

    recent = df.iloc[-lookback:]
    best_strength = 0.0
    fired = False
    bars_ago = -1

    for i, (_, bar) in enumerate(recent.iloc[::-1].iterrows()):  # newest first
        o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
        body = c - o
        rng  = h - l
        if rng <= 0 or body <= 0:
            continue
        # Close in upper portion of range?
        close_pos = (c - l) / rng    # 0 = at low, 1 = at high
        if close_pos < (1.0 - close_upper_pct):
            continue
        body_atr_mult = body / atr_now
        if body_atr_mult < atr_mult:
            continue
        # Strength: bigger body relative to ATR mult, more recent
        strength = min((body_atr_mult / atr_mult - 1.0) + 0.5, 1.0)
        recency_factor = 1.0 - (i / (lookback * 1.5))   # newer = stronger
        strength *= recency_factor
        if strength > best_strength:
            best_strength = strength
            bars_ago = i
            fired = True

    return SignalResult(
        fired    = fired,
        strength = float(best_strength),
        value    = atr_now,
        extras   = {"bars_ago": bars_ago} if fired else {},
    )


# ═════════════════════════════════════════════════════════════════════════════
# CONTESTED SIGNAL #6 — RSI (split into reset and in_zone)
# ═════════════════════════════════════════════════════════════════════════════

def sig_rsi_reset(
    df:           pd.DataFrame,
    rsi_period:   int   = 7,
    low_thresh:   float = 42.0,
    lookback:     int   = 24,
) -> SignalResult:
    """
    RSI reset: RSI was <= low_thresh within the last `lookback` bars,
    AND RSI is currently rising (last bar > previous bar).
    Catches the bottoming/reversal process.

    Uses RSI(7) per spec.
    """
    if not _enough_bars(df, max(rsi_period, lookback) + 5):
        return SignalResult.empty()

    rsi = compute_rsi(df["close"], rsi_period).dropna()
    if len(rsi) < lookback + 2:
        return SignalResult.empty()

    window = rsi.iloc[-lookback:]
    cur    = float(rsi.iloc[-1])
    prev   = float(rsi.iloc[-2])
    had_low  = bool((window <= low_thresh).any())
    rising   = cur > prev
    fired    = had_low and rising

    strength = 0.0
    if fired:
        # Stronger when current RSI is further off the bottom and rising harder
        depth   = (low_thresh - float(window.min())) / low_thresh   # how oversold did it get
        bounce  = (cur - float(window.min())) / max(low_thresh, 1)  # how much off the bottom
        rise    = (cur - prev) / 100.0                              # current bar rise
        strength = float(min(max(depth + bounce + rise * 5, 0.0), 1.0))

    return SignalResult(
        fired    = fired,
        strength = strength,
        value    = cur,
        extras   = {
            "min_in_window": round(float(window.min()), 1),
            "rsi_now":       round(cur, 1),
        },
    )


def sig_rsi_in_zone(
    df:         pd.DataFrame,
    rsi_period: int   = 7,
    low:        float = 25.0,
    high:       float = 62.0,
) -> SignalResult:
    """
    RSI currently in the healthy zone [low, high].
    Acts as a filter — keeps both oversold extremes AND overbought froth out.
    Pure boolean; strength = how well-centered in the zone (peaks at midpoint).
    """
    if not _enough_bars(df, rsi_period + 5):
        return SignalResult.empty()
    rsi_val = _safe_float(compute_rsi(df["close"], rsi_period).iloc[-1])
    if np.isnan(rsi_val):
        return SignalResult.empty()
    fired = low <= rsi_val <= high

    strength = 0.0
    if fired:
        mid    = (low + high) / 2.0
        half   = (high - low) / 2.0
        strength = float(max(0.0, 1.0 - abs(rsi_val - mid) / half))

    return SignalResult(
        fired    = bool(fired),
        strength = strength,
        value    = rsi_val,
    )


# ═════════════════════════════════════════════════════════════════════════════
# UNCONTESTED TREND SIGNALS — single source of truth, used by trend_scanner
# ═════════════════════════════════════════════════════════════════════════════

def sig_macd_crossover(df: pd.DataFrame) -> SignalResult:
    """MACD histogram just crossed above zero this bar."""
    if not _enough_bars(df, 35):
        return SignalResult.empty()
    _, _, hist = compute_macd(df["close"])
    h = hist.dropna()
    if len(h) < 4:
        return SignalResult.empty()
    h0, h1 = float(h.iloc[-1]), float(h.iloc[-2])
    fired  = h0 > 0 and h1 <= 0
    return SignalResult(
        fired    = bool(fired),
        strength = float(min(abs(h0 - h1) * 100, 1.0)) if fired else 0.0,
        value    = h0,
    )


def sig_macd_turning(df: pd.DataFrame) -> SignalResult:
    """
    Histogram negative but rising for 3 consecutive bars.
    Earlier than crossover but still filtered.
    """
    if not _enough_bars(df, 35):
        return SignalResult.empty()
    _, _, hist = compute_macd(df["close"])
    h = hist.dropna()
    if len(h) < 4:
        return SignalResult.empty()
    h0, h1, h2, h3 = (float(h.iloc[-1]), float(h.iloc[-2]),
                      float(h.iloc[-3]), float(h.iloc[-4]))
    fired = h0 < 0 and h3 < h2 < h1 < h0
    return SignalResult(
        fired    = bool(fired),
        strength = 0.6 if fired else 0.0,
        value    = h0,
    )


def sig_adx_trend_strong(
    df:    pd.DataFrame,
    period: int   = 14,
    min_adx: float = 25.0,
) -> SignalResult:
    """ADX > threshold and +DI > -DI — bullish trend strength."""
    if not _enough_bars(df, period + 10):
        return SignalResult.empty()
    adx, pdi, ndi = compute_adx(df, period)
    a  = _safe_float(adx.iloc[-1])
    p  = _safe_float(pdi.iloc[-1])
    n  = _safe_float(ndi.iloc[-1])
    if any(np.isnan([a, p, n])):
        return SignalResult.empty()
    fired = a >= min_adx and p > n
    return SignalResult(
        fired    = bool(fired),
        strength = float(min(max(a - min_adx, 0) / 25.0, 1.0)) if fired else 0.0,
        value    = a,
        extras   = {"plus_di": round(p, 1), "minus_di": round(n, 1)},
    )


def sig_atr_expanding(df: pd.DataFrame, atr_period: int = 14, slope_window: int = 5) -> SignalResult:
    """ATR slope is positive — volatility expanding (energy building)."""
    if not _enough_bars(df, atr_period + slope_window + 5):
        return SignalResult.empty()
    closes = df["close"]
    atr = compute_atr(df, atr_period)
    atr_norm = atr / closes.replace(0, np.nan)
    slope = compute_slope(atr_norm, slope_window)
    if np.isnan(slope):
        return SignalResult.empty()
    cur_pct = float(atr_norm.iloc[-1]) if not pd.isna(atr_norm.iloc[-1]) else 0.0
    fired   = slope > 0 and cur_pct > 0.025
    return SignalResult(
        fired    = bool(fired),
        strength = float(min(slope * 1000, 1.0)) if fired else 0.0,
        value    = cur_pct,
    )


def sig_cmf_positive(
    df:        pd.DataFrame,
    period:    int   = 20,
    threshold: float = 0.05,
) -> SignalResult:
    """Chaikin Money Flow > threshold — institutional buying pressure."""
    if not _enough_bars(df, period + 5) or "volume" not in df.columns:
        return SignalResult.empty()
    cmf = _safe_float(compute_cmf(df, period).iloc[-1])
    if np.isnan(cmf):
        return SignalResult.empty()
    fired = cmf >= threshold
    return SignalResult(
        fired    = bool(fired),
        strength = float(min(max((cmf - threshold) / 0.15, 0.0), 1.0)) if fired else 0.0,
        value    = cmf,
    )


def sig_higher_lows(
    df:        pd.DataFrame,
    window:    int   = 30,
    pivot_left: int  = 3,
    pivot_right: int = 3,
) -> SignalResult:
    """
    Two consecutive higher swing lows — base-building / accumulation structure.
    """
    if not _enough_bars(df, window + pivot_right + 5):
        return SignalResult.empty()
    closes = df["close"].iloc[-window:]
    _, lows_idx = find_pivots(closes, pivot_left, pivot_right)
    if len(lows_idx) < 2:
        return SignalResult.empty()
    l1 = float(closes.iloc[lows_idx[-2]])
    l2 = float(closes.iloc[lows_idx[-1]])
    fired = l2 > l1 * 1.001
    return SignalResult(
        fired    = bool(fired),
        strength = float(min((l2 - l1) / l1 * 20, 1.0)) if fired else 0.0,
        value    = (l2 - l1) / l1 if l1 else 0.0,
    )


def sig_supertrend_bullish(
    df:        pd.DataFrame,
    period:    int   = 10,
    multiplier: float = 3.0,
) -> SignalResult:
    """SuperTrend currently bullish (price above ST line)."""
    if not _enough_bars(df, period + 10):
        return SignalResult.empty()
    st = compute_supertrend(df, period, multiplier)
    fired = bool(st.iloc[-1])
    # Strength: how many of last 5 bars were also bullish?
    last5 = st.iloc[-5:].sum() if len(st) >= 5 else int(fired)
    strength = float(last5) / 5.0 if fired else 0.0
    return SignalResult(fired=fired, strength=strength, value=1.0 if fired else 0.0)


def sig_price_range_break(
    df:       pd.DataFrame,
    lookback: int = 120,
) -> SignalResult:
    """Current close is the highest close in the lookback window."""
    if not _enough_bars(df, 20):
        return SignalResult.empty()
    closes = df["close"]
    n = min(lookback, len(closes))
    window_max = float(closes.iloc[-n:].max())
    cur = float(closes.iloc[-1])
    fired = cur >= window_max * 0.999
    if not fired:
        return SignalResult(fired=False, strength=0.0, value=cur / window_max if window_max else 0)
    # Strength = how clean the breakout is (prior bar much lower = clean break)
    if len(closes) >= 5:
        prior_max = float(closes.iloc[-n:-1].max())
        margin = (cur - prior_max) / prior_max if prior_max else 0
        strength = float(min(max(margin * 50, 0.2), 1.0))
    else:
        strength = 0.5
    return SignalResult(fired=True, strength=strength, value=cur)


# ═════════════════════════════════════════════════════════════════════════════
# RELATIVE STRENGTH vs BTC
# ═════════════════════════════════════════════════════════════════════════════

def sig_rs_vs_btc(
    token_closes: pd.Series,
    btc_closes:   pd.Series,
    window:       int   = 42,    # default 42 bars on 4h = 7 days
    min_outperformance: float = 0.03,
) -> SignalResult:
    """
    Token's return over `window` bars exceeds BTC's by min_outperformance.
    """
    if (token_closes is None or btc_closes is None
            or len(token_closes) < window + 1 or len(btc_closes) < window + 1):
        return SignalResult.empty()
    t = token_closes.iloc[-(window + 1):].dropna()
    b = btc_closes.iloc[-(window + 1):].dropna()
    if len(t) < 2 or len(b) < 2:
        return SignalResult.empty()
    tok_ret = (float(t.iloc[-1]) - float(t.iloc[0])) / float(t.iloc[0])
    btc_ret = (float(b.iloc[-1]) - float(b.iloc[0])) / float(b.iloc[0])
    rs      = tok_ret - btc_ret
    fired   = rs >= min_outperformance
    return SignalResult(
        fired    = bool(fired),
        strength = float(min(max((rs - min_outperformance) / 0.15, 0.0), 1.0)) if fired else 0.0,
        value    = rs,
        extras   = {"token_ret_pct": round(tok_ret * 100, 2),
                    "btc_ret_pct":   round(btc_ret * 100, 2)},
    )


def sig_rs_acceleration(
    token_closes: pd.Series,
    btc_closes:   pd.Series,
    short_window: int   = 7,
    long_window:  int   = 42,
    min_outperformance: float = 0.03,
) -> SignalResult:
    """
    Short-window RS exceeds long-window RS — momentum is accelerating.
    Both windows must be outperforming individually.
    """
    short_rs = sig_rs_vs_btc(token_closes, btc_closes, short_window, min_outperformance)
    long_rs  = sig_rs_vs_btc(token_closes, btc_closes, long_window,  min_outperformance)
    if short_rs.value is None or long_rs.value is None:
        return SignalResult.empty()
    fired = (short_rs.fired and long_rs.fired
             and short_rs.value > long_rs.value)
    return SignalResult(
        fired    = bool(fired),
        strength = min(short_rs.strength, long_rs.strength) if fired else 0.0,
        value    = short_rs.value - long_rs.value,
        extras   = {"short_rs": short_rs.value, "long_rs": long_rs.value},
    )


def sig_btc_decoupling(
    token_closes: pd.Series,
    btc_closes:   pd.Series,
    window:       int   = 6,
    min_decoupling: float = 0.015,    # 1.5% outperformance
) -> SignalResult:
    """
    Short-window decoupling: token outperforms BTC by min_decoupling
    over `window` bars. Catches token-specific catalysts.
    """
    res = sig_rs_vs_btc(token_closes, btc_closes, window, min_decoupling)
    return res


# ═════════════════════════════════════════════════════════════════════════════
# PERP-SPECIFIC SIGNALS  — for perp_scanner (Bybit positioning data)
# ═════════════════════════════════════════════════════════════════════════════

def sig_oi_building(
    oi_now:      float,
    oi_prev:     float | None,
    min_pct:     float = 0.03,
) -> SignalResult:
    """OI grew vs prior snapshot by ≥ min_pct."""
    if oi_prev is None or oi_prev <= 0:
        return SignalResult.empty()
    pct = (oi_now - oi_prev) / oi_prev
    fired = pct >= min_pct
    return SignalResult(
        fired    = bool(fired),
        strength = float(min(max((pct - min_pct) / 0.10, 0.0), 1.0)) if fired else 0.0,
        value    = pct,
    )


def sig_oi_unwind(
    oi_now:      float,
    oi_prev:     float | None,
    max_pct:     float = -0.05,
) -> SignalResult:
    """OI fell vs prior snapshot by ≥ |max_pct| — conviction leaving."""
    if oi_prev is None or oi_prev <= 0:
        return SignalResult.empty()
    pct = (oi_now - oi_prev) / oi_prev
    fired = pct <= max_pct
    return SignalResult(
        fired    = bool(fired),
        strength = float(min(abs(pct - max_pct) / 0.10, 1.0)) if fired else 0.0,
        value    = pct,
    )


def sig_funding_negative(
    funding_rate: float,
    threshold:    float = -0.0001,
) -> SignalResult:
    """Funding rate <= threshold — shorts paying longs (squeeze fuel)."""
    if funding_rate is None:
        return SignalResult.empty()
    fired = funding_rate <= threshold
    return SignalResult(
        fired    = bool(fired),
        strength = float(min(abs(funding_rate) / 0.0005, 1.0)) if fired else 0.0,
        value    = funding_rate,
    )


def sig_funding_extreme_long(
    funding_rate: float,
    threshold:    float = 0.0008,
) -> SignalResult:
    """
    Funding above extreme long threshold — DANGER signal (fade).
    fired=True is BAD here — caller should treat as a penalty.
    """
    if funding_rate is None:
        return SignalResult.empty()
    fired = funding_rate >= threshold
    return SignalResult(
        fired    = bool(fired),
        strength = float(min((funding_rate - threshold) / 0.0010, 1.0)) if fired else 0.0,
        value    = funding_rate,
    )


def sig_vol_oi_surge(
    turnover_24h: float,
    oi_value:     float,
    threshold:    float = 2.0,
) -> SignalResult:
    """24h turnover relative to OI value — high ratio = volume surge."""
    if oi_value is None or oi_value <= 0:
        return SignalResult.empty()
    ratio = turnover_24h / oi_value
    fired = ratio >= threshold
    return SignalResult(
        fired    = bool(fired),
        strength = float(min(max((ratio - threshold) / 5.0, 0.0), 1.0)) if fired else 0.0,
        value    = ratio,
    )


# ═════════════════════════════════════════════════════════════════════════════
# SIGNAL REGISTRY  — for introspection / programmatic dispatch
# ═════════════════════════════════════════════════════════════════════════════

SIGNAL_REGISTRY = {
    # contested (canonical)
    "bb_squeeze":          sig_bb_squeeze,
    "rsi_divergence":      sig_rsi_divergence,
    "obv_slope":           sig_obv_slope,
    "obv_stealth_accum":   sig_obv_stealth_accum,
    "obv_divergence":      sig_obv_divergence,
    "vol_expansion":       sig_vol_expansion,
    "vol_in_window":       sig_vol_in_window,
    "whale_candle":        sig_whale_candle,
    "rsi_reset":           sig_rsi_reset,
    "rsi_in_zone":         sig_rsi_in_zone,
    # uncontested trend
    "macd_crossover":      sig_macd_crossover,
    "macd_turning":        sig_macd_turning,
    "adx_trend_strong":    sig_adx_trend_strong,
    "atr_expanding":       sig_atr_expanding,
    "cmf_positive":        sig_cmf_positive,
    "higher_lows":         sig_higher_lows,
    "supertrend_bullish":  sig_supertrend_bullish,
    "price_range_break":   sig_price_range_break,
    # RS family (special: take btc series too)
    "rs_vs_btc":           sig_rs_vs_btc,
    "rs_acceleration":     sig_rs_acceleration,
    "btc_decoupling":      sig_btc_decoupling,
    # perp-specific (special: take scalars not df)
    "oi_building":         sig_oi_building,
    "oi_unwind":           sig_oi_unwind,
    "funding_negative":    sig_funding_negative,
    "funding_extreme_long": sig_funding_extreme_long,
    "vol_oi_surge":        sig_vol_oi_surge,
}
