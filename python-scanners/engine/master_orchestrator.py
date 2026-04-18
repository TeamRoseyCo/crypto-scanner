"""
================================================================================
CRYPTO MASTER ORCHESTRATOR v2.0
================================================================================
The single script that does everything:
  - Scans top coins with 15 layered technical signals (pre-trend biased)
  - Combines Prime Key (momentum) + Pump Hunter (accumulation) logic
  - Classifies market regime (BULL / SIDEWAYS / BEAR) with hard gates:
      BEAR     → NO new longs, standing down to protect capital
      SIDEWAYS → conviction ≥ 60, max position 6%, stops tightened
      BULL     → normal parameters (conviction ≥ 45, max position 12%)
  - 2-scan persistence rule: token must qualify on 2 consecutive scans to enter
  - Generates specific trade plans: exact entry, stop, and 3 take-profit levels
  - Sizes positions using ATR-based risk management
  - Monitors total portfolio heat to prevent over-exposure
  - Outputs a clean, actionable report (txt + json)

Key design principles:
  - NEVER exceed 1.5% account risk per trade
  - ALWAYS use ATR-based stops (adapts to each token's volatility)
  - ALWAYS have at least 7 signals confirming before flagging a setup
  - NEVER enter in a bear market — cash is king
  - Honor stop losses — no exceptions

Usage:
  python master_orchestrator.py
  python master_orchestrator.py --account 95255
================================================================================
"""

import os
import sys
import argparse
import requests

# Ensure demo key is always active regardless of launch method (bat, Task Scheduler, direct Python)
os.environ.setdefault("CG_DEMO_KEY", "CG-oEG3MATjJ1ShQN3xnkJDcGVS")
import pandas as pd
import numpy as np

# Shared indicator implementations (single source of truth across all scanners)
try:
    import sys as _sys
    import os as _os
    _sys.path.insert(0, _os.path.dirname(__file__))
    from indicators import compute_rsi, compute_atr, compute_macd, compute_adx, compute_obv, compute_supertrend, compute_cmf, compute_bb, compute_keltner
except ImportError:
    pass  # indicators.py not found — local fallback functions remain active
import time
import json
import logging
from datetime import datetime
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# PATHS  (always resolved relative to this file — safe from CWD issues)
# ─────────────────────────────────────────────────────────────────────────────
_ENGINE_DIR   = Path(__file__).resolve().parent
_PYTHON_DIR   = _ENGINE_DIR.parent
_PROJECT_ROOT = _PYTHON_DIR.parent
_CACHE_DIR    = _PROJECT_ROOT / "cache"    / "shared_ohlcv"
_OUTPUT_DIR   = _PROJECT_ROOT / "outputs"  / "scanner-results"
_LOG_DIR      = _PROJECT_ROOT / "outputs"  / "logs"

_PERSISTENCE_FILE = _CACHE_DIR / "candidate_history.json"

