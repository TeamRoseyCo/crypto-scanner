"""
================================================================================
SHORT SCANNER  v3.0
================================================================================
Bearish mirror of ignition_scanner. Surfaces shorting opportunities on Bybit
perps — distribution, bearish divergences, lower-highs structure, failed
breakouts, crowded-long funding.

What it answers: "What's about to break?"
  Identifies coins showing distribution / bearish divergence / topping patterns
  BEFORE they roll over. Uses the same signal philosophy as ignition but
  inverted: bearish-twin signals from signals.py.

Bybit perps only — you need a perp/margin venue to short. Spot can't.

Hard exclusions (prevents shorting into a squeeze):
  - funding_rate < -0.0001  (shorts already paying — squeeze fuel)
  - Cross-checking against ignition WATCH NOW is the orchestrator's job, NOT
    this scanner's. This scanner just surfaces; orchestrator de-conflicts.

Output: same format as ignition (LATEST.txt + LATEST.json + timestamped TXT)

PREREQUISITE — add these to signals.py first (see signals_short_additions.py):
  sig_vol_distribution
  sig_bear_distribution_candle
  sig_bear_obv_distribution
  sig_bear_obv_divergence
  sig_bear_rsi_divergence
  sig_rsi_overbought_reset
  sig_lower_highs
  sig_cmf_negative
  sig_bear_failed_breakout
  sig_price_range_fail
  sig_btc_underperform

Reused from existing signals.py without modification:
  sig_bb_squeeze          (squeeze is direction-agnostic)
  sig_rsi_in_zone         (called with bearish-zone params 40-65)
  sig_funding_extreme_long

Run:
  python short_scanner.py
  python short_scanner.py --top 30
  python short_scanner.py --no-cache
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

import pandas as pd

import data
import signals as S


# ─────────────────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────────────────
_THIS_DIR     = Path(__file__).resolve().parent
_ENGINE_DIR   = _THIS_DIR.parent
_PYTHON_DIR   = _ENGINE_DIR.parent
_PROJECT_ROOT = _PYTHON_DIR.parent
_OUTPUT_DIR   = _PROJECT_ROOT / "outputs" / "scanner-results"
_LOG_DIR      = _PROJECT_ROOT / "outputs" / "logs"
for d in (_OUTPUT_DIR, _LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
log = logging.getLogger("scanner_v3.short")
if not log.handlers:
    handler_file = logging.FileHandler(
        _LOG_DIR / f"short_v3_{datetime.now().strftime('%Y%m%d')}.log",
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

SCAN = {
    "tf":              "1h",
    "ohlcv_bars":       200,
    "max_coins":         500,
    "min_volume_24h": 500_000,   # Higher floor than ignition: don't short illiquid
    "min_price":      1e-6,
    "max_24h_change":   30.0,    # already-pumping coins can keep pumping
    "min_24h_change":  -50.0,    # don't short something already crashed
    # Hard exclude: negative funding means short squeeze setup, not short setup
    "funding_exclude_below": -0.0001,
}

# Tier thresholds — slightly higher than ignition. Shorts in crypto are
# asymmetric (squeeze risk is real), so demand more confluence.
TIERS = {
    "watch_now_conviction": 45.0,
    "watch_now_signals":     5,
    "on_radar_conviction":  28.0,
    "on_radar_signals":      3,
}

# Bearish-twin signal weights. Divergences and distribution candles are the
# strongest tells (same reasoning as ignition's bullish weights).
SIGNAL_WEIGHTS: dict[str, float] = {
    # Core bearish (11) ─────────────────────────────────────────────
    "bear_distribution_candle": 2.5,
    "bear_obv_divergence":      2.5,
    "bear_rsi_divergence":      2.5,
    "bear_obv_distribution":    2.0,
    "bear_failed_breakout":     2.0,
    "bb_squeeze":               1.5,    # reused (direction-agnostic)
    "cmf_negative":             1.5,
    "lower_highs":              1.5,
    "rsi_overbought_reset":     1.5,
    "rsi_in_zone_bear":         1.0,    # reuses sig_rsi_in_zone with bear params
    "vol_distribution":         1.0,
    # Bonus (perp-specific) (3) ────────────────────────────────────
    "funding_extreme_long":     1.5,    # reused from existing signals.py
    "btc_underperform":         1.0,
    "price_range_fail":         1.0,
}
TOTAL_WEIGHT = sum(SIGNAL_WEIGHTS.values())   # ~22.5

# Per-signal params
SIGNAL_PARAMS: dict[str, dict] = {
    "bb_squeeze":               {"width_lookback": 120, "width_pct": 20.0},
    "vol_distribution":         {"recent": 6, "base_start": 7, "base_end": 42, "mult": 1.5},
    "bear_distribution_candle": {"lookback": 6, "atr_mult": 1.8, "close_lower_pct": 0.30},
    "bear_obv_distribution":    {"obv_lookback": 12, "max_obv_pct": -0.015, "max_price_move": 0.03},
    "bear_obv_divergence":      {"lookback": 40, "pivot_left": 3, "pivot_right": 3},
    "bear_rsi_divergence":      {"rsi_period": 7, "lookback": 60},
    "rsi_overbought_reset":     {"rsi_period": 7, "lookback": 24, "high_thresh": 68.0},
    "rsi_in_zone_bear":         {"rsi_period": 7, "low": 40.0, "high": 65.0},
    "cmf_negative":             {"period": 20, "threshold": -0.05},
    "lower_highs":              {"window": 30, "pivot_left": 3, "pivot_right": 3},
    "bear_failed_breakout":     {"lookback": 60, "max_bars_back": 5},
    "btc_underperform":         {"window": 6,  "min_underperform": 0.015},
    "funding_extreme_long":     {"threshold": 0.0008},
    "price_range_fail":         {"lookback": 120, "rejection_pct": 0.02},
}


# ─────────────────────────────────────────────────────────────────────────────
# RESULT TYPE
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ShortResult:
    base:           str
    price:          float
    volume_24h:     float
    price_24h_pct:  float
    direction:      str = "short"                              # explicit for orchestrator
    tier:           str = "below"
    conviction:     float = 0.0
    signal_count:   int = 0
    fired_signals:  list[str] = field(default_factory=list)
    signal_strengths: dict[str, float] = field(default_factory=dict)
    signal_extras:    dict[str, dict]   = field(default_factory=dict)
    funding_rate:   Optional[float]  = None
    rs_btc_6h_pct:  Optional[float]  = None
    excluded_reason: Optional[str]   = None
    error:          Optional[str]    = None

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# SCORING
# ─────────────────────────────────────────────────────────────────────────────

def score_coin(
    base:        str,
    df:          pd.DataFrame,
    btc_closes:  Optional[pd.Series] = None,
    funding_rate: Optional[float]    = None,
) -> Optional[ShortResult]:
    """Run all bearish signals on `df`. Returns ShortResult or None if too thin."""
    if df is None or len(df) < 50:
        return None

    # ── Hard exclude: never short with negative funding (squeeze setup) ─────
    if funding_rate is not None and funding_rate < SCAN["funding_exclude_below"]:
        return ShortResult(
            base            = base,
            price           = float(df["close"].iloc[-1]),
            volume_24h      = 0.0,
            price_24h_pct   = 0.0,
            funding_rate    = funding_rate,
            tier            = "below",
            excluded_reason = f"funding_rate {funding_rate*100:+.4f}% < threshold",
        )

    fired: list[str]            = []
    strengths: dict[str, float] = {}
    extras: dict[str, dict]     = {}
    earned_weight: float        = 0.0

    def record(name: str, res: S.SignalResult) -> None:
        nonlocal earned_weight
        if res.fired:
            fired.append(name)
            strengths[name] = round(res.strength, 3)
            if res.extras:
                extras[name] = res.extras
            w = SIGNAL_WEIGHTS.get(name, 0.0)
            earned_weight += w * (0.5 + 0.5 * res.strength)

    # ── Core bearish signals ─────────────────────────────────────────────────
    # Reused from existing signals.py:
    record("bb_squeeze",        S.sig_bb_squeeze       (df, **SIGNAL_PARAMS["bb_squeeze"]))
    # rsi_in_zone reused with bearish-zone params (40-65):
    record("rsi_in_zone_bear",  S.sig_rsi_in_zone      (df, **SIGNAL_PARAMS["rsi_in_zone_bear"]))

    # New bearish-twin signals (must be added to signals.py — see additions file):
    record("vol_distribution",         S.sig_vol_distribution        (df, **SIGNAL_PARAMS["vol_distribution"]))
    record("bear_distribution_candle", S.sig_bear_distribution_candle(df, **SIGNAL_PARAMS["bear_distribution_candle"]))
    record("bear_obv_distribution",    S.sig_bear_obv_distribution   (df, **SIGNAL_PARAMS["bear_obv_distribution"]))
    record("bear_obv_divergence",      S.sig_bear_obv_divergence     (df, **SIGNAL_PARAMS["bear_obv_divergence"]))
    record("bear_rsi_divergence",      S.sig_bear_rsi_divergence     (df, **SIGNAL_PARAMS["bear_rsi_divergence"]))
    record("rsi_overbought_reset",     S.sig_rsi_overbought_reset    (df, **SIGNAL_PARAMS["rsi_overbought_reset"]))
    record("cmf_negative",             S.sig_cmf_negative            (df, **SIGNAL_PARAMS["cmf_negative"]))
    record("lower_highs",              S.sig_lower_highs             (df, **SIGNAL_PARAMS["lower_highs"]))
    record("bear_failed_breakout",     S.sig_bear_failed_breakout    (df, **SIGNAL_PARAMS["bear_failed_breakout"]))

    # ── Bonus: BTC underperformance ─────────────────────────────────────────
    rs_btc_6h_pct: Optional[float] = None
    if btc_closes is not None and len(btc_closes) >= 8:
        res = S.sig_btc_underperform(df["close"], btc_closes, **SIGNAL_PARAMS["btc_underperform"])
        record("btc_underperform", res)
        if res.value is not None:
            rs_btc_6h_pct = round(res.value * 100, 2)

    # ── Bonus: extreme positive funding (crowded long) — reused from signals.py
    if funding_rate is not None:
        record("funding_extreme_long",
               S.sig_funding_extreme_long(funding_rate, **SIGNAL_PARAMS["funding_extreme_long"]))

    # ── Bonus: rejection at recent high ─────────────────────────────────────
    record("price_range_fail", S.sig_price_range_fail(df, **SIGNAL_PARAMS["price_range_fail"]))

    # ── Conviction & tier ───────────────────────────────────────────────────
    conviction   = round((earned_weight / TOTAL_WEIGHT) * 100, 1)
    signal_count = len(fired)

    if conviction >= TIERS["watch_now_conviction"] and signal_count >= TIERS["watch_now_signals"]:
        tier = "watch_now"
    elif conviction >= TIERS["on_radar_conviction"] and signal_count >= TIERS["on_radar_signals"]:
        tier = "on_radar"
    else:
        tier = "below"

    return ShortResult(
        base             = base,
        price            = float(df["close"].iloc[-1]),
        volume_24h       = 0.0,
        price_24h_pct    = 0.0,
        tier             = tier,
        conviction       = conviction,
        signal_count     = signal_count,
        fired_signals    = fired,
        signal_strengths = strengths,
        signal_extras    = extras,
        funding_rate     = funding_rate,
        rs_btc_6h_pct    = rs_btc_6h_pct,
    )


# ─────────────────────────────────────────────────────────────────────────────
# UNIVERSE — Bybit perps only
# ─────────────────────────────────────────────────────────────────────────────

def build_universe() -> list[dict]:
    """Bybit-only universe — you need a perp venue to short."""
    universe = data.get_universe("bybit")
    filtered = []
    for c in universe:
        vol = max(c.get("volume_24h", 0.0), c.get("turnover_24h", 0.0))
        if vol < SCAN["min_volume_24h"]:
            continue
        if c.get("price", 0.0) < SCAN["min_price"]:
            continue
        chg = c.get("price_24h_pct", 0.0)
        if chg < SCAN["min_24h_change"] or chg > SCAN["max_24h_change"]:
            continue
        c["_combined_volume"] = vol
        filtered.append(c)

    filtered.sort(key=lambda x: x["_combined_volume"], reverse=True)
    return filtered[:SCAN["max_coins"]]


# ─────────────────────────────────────────────────────────────────────────────
# REPORTS
# ─────────────────────────────────────────────────────────────────────────────

def _signal_label(name: str) -> str:
    return {
        "bb_squeeze":               "TTM squeeze",
        "vol_distribution":         "vol distrib",
        "bear_distribution_candle": "distrib candle",
        "bear_obv_distribution":    "OBV distrib",
        "bear_obv_divergence":      "OBV bear div",
        "bear_rsi_divergence":      "RSI bear div",
        "rsi_overbought_reset":     "RSI OB reset",
        "rsi_in_zone_bear":         "RSI top zone",
        "cmf_negative":             "CMF selling",
        "lower_highs":              "lower highs",
        "bear_failed_breakout":     "failed bo",
        "btc_underperform":         "vs BTC weak",
        "funding_extreme_long":     "fund hot",
        "price_range_fail":         "5d top rej",
    }.get(name, name)


def build_text_report(
    watch_now:    list[ShortResult],
    on_radar:     list[ShortResult],
    universe_size: int,
    scanned:      int,
    excluded:     int,
    elapsed_s:    float,
    btc_price:    Optional[float],
) -> str:
    sep  = "=" * 80
    dash = "-" * 80
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        sep,
        "  SHORT SCANNER v3.0  —  bearish setup watchlist",
        f"  Generated : {ts}",
        f"  Universe  : {universe_size} Bybit perps  |  Scanned: {scanned}  |  Excluded (funding): {excluded}",
        f"  Scan time : {elapsed_s:.0f}s"
        + (f"  |  BTC: ${btc_price:,.0f}" if btc_price else ""),
        sep,
        "",
        f"  Tiers — WATCH NOW: conviction ≥ {TIERS['watch_now_conviction']:.0f},"
        f" signals ≥ {TIERS['watch_now_signals']}",
        f"          ON RADAR : conviction ≥ {TIERS['on_radar_conviction']:.0f},"
        f" signals ≥ {TIERS['on_radar_signals']}",
        "",
        "  Hard exclude: funding rate ≤ -0.01% (avoid shorting into squeeze).",
        "  This scanner does NOT produce trade plans — surfaces what's about to break.",
        "  Confirm with trend_scanner regime check (BEAR/SIDEWAYS preferred) before entry.",
        "",
        dash,
    ]

    # WATCH NOW
    lines.append(f"  WATCH NOW (SHORT)  —  {len(watch_now)} coin(s)")
    lines.append(dash)
    if not watch_now:
        lines.append("  (none)")
    else:
        for i, r in enumerate(watch_now, 1):
            sigs = ", ".join(_signal_label(s) for s in r.fired_signals)
            extra_parts = []
            if r.funding_rate is not None:
                extra_parts.append(f"fund: {r.funding_rate*100:+.4f}%/8h")
            if r.rs_btc_6h_pct is not None:
                extra_parts.append(f"vs BTC 6h: {r.rs_btc_6h_pct:+.2f}%")
            extra_str = "  |  ".join(extra_parts)
            lines.append("")
            lines.append(
                f"  [{i:>2}] {r.base:<10}  conv={r.conviction:>5.1f}  "
                f"sigs={r.signal_count:>2}  "
                f"price=${r.price:<12,.6f}  "
                f"24h={r.price_24h_pct:+6.2f}%  "
                f"vol=${r.volume_24h/1e6:>6.1f}M"
            )
            if extra_str:
                lines.append(f"       {extra_str}")
            lines.append(f"       → {sigs}")

    # ON RADAR
    lines.append("")
    lines.append(dash)
    lines.append(f"  ON RADAR (SHORT)   —  {len(on_radar)} coin(s)")
    lines.append(dash)
    lines.append(
        f"  {'#':<3} {'Symbol':<10} {'Conv':>5} {'Sigs':>4}"
        f"  {'Price':>12}  {'24h%':>7}  {'Vol $M':>8}  Signals"
    )
    lines.append("  " + "-" * 78)
    if not on_radar:
        lines.append("  (none)")
    else:
        for i, r in enumerate(on_radar, 1):
            sigs_short = ", ".join(_signal_label(s) for s in r.fired_signals[:5])
            if len(r.fired_signals) > 5:
                sigs_short += f", +{len(r.fired_signals)-5}"
            lines.append(
                f"  {i:>3} {r.base:<10} {r.conviction:>5.1f} {r.signal_count:>4}"
                f"  ${r.price:>11,.6f}  {r.price_24h_pct:>+6.2f}%"
                f"  ${r.volume_24h/1e6:>7.1f}  {sigs_short}"
            )

    lines.append("")
    lines.append(sep)
    return "\n".join(lines)


def build_json_payload(
    watch_now:    list[ShortResult],
    on_radar:     list[ShortResult],
    universe_size: int,
    scanned:      int,
    excluded:     int,
    elapsed_s:    float,
) -> dict:
    return {
        "scanner":       "short_scanner",
        "version":       "3.0",
        "direction":     "short",
        "generated_at":  datetime.now().isoformat(),
        "elapsed_s":     round(elapsed_s, 2),
        "universe_size": universe_size,
        "scanned":       scanned,
        "excluded":      excluded,
        "thresholds":    TIERS,
        "weights":       SIGNAL_WEIGHTS,
        "watch_now":     [r.to_dict() for r in watch_now],
        "on_radar":      [r.to_dict() for r in on_radar],
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run(top_n: int = 50, use_cache: bool = True) -> dict:
    scan_start = time.time()
    log.info("=" * 64)
    log.info("SHORT SCANNER v3.0")
    log.info("=" * 64)

    log.info("Building universe (Bybit perps)...")
    universe = build_universe()
    universe_size = len(universe)
    log.info(f"  Universe (post-filter): {universe_size} coins")

    if not universe:
        log.error("No coins passed filters — aborting")
        return {}

    log.info("Fetching BTC reference (1h)...")
    btc_df  = data.get_btc(SCAN["tf"], SCAN["ohlcv_bars"])
    btc_closes = btc_df["close"] if btc_df is not None else None
    btc_price  = float(btc_df["close"].iloc[-1]) if btc_df is not None else None

    log.info(f"Scanning {universe_size} coins on {SCAN['tf']} timeframe...")
    results: list[ShortResult] = []
    excluded: int = 0
    failed:   int = 0
    progress_every = max(50, universe_size // 20)

    for i, coin in enumerate(universe, 1):
        base = coin["base"]
        df = data.get_ohlcv(base, "bybit", SCAN["tf"], SCAN["ohlcv_bars"], use_cache=use_cache)
        if df is None or len(df) < 50:
            failed += 1
            continue

        result = score_coin(
            base         = base,
            df           = df,
            btc_closes   = btc_closes,
            funding_rate = coin.get("funding_rate"),
        )
        if result is None:
            failed += 1
            continue

        result.volume_24h    = float(coin.get("_combined_volume", 0.0))
        result.price_24h_pct = float(coin.get("price_24h_pct", 0.0))

        if result.excluded_reason:
            excluded += 1
            continue

        if result.tier != "below":
            results.append(result)

        if i % progress_every == 0:
            log.info(f"  ...{i}/{universe_size}  "
                     f"(surfaced: {len(results)}  excluded: {excluded})")

    results.sort(key=lambda r: (r.conviction, r.signal_count), reverse=True)
    watch_now = [r for r in results if r.tier == "watch_now"][:top_n]
    on_radar  = [r for r in results if r.tier == "on_radar" ][:top_n]

    elapsed_s = time.time() - scan_start
    scanned   = universe_size - failed
    log.info(
        f"Done in {elapsed_s:.0f}s  |  scanned={scanned}  failed={failed}  "
        f"excluded={excluded}  watch_now={len(watch_now)}  on_radar={len(on_radar)}"
    )

    report_text = build_text_report(
        watch_now, on_radar, universe_size, scanned, excluded, elapsed_s, btc_price
    )
    log.info("\n" + report_text)

    payload = build_json_payload(
        watch_now, on_radar, universe_size, scanned, excluded, elapsed_s
    )

    ts_file     = datetime.now().strftime("%Y%m%d_%H%M%S")
    txt_ts      = _OUTPUT_DIR / f"short_v3_{ts_file}.txt"
    txt_latest  = _OUTPUT_DIR / "short_v3_LATEST.txt"
    json_latest = _OUTPUT_DIR / "short_v3_LATEST.json"
    json_ts     = _OUTPUT_DIR / f"short_v3_{ts_file}.json"

    txt_ts.write_text(report_text, encoding="utf-8")
    txt_latest.write_text(report_text, encoding="utf-8")
    json_ts.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    json_latest.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    log.info(f"  Saved → {txt_latest.name}, {json_latest.name}, {txt_ts.name}")

    return payload


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Short Scanner v3.0 — bearish setup watchlist")
    parser.add_argument("--top", type=int, default=50, help="Max entries per tier")
    parser.add_argument("--no-cache", action="store_true", help="Force fresh OHLCV fetch")
    args = parser.parse_args()

    run(top_n=args.top, use_cache=not args.no_cache)
