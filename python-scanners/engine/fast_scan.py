"""
================================================================================
FAST SCAN  v1.0
================================================================================
30-minute lightweight scan — NO OHLCV fetches, pure market data.

Speed: 2–4 CoinGecko API calls + 1 Bybit call = ~15 seconds total.
Purpose: catch momentum shifts BETWEEN full master scans.

Scoring (each signal = 1 point):
  Momentum:
    7d_outperform_btc    — 7d change > BTC 7d + 5%
    24h_positive         — 24h change > +1%
    24h_outperform_btc   — 24h change > BTC 24h + 2%
    7d_positive          — 7d change > 0 (above water)

  Relative Strength:
    rs_vs_btc_strong     — 7d outperformance > 10%
    rs_vs_eth_strong     — 7d outperformance vs ETH > 8%
    rs_accel             — 24h RS > 7d RS baseline (accelerating)

  Volume proxy:
    vol_rank_ok          — volume rank in top half of scanned universe

  Bybit signals (from bybit_radar state):
    bybit_funding_neg    — funding rate < -0.01% (squeeze fuel)
    bybit_oi_building    — OI grew since last bybit_radar run

Outputs:
  - outputs/scanner-results/fast_scan_LATEST.txt

Usage:
  python fast_scan.py
  python fast_scan.py --top 15 --min-signals 3
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

_OI_STATE_FILE = _CACHE_DIR / "bybit_oi_state.json"

# ── Logging ───────────────────────────────────────────────────────────────────
_log_file = _LOG_DIR / f"fast_scan_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("fast_scan")

# ── Config ────────────────────────────────────────────────────────────────────
_CG_PRO_KEY  = os.environ.get("CG_API_KEY", "")
_CG_DEMO_KEY = os.environ.get("CG_DEMO_KEY", "")

COINGECKO_API = (
    "https://pro-api.coingecko.com/api/v3" if _CG_PRO_KEY
    else "https://api.coingecko.com/api/v3"
)

_CG_SESSION = requests.Session()
if _CG_PRO_KEY:
    _CG_SESSION.headers.update({"x-cg-pro-api-key": _CG_PRO_KEY})
elif _CG_DEMO_KEY:
    _CG_SESSION.headers.update({"x-cg-demo-api-key": _CG_DEMO_KEY})

_BYBIT_SESSION = requests.Session()
_BYBIT_SESSION.headers.update({"User-Agent": "crypto-scanner/2.0"})

SCAN = {
    "top_n_coins":     500,
    "min_rank":         20,
    "max_rank":        400,
    "min_volume_24h": 1_000_000,
    "api_delay_s":    4.5 if _CG_DEMO_KEY else (1.2 if _CG_PRO_KEY else 6.5),
}

THRESHOLDS = {
    "rs_vs_btc_strong":  10.0,   # Token outperforms BTC by 10%+ (7d)
    "rs_vs_eth_strong":   8.0,   # Token outperforms ETH by 8%+ (7d)
    "rs_vs_btc_basic":    5.0,   # Basic outperformance threshold
    "24h_pos_min":        1.0,   # Token up > 1% today
    "24h_outperform":     2.0,   # Token > BTC 24h + 2%
    "7d_pos":             0.0,   # Token above water for the week
    "bybit_funding_neg": -0.0001, # Funding rate squeeze threshold
}

STABLECOINS = {
    "USDT", "USDC", "DAI", "BUSD", "TUSD", "USDD", "FDUSD", "USDE",
    "SUSDE", "WBTC", "WETH", "STETH", "RETH", "PAXG", "XAUT",
}


# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCHING
# ─────────────────────────────────────────────────────────────────────────────

def _get_cg(url: str, params: dict) -> dict | list | None:
    """CoinGecko GET with single retry."""
    for attempt in range(2):
        try:
            r = _CG_SESSION.get(url, params=params, timeout=20)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 30))
                log.warning(f"Rate-limited — waiting {wait}s")
                time.sleep(wait)
                continue
            if r.status_code == 200:
                return r.json()
            return None
        except Exception as e:
            log.warning(f"CG request error: {e}")
            time.sleep(5)
    return None


def _fetch_btc_from_binance() -> dict | None:
    """Fallback: fetch BTC 24h stats from Binance public API."""
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT",
            timeout=10,
        )
        if r.status_code == 200:
            d = r.json()
            price     = float(d.get("lastPrice", 0))
            change_24h = float(d.get("priceChangePercent", 0))
            return {"btc_price": price, "btc_24h": change_24h}
    except Exception:
        pass
    return None


def fetch_market_context() -> dict:
    """Fetch BTC + ETH market context."""
    log.info("Fetching market context (BTC + ETH)...")
    ctx = {"btc_7d": 0.0, "btc_24h": 0.0, "eth_7d": 0.0, "btc_price": 0.0, "regime": "SIDEWAYS"}

    data = _get_cg(
        f"{COINGECKO_API}/coins/markets",
        {
            "vs_currency":             "usd",
            "ids":                     "bitcoin,ethereum",
            "price_change_percentage": "7d,24h",
            "sparkline":               False,
        },
    )
    if not data:
        return ctx

    for coin in data:
        if coin["id"] == "bitcoin":
            ctx["btc_7d"]    = coin.get("price_change_percentage_7d_in_currency") or 0.0
            ctx["btc_24h"]   = coin.get("price_change_percentage_24h")            or 0.0
            ctx["btc_price"] = coin.get("current_price")                          or 0.0
        elif coin["id"] == "ethereum":
            ctx["eth_7d"] = coin.get("price_change_percentage_7d_in_currency") or 0.0

    btc_7d = ctx["btc_7d"]
    if btc_7d >= 3.0:
        ctx["regime"] = "BULL"
    elif btc_7d >= -7.0:
        ctx["regime"] = "SIDEWAYS"
    else:
        ctx["regime"] = "BEAR"

    log.info(
        f"  BTC: ${ctx['btc_price']:,.0f} | 7d: {ctx['btc_7d']:+.1f}% | "
        f"ETH 7d: {ctx['eth_7d']:+.1f}% | Regime: {ctx['regime']}"
    )
    time.sleep(SCAN["api_delay_s"])
    return ctx


def fetch_coins(n: int = 500) -> list:
    """Fetch top N coins from CoinGecko markets endpoint."""
    log.info(f"Fetching top {n} coins...")
    coins = []
    pages = (n // 250) + (1 if n % 250 else 0)

    for page in range(1, pages + 1):
        data = _get_cg(
            f"{COINGECKO_API}/coins/markets",
            {
                "vs_currency":             "usd",
                "order":                   "market_cap_desc",
                "per_page":                min(250, n - len(coins)),
                "page":                    page,
                "price_change_percentage": "7d",
                "sparkline":               False,
            },
        )
        if not data:
            break
        coins.extend(data)
        if len(coins) >= n:
            break
        if page < pages:
            time.sleep(SCAN["api_delay_s"])

    log.info(f"  Fetched {len(coins)} coins")
    return coins[:n]


def load_bybit_state() -> dict:
    """Load Bybit OI + funding state from bybit_radar snapshot."""
    if not _OI_STATE_FILE.exists():
        return {}
    try:
        return json.loads(_OI_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def fetch_bybit_funding_snapshot() -> dict:
    """
    Fetch current Bybit funding rates in one call.
    Returns dict: base_symbol → funding_rate (float)
    """
    try:
        r = _BYBIT_SESSION.get(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "linear"},
            timeout=15,
        )
        if r.status_code != 200:
            return {}
        data = r.json()
        if data.get("retCode") != 0:
            return {}
        result = {}
        for t in data["result"]["list"]:
            sym = t.get("symbol", "")
            if sym.endswith("USDT"):
                base = sym[:-4]
                result[base] = float(t.get("fundingRate", 0) or 0)
        return result
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# SCORING
# ─────────────────────────────────────────────────────────────────────────────

def score_coin(coin: dict, ctx: dict, bybit_funding: dict, bybit_state: dict,
               vol_ranks: dict) -> dict | None:
    """
    Score a coin using only market data (no OHLCV).
    Returns scored dict or None if coin fails basic filters.
    """
    symbol  = coin.get("symbol", "").upper()
    rank    = coin.get("market_cap_rank") or 9999
    price   = coin.get("current_price") or 0.0
    vol_24h = coin.get("total_volume") or 0.0
    ch_7d   = coin.get("price_change_percentage_7d_in_currency")
    ch_24h  = coin.get("price_change_percentage_24h") or 0.0

    if symbol in STABLECOINS:
        return None
    if not (SCAN["min_rank"] <= rank <= SCAN["max_rank"]):
        return None
    if vol_24h < SCAN["min_volume_24h"]:
        return None
    if price <= 0:
        return None
    if ch_7d is None:
        return None

    btc_7d  = ctx["btc_7d"]
    eth_7d  = ctx["eth_7d"]
    btc_24h = ctx["btc_24h"]

    rs_7d    = ch_7d  - btc_7d
    rs_24h   = ch_24h - btc_24h
    rs_vs_eth = ch_7d - eth_7d

    active = []
    score  = 0

    # Momentum signals
    if rs_7d >= THRESHOLDS["rs_vs_btc_strong"]:
        active.append(f"rs_strong(+{rs_7d:.1f}% vs BTC)")
        score += 2  # weighted higher
    elif rs_7d >= THRESHOLDS["rs_vs_btc_basic"]:
        active.append(f"rs_outperform(+{rs_7d:.1f}% vs BTC)")
        score += 1

    if ch_7d > THRESHOLDS["7d_pos"]:
        active.append(f"7d_pos({ch_7d:+.1f}%)")
        score += 1

    if ch_24h > THRESHOLDS["24h_pos_min"]:
        active.append(f"24h_up({ch_24h:+.1f}%)")
        score += 1

    if rs_24h > THRESHOLDS["24h_outperform"]:
        active.append(f"24h_outperform(+{rs_24h:.1f}% vs BTC)")
        score += 1

    if rs_vs_eth >= THRESHOLDS["rs_vs_eth_strong"]:
        active.append(f"rs_vs_eth(+{rs_vs_eth:.1f}%)")
        score += 1

    # Accelerating RS (24h RS better than 7d RS — momentum building)
    if rs_24h > rs_7d and rs_7d > 0:
        active.append("rs_accel")
        score += 1

    # Volume rank (higher-ranked in universe = more liquid with this move)
    vol_rank = vol_ranks.get(symbol, 999)
    total_coins = max(len(vol_ranks), 1)
    if vol_rank < total_coins // 3:
        active.append("vol_rank_top")
        score += 1

    # Bybit signals (bonus signals from OI/funding state)
    funding = bybit_funding.get(symbol)
    if funding is not None:
        if funding <= THRESHOLDS["bybit_funding_neg"]:
            active.append(f"bybit_funding_neg({funding*100:.4f}%)")
            score += 1

    return {
        "symbol":    symbol,
        "coin_id":   coin.get("id", ""),
        "rank":      rank,
        "price":     price,
        "ch_7d":     ch_7d,
        "ch_24h":    ch_24h,
        "rs_7d":     rs_7d,
        "rs_vs_eth": rs_vs_eth,
        "vol_24h":   vol_24h,
        "score":     score,
        "active_signals": active,
    }


# ─────────────────────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────────────────────

def build_report(results: list, ctx: dict, top_n: int, min_signals: int,
                 scan_start: datetime) -> str:
    elapsed = (datetime.now() - scan_start).total_seconds()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    regime = ctx["regime"]
    regime_icon = {"BULL": "🟢", "SIDEWAYS": "🟡", "BEAR": "🔴"}.get(regime, "⚪")

    lines = [
        "═" * 72,
        f"  FAST SCAN v1.0 — {ts}",
        f"  Elapsed: {elapsed:.0f}s (no OHLCV — pure market data)",
        f"  Regime: {regime_icon} {regime}  |  BTC: ${ctx['btc_price']:,.0f}  "
        f"7d: {ctx['btc_7d']:+.1f}%  ETH 7d: {ctx['eth_7d']:+.1f}%",
        "═" * 72,
        "",
    ]

    shown = [r for r in results if r["score"] >= min_signals]

    if not shown:
        lines.append(f"  No tokens with ≥{min_signals} signals found.")
        lines.append("  Market may be rotating or digesting — check master scanner for full analysis.")
    else:
        lines.append(f"  TOP MOVERS  (score ≥ {min_signals}, ranked by score)")
        lines.append("─" * 72)
        lines.append(
            f"  {'#':>3}  {'Symbol':<8}  {'Score':>5}  "
            f"{'7d%':>7}  {'24h%':>6}  {'RS vs BTC':>10}  "
            f"{'Rank':>5}  Signals"
        )
        lines.append("  " + "─" * 68)

        for i, r in enumerate(shown[:top_n], 1):
            sig_str = ", ".join(s.split("(")[0] for s in r["active_signals"])
            lines.append(
                f"  {i:>3}  {r['symbol']:<8}  {r['score']:>5}  "
                f"{r['ch_7d']:>+7.1f}  {r['ch_24h']:>+6.1f}  "
                f"{r['rs_7d']:>+10.1f}  #{r['rank']:>4}  {sig_str}"
            )

        lines.append("")
        if regime == "BEAR":
            lines.append(
                "  ⚠️  BEAR regime — fast scan shows momentum only. "
                "Master scanner gate still applies."
            )
        elif regime == "SIDEWAYS":
            lines.append(
                "  🟡 SIDEWAYS — cross-check with master scanner before entering. "
                "Score ≥ 5 recommended."
            )

    lines.append("")
    lines.append(
        "  NOTE: Fast scan has NO technical analysis (no RSI, MACD, ATR).\n"
        "  Use as a watchlist alert only — confirm with full master scan before entry."
    )
    lines.append("═" * 72)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run(top_n: int = 15, min_signals: int = 3) -> list:
    scan_start = datetime.now()
    log.info("=" * 64)
    log.info("FAST SCAN v1.0 — market data only, no OHLCV")
    log.info("=" * 64)

    # Step 1: Market context
    ctx = fetch_market_context()
    if ctx["btc_price"] == 0.0:
        # CoinGecko failed — try Binance as fallback
        log.warning("CoinGecko BTC context failed — trying Binance fallback...")
        bn_ctx = _fetch_btc_from_binance()
        if bn_ctx:
            ctx["btc_price"] = bn_ctx["btc_price"]
            ctx["btc_24h"]   = bn_ctx["btc_24h"]
            # 7d change unavailable from Binance 24h ticker — leave at 0.0
            log.info(
                f"  Binance fallback OK: BTC ${ctx['btc_price']:,.0f}  "
                f"24h: {ctx['btc_24h']:+.1f}%  (7d unavailable)"
            )
        else:
            logging.error("Both CoinGecko and Binance BTC context failed — aborting scan")
            return []

    # Step 2: Coin list
    coins = fetch_coins(SCAN["top_n_coins"])
    if not coins:
        log.error("No coins fetched — aborting.")
        return []

    # Step 3: Bybit funding (single call)
    log.info("Fetching Bybit funding rates...")
    bybit_funding = fetch_bybit_funding_snapshot()
    bybit_state   = load_bybit_state()
    log.info(f"  Bybit funding loaded: {len(bybit_funding)} symbols")

    # Step 4: Build volume rank lookup
    coins_sorted_vol = sorted(
        coins, key=lambda c: c.get("total_volume") or 0, reverse=True
    )
    vol_ranks = {c.get("symbol", "").upper(): i for i, c in enumerate(coins_sorted_vol)}

    # Step 5: Score
    log.info("Scoring coins...")
    results = []
    for coin in coins:
        scored = score_coin(coin, ctx, bybit_funding, bybit_state, vol_ranks)
        if scored:
            results.append(scored)

    results.sort(key=lambda r: (r["score"], r["rs_7d"]), reverse=True)
    log.info(f"  Scored {len(results)} coins  |  Top score: {results[0]['score'] if results else 0}")

    # Build report
    report_text = build_report(results, ctx, top_n, min_signals, scan_start)
    log.info("\n" + report_text)

    ts_str   = datetime.now().strftime("%Y%m%d_%H%M%S")
    ts_file  = _OUTPUT_DIR / f"fast_scan_{ts_str}.txt"
    lat_file = _OUTPUT_DIR / "fast_scan_LATEST.txt"

    ts_file.write_text(report_text, encoding="utf-8")
    lat_file.write_text(report_text, encoding="utf-8")
    log.info(f"  Saved → {lat_file.name}")

    # Telegram alert for top movers
    try:
        from alerts import alert_watchlist, is_configured
        if is_configured():
            top_entries = [
                {"symbol": r["symbol"], "conviction": r["score"] * 10}
                for r in results[:5] if r["score"] >= min_signals
            ]
            if top_entries:
                alert_watchlist("Fast Scan", top_entries)
    except Exception:
        pass

    return results


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Fast momentum scan — no OHLCV")
    parser.add_argument("--top",          type=int, default=15, help="Top N to show")
    parser.add_argument("--min-signals",  type=int, default=3,  help="Min signals to display")
    args = parser.parse_args()
    run(top_n=args.top, min_signals=args.min_signals)
