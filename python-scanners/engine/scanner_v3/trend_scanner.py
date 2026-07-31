"""
================================================================================
TREND SCANNER  v3.0
================================================================================
Multi-timeframe trend confluence + trade plan generator.
Replaces spot_scanner.py + enhanced_scan.py with a single consolidated scanner.

What it answers: "Is this coin's trend healthy enough to trade RIGHT NOW?"
  - Score per timeframe (1H/2H/4H/6H/12H/1D) using ~17 indicators
  - Weighted aggregate (1D heaviest, 1H lightest)
  - Regime gating (BULL/SIDEWAYS/BEAR) sets thresholds + position sizing
  - Trade plans: ATR-based stop, 3 take-profits, position sizing

How it differs from the legacy scanners it replaces:
  - One universe (Bybit linear perps), one fetch path
  - Uses extended indicators.py — no more inline duplicate implementations
  - Multi-TF score retains enhanced_scan's structure
  - Regime + trade plans retain spot_scanner's logic
  - No 2-scan persistence (Phase 5 orchestrator's confluence is better filter)
  - Output format aligned with ignition_scanner + perp_scanner

NOTE: Regime detection uses BTC 7d as proxy. Phase 5 wires in the real TPI.

Run:
  python trend_scanner.py
  python trend_scanner.py --account 100000
  python trend_scanner.py --no-cache
================================================================================
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

import data
from indicators import (
    compute_rsi, compute_atr, compute_macd, compute_adx,
    compute_obv, compute_cmf, compute_bb, compute_keltner,
    compute_supertrend, compute_ema, compute_hull_ma,
    compute_aroon, compute_ichimoku, compute_stoch_rsi,
    compute_mfi, compute_cci, compute_psar, compute_slope,
    find_pivots,
)
import signals as S


# ─────────────────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────────────────
_THIS_DIR     = Path(__file__).resolve().parent             # scanner_v3/
_ENGINE_DIR   = _THIS_DIR.parent                             # engine/
_PYTHON_DIR   = _ENGINE_DIR.parent                           # python-scanners/
_PROJECT_ROOT = _PYTHON_DIR.parent                           # crypto-scanner/
_OUTPUT_DIR   = _PROJECT_ROOT / "outputs" / "scanner-results"
_LOG_DIR      = _PROJECT_ROOT / "outputs" / "logs"
for d in (_OUTPUT_DIR, _LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
log = logging.getLogger("scanner_v3.trend")
if not log.handlers:
    handler_file = logging.FileHandler(
        _LOG_DIR / f"trend_v3_{datetime.now().strftime('%Y%m%d')}.log",
        encoding="utf-8",
    )
    handler_file.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    handler_stdout = logging.StreamHandler(sys.stdout)
    handler_stdout.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(handler_file)
    log.addHandler(handler_stdout)
    log.setLevel(logging.INFO)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

# Timeframes and their weights — match enhanced_scan exactly
# (label, data.py timeframe key, weight)
TIMEFRAMES: list[tuple[str, str, float]] = [
    ("1D",  "1d",  0.28),
    ("12H", "12h", 0.20),
    ("6H",  "6h",  0.15),
    ("4H",  "4h",  0.15),
    ("2H",  "2h",  0.12),
    ("1H",  "1h",  0.10),
]

SCAN = {
    "min_turnover_24h": 1_000_000,   # $1M floor
    "ohlcv_bars":             200,    # per timeframe
    "max_coins":              500,    # cap
}

# Tier thresholds (in raw weighted-score units, ~0-200 typical range)
TIERS_BULL = {
    "strong_score": 130.0,
    "strong_st":      4,    # ST aligned on N+ TFs
    "long_score":   100.0,
    "long_st":        3,
    "watch_score":   70.0,
}

# In SIDEWAYS regime, raise the bar by 20 points
TIERS_SIDEWAYS = {
    "strong_score": 150.0,
    "strong_st":      5,
    "long_score":   120.0,
    "long_st":        4,
    "watch_score":   90.0,
}

# In BEAR, only WATCH tier surfaces
TIERS_BEAR = {
    "strong_score": 9999.0,    # blocked
    "strong_st":      6,        # blocked
    "long_score":   9999.0,    # blocked
    "long_st":        6,        # blocked
    "watch_score":   90.0,
}

REGIME = {
    "bull_btc_7d_pct":     3.0,
    "neutral_btc_7d_pct": -7.0,
    "btc_24h_danger":     -3.0,    # adds caution penalty if BTC dumping today
}

ACCOUNT = {
    "default_size_usdt":   100_000.0,
    "risk_pct_bull":            1.5,
    "risk_pct_sideways":        0.75,
    "max_notional":      20_000.0,
    "atr_stop_mult_bull":        1.5,
    "atr_stop_mult_sideways":    1.0,
    "stop_min_pct":             -15.0,
    "stop_max_pct":              -5.0,
    "tp_rr":             [1.5, 3.0, 5.0],
    "tp_split_pct":      [40,  35,  25],
}

# ─────────────────────────────────────────────────────────────────────────────
# MACRO SIZING OVERLAY (Layer 0 → position size)
# Reads the macro verdict written by macro_watch.py and shrinks position size
# when the dollar+yields are a headwind. MEDIUM integration: this NEVER blocks
# a setup — it only scales the risk/size of the eventual trade. The scanner
# keeps surfacing everything; macro just sizes the trade down in bad weather.
#
# Tune these multipliers freely. 1.0 = full size, lower = smaller.
# ─────────────────────────────────────────────────────────────────────────────
MACRO_SIZING = {
    "RISK-ON":    1.00,   # dollar DOWN + yields DOWN — tailwind, full size
    "MIXED-POS":  0.85,   # one easing — early thaw
    "NEUTRAL":    0.75,   # flat — no signal either way
    "MIXED-NEG":  0.60,   # one rising — still net headwind
    "RISK-OFF":   0.40,   # dollar UP + yields UP — headwind, shrink hard
    "?":          0.75,   # macro data unavailable — treat as neutral
}
# Fallback when the flag is missing or stale (>this many hours old).
# Neutral, never zero — the scanner must keep working without macro_watch.
MACRO_FALLBACK_MULT  = 0.75
MACRO_STALE_HOURS    = 24
_MACRO_FLAG_FILE     = _PROJECT_ROOT / "outputs" / "macro" / "latest.json"


def get_macro_multiplier() -> tuple[float, str]:
    """Read macro_watch's verdict flag and return (multiplier, reason_str).

    Safe by design: any problem (missing file, stale, unparseable, unknown
    verdict) falls back to MACRO_FALLBACK_MULT and NEVER raises. Macro can
    only shrink or hold size — it cannot block a setup or break the scan.
    """
    try:
        if not _MACRO_FLAG_FILE.exists():
            return MACRO_FALLBACK_MULT, "macro flag missing — neutral fallback"
        d = json.loads(_MACRO_FLAG_FILE.read_text(encoding="utf-8"))

        # Staleness check
        ts = d.get("checked_at_utc")
        if ts:
            try:
                # macro_watch writes a tz-aware UTC ISO string. Parse it and
                # drop tzinfo so we compare naive-to-naive against utcnow()
                # (mixing aware + naive raises TypeError).
                parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if parsed.tzinfo is not None:
                    parsed = parsed.replace(tzinfo=None)
                age_h = (datetime.utcnow() - parsed).total_seconds() / 3600
                if age_h > MACRO_STALE_HOURS:
                    return (MACRO_FALLBACK_MULT,
                            f"macro flag stale ({age_h:.0f}h old) — neutral fallback")
            except Exception:
                pass  # bad timestamp → ignore staleness, still use verdict below

        verdict = str(d.get("verdict", "?")).upper()
        mult    = MACRO_SIZING.get(verdict, MACRO_FALLBACK_MULT)
        dxy     = d.get("dxy")
        y10     = d.get("y10")
        ctx     = ""
        if dxy is not None and y10 is not None:
            ctx = f"  DXY {dxy:.2f} / 10Y {y10:.2f}%"
        return mult, f"macro {verdict} → {mult:.2f}x{ctx}"
    except Exception as e:
        return MACRO_FALLBACK_MULT, f"macro read error ({e}) — neutral fallback"


# ─────────────────────────────────────────────────────────────────────────────
# RESULT TYPES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TFScore:
    """Per-timeframe score breakdown."""
    label:     str
    score:     float = 0.0
    rsi:       Optional[float] = None
    adx:       Optional[float] = None
    plus_di:   Optional[float] = None
    minus_di:  Optional[float] = None
    st_bull:   bool  = False
    cmf:       Optional[float] = None
    mfi:       Optional[float] = None
    bb_pct:    Optional[float] = None     # price position in BB (0-1)
    cloud:     Optional[str]   = None     # 'bull'|'bear'|None


@dataclass
class TradePlan:
    """ATR-based trade plan."""
    entry:        float
    stop:         float
    stop_pct:     float
    risk_usdt:    float
    risk_pct:     float
    pos_value:    float
    pos_pct:      float
    quantity:     float
    take_profits: list[dict]    # [{price, gain_pct, rr, sell_pct}, ...]


@dataclass
class TrendResult:
    """Per-coin result with everything the report and orchestrator need."""
    base:           str
    symbol:         str
    price:          float
    volume_24h:     float
    price_24h_pct:  float
    funding_rate:   Optional[float]

    total_score:    float
    base_score:     float       # before bonuses
    bonus_score:    float
    tf_scores:      dict[str, float]    # label -> score
    tf_details:     dict[str, dict]      # label -> meta dict (rsi, adx, etc.)
    st_aligned:     int                  # # of TFs where SuperTrend is bull

    rsi_div_1d:     Optional[str]        # 'bullish'|'hidden_bullish'|None
    prepump_signals: list[str]           # 1H pre-pump flags fired
    rs_vs_btc_7d:   Optional[float]      # token_7d - btc_7d (raw decimal)

    tier:           str                  # 'strong'|'long'|'watch'|'below'
    regime:         str                  # 'bull'|'sideways'|'bear'
    trade_plan:     Optional[TradePlan] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.trade_plan is not None:
            d["trade_plan"] = asdict(self.trade_plan)
        return d


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _last(s: pd.Series) -> Optional[float]:
    """Return the last non-NaN value of a series, or None."""
    if s is None or len(s) == 0:
        return None
    s2 = s.dropna()
    if len(s2) == 0:
        return None
    return float(s2.iloc[-1])


def _safe_atr(df: pd.DataFrame, period: int = 14) -> float:
    """ATR scalar with NaN-safe fallback."""
    if df is None or len(df) < period + 1:
        return 0.0
    val = _last(compute_atr(df, period))
    return val if val is not None else 0.0


# Regime is persisted between runs so a transient BTC-1D fetch miss doesn't
# silently downgrade the whole long side to SIDEWAYS @ 0.00% (regime gates tier
# thresholds AND position sizing, so a wrong default is expensive).
_REGIME_STATE_FILE = _OUTPUT_DIR / "last_regime.json"


def _save_regime(regime: str, btc_7d_pct: float, btc_24h_pct: float) -> None:
    try:
        _REGIME_STATE_FILE.write_text(json.dumps({
            "regime":      regime,
            "btc_7d_pct":  btc_7d_pct,
            "btc_24h_pct": btc_24h_pct,
            "saved_at":    datetime.now().isoformat(),
        }), encoding="utf-8")
    except Exception as e:
        log.debug(f"Could not persist regime state: {e}")


def _load_last_regime() -> Optional[tuple[str, float, float]]:
    try:
        d = json.loads(_REGIME_STATE_FILE.read_text(encoding="utf-8"))
        return d["regime"], float(d["btc_7d_pct"]), float(d["btc_24h_pct"])
    except Exception:
        return None


def _detect_regime_live() -> tuple[str, float, float, float]:
    """LIVE regime via data.get_live_market_change() — fresh Bybit 4h klines,
    current close vs exactly 168h ago (7d) and 24h ago. This is the SAME method
    spot_scanner uses, so the radar and spot boards now report ONE consistent,
    live regime — fixing the stale closed-1D read that froze the radar's 7d for a
    full day (it compared yesterday's daily close to an 8-day-old one, ignoring
    the live session).

    On a fetch miss, reuse the LAST KNOWN regime (persisted from the previous good
    run) instead of silently defaulting to sideways; only default if there is no
    prior state. Thresholds unchanged. Returns (regime, btc_7d_pct, btc_24h_pct, btc_price).
    """
    chg = None
    for attempt in range(1, 3):
        chg = data.get_live_market_change()
        if chg is not None:
            break
        if attempt < 2:
            log.warning(f"  BTC regime fetch miss (attempt {attempt}/2) — retrying...")
            time.sleep(2)

    if chg is None:
        last = _load_last_regime()
        if last is not None:
            regime, b7, b24 = last
            log.warning(f"  BTC data unavailable — reusing last known regime: "
                        f"{regime.upper()} (BTC 7d {b7:+.2f}%)")
            return regime, b7, b24, 0.0
        log.warning("  BTC data unavailable and no prior regime on file — defaulting to sideways")
        return "sideways", 0.0, 0.0, 0.0

    btc_7d_pct, btc_24h_pct, btc_price = chg
    if btc_7d_pct >= REGIME["bull_btc_7d_pct"]:
        regime = "bull"
    elif btc_7d_pct >= REGIME["neutral_btc_7d_pct"]:
        regime = "sideways"
    else:
        regime = "bear"

    _save_regime(regime, btc_7d_pct, btc_24h_pct)
    return regime, btc_7d_pct, btc_24h_pct, btc_price


def _tiers_for_regime(regime: str) -> dict:
    return {"bull": TIERS_BULL, "sideways": TIERS_SIDEWAYS, "bear": TIERS_BEAR}[regime]


# ─────────────────────────────────────────────────────────────────────────────
# PER-TIMEFRAME SCORING  — distilled from enhanced_scan, using indicators.py
# ─────────────────────────────────────────────────────────────────────────────

def score_timeframe(df: pd.DataFrame, label: str) -> TFScore:
    """
    Score a single timeframe 0..~200.
    Returns TFScore with the score and key diagnostic values.

    Indicator point allocations match enhanced_scan's tuning so the new
    scanner produces numbers comparable to what you've been seeing.
    """
    if df is None or len(df) < 60:
        return TFScore(label=label, score=0.0)

    closes = df["close"]
    price  = float(closes.iloc[-1])
    score  = 0.0
    tf     = TFScore(label=label)

    # ── 1. SuperTrend (10,3) — max 20 ───────────────────────────────────────
    st_series = compute_supertrend(df, 10, 3.0)
    st_bull   = bool(st_series.iloc[-1]) if len(st_series) > 0 else False
    tf.st_bull = st_bull
    if st_bull:
        score += 20

    # ── 2. EMA stack 20/50/200 — max 15 ─────────────────────────────────────
    e20  = _last(compute_ema(closes,  20))
    e50  = _last(compute_ema(closes,  50))
    e200 = _last(compute_ema(closes, 200))
    if e20 and e50 and e200:
        if price > e20 > e50 > e200: score += 15
        elif price > e20 > e50:      score += 10
        elif price > e50:            score += 5

    # ── 3. RSI(7) — max 15 (matches v3 spec) ─────────────────────────────────
    rsi_now = _last(compute_rsi(closes, 7))
    tf.rsi  = round(rsi_now, 1) if rsi_now is not None else None
    if rsi_now is not None:
        if 40 <= rsi_now <= 65:    score += 15
        elif 65 < rsi_now <= 72:   score += 8
        elif rsi_now > 72:         score += 2
        elif rsi_now < 30:         score += 8    # oversold bounce

    # ── 4. MACD — max 17 ─────────────────────────────────────────────────────
    ml, sl, hl = compute_macd(closes)
    macd_now = _last(ml)
    sig_now  = _last(sl)
    hist_now = _last(hl)
    if macd_now is not None and sig_now is not None:
        if macd_now > sig_now:        score += 12
        if hist_now and hist_now > 0: score += 5

    # ── 5. ADX + DI — max 15 ─────────────────────────────────────────────────
    adx_s, pdi_s, ndi_s = compute_adx(df, 14)
    adx_now  = _last(adx_s)
    pdi_now  = _last(pdi_s)
    ndi_now  = _last(ndi_s)
    tf.adx       = round(adx_now,  1) if adx_now  is not None else None
    tf.plus_di   = round(pdi_now,  1) if pdi_now  is not None else None
    tf.minus_di  = round(ndi_now,  1) if ndi_now  is not None else None
    if pdi_now and ndi_now and pdi_now > ndi_now:
        score += 10
        if adx_now and adx_now > 25:
            score += 5

    # ── 6. Bollinger Bands — max 8 (price position) ─────────────────────────
    bb_u, bb_m, bb_l = compute_bb(closes, 20, 2.0)
    bb_u_n = _last(bb_u); bb_m_n = _last(bb_m); bb_l_n = _last(bb_l)
    if bb_u_n and bb_l_n and bb_u_n != bb_l_n:
        bb_pct = (price - bb_l_n) / (bb_u_n - bb_l_n)
        tf.bb_pct = round(bb_pct, 3)
        if 0.3 <= bb_pct <= 0.7:  score += 8
        elif bb_pct < 0.2:        score += 5

    # ── 7. Aroon (25) — max 10 ───────────────────────────────────────────────
    ar_up_s, ar_dn_s = compute_aroon(df, 25)
    ar_up = _last(ar_up_s)
    ar_dn = _last(ar_dn_s)
    if ar_up is not None and ar_dn is not None:
        if ar_up > 70 and ar_dn < 30: score += 10
        elif ar_up > ar_dn:           score += 5

    # ── 8. Stochastic RSI — max 15 ──────────────────────────────────────────
    k_line, d_line = compute_stoch_rsi(closes)
    k_now = _last(k_line); d_now = _last(d_line)
    if k_now is not None and d_now is not None:
        if k_now > d_now and k_now < 80:    score += 15
        elif k_now > d_now:                 score += 7
        elif k_now < 20:                    score += 5

    # ── 9. Ichimoku Cloud — max 15 ──────────────────────────────────────────
    tenkan, kijun, senkou_a, senkou_b = compute_ichimoku(df)
    tk = _last(tenkan); kj = _last(kijun)
    sa = _last(senkou_a); sb = _last(senkou_b)
    if tk and kj and sa and sb:
        cloud_bull   = sa > sb
        above_cloud  = price > max(sa, sb)
        tk_kj_bull   = tk > kj
        tf.cloud = "bull" if cloud_bull else "bear"
        if above_cloud and cloud_bull and tk_kj_bull: score += 15
        elif above_cloud and cloud_bull:              score += 10
        elif above_cloud:                             score += 5
        elif tk_kj_bull and cloud_bull:               score += 5

    # ── 10. CMF — max 10 ─────────────────────────────────────────────────────
    cmf_now = _last(compute_cmf(df, 20))
    tf.cmf = round(cmf_now, 3) if cmf_now is not None else None
    if cmf_now is not None:
        if cmf_now > 0.10:    score += 10
        elif cmf_now > 0:     score += 6
        elif cmf_now > -0.05: score += 2

    # ── 11. OBV slope — max 10 ──────────────────────────────────────────────
    obv = compute_obv(df)
    if len(obv) >= 10:
        obv_slope = float(obv.iloc[-1]) - float(obv.iloc[-10])
        if obv_slope > 0:
            score += 10

    # ── 12. MFI — max 10 ─────────────────────────────────────────────────────
    mfi_now = _last(compute_mfi(df, 14))
    tf.mfi = round(mfi_now, 1) if mfi_now is not None else None
    if mfi_now is not None:
        if 40 <= mfi_now <= 70: score += 10
        elif mfi_now > 70:      score += 4
        elif mfi_now < 30:      score += 6     # oversold

    # ── 13. CCI — max 8 ──────────────────────────────────────────────────────
    cci_now = _last(compute_cci(df, 20))
    if cci_now is not None:
        if   0 < cci_now <= 100:   score += 8
        elif cci_now > 100:        score += 3
        elif -100 <= cci_now < 0:  score += 2

    # ── 14. Hull MA — max 8 ──────────────────────────────────────────────────
    hma = compute_hull_ma(closes, 20)
    hma_now = _last(hma)
    hma_clean = hma.dropna()
    prev_hma  = float(hma_clean.iloc[-2]) if len(hma_clean) >= 2 else None
    if hma_now and prev_hma:
        if price > hma_now and hma_now > prev_hma: score += 8
        elif price > hma_now:                       score += 4

    # ── 15. Parabolic SAR — max 7 ───────────────────────────────────────────
    _, psar_bull = compute_psar(df)
    if len(psar_bull) > 0 and bool(psar_bull.iloc[-1]):
        score += 7

    # ── 16. Volume surge — max 8 ─────────────────────────────────────────────
    if "volume" in df.columns:
        vols = df["volume"]
        vsma = vols.rolling(20).mean()
        vsma_now = _last(vsma)
        if vsma_now and vsma_now > 0:
            vol_ratio = float(vols.iloc[-1]) / vsma_now
            if vol_ratio > 1.5:   score += 8
            elif vol_ratio > 1.2: score += 4

    # ── 17. ATR Trailing Stop bullish flag — max 8 ──────────────────────────
    # Simple proxy: price above EMA(50) with positive slope
    if e50 and price > e50:
        e50_series = compute_ema(closes, 50).dropna()
        if len(e50_series) >= 5 and float(e50_series.iloc[-1]) > float(e50_series.iloc[-5]):
            score += 8

    tf.score = round(score, 1)
    return tf


# ─────────────────────────────────────────────────────────────────────────────
# SCORE A WHOLE COIN  — combine TFs + bonuses + regime gate
# ─────────────────────────────────────────────────────────────────────────────

def score_coin(
    base:           str,
    symbol:         str,
    candles_by_tf:  dict[str, pd.DataFrame],
    btc_1d:         Optional[pd.DataFrame],
    funding_rate:   Optional[float],
    regime:         str,
    coin_meta:      dict,
) -> Optional[TrendResult]:
    """
    Multi-TF score + bonuses + regime-aware tier classification.
    Returns None if 1D data is too thin to be meaningful.
    """
    df_1d = candles_by_tf.get("1D")
    df_1h = candles_by_tf.get("1H")
    if df_1d is None or len(df_1d) < 60:
        return None

    # ── Score each TF ────────────────────────────────────────────────────────
    tf_scores:  dict[str, float] = {}
    tf_details: dict[str, dict]  = {}
    base_score: float = 0.0
    st_aligned: int   = 0

    for label, _, weight in TIMEFRAMES:
        candles = candles_by_tf.get(label)
        tfs     = score_timeframe(candles, label) if candles is not None else TFScore(label=label)
        tf_scores[label]  = tfs.score
        tf_details[label] = {
            "rsi":      tfs.rsi,
            "adx":      tfs.adx,
            "plus_di":  tfs.plus_di,
            "minus_di": tfs.minus_di,
            "st":       tfs.st_bull,
            "cmf":      tfs.cmf,
            "mfi":      tfs.mfi,
            "bb_pct":   tfs.bb_pct,
            "cloud":    tfs.cloud,
        }
        base_score += tfs.score * weight
        if tfs.st_bull:
            st_aligned += 1

    # ── Bonuses (added to base) ─────────────────────────────────────────────
    bonus = 0.0

    # RSI(7) divergence on 1D using signals.py (canonical)
    rsi_div_kind = None
    if len(df_1d) >= 80:
        res_div = S.sig_rsi_divergence(df_1d, rsi_period=7, lookback=60)
        if res_div.fired:
            kind = (res_div.extras or {}).get("kind")
            rsi_div_kind = kind
            if kind == "bullish":
                bonus += 5
            elif kind == "hidden_bullish":
                bonus += 3   # less weight for hidden divergence

    # Pre-pump cluster on 1H (BB squeeze / OBV divergence / ATR coil)
    prepump_signals: list[str] = []
    if df_1h is not None and len(df_1h) >= 50:
        if S.sig_bb_squeeze(df_1h).fired:        prepump_signals.append("bb_squeeze")
        if S.sig_obv_divergence(df_1h).fired:    prepump_signals.append("obv_divergence")
        if S.sig_obv_stealth_accum(df_1h).fired: prepump_signals.append("obv_stealth_accum")
        # +2 per signal, max +6
        bonus += min(len(prepump_signals) * 2, 6)

    # RS vs BTC 7d
    rs_vs_btc_7d: Optional[float] = None
    if btc_1d is not None and len(btc_1d) >= 8 and len(df_1d) >= 8:
        try:
            tok_7d = (float(df_1d["close"].iloc[-1])  / float(df_1d["close"].iloc[-8])  - 1)
            btc_7d = (float(btc_1d["close"].iloc[-1]) / float(btc_1d["close"].iloc[-8]) - 1)
            rs_vs_btc_7d = tok_7d - btc_7d
            if rs_vs_btc_7d > 0:
                bonus += 5
        except (ValueError, ZeroDivisionError):
            pass

    # Funding rate signal (bullish if negative, penalty if extreme positive)
    if funding_rate is not None:
        if S.sig_funding_negative(funding_rate).fired:
            bonus += 3
        if S.sig_funding_extreme_long(funding_rate).fired:
            bonus -= 3

    total_score = base_score + bonus

    # ── Apply regime gate ──────────────────────────────────────────────────
    tiers = _tiers_for_regime(regime)
    if total_score >= tiers["strong_score"] and st_aligned >= tiers["strong_st"]:
        tier = "strong"
    elif total_score >= tiers["long_score"] and st_aligned >= tiers["long_st"]:
        tier = "long"
    elif total_score >= tiers["watch_score"]:
        tier = "watch"
    else:
        tier = "below"

    # ── Build result ────────────────────────────────────────────────────────
    result = TrendResult(
        base             = base,
        symbol           = symbol,
        # Live ticker price, falling back to the last closed 1D bar. The 1D close
        # is up to a full day behind the tape, and this price feeds build_trade_plan
        # below — a stale anchor there means stale entry/stop/TP levels.
        price            = float(coin_meta.get("price") or df_1d["close"].iloc[-1]),
        volume_24h       = float(coin_meta.get("turnover_24h", coin_meta.get("volume_24h", 0))),
        price_24h_pct    = float(coin_meta.get("price_24h_pct", 0)),
        funding_rate     = funding_rate,
        total_score      = round(total_score, 1),
        base_score       = round(base_score,  1),
        bonus_score      = round(bonus,       1),
        tf_scores        = {k: round(v, 1) for k, v in tf_scores.items()},
        tf_details       = tf_details,
        st_aligned       = st_aligned,
        rsi_div_1d       = rsi_div_kind,
        prepump_signals  = prepump_signals,
        rs_vs_btc_7d     = round(rs_vs_btc_7d, 4) if rs_vs_btc_7d is not None else None,
        tier             = tier,
        regime           = regime,
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# TRADE PLAN BUILDER  — distilled from spot_scanner
# ─────────────────────────────────────────────────────────────────────────────

def build_trade_plan(
    price:   float,
    atr_1d:  float,
    regime:  str,
    account_size: float,
    macro_mult:   float = 1.0,
) -> Optional[TradePlan]:
    """
    ATR-based trade plan with regime-aware sizing.
    `macro_mult` (0-1) scales position size down in macro headwinds (Layer 0).
    Default 1.0 = no macro effect (e.g. if called without the overlay).
    Returns None if inputs are invalid.
    """
    if price <= 0 or atr_1d <= 0 or account_size <= 0:
        return None

    # Regime-specific parameters
    atr_mult  = (ACCOUNT["atr_stop_mult_sideways"] if regime == "sideways"
                 else ACCOUNT["atr_stop_mult_bull"])
    risk_pct  = (ACCOUNT["risk_pct_sideways"] if regime == "sideways"
                 else ACCOUNT["risk_pct_bull"])

    # Stop = max(price - atr_mult*ATR, price * (1 + stop_min/100)),
    # capped to stop_max
    stop_raw     = price - atr_mult * atr_1d
    stop_floor   = price * (1 + ACCOUNT["stop_min_pct"]  / 100)   # widest stop
    stop_ceiling = price * (1 + ACCOUNT["stop_max_pct"]  / 100)   # tightest stop
    stop         = max(stop_raw, stop_floor)
    stop         = min(stop,     stop_ceiling)

    risk_per_unit = price - stop
    if risk_per_unit <= 0:
        return None

    # Base risk from regime, then scale by the macro overlay (Layer 0).
    # macro_mult only shrinks/holds — it is clamped to (0, 1].
    macro_mult   = max(0.0, min(1.0, macro_mult))
    risk_usdt    = account_size * (risk_pct / 100) * macro_mult
    quantity     = risk_usdt / risk_per_unit
    pos_value    = quantity * price
    if pos_value > ACCOUNT["max_notional"]:
        # Cap position size at max notional
        pos_value = ACCOUNT["max_notional"]
        quantity  = pos_value / price
        risk_usdt = quantity * risk_per_unit

    take_profits = []
    for rr, split_pct in zip(ACCOUNT["tp_rr"], ACCOUNT["tp_split_pct"]):
        tp_price = price + rr * risk_per_unit
        take_profits.append({
            "price":     round(tp_price, 8),
            "gain_pct":  round((tp_price - price) / price * 100, 2),
            "rr":        rr,
            "sell_pct":  split_pct,
        })

    return TradePlan(
        entry        = round(price, 8),
        stop         = round(stop,  8),
        stop_pct     = round((stop - price) / price * 100, 2),
        risk_usdt    = round(risk_usdt, 2),
        risk_pct     = round((risk_usdt / account_size) * 100, 3),
        pos_value    = round(pos_value, 2),
        pos_pct      = round((pos_value / account_size) * 100, 2),
        quantity     = round(quantity,  6),
        take_profits = take_profits,
    )


# ─────────────────────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────────────────────

def _tf_breakdown_str(tf_scores: dict[str, float]) -> str:
    """One-line per-TF breakdown: '1D=145 12H=130 6H=115 ...'"""
    return "  ".join(f"{lbl}={tf_scores.get(lbl, 0):>3.0f}" for lbl, _, _ in TIMEFRAMES)


def build_text_report(
    strong:         list[TrendResult],
    longs:          list[TrendResult],
    watch:          list[TrendResult],
    universe_size:  int,
    scanned:        int,
    elapsed_s:      float,
    regime:         str,
    btc_7d_pct:     float,
    btc_24h_pct:    float,
    btc_price:      float,
    account_size:   float,
) -> str:
    sep  = "=" * 80
    dash = "-" * 80
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    tiers = _tiers_for_regime(regime)
    regime_emoji = {"bull": "🟢 BULL", "sideways": "🟡 SIDEWAYS", "bear": "🔴 BEAR"}[regime]

    lines = [
        sep,
        "  TREND SCANNER v3.0  —  multi-timeframe confirmation + trade plans",
        f"  Generated  : {ts}",
        f"  Universe   : {universe_size} Bybit perps  |  Scanned: {scanned}",
        f"  Scan time  : {elapsed_s:.0f}s",
        sep,
        "",
        f"  Regime     : {regime_emoji}",
        f"               BTC 7d {btc_7d_pct:+.2f}%  |  24h {btc_24h_pct:+.2f}%  |  ${btc_price:,.0f}",
    ]
    if regime == "bear":
        lines.append("               ⚠️  STRONG/LONG tiers BLOCKED — capital preservation mode")
    elif regime == "sideways":
        lines.append("               ⚠️  Tightened thresholds, half position size")
    lines.append("")
    lines.append(f"  Account    : ${account_size:,.0f}  |  Risk/trade: "
                 f"{ACCOUNT['risk_pct_sideways' if regime == 'sideways' else 'risk_pct_bull']}%")
    lines.append("")
    lines.append(
        f"  Tiers ({regime}):  STRONG ≥ {tiers['strong_score']:.0f} & ST≥{tiers['strong_st']}  |  "
        f"LONG ≥ {tiers['long_score']:.0f} & ST≥{tiers['long_st']}  |  "
        f"WATCH ≥ {tiers['watch_score']:.0f}"
    )
    lines.append("")
    lines.append(f"  TF weights : {' · '.join(f'{lbl}×{w}' for lbl,_,w in TIMEFRAMES)}")
    lines.append("")

    # ── STRONG tier — full detail with trade plan ───────────────────────────
    lines.append(dash)
    lines.append(f"  STRONG  —  {len(strong)} coin(s)  (highest conviction, full position)")
    lines.append(dash)
    if not strong:
        lines.append("  (none)")
    for i, r in enumerate(strong, 1):
        _render_full_entry(lines, i, r)

    # ── LONG tier — full detail with trade plan ─────────────────────────────
    lines.append("")
    lines.append(dash)
    lines.append(f"  LONG  —  {len(longs)} coin(s)  (tradeable trend)")
    lines.append(dash)
    if not longs:
        lines.append("  (none)")
    for i, r in enumerate(longs, 1):
        _render_full_entry(lines, i, r)

    # ── WATCH tier — compact table ───────────────────────────────────────────
    lines.append("")
    lines.append(dash)
    lines.append(f"  WATCH  —  {len(watch)} coin(s)  (worth tracking)")
    lines.append(dash)
    lines.append(
        f"  {'#':<3} {'Symbol':<10} {'Score':>6} {'ST':>3}  "
        f"{'Price':>12} {'24h%':>7} {'Vol $M':>8}  TF breakdown"
    )
    lines.append("  " + "-" * 78)
    for i, r in enumerate(watch, 1):
        lines.append(
            f"  {i:>3} {r.base:<10} {r.total_score:>6.1f} {r.st_aligned:>2}/6  "
            f"${r.price:>11,.6f} {r.price_24h_pct:>+6.2f}%  "
            f"${r.volume_24h/1e6:>7.1f}  {_tf_breakdown_str(r.tf_scores)}"
        )

    lines.append("")
    lines.append(sep)
    return "\n".join(lines)


def _render_full_entry(lines: list[str], idx: int, r: TrendResult) -> None:
    """Append a rich-format entry for STRONG/LONG tier coin."""
    lines.append("")
    lines.append(
        f"  [{idx:>2}] {r.base:<10}  score={r.total_score:>6.1f}  "
        f"ST={r.st_aligned}/6  "
        f"price=${r.price:<12,.6f}  "
        f"24h={r.price_24h_pct:+6.2f}%  vol=${r.volume_24h/1e6:>5.1f}M"
    )
    lines.append(f"       TF:  {_tf_breakdown_str(r.tf_scores)}")

    # Bonuses summary
    bonus_parts = []
    if r.rsi_div_1d:           bonus_parts.append(f"RSI div 1D ({r.rsi_div_1d})")
    if r.prepump_signals:      bonus_parts.append(f"1H pre-pump ({', '.join(r.prepump_signals)})")
    if r.rs_vs_btc_7d is not None and r.rs_vs_btc_7d > 0:
        bonus_parts.append(f"RS vs BTC 7d {r.rs_vs_btc_7d*100:+.1f}%")
    if r.funding_rate is not None and r.funding_rate < -0.0001:
        bonus_parts.append(f"funding {r.funding_rate*100:+.4f}%/8h")
    if bonus_parts:
        lines.append(f"       Bonuses: {' | '.join(bonus_parts)}  (+{r.bonus_score:.0f} pts)")

    # Trade plan
    if r.trade_plan:
        tp = r.trade_plan
        lines.append(
            f"       Entry: ${tp.entry:.6f}  |  Stop: ${tp.stop:.6f} ({tp.stop_pct:+.2f}%)  |  "
            f"Risk: ${tp.risk_usdt:.0f} ({tp.risk_pct:.2f}% acct)"
        )
        lines.append(
            f"       Position: ${tp.pos_value:.0f} ({tp.pos_pct:.2f}% acct, qty {tp.quantity:.4f})"
        )
        for j, t in enumerate(tp.take_profits, 1):
            lines.append(
                f"       TP{j}: ${t['price']:.6f} (+{t['gain_pct']:.1f}%, "
                f"{t['rr']:.1f}R, sell {t['sell_pct']}%)"
            )


def build_json_payload(
    strong:         list[TrendResult],
    longs:          list[TrendResult],
    watch:          list[TrendResult],
    universe_size:  int,
    scanned:        int,
    elapsed_s:      float,
    regime:         str,
    btc_7d_pct:     float,
    btc_24h_pct:    float,
    account_size:   float,
) -> dict:
    return {
        "scanner":       "trend_scanner",
        "version":       "3.0",
        "generated_at":  datetime.now().isoformat(),
        "elapsed_s":     round(elapsed_s, 2),
        "universe_size": universe_size,
        "scanned":       scanned,
        "regime":        regime,
        "btc_7d_pct":    round(btc_7d_pct, 2),
        "btc_24h_pct":   round(btc_24h_pct, 2),
        "account_size":  account_size,
        "tiers":         _tiers_for_regime(regime),
        "tf_weights":    {lbl: w for lbl, _, w in TIMEFRAMES},
        "strong":        [r.to_dict() for r in strong],
        "long":          [r.to_dict() for r in longs],
        "watch":         [r.to_dict() for r in watch],
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ORCHESTRATION
# ─────────────────────────────────────────────────────────────────────────────

def run(
    account_size:  float = ACCOUNT["default_size_usdt"],
    use_cache:     bool  = True,
    top_n:         int   = 50,
) -> dict:
    scan_start = time.time()
    log.info("=" * 64)
    log.info("TREND SCANNER v3.0")
    log.info("=" * 64)

    # ── Universe (Bybit perps only) ──────────────────────────────────────────
    log.info("Building universe (Bybit perps)...")
    universe_raw = data.get_universe("bybit")
    universe = [c for c in universe_raw
                if c.get("turnover_24h", 0) >= SCAN["min_turnover_24h"]
                and c.get("price", 0) > 0]
    universe = universe[:SCAN["max_coins"]]
    log.info(f"  Universe (post-filter): {len(universe)} coins")

    if not universe:
        log.error("No coins passed filters — aborting")
        return {}

    # ── BTC reference for regime + RS ────────────────────────────────────────
    log.info("Detecting market regime (live BTC 4h, unified with spot)...")
    regime, btc_7d_pct, btc_24h_pct, btc_price = _detect_regime_live()
    log.info(f"  Regime: {regime.upper()}  |  BTC 7d {btc_7d_pct:+.2f}%  "
             f"|  24h {btc_24h_pct:+.2f}%")

    # BTC 1D reference dataframe for per-coin relative-strength (rs_vs_btc).
    # Regime/7d/24h now come from the live 4h calc above, but score_coin still
    # needs the BTC 1D series — fetch it here so it stays defined.
    btc_1d = data.get_btc("1d", SCAN["ohlcv_bars"])

    if regime == "bear":
        log.warning("  BEAR regime — STRONG/LONG tiers blocked, capital preservation mode")

    # ── Macro overlay (Layer 0): read DXY+yields verdict once per scan ───────
    macro_mult, macro_reason = get_macro_multiplier()
    log.info(f"  Macro sizing overlay: {macro_reason}")
    if macro_mult < 1.0:
        log.info(f"  → position sizes scaled to {macro_mult:.0%} of regime base")

    # ── Per-coin scan: fetch all 6 TFs, score, build trade plans ─────────────
    log.info(f"Scanning {len(universe)} coins across {len(TIMEFRAMES)} timeframes...")
    log.info("(this is the slowest scanner — first run ~10min, subsequent ~2-3min)")

    results: list[TrendResult] = []
    failed:   int = 0
    progress_every = max(20, len(universe) // 20)

    for i, coin in enumerate(universe, 1):
        base   = coin["base"]
        symbol = coin.get("symbol_bybit", f"{base}USDT")

        # Fetch all 6 TFs (cache will hit after first run)
        candles_by_tf: dict[str, pd.DataFrame] = {}
        for label, tf_key, _ in TIMEFRAMES:
            df = data.get_ohlcv(base, "bybit", tf_key,
                                SCAN["ohlcv_bars"], use_cache=use_cache)
            if df is not None:
                candles_by_tf[label] = df

        if "1D" not in candles_by_tf:
            failed += 1
            continue

        funding = coin.get("funding_rate")

        result = score_coin(
            base          = base,
            symbol        = symbol,
            candles_by_tf = candles_by_tf,
            btc_1d        = btc_1d,
            funding_rate  = funding,
            regime        = regime,
            coin_meta     = coin,
        )
        if result is None:
            failed += 1
            continue

        # Build trade plan for STRONG/LONG only
        if result.tier in ("strong", "long"):
            atr_1d = _safe_atr(candles_by_tf["1D"], 14)
            result.trade_plan = build_trade_plan(
                price        = result.price,
                atr_1d       = atr_1d,
                regime       = regime,
                account_size = account_size,
                macro_mult   = macro_mult,
            )

        if result.tier != "below":
            results.append(result)

        if i % progress_every == 0:
            log.info(f"  ...{i}/{len(universe)}  (surfaced: {len(results)})")

    # ── Sort & split into tiers ──────────────────────────────────────────────
    results.sort(key=lambda r: r.total_score, reverse=True)
    strong = [r for r in results if r.tier == "strong"][:top_n]
    longs  = [r for r in results if r.tier == "long"  ][:top_n]
    watch  = [r for r in results if r.tier == "watch" ][:top_n]

    elapsed_s = time.time() - scan_start
    log.info(
        f"Done in {elapsed_s:.0f}s  |  scanned={len(universe)-failed}  failed={failed}  "
        f"strong={len(strong)}  long={len(longs)}  watch={len(watch)}"
    )

    # ── Reports ──────────────────────────────────────────────────────────────
    report_text = build_text_report(
        strong, longs, watch, len(universe), len(universe) - failed,
        elapsed_s, regime, btc_7d_pct, btc_24h_pct, btc_price, account_size,
    )
    log.info("\n" + report_text)

    payload = build_json_payload(
        strong, longs, watch, len(universe), len(universe) - failed,
        elapsed_s, regime, btc_7d_pct, btc_24h_pct, account_size,
    )

    # ── Write outputs ────────────────────────────────────────────────────────
    ts_file     = datetime.now().strftime("%Y%m%d_%H%M%S")
    txt_ts      = _OUTPUT_DIR / f"trend_v3_{ts_file}.txt"
    txt_latest  = _OUTPUT_DIR / "trend_v3_LATEST.txt"
    json_latest = _OUTPUT_DIR / "trend_v3_LATEST.json"
    json_ts     = _OUTPUT_DIR / f"trend_v3_{ts_file}.json"

    txt_ts.write_text(report_text, encoding="utf-8")
    txt_latest.write_text(report_text, encoding="utf-8")
    json_ts.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    json_latest.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    log.info(f"  Saved → {txt_latest.name}, {json_latest.name}, {txt_ts.name}")

    return payload


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description="Trend Scanner v3.0 — multi-TF confluence + trade plans",
    )
    parser.add_argument("--account",  type=float, default=ACCOUNT["default_size_usdt"],
                        help=f"Account size in USDT (default: {ACCOUNT['default_size_usdt']:,.0f})")
    parser.add_argument("--top",      type=int,   default=50, help="Max entries per tier")
    parser.add_argument("--no-cache", action="store_true",    help="Force fresh OHLCV fetch")
    args = parser.parse_args()

    run(
        account_size = args.account,
        use_cache    = not args.no_cache,
        top_n        = args.top,
    )
