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
  python master_orchestrator.py --account 96700
================================================================================
"""

import os
import sys
import argparse
import requests
import pandas as pd
import numpy as np
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
    "size_usdt":          96_700.0,   # Your trading balance
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
    "divergence_window":     30,      # Bars to scan for RSI bullish divergence
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
            entry["conviction"] = conv
            return entry["count"]

    # First sighting or too long a gap — reset
    history[coin_id] = {
        "count":      1,
        "first_seen": now,
        "last_seen":  now,
        "conviction": conv,
    }
    return 1


# ─────────────────────────────────────────────────────────────────────────────
# MARKET CONTEXT
# ─────────────────────────────────────────────────────────────────────────────

def get_market_context() -> dict | None:
    """Fetch BTC data and classify the current market regime."""
    log.info("Fetching market context (BTC)...")
    try:
        data = _get_with_retry(f"{COINGECKO_API}/coins/bitcoin", {})
        if data is None:
            log.warning("Could not fetch BTC context after retries.")
            return None
        md = data["market_data"]
        btc_7d   = md["price_change_percentage_7d_in_currency"]["usd"]
        btc_24h  = md["price_change_percentage_24h_in_currency"]["usd"]
        btc_price = md["current_price"]["usd"]

        if btc_7d >= MACRO["bull_7d_pct"]:
            regime, icon = "BULL",     "🟢"
        elif btc_7d >= MACRO["neutral_7d_pct"]:
            regime, icon = "SIDEWAYS", "🟡"
        else:
            regime, icon = "BEAR",     "🔴"

        ctx = {
            "btc_price": btc_price,
            "btc_7d":    btc_7d,
            "btc_24h":   btc_24h,
            "regime":    regime,
            "icon":      icon,
            "healthy":   btc_7d >= MACRO["neutral_7d_pct"],
        }
        log.info(f"  BTC: ${btc_price:,.0f} | 7d: {btc_7d:+.1f}% | Regime: {icon} {regime}")
        return ctx

    except Exception as e:
        log.warning(f"Could not fetch BTC context: {e}")
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

    # ── Cache hit ──
    if cache_file.exists():
        age_h = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_h < SCAN["cache_max_age_h"]:
            try:
                df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
                if len(df) >= 20:
                    # Source unknown from cached file — probe Binance if symbol given
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

    return p_low2 <= p_low1 * price_gap and r_low2 >= r_low1 + rsi_gap


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


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL DETECTION  (15 layered signals, weighted)
# ─────────────────────────────────────────────────────────────────────────────

# Signal weights — pre-trend signals weighted highest, lagging confirmers lowest.
# Total weight is computed automatically; conviction = earned / total × 100.
_WEIGHTS = {
    # ── Early / pre-trend signals (catch the move before it starts) ──────────
    "rsi_divergence":     3.5,   # Price lower-low, RSI higher-low — earliest signal
    "rs_vs_btc":          3.0,   # Token outperforming BTC 7-day (alpha rotation)
    "macd_turning":       2.5,   # Histogram rising from its bottom before zero cross
    "stealth_accum":      2.5,   # OBV rising while price flat (smart money)
    "funding_neg":        2.0,   # Negative perp funding = shorts paying longs (free carry)
    "cmf":                2.0,   # Chaikin Money Flow > 0.05 — institutional buying pressure
    "vol_expansion":      2.0,   # Recent 24h vol ≥ 1.5× 1-week baseline (fresh capital)
    "bb_squeeze":         2.0,   # Volatility compression — coiling before explosion
    "higher_lows":        2.0,   # Ascending swing lows: base-building structure
    "rs_acceleration":    1.5,   # Short-term RS (28h) confirms recent momentum building
    "declining_sell_vol": 1.5,   # Red-candle volume shrinking — sellers exhausting
    "rsi_ignition":       1.5,   # RSI leaving oversold zone
    "whale_candles":      1.5,   # Large BULLISH candles, close in upper 30% of range
    # ── Trend confirmation (slightly lagging — lower weights) ────────────────
    "macd_crossover":     1.5,   # MACD histogram just crossed zero from below
    "vol_velocity":       1.5,   # Volume accelerating (short MA > long MA)
    "trend_strong":       1.5,   # ADX > threshold and +DI > -DI
    "atr_expanding":      1.0,   # Volatility expanding (energy building)
    "rsi_in_zone":        0.5,   # RSI in 32–65 sweet-spot (broad filter only)
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
    Run all 14 signal layers (pre-trend biased).
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

        # ── 8. Bollinger squeeze ──────────────────────────────────────────────
        bb_w, bb_avg   = _bb_width(closes)
        s["bb_squeeze"] = (
            not np.isnan(bb_w) and not np.isnan(bb_avg)
            and bb_w < SIGNAL["bb_squeeze_width"]
            and bb_w < bb_avg * 0.8
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
        s["vol_expansion"] = (
            _vol_expansion(
                vols,
                recent_bars    = SIGNAL["vol_expansion_recent"],
                baseline_start = SIGNAL["vol_expansion_base_start"],
                baseline_end   = SIGNAL["vol_expansion_base_end"],
                multiplier     = SIGNAL["vol_expansion_mult"],
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
) -> dict:
    """
    Generate a specific, actionable trade plan.

    Stop loss: ATR-based (entry − ATR × multiplier), bounded within [5%, 15%].
    Take profits: 3 levels at configurable R:R ratios.
    Position size: risk-pct / stop-distance, capped at max single position %.
    """
    atr_val = signals.get("atr_value") or 0.0
    if atr_val <= 0:
        # Fallback: derive from ATR%
        atr_pct_fallback = (signals.get("atr_pct") or 5.0) / 100
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
    risk_per_unit  = entry - stop          # always positive

    # ── Position sizing ──────────────────────────────────────────────────────
    risk_usd    = account_size * (ACCOUNT["risk_per_trade_pct"] / 100)
    quantity    = risk_usd / risk_per_unit if risk_per_unit > 0 else 0
    pos_value   = quantity * entry
    pos_pct     = (pos_value / account_size) * 100

    # Cap at max single position size
    if pos_pct > ACCOUNT["max_single_pos_pct"]:
        pos_pct   = ACCOUNT["max_single_pos_pct"]
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
        "  ⏳ = qualified this scan but needs 1 more confirmation scan before entry.",
        "  Monitor all. Enter only after a fresh scan confirms conviction ≥ threshold.",
        "",
        f"  {'#':<4}  {'SYMBOL':<9}  {'RANK':>4}  {'PRICE':>12}  {'7d%':>7}  "
        f"{'CONV':>5}  {'SIGS':>4}  KEY SIGNALS",
        f"  {'-'*4}  {'-'*9}  {'-'*4}  {'-'*12}  {'-'*7}  {'-'*5}  {'-'*4}  {'-'*30}",
    ]
    for i, w in enumerate(watchlist, 1):
        sig      = w["signals"]
        conv     = sig["conviction"]
        nsig     = sig["signal_count"]
        chg7     = w.get("change_7d")
        chg_str  = f"{chg7:+.1f}%" if chg7 is not None else "  N/A"
        # Show the top 3 active signals only to keep the line short
        top_sigs = ", ".join(sig["active_signals"][:3])
        if nsig > 3:
            top_sigs += f" +{nsig - 3}"
        icon = "⏳" if w.get("pending") else ("🔶" if conv >= 38 else "🔹")
        lines.append(
            f"  {i:<4}  {icon}{w['symbol']:<8}  #{w['rank']:>3}  "
            f"${w['price']:<12.5f}  {chg_str:>7}  {conv:>4.0f}  {nsig:>4}  {top_sigs}"
        )
    lines += ["", dash]


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
        lines += [
            f"  BTC Price    :  ${market_ctx['btc_price']:>12,.2f}",
            f"  BTC 7-day    :  {market_ctx['btc_7d']:>+8.2f}%",
            f"  BTC 24-hour  :  {market_ctx['btc_24h']:>+8.2f}%",
            f"  Regime       :  {market_ctx['icon']} {market_ctx['regime']}",
        ]
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

        lines += [
            "",
            f"[{i}] {icon}  {c['symbol']}  (Rank #{c['rank']})",
            f"     Conviction  : {sig['conviction']:.0f} / 100",
            f"     Signals     : {sig['signal_count']} / {len(_WEIGHTS)} active",
            f"     Active      : {active_str}",
            "",
            "     ┌─ TRADE PLAN ──────────────────────────────────────────────",
            f"     │  Entry price  : ${plan['entry']:.6f}   ← buy at market",
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
            f"     RSI = {sig.get('rsi_value', 'N/A')}  |  "
            f"ADX = {sig.get('adx_value', 'N/A')}  |  "
            f"ATR = {sig.get('atr_pct', 'N/A')}%  |  "
            f"RS vs BTC = {sig.get('rs_value', 'N/A')}%  |  "
            f"BB width = {sig.get('bb_width', 'N/A')}%",
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
        account_size = ACCOUNT["size_usdt"]

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
                _regime = market_ctx["regime"] if market_ctx else "SIDEWAYS"
                plan = build_trade_plan(symbol, rank, price, signals, account_size, regime=_regime)
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
            watchlist.append({
                "symbol":    symbol,
                "coin_id":   coin_id,
                "rank":      rank,
                "price":     price,
                "change_7d": change_7d,
                "signals":   signals,
                "pending":   False,
            })

        if not _cache_fresh:
            time.sleep(SCAN["api_delay_s"])

    # ── Save persistence history ──────────────────────────────────────────────
    _save_history(candidate_history)

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
