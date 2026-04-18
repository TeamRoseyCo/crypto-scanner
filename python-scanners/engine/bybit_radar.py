"""
================================================================================
BYBIT RADAR  v1.0
================================================================================
Uses Bybit's free perp API to detect pre-pump positioning signals.

Why Bybit data is LEADING (fires before price moves):
  - Open Interest building = new money entering = accumulation in progress
  - Funding rate extreme negative = shorts over-extended = squeeze likely
  - Funding rate flip from negative → neutral = shorts starting to cover
  - Volume/OI surge = conviction behind the move (not just noise)
  - 1h price decoupling from BTC = token-specific catalyst

Single API call returns ALL tickers — no rate limits, near-instant.

Outputs:
  - outputs/scanner-results/bybit_radar_LATEST.txt   (human report)
  - cache/bybit_oi_state.json                        (OI snapshot for next run)
  - cache/bybit_symbols.json                         (set of Bybit base symbols)

Usage:
  python bybit_radar.py
  python bybit_radar.py --top 30
================================================================================
"""

import os
import sys
import json
import time
import argparse
import logging
import requests

from datetime import datetime
from pathlib import Path

# Ensure demo key regardless of launch method
os.environ.setdefault("CG_DEMO_KEY", "CG-oEG3MATjJ1ShQN3xnkJDcGVS")

# ── Paths ─────────────────────────────────────────────────────────────────────
_ENGINE_DIR   = Path(__file__).resolve().parent
_PYTHON_DIR   = _ENGINE_DIR.parent
_PROJECT_ROOT = _PYTHON_DIR.parent
_CACHE_DIR    = _PROJECT_ROOT / "cache"   / "shared_ohlcv"
_OUTPUT_DIR   = _PROJECT_ROOT / "outputs" / "scanner-results"
_LOG_DIR      = _PROJECT_ROOT / "outputs" / "logs"

for d in (_CACHE_DIR, _OUTPUT_DIR, _LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

_OI_STATE_FILE     = _CACHE_DIR / "bybit_oi_state.json"
_SYMBOLS_FILE      = _CACHE_DIR / "bybit_symbols.json"

# ── Logging ───────────────────────────────────────────────────────────────────
_log_file = _LOG_DIR / f"bybit_radar_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("bybit_radar")

# ── Config ────────────────────────────────────────────────────────────────────
BYBIT_API = "https://api.bybit.com"

THRESHOLDS = {
    # Funding rate thresholds (8h rate, as returned by Bybit)
    "funding_neg_strong":  -0.0003,   # < -0.03%/8h — strong short squeeze risk
    "funding_neg_mild":    -0.0001,   # < -0.01%/8h — mild bearish sentiment
    "funding_pos_extreme":  0.0008,   # > 0.08%/8h  — dangerously crowded long (fade)
    "funding_pos_elevated": 0.0003,   # > 0.03%/8h  — elevated (caution)

    # OI change threshold (vs previous snapshot)
    "oi_build_pct":         0.03,     # OI grew by > 3%  → accumulation
    "oi_unwind_pct":       -0.05,     # OI fell by > 5%  → unwinding

    # Turnover / OI ratio (volume surprise relative to open interest)
    "vol_oi_elevated":      2.0,      # turnover24h / openInterestValue > 2× → surge

    # 1h price move
    "price_1h_pos":         0.01,     # +1%/h → intraday momentum
    "price_1h_neg":        -0.02,     # −2%/h → distribution

    # Minimum open interest value (USD) to be relevant
    "min_oi_value_usd":    500_000,
    "min_turnover_24h":    200_000,
}

# ── Signal weights ────────────────────────────────────────────────────────────
WEIGHTS = {
    "oi_building":       3.0,   # New money entering — highest quality signal
    "oi_spike":          2.0,   # Big OI jump in one period
    "funding_neg":       2.5,   # Shorts paying = squeeze fuel
    "funding_flip":      3.0,   # Shorts starting to cover = timing signal
    "price_1h_pos":      1.5,   # Intraday momentum confirming
    "vol_oi_surge":      2.0,   # Volume surge vs positioning
    "funding_not_high":  1.0,   # Funding not crowded = room to run
}

PENALTY_WEIGHTS = {
    "funding_extreme":  -3.0,   # Dangerously crowded long — fade signal
    "oi_unwind":        -2.0,   # OI falling = conviction leaving
    "price_1h_neg":     -1.5,   # Distribution pressure
}


# ─────────────────────────────────────────────────────────────────────────────
# API CALLS
# ─────────────────────────────────────────────────────────────────────────────

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "crypto-scanner/2.0"})


