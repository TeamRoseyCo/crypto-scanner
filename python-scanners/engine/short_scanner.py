"""
================================================================================
SHORT SCANNER  v1.0
================================================================================
Scans rank 50–400 coins for high-conviction BEARISH setups on Binance perps.

Key design differences from master_orchestrator (longs):
  - No BTC regime gate — shorts work in all regimes (thresholds adjust)
  - Only scans tokens with active Binance USDT perpetual markets
  - Stop placed ABOVE entry (ATR-based), TPs BELOW entry
  - Positive funding rate = conviction BOOST (crowded longs = short fuel)
  - Higher conviction bar in BULL regime (going against the tide)

13 bearish signals:
  Early / pre-trend (leading):
    rsi_divergence_bear  — price higher-high, RSI lower-high (distribution)
    macd_turning_bear    — histogram falling from peak while still positive
    stealth_distrib      — OBV falling while price flat (smart money exiting)
    funding_crowded      — funding > 0.1%/8h (crowded longs = short fuel)
    rs_vs_btc_neg        — underperforming BTC by 3%+ over 7 days
    rs_decel             — short-term RS (28h) worse than 7-day baseline

  Structure / trend (confirming):
    lower_highs          — descending swing highs structure
    bear_candles         — large bearish candles, close in lower 30% of range
    increasing_sell_vol  — red-candle volume growing (sellers gaining strength)
    vol_on_down          — volume expanding on down-moves (conviction selling)

  Lagging (broad confirmation):
    macd_crossover_bear  — MACD histogram just crossed zero downward
    adx_bearish          — ADX > 25 and -DI > +DI (strong downtrend confirmed)
    rsi_overbought       — RSI > 68 (entering distribution zone)

Usage:
  python short_scanner.py
  python short_scanner.py --account 96700
  python short_scanner.py --top 200
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
# PATHS
# ─────────────────────────────────────────────────────────────────────────────
_ENGINE_DIR   = Path(__file__).resolve().parent
_PYTHON_DIR   = _ENGINE_DIR.parent
_PROJECT_ROOT = _PYTHON_DIR.parent
_CACHE_DIR    = _PROJECT_ROOT / "cache"   / "shared_ohlcv"
_OUTPUT_DIR   = _PROJECT_ROOT / "outputs" / "scanner-results"
_LOG_DIR      = _PROJECT_ROOT / "outputs" / "logs"

for d in (_CACHE_DIR, _OUTPUT_DIR, _LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
_log_file = _LOG_DIR / f"short_scanner_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("short_scanner")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

ACCOUNT = {
    "size_usdt":          96_700.0,
    "risk_per_trade_pct":      1.5,   # % of account risked per trade
    "max_positions":             6,   # fewer simultaneous shorts than longs
    "max_heat_pct":            9.0,   # tighter total heat for shorts
    "max_single_pos_pct":     10.0,
}

SCAN = {
    "top_n_coins":          400,      # scan rank 50–400 (need perp liquidity)
    "min_rank":              50,
    "max_rank":             400,
    "min_volume_24h":  1_000_000,     # higher floor — need liquid perp markets
    "min_price":          0.0001,
    "min_7d_pct":           -40.0,    # don't short things already in freefall
    "max_7d_pct":            60.0,    # don't short massive breakouts blindly
    "cache_max_age_h":        4.0,
    "api_delay_s":    (1.2 if os.environ.get("CG_API_KEY") else
                       4.5 if os.environ.get("CG_DEMO_KEY") else 6.5),
    "min_atr_pct":            0.5,    # need volatility to make the trade worthwhile
}

SIGNAL = {
    # ── RSI ──────────────────────────────────────────────────────────────────
    "rsi_overbought":        68,      # RSI above this = entering distribution zone
    "divergence_window":     30,      # bars to scan for bearish divergence
    "divergence_price_gap":  1.02,    # price-high-2 must be ≥ 1.02 × price-high-1
    "divergence_rsi_gap":    5.0,     # RSI at recent high must be N pts BELOW prior high

    # ── MACD ─────────────────────────────────────────────────────────────────
    "macd_turning_bars":      3,      # histogram must fall for N bars from peak

    # ── Trend / structure ─────────────────────────────────────────────────────
    "adx_min":               25,
    "rs_vs_btc_min":        -0.03,    # must underperform BTC by 3%+ (7d)

    # ── Volume ───────────────────────────────────────────────────────────────
    "vol_expansion_recent":   6,      # recent bars (6 × 4h ≈ 24h)
    "vol_expansion_base_start": 7,
    "vol_expansion_base_end":  42,
    "vol_expansion_mult":     1.5,
    "sell_vol_increase":      1.20,   # recent red-candle vol ≥ 1.2× earlier (growing)

    # ── Funding ──────────────────────────────────────────────────────────────
    "funding_crowded":       0.001,   # > 0.1% per 8h = crowded long = short fuel

    # ── Trade management ─────────────────────────────────────────────────────
    "atr_stop_mult":          1.5,    # stop = entry + (ATR × mult) — ABOVE entry
    "stop_min_pct":           5.0,    # stop never tighter than 5% above entry
    "stop_max_pct":          15.0,    # stop never wider than 15% above entry
    "tp_rr":          [2.0, 3.0, 5.0],
    "tp_exit_pct":    [ 30,  40,  30],

    # ── Qualification ─────────────────────────────────────────────────────────
    "min_signals":            4,      # raw signal count floor
    "whale_candle_mult":      2.0,
    "higher_lows_window":    30,
}

MACRO = {
    "bull_7d_pct":                3.0,
    "neutral_7d_pct":            -7.0,
    # Regime-specific conviction thresholds for shorts
    "bull_min_conviction":        55,   # high bar — going against bull trend
    "sideways_min_conviction":    42,   # normal
    "bear_min_conviction":        35,   # easier — trend is your friend
}

STABLECOINS = {
    "USDT", "USDC", "DAI", "BUSD", "TUSD", "USDD", "FDUSD", "PYUSD",
    "USDE", "SUSDE", "BFUSD", "RLUSD", "USDG", "USD0", "GHO", "USDAI",
    "WBTC", "WETH", "STETH", "RETH", "CBETH", "PAXG", "XAUT", "TBTC",
    "WBNB", "JITOSOL", "MSOL", "BNSOL", "EURC", "FRAX", "LUSD", "SUSD",
}

# ─────────────────────────────────────────────────────────────────────────────
# SHORT SIGNAL WEIGHTS
# ─────────────────────────────────────────────────────────────────────────────
_SHORT_WEIGHTS = {
    # ── Early / pre-trend (highest weights) ──────────────────────────────────
    "rsi_divergence_bear":  3.0,   # price higher-high, RSI lower-high — earliest
    "funding_crowded":      2.5,   # crowded longs = trapped = short fuel
    "stealth_distrib":      2.5,   # OBV falling while price flat (smart money out)
    "macd_turning_bear":    2.5,   # histogram falling from peak — pre-crossover
    "rs_vs_btc_neg":        2.0,   # underperforming BTC (alpha going elsewhere)
    "rs_decel":             2.0,   # recent momentum fading vs 7-day baseline
    # ── Structure / trend ────────────────────────────────────────────────────
    "lower_highs":          2.0,   # descending swing highs structure
    "bear_candles":         2.0,   # large bearish candles, close in lower 30%
    "increasing_sell_vol":  1.5,   # red-candle volume growing (sellers strengthening)
    "vol_on_down":          1.5,   # volume expands on down moves (conviction selling)
    # ── Lagging confirmation ──────────────────────────────────────────────────
    "macd_crossover_bear":  1.5,   # MACD just crossed zero downward
    "adx_bearish":          1.0,   # ADX > 25 and -DI > +DI
    "rsi_overbought":       0.5,   # RSI > 68 — distribution zone
}
_TOTAL_SHORT_WEIGHT = sum(_SHORT_WEIGHTS.values())

# ─────────────────────────────────────────────────────────────────────────────
# API SESSIONS
# ─────────────────────────────────────────────────────────────────────────────
_CG_KEY     = os.environ.get("CG_API_KEY", "") or os.environ.get("CG_DEMO_KEY", "")
_CG_HEADERS = {"x-cg-pro-api-key": _CG_KEY} if os.environ.get("CG_API_KEY") else \
              {"x-cg-demo-api-key": _CG_KEY} if os.environ.get("CG_DEMO_KEY") else {}

_CG_SESSION = requests.Session()
_CG_SESSION.headers.update({**_CG_HEADERS, "User-Agent": "crypto-short-scanner/1.0"})

_BN_SESSION = requests.Session()
_BN_SESSION.headers.update({"User-Agent": "crypto-short-scanner/1.0"})

_BB_SESSION = requests.Session()
_BB_SESSION.headers.update({"User-Agent": "crypto-short-scanner/1.0"})

_CG_BASE = "https://api.coingecko.com/api/v3"
_BN_BASE = "https://api.binance.com/api/v3"
_BB_BASE = "https://api.bybit.com/v5"        # ByBit v5 — perp symbols + funding rates

# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def _rsi_series(closes: pd.Series, window: int = 14) -> pd.Series:
    delta = closes.diff()
    gain  = delta.clip(lower=0).rolling(window).mean()
    loss  = (-delta.clip(upper=0)).rolling(window).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _rsi(closes: pd.Series, window: int = 14) -> float:
    s = _rsi_series(closes, window)
    return float(s.iloc[-1]) if not s.empty else np.nan


def _macd_hist_series(closes: pd.Series, fast=12, slow=26, signal=9) -> pd.Series:
    ema_f = closes.ewm(span=fast,   adjust=False).mean()
    ema_s = closes.ewm(span=slow,   adjust=False).mean()
    macd  = ema_f - ema_s
    sig   = macd.ewm(span=signal,   adjust=False).mean()
    return macd - sig


def _obv(closes: pd.Series, volumes: pd.Series) -> pd.Series:
    direction = np.sign(closes.diff().fillna(0))
    return (direction * volumes).cumsum()


def _atr(highs: pd.Series, lows: pd.Series, closes: pd.Series, window: int = 14) -> float:
    prev_close = closes.shift(1)
    tr = pd.concat([
        highs - lows,
        (highs - prev_close).abs(),
        (lows  - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_s = tr.rolling(window).mean()
    return float(atr_s.iloc[-1]) if not atr_s.empty else np.nan


def _adx(highs: pd.Series, lows: pd.Series, closes: pd.Series, window: int = 14) -> tuple[float, float, float]:
    """Returns (ADX, +DI, -DI)."""
    if len(closes) < window * 2:
        return np.nan, np.nan, np.nan
    up   = highs.diff()
    down = -lows.diff()
    plus_dm  = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    prev_close = closes.shift(1)
    tr = pd.concat([highs - lows, (highs - prev_close).abs(), (lows - prev_close).abs()], axis=1).max(axis=1)
    atr_s    = tr.rolling(window).mean()
    plus_di  = 100 * pd.Series(plus_dm,  index=closes.index).rolling(window).mean() / atr_s.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=closes.index).rolling(window).mean() / atr_s.replace(0, np.nan)
    dx       = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx_val  = dx.rolling(window).mean()
    return (
        float(adx_val.iloc[-1])  if not adx_val.isna().all()  else np.nan,
        float(plus_di.iloc[-1])  if not plus_di.isna().all()  else np.nan,
        float(minus_di.iloc[-1]) if not minus_di.isna().all() else np.nan,
    )


def _rsi_bearish_divergence(
    closes: pd.Series,
    window: int   = 30,
    rsi_window: int = 9,
    price_gap: float = 1.02,
    rsi_gap:   float = 5.0,
) -> bool:
    """
    Bearish RSI divergence: price makes a higher high in the second half of
    `window` bars while RSI at that high is LOWER than RSI at the first-half
    high. Indicates weakening momentum at new price highs — distribution signal.
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
    idx1 = int(np.nanargmax(c1.values))
    idx2 = int(np.nanargmax(c2.values))
    p_hi1, p_hi2 = float(c1.iloc[idx1]), float(c2.iloc[idx2])
    r_hi1, r_hi2 = float(r1.iloc[idx1]), float(r2.iloc[idx2])
    if any(np.isnan(v) for v in (p_hi1, p_hi2, r_hi1, r_hi2)):
        return False
    # Price made a higher high BUT RSI is lower — divergence confirmed
    return p_hi2 >= p_hi1 * price_gap and r_hi2 <= r_hi1 - rsi_gap