for d in (_CACHE_DIR, _OUTPUT_DIR, _LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
_log_file = _LOG_DIR / f"orchestrator_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("orchestrator")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION  ← edit these to tune the engine
# ─────────────────────────────────────────────────────────────────────────────

ACCOUNT = {
    "size_usdt":          95_255.0,   # Your trading balance
    "risk_per_trade_pct":      1.5,   # % of account risked per trade
    "max_positions":             8,   # Max simultaneous open positions
    "max_heat_pct":           12.0,   # Max total risk across all open positions
    "max_single_pos_pct":     12.0,   # Max % of account in any one position
}

SCAN = {
    "top_n_coins":           1000,     # How many coins to fetch
    "min_rank":               50,     # Skip the largest (less volatile)
    "max_rank":              600,     # Skip illiquid micro-caps
    "min_volume_24h":    500_000,     # Minimum 24h volume in USD
    "min_price":          0.0001,     # Reject dust tokens
    "min_7d_pct":           -25.0,    # Reject free-falling tokens
    "max_7d_pct":            75.0,    # Reject parabolic / FOMO tokens
    "cache_max_age_h":        4.0,    # Reuse cache if fresher than N hours
    "api_delay_s":    (1.2 if os.environ.get("CG_API_KEY") else
                       4.5 if os.environ.get("CG_DEMO_KEY") else 6.5),  # Pro / Demo / free
    "min_atr_pct":            0.5,    # Reject flatliners — ATR must be ≥ 0.5% of price
    "min_bb_width_pct":       0.5,    # Reject zero-volatility tokens — BB width ≥ 0.5%
    "min_abs_24h_pct":        0.3,    # Reject flatliners early — |24h change| < 0.3% = pegged/dead
    # ── Speed improvements ──────────────────────────────────────────────────
    "rs_prefilter_margin": -12.0,   # Skip coins underperforming BTC by >12pp (7d) — no OHLCV fetch
    "bybit_filter":         True,   # If bybit_symbols.json exists, only scan Bybit-listed perps
    "quiet_hours_utc":     (0, 6),  # Skip high-conviction entries 00:00–06:00 UTC (low liquidity)
}

SIGNAL = {
    # ── Prime Key (momentum quality) ──
    "rsi_min":             32,
    "rsi_max":             65,
    "adx_min":             25,
    "rs_vs_btc_min":     0.03,        # Token must outperform BTC by 2%+ (7d)

    # ── Pump Hunter (accumulation) ──
    "stealth_obv_threshold": 0.015,   # OBV change vs flat price
    "cmf_threshold":         0.05,    # CMF above this = institutional buying confirmed
    "bb_squeeze_width":    0.038,     # BB width below this = compressed
    "rsi_ignition_low":       22,     # RSI floor for ignition zone
    "rsi_ignition_high":      42,     # RSI ceiling for ignition zone
    "whale_candle_mult":     2.0,     # Candle vs avg range multiplier
    "vol_velocity_mult":     1.4,     # Short/long volume MA ratio

    # ── Trade management ──
    "atr_stop_mult":         1.5,     # Stop = entry − (ATR × mult)
    "stop_min_pct":         -15.0,    # Stop never wider than 15%
    "stop_max_pct":          -5.0,    # Stop never tighter than 5%
    "tp_rr":          [2.0, 3.0, 5.0],   # R:R for TP1, TP2, TP3
    "tp_exit_pct":    [ 30,  40,  30],   # % of position sold at each TP

    # ── Qualification ──
    "min_conviction":       45,       # Minimum weighted conviction score (BULL baseline)
    "min_signals":           6,       # Minimum raw signal count (majority must agree)
    # ── Volume expansion (Fix 4 — new signal) ──
    "vol_expansion_recent":  6,       # Recent bars to average (6 × 4h = ~24h)
    "vol_expansion_base_start": 7,    # Baseline window start (bars back)
    "vol_expansion_base_end":  42,    # Baseline window end (bars back, ~1 week)
    "vol_expansion_mult":    1.5,     # Recent vol must be ≥ 1.5× baseline

    # ── New pre-trend signal parameters ────────────────────────────────────
    "divergence_window":     60,      # Bars to scan for RSI bullish divergence (60 = ~10 days @ 4h)
    "divergence_price_gap":  0.98,    # Price-low-2 must be ≤ this × price-low-1
    "divergence_rsi_gap":    5.0,     # RSI at recent low must be N pts above prior low
    "sell_vol_reduction":    0.80,    # Recent red-candle vol ≤ this × earlier red-candle vol
    "higher_lows_window":    30,      # Bars to scan for swing-low structure
}

MACRO = {
    "bull_7d_pct":                3.0,   # BTC 7d above this → BULL
    "neutral_7d_pct":            -7.0,   # BTC 7d above this → SIDEWAYS (below → BEAR)
    "btc_24h_danger":            -3.0,   # BTC 24h drop → add +5 to conviction threshold
    # Regime-specific thresholds (BULL uses the base SIGNAL values)
    "sideways_min_conviction":    60,    # Raised bar in sideways — must really mean it
    "sideways_max_pos_pct":        6.0,  # Half position size in sideways
    "sideways_atr_mult":           1.0,  # Tighter stops in sideways (1.0× vs 1.5×)
    # Persistence — 2-scan confirmation rule
    "persistence_window_h":       24.0,  # Qualifying scans must be within 24h of each other
    "persistence_min_scans":       2,    # Must qualify on N consecutive scans before entry
}

# ─────────────────────────────────────────────────────────────────────────────
# NAMED CONSTANTS  (replace raw magic numbers throughout)
# ─────────────────────────────────────────────────────────────────────────────
STOP_MAX_PCT            = -15.0    # Stop never wider than 15% below entry
STOP_MIN_PCT            = -5.0     # Stop never tighter than 5% below entry
ATR_STOP_MULTIPLIER     = 1.5      # Default ATR multiplier for stop calculation
CONVICTION_DISABLED     = 999      # Effective min conviction when regime blocks trading
MIN_COMBO_FIRES         = 20       # Discard signal combos with fewer fires (backtest)
FUNDING_CROWDED_THRESHOLD = 0.001  # Funding rate above this = crowded long

STABLECOINS = {
    "USDT", "USDC", "DAI", "BUSD", "TUSD", "USDD", "FDUSD", "PYUSD",
    "USDE", "SUSDE", "BFUSD", "RLUSD", "USDG", "USD0", "GHO", "USDAI",
    "WBTC", "WETH", "STETH", "RETH", "CBETH", "PAXG", "XAUT", "TBTC",
    "WBNB", "JITOSOL", "MSOL", "BNSOL", "EURC", "FRAX", "LUSD", "SUSD",
    "CRVUSD", "GUSD", "TUSD", "USDS", "SUSDS", "FRXETH", "OETH", "SUPRETH",
}

# ─────────────────────────────────────────────────────────────────────────────
# DATA SOURCE TRACKING  (Binance vs CoinGecko — volume signals unreliable on CG)
# ─────────────────────────────────────────────────────────────────────────────

_DATA_SOURCE_FILE = _CACHE_DIR / "data_sources.json"

def _load_data_sources() -> dict:
    if _DATA_SOURCE_FILE.exists():
        try:
            return json.loads(_DATA_SOURCE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _save_data_sources(sources: dict) -> None:
    try:
        _DATA_SOURCE_FILE.write_text(json.dumps(sources, indent=2), encoding="utf-8")
    except Exception:
        pass

_DATA_SOURCES: dict = _load_data_sources()   # coin_id → "binance" | "coingecko"


_CG_PRO_KEY   = os.environ.get("CG_API_KEY", "")    # Pro plan key
_CG_DEMO_KEY  = os.environ.get("CG_DEMO_KEY", "")   # Free Demo plan key

if _CG_PRO_KEY:
    COINGECKO_API = "https://pro-api.coingecko.com/api/v3"
else:
    COINGECKO_API = "https://api.coingecko.com/api/v3"

_CG_SESSION = requests.Session()
if _CG_PRO_KEY:
    _CG_SESSION.headers.update({"x-cg-pro-api-key": _CG_PRO_KEY})
elif _CG_DEMO_KEY:
    _CG_SESSION.headers.update({"x-cg-demo-api-key": _CG_DEMO_KEY})

# Binance public API — no key required, 1200 req/min
_BINANCE_API = "https://api.binance.com/api/v3"
_BN_SESSION  = requests.Session()
_BN_SESSION.headers.update({"User-Agent": "crypto-scanner/2.0"})


# ─────────────────────────────────────────────────────────────────────────────
# PERSISTENCE — 2-scan confirmation rule (Fix 2)
# ─────────────────────────────────────────────────────────────────────────────

def _load_history() -> dict:
    """Load candidate history, pruning entries older than 48h."""
    if not _PERSISTENCE_FILE.exists():
        return {}
    try:
        data = json.loads(_PERSISTENCE_FILE.read_text(encoding="utf-8"))
        cutoff = datetime.now().timestamp() - 48 * 3600
        return {k: v for k, v in data.items() if v.get("last_seen", 0) > cutoff}
    except Exception:
        return {}


def _save_history(history: dict) -> None:
    try:
        _PERSISTENCE_FILE.write_text(json.dumps(history, indent=2), encoding="utf-8")
    except Exception:
        pass


def _check_persistence(coin_id: str, conv: float, history: dict) -> int:
    """
    Call this when a coin meets the conviction threshold.
    Increments the consecutive-scan counter if within the persistence window,
    otherwise resets to 1 (first sighting).
    Returns the updated consecutive count.
    """
    now    = datetime.now().timestamp()
    window = MACRO["persistence_window_h"] * 3600

    if coin_id in history:
        entry = history[coin_id]
        if now - entry["last_seen"] <= window:
            entry["count"]     += 1
            entry["last_seen"]  = now
            # Track conviction history (keep last 5 values for trend analysis)
            ch = entry.setdefault("conviction_history", [entry.get("conviction", conv)])
            ch.append(conv)
            if len(ch) > 5:
                ch[:] = ch[-5:]
            entry["conviction"] = conv
            return entry["count"]

    # First sighting or too long a gap — reset
    history[coin_id] = {
        "count":            1,
        "first_seen":       now,
        "last_seen":        now,
        "conviction":       conv,
        "conviction_history": [conv],
    }
    return 1


def _track_watchlist_conviction(coin_id: str, conv: float, history: dict) -> int:
    """
    Track conviction history and consecutive scan count for near-miss (watchlist) coins.
    Returns the updated consecutive count.
    """
    now    = datetime.now().timestamp()
    window = MACRO["persistence_window_h"] * 3600
    key    = f"_wl_{coin_id}"   # prefix to distinguish from qualified candidates
    if key in history:
        entry = history[key]
        ch = entry.setdefault("conviction_history", [entry.get("conviction", conv)])
        ch.append(conv)
        if len(ch) > 5:
            ch[:] = ch[-5:]
        entry["conviction"] = conv
        entry["last_seen"]  = now
        if now - entry.get("last_seen_prev", 0) <= window:
            entry["count"] = entry.get("count", 1) + 1
        else:
            entry["count"] = 1
        entry["last_seen_prev"] = now
        return entry["count"]
    else:
        history[key] = {
            "conviction":         conv,
            "conviction_history": [conv],
            "first_seen":         now,
            "last_seen":          now,
            "last_seen_prev":     now,
            "count":              1,
        }
        return 1


def _conviction_trend(coin_id: str, history: dict, is_watchlist: bool = False) -> str:
    """
    Return 'up', 'down', or 'flat' based on the last 2+ conviction readings.
    """
    key   = f"_wl_{coin_id}" if is_watchlist else coin_id
    entry = history.get(key)
    if not entry:
        return "flat"
    ch = entry.get("conviction_history", [])
    if len(ch) < 2:
        return "flat"
    delta = ch[-1] - ch[-2]
    if delta > 2:
        return "up"
    if delta < -2:
        return "down"
    return "flat"


# ─────────────────────────────────────────────────────────────────────────────
# CORRELATION FILTER — block new entries correlated ≥ 0.85 with open positions
# ─────────────────────────────────────────────────────────────────────────────

def _check_correlation(
    new_sym:   str,
    open_syms: list[str],
    threshold: float = 0.85,
) -> tuple[bool, str]:
    """
    Check whether `new_sym` is highly correlated with any of `open_syms`.
    Reads 30d OHLCV from the shared cache (keyed by coin_id, same as open_syms).

    Returns (allowed, blocking_symbol).
      allowed=True  → entry permitted
      allowed=False → blocked; blocking_symbol is the open position causing the block
    If OHLCV is unavailable for a pair, that pair is skipped (don't block).
    """
    if not open_syms:
        return True, ""

    def _load_closes(coin_id: str) -> pd.Series | None:
        cache_file = _CACHE_DIR / f"{coin_id}_30d.csv"
        if not cache_file.exists():
            return None
        try:
            df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
            if "close" in df.columns and len(df) >= 30:
                return df["close"].pct_change().dropna().tail(30)
        except Exception:
            pass
        return None

    new_returns = _load_closes(new_sym)
    if new_returns is None:
        return True, ""  # can't check — allow

    for sym in open_syms:
        other_returns = _load_closes(sym)
        if other_returns is None:
            continue
        aligned = pd.concat([new_returns, other_returns], axis=1).dropna()
        if len(aligned) < 20:
            continue
        corr = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
        if corr >= threshold:
            return False, sym

    return True, ""


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL COOLDOWN — prevent re-evaluating watchlist coins within 24h
# ─────────────────────────────────────────────────────────────────────────────

_COOLDOWN_FILE        = _CACHE_DIR / "signal_cooldowns.json"
SIGNAL_COOLDOWN_HOURS = 24.0

def _load_cooldowns() -> dict[str, float]:
    """Load cooldown dict from disk, pruning expired entries."""
    if not _COOLDOWN_FILE.exists():
        return {}
    try:
        data    = json.loads(_COOLDOWN_FILE.read_text(encoding="utf-8"))
        cutoff  = time.time() - SIGNAL_COOLDOWN_HOURS * 3600
        return {k: v for k, v in data.items() if v > cutoff}
    except Exception:
        return {}


def _save_cooldowns(cooldowns: dict[str, float]) -> None:
    try:
        _COOLDOWN_FILE.write_text(json.dumps(cooldowns, indent=2), encoding="utf-8")
    except Exception:
        pass


def _is_on_cooldown(symbol: str, cooldowns: dict[str, float]) -> bool:
    last = cooldowns.get(symbol, 0)
    return (time.time() - last) < SIGNAL_COOLDOWN_HOURS * 3600


def _set_cooldown(symbol: str, cooldowns: dict[str, float]) -> None:
    cooldowns[symbol] = time.time()


# ─────────────────────────────────────────────────────────────────────────────
# MARKET CONTEXT
# ─────────────────────────────────────────────────────────────────────────────

def get_market_context() -> dict | None:
    """Fetch BTC + ETH data + BTC dominance and classify the current market regime."""
    log.info("Fetching market context (BTC + ETH + dominance)...")
    try:
        # ── BTC + ETH in one call ─────────────────────────────────────────────
        markets = _get_with_retry(
            f"{COINGECKO_API}/coins/markets",
            {
                "vs_currency":             "usd",
                "ids":                     "bitcoin,ethereum",
                "price_change_percentage": "7d",
                "sparkline":               False,
            },
        )
        if not markets:
            log.warning("Could not fetch BTC/ETH context after retries.")
            return None

        btc_7d = btc_24h = btc_price = eth_7d = None
        for coin in markets:
            if coin["id"] == "bitcoin":
                btc_7d    = coin.get("price_change_percentage_7d_in_currency") or 0.0
                btc_24h   = coin.get("price_change_percentage_24h")            or 0.0
                btc_price = coin.get("current_price")                          or 0.0
            elif coin["id"] == "ethereum":
                eth_7d = coin.get("price_change_percentage_7d_in_currency")    or 0.0

        if btc_7d is None:
            log.warning("BTC data not found in markets response.")
            return None

        time.sleep(SCAN["api_delay_s"])

        # ── BTC dominance ─────────────────────────────────────────────────────
        btc_dominance = None
        global_data = _get_with_retry(f"{COINGECKO_API}/global", {})
        if global_data:
            btc_dominance = global_data.get("data", {}).get("market_cap_percentage", {}).get("btc")

        # ── Regime classification ─────────────────────────────────────────────
        if btc_7d >= MACRO["bull_7d_pct"]:
            regime, icon = "BULL",     "🟢"
        elif btc_7d >= MACRO["neutral_7d_pct"]:
            regime, icon = "SIDEWAYS", "🟡"
        else:
            regime, icon = "BEAR",     "🔴"

        # ETH confirmation: if ETH also in bear territory, note it
        eth_bear = eth_7d is not None and eth_7d < MACRO["neutral_7d_pct"]

        ctx = {
            "btc_price":     btc_price,
            "btc_7d":        btc_7d,
            "btc_24h":       btc_24h,
            "eth_7d":        eth_7d,
            "btc_dominance": btc_dominance,
            "eth_bear":      eth_bear,
            "regime":        regime,
            "icon":          icon,
            "healthy":       btc_7d >= MACRO["neutral_7d_pct"],
        }

        eth_str = f"  ETH 7d: {eth_7d:+.1f}%" if eth_7d is not None else ""
        dom_str = f"  Dominance: {btc_dominance:.1f}%" if btc_dominance else ""
        log.info(
            f"  BTC: ${btc_price:,.0f} | 7d: {btc_7d:+.1f}%{eth_str}{dom_str} | "
            f"Regime: {icon} {regime}"
        )
        if eth_bear and regime != "BEAR":
            log.warning("  ⚠️  ETH also below -7% 7d — broad weakness, not just BTC")
        return ctx

    except Exception as e:
        log.warning(f"Could not fetch market context: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCHING  (rate-limit-aware, with caching)
# ─────────────────────────────────────────────────────────────────────────────

def _get_with_retry(url: str, params: dict, max_attempts: int = 3) -> dict | list | None:
    """GET with exponential backoff on 429 and transient errors."""
    for attempt in range(max_attempts):
        try:
            r = _CG_SESSION.get(url, params=params, timeout=20)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 30 * (2 ** attempt)))
                log.warning(f"Rate-limited — waiting {wait}s (attempt {attempt+1})")
                time.sleep(wait)
                continue
            if r.status_code == 200:
                return r.json()
            log.debug(f"HTTP {r.status_code} for {url}")
            return None
        except requests.exceptions.Timeout:
            log.warning(f"Timeout on attempt {attempt+1} — {url}")
            time.sleep(5)
        except Exception as e:
            log.warning(f"Request error: {e}")
            time.sleep(5)
    return None


def _fetch_binance_ohlcv(symbol: str, days: int = 30) -> pd.DataFrame | None:
    """
    Fetch OHLCV from Binance klines (no API key, 1200 req/min).
    Tries {SYMBOL}USDT pair.  Returns None if symbol not listed on Binance.
    Volume is quote volume (USDT) — comparable across tokens.
    """
    limit = min(days * 6, 1000)   # 4h bars: 6/day × 30 days = 180
    try:
        r = _BN_SESSION.get(
            f"{_BINANCE_API}/klines",
            params={"symbol": f"{symbol}USDT", "interval": "4h", "limit": limit},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        rows = r.json()
        if not isinstance(rows, list) or len(rows) < 20:
            return None
        df = pd.DataFrame(rows, columns=[
            "ts", "open", "high", "low", "close", "base_vol",
            "close_time", "volume", "trades",
            "taker_base", "taker_quote", "ignore",
        ])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms")
        df.set_index("ts", inplace=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df[["open", "high", "low", "close", "volume"]].dropna()
    except Exception:
        return None


def fetch_top_coins(n: int = 400) -> list:
    """Fetch top N coins by market cap from CoinGecko."""
    log.info(f"Fetching top {n} coins by market cap...")
    coins = []
    pages = (n // 250) + (1 if n % 250 else 0)

    for page in range(1, pages + 1):
        data = _get_with_retry(
            f"{COINGECKO_API}/coins/markets",
            {
                "vs_currency":            "usd",
                "order":                  "market_cap_desc",
                "per_page":               min(250, n - len(coins)),
                "page":                   page,
                "price_change_percentage": "7d",
                "sparkline":              False,
            },
        )
        if not data:
            break
        coins.extend(data)
        if len(coins) >= n:
            break
        time.sleep(1.5)

    log.info(f"  Fetched {len(coins)} coins")
    return coins[:n]


def fetch_ohlcv(coin_id: str, days: int = 30, symbol: str = "") -> pd.DataFrame | None:
    """
    Fetch OHLCV for a coin.  Uses a local cache (max age: SCAN.cache_max_age_h hours).
    Tries Binance first (fast, no rate limits) then falls back to CoinGecko.
    Returns a DataFrame with columns: open, high, low, close, volume  (index = datetime).
    Returns None if data is unavailable or insufficient.
    """
    cache_file = _CACHE_DIR / f"{coin_id}_{days}d.csv"

    # ── Cache hit (prefer Binance-sourced cache) ──
    if cache_file.exists():
        age_h = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_h < SCAN["cache_max_age_h"]:
            # Only trust cache if it came from Binance (real intraday volume)
            if _DATA_SOURCES.get(coin_id) == "binance":
                try:
                    df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
                    if len(df) >= 20:
                        return df
                except Exception:
                    pass
            else:
                # CoinGecko or unknown source — try to use cache, probe Binance if needed
                try:
                    df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
                    if coin_id not in _DATA_SOURCES and symbol:
                        probe = _fetch_binance_ohlcv(symbol, 1)
                        _DATA_SOURCES[coin_id] = "binance" if probe is not None else "coingecko"
                        _save_data_sources(_DATA_SOURCES)
                    elif coin_id not in _DATA_SOURCES:
                        _DATA_SOURCES[coin_id] = "coingecko"
                    return df
                except Exception:
                    pass  # corrupt cache → re-fetch

    # ── Binance (primary — no rate limits) ──
    if symbol:
        df = _fetch_binance_ohlcv(symbol, days)
        if df is not None:
            _DATA_SOURCES[coin_id] = "binance"
            _save_data_sources(_DATA_SOURCES)
            try:
                df.to_csv(cache_file)
            except Exception:
                pass
            return df

    # ── CoinGecko fallback ──
    allowed = [1, 7, 14, 30, 90]
    best_days = min(allowed, key=lambda x: abs(x - days))

    ohlc_data = _get_with_retry(
        f"{COINGECKO_API}/coins/{coin_id}/ohlc",
        {"vs_currency": "usd", "days": best_days},
    )
    if not isinstance(ohlc_data, list) or len(ohlc_data) < 20:
        return None

    df = pd.DataFrame(ohlc_data, columns=["ts", "open", "high", "low", "close"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    df.set_index("ts", inplace=True)

    # ── Volume (separate endpoint) ──
    time.sleep(2.0 if _CG_DEMO_KEY or _CG_PRO_KEY else 0.5)
    mc_data = _get_with_retry(
        f"{COINGECKO_API}/coins/{coin_id}/market_chart",
        {"vs_currency": "usd", "days": best_days},
    )
    if mc_data and "total_volumes" in mc_data and mc_data["total_volumes"]:
        vol_df = pd.DataFrame(mc_data["total_volumes"], columns=["ts", "volume"])
        vol_df["ts"] = pd.to_datetime(vol_df["ts"], unit="ms")
        vol_df.set_index("ts", inplace=True)
        # Resample to match OHLC frequency then join
        vol_resampled = vol_df.resample("4h").mean()
        df = df.join(vol_resampled, how="left")
        df["volume"] = df["volume"].ffill()
    else:
        df["volume"] = np.nan

    _DATA_SOURCES[coin_id] = "coingecko"
    _save_data_sources(_DATA_SOURCES)
    try:
        df.to_csv(cache_file)
    except Exception:
        pass  # non-fatal if cache write fails

    return df


# ─────────────────────────────────────────────────────────────────────────────
# DATA FRESHNESS CHECK
# ─────────────────────────────────────────────────────────────────────────────

def _check_data_freshness(df: pd.DataFrame, interval_hours: float = 4.0) -> bool:
    """
    Returns True if OHLCV data is fresh, False if the latest candle is stale.

    Staleness criteria:
      - Latest candle is more than 2 × interval_hours old (missed candles)
      - Index contains duplicate timestamps (data corruption indicator)

    If the index is not a DatetimeIndex, assumes data is ok (returns True).
    """
    if not isinstance(df.index, pd.DatetimeIndex) or len(df) == 0:
        return True  # can't check, assume ok

    latest_ts = df.index[-1]
    if latest_ts.tzinfo is None:
        latest_ts = latest_ts.tz_localize("UTC")

    from datetime import datetime as _dt, timezone as _tz
    now       = _dt.now(_tz.utc)
    age_hours = (now - latest_ts).total_seconds() / 3600

    if age_hours > interval_hours * 2:
        return False   # stale — latest candle is more than 2 intervals old

    # Duplicate timestamps = data quality issue
    if df.index.duplicated().any():
        return False

    return True


# ─────────────────────────────────────────────────────────────────────────────
# TECHNICAL INDICATORS
# ─────────────────────────────────────────────────────────────────────────────

def _rsi(closes: pd.Series, window: int = 9) -> float:
    delta = closes.diff()
    gain  = delta.clip(lower=0).rolling(window).mean()
    loss  = (-delta.clip(upper=0)).rolling(window).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    val   = rsi.dropna()
    return float(val.iloc[-1]) if len(val) > 0 else np.nan


def _rsi_prev(closes: pd.Series, lookback: int = 3, window: int = 9) -> float:
    """RSI calculated on closes excluding the last `lookback` candles."""
    if len(closes) <= lookback + window:
        return np.nan
    return _rsi(closes.iloc[:-lookback], window)


def _atr(highs: pd.Series, lows: pd.Series, closes: pd.Series,
         window: int = 10) -> tuple[float, pd.Series]:
    tr = pd.concat(
        [highs - lows,
         (highs - closes.shift(1)).abs(),
         (lows  - closes.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    atr_series = tr.rolling(window).mean()
    val = atr_series.dropna()
    return (float(val.iloc[-1]) if len(val) > 0 else np.nan), atr_series


def _macd_hist(closes: pd.Series,
               fast: int = 12, slow: int = 26, signal: int = 9) -> float:
    ema_f = closes.ewm(span=fast,   adjust=False).mean()
    ema_s = closes.ewm(span=slow,   adjust=False).mean()
    macd  = ema_f - ema_s
    sig   = macd.ewm(span=signal,   adjust=False).mean()
    val   = (macd - sig).dropna()
    return float(val.iloc[-1]) if len(val) > 0 else np.nan


def _adx(highs: pd.Series, lows: pd.Series, closes: pd.Series,
         window: int = 10) -> tuple[float, float, float]:
    up       = highs.diff()
    down     = -lows.diff()
    plus_dm  = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr       = pd.concat(
        [highs - lows,
         (highs - closes.shift(1)).abs(),
         (lows  - closes.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    atr      = tr.rolling(window).mean().replace(0, np.nan)
    plus_di  = 100 * (plus_dm.rolling(window).mean()  / atr)
    minus_di = 100 * (minus_dm.rolling(window).mean() / atr)
    dx       = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx      = dx.rolling(window).mean()

    def _last(s: pd.Series) -> float:
        v = s.dropna()
        return float(v.iloc[-1]) if len(v) > 0 else np.nan

    return _last(plus_di), _last(minus_di), _last(adx)


def _bb_width(closes: pd.Series, window: int = 20) -> tuple[float, float]:
    sma     = closes.rolling(window).mean()
    std     = closes.rolling(window).std()
    ratio   = (std / sma.replace(0, np.nan))
    width   = ratio.dropna()
    avg_w   = ratio.rolling(window).mean().dropna()
    w_now   = float(width.iloc[-1])  if len(width) > 0  else np.nan
    w_avg   = float(avg_w.iloc[-1])  if len(avg_w) > 0  else np.nan
    return w_now, w_avg


def _keltner(
    highs: pd.Series,
    lows: pd.Series,
    closes: pd.Series,
    period: int = 20,
    atr_period: int = 10,
    multiplier: float = 1.5,
) -> tuple[float, float]:
    """Keltner Channel upper and lower bands at last bar. Used for BB squeeze confirmation."""
    mid  = closes.ewm(span=period, adjust=False).mean()
    tr   = pd.concat(
        [highs - lows,
         (highs - closes.shift(1)).abs(),
         (lows  - closes.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    atr  = tr.rolling(atr_period).mean()
    kc_u = (mid + multiplier * atr).dropna()
    kc_l = (mid - multiplier * atr).dropna()
    kc_upper = float(kc_u.iloc[-1]) if len(kc_u) > 0 else np.nan
    kc_lower = float(kc_l.iloc[-1]) if len(kc_l) > 0 else np.nan
    return kc_upper, kc_lower


def _obv(closes: pd.Series, volumes: pd.Series) -> pd.Series:
    direction = closes.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (direction * volumes).cumsum()


def _rs_vs_btc(token: pd.Series, btc: pd.Series, window: int = 7) -> float:
    if len(token) < window or len(btc) < window:
        return np.nan
    tok_ret = (token.iloc[-1] / token.iloc[-window]) - 1
    btc_ret = (btc.iloc[-1]   / btc.iloc[-window])   - 1
    return float(tok_ret - btc_ret)


def _fetch_funding_rate(symbol: str) -> float | None:
    """
    Fetch the latest perpetual funding rate from Binance (no API key needed).
    Returns the rate as a float (e.g. 0.0001 = 0.01% per 8h), or None on failure.

    Interpretation:
      Negative rate → shorts paying longs → bearish crowd, good for longs (add +2.0 conviction)
      Positive >0.001 (0.1%/8h) → crowded long → apply -10 conviction penalty
    """
    try:
        r = _BN_SESSION.get(
            "https://fapi.binance.com/fapi/v1/fundingRate",
            params={"symbol": f"{symbol}USDT", "limit": 1},
            timeout=5,
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                return float(data[0].get("fundingRate", 0))
    except Exception:
        pass
    return None


def _slope(series: pd.Series, window: int = 5) -> float:
    s = series.dropna()
    if len(s) < window:
        return np.nan
    y = s.iloc[-window:].values.astype(float)
    x = np.arange(len(y), dtype=float)
    try:
        return float(np.polyfit(x, y, 1)[0])
    except Exception:
        return np.nan


def _macd_hist_series(
    closes: pd.Series,
    fast: int = 12, slow: int = 26, signal: int = 9,
) -> pd.Series:
    """Return the full MACD histogram series (needed for crossover + turning signals)."""
    ema_f = closes.ewm(span=fast,   adjust=False).mean()
    ema_s = closes.ewm(span=slow,   adjust=False).mean()
    macd  = ema_f - ema_s
    sig   = macd.ewm(span=signal,   adjust=False).mean()
    return macd - sig


def _rsi_series(closes: pd.Series, window: int = 9) -> pd.Series:
    """Return the full RSI series (needed for divergence detection)."""
    delta = closes.diff()
    gain  = delta.clip(lower=0).rolling(window).mean()
    loss  = (-delta.clip(upper=0)).rolling(window).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _rsi_divergence(
    closes:     pd.Series,
    window:     int   = 20,
    rsi_window: int   = 9,
    price_gap:  float = 0.98,
    rsi_gap:    float = 3.0,
) -> bool:
    """
    Bullish RSI divergence: price makes a lower low in the second half of
    `window` bars while RSI at that low is *higher* than RSI at the first-half
    low.  Indicates buyer strength building even as price dips — a leading
    signal for an imminent trend reversal upward.
    """
    if len(closes) < window + rsi_window + 2:
        return False

    rsi_s = _rsi_series(closes, rsi_window)
    c_win = closes.iloc[-window:]
    r_win = rsi_s.iloc[-window:]

    if r_win.isna().sum() > window // 2:
        return False

    mid  = window // 2
    c1, c2 = c_win.iloc[:mid], c_win.iloc[mid:]
    r1, r2 = r_win.iloc[:mid], r_win.iloc[mid:]

    idx1 = int(np.nanargmin(c1.values))
    idx2 = int(np.nanargmin(c2.values))

    p_low1, p_low2 = float(c1.iloc[idx1]), float(c2.iloc[idx2])
    r_low1, r_low2 = float(r1.iloc[idx1]), float(r2.iloc[idx2])

    if any(np.isnan(v) for v in (p_low1, p_low2, r_low1, r_low2)):
        return False

    if not (p_low2 <= p_low1 * price_gap and r_low2 >= r_low1 + rsi_gap):
        return False

    # 3-bar confirmation: RSI must rise on 3 consecutive bars (filters noise)
    rsi_s_recent = rsi_s.dropna()
    if len(rsi_s_recent) < 3:
        return False
    return (
        float(rsi_s_recent.iloc[-1]) > float(rsi_s_recent.iloc[-2])
        and float(rsi_s_recent.iloc[-2]) > float(rsi_s_recent.iloc[-3])
    )


def _higher_lows(lows: pd.Series, window: int = 20) -> bool:
    """
    True when the last 3 swing lows (local minima where each bar is lower than
    both neighbours) within `window` bars are each higher than the one before.
    Signals a base being built with ascending demand — pre-trend structure.
    """
    if len(lows) < window:
        return False
    recent = lows.iloc[-window:]
    # Require 3 bars on each side to be higher — prevents single-candle noise
    # from qualifying as a swing low on 4h data.
    swings = [
        float(recent.iloc[i])
        for i in range(3, len(recent) - 3)
        if float(recent.iloc[i]) == float(recent.iloc[i-3:i+4].min())
    ]
    return len(swings) >= 3 and swings[-1] > swings[-2] > swings[-3]


def _declining_sell_volume(
    ohlcv:     pd.DataFrame,
    window:    int   = 10,
    reduction: float = 0.80,
) -> bool:
    """
    True when the average volume on red (bearish) candles in the recent half
    of `window` is ≤ `reduction` × the earlier half.  Shrinking sell-side
    volume means sellers are running out of fuel — a key accumulation sign.
    """
    if "volume" not in ohlcv.columns or len(ohlcv) < window:
        return False
    recent = ohlcv.iloc[-window:].dropna(subset=["volume", "open", "close"])
    if len(recent) < 6:
        return False
    mid   = len(recent) // 2
    early = recent.iloc[:mid]
    late  = recent.iloc[mid:]
    rv_e  = early.loc[early["close"] < early["open"], "volume"].values
    rv_l  = late.loc[late["close"]  < late["open"],  "volume"].values
    if len(rv_e) == 0 or len(rv_l) == 0:
        return False
    return float(np.mean(rv_l)) <= float(np.mean(rv_e)) * reduction


def _cmf(ohlcv: pd.DataFrame, window: int = 20) -> float:
    """
    Chaikin Money Flow: measures buying/selling pressure by weighting volume
    by where price closed within the bar's range.  Positive = buyers in control.
    Returns the CMF value (–1 to +1), or nan if insufficient data.
    """
    if len(ohlcv) < window:
        return np.nan
    df  = ohlcv[["high", "low", "close", "volume"]].dropna()
    if len(df) < window:
        return np.nan
    hl  = (df["high"] - df["low"]).replace(0, np.nan)
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / hl
    mfv = mfm * df["volume"]
    cmf = mfv.rolling(window).sum() / df["volume"].rolling(window).sum().replace(0, np.nan)
    val = cmf.dropna()
    return float(val.iloc[-1]) if len(val) > 0 else np.nan


def _vol_expansion(
    volumes:        pd.Series,
    recent_bars:    int   = 6,
    baseline_start: int   = 7,
    baseline_end:   int   = 42,
    multiplier:     float = 1.5,
) -> bool:
    """
    True when the average volume of the last `recent_bars` bars is ≥ `multiplier`
    times the baseline average (bars `baseline_start`–`baseline_end` back).
    Confirms fresh capital is flowing in — separates genuine moves from low-volume
    drifts and filters the most common false signal scenario.
    """
    v = volumes.dropna()
    if len(v) < baseline_end:
        return False
    recent   = float(v.iloc[-recent_bars:].mean())
    baseline = float(v.iloc[-baseline_end:-baseline_start].mean())
    return baseline > 0 and recent >= baseline * multiplier


def _vol_expansion_dow_normalized(
    ohlcv:       pd.DataFrame,
    recent_bars: int   = 6,
    multiplier:  float = 1.5,
) -> bool:
    """
    Day-of-week normalized volume expansion.
    Compares recent volume against the average volume for the SAME day of week
    over the past 6 weeks.  Prevents Monday volume appearing as a breakout
    simply because it is normally lower than Sunday.

    Falls back to _vol_expansion() if the index is not a DatetimeIndex or there
    are fewer than 4 same-DOW historical bars available.
    """
    if "volume" not in ohlcv.columns:
        return False
    vols = ohlcv["volume"].dropna()
    if len(vols) < 20:
        return False

    if not isinstance(ohlcv.index, pd.DatetimeIndex):
        # No datetime index — use standard baseline
        return _vol_expansion(vols, recent_bars=recent_bars, multiplier=multiplier)

    current_dow = ohlcv.index[-1].dayofweek
    dow_vols    = vols[vols.index.dayofweek == current_dow]

    if len(dow_vols) < 4:
        # Not enough same-DOW bars — fall back to standard baseline
        return _vol_expansion(vols, recent_bars=recent_bars, multiplier=multiplier)

    dow_baseline = float(dow_vols.iloc[:-1].mean())  # exclude current bar
    recent_vol   = float(vols.iloc[-recent_bars:].mean())
    return dow_baseline > 0 and recent_vol >= dow_baseline * multiplier


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL DETECTION  (15 layered signals, weighted)
# ─────────────────────────────────────────────────────────────────────────────

# Signal weights — pre-trend signals weighted highest, lagging confirmers lowest.
# Total weight is computed automatically; conviction = earned / total × 100.
_WEIGHTS = {
    # Auto-calibrated by backtest_signals.py on 2026-04-06 22:58
    # Backup: master_orchestrator.py.bak  |  Re-run backtest_signals.py to recalibrate.
    # ── Sorted by weight (highest → lowest) ────────────────────────────────────
    "whale_candles":      4.5,   # Large bullish candles, close in upper 30% of range
    "funding_neg":        2.0,   # Negative perp funding = shorts paying longs (free carry)
    "rsi_divergence":     1.0,   # Price lower-low, RSI higher-low — earliest signal
    "rs_vs_btc":          1.0,   # Token outperforming BTC 7-day (alpha rotation)
    "macd_turning":       1.0,   # Histogram rising from its trough before zero-cross
    "stealth_accum":      1.0,   # OBV rising while price flat (smart money)
    "cmf":                1.0,   # Chaikin Money Flow > 0.05 — institutional buying
    "vol_expansion":      1.0,   # Recent 24h vol ≥ 1.5× 1-week baseline (fresh capital)
    "bb_squeeze":         1.0,   # Volatility compression — coiling before explosion
    "higher_lows":        1.0,   # Ascending swing lows: base-building structure
    "rs_acceleration":    1.0,   # Short-term RS (28h) confirms momentum building
    "declining_sell_vol": 1.0,   # Red-candle volume shrinking — sellers exhausting
    "rsi_ignition":       1.0,   # RSI leaving oversold zone
    "macd_crossover":     1.0,   # MACD histogram just crossed zero from below
    "vol_velocity":       1.0,   # Volume accelerating (short MA > long MA)
    "trend_strong":       1.0,   # ADX > threshold and +DI > -DI
    "atr_expanding":      1.0,   # Volatility expanding (energy building)
    "rsi_in_zone":        1.0,   # RSI in 32–65 sweet-spot (broad filter only)
}
_TOTAL_WEIGHT = sum(_WEIGHTS.values())

# Volume-dependent signals — unreliable when data comes from CoinGecko
# (CoinGecko returns daily volume resampled to 4h, not real intraday volume).
# Their contribution is halved automatically when binance_source=False.
_VOLUME_SIGNAL_KEYS = {
    "vol_velocity", "vol_expansion", "stealth_accum",
    "cmf", "whale_candles", "declining_sell_vol",
}


def detect_signals(
    ohlcv:          pd.DataFrame,
    btc_closes:     pd.Series | None,
    price:          float,
    binance_source: bool       = True,
    funding_rate:   float | None = None,
) -> dict | None:
    """
    Run all 18 signal layers (pre-trend biased).
    Returns a dict with boolean flags, scalar values, and a conviction score.
    Returns None if data is insufficient.
    """
    if ohlcv is None or len(ohlcv) < 20:
        return None

    closes  = ohlcv["close"]
    highs   = ohlcv["high"]
    lows    = ohlcv["low"]
    opens   = ohlcv["open"]
    has_vol = "volume" in ohlcv.columns and not ohlcv["volume"].isna().all()
    vols    = ohlcv["volume"] if has_vol else pd.Series(np.nan, index=closes.index)

    s = {}  # signals dict

    try:
        # ── 1. RSI in zone ───────────────────────────────────────────────────
        rsi = _rsi(closes)
        s["rsi_in_zone"] = not np.isnan(rsi) and SIGNAL["rsi_min"] < rsi < SIGNAL["rsi_max"]
        s["rsi_value"]   = round(rsi, 1) if not np.isnan(rsi) else None

        # ── 2. RSI ignition (leaving oversold) ───────────────────────────────
        rsi_prev = _rsi_prev(closes, lookback=3)
        lo, hi   = SIGNAL["rsi_ignition_low"], SIGNAL["rsi_ignition_high"]
        s["rsi_ignition"] = (
            not np.isnan(rsi) and not np.isnan(rsi_prev)
            and rsi_prev < lo and lo <= rsi < hi
        )

        # ── 3. RSI bullish divergence (NEW — leading pre-trend signal) ────────
        s["rsi_divergence"] = _rsi_divergence(
            closes,
            window    = SIGNAL["divergence_window"],
            rsi_window = 9,
            price_gap  = SIGNAL["divergence_price_gap"],
            rsi_gap    = SIGNAL["divergence_rsi_gap"],
        )

        # ── 4. MACD crossover + MACD turning (replaces simple macd_bullish) ──
        macd_vals = _macd_hist_series(closes).dropna()
        if len(macd_vals) >= 4:
            h0, h1, h2, h3 = (
                float(macd_vals.iloc[-1]),
                float(macd_vals.iloc[-2]),
                float(macd_vals.iloc[-3]),
                float(macd_vals.iloc[-4]),
            )
            # Crossover: histogram just flipped positive this bar
            s["macd_crossover"] = h0 > 0 and h1 <= 0
            # Turning: still negative but strictly rising for 3 consecutive bars
            # (fires before crossover — earliest MACD signal; 3 bars filters noise)
            s["macd_turning"]   = h0 < 0 and h3 < h2 < h1 < h0
        else:
            s["macd_crossover"] = False
            s["macd_turning"]   = False

        # ── 5. ADX / trend strength ───────────────────────────────────────────
        plus_di, minus_di, adx = _adx(highs, lows, closes)
        s["trend_strong"] = (
            not np.isnan(adx) and adx > SIGNAL["adx_min"]
            and not np.isnan(plus_di) and not np.isnan(minus_di)
            and plus_di > minus_di
        )
        s["adx_value"]  = round(adx,      1) if not np.isnan(adx)      else None
        s["plus_di"]    = round(plus_di,  1) if not np.isnan(plus_di)  else None
        s["minus_di"]   = round(minus_di, 1) if not np.isnan(minus_di) else None

        # ── 6. RS vs BTC (7-day window) ───────────────────────────────────────
        # Primary: 7-day (42 bars) sustained outperformance = real rotation.
        # Acceleration: 28h (7 bars) confirms fresh momentum building right now.
        rs_7d  = _rs_vs_btc(closes, btc_closes, window=42) if btc_closes is not None else np.nan
        rs_28h = _rs_vs_btc(closes, btc_closes, window=7)  if btc_closes is not None else np.nan
        s["rs_vs_btc"]     = not np.isnan(rs_7d) and rs_7d >= SIGNAL["rs_vs_btc_min"]
        s["rs_acceleration"] = (
            not np.isnan(rs_28h) and not np.isnan(rs_7d)
            and rs_28h >= SIGNAL["rs_vs_btc_min"]  # recent window also outperforming
            and rs_28h > rs_7d                      # recent momentum accelerating vs base
        )
        s["rs_value"] = round(rs_7d * 100, 2) if not np.isnan(rs_7d) else None

        # ── 7. ATR expanding ──────────────────────────────────────────────────
        atr_val, atr_series = _atr(highs, lows, closes)
        atr_pct             = (atr_val / price) if (price > 0 and not np.isnan(atr_val)) else np.nan
        atr_slope           = _slope(atr_series / closes.replace(0, np.nan), window=5)
        s["atr_expanding"]  = (
            not np.isnan(atr_slope) and atr_slope > 0
            and not np.isnan(atr_pct) and atr_pct > 0.025
        )
        s["atr_value"] = float(atr_val)          if not np.isnan(atr_val) else None
        s["atr_pct"]   = round(atr_pct * 100, 2) if not np.isnan(atr_pct) else None

        # ── 8. Bollinger squeeze with Keltner Channel confirmation (A9 — TTM squeeze) ──
        # True squeeze: BB is narrow AND BB bands are inside Keltner bands.
        # This filters out fake compressions and confirms a real coiling setup.
        bb_w, bb_avg = _bb_width(closes)
        kc_upper, kc_lower = _keltner(highs, lows, closes)

        # Compute BB upper/lower at the last bar for KC comparison
        _bb_sma = closes.rolling(20).mean()
        _bb_std = closes.rolling(20).std()
        _bb_u_s = (_bb_sma + 2.0 * _bb_std).dropna()
        _bb_l_s = (_bb_sma - 2.0 * _bb_std).dropna()
        _bb_u   = float(_bb_u_s.iloc[-1]) if len(_bb_u_s) > 0 else np.nan
        _bb_l   = float(_bb_l_s.iloc[-1]) if len(_bb_l_s) > 0 else np.nan
        _bb_inside_kc = (
            not any(np.isnan(x) for x in [kc_upper, kc_lower, _bb_u, _bb_l])
            and _bb_u < kc_upper and _bb_l > kc_lower
        )
        s["bb_squeeze"] = (
            not np.isnan(bb_w) and not np.isnan(bb_avg)
            and bb_w < SIGNAL["bb_squeeze_width"]
            and bb_w < bb_avg * 0.8
            and _bb_inside_kc   # BB must be inside Keltner bands (TTM squeeze)
        )
        s["bb_width"] = round(bb_w * 100, 2) if not np.isnan(bb_w) else None

        # ── 9. Volume velocity (acceleration) ────────────────────────────────
        vol_accel = False
        if has_vol:
            v = vols.dropna()
            if len(v) >= 10:
                short_ma   = v.rolling(5).mean()
                long_ma    = v.rolling(10).mean()
                sv, lv     = float(short_ma.iloc[-1]), float(long_ma.iloc[-1])
                pv, plv    = float(short_ma.iloc[-3]), float(long_ma.iloc[-3])
                ratio_now  = sv / lv  if lv  > 0 else 0
                ratio_prev = pv / plv if plv > 0 else 0
                vol_accel  = (
                    ratio_now > SIGNAL["vol_velocity_mult"]
                    and ratio_now > ratio_prev
                )
        s["vol_velocity"] = vol_accel

        # ── 9b. Volume expansion — fresh capital flowing in (Fix 4) ──────────
        # Uses day-of-week normalisation when possible to avoid false signals
        # from daily volume seasonality (e.g. Monday always lower than Sunday).
        s["vol_expansion"] = (
            _vol_expansion_dow_normalized(
                ohlcv,
                recent_bars = SIGNAL["vol_expansion_recent"],
                multiplier  = SIGNAL["vol_expansion_mult"],
            )
            if has_vol else False
        )

        # ── 10. Stealth accumulation — OBV divergence (fixed normalisation) ──
        #  OLD bug: normalised by abs(obv[-10])+1 which is meaningless for large
        #  cumulative sums.  Now normalised by avg_vol × lookback — a stable unit.
        stealth = False
        if has_vol:
            valid = ohlcv[["close", "volume"]].dropna()
            if len(valid) >= 10:
                obv       = _obv(valid["close"], valid["volume"])
                price_chg = (valid["close"].iloc[-1] / valid["close"].iloc[-10]) - 1
                avg_vol   = float(valid["volume"].mean())
                obv_chg   = (obv.iloc[-1] - obv.iloc[-10]) / (avg_vol * 10 + 1)
                stealth   = (
                    obv_chg        > SIGNAL["stealth_obv_threshold"]
                    and abs(price_chg) < 0.02   # price genuinely flat (tightened: was 0.05)
                )
        s["stealth_accum"] = stealth

        # ── 10b. Chaikin Money Flow — institutional buying pressure ──────────
        cmf_val      = _cmf(ohlcv) if has_vol else np.nan
        s["cmf"]     = not np.isnan(cmf_val) and cmf_val > SIGNAL["cmf_threshold"]
        s["cmf_value"] = round(cmf_val, 3) if not np.isnan(cmf_val) else None

        # ── 11. Whale candles — bullish only (fixed direction bias) ──────────
        #  OLD bug: large down candles triggered this too.  Now requires:
        #    (a) large range vs 20-bar avg, (b) green candle, (c) close in
        #    upper 30% of the candle's high-low range (buying conviction).
        whale = False
        if len(closes) >= 20:
            ranges    = highs - lows
            avg_range = ranges.rolling(20).mean()
            rng5      = ranges.iloc[-5:]
            avg5      = avg_range.iloc[-5:]
            o5        = opens.iloc[-5:]
            c5        = closes.iloc[-5:]
            l5        = lows.iloc[-5:]
            pos5      = (c5 - l5) / rng5.clip(lower=1e-12)   # 0 = bottom, 1 = top
            bullish_whale = (
                (rng5 > avg5 * SIGNAL["whale_candle_mult"]) &  # large range
                (c5   > o5) &                                   # green candle
                (pos5 >= 0.70)                                  # close in upper 30%
            )
            whale = bool(bullish_whale.any())
        s["whale_candles"] = whale

        # ── 12. Declining sell volume (NEW — sellers exhausting) ─────────────
        s["declining_sell_vol"] = (
            _declining_sell_volume(
                ohlcv,
                window    = 10,
                reduction = SIGNAL["sell_vol_reduction"],
            )
            if has_vol else False
        )

        # ── 13. Higher lows (base-building / uptrend structure) ──────────────
        s["higher_lows"] = _higher_lows(lows, window=SIGNAL["higher_lows_window"])

        # ── 14. Funding rate — perp market sentiment ──────────────────────────
        # Negative funding = shorts paying longs (free carry for longs, bullish crowd).
        # Crowded long (>0.1%/8h) = penalty applied outside weight system (see below).
        funding_crowded = False
        if funding_rate is not None:
            s["funding_neg"] = funding_rate < 0.0
            funding_crowded  = funding_rate > 0.001   # > 0.1% per 8h = crowded long
        else:
            s["funding_neg"] = False
        s["funding_crowded"] = funding_crowded

        # ── 15. Conviction score ──────────────────────────────────────────────
        # Volume signals are halved for CoinGecko-sourced data (daily vol resampled
        # to 4h — not real intraday volume, produces systematic false signals).
        effective_score = 0.0
        for sig_key, weight in _WEIGHTS.items():
            if not s.get(sig_key, False):
                continue
            if not binance_source and sig_key in _VOLUME_SIGNAL_KEYS:
                effective_score += weight * 0.5   # halved — unreliable data source
            else:
                effective_score += weight

        # Crowded long penalty — outside weight system so it can push below threshold
        crowded_penalty = 10.0 if funding_crowded else 0.0

        conviction = max(0.0, round(
            (effective_score / _TOTAL_WEIGHT) * 100 - crowded_penalty, 1
        ))
        active = [k for k in _WEIGHTS if s.get(k, False)]

        s["conviction"]        = conviction
        s["signal_count"]      = len(active)
        s["active_signals"]    = active
        s["binance_source"]    = binance_source
        s["crowded_penalty"]   = crowded_penalty

        return s

    except Exception as e:
        log.debug(f"Signal detection error: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# TRADE PLAN GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def build_trade_plan(
    symbol:       str,
    rank:         int,
    price:        float,
    signals:      dict,
    account_size: float,
    regime:       str = "SIDEWAYS",
) -> dict | None:
    """
    Generate a specific, actionable trade plan.

    Stop loss: ATR-based (entry − ATR × multiplier), bounded within [5%, 15%].
    Take profits: 3 levels at configurable R:R ratios.
    Position size: risk-pct / stop-distance, capped at max single position %.
    Returns None if ATR is invalid or stop >= entry (safety guard).
    """
    atr_val = signals.get("atr_value") or 0.0
    if np.isnan(atr_val) or atr_val <= 0:
        # Fallback: derive from ATR%
        atr_pct_fallback = (signals.get("atr_pct") or 5.0) / 100
        if np.isnan(atr_pct_fallback) or atr_pct_fallback <= 0:
            atr_pct_fallback = 0.05
        atr_val = price * atr_pct_fallback

    entry = price

    # ── Stop loss ────────────────────────────────────────────────────────────
    raw_stop      = entry - (atr_val * SIGNAL["atr_stop_mult"])
    raw_stop_pct  = ((raw_stop / entry) - 1) * 100

    # Clamp to [min, max] bounds
    if raw_stop_pct < SIGNAL["stop_min_pct"]:
        stop_pct = SIGNAL["stop_min_pct"]
    elif raw_stop_pct > SIGNAL["stop_max_pct"]:
        stop_pct = SIGNAL["stop_max_pct"]
    else:
        stop_pct = raw_stop_pct

    stop           = entry * (1 + stop_pct / 100)
    if stop >= entry:
        logging.warning(
            f"Invalid stop for {symbol}: stop={stop:.6f} >= entry={entry:.6f}, skipping"
        )
        return None
    risk_per_unit  = entry - stop          # always positive

    # ── Position sizing (ATR-volatility-scaled) ──────────────────────────────
    # Base formula: risk_usdt / stop_distance gives the risk-based position.
    # ATR scalar: 3% ATR is the reference (1.0×). Higher ATR = smaller position
    # (already high volatility, don't compound by sizing large). Lower ATR =
    # allow slightly larger position, capped at 1.5× to avoid concentration.
    atr_pct_val  = signals.get("atr_pct") or 3.0       # ATR as % of price
    if not atr_pct_val or np.isnan(atr_pct_val):
        atr_pct_val = 3.0
    ATR_REFERENCE_PCT = 3.0
    atr_scalar   = ATR_REFERENCE_PCT / max(atr_pct_val, 1.0)
    atr_scalar   = min(atr_scalar, 1.5)   # cap upside at 1.5×
    atr_scalar   = max(atr_scalar, 0.5)   # floor at 0.5×

    risk_usd     = account_size * (ACCOUNT["risk_per_trade_pct"] / 100)
    quantity     = risk_usd / risk_per_unit if risk_per_unit > 0 else 0
    pos_value    = quantity * entry
    pos_pct      = (pos_value / account_size) * 100

    # ATR-scaled cap: volatile coins get smaller max position
    max_pos_pct_scaled = ACCOUNT["max_single_pos_pct"] * atr_scalar

    # Cap at ATR-scaled max single position size
    if pos_pct > max_pos_pct_scaled:
        pos_pct   = max_pos_pct_scaled
        pos_value = account_size * (pos_pct / 100)
        quantity  = pos_value / entry
        risk_usd  = quantity * risk_per_unit   # recalculate actual risk

    # ── Take profit levels ───────────────────────────────────────────────────
    tps = []
    for rr, sell_pct in zip(SIGNAL["tp_rr"], SIGNAL["tp_exit_pct"]):
        tp_price    = entry + (risk_per_unit * rr)
        tp_gain_pct = ((tp_price / entry) - 1) * 100
        tp_qty      = quantity * (sell_pct / 100)
        tp_usdt     = tp_qty * tp_price
        tps.append({
            "price":    round(tp_price, 8),
            "gain_pct": round(tp_gain_pct, 1),
            "rr":       rr,
            "sell_pct": sell_pct,
            "usdt":     round(tp_usdt, 2),
        })

    # ── Expected value — regime-specific win rates ────────────────────────────
    # These are estimates based on signal-type backtesting on mid-cap alts.
    # Run backtest/backtest_signals.py to calibrate with your own historical data.
    _regime_win_rates = {"BULL": 0.45, "SIDEWAYS": 0.38, "BEAR": 0.30}
    win_prob   = _regime_win_rates.get(regime, 0.38)
    avg_gain   = sum(tp["gain_pct"] for tp in tps) / len(tps)
    avg_loss   = abs(stop_pct)
    ev_pct     = round((win_prob * avg_gain) - ((1 - win_prob) * avg_loss), 2)

    return {
        "symbol":       symbol,
        "rank":         rank,
        "entry":        round(entry, 8),
        "stop":         round(stop,  8),
        "stop_pct":     round(stop_pct, 1),
        "risk_usd":     round(risk_usd,  2),
        "risk_pct":     round((risk_usd / account_size) * 100, 2),
        "quantity":     round(quantity,  4),
        "pos_value":    round(pos_value, 2),
        "pos_pct":      round(pos_pct,   1),
        "take_profits": tps,
        "ev_pct":       ev_pct,
    }


# ─────────────────────────────────────────────────────────────────────────────
# REPORT GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def _append_watchlist(lines: list, watchlist: list | None, sep: str, dash: str) -> None:
    """Append the WATCHLIST section (near-miss tokens, conviction 25–44) to lines."""
    if not watchlist:
        return
    lines += [
        "",
        "WATCHLIST  — tokens building momentum, not ready to enter yet",
        "-" * 40,
        "  ⏳ = needs 1 more confirmation scan  |  ↗ = conviction rising  |  ↘ = fading",
        "  Monitor all. Enter only after a fresh scan confirms conviction ≥ threshold.",
        "",
        f"  {'#':<4}  {'SYMBOL':<9}  {'RANK':>4}  {'PRICE':>12}  {'7d%':>7}  "
        f"{'CONV':>5}  {'SIGS':>4}  {'TREND':>5}  KEY SIGNALS",
        f"  {'-'*4}  {'-'*9}  {'-'*4}  {'-'*12}  {'-'*7}  {'-'*5}  {'-'*4}  {'-'*5}  {'-'*28}",
    ]
    for i, w in enumerate(watchlist, 1):
        sig      = w["signals"]
        conv     = sig["conviction"]
        nsig     = sig["signal_count"]
        chg7     = w.get("change_7d")
        chg_str  = f"{chg7:+.1f}%" if chg7 is not None else "  N/A"
        trend    = w.get("trend", "flat")
        trend_str = " ↗" if trend == "up" else (" ↘" if trend == "down" else "  →")
        # Show the top 3 active signals only to keep the line short
        top_sigs = ", ".join(sig["active_signals"][:3])
        if nsig > 3:
            top_sigs += f" +{nsig - 3}"
        wl_count = w.get("wl_count", 1)
        count_str = f" ×{wl_count}" if wl_count > 1 else ""
        if w.get("rsi_overbought"):
            icon = "🔴"
            rsi_note = f"  ⚠️ RSI {w['signals'].get('rsi_value', '?'):.0f} — wait for cooldown <65"
        else:
            icon = "⏳" if w.get("pending") else ("🔶" if conv >= 38 else "🔹")
            rsi_note = ""
        lines.append(
            f"  {i:<4}  {icon}{w['symbol']:<8}  #{w['rank']:>3}  "
            f"${w['price']:<12.5f}  {chg_str:>7}  {conv:>4.0f}  {nsig:>4}  {trend_str:>5}  {top_sigs}{count_str}{rsi_note}"
        )
    lines += ["", dash]


def _rsi_zone_label(rsi: float | None) -> str:
    """Return a short entry-timing label based on RSI value."""
    if rsi is None:
        return ""
    if rsi < 45:
        return "[IDEAL — room to run]"
    if rsi < 55:
        return "[GOOD — momentum zone]"
    if rsi < 65:
        return "[OK — getting extended]"
    return "[EXTENDED — wait for pullback <55]"


def _rsi_entry_note(rsi: float | None) -> str:
    """Return entry action note for the trade plan entry line."""
    if rsi is None:
        return "<- buy at market"
    if rsi < 55:
        return "<- buy at market"
    if rsi < 65:
        return "<- consider limit or wait for dip"
    return "<- WAIT — RSI extended, target pullback to 50-55"


def _load_cross_scanner_symbols() -> tuple[set[str], set[str]]:
    """
    Parse fast_scan_LATEST.txt and bybit_radar_LATEST.txt to extract symbol sets.
    Returns (fast_symbols, bybit_symbols).  Fails silently if files are missing.
    """
    fast_syms:  set[str] = set()
    bybit_syms: set[str] = set()

    fast_file  = _OUTPUT_DIR / "fast_scan_LATEST.txt"
    bybit_file = _OUTPUT_DIR / "bybit_radar_LATEST.txt"

    # Fast scan: lines like "    1  FF    9   +32.4  ..."
    if fast_file.exists():
        try:
            import re as _re
            for line in fast_file.read_text(encoding="utf-8").splitlines():
                m = _re.match(r"^\s+\d+\s+([A-Z0-9]+)\s+\d+", line)
                if m:
                    fast_syms.add(m.group(1))
        except Exception:
            pass

    # Bybit radar: lines like "  [ 1] TRU   Score: 12.0  ..."
    if bybit_file.exists():
        try:
            import re as _re
            for line in bybit_file.read_text(encoding="utf-8").splitlines():
                m = _re.match(r"^\s+\[\s*\d+\]\s+([A-Z0-9]+)\s+", line)
                if m:
                    bybit_syms.add(m.group(1))
        except Exception:
            pass

    return fast_syms, bybit_syms


def build_report(
    market_ctx:   dict | None,
    candidates:   list,
    account_size: float,
    watchlist:    list | None = None,
    scan_start:   datetime | None = None,
) -> str:
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sep  = "=" * 80
    dash = "-" * 80

    fast_syms, bybit_syms = _load_cross_scanner_symbols()

    lines = [
        sep,
        "  CRYPTO MASTER ORCHESTRATOR — TRADE PLAN REPORT",
        f"  Generated: {ts}",
        sep,
    ]

    # Staleness warning — entries are only valid near scan time
    if scan_start is not None:
        age_min = (datetime.now() - scan_start).total_seconds() / 60
        if age_min > 60:
            lines += [
                "",
                f"  ⚠️  SCAN DATA IS {age_min:.0f} MINUTES OLD.",
                "  Verify current price is within 2% of entry before placing orders.",
                "  If price has moved past TP1, skip the trade entirely.",
            ]

    # ── Market context ────────────────────────────────────────────────────────
    lines += ["", "MARKET CONTEXT", "-" * 40]
    if market_ctx:
        eth_line = (
            f"  ETH 7-day    :  {market_ctx['eth_7d']:>+8.2f}%"
            + ("  ⚠️ ETH also weak" if market_ctx.get("eth_bear") else "")
        ) if market_ctx.get("eth_7d") is not None else None
        dom_line = (
            f"  BTC Dominance:  {market_ctx['btc_dominance']:>8.1f}%"
        ) if market_ctx.get("btc_dominance") is not None else None

        ctx_lines = [
            f"  BTC Price    :  ${market_ctx['btc_price']:>12,.2f}",
            f"  BTC 7-day    :  {market_ctx['btc_7d']:>+8.2f}%",
            f"  BTC 24-hour  :  {market_ctx['btc_24h']:>+8.2f}%",
        ]
        if eth_line:
            ctx_lines.append(eth_line)
        if dom_line:
            ctx_lines.append(dom_line)
        ctx_lines.append(f"  Regime       :  {market_ctx['icon']} {market_ctx['regime']}")
        lines += ctx_lines
        if market_ctx["regime"] == "BEAR":
            lines += [
                "",
                "  ⛔ BEAR MARKET — NO new long positions.",
                "  BTC down > 7% over 7 days. Standing down to protect capital.",
                "  Hold cash. Wait for regime to recover.",
            ]
        elif market_ctx["regime"] == "SIDEWAYS":
            lines += [
                "",
                f"  🟡 SIDEWAYS — Thresholds tightened for safety:",
                f"     Conviction ≥ {MACRO['sideways_min_conviction']}  |  "
                f"Max position {MACRO['sideways_max_pos_pct']}%  |  "
                f"Stops {MACRO['sideways_atr_mult']}× ATR",
                "     Missing a trade is better than losing capital in chop.",
            ]
        if market_ctx.get("btc_24h", 0) < MACRO.get("btc_24h_danger", -3.0):
            lines += [
                "",
                f"  ⚠️  BTC down {market_ctx['btc_24h']:.1f}% today — conviction threshold raised +5.",
            ]
    else:
        lines.append("  BTC data unavailable. Proceed with extreme caution.")

    # ── Account summary ───────────────────────────────────────────────────────
    lines += [
        "",
        "ACCOUNT",
        "-" * 40,
        f"  Balance       : ${account_size:>12,.2f} USDT",
        f"  Risk / trade  : {ACCOUNT['risk_per_trade_pct']}%  "
        f"(${account_size * ACCOUNT['risk_per_trade_pct'] / 100:,.0f} USDT per trade)",
        f"  Max positions : {ACCOUNT['max_positions']}",
        f"  Max heat      : {ACCOUNT['max_heat_pct']}%",
    ]

    if not candidates:
        lines += [
            "",
            sep,
            "  NO QUALIFYING SETUPS FOUND",
            "",
            "  All scanned tokens failed to meet the minimum conviction threshold.",
            "  This means market conditions are not favorable right now.",
            "",
            "  RECOMMENDATION: Stay in USDT. Wait for the next scan.",
            "  Patience is a trading edge. Forcing trades loses money.",
            sep,
        ]
        _append_watchlist(lines, watchlist, sep, dash)
        return "\n".join(lines)

    # ── Trade setups ──────────────────────────────────────────────────────────
    total_risk = 0.0
    total_pos  = 0.0

    lines += ["", "", f"TOP {len(candidates)} SETUPS  (ranked by conviction)", sep]

    for i, c in enumerate(candidates, 1):
        sig  = c["signals"]
        plan = c["plan"]
        total_risk += plan["risk_usd"]
        total_pos  += plan["pos_value"]

        icon = "🔥" if sig["conviction"] >= 70 else "⚡" if sig["conviction"] >= 55 else "💡"
        active_str = "  |  ".join(sig["active_signals"])

        # Cross-scanner detection
        sym = c["symbol"]
        in_fast  = sym in fast_syms
        in_bybit = sym in bybit_syms
        cross_parts = []
        if in_fast:
            cross_parts.append("Fast")
        if in_bybit:
            cross_parts.append("Bybit")
        cross_badge = f"  🔥 CROSS-SIGNAL ({' + '.join(cross_parts)})" if cross_parts else ""

        lines += [
            "",
            f"[{i}] {icon}  {c['symbol']}  (Rank #{c['rank']}){cross_badge}",
            f"     Conviction  : {sig['conviction']:.0f} / 100",
            f"     Signals     : {sig['signal_count']} / {len(_WEIGHTS)} active",
            f"     Active      : {active_str}",
            "",
            "     ┌─ TRADE PLAN ──────────────────────────────────────────────",
            f"     │  Entry price  : ${plan['entry']:.6f}   {_rsi_entry_note(sig.get('rsi_value'))}",
            f"     │  STOP LOSS    : ${plan['stop']:.6f}   ({plan['stop_pct']:.1f}%)"
            "   ← EXIT HARD — no exceptions",
            f"     │",
            f"     │  Position     : ${plan['pos_value']:>10,.0f} USDT "
            f"({plan['pos_pct']:.1f}% of account)",
            f"     │  Risk amount  : ${plan['risk_usd']:>10,.0f} USDT "
            f"({plan['risk_pct']:.2f}% of account)",
            f"     │",
        ]

        for j, tp in enumerate(plan["take_profits"], 1):
            lines.append(
                f"     │  TP{j}  ${tp['price']:.6f} "
                f"(+{tp['gain_pct']:.1f}%)  "
                f"Sell {tp['sell_pct']}%  ≈ ${tp['usdt']:,.0f} USDT  "
                f"R:R 1:{tp['rr']:.0f}"
            )

        lines += [
            f"     │",
            f"     │  Expected value (40% win rate): {plan['ev_pct']:+.1f}%",
            "     └──────────────────────────────────────────────────────────",
            "",
            "     ── INDICATORS ──────────────────────────────────────────────",
            f"     RSI = {sig.get('rsi_value', 'N/A')}  {_rsi_zone_label(sig.get('rsi_value'))}  |  "
            f"ADX = {sig.get('adx_value', 'N/A')}  |  "
            f"ATR = {sig.get('atr_pct', 'N/A')}%  |  "
            f"RS vs BTC = {sig.get('rs_value', 'N/A')}%  |  "
            f"BB width = {sig.get('bb_width', 'N/A')}%",
            "     ⚠️  RSI above is 14-period on 4h candles.  Always confirm on 1h RSI7 before entry.",
            dash,
        ]

    # ── Portfolio heat ────────────────────────────────────────────────────────
    heat_pct  = (total_risk / account_size) * 100
    heat_icon = "🟢" if heat_pct < 8 else "🟡" if heat_pct < 12 else "🔴"
    cash_left = account_size - total_pos

    lines += [
        "",
        "PORTFOLIO SUMMARY",
        "-" * 40,
        f"  Total deployed   : ${total_pos:>10,.0f} USDT  ({total_pos/account_size*100:.1f}%)",
        f"  Total at risk    : ${total_risk:>10,.0f} USDT  ({heat_pct:.1f}%)  {heat_icon}",
        f"  Cash reserve     : ${cash_left:>10,.0f} USDT",
    ]

    if heat_pct > ACCOUNT["max_heat_pct"]:
        lines.append(
            f"\n  ⚠️  Portfolio heat {heat_pct:.1f}% exceeds max "
            f"{ACCOUNT['max_heat_pct']}%.  Skip lower-conviction setups."
        )

    # ── Watchlist ─────────────────────────────────────────────────────────────
    _append_watchlist(lines, watchlist, sep, dash)

    # ── Rules ─────────────────────────────────────────────────────────────────
    lines += [
        "",
        "TRADING RULES  (non-negotiable)",
        "-" * 40,
        "  1. Honor every stop loss.  No exceptions.  No averaging down.",
        "  2. After TP1 hit → move stop to breakeven.  Now it's a free trade.",
        "  3. After TP2 hit → move stop to TP1.  Lock in profit.",
        "  4. Never open more than 8 positions simultaneously.",
        "  5. If BTC drops >5% in a single day → tighten all stops to -5%.",
        "  6. Run this scanner again in 2-3 hours for fresh signals.",
        "  7. ⏳ rule: a token must qualify on 2 consecutive scans before entry.",
        "  8. Cash is a position.  Forcing trades is how accounts blow up.",
        "",
        "DISCLAIMER",
        "-" * 40,
        "  This is a technical analysis tool, not financial advice.",
        "  Past signals do not guarantee future results.",
        "  Crypto markets are highly volatile.  Only risk what you can afford to lose.",
        sep,
    ]

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ORCHESTRATION LOOP
# ─────────────────────────────────────────────────────────────────────────────

def run(account_size: float | None = None, coin_whitelist: set | None = None) -> list:
    """
    Full scan + analysis + report generation.
    Returns the list of final candidates with their trade plans.
    """
    if account_size is None:
        try:
            from bybit_auth import fetch_live_balance_with_fallback
            account_size = fetch_live_balance_with_fallback(ACCOUNT["size_usdt"])
        except ImportError:
            account_size = ACCOUNT["size_usdt"]

    # Spot balance warning — if free USDT is far below configured default,
    # it likely means capital is tied up in spot positions (not a real loss).
    _configured_default = ACCOUNT["size_usdt"]
    if account_size < _configured_default * 0.70:
        _diff = _configured_default - account_size
        print(
            f"\n  ⚠️  BALANCE WARNING: Free USDT is ${account_size:,.0f} "
            f"(${_diff:,.0f} below configured ${_configured_default:,.0f}).\n"
            f"  This usually means capital is locked in open spot positions.\n"
            f"  Risk sizing below is based on available USDT only.\n"
        )

    log.info("")
    log.info("=" * 80)
    log.info("  CRYPTO MASTER ORCHESTRATOR v2.1")
    log.info("  18-signal engine | funding rate | corr filter | volume source tagging")
    log.info("=" * 80)
    log.info(f"  Account: ${account_size:,.2f} USDT  |  "
             f"Risk/trade: {ACCOUNT['risk_per_trade_pct']}%  |  "
             f"Max positions: {ACCOUNT['max_positions']}")
    log.info("")

    scan_start = datetime.now()   # track for staleness warning in report

    # ── Circuit breaker — halt new entries after -5% daily loss ──────────────
    _CIRCUIT_BREAKER_ACTIVE = False
    try:
        from trade_journal import get_today_pnl
        today_pnl     = get_today_pnl()
        today_pnl_pct = today_pnl / account_size * 100
        if today_pnl_pct <= -5.0:
            print(f"\n  ⛔  CIRCUIT BREAKER TRIGGERED")
            print(f"  Today's P&L: {today_pnl_pct:.1f}% — exceeds -5% daily loss limit.")
            print(f"  No new entries until tomorrow UTC.\n")
            _CIRCUIT_BREAKER_ACTIVE = True
        else:
            _CIRCUIT_BREAKER_ACTIVE = False
    except Exception:
        _CIRCUIT_BREAKER_ACTIVE = False

    # ── Step 1: Market context ────────────────────────────────────────────────
    log.info("[1/5] Analyzing market regime...")
    market_ctx = get_market_context()

    # ── Fix 1: Hard regime gate ───────────────────────────────────────────────
    if market_ctx:
        if market_ctx["regime"] == "BEAR":
            log.warning("  ⛔ BEAR market — NO new longs. Standing down to protect capital.")
            report_text = build_report(market_ctx, [], account_size,
                                        watchlist=[], scan_start=scan_start)
            log.info("\n" + report_text)
            ts_str     = datetime.now().strftime("%Y%m%d_%H%M%S")
            ts_file    = _OUTPUT_DIR / f"master_trade_plan_{ts_str}.txt"
            latest_txt = _OUTPUT_DIR / "master_trade_plan_LATEST.txt"
            ts_file.write_text(report_text, encoding="utf-8")
            latest_txt.write_text(report_text, encoding="utf-8")
            log.info(f"\n  Done.  Bear market — no new longs.")
            return []

        elif market_ctx["regime"] == "SIDEWAYS":
            SIGNAL["min_conviction"]      = MACRO["sideways_min_conviction"]
            ACCOUNT["max_single_pos_pct"] = MACRO["sideways_max_pos_pct"]
            SIGNAL["atr_stop_mult"]       = MACRO["sideways_atr_mult"]
            log.info(
                f"  🟡 SIDEWAYS — conviction raised to {MACRO['sideways_min_conviction']}, "
                f"max position capped at {MACRO['sideways_max_pos_pct']}%, "
                f"stops tightened to {MACRO['sideways_atr_mult']}× ATR"
            )
        else:
            log.info("  🟢 BULL — normal parameters apply")

        # Extra caution if BTC is dropping hard today even in a bull regime
        if market_ctx["btc_24h"] < MACRO["btc_24h_danger"]:
            extra = 5
            SIGNAL["min_conviction"] += extra
            log.warning(
                f"  ⚠️  BTC dropping {market_ctx['btc_24h']:.1f}% today — "
                f"conviction threshold raised by +{extra} to {SIGNAL['min_conviction']}"
            )

        # A4 — Time-of-day filter: raise bar during low-liquidity quiet hours
        _qh = SCAN.get("quiet_hours_utc")
        if _qh:
            _utc_h = datetime.utcnow().hour
            if _qh[0] <= _utc_h < _qh[1]:
                SIGNAL["min_conviction"] += 10
                log.warning(
                    f"  🌙 Quiet hours {_qh[0]:02d}:00–{_qh[1]:02d}:00 UTC — "
                    f"low liquidity filter: conviction threshold raised +10 to {SIGNAL['min_conviction']}"
                )

    # ── Step 2: BTC OHLCV (for relative strength) ────────────────────────────
    log.info("[2/5] Fetching BTC historical data...")
    btc_ohlcv  = fetch_ohlcv("bitcoin", 30)
    btc_closes = btc_ohlcv["close"] if btc_ohlcv is not None else None
    log.info(f"  BTC data: {'OK' if btc_closes is not None else 'FAILED (RS signals disabled)'}")
    time.sleep(SCAN["api_delay_s"])

    # ── Step 3: Coin list ─────────────────────────────────────────────────────
    log.info(f"[3/5] Fetching top {SCAN['top_n_coins']} coins...")
    coins = fetch_top_coins(SCAN["top_n_coins"])
    log.info(f"  Fetched {len(coins)} coins")

    if coin_whitelist:
        coins = [c for c in coins if c["id"] in coin_whitelist]
        log.info(f"  Pipeline mode — {len(coins)} coins after whitelist filter")

    # ── Step 4: Scan ──────────────────────────────────────────────────────────
    log.info(f"[4/5] Scanning for high-conviction setups...")
    log.info(
        f"  Filters: Rank {SCAN['min_rank']}–{SCAN['max_rank']}  |  "
        f"Vol ≥ ${SCAN['min_volume_24h']:,}  |  "
        f"Min conviction: {SIGNAL['min_conviction']}"
    )
    log.info("")

    # ── Fix 2: Load persistence history ──────────────────────────────────────
    candidate_history = _load_history()

    # ── A6: Load signal cooldowns ─────────────────────────────────────────────
    _cooldowns = _load_cooldowns()

    # ── Bybit universe filter — load symbol set if available ─────────────────
    _bybit_symbols: set = set()
    if SCAN.get("bybit_filter"):
        _sym_file = _CACHE_DIR / "bybit_symbols.json"
        if _sym_file.exists():
            try:
                _sym_data   = json.loads(_sym_file.read_text(encoding="utf-8"))
                _bybit_symbols = set(_sym_data.get("symbols", []))
                log.info(f"  Bybit filter active: {len(_bybit_symbols)} perp symbols loaded")
            except Exception:
                log.warning("  Could not load bybit_symbols.json — scanning all coins")

    raw_candidates = []
    watchlist      = []      # near-miss tokens (conviction 25–59, signals ≥ 3)
    seen_ids       = set()   # deduplication — coin_id → skip if already processed
    scanned        = 0

    for coin in coins:
        symbol     = coin["symbol"].upper()
        coin_id    = coin["id"]
        rank       = coin.get("market_cap_rank") or 9999
        price      = coin.get("current_price") or 0.0
        vol_24h    = coin.get("total_volume") or 0.0
        change_7d  = coin.get("price_change_percentage_7d_in_currency")
        change_24h = coin.get("price_change_percentage_24h")

        # ── Quick pre-filters (no API call needed) ────────────────────────────
        if coin_id in seen_ids:
            log.debug(f"  skip {symbol} — already scanned this session")
            continue
        if symbol in STABLECOINS:
            continue
        if not (SCAN["min_rank"] <= rank <= SCAN["max_rank"]):
            continue
        if vol_24h < SCAN["min_volume_24h"]:
            continue
        if price < SCAN["min_price"]:
            continue
        if change_7d is not None and not (SCAN["min_7d_pct"] <= change_7d <= SCAN["max_7d_pct"]):
            continue
        # Early flatliner rejection — saves a full OHLCV fetch + 6.5s delay per token.
        # Pegged tokens / stablecoins missed by the symbol list have near-zero 24h moves.
        if change_24h is not None and abs(change_24h) < SCAN["min_abs_24h_pct"]:
            continue

        # ── Bybit universe filter (saves OHLCV fetch for non-perp coins) ─────
        if _bybit_symbols and symbol not in _bybit_symbols:
            log.debug(f"  skip {symbol} — not listed as Bybit perp")
            continue

        # ── RS pre-filter — skip clear underperformers without OHLCV fetch ───
        # If a coin is underperforming BTC by more than the margin, it won't pass
        # enough signals to be a candidate.  Saves 4.5s per skipped coin.
        if market_ctx and change_7d is not None:
            _btc_7d    = market_ctx.get("btc_7d", 0.0)
            _rs_margin = change_7d - _btc_7d
            if _rs_margin < SCAN["rs_prefilter_margin"]:
                log.debug(
                    f"  skip {symbol} — RS pre-filter "
                    f"({change_7d:+.1f}% vs BTC {_btc_7d:+.1f}%  margin {_rs_margin:+.1f}pp)"
                )
                continue

        # ── A6: Skip coins on 24h signal cooldown ──────────────────────────
        if _is_on_cooldown(symbol, _cooldowns):
            log.debug(f"  skip {symbol} — on 24h signal cooldown")
            continue

        seen_ids.add(coin_id)
        scanned += 1
        log.info(
            f"  [{scanned:3d}]  {symbol:<8}  (#{rank:>3})  "
            f"${price:<12.5f}  7d: {change_7d:+.1f}%" if change_7d is not None
            else f"  [{scanned:3d}]  {symbol:<8}  (#{rank:>3})  ${price:<12.5f}"
        )

        # ── Fetch OHLCV — Binance first, CoinGecko fallback ─────────────────
        _cf = _CACHE_DIR / f"{coin_id}_30d.csv"
        _cache_fresh = _cf.exists() and (time.time() - _cf.stat().st_mtime) / 3600 < SCAN["cache_max_age_h"]
        ohlcv = fetch_ohlcv(coin_id, 30, symbol)

        # ── A12: Data staleness check ─────────────────────────────────────────
        if ohlcv is not None and not _check_data_freshness(ohlcv):
            log.warning(f"          → skip {symbol}: OHLCV data is stale (>8h old or duplicates)")
            if not _cache_fresh:
                time.sleep(SCAN["api_delay_s"])
            continue

        # ── Fetch funding rate (Binance perps, no key needed) ────────────────
        funding_rate = _fetch_funding_rate(symbol)
        if funding_rate is not None:
            fr_str = f"{funding_rate*100:+.4f}%/8h"
            if funding_rate > 0.001:
                fr_str += " ⚠️ CROWDED"
            elif funding_rate < 0:
                fr_str += " ✅ shorts paying"
            log.debug(f"          Funding: {fr_str}")

        # ── Data source for volume signal reliability ─────────────────────────
        is_binance = _DATA_SOURCES.get(coin_id, "coingecko") == "binance"

        # ── Run signals ───────────────────────────────────────────────────────
        signals = detect_signals(ohlcv, btc_closes, price,
                                 binance_source=is_binance, funding_rate=funding_rate)

        # ── A7: MACD suppression in SIDEWAYS/BEAR — excessive whipsaws ────────
        # MACD crossovers produce false signals in ranging markets.
        # Only count MACD signals in BULL regime.
        # After suppressing, recalculate conviction to keep score consistent.
        if signals is not None and market_ctx:
            _regime_for_macd = market_ctx.get("regime", "SIDEWAYS")
            if _regime_for_macd != "BULL":
                signals["macd_crossover"] = False
                signals["macd_bullish"]   = False
                # Recalculate conviction without suppressed MACD signals
                _eff = 0.0
                for _sk, _w in _WEIGHTS.items():
                    if not signals.get(_sk, False):
                        continue
                    if not is_binance and _sk in _VOLUME_SIGNAL_KEYS:
                        _eff += _w * 0.5
                    else:
                        _eff += _w
                _crowded_pen = 10.0 if signals.get("funding_crowded", False) else 0.0
                signals["conviction"]     = max(0.0, round((_eff / _TOTAL_WEIGHT) * 100 - _crowded_pen, 1))
                signals["active_signals"] = [k for k in _WEIGHTS if signals.get(k, False)]
                signals["signal_count"]   = len(signals["active_signals"])

        if signals is None:
            log.info("          → skip (insufficient data)")
            if not _cache_fresh:
                time.sleep(SCAN["api_delay_s"])
            continue

        # ── Volatility floor — reject flatliners (stablecoins, pegged tokens) ─
        atr_pct  = signals.get("atr_pct")  or 0.0
        bb_width = signals.get("bb_width") or 0.0
        if atr_pct < SCAN["min_atr_pct"] or bb_width < SCAN["min_bb_width_pct"]:
            log.info(
                f"          → skip (flatliner: ATR {atr_pct:.2f}% / BB {bb_width:.2f}%)"
            )
            if not _cache_fresh:
                time.sleep(SCAN["api_delay_s"])
            continue

        conv  = signals["conviction"]
        nsig  = signals["signal_count"]
        log.info(f"          → conviction {conv:.0f}/100  |  signals {nsig}/{len(_WEIGHTS)}")

        # ── Qualification with 2-scan persistence rule (Fix 2) ───────────────
        if conv >= SIGNAL["min_conviction"] and nsig >= SIGNAL["min_signals"]:
            scan_count = _check_persistence(coin_id, conv, candidate_history)
            active_str = ", ".join(signals["active_signals"])
            if scan_count < MACRO["persistence_min_scans"]:
                log.info(
                    f"          ⏳ FIRST SIGHTING (scan {scan_count}/{MACRO['persistence_min_scans']}) "
                    f"— needs confirmation next scan"
                )
                # Park in watchlist as pending — not yet tradeable
                watchlist.append({
                    "symbol":    symbol,
                    "coin_id":   coin_id,
                    "rank":      rank,
                    "price":     price,
                    "change_7d": change_7d,
                    "signals":   signals,
                    "pending":   True,
                })
            else:
                # ── A2: Circuit breaker — skip new entries if daily loss limit hit ──
                if _CIRCUIT_BREAKER_ACTIVE:
                    log.info(
                        f"          ⛔ CIRCUIT BREAKER ACTIVE — {symbol} blocked (daily loss limit)"
                    )
                    continue

                # ── RSI overbought gate — demote if 4h RSI > 70 ──────────────────
                _rsi_val = signals.get("rsi_value") or 0.0
                if _rsi_val > 70:
                    log.info(
                        f"          ⚠️  RSI OVERBOUGHT ({_rsi_val:.1f}) — "
                        f"demoting to watchlist, wait for cooldown below 65"
                    )
                    watchlist.append({
                        "symbol":        symbol,
                        "coin_id":       coin_id,
                        "rank":          rank,
                        "price":         price,
                        "change_7d":     change_7d,
                        "signals":       signals,
                        "pending":       False,
                        "trend":         "flat",
                        "rsi_overbought": True,
                        "blocked_reason": f"RSI overbought ({_rsi_val:.1f}) — wait for cooldown to <65",
                    })
                    continue

                _regime = market_ctx["regime"] if market_ctx else "SIDEWAYS"
                plan = build_trade_plan(symbol, rank, price, signals, account_size, regime=_regime)
                if plan is None:
                    log.warning(f"  {symbol}: invalid stop/ATR — skipping from candidates")
                    continue

                # ── A1: Correlation filter — block if ≥ 0.85 corr with open positions ──
                _open_coin_ids = [c["coin_id"] for c in raw_candidates]
                _corr_ok, _corr_blocker = _check_correlation(coin_id, _open_coin_ids)
                if not _corr_ok:
                    log.info(
                        f"          BLOCKED: correlation >= 0.85 with {_corr_blocker} — skipping"
                    )
                    watchlist.append({
                        "symbol":    symbol,
                        "coin_id":   coin_id,
                        "rank":      rank,
                        "price":     price,
                        "change_7d": change_7d,
                        "signals":   signals,
                        "pending":   False,
                        "trend":     "flat",
                        "blocked_reason": f"correlation >= 0.85 with {_corr_blocker}",
                    })
                    continue

                raw_candidates.append({
                    "symbol":    symbol,
                    "coin_id":   coin_id,
                    "rank":      rank,
                    "price":     price,
                    "vol_24h":   vol_24h,
                    "change_7d": change_7d,
                    "signals":   signals,
                    "plan":      plan,
                    "scan_count": scan_count,
                })
                log.info(
                    f"          ★★★ CONFIRMED CANDIDATE (scan {scan_count}) — {active_str}"
                )

        elif conv >= 25 and nsig >= 3:
            _wl_count = _track_watchlist_conviction(coin_id, conv, candidate_history)
            _trend = _conviction_trend(coin_id, candidate_history, is_watchlist=True)
            watchlist.append({
                "symbol":    symbol,
                "coin_id":   coin_id,
                "rank":      rank,
                "price":     price,
                "change_7d": change_7d,
                "signals":   signals,
                "pending":   False,
                "trend":     _trend,
                "wl_count":  _wl_count,
            })
            # A6: Set 24h cooldown so the same coin doesn't re-accumulate conviction
            # during the next scan before a genuine signal change occurs.
            _set_cooldown(symbol, _cooldowns)

        if not _cache_fresh:
            time.sleep(SCAN["api_delay_s"])

    # ── Save persistence history ──────────────────────────────────────────────
    _save_history(candidate_history)

    # ── Save signal cooldowns ─────────────────────────────────────────────────
    _save_cooldowns(_cooldowns)

    # ── Step 5: Rank, correlation filter, heat limit, generate report ────────
    log.info(f"\n[5/5] Building trade plan report...")
    log.info(f"  Raw candidates found: {len(raw_candidates)}")

    raw_candidates.sort(key=lambda c: c["signals"]["conviction"], reverse=True)

    # ── Correlation filter — remove duplicates within correlated pairs ────────
    # When two candidates have close-price correlation > 0.75 over the last 30d,
    # they are effectively the same bet. Keep only the higher-conviction one.
    if len(raw_candidates) > 1:
        _corr_closes: dict = {}
        for c in raw_candidates:
            try:
                _cf_path = _CACHE_DIR / f"{c['coin_id']}_30d.csv"
                if _cf_path.exists():
                    _cdf = pd.read_csv(_cf_path, index_col=0, parse_dates=True)
                    if "close" in _cdf.columns and len(_cdf) >= 20:
                        _corr_closes[c["coin_id"]] = _cdf["close"]
            except Exception:
                pass

        _to_remove: set = set()
        _ids = [c["coin_id"] for c in raw_candidates if c["coin_id"] in _corr_closes]
        for _i, _id_a in enumerate(_ids):
            for _id_b in _ids[_i + 1:]:
                if _id_a in _to_remove or _id_b in _to_remove:
                    continue
                try:
                    _aligned = pd.concat(
                        [_corr_closes[_id_a], _corr_closes[_id_b]], axis=1
                    ).dropna()
                    if len(_aligned) >= 20:
                        _corr = float(_aligned.iloc[:, 0].corr(_aligned.iloc[:, 1]))
                        if _corr > 0.75:
                            _conv_a = next(
                                c["signals"]["conviction"] for c in raw_candidates
                                if c["coin_id"] == _id_a
                            )
                            _conv_b = next(
                                c["signals"]["conviction"] for c in raw_candidates
                                if c["coin_id"] == _id_b
                            )
                            _drop = _id_b if _conv_a >= _conv_b else _id_a
                            _keep = _id_a if _drop == _id_b else _id_b
                            _to_remove.add(_drop)
                            log.info(
                                f"  Correlation filter: dropped {_drop} "
                                f"(corr={_corr:.2f} with {_keep} — kept higher conviction)"
                            )
                except Exception:
                    pass

        if _to_remove:
            raw_candidates = [c for c in raw_candidates if c["coin_id"] not in _to_remove]
            log.info(f"  After correlation filter: {len(raw_candidates)} unique candidates")

    watchlist.sort(key=lambda w: w["signals"]["conviction"], reverse=True)
    watchlist = watchlist[:10]  # cap at top 10

    final = []
    cum_heat = 0.0

    for c in raw_candidates:
        heat_add = c["plan"]["risk_pct"]
        if len(final) >= ACCOUNT["max_positions"]:
            log.info(
                f"  Skipping {c['symbol']} — max {ACCOUNT['max_positions']} positions reached"
            )
            continue
        if cum_heat + heat_add > ACCOUNT["max_heat_pct"]:
            log.info(
                f"  Skipping {c['symbol']} — would push heat to "
                f"{cum_heat + heat_add:.1f}% (max {ACCOUNT['max_heat_pct']}%)"
            )
            continue
        final.append(c)
        cum_heat += heat_add

    # ── Output ────────────────────────────────────────────────────────────────
    report_text = build_report(market_ctx, final, account_size,
                               watchlist=watchlist, scan_start=scan_start)
    log.info("\n" + report_text)

    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Timestamped copy
    ts_file = _OUTPUT_DIR / f"master_trade_plan_{ts_str}.txt"
    ts_file.write_text(report_text, encoding="utf-8")

    # Latest (always overwritten — easy to find)
    latest_txt = _OUTPUT_DIR / "master_trade_plan_LATEST.txt"
    latest_txt.write_text(report_text, encoding="utf-8")

    # ── Telegram alerts for confirmed setups ──────────────────────────────────
    try:
        from alerts import alert_setup, alert_watchlist, is_configured, send_heartbeat
        if is_configured():
            _regime = market_ctx["regime"] if market_ctx else "SIDEWAYS"
            if final:
                for _c in final:
                    _sig  = _c["signals"]
                    _plan = _c["plan"]
                    alert_setup(
                        scanner    = "Master",
                        symbol     = _c["symbol"],
                        conviction = int(_sig["conviction"]),
                        entry      = _plan["entry"],
                        stop       = _plan["stop"],
                        tp1        = _plan["take_profits"][0]["price"],
                        regime     = _regime,
                        signals    = _sig["active_signals"],
                    )
            # A11: Heartbeat — confirms scanner ran successfully
            _top_sym = final[0]["symbol"] if final else None
            send_heartbeat("Master Scanner", coins_scanned=scanned, top_setup=_top_sym)
    except Exception:
        pass

    # JSON (for programmatic access / future dashboard)
    def _serialise(obj):
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        return str(obj)

    json_payload = {
        "timestamp":      ts_str,
        "account_size":   account_size,
        "market_context": market_ctx,
        "config": {
            "account": ACCOUNT,
            "scan":    SCAN,
            "signal":  SIGNAL,
        },
        "candidates": [
            {
                "symbol":    c["symbol"],
                "coin_id":   c["coin_id"],
                "rank":      c["rank"],
                "price":     c["price"],
                "vol_24h":   c["vol_24h"],
                "change_7d": c["change_7d"],
                "signals": {
                    k: (_serialise(v) if isinstance(v, (np.bool_, np.integer, np.floating)) else v)
                    for k, v in c["signals"].items()
                },
                "plan": c["plan"],
            }
            for c in final
        ],
    }
    latest_json = _OUTPUT_DIR / "master_trade_plan_LATEST.json"
    with open(latest_json, "w", encoding="utf-8") as f:
        json.dump(json_payload, f, indent=2, default=_serialise)

    log.info(f"\n  Report  → {latest_txt}")
    log.info(f"  JSON    → {latest_json}")
    log.info(f"  Log     → {_log_file}")
    log.info(f"\n  Done.  {len(final)} high-conviction setup(s) found.")

    return final


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Crypto Master Orchestrator — unified trade plan generator"
    )
    parser.add_argument(
        "--account",
        type=float,
        default=None,
        help=f"Account size in USDT (default: {ACCOUNT['size_usdt']:,.0f})",
    )
    args = parser.parse_args()

    results = run(account_size=args.account)
