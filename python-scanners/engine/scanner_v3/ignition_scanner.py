"""
================================================================================
IGNITION SCANNER  v3.0
================================================================================
Early-warning watchlist. Replaces ignition_radar.py + prepump_radar.py.

What it answers: "What's brewing?"
  Surfaces coins showing accumulation / coiling / divergence signals BEFORE
  they've moved much. This is the alpha tier — confirmation comes later in
  trend_scanner (Phase 4) and orchestrator confluence (Phase 5).

How it differs from the legacy scanners it replaces:
  - Single universe (Bybit perps + Binance spot, ~600 coins) — one fetch path
  - All signals from the canonical signals.py (TTM squeeze, RSI(7) divergence,
    pivot-based OBV divergence, etc.) — no more 4 different definitions of
    BB squeeze across files
  - Cleaner two-tier output: WATCH NOW (≥50 conviction, ≥4 sigs) and
    ON RADAR (≥30, ≥3 sigs)
  - Writes LATEST.txt + timestamped TXT + LATEST.json (machine-readable for
    the orchestrator in Phase 5)
  - Does NOT produce trade plans (that's trend_scanner's job)
  - Does NOT apply regime gating (that's the orchestrator's job — TPI hook)

Run:
  python ignition_scanner.py
  python ignition_scanner.py --top 30
  python ignition_scanner.py --no-cache    # force fresh fetch
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

# ─── Phase 1 foundation ──────────────────────────────────────────────────────
import data
import signals as S


# ─────────────────────────────────────────────────────────────────────────────
# PATHS  (consistent with existing scanner conventions)
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
log = logging.getLogger("scanner_v3.ignition")
if not log.handlers:
    handler_file = logging.FileHandler(
        _LOG_DIR / f"ignition_v3_{datetime.now().strftime('%Y%m%d')}.log",
        encoding="utf-8",
    )
    handler_file.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    handler_stdout = logging.StreamHandler(sys.stdout)
    handler_stdout.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(handler_file)
    log.addHandler(handler_stdout)
    log.setLevel(logging.INFO)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION  — single source of truth, no magic numbers below this line
# ─────────────────────────────────────────────────────────────────────────────

# Universe filter floors — match data.py defaults but allow override here
SCAN = {
    "tf":              "1h",
    "ohlcv_bars":       200,    # ~8 days on 1h
    "max_coins":         700,    # cap to avoid pathological runs
    "min_volume_24h": 200_000,   # combined floor
    "min_price":      1e-7,      # reject true-dust tokens
    "max_24h_change":   75.0,    # skip tokens already pumping >75%/24h
    "min_24h_change":  -50.0,    # skip free-falling tokens
}

# Tier thresholds — calibrated against real-data observation.
# Initial values 50/30 were too punitive given the strength-weighted scoring
# (max realistic conviction ~70 with 8 signals firing strongly). Lowering to
# 40/25 brings WATCH NOW back into reach for genuine setups.
TIERS = {
    "watch_now_conviction": 40.0,
    "watch_now_signals":     5,        # bumped 4→5: with looser conv,
                                       # require more signals to compensate
    "on_radar_conviction":  25.0,
    "on_radar_signals":      3,
}

# Signal weights (per spec). Sum dominates the conviction normalization.
SIGNAL_WEIGHTS: dict[str, float] = {
    # Core (11) ─────────────────────────────────────────────
    "whale_candle":      2.5,   # large bullish bar in last N
    "obv_divergence":    2.5,   # rare and high-quality
    "rsi_divergence":    2.5,   # leading signal (RSI(7))
    "bb_squeeze":        2.0,   # TTM compression coil (or deep alone)
    "obv_stealth_accum": 2.0,   # smart-money tell
    "cmf_positive":      1.5,   # institutional buying confirmed
    "vol_expansion":     1.5,   # fresh capital arriving
    "higher_lows":       1.5,   # base-building structure
    "rsi_reset":         1.5,   # bottoming pattern
    "rsi_in_zone":       1.0,   # currently in healthy RSI(7) zone
    "vol_in_window":     1.0,   # building but not pumping
    # Bonus (3) — fire when present, do not gate ──────────────
    "btc_decoupling":    1.0,
    "funding_negative":  1.0,
    "price_range_break": 1.0,
}
TOTAL_WEIGHT = sum(SIGNAL_WEIGHTS.values())   # ~22

# Per-signal call config — pass through to signals.py
# (where we deviate from defaults, document why inline)
SIGNAL_PARAMS: dict[str, dict] = {
    "bb_squeeze":        {"width_lookback": 120, "width_pct": 20.0},
    "rsi_divergence":    {"rsi_period": 7, "lookback": 60},
    "rsi_reset":         {"rsi_period": 7, "lookback": 24, "low_thresh": 42.0},
    "rsi_in_zone":       {"rsi_period": 7, "low": 25.0, "high": 62.0},
    "vol_expansion":     {"recent": 6, "base_start": 7, "base_end": 42, "mult": 1.5},
    "vol_in_window":     {"recent": 6, "base_back": 30, "low_mult": 1.2, "high_mult": 4.5},
    "whale_candle":      {"lookback": 6, "atr_mult": 1.8, "close_upper_pct": 0.30},
    "obv_stealth_accum": {"obv_lookback": 12, "min_obv_pct": 0.015, "max_price_move": 0.03},
    "obv_divergence":    {"lookback": 40, "pivot_left": 3, "pivot_right": 3},
    "cmf_positive":      {"period": 20, "threshold": 0.05},
    "higher_lows":       {"window": 30, "pivot_left": 3, "pivot_right": 3},
    "btc_decoupling":    {"window": 6,  "min_decoupling": 0.015},   # 1.5% in 6h
    "funding_negative":  {"threshold": -0.0001},                     # -0.01%
    "price_range_break": {"lookback": 120},
}


# ─────────────────────────────────────────────────────────────────────────────
# RESULT TYPE
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class IgnitionResult:
    """Per-coin result with everything the report and downstream layers need."""
    base:           str
    price:          float
    volume_24h:     float
    price_24h_pct:  float
    sources:        list[str]                                  # ["bybit","binance"]
    tier:           str                                        # "watch_now"|"on_radar"|"below"
    conviction:     float
    signal_count:   int
    fired_signals:  list[str]                                  # names that fired
    signal_strengths: dict[str, float] = field(default_factory=dict)
    signal_extras:    dict[str, dict]   = field(default_factory=dict)
    funding_rate:   Optional[float]  = None
    rs_btc_6h_pct:  Optional[float]  = None      # bonus: how much vs BTC over 6h
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
) -> Optional[IgnitionResult]:
    """
    Run all ignition signals on `df` (1h OHLCV for `base`).
    Returns IgnitionResult with conviction and tier, or None if data is too thin.
    """
    if df is None or len(df) < 50:
        return None

    fired: list[str]          = []
    strengths: dict[str, float] = {}
    extras: dict[str, dict]   = {}
    earned_weight: float      = 0.0

    # Helper to record one signal's outcome ───────────────────────────────────
    def record(name: str, res: S.SignalResult) -> None:
        nonlocal earned_weight
        if res.fired:
            fired.append(name)
            strengths[name] = round(res.strength, 3)
            if res.extras:
                extras[name] = res.extras
            # Weight contribution scaled by strength (so a weak fire counts less
            # than a strong one). Keeps confluence honest.
            w = SIGNAL_WEIGHTS.get(name, 0.0)
            earned_weight += w * (0.5 + 0.5 * res.strength)
            #   ^ floor at half-weight on a fired signal so a fire is still
            #     meaningful even at strength=0; full weight at strength=1.

    # ── Core signals ────────────────────────────────────────────────────────
    record("bb_squeeze",        S.sig_bb_squeeze       (df, **SIGNAL_PARAMS["bb_squeeze"]))
    record("vol_in_window",     S.sig_vol_in_window    (df, **SIGNAL_PARAMS["vol_in_window"]))
    record("vol_expansion",     S.sig_vol_expansion    (df, **SIGNAL_PARAMS["vol_expansion"]))
    record("whale_candle",      S.sig_whale_candle     (df, **SIGNAL_PARAMS["whale_candle"]))
    record("obv_stealth_accum", S.sig_obv_stealth_accum(df, **SIGNAL_PARAMS["obv_stealth_accum"]))
    record("obv_divergence",    S.sig_obv_divergence   (df, **SIGNAL_PARAMS["obv_divergence"]))
    record("rsi_divergence",    S.sig_rsi_divergence   (df, **SIGNAL_PARAMS["rsi_divergence"]))
    record("rsi_reset",         S.sig_rsi_reset        (df, **SIGNAL_PARAMS["rsi_reset"]))
    record("rsi_in_zone",       S.sig_rsi_in_zone      (df, **SIGNAL_PARAMS["rsi_in_zone"]))
    record("cmf_positive",      S.sig_cmf_positive     (df, **SIGNAL_PARAMS["cmf_positive"]))
    record("higher_lows",       S.sig_higher_lows      (df, **SIGNAL_PARAMS["higher_lows"]))

    # ── Bonus 1: BTC decoupling (only if BTC data available) ─────────────────
    rs_btc_6h_pct: Optional[float] = None
    if btc_closes is not None and len(btc_closes) >= 8:
        res = S.sig_btc_decoupling(df["close"], btc_closes, **SIGNAL_PARAMS["btc_decoupling"])
        record("btc_decoupling", res)
        if res.value is not None:
            rs_btc_6h_pct = round(res.value * 100, 2)

    # ── Bonus 2: funding negative (only if perp data) ───────────────────────
    if funding_rate is not None:
        record("funding_negative", S.sig_funding_negative(funding_rate, **SIGNAL_PARAMS["funding_negative"]))

    # ── Bonus 3: price range break ──────────────────────────────────────────
    record("price_range_break", S.sig_price_range_break(df, **SIGNAL_PARAMS["price_range_break"]))

    # ── Conviction & tier ───────────────────────────────────────────────────
    conviction   = round((earned_weight / TOTAL_WEIGHT) * 100, 1)
    signal_count = len(fired)

    if conviction >= TIERS["watch_now_conviction"] and signal_count >= TIERS["watch_now_signals"]:
        tier = "watch_now"
    elif conviction >= TIERS["on_radar_conviction"] and signal_count >= TIERS["on_radar_signals"]:
        tier = "on_radar"
    else:
        tier = "below"

    return IgnitionResult(
        base             = base,
        price            = float(df["close"].iloc[-1]),
        volume_24h       = 0.0,                                # filled in by caller
        price_24h_pct    = 0.0,                                # filled in by caller
        sources          = [],                                 # filled in by caller
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
# UNIVERSE BUILDING
# ─────────────────────────────────────────────────────────────────────────────

def build_universe() -> list[dict]:
    """
    Build the deduplicated, filtered scanning universe.
    Combines Bybit + Binance, applies SCAN floors, sorts by max(turnover, volume).
    """
    universe = data.get_universe("both")
    filtered = []
    for c in universe:
        # max liquidity across venues
        vol = max(c.get("volume_24h", 0.0), c.get("turnover_24h", 0.0))
        if vol < SCAN["min_volume_24h"]:
            continue
        if c["price"] < SCAN["min_price"]:
            continue
        chg = c.get("price_24h_pct", 0.0)
        if chg < SCAN["min_24h_change"] or chg > SCAN["max_24h_change"]:
            continue
        c["_combined_volume"] = vol
        filtered.append(c)

    filtered.sort(key=lambda x: x["_combined_volume"], reverse=True)
    return filtered[:SCAN["max_coins"]]


def _pick_source(coin: dict) -> Optional[str]:
    """
    Decide which venue to fetch OHLCV from for a given coin.
    Prefer Bybit (perp, includes funding/OI for downstream perp_scanner);
    fall back to Binance for spot-only coins.
    """
    if coin.get("on_bybit"):
        return "bybit"
    if coin.get("on_binance"):
        return "binance"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# REPORT BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def _signal_label(name: str) -> str:
    """Pretty-print signal name for the report."""
    return {
        "bb_squeeze":        "TTM squeeze",
        "vol_in_window":     "vol building",
        "vol_expansion":     "vol expansion",
        "whale_candle":      "whale candle",
        "obv_stealth_accum": "stealth accum",
        "obv_divergence":    "OBV divergence",
        "rsi_divergence":    "RSI divergence",
        "rsi_reset":         "RSI reset",
        "rsi_in_zone":       "RSI in zone",
        "cmf_positive":      "CMF buying",
        "higher_lows":       "higher lows",
        "btc_decoupling":    "decoupling BTC",
        "funding_negative":  "funding neg",
        "price_range_break": "5d high",
    }.get(name, name)


def build_text_report(
    watch_now:    list[IgnitionResult],
    on_radar:     list[IgnitionResult],
    universe_size: int,
    scanned:      int,
    elapsed_s:    float,
    btc_price:    Optional[float],
) -> str:
    sep  = "=" * 80
    dash = "-" * 80
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        sep,
        "  IGNITION SCANNER v3.0  —  early warning watchlist",
        f"  Generated : {ts}",
        f"  Universe  : {universe_size} bases (Bybit + Binance)  |  Scanned: {scanned}",
        f"  Scan time : {elapsed_s:.0f}s"
        + (f"  |  BTC: ${btc_price:,.0f}" if btc_price else ""),
        sep,
        "",
        f"  Tiers — WATCH NOW: conviction ≥ {TIERS['watch_now_conviction']:.0f},"
        f" signals ≥ {TIERS['watch_now_signals']}",
        f"          ON RADAR : conviction ≥ {TIERS['on_radar_conviction']:.0f},"
        f" signals ≥ {TIERS['on_radar_signals']}",
        "",
        "  This scanner does NOT produce trade plans — it surfaces what's brewing.",
        "  Confirm with trend_scanner (Phase 4) before entry.",
        "",
        dash,
    ]

    # ── WATCH NOW ────────────────────────────────────────────────────────────
    lines.append(f"  WATCH NOW  —  {len(watch_now)} coin(s)")
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
            lines.append(f"       sources: {', '.join(r.sources)}"
                         + (f"   {extra_str}" if extra_str else ""))
            lines.append(f"       → {sigs}")

    # ── ON RADAR ────────────────────────────────────────────────────────────
    lines.append("")
    lines.append(dash)
    lines.append(f"  ON RADAR   —  {len(on_radar)} coin(s)")
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
    watch_now:    list[IgnitionResult],
    on_radar:     list[IgnitionResult],
    universe_size: int,
    scanned:      int,
    elapsed_s:    float,
) -> dict:
    """Machine-readable output for the orchestrator (Phase 5) to consume."""
    return {
        "scanner":       "ignition_scanner",
        "version":       "3.0",
        "generated_at":  datetime.now().isoformat(),
        "elapsed_s":     round(elapsed_s, 2),
        "universe_size": universe_size,
        "scanned":       scanned,
        "thresholds":    TIERS,
        "weights":       SIGNAL_WEIGHTS,
        "watch_now":     [r.to_dict() for r in watch_now],
        "on_radar":      [r.to_dict() for r in on_radar],
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ORCHESTRATION
# ─────────────────────────────────────────────────────────────────────────────

def run(top_n: int = 50, use_cache: bool = True) -> dict:
    scan_start = time.time()
    log.info("=" * 64)
    log.info("IGNITION SCANNER v3.0")
    log.info("=" * 64)

    # ── Universe ────────────────────────────────────────────────────────────
    log.info("Building universe...")
    universe = build_universe()
    universe_size = len(universe)
    log.info(f"  Universe (post-filter): {universe_size} coins")

    if not universe:
        log.error("No coins passed filters — aborting")
        return {}

    # ── BTC reference (for decoupling bonus signal) ─────────────────────────
    log.info("Fetching BTC reference (1h)...")
    btc_df  = data.get_btc(SCAN["tf"], SCAN["ohlcv_bars"])
    btc_closes = btc_df["close"] if btc_df is not None else None
    btc_price  = float(btc_df["close"].iloc[-1]) if btc_df is not None else None
    if btc_closes is None:
        log.warning("  BTC reference unavailable — btc_decoupling bonus disabled")

    # ── Per-coin scan ───────────────────────────────────────────────────────
    log.info(f"Scanning {universe_size} coins on {SCAN['tf']} timeframe...")
    results: list[IgnitionResult] = []
    failed:  int = 0
    skipped: int = 0
    progress_every = max(50, universe_size // 20)

    for i, coin in enumerate(universe, 1):
        base   = coin["base"]
        source = _pick_source(coin)
        if source is None:
            skipped += 1
            continue

        df = data.get_ohlcv(base, source, SCAN["tf"], SCAN["ohlcv_bars"], use_cache=use_cache)
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

        # Stamp metadata that score_coin couldn't see
        result.volume_24h    = float(coin.get("_combined_volume", 0.0))
        result.price_24h_pct = float(coin.get("price_24h_pct", 0.0))
        result.sources       = [
            s for s, key in [("bybit","on_bybit"),("binance","on_binance")]
            if coin.get(key)
        ]

        if result.tier != "below":
            results.append(result)

        if i % progress_every == 0:
            log.info(f"  ...{i}/{universe_size}  "
                     f"(surfaced so far: {len(results)})")

    # ── Sort & split into tiers ─────────────────────────────────────────────
    results.sort(key=lambda r: (r.conviction, r.signal_count), reverse=True)
    watch_now = [r for r in results if r.tier == "watch_now"][:top_n]
    on_radar  = [r for r in results if r.tier == "on_radar" ][:top_n]

    elapsed_s = time.time() - scan_start
    scanned   = universe_size - skipped - failed
    log.info(
        f"Done in {elapsed_s:.0f}s  |  scanned={scanned}  failed={failed}  "
        f"skipped={skipped}  watch_now={len(watch_now)}  on_radar={len(on_radar)}"
    )

    # ── Reports ─────────────────────────────────────────────────────────────
    report_text = build_text_report(
        watch_now, on_radar, universe_size, scanned, elapsed_s, btc_price
    )
    log.info("\n" + report_text)

    payload = build_json_payload(
        watch_now, on_radar, universe_size, scanned, elapsed_s
    )

    # ── Write outputs ───────────────────────────────────────────────────────
    ts_file   = datetime.now().strftime("%Y%m%d_%H%M%S")
    txt_ts    = _OUTPUT_DIR / f"ignition_v3_{ts_file}.txt"
    txt_latest = _OUTPUT_DIR / "ignition_v3_LATEST.txt"
    json_latest = _OUTPUT_DIR / "ignition_v3_LATEST.json"
    json_ts     = _OUTPUT_DIR / f"ignition_v3_{ts_file}.json"

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

    parser = argparse.ArgumentParser(description="Ignition Scanner v3.0 — early warning watchlist")
    parser.add_argument("--top", type=int, default=50, help="Max entries per tier")
    parser.add_argument("--no-cache", action="store_true", help="Force fresh OHLCV fetch")
    args = parser.parse_args()

    run(top_n=args.top, use_cache=not args.no_cache)