def _lower_highs(highs: pd.Series, window: int = 30) -> bool:
    """
    True when the last 3 swing highs (local maxima in a ±3 bar window)
    within `window` bars are each lower than the one before.
    Signals descending supply — pre-trend structure for shorts.
    """
    if len(highs) < window:
        return False
    recent = highs.iloc[-window:]
    swings = [
        float(recent.iloc[i])
        for i in range(3, len(recent) - 3)
        if float(recent.iloc[i]) == float(recent.iloc[i-3:i+4].max())
    ]
    return len(swings) >= 3 and swings[-1] < swings[-2] < swings[-3]


def _increasing_sell_volume(
    ohlcv:     pd.DataFrame,
    window:    int   = 10,
    increase:  float = 1.20,
) -> bool:
    """
    True when average red-candle volume in the recent half of `window`
    is ≥ `increase` × the earlier half. Growing sell pressure.
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
    return float(np.mean(rv_l)) >= float(np.mean(rv_e)) * increase


def _vol_expansion_on_down(
    ohlcv:          pd.DataFrame,
    recent_bars:    int   = 6,
    baseline_start: int   = 7,
    baseline_end:   int   = 42,
    multiplier:     float = 1.5,
) -> bool:
    """
    True when recent volume is ≥ multiplier × baseline AND price is trending
    down in the recent period (more than half recent bars are red).
    """
    if "volume" not in ohlcv.columns or len(ohlcv) < baseline_end + 1:
        return False
    vols    = ohlcv["volume"].dropna()
    recent  = vols.iloc[-recent_bars:]
    baseline = vols.iloc[-(baseline_end):-(baseline_start)]
    if len(recent) < 2 or len(baseline) < 5:
        return False
    vol_ok = float(recent.mean()) >= float(baseline.mean()) * multiplier
    # Confirm selling pressure: majority of recent bars are red
    recent_ohlcv = ohlcv.iloc[-recent_bars:]
    red_bars = (recent_ohlcv["close"] < recent_ohlcv["open"]).sum()
    return vol_ok and red_bars >= recent_bars * 0.5


# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCHING
# ─────────────────────────────────────────────────────────────────────────────

def fetch_bybit_perp_symbols() -> set[str]:
    """
    Fetch all actively-trading linear (USDT perpetual) symbols from ByBit v5.
    Endpoint: GET /v5/market/instruments-info?category=linear
    Returns a set of base asset symbols e.g. {'BTC', 'ETH', 'ANKR', ...}
    """
    symbols: set[str] = set()
    cursor  = ""
    while True:
        try:
            params = {"category": "linear", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            r = _BB_SESSION.get(
                f"{_BB_BASE}/market/instruments-info",
                params=params,
                timeout=10,
            )
            if r.status_code != 200:
                break
            body   = r.json()
            result = body.get("result", {})
            for item in result.get("list", []):
                if (
                    item.get("quoteCoin") == "USDT"
                    and item.get("contractType") == "LinearPerpetual"
                    and item.get("status") == "Trading"
                ):
                    symbols.add(item["baseCoin"].upper())
            cursor = result.get("nextPageCursor", "")
            if not cursor:
                break
        except Exception:
            break

    if not symbols:
        log.warning("Could not fetch ByBit perp symbols — no perp filter applied.")
    return symbols


def fetch_funding_rate(symbol: str) -> float | None:
    """
    Latest funding rate from ByBit linear perpetuals.
    Endpoint: GET /v5/market/tickers?category=linear&symbol={symbol}USDT
    Returns the current funding rate as a float (e.g. 0.0001 = 0.01% per 8h).
    """
    try:
        r = _BB_SESSION.get(
            f"{_BB_BASE}/market/tickers",
            params={"category": "linear", "symbol": f"{symbol}USDT"},
            timeout=5,
        )
        if r.status_code == 200:
            items = r.json().get("result", {}).get("list", [])
            if items:
                return float(items[0].get("fundingRate", 0))
    except Exception:
        pass
    return None


def fetch_btc_data(days: int = 30) -> pd.DataFrame | None:
    """Fetch BTC 4h OHLCV from Binance for regime classification."""
    cache = _CACHE_DIR / "BTC_regime_4h.csv"
    if cache.exists() and (time.time() - cache.stat().st_mtime) / 3600 < SCAN["cache_max_age_h"]:
        try:
            df = pd.read_csv(cache, index_col=0, parse_dates=True)
            if len(df) >= 42:
                return df
        except Exception:
            pass
    try:
        bars  = days * 6
        r = _BN_SESSION.get(f"{_BN_BASE}/klines",
            params={"symbol": "BTCUSDT", "interval": "4h", "limit": min(bars, 1000)},
            timeout=15)
        if r.status_code != 200:
            return None
        rows = r.json()
        df = pd.DataFrame(rows, columns=[
            "ts","open","high","low","close","base_vol",
            "close_time","volume","trades","taker_base","taker_quote","ignore",
        ])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms")
        df = df.set_index("ts")
        for col in ["open","high","low","close","volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df[["open","high","low","close","volume"]].dropna()
        df.to_csv(cache)
        return df
    except Exception:
        return None


def fetch_market_coins(top_n: int = 400) -> list[dict]:
    """Fetch top_n coins from CoinGecko markets endpoint."""
    coins = []
    page  = 1
    while len(coins) < top_n:
        for attempt in range(3):
            try:
                r = _CG_SESSION.get(
                    f"{_CG_BASE}/coins/markets",
                    params={
                        "vs_currency": "usd",
                        "order":       "market_cap_desc",
                        "per_page":    250,
                        "page":        page,
                        "price_change_percentage": "7d,24h",
                    },
                    timeout=20,
                )
                if r.status_code == 429:
                    wait = int(r.headers.get("Retry-After", 60))
                    log.warning(f"Rate-limited — waiting {wait}s")
                    time.sleep(wait)
                    continue
                if r.status_code != 200:
                    return coins
                data = r.json()
                if not data:
                    return coins
                coins.extend(data)
                break
            except Exception:
                time.sleep(5)
        page += 1
        if len(coins) >= top_n:
            break
    return coins[:top_n]


def fetch_ohlcv(coin_id: str, symbol: str) -> pd.DataFrame | None:
    """
    Fetch 4h OHLCV — Binance first (real intraday data), CoinGecko fallback.
    Uses shared cache with master_orchestrator.
    """
    cache_file = _CACHE_DIR / f"{coin_id}_4h.csv"
    if cache_file.exists():
        age_h = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_h < SCAN["cache_max_age_h"]:
            try:
                df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
                if len(df) >= 60:
                    return df
            except Exception:
                pass

    # Try Binance
    try:
        r = _BN_SESSION.get(f"{_BN_BASE}/klines",
            params={"symbol": f"{symbol}USDT", "interval": "4h", "limit": 200},
            timeout=10)
        if r.status_code == 200 and r.json():
            rows = r.json()
            df = pd.DataFrame(rows, columns=[
                "ts","open","high","low","close","base_vol",
                "close_time","volume","trades","taker_base","taker_quote","ignore",
            ])
            df["ts"] = pd.to_datetime(df["ts"], unit="ms")
            df = df.set_index("ts")
            for col in ["open","high","low","close","volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df[["open","high","low","close","volume"]].dropna()
            if len(df) >= 60:
                df.to_csv(cache_file)
                return df
    except Exception:
        pass

    # CoinGecko fallback
    try:
        r = _CG_SESSION.get(
            f"{_CG_BASE}/coins/{coin_id}/ohlc",
            params={"vs_currency": "usd", "days": 60},
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            if data:
                df = pd.DataFrame(data, columns=["ts","open","high","low","close"])
                df["ts"] = pd.to_datetime(df["ts"], unit="ms")
                df = df.set_index("ts").sort_index()
                df["volume"] = np.nan
                for col in ["open","high","low","close"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna(subset=["open","high","low","close"])
                if len(df) >= 30:
                    df.to_csv(cache_file)
                    return df
    except Exception:
        pass

    return None


# ─────────────────────────────────────────────────────────────────────────────
# BEARISH SIGNAL DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def detect_short_signals(
    ohlcv:        pd.DataFrame,
    btc_7d_pct:   float,
    price:        float,
    funding_rate: float | None = None,
) -> dict | None:
    """
    Run all 13 bearish signal layers.
    Returns dict with boolean flags, scalar values, and short conviction score.
    Returns None if data is insufficient.
    """
    if ohlcv is None or len(ohlcv) < 30:
        return None

    closes  = ohlcv["close"]
    highs   = ohlcv["high"]
    lows    = ohlcv["low"]
    opens   = ohlcv["open"]
    has_vol = "volume" in ohlcv.columns and not ohlcv["volume"].isna().all()
    vols    = ohlcv["volume"] if has_vol else pd.Series(np.nan, index=closes.index)

    s = {}

    try:
        # ── 1. RSI overbought ─────────────────────────────────────────────────
        rsi_val = _rsi(closes)
        s["rsi_overbought"] = not np.isnan(rsi_val) and rsi_val > SIGNAL["rsi_overbought"]
        s["rsi_value"]      = round(rsi_val, 1) if not np.isnan(rsi_val) else None

        # ── 2. RSI bearish divergence ─────────────────────────────────────────
        s["rsi_divergence_bear"] = _rsi_bearish_divergence(
            closes,
            window     = SIGNAL["divergence_window"],
            rsi_window = 9,
            price_gap  = SIGNAL["divergence_price_gap"],
            rsi_gap    = SIGNAL["divergence_rsi_gap"],
        )

        # ── 3. MACD turning bearish (pre-crossover) ───────────────────────────
        macd_hist = _macd_hist_series(closes)
        macd_turn_bear = False
        n_bars = SIGNAL["macd_turning_bars"]
        if len(macd_hist.dropna()) >= n_bars + 2:
            recent_hist = macd_hist.iloc[-n_bars-1:]
            # Histogram positive (not yet crossed) and falling for n_bars
            if float(recent_hist.iloc[-1]) > -0.0001:  # still above (or near) zero
                peak_idx = int(recent_hist.values.argmax())
                if peak_idx < len(recent_hist) - 1:  # peak is not the latest bar
                    vals_after_peak = recent_hist.iloc[peak_idx:]
                    macd_turn_bear = bool(
                        len(vals_after_peak) >= n_bars
                        and all(vals_after_peak.iloc[i] >= vals_after_peak.iloc[i+1]
                                for i in range(len(vals_after_peak)-1))
                    )
        s["macd_turning_bear"] = macd_turn_bear

        # ── 4. MACD bearish crossover ─────────────────────────────────────────
        s["macd_crossover_bear"] = (
            len(macd_hist.dropna()) >= 2
            and float(macd_hist.iloc[-1]) < 0
            and float(macd_hist.iloc[-2]) >= 0
        )

        # ── 5. ADX bearish ────────────────────────────────────────────────────
        adx_val, plus_di, minus_di = _adx(highs, lows, closes)
        s["adx_bearish"] = (
            not any(np.isnan(v) for v in (adx_val, plus_di, minus_di))
            and adx_val > SIGNAL["adx_min"]
            and minus_di > plus_di
        )
        s["adx_value"]  = round(adx_val, 1) if not np.isnan(adx_val) else None

        # ── 6. RS vs BTC negative ─────────────────────────────────────────────
        # token 7d return relative to BTC 7d return (passed in from market data)
        token_7d_pct = float((closes.iloc[-1] / closes.iloc[max(-42, -len(closes))] - 1) * 100)
        rs_vs_btc    = (token_7d_pct - btc_7d_pct) / 100
        s["rs_vs_btc_neg"]  = rs_vs_btc <= SIGNAL["rs_vs_btc_min"]
        s["rs_value"]       = round(rs_vs_btc * 100, 2)

        # ── 7. RS deceleration ────────────────────────────────────────────────
        # Short-term RS (7 bars = 28h) worse than 42-bar RS
        rs_7bar  = float((closes.iloc[-1] / closes.iloc[max(-7,  -len(closes))] - 1) * 100)
        rs_42bar = float((closes.iloc[-1] / closes.iloc[max(-42, -len(closes))] - 1) * 100)
        btc_7bar = btc_7d_pct * (7 / 42)   # approximate short-term BTC return
        rel_7  = rs_7bar  - btc_7bar
        rel_42 = rs_42bar - btc_7d_pct
        s["rs_decel"] = rel_7 < rel_42 - 2.0   # recent RS clearly worse than 7d baseline

        # ── 8. Stealth distribution ───────────────────────────────────────────
        stealth_dist = False
        if has_vol:
            valid = ohlcv[["close", "volume"]].dropna()
            if len(valid) >= 10:
                obv       = _obv(valid["close"], valid["volume"])
                price_chg = (valid["close"].iloc[-1] / valid["close"].iloc[-10]) - 1
                avg_vol   = float(valid["volume"].mean())
                obv_chg   = (obv.iloc[-1] - obv.iloc[-10]) / (avg_vol * 10 + 1)
                # OBV falling while price flat = smart money exiting
                stealth_dist = obv_chg < -0.015 and abs(price_chg) < 0.02
        s["stealth_distrib"] = stealth_dist

        # ── 9. Bear candles ───────────────────────────────────────────────────
        bear_whale = False
        if len(closes) >= 20:
            ranges    = highs - lows
            avg_range = ranges.rolling(20).mean()
            rng5      = ranges.iloc[-5:]
            avg5      = avg_range.iloc[-5:]
            o5        = opens.iloc[-5:]
            c5        = closes.iloc[-5:]
            h5        = highs.iloc[-5:]
            pos5      = (h5 - c5) / rng5.clip(lower=1e-12)   # 0=closed at top, 1=closed at bottom
            bearish_whale = (
                (rng5 > avg5 * SIGNAL["whale_candle_mult"]) &
                (c5   < o5) &      # red candle
                (pos5 >= 0.70)     # close in lower 30%
            )
            bear_whale = bool(bearish_whale.any())
        s["bear_candles"] = bear_whale

        # ── 10. Increasing sell volume ─────────────────────────────────────────
        s["increasing_sell_vol"] = (
            _increasing_sell_volume(ohlcv, window=10, increase=SIGNAL["sell_vol_increase"])
            if has_vol else False
        )

        # ── 11. Volume expansion on down moves ────────────────────────────────
        s["vol_on_down"] = (
            _vol_expansion_on_down(
                ohlcv,
                recent_bars    = SIGNAL["vol_expansion_recent"],
                baseline_start = SIGNAL["vol_expansion_base_start"],
                baseline_end   = SIGNAL["vol_expansion_base_end"],
                multiplier     = SIGNAL["vol_expansion_mult"],
            )
            if has_vol else False
        )

        # ── 12. Lower highs ───────────────────────────────────────────────────
        s["lower_highs"] = _lower_highs(highs, window=SIGNAL["higher_lows_window"])

        # ── 13. Funding rate — crowded long ───────────────────────────────────
        if funding_rate is not None:
            s["funding_crowded"] = funding_rate > SIGNAL["funding_crowded"]
            s["funding_value"]   = round(funding_rate * 100, 4)
        else:
            s["funding_crowded"] = False
            s["funding_value"]   = None

    except Exception as e:
        log.debug(f"Signal error: {e}")
        return None

    # ── Conviction score ──────────────────────────────────────────────────────
    score = sum(
        w for k, w in _SHORT_WEIGHTS.items()
        if s.get(k, False)
    )
    # Crowded funding bonus: +5 conviction on top of weight (extreme short setup)
    funding_bonus = 5.0 if s.get("funding_crowded") and s.get("funding_value", 0) and \
                    s["funding_value"] > 0.15 else 0.0
    conviction = max(0.0, round((score / _TOTAL_SHORT_WEIGHT) * 100 + funding_bonus, 1))

    active = [k for k in _SHORT_WEIGHTS if s.get(k, False)]
    s["conviction"]    = conviction
    s["signal_count"]  = len(active)
    s["active_signals"] = active

    return s


# ─────────────────────────────────────────────────────────────────────────────
# SHORT TRADE PLAN BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_short_plan(
    symbol:       str,
    rank:         int,
    entry:        float,
    ohlcv:        pd.DataFrame,
    account_size: float,
    regime:       str = "SIDEWAYS",
) -> dict:
    """
    Build a short trade plan:
      Stop    = entry + ATR × mult  (ABOVE entry)
      TP1/2/3 = entry - risk × R    (BELOW entry)
    """
    atr_val     = _atr(ohlcv["high"], ohlcv["low"], ohlcv["close"])
    raw_stop_pct = (atr_val / entry) * 100 * SIGNAL["atr_stop_mult"] if entry > 0 else 10.0

    # Clamp stop
    if raw_stop_pct < SIGNAL["stop_min_pct"]:
        stop_pct = SIGNAL["stop_min_pct"]
    elif raw_stop_pct > SIGNAL["stop_max_pct"]:
        stop_pct = SIGNAL["stop_max_pct"]
    else:
        stop_pct = raw_stop_pct

    stop         = entry * (1 + stop_pct / 100)   # ABOVE entry for shorts
    risk_per_unit = stop - entry                   # always positive

    # Position sizing
    risk_usd  = account_size * (ACCOUNT["risk_per_trade_pct"] / 100)
    quantity  = risk_usd / risk_per_unit if risk_per_unit > 0 else 0
    pos_value = quantity * entry
    pos_pct   = (pos_value / account_size) * 100

    if pos_pct > ACCOUNT["max_single_pos_pct"]:
        pos_pct   = ACCOUNT["max_single_pos_pct"]
        pos_value = account_size * (pos_pct / 100)
        quantity  = pos_value / entry
        risk_usd  = quantity * risk_per_unit

    # Take profits — BELOW entry
    tps = []
    for rr, sell_pct in zip(SIGNAL["tp_rr"], SIGNAL["tp_exit_pct"]):
        tp_price    = entry - (risk_per_unit * rr)
        tp_gain_pct = ((entry - tp_price) / entry) * 100   # positive = profit on short
        tp_qty      = quantity * (sell_pct / 100)
        tp_usdt     = tp_qty * tp_price
        tps.append({
            "price":    round(tp_price,    8),
            "gain_pct": round(tp_gain_pct, 1),
            "rr":       rr,
            "sell_pct": sell_pct,
            "usdt":     round(tp_usdt,     2),
        })

    # Expected value — shorts historically lower win rate than longs
    _regime_win_rates = {"BULL": 0.28, "SIDEWAYS": 0.38, "BEAR": 0.48}
    win_prob  = _regime_win_rates.get(regime, 0.38)
    avg_gain  = sum(tp["gain_pct"] for tp in tps) / len(tps)
    avg_loss  = stop_pct
    ev_pct    = round((win_prob * avg_gain) - ((1 - win_prob) * avg_loss), 2)

    return {
        "symbol":       symbol,
        "rank":         rank,
        "direction":    "SHORT",
        "entry":        round(entry,     8),
        "stop":         round(stop,      8),
        "stop_pct":     round(stop_pct,  1),
        "risk_usd":     round(risk_usd,  2),
        "risk_pct":     round((risk_usd / account_size) * 100, 2),
        "quantity":     round(quantity,  4),
        "pos_value":    round(pos_value, 2),
        "pos_pct":      round(pos_pct,   1),
        "take_profits": tps,
        "ev_pct":       ev_pct,
    }


# ─────────────────────────────────────────────────────────────────────────────
# REPORT BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_report(
    setups:       list[dict],
    watchlist:    list[dict],
    btc_price:    float,
    btc_7d:       float,
    btc_24h:      float,
    regime:       str,
    account_size: float,
    min_conv:     int,
) -> str:
    sep  = "=" * 80
    dash = "-" * 40
    lines = [
        "",
        sep,
        "  SHORT SCANNER v1.0 — BEARISH TRADE PLAN",
        f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        sep,
        "",
        "MARKET CONTEXT",
        dash,
        f"  BTC Price    :  $ {btc_price:>12,.2f}",
        f"  BTC 7-day    :  {btc_7d:>+10.2f}%",
        f"  BTC 24-hour  :  {btc_24h:>+10.2f}%",
        f"  Regime       :  {'🔴 BEAR' if regime == 'BEAR' else '🟡 SIDEWAYS' if regime == 'SIDEWAYS' else '🟢 BULL'}",
        "",
    ]

    if regime == "BEAR":
        lines += [f"  🔴 BEAR — Short setups have highest edge. Min conviction ≥ {min_conv}.", ""]
    elif regime == "SIDEWAYS":
        lines += [f"  🟡 SIDEWAYS — Selective shorts only. Min conviction ≥ {min_conv}.", ""]
    else:
        lines += [
            f"  🟢 BULL — Shorting against the trend. Higher bar: conviction ≥ {min_conv}.",
            "  Only take shorts with very clear distribution signals.",
            "",
        ]

    lines += [
        "ACCOUNT",
        dash,
        f"  Balance       : $ {account_size:>12,.2f} USDT",
        f"  Risk / trade  : {ACCOUNT['risk_per_trade_pct']}%  "
        f"(${account_size * ACCOUNT['risk_per_trade_pct'] / 100:,.0f} USDT per trade)",
        f"  Max positions : {ACCOUNT['max_positions']}",
        f"  Max heat      : {ACCOUNT['max_heat_pct']}%",
        "",
    ]

    if setups:
        lines += [sep, f"  {len(setups)} HIGH-CONVICTION SHORT SETUP(S) FOUND", sep, ""]
        for i, setup in enumerate(setups, 1):
            plan = setup["plan"]
            sig  = setup["signals"]
            active_str = ", ".join(sig["active_signals"][:3])
            if len(sig["active_signals"]) > 3:
                active_str += f" +{len(sig['active_signals'])-3}"

            lines += [
                f"[{i}]  ▼  {setup['symbol']}  (Rank #{setup['rank']})",
                f"     Conviction  : {sig['conviction']:.0f} / 100",
                f"     Signals     : {sig['signal_count']} / {len(_SHORT_WEIGHTS)} active",
                f"     Active      : {active_str}",
                f"     RSI         : {sig.get('rsi_value', 'N/A')}",
                f"     Funding     : {sig.get('funding_value', 'N/A')}% (crowded={'YES ⚡' if sig.get('funding_crowded') else 'no'})",
                f"     RS vs BTC   : {sig.get('rs_value', 'N/A')}%",
                "",
                "     ── SHORT TRADE PLAN ──────────────────────────────",
                f"     Entry (SHORT) : $ {plan['entry']:>14,.6f}",
                f"     Stop (BUY)   : $ {plan['stop']:>14,.6f}  (+{plan['stop_pct']:.1f}%)",
                f"     Risk         : $ {plan['risk_usd']:>10,.2f}  ({plan['risk_pct']:.2f}% of account)",
                f"     Position     : $ {plan['pos_value']:>10,.2f}  ({plan['pos_pct']:.1f}% of account)",
                f"     Quantity     :   {plan['quantity']:>10,.4f}  {setup['symbol']}",
                "",
            ]
            for j, tp in enumerate(plan["take_profits"], 1):
                lines.append(
                    f"     TP{j} ({tp['rr']:.0f}R)  : $ {tp['price']:>14,.6f}"
                    f"  (-{tp['gain_pct']:.1f}%)  → sell {tp['sell_pct']}% = ${tp['usdt']:,.0f}"
                )
            lines += [
                f"     E[V]         :  {plan['ev_pct']:+.2f}% per trade",
                "",
                f"  ⚠️  SHORTING ON BYBIT — use isolated margin, verify liquidation price.",
                f"     Pair: {setup['symbol']}USDT  |  Category: Linear Perpetual",
                dash,
                "",
            ]
    else:
        lines += [
            sep,
            "  NO QUALIFYING SHORT SETUPS FOUND",
            "",
            "  No tokens met the minimum conviction threshold for shorting.",
            "  This is normal — clear short setups are rarer than long setups.",
            sep,
        ]

    # Watchlist
    if watchlist:
        lines += [
            "",
            "SHORT WATCHLIST — building bearish conviction, not ready yet",
            dash,
            f"  {'#':<4}  {'SYMBOL':<10}  {'RANK':>4}  {'PRICE':>12}  {'7d%':>7}  {'CONV':>5}  {'SIGS':>4}  KEY SIGNALS",
            "  " + "-" * 75,
        ]
        for i, w in enumerate(watchlist[:10], 1):
            sig    = w["signals"]
            active = ", ".join(sig["active_signals"][:3])
            if len(sig["active_signals"]) > 3:
                active += f" +{len(sig['active_signals'])-3}"
            lines.append(
                f"  {i:<4}  {w['symbol']:<10}  #{w['rank']:<4}  "
                f"${w['price']:>11,.5f}  {w['change_7d']:>+6.1f}%  "
                f"{sig['conviction']:>4.0f}  {sig['signal_count']:>4}  {active}"
            )

    lines += ["", dash]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN SCAN
# ─────────────────────────────────────────────────────────────────────────────

def run(account_size: float | None = None) -> None:
    account_size = account_size or ACCOUNT["size_usdt"]
    t0           = datetime.now()

    log.info("")
    log.info("=" * 80)
    log.info("  SHORT SCANNER v1.0")
    log.info("  Scanning for high-conviction BEARISH setups on Binance perps")
    log.info("=" * 80)

    # ── 1. Regime ─────────────────────────────────────────────────────────────
    log.info("\n[1/5] Analyzing market regime...")
    btc_df = fetch_btc_data(30)
    if btc_df is None:
        log.error("Cannot fetch BTC data. Aborting.")
        return

    btc_price = float(btc_df["close"].iloc[-1])
    btc_7d    = float((btc_df["close"].iloc[-1] / btc_df["close"].iloc[max(-42, -len(btc_df))] - 1) * 100)
    btc_24h   = float((btc_df["close"].iloc[-1] / btc_df["close"].iloc[max(-6,  -len(btc_df))] - 1) * 100)

    if btc_7d >= MACRO["bull_7d_pct"]:
        regime  = "BULL"
        min_conv = MACRO["bull_min_conviction"]
    elif btc_7d >= MACRO["neutral_7d_pct"]:
        regime  = "SIDEWAYS"
        min_conv = MACRO["sideways_min_conviction"]
    else:
        regime  = "BEAR"
        min_conv = MACRO["bear_min_conviction"]

    log.info(f"  BTC ${btc_price:,.0f}  |  7d {btc_7d:+.2f}%  |  Regime: {regime}")
    log.info(f"  Min conviction for shorts: {min_conv}")

    # ── 2. ByBit perp universe ────────────────────────────────────────────────
    log.info("\n[2/5] Fetching ByBit perp symbols...")
    perp_symbols = fetch_bybit_perp_symbols()
    log.info(f"  {len(perp_symbols)} active ByBit USDT linear perp markets found")

    # ── 3. Market coins ───────────────────────────────────────────────────────
    log.info("\n[3/5] Fetching top coins from CoinGecko...")
    coins = fetch_market_coins(SCAN["top_n_coins"])
    log.info(f"  {len(coins)} coins fetched")

    # ── 4. Scan ───────────────────────────────────────────────────────────────
    log.info(f"\n[4/5] Scanning for bearish setups (min conviction {min_conv})...\n")

    setups    = []
    watchlist = []

    for i, coin in enumerate(coins, 1):
        symbol    = coin.get("symbol", "").upper()
        coin_id   = coin.get("id", "")
        rank      = coin.get("market_cap_rank") or 9999
        price     = float(coin.get("current_price") or 0)
        vol_24h   = float(coin.get("total_volume") or 0)
        change_7d = float(coin.get("price_change_percentage_7d_in_currency") or 0)
        change_24h = float(coin.get("price_change_percentage_24h") or 0)

        # Basic filters
        if symbol in STABLECOINS:
            continue
        if rank < SCAN["min_rank"] or rank > SCAN["max_rank"]:
            continue
        if price < SCAN["min_price"]:
            continue
        if vol_24h < SCAN["min_volume_24h"]:
            continue
        if change_7d < SCAN["min_7d_pct"] or change_7d > SCAN["max_7d_pct"]:
            continue

        # Must have a Binance perp market (can actually be shorted)
        if perp_symbols and symbol not in perp_symbols:
            continue

        log.info(f"  [{i}]  {symbol:<10} (#{rank})  ${price:.5f}  7d: {change_7d:+.1f}%")

        ohlcv = fetch_ohlcv(coin_id, symbol)
        if ohlcv is None or len(ohlcv) < 30:
            log.info(f"          → skip (insufficient OHLCV data)")
            time.sleep(SCAN["api_delay_s"])
            continue

        # ATR filter
        atr_val = _atr(ohlcv["high"], ohlcv["low"], ohlcv["close"])
        atr_pct = (atr_val / price * 100) if price > 0 and not np.isnan(atr_val) else 0
        if atr_pct < SCAN["min_atr_pct"]:
            log.info(f"          → skip (flatliner: ATR {atr_pct:.2f}%)")
            continue

        # Funding rate
        funding_rate = None
        if symbol in perp_symbols:
            funding_rate = fetch_funding_rate(symbol)

        # Detect bearish signals
        signals = detect_short_signals(ohlcv, btc_7d, price, funding_rate)
        if signals is None:
            log.info(f"          → skip (signal computation failed)")
            continue

        conv = signals["conviction"]
        nsig = signals["signal_count"]
        log.info(f"          → conviction {conv:.0f}/100  |  signals {nsig}/{len(_SHORT_WEIGHTS)}")

        if conv >= min_conv and nsig >= SIGNAL["min_signals"]:
            plan = build_short_plan(symbol, rank, price, ohlcv, account_size, regime)
            setups.append({
                "symbol":    symbol,
                "rank":      rank,
                "price":     price,
                "change_7d": change_7d,
                "signals":   signals,
                "plan":      plan,
            })
        elif conv >= min_conv * 0.65 and nsig >= SIGNAL["min_signals"] - 1:
            watchlist.append({
                "symbol":    symbol,
                "rank":      rank,
                "price":     price,
                "change_7d": change_7d,
                "signals":   signals,
            })

        time.sleep(SCAN["api_delay_s"])

    # ── 5. Report ─────────────────────────────────────────────────────────────
    log.info(f"\n[5/5] Building short scan report...")
    log.info(f"  Short setups found: {len(setups)}")

    setups.sort(key=lambda x: -x["signals"]["conviction"])
    watchlist.sort(key=lambda x: -x["signals"]["conviction"])

    report = build_report(
        setups, watchlist, btc_price, btc_7d, btc_24h,
        regime, account_size, min_conv,
    )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = _OUTPUT_DIR / f"short_scan_{ts}.txt"
    latest_path = _OUTPUT_DIR / "short_scan_LATEST.txt"
    report_path.write_text(report, encoding="utf-8")
    latest_path.write_text(report, encoding="utf-8")

    # JSON output
    output_json = {
        "generated":   datetime.now().isoformat(),
        "regime":      regime,
        "btc_price":   btc_price,
        "btc_7d":      btc_7d,
        "setups":      [
            {**s, "signals": {k: v for k, v in s["signals"].items()
                               if not isinstance(v, bool) or v}}
            for s in setups
        ],
    }
    json_path = _OUTPUT_DIR / "short_scan_LATEST.json"
    json_path.write_text(json.dumps(output_json, indent=2, default=str), encoding="utf-8")

    log.info(f"\n  Report → {latest_path}")
    log.info(f"  JSON   → {json_path}")
    log.info(report)
    log.info(f"\n  Done.  {len(setups)} short setup(s) found.")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Short Scanner — bearish setups on Binance perps")
    parser.add_argument("--account", type=float, default=None, help="Account size in USDT")
    parser.add_argument("--top",     type=int,   default=None, help="Scan top N coins (default 400)")
    args = parser.parse_args()

    if args.top:
        SCAN["top_n_coins"] = args.top
        SCAN["max_rank"]    = args.top

    run(account_size=args.account)
