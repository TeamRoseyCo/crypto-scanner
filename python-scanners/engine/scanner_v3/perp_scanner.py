"""
================================================================================
PERP SCANNER  v3.0
================================================================================
Bybit perp positioning radar. Refactor of bybit_radar.py using the v3
foundation — same job, cleaner code, integrated with the rest of the system.

What it answers: "Where is positioning revealing something?"
  - OI building (new money entering)
  - Funding rate skew (squeeze fuel or crowded long)
  - Volume/OI surge (conviction behind the move)
  - 1h price decoupling from BTC (token-specific catalyst)

How it differs from bybit_radar.py:
  - Uses signals.py's perp signals (single source of truth)
  - Uses data.py's Bybit fetchers (shared session, no duplicate API code)
  - Output format aligned with ignition_scanner (LATEST.txt + .json + timestamped)
  - Cache file shared across scanners (cache/shared_ohlcv/)
  - Splits "WATCH NOW" and "ON RADAR" tiers explicitly (was raw score before)
  - Negative signals (oi_unwind, funding_extreme_long) tracked as penalties
    that DEMOTE a coin's tier — protects against fading crowded longs

Run:
  python perp_scanner.py
  python perp_scanner.py --top 30
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

import data
import signals as S


# ─────────────────────────────────────────────────────────────────────────────
# PATHS  — match the project layout used by other scanners
# ─────────────────────────────────────────────────────────────────────────────
_THIS_DIR     = Path(__file__).resolve().parent             # scanner_v3/
_ENGINE_DIR   = _THIS_DIR.parent                             # engine/
_PYTHON_DIR   = _ENGINE_DIR.parent                           # python-scanners/
_PROJECT_ROOT = _PYTHON_DIR.parent                           # crypto-scanner/
_CACHE_DIR    = _PROJECT_ROOT / "cache"   / "shared_ohlcv"
_OUTPUT_DIR   = _PROJECT_ROOT / "outputs" / "scanner-results"
_LOG_DIR      = _PROJECT_ROOT / "outputs" / "logs"
for d in (_CACHE_DIR, _OUTPUT_DIR, _LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Persisted state (so we can compute OI deltas across runs)
_OI_STATE_FILE = _CACHE_DIR / "perp_oi_state.json"
_SYMBOLS_FILE  = _CACHE_DIR / "bybit_symbols.json"


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
log = logging.getLogger("scanner_v3.perp")
if not log.handlers:
    handler_file = logging.FileHandler(
        _LOG_DIR / f"perp_v3_{datetime.now().strftime('%Y%m%d')}.log",
        encoding="utf-8",
    )
    handler_file.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    handler_stdout = logging.StreamHandler(sys.stdout)
    handler_stdout.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(handler_file)
    log.addHandler(handler_stdout)
    log.setLevel(logging.INFO)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

SCAN = {
    "min_oi_value_usd":   500_000,    # OI floor — skip illiquid perps
    "min_turnover_24h":   200_000,    # Volume floor
    "max_coins":            500,      # Cap (Bybit has ~320 perps anyway)
}

# Signal weights — perp signals only. Sum dominates conviction normalization.
SIGNAL_WEIGHTS: dict[str, float] = {
    # Positive (bullish setup)
    "oi_building":          3.0,    # New money entering — strongest tell
    "funding_negative":      2.5,    # Shorts paying longs (squeeze fuel)
    "vol_oi_surge":          2.0,    # Volume conviction
    "btc_decoupling_1h":     1.5,    # Token outperforming BTC short-term
}
# Penalty weights — these REDUCE conviction when they fire
PENALTY_WEIGHTS: dict[str, float] = {
    "funding_extreme_long":  3.0,    # Crowded long — fade signal
    "oi_unwind":             2.0,    # OI falling — conviction leaving
}
TOTAL_WEIGHT = sum(SIGNAL_WEIGHTS.values())   # 9.0

# Per-signal call config
SIGNAL_PARAMS: dict[str, dict] = {
    "oi_building":          {"min_pct": 0.03},        # +3% OI vs prev snapshot
    "oi_unwind":            {"max_pct": -0.05},       # -5% OI vs prev
    "funding_negative":     {"threshold": -0.0001},   # < -0.01% / 8h
    "funding_extreme_long": {"threshold":  0.0008},   # > +0.08% / 8h
    "vol_oi_surge":         {"threshold": 2.0},       # turnover/OI ratio
    "btc_decoupling_1h":    {"min_diff_pct": 1.0},    # 1% over BTC in 1h
}

# Tier thresholds — calibrated to be reachable but selective
TIERS = {
    "watch_now_conviction": 50.0,   # ~50% of max weight
    "watch_now_signals":     3,     # at least 3 positive signals
    "on_radar_conviction":  25.0,
    "on_radar_signals":      2,
}


# ─────────────────────────────────────────────────────────────────────────────
# RESULT TYPE
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PerpResult:
    """Per-coin result with everything the report and downstream layers need."""
    base:            str
    symbol:          str               # e.g. "BTCUSDT"
    price:           float
    funding_rate:    float
    open_interest:   float             # contracts (raw)
    oi_value:        float             # USD value of OI
    oi_change_pct:   float             # vs previous snapshot
    turnover_24h:    float
    vol_oi_ratio:    float             # turnover_24h / oi_value
    price_24h_pct:   float
    price_1h_pct:    float
    btc_1h_pct:      float
    decoupling_1h:   float             # price_1h_pct - btc_1h_pct
    tier:            str                # "watch_now" | "on_radar" | "below"
    conviction:      float
    signal_count:    int
    fired_signals:   list[str]
    penalty_signals: list[str]
    signal_strengths: dict[str, float] = field(default_factory=dict)
    signal_extras:    dict[str, dict]  = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# STATE MANAGEMENT  — persist OI snapshots between runs
# ─────────────────────────────────────────────────────────────────────────────

def _load_oi_state() -> dict:
    if not _OI_STATE_FILE.exists():
        return {}
    try:
        return json.loads(_OI_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_oi_state(state: dict) -> None:
    try:
        _OI_STATE_FILE.write_text(
            json.dumps(state, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning(f"Could not save OI state: {e}")


def _save_bybit_symbols(symbols: set[str]) -> None:
    """Save base symbols listed on Bybit so other scanners can use the set."""
    try:
        _SYMBOLS_FILE.write_text(
            json.dumps(
                {"symbols": sorted(symbols), "updated": datetime.now().isoformat()},
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning(f"Could not save Bybit symbols: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# SCORING
# ─────────────────────────────────────────────────────────────────────────────

def score_ticker(
    ticker:   dict,
    prev_oi:  Optional[float],
    btc_1h_pct: float,
) -> Optional[PerpResult]:
    """
    Score a single Bybit ticker. Returns None if data is insufficient
    (universe filter floors).
    """
    sym       = ticker.get("symbol", "")
    if not sym.endswith("USDT"):
        return None
    base      = sym[:-4]

    # Numeric fields — defensive parsing
    try:
        price        = float(ticker.get("lastPrice")        or 0)
        funding      = float(ticker.get("fundingRate")      or 0)
        oi           = float(ticker.get("openInterest")     or 0)
        oi_value     = float(ticker.get("openInterestValue")or 0)
        turnover_24h = float(ticker.get("turnover24h")      or 0)
        price_24h    = float(ticker.get("price24hPcnt")     or 0) * 100
        price_1h     = float(ticker.get("price1hPcnt")      or 0) * 100
    except (ValueError, TypeError):
        return None

    if oi_value < SCAN["min_oi_value_usd"]:
        return None
    if turnover_24h < SCAN["min_turnover_24h"]:
        return None
    if price <= 0:
        return None

    # OI change vs previous snapshot
    if isinstance(prev_oi, (int, float)) and prev_oi > 0:
        oi_change_pct = (oi - prev_oi) / prev_oi
    else:
        oi_change_pct = 0.0   # No prior snapshot → no signal possible

    vol_oi_ratio  = turnover_24h / oi_value if oi_value > 0 else 0.0
    decoupling_1h = price_1h - btc_1h_pct

    # ── Run signals ─────────────────────────────────────────────────────────
    fired_signals:   list[str] = []
    penalty_signals: list[str] = []
    strengths: dict[str, float] = {}
    extras:    dict[str, dict]  = {}
    earned_weight: float = 0.0
    penalty_weight: float = 0.0

    def record_positive(name: str, res: S.SignalResult) -> None:
        nonlocal earned_weight
        if res.fired:
            fired_signals.append(name)
            strengths[name] = round(res.strength, 3)
            if res.extras: extras[name] = res.extras
            w = SIGNAL_WEIGHTS.get(name, 0.0)
            earned_weight += w * (0.5 + 0.5 * res.strength)

    def record_penalty(name: str, res: S.SignalResult) -> None:
        nonlocal penalty_weight
        if res.fired:
            penalty_signals.append(name)
            strengths[name] = round(res.strength, 3)
            if res.extras: extras[name] = res.extras
            w = PENALTY_WEIGHTS.get(name, 0.0)
            penalty_weight += w * (0.5 + 0.5 * res.strength)

    # Positive signals (need OI history for OI building)
    if prev_oi is not None:
        record_positive(
            "oi_building",
            S.sig_oi_building(oi, prev_oi, **SIGNAL_PARAMS["oi_building"]),
        )
        record_penalty(
            "oi_unwind",
            S.sig_oi_unwind(oi, prev_oi, **SIGNAL_PARAMS["oi_unwind"]),
        )

    record_positive(
        "funding_negative",
        S.sig_funding_negative(funding, **SIGNAL_PARAMS["funding_negative"]),
    )
    record_penalty(
        "funding_extreme_long",
        S.sig_funding_extreme_long(funding, **SIGNAL_PARAMS["funding_extreme_long"]),
    )
    record_positive(
        "vol_oi_surge",
        S.sig_vol_oi_surge(turnover_24h, oi_value, **SIGNAL_PARAMS["vol_oi_surge"]),
    )

    # 1h decoupling — custom (not a generic signals.py function since it's
    # specific to having a BTC reference)
    decoupling_min = SIGNAL_PARAMS["btc_decoupling_1h"]["min_diff_pct"]
    if decoupling_1h >= decoupling_min:
        fired_signals.append("btc_decoupling_1h")
        # Strength: 1% diff = 0.0, 5% = 1.0
        strength = float(min(max((decoupling_1h - decoupling_min) / 4.0, 0.0), 1.0))
        strengths["btc_decoupling_1h"] = round(strength, 3)
        w = SIGNAL_WEIGHTS["btc_decoupling_1h"]
        earned_weight += w * (0.5 + 0.5 * strength)

    # ── Conviction & tier ───────────────────────────────────────────────────
    # Penalties subtract, but don't allow conviction to go negative
    net_weight    = earned_weight - penalty_weight
    conviction    = round(max((net_weight / TOTAL_WEIGHT) * 100, 0.0), 1)
    signal_count  = len(fired_signals)

    # Tier: penalties demote one level
    has_penalty = len(penalty_signals) > 0
    if has_penalty:
        tier = "below"     # any penalty firing → don't surface
    elif conviction >= TIERS["watch_now_conviction"] and signal_count >= TIERS["watch_now_signals"]:
        tier = "watch_now"
    elif conviction >= TIERS["on_radar_conviction"] and signal_count >= TIERS["on_radar_signals"]:
        tier = "on_radar"
    else:
        tier = "below"

    return PerpResult(
        base             = base,
        symbol           = sym,
        price            = price,
        funding_rate     = funding,
        open_interest    = oi,
        oi_value         = oi_value,
        oi_change_pct    = oi_change_pct,
        turnover_24h     = turnover_24h,
        vol_oi_ratio     = vol_oi_ratio,
        price_24h_pct    = price_24h,
        price_1h_pct     = price_1h,
        btc_1h_pct       = btc_1h_pct,
        decoupling_1h    = decoupling_1h,
        tier             = tier,
        conviction       = conviction,
        signal_count     = signal_count,
        fired_signals    = fired_signals,
        penalty_signals  = penalty_signals,
        signal_strengths = strengths,
        signal_extras    = extras,
    )


# ─────────────────────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────────────────────

def _signal_label(name: str) -> str:
    return {
        "oi_building":          "OI building",
        "oi_unwind":            "OI unwind",
        "funding_negative":     "funding neg",
        "funding_extreme_long": "CROWDED LONG",
        "vol_oi_surge":         "vol/OI surge",
        "btc_decoupling_1h":    "decoupling 1h",
    }.get(name, name)


def build_text_report(
    watch_now:    list[PerpResult],
    on_radar:     list[PerpResult],
    universe_size: int,
    scanned:      int,
    elapsed_s:    float,
    btc_1h_pct:   float,
    no_state:     bool,
) -> str:
    sep  = "=" * 80
    dash = "-" * 80
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        sep,
        "  PERP SCANNER v3.0  —  Bybit positioning radar",
        f"  Generated  : {ts}",
        f"  Universe   : {universe_size} Bybit perps  |  Scanned: {scanned}",
        f"  Scan time  : {elapsed_s:.1f}s  |  BTC 1h: {btc_1h_pct:+.2f}%",
        sep,
        "",
        f"  Tiers — WATCH NOW: conviction ≥ {TIERS['watch_now_conviction']:.0f},"
        f" positive signals ≥ {TIERS['watch_now_signals']}, no penalties",
        f"          ON RADAR : conviction ≥ {TIERS['on_radar_conviction']:.0f},"
        f" positive signals ≥ {TIERS['on_radar_signals']}, no penalties",
        "",
        "  Signals : OI building · funding neg · vol/OI surge · decoupling 1h",
        "  Penalty : OI unwind · CROWDED LONG (extreme positive funding)",
        "",
    ]

    if no_state:
        lines.append("  ⚠️  No prior OI snapshot found — OI signals disabled this scan.")
        lines.append("      Run again in 1+ hours and OI building/unwind will activate.")
        lines.append("")

    lines.append(dash)
    lines.append(f"  WATCH NOW  —  {len(watch_now)} coin(s)")
    lines.append(dash)

    if not watch_now:
        lines.append("  (none)")
    else:
        for i, r in enumerate(watch_now, 1):
            sigs = ", ".join(_signal_label(s) for s in r.fired_signals)
            lines.append("")
            lines.append(
                f"  [{i:>2}] {r.base:<10}  conv={r.conviction:>5.1f}  "
                f"sigs={r.signal_count:>2}  "
                f"price=${r.price:<12,.6f}  "
                f"24h={r.price_24h_pct:+6.2f}%"
            )
            lines.append(
                f"       OI=${r.oi_value/1e6:>6.1f}M  Δ={r.oi_change_pct*100:+6.2f}%  "
                f"funding={r.funding_rate*100:+.4f}%/8h  "
                f"vol/OI={r.vol_oi_ratio:>4.1f}x  "
                f"1h={r.price_1h_pct:+5.2f}% (vs BTC {r.decoupling_1h:+5.2f}%)"
            )
            lines.append(f"       → {sigs}")

    lines.append("")
    lines.append(dash)
    lines.append(f"  ON RADAR   —  {len(on_radar)} coin(s)")
    lines.append(dash)
    lines.append(
        f"  {'#':<3} {'Symbol':<10} {'Conv':>5} {'Sigs':>4}  "
        f"{'OI $M':>7} {'OI Δ':>7} {'Fund%':>9} {'V/OI':>5}  Signals"
    )
    lines.append("  " + "-" * 78)
    if not on_radar:
        lines.append("  (none)")
    else:
        for i, r in enumerate(on_radar, 1):
            sigs_short = ", ".join(_signal_label(s) for s in r.fired_signals[:4])
            if len(r.fired_signals) > 4:
                sigs_short += f", +{len(r.fired_signals)-4}"
            lines.append(
                f"  {i:>3} {r.base:<10} {r.conviction:>5.1f} {r.signal_count:>4}  "
                f"${r.oi_value/1e6:>6.1f}  {r.oi_change_pct*100:>+6.2f}%  "
                f"{r.funding_rate*100:>+8.4f}%  {r.vol_oi_ratio:>4.1f}  {sigs_short}"
            )

    lines.append("")
    lines.append(sep)
    return "\n".join(lines)


def build_json_payload(
    watch_now:    list[PerpResult],
    on_radar:     list[PerpResult],
    universe_size: int,
    scanned:      int,
    elapsed_s:    float,
    btc_1h_pct:   float,
) -> dict:
    return {
        "scanner":       "perp_scanner",
        "version":       "3.0",
        "generated_at":  datetime.now().isoformat(),
        "elapsed_s":     round(elapsed_s, 2),
        "universe_size": universe_size,
        "scanned":       scanned,
        "btc_1h_pct":    round(btc_1h_pct, 2),
        "thresholds":    TIERS,
        "weights":       SIGNAL_WEIGHTS,
        "penalty_weights": PENALTY_WEIGHTS,
        "watch_now":     [r.to_dict() for r in watch_now],
        "on_radar":      [r.to_dict() for r in on_radar],
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ORCHESTRATION
# ─────────────────────────────────────────────────────────────────────────────

def run(top_n: int = 50) -> dict:
    scan_start = time.time()
    log.info("=" * 64)
    log.info("PERP SCANNER v3.0")
    log.info("=" * 64)

    # ── Load prior OI state ─────────────────────────────────────────────────
    prev_state = _load_oi_state()
    log.info(f"  Previous OI state: {len(prev_state)} symbols loaded")
    no_state = (len(prev_state) == 0)

    # ── Fetch all Bybit tickers (single API call) ───────────────────────────
    log.info("Fetching Bybit tickers...")
    tickers = data.get_bybit_tickers()
    log.info(f"  Received {len(tickers)} tickers")

    if not tickers:
        log.error("No tickers — aborting.")
        return {}

    # ── BTC 1h move (for decoupling signal) ─────────────────────────────────
    btc_df = data.get_btc("1h", 5)    # only need 2 bars but fetch a few for safety
    if btc_df is None or len(btc_df) < 2:
        log.warning("BTC 1h reference unavailable — decoupling signal disabled")
        btc_1h_pct = 0.0
    else:
        # 1h pct = (latest - prev) / prev
        prev_close = float(btc_df["close"].iloc[-2])
        last_close = float(btc_df["close"].iloc[-1])
        btc_1h_pct = (last_close - prev_close) / prev_close * 100 if prev_close > 0 else 0.0
    log.info(f"  BTC 1h: {btc_1h_pct:+.2f}%")

    # ── Score every ticker ──────────────────────────────────────────────────
    log.info("Scoring tickers...")
    results: list[PerpResult] = []
    new_state: dict[str, float] = {}
    base_symbols: set[str] = set()
    skipped = 0

    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            skipped += 1
            continue

        prev_oi  = prev_state.get(sym)
        # prev_state values may be {"oi": X} (legacy) or just X — handle both
        if isinstance(prev_oi, dict):
            prev_oi = prev_oi.get("oi")

        result = score_ticker(t, prev_oi, btc_1h_pct)
        if result is None:
            skipped += 1
        else:
            base_symbols.add(result.base)
            try:
                cur_oi = float(t.get("openInterest") or 0)
                new_state[sym] = cur_oi
            except (ValueError, TypeError):
                pass
            if result.tier != "below":
                results.append(result)

    # ── Persist state for next run ──────────────────────────────────────────
    _save_oi_state(new_state)
    _save_bybit_symbols(base_symbols)

    # ── Sort & split into tiers ─────────────────────────────────────────────
    results.sort(key=lambda r: (r.conviction, r.signal_count), reverse=True)
    watch_now = [r for r in results if r.tier == "watch_now"][:top_n]
    on_radar  = [r for r in results if r.tier == "on_radar" ][:top_n]

    elapsed_s = time.time() - scan_start
    scanned   = len(tickers) - skipped
    log.info(
        f"Done in {elapsed_s:.1f}s  |  scanned={scanned}  skipped={skipped}  "
        f"watch_now={len(watch_now)}  on_radar={len(on_radar)}"
    )

    # ── Reports ─────────────────────────────────────────────────────────────
    report_text = build_text_report(
        watch_now, on_radar, len(tickers), scanned, elapsed_s, btc_1h_pct, no_state
    )
    log.info("\n" + report_text)

    payload = build_json_payload(
        watch_now, on_radar, len(tickers), scanned, elapsed_s, btc_1h_pct
    )

    # ── Write outputs ───────────────────────────────────────────────────────
    ts_file     = datetime.now().strftime("%Y%m%d_%H%M%S")
    txt_ts      = _OUTPUT_DIR / f"perp_v3_{ts_file}.txt"
    txt_latest  = _OUTPUT_DIR / "perp_v3_LATEST.txt"
    json_latest = _OUTPUT_DIR / "perp_v3_LATEST.json"
    json_ts     = _OUTPUT_DIR / f"perp_v3_{ts_file}.json"

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

    parser = argparse.ArgumentParser(description="Perp Scanner v3.0 — Bybit positioning radar")
    parser.add_argument("--top", type=int, default=50, help="Max entries per tier")
    args = parser.parse_args()

    run(top_n=args.top)