def fetch_all_tickers() -> list:
    """Fetch all Bybit linear perp tickers in a single call."""
    try:
        r = _SESSION.get(
            f"{BYBIT_API}/v5/market/tickers",
            params={"category": "linear"},
            timeout=15,
        )
        if r.status_code != 200:
            log.error(f"Bybit tickers failed: HTTP {r.status_code}")
            return []
        data = r.json()
        if data.get("retCode") != 0:
            log.error(f"Bybit API error: {data.get('retMsg')}")
            return []
        return data["result"]["list"]
    except Exception as e:
        log.error(f"Error fetching Bybit tickers: {e}")
        return []


def fetch_oi_history(symbol: str, interval: str = "1h", limit: int = 2) -> list:
    """
    Fetch OI history for a symbol (used to detect OI change velocity).
    Returns list of {timestamp, openInterest} dicts, newest first.
    """
    try:
        r = _SESSION.get(
            f"{BYBIT_API}/v5/market/open-interest",
            params={
                "category":     "linear",
                "symbol":       symbol,
                "intervalTime": interval,
                "limit":        limit,
            },
            timeout=10,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        if data.get("retCode") != 0:
            return []
        return data["result"]["list"]  # newest first
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# STATE MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

def _load_oi_state() -> dict:
    """Load previous OI snapshot keyed by symbol."""
    if not _OI_STATE_FILE.exists():
        return {}
    try:
        return json.loads(_OI_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_oi_state(state: dict) -> None:
    try:
        _OI_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"Could not save OI state: {e}")


def _save_symbols(symbols: set) -> None:
    """Save the set of Bybit base symbols for use by other scanners."""
    try:
        _SYMBOLS_FILE.write_text(
            json.dumps({"symbols": sorted(symbols), "updated": datetime.now().isoformat()},
                       indent=2),
            encoding="utf-8",
        )
        log.info(f"  Saved {len(symbols)} Bybit symbols → {_SYMBOLS_FILE.name}")
    except Exception as e:
        log.warning(f"Could not save Bybit symbols: {e}")


def get_bybit_symbols() -> set:
    """
    Return the set of BASE symbols (e.g. 'BTC', 'SOL') listed on Bybit as perp contracts.
    Returns empty set if the symbols file doesn't exist (run bybit_radar.py first).
    """
    if not _SYMBOLS_FILE.exists():
        return set()
    try:
        data = json.loads(_SYMBOLS_FILE.read_text(encoding="utf-8"))
        return set(data.get("symbols", []))
    except Exception:
        return set()


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL SCORING
# ─────────────────────────────────────────────────────────────────────────────

def score_ticker(ticker: dict, prev_oi: float | None, prev_funding: float | None) -> dict:
    """
    Score a single Bybit ticker on pre-pump signals.
    Returns a dict with signals, score, and metadata.
    """
    symbol       = ticker.get("symbol", "")
    base         = symbol.replace("USDT", "").replace("PERP", "")
    last_price   = float(ticker.get("lastPrice",   0) or 0)
    funding_rate = float(ticker.get("fundingRate", 0) or 0)
    oi           = float(ticker.get("openInterest",      0) or 0)
    oi_value     = float(ticker.get("openInterestValue", 0) or 0)
    turnover_24h = float(ticker.get("turnover24h",       0) or 0)
    prev_price_1h = float(ticker.get("prevPrice1h",      0) or 0)

    # Skip dust/illiquid
    if oi_value < THRESHOLDS["min_oi_value_usd"] or turnover_24h < THRESHOLDS["min_turnover_24h"]:
        return {}

    # ── Compute metrics ────────────────────────────────────────────────────────
    price_1h_pct = (last_price - prev_price_1h) / prev_price_1h if prev_price_1h > 0 else 0.0
    vol_oi_ratio = turnover_24h / oi_value if oi_value > 0 else 0.0

    oi_change_pct = 0.0
    if prev_oi and prev_oi > 0:
        oi_change_pct = (oi - prev_oi) / prev_oi

    funding_flip = False
    if prev_funding is not None and prev_funding < THRESHOLDS["funding_neg_mild"]:
        if funding_rate > THRESHOLDS["funding_neg_mild"]:
            funding_flip = True  # Funding moved from negative to neutral/positive

    # ── Signals ───────────────────────────────────────────────────────────────
    active   = []
    score    = 0.0
    warnings = []

    # Bullish signals
    if oi_change_pct >= THRESHOLDS["oi_build_pct"] and oi_value >= THRESHOLDS["min_oi_value_usd"]:
        active.append(f"oi_building(+{oi_change_pct*100:.1f}%)")
        score += WEIGHTS["oi_building"]

    if oi_change_pct >= 0.10 and oi_value >= THRESHOLDS["min_oi_value_usd"]:
        active.append(f"oi_spike(+{oi_change_pct*100:.1f}%)")
        score += WEIGHTS["oi_spike"]

    if funding_rate <= THRESHOLDS["funding_neg_strong"]:
        active.append(f"funding_neg({funding_rate*100:.4f}%)")
        score += WEIGHTS["funding_neg"]
    elif funding_rate <= THRESHOLDS["funding_neg_mild"]:
        active.append(f"funding_mildly_neg({funding_rate*100:.4f}%)")
        score += WEIGHTS["funding_neg"] * 0.5

    if funding_flip:
        active.append("funding_flip(shorts_covering)")
        score += WEIGHTS["funding_flip"]

    if price_1h_pct >= THRESHOLDS["price_1h_pos"]:
        active.append(f"price_1h(+{price_1h_pct*100:.1f}%)")
        score += WEIGHTS["price_1h_pos"]

    if vol_oi_ratio >= THRESHOLDS["vol_oi_elevated"]:
        active.append(f"vol_oi_surge({vol_oi_ratio:.1f}x)")
        score += WEIGHTS["vol_oi_surge"]

    if funding_rate < THRESHOLDS["funding_pos_elevated"]:
        active.append("funding_not_crowded")
        score += WEIGHTS["funding_not_high"]

    # Penalties
    if funding_rate >= THRESHOLDS["funding_pos_extreme"]:
        warnings.append(f"CROWDED_LONG({funding_rate*100:.4f}%/8h)")
        score += PENALTY_WEIGHTS["funding_extreme"]

    if oi_change_pct <= THRESHOLDS["oi_unwind_pct"]:
        warnings.append(f"OI_UNWIND({oi_change_pct*100:.1f}%)")
        score += PENALTY_WEIGHTS["oi_unwind"]

    if price_1h_pct <= THRESHOLDS["price_1h_neg"]:
        warnings.append(f"PRICE_DROPPING({price_1h_pct*100:.1f}%/h)")
        score += PENALTY_WEIGHTS["price_1h_neg"]

    return {
        "symbol":        symbol,
        "base":          base,
        "price":         last_price,
        "funding_rate":  funding_rate,
        "oi":            oi,
        "oi_value":      oi_value,
        "oi_change_pct": oi_change_pct,
        "price_1h_pct":  price_1h_pct,
        "vol_oi_ratio":  vol_oi_ratio,
        "turnover_24h":  turnover_24h,
        "active_signals": active,
        "warnings":      warnings,
        "score":         max(score, 0.0),
        "signal_count":  len(active),
    }


# ─────────────────────────────────────────────────────────────────────────────
# REPORT BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_report(results: list, top_n: int, scan_start: datetime) -> str:
    elapsed = (datetime.now() - scan_start).total_seconds()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        "═" * 72,
        f"  BYBIT RADAR v1.0 — {ts}",
        f"  Scan time: {elapsed:.0f}s  |  Symbols scored: {len(results)}",
        "═" * 72,
        "",
        "  Signals:",
        "    oi_building    — Open Interest growing vs last snapshot (+3%+)",
        "    oi_spike       — OI jumped +10%+ (sharp accumulation)",
        "    funding_neg    — Shorts paying longs (squeeze fuel)",
        "    funding_flip   — Shorts started covering (timing signal)",
        "    price_1h_pos   — Intraday momentum +1%/h",
        "    vol_oi_surge   — Volume ≥ 2× open interest value",
        "    funding_not_crowded — Funding below elevated threshold",
        "",
        "  ⚠️  Penalties: funding_extreme (>0.08%), oi_unwind (<-5%), price_1h_neg",
        "",
        "─" * 72,
    ]

    high_signal = [r for r in results if r["signal_count"] >= 3]
    if not high_signal:
        high_signal = results[:5]

    lines.append(f"  TOP SETUPS  (≥3 signals, ranked by score)")
    lines.append("─" * 72)

    if not high_signal:
        lines.append("  No high-signal setups found this scan.")
    else:
        for i, r in enumerate(high_signal[:top_n], 1):
            fr_pct = r["funding_rate"] * 100
            fr_color = "✅" if r["funding_rate"] <= THRESHOLDS["funding_neg_mild"] else (
                       "⚠️" if r["funding_rate"] >= THRESHOLDS["funding_pos_extreme"] else "  ")
            oi_val_m = r["oi_value"] / 1_000_000
            p1h = r["price_1h_pct"] * 100

            lines.append("")
            lines.append(
                f"  [{i:2d}] {r['base']:<8}  Score: {r['score']:.1f}  |  "
                f"Signals: {r['signal_count']}  |  Price: ${r['price']:,.4f}"
            )
            lines.append(
                f"       Funding: {fr_color} {fr_pct:+.4f}%/8h  |  "
                f"OI: ${oi_val_m:.1f}M  |  "
                f"OI Δ: {r['oi_change_pct']*100:+.1f}%  |  "
                f"1h: {p1h:+.2f}%"
            )
            if r["active_signals"]:
                lines.append(f"       → {', '.join(r['active_signals'])}")
            if r["warnings"]:
                lines.append(f"       ⚠️  {', '.join(r['warnings'])}")

    lines.append("")
    lines.append("─" * 72)
    lines.append("  ALL SCORED (sorted by score)")
    lines.append("─" * 72)
    lines.append(
        f"  {'Symbol':<12} {'Score':>5} {'Sigs':>4}  "
        f"{'Fund%/8h':>10}  {'OI Δ':>8}  {'1h%':>6}  "
        f"{'Vol/OI':>6}  Signals"
    )
    lines.append("  " + "─" * 70)
    for r in results[:30]:
        fr_pct = r["funding_rate"] * 100
        sig_short = ", ".join(s.split("(")[0] for s in r["active_signals"])
        lines.append(
            f"  {r['base']:<12} {r['score']:>5.1f} {r['signal_count']:>4}  "
            f"{fr_pct:>10.4f}  {r['oi_change_pct']*100:>8.1f}  "
            f"{r['price_1h_pct']*100:>6.2f}  "
            f"{r['vol_oi_ratio']:>6.1f}  {sig_short}"
        )

    lines.append("")
    lines.append("═" * 72)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run(top_n: int = 20) -> list:
    scan_start = datetime.now()
    log.info("=" * 64)
    log.info("BYBIT RADAR v1.0")
    log.info("=" * 64)

    # Load previous OI state
    prev_state = _load_oi_state()
    log.info(f"  Previous OI state: {len(prev_state)} symbols loaded")

    # Fetch all tickers (single API call)
    log.info("Fetching all Bybit linear tickers...")
    tickers = fetch_all_tickers()
    log.info(f"  Received {len(tickers)} tickers")

    if not tickers:
        log.error("No tickers returned — aborting.")
        return []

    # Build symbol set (base symbols only, USDT-margined perps)
    usdt_tickers = [t for t in tickers if t.get("symbol", "").endswith("USDT")]
    base_symbols  = {t["symbol"].replace("USDT", "") for t in usdt_tickers}
    _save_symbols(base_symbols)

    # Score each ticker
    results     = []
    new_state   = {}
    prev_funding: dict = prev_state.get("_funding", {})

    for t in usdt_tickers:
        sym      = t.get("symbol", "")
        oi_now   = float(t.get("openInterest", 0) or 0)
        prev_oi  = prev_state.get(sym, {}).get("oi") if isinstance(prev_state.get(sym), dict) else None
        prev_fr  = prev_funding.get(sym)

        scored = score_ticker(t, prev_oi, prev_fr)
        if scored:
            results.append(scored)

        # Save current state for next run
        new_state[sym] = {"oi": oi_now}
        fr = float(t.get("fundingRate", 0) or 0)
        prev_funding[sym] = fr

    new_state["_funding"] = prev_funding
    _save_oi_state(new_state)

    # Sort by score (then signal_count as tiebreaker)
    results.sort(key=lambda r: (r["score"], r["signal_count"]), reverse=True)

    top_score = results[0]["score"] if results else 0.0
    log.info(f"  Scored {len(results)} symbols  |  Top score: {top_score:.1f}")

    # Build and save report
    report_text = build_report(results, top_n, scan_start)
    log.info("\n" + report_text)

    ts_str   = datetime.now().strftime("%Y%m%d_%H%M%S")
    ts_file  = _OUTPUT_DIR / f"bybit_radar_{ts_str}.txt"
    lat_file = _OUTPUT_DIR / "bybit_radar_LATEST.txt"

    ts_file.write_text(report_text, encoding="utf-8")
    lat_file.write_text(report_text, encoding="utf-8")
    log.info(f"  Saved → {lat_file.name}")

    # Send Telegram alert for top setups if configured
    try:
        from alerts import alert_watchlist, is_configured
        if is_configured():
            top_entries = [
                {"symbol": r["base"], "conviction": r["score"] * 10, "trend": "up"}
                for r in results[:5] if r["signal_count"] >= 3
            ]
            if top_entries:
                alert_watchlist("Bybit Radar", top_entries)
    except Exception:
        pass

    return results


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Bybit OI + Funding Radar")
    parser.add_argument("--top", type=int, default=20, help="Top N results to highlight")
    args = parser.parse_args()
    run(top_n=args.top)
