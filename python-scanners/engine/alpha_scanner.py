"""
================================================================================
ALPHA BREAKOUT SCANNER  v1.2
================================================================================
Finds tokens GENUINELY DECOUPLING UPWARD from BTC, with tiered regime gates.

The core idea: when BTC is flat or falling and a token is still climbing,
that's real alpha. Someone is accumulating. Follow the strength.

Key differences from master_orchestrator (longs):
  - REGIME GATE (tiered, not binary) — thresholds tighten in SIDEWAYS/BEAR
  - RS vs BTC ≥5% over 7 days is REQUIRED (gates entry completely)
  - Smaller risk per trade: 0.75% (half the normal 1.5%)
  - Tighter stops: ATR×1.2, capped 4%–10%
  - Scans rank 5–300 (needs liquidity, but alpha can appear anywhere)
  - Uses shared OHLCV cache with master_orchestrator

11 bullish signals:
  RS / relative strength (core edge):
    rs_vs_btc_strong  — 7d token outperformance vs BTC ≥ 5%  ← REQUIRED
    rs_accel          — 1-day RS better than 7-day baseline (accelerating)
    rs_sustained      — 3-day RS also positive (not a 1-day spike)

  Momentum / breakout confirmation:
    vol_breakout      — recent volume ≥ 2× 7-day avg (institutional buying)
    macd_bullish      — MACD histogram rising and positive (or crossing zero up)
    higher_lows       — ascending swing lows structure forming

  Volume quality:
    vol_on_up         — up-move volume > down-move volume (smart money buying)
    stealth_accum     — OBV rising while price flat/consolidating

  Broad confirmation (lagging):
    rsi_range         — RSI 45–72 (healthy momentum, room to run)
    adx_bullish       — ADX > 20 with +DI > -DI (trend confirmed)
    supertrend_bull   — price above SuperTrend line (trend direction confirmed)

Usage:
  python alpha_scanner.py
  python alpha_scanner.py --account 96700
  python alpha_scanner.py --top 200
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
_log_file = _LOG_DIR / f"alpha_scanner_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("alpha_scanner")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

ACCOUNT = {
    "size_usdt":          96_700.0,
    "risk_per_trade_pct":      0.75,  # HALF normal — alpha plays are higher risk/reward
    "max_positions":              6,
    "max_heat_pct":             4.5,  # 6 × 0.75% = 4.5% max total heat
    "max_single_pos_pct":       8.0,
}

SCAN = {
    "top_n_coins":          700,
    "min_rank":               5,      # include top coins — they can break out too
    "max_rank":             700,
    "min_volume_24h":   500_000,      # lower floor — some alpha plays have moderate vol
    "min_price":          0.0001,
    "cache_max_age_h":        4.0,
    "api_delay_s":    (1.2 if os.environ.get("CG_API_KEY") else
                       4.5 if os.environ.get("CG_DEMO_KEY") else 6.5),
    "min_atr_pct":            0.3,    # only need modest volatility
}

SIGNAL = {
    # ── RS (relative strength vs BTC) ─────────────────────────────────────────
    "rs_min_7d_pct":           5.0,   # token must outperform BTC by ≥ 5% over 7 days
    "rs_accel_edge":           2.0,   # 1-day RS must beat 7-day RS by this margin (%)
    "rs_3d_min":               1.0,   # 3-day RS must be positive by ≥ 1%

    # ── Volume ────────────────────────────────────────────────────────────────
    "vol_breakout_mult":       2.0,   # recent 24h vol ≥ 2× 7-day avg
    "vol_on_up_bars":         20,     # bars to compare up-vs-down volume
    "stealth_accum_bars":     12,     # bars for OBV analysis

    # ── Structural ────────────────────────────────────────────────────────────
    "higher_lows_window":     30,
    "adx_min":                20,     # lower than short scanner — early trend
    "rsi_min":                45,
    "rsi_max":                72,

    # ── SuperTrend ────────────────────────────────────────────────────────────
    "supertrend_period":      10,     # ATR period for SuperTrend calculation
    "supertrend_mult":       3.0,     # ATR multiplier (3.0 = standard)

    # ── MACD ──────────────────────────────────────────────────────────────────
    "macd_rising_bars":        3,     # histogram must rise for N bars

    # ── Trade management ──────────────────────────────────────────────────────
    "atr_stop_mult":           1.2,   # tighter stop than longs (1.5×)
    "stop_min_pct":            4.0,   # never tighter than 4% below entry
    "stop_max_pct":           10.0,   # never wider than 10% below entry
    "tp_rr":         [1.5, 2.5, 4.0],
    "tp_exit_pct":   [ 40,  35,  25],

    # ── Qualification ─────────────────────────────────────────────────────────
    "min_conviction":         38,     # lower bar because RS pre-filter is strict
    "watchlist_min_conv":     24,
    "min_signals":             4,     # raw signal count floor
}

MACRO = {
    "bull_7d_pct":   3.0,
    "neutral_7d_pct": -7.0,
    # Win rate estimates for alpha breakouts by regime
    "win_rates": {
        "BULL":     0.48,
        "SIDEWAYS": 0.45,   # reduced — April audit showed SIDEWAYS stops out frequently
        "BEAR":     0.35,
    },
}

# ── Regime gate — coordinates alpha scanner with master_orchestrator ──────────
# Lesson from April 3-5 2026: master said STAY OUT while alpha fired SIDEWAYS
# signals. All stopped out. This gate enforces a hierarchy: master regime = filter.
#
# BULL     : normal operation — conviction ≥ 38, risk 0.75%
# SIDEWAYS : tightened — conviction ≥ 70, risk halved to 0.375%, RSI max tightened
# BEAR     : no new longs — scanner runs but outputs watchlist only
REGIME_GATE = {
    "BULL": {
        "min_conviction":    38,     # normal alpha threshold
        "risk_pct":          0.75,   # normal alpha risk (half of master's 1.5%)
        "rsi_max":           72,     # normal RSI ceiling
        "label": "🟢 BULL — Normal alpha parameters.",
    },
    "SIDEWAYS": {
        "min_conviction":    70,     # only the very strongest RS plays
        "risk_pct":          0.375,  # quarter of normal risk
        "rsi_max":           65,     # tighter RSI ceiling in chop
        "label": "🟡 SIDEWAYS — Tightened: conviction ≥ 70, risk halved, RSI ≤ 65.",
    },
    "BEAR": {
        "min_conviction":    999,    # effectively disabled
        "risk_pct":          0.0,
        "rsi_max":           65,
        "label": "🔴 BEAR — No new longs. Watchlist only.",
    },
}

STABLECOINS = {
    "USDT", "USDC", "DAI", "BUSD", "TUSD", "USDD", "FDUSD", "PYUSD",
    "USDE", "SUSDE", "BFUSD", "RLUSD", "USDG", "USD0", "GHO", "USDAI",
    "WBTC", "WETH", "STETH", "RETH", "CBETH", "PAXG", "XAUT", "TBTC",
    "WBNB", "JITOSOL", "MSOL", "BNSOL", "EURC", "FRAX", "LUSD", "SUSD",
}

# Exclude BTC/ETH themselves — we want tokens outperforming them, not them
EXCLUDED_SYMBOLS = {"BTC", "ETH"}

# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL WEIGHTS
# ─────────────────────────────────────────────────────────────────────────────
_ALPHA_WEIGHTS = {
    # ── RS / relative strength (highest weights — the edge) ───────────────────
    "rs_vs_btc_strong":  3.5,   # 7d outperformance ≥ 5% — the whole point
    "rs_accel":          2.5,   # 1d RS accelerating above 7d baseline
    "rs_sustained":      2.0,   # 3d RS positive — not a 1-day fluke
    # ── Momentum / breakout confirmation ─────────────────────────────────────
    "vol_breakout":      2.5,   # institutional volume surge
    "macd_bullish":      2.0,   # MACD histogram rising and positive
    "higher_lows":       2.0,   # ascending structure forming
    # ── Volume quality ────────────────────────────────────────────────────────
    "vol_on_up":         1.5,   # buying pressure dominates
    "stealth_accum":     1.5,   # OBV rising during consolidation
    # ── Broad confirmation (lagging) ──────────────────────────────────────────
    "rsi_range":         1.0,   # healthy momentum zone
    "adx_bullish":       1.0,   # trend strengthening with +DI > -DI
    "supertrend_bull":   1.5,   # price above SuperTrend line
}
_TOTAL_ALPHA_WEIGHT = sum(_ALPHA_WEIGHTS.values())

# ─────────────────────────────────────────────────────────────────────────────
# API SESSIONS
# ─────────────────────────────────────────────────────────────────────────────
_CG_KEY     = os.environ.get("CG_API_KEY", "") or os.environ.get("CG_DEMO_KEY", "")
_CG_HEADERS = {"x-cg-pro-api-key": _CG_KEY} if os.environ.get("CG_API_KEY") else \
              {"x-cg-demo-api-key": _CG_KEY} if os.environ.get("CG_DEMO_KEY") else {}

_CG_SESSION = requests.Session()
_CG_SESSION.headers.update({**_CG_HEADERS, "User-Agent": "crypto-alpha-scanner/1.0"})

_BN_SESSION = requests.Session()
_BN_SESSION.headers.update({"User-Agent": "crypto-alpha-scanner/1.0"})

_CG_BASE = "https://api.coingecko.com/api/v3"
_BN_BASE = "https://api.binance.com/api/v3"

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


def _higher_lows(lows: pd.Series, window: int = 30) -> bool:
    """True when the last 3 swing lows within `window` bars are each higher than the prior one."""
    if len(lows) < window:
        return False
    recent = lows.iloc[-window:]
    swings = [
        float(recent.iloc[i])
        for i in range(3, len(recent) - 3)
        if float(recent.iloc[i]) == float(recent.iloc[i-3:i+4].min())
    ]
    return len(swings) >= 3 and swings[-1] > swings[-2] > swings[-3]


def _supertrend(
    highs: pd.Series,
    lows: pd.Series,
    closes: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
) -> tuple[bool, float]:
    """
    SuperTrend indicator.
    Returns (is_bullish, supertrend_line_value).
    is_bullish = True when price is above the SuperTrend line (uptrend confirmed).
    Uses numpy arrays for performance and to avoid pandas SettingWithCopyWarning.
    """
    n = len(closes)
    if n < period * 2:
        return False, np.nan

    h = highs.values.astype(float)
    l = lows.values.astype(float)
    c = closes.values.astype(float)

    # True Range
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))

    # ATR (simple rolling mean)
    atr = np.zeros(n)
    for i in range(period, n):
        atr[i] = tr[i - period + 1 : i + 1].mean()

    hl2          = (h + l) / 2.0
    upper_basic  = hl2 + multiplier * atr
    lower_basic  = hl2 - multiplier * atr

    upper = upper_basic.copy()
    lower = lower_basic.copy()

    # Carry-forward bands: tighten over time, never widen while trend holds
    for i in range(1, n):
        upper[i] = (
            upper_basic[i]
            if (upper_basic[i] < upper[i - 1] or c[i - 1] > upper[i - 1])
            else upper[i - 1]
        )
        lower[i] = (
            lower_basic[i]
            if (lower_basic[i] > lower[i - 1] or c[i - 1] < lower[i - 1])
            else lower[i - 1]
        )

    # Determine SuperTrend line and trend direction
    st = np.zeros(n)
    in_uptrend = True
    for i in range(period, n):
        if atr[i] == 0:
            continue
        if c[i] > upper[i - 1]:
            in_uptrend = True
        elif c[i] < lower[i - 1]:
            in_uptrend = False
        st[i] = lower[i] if in_uptrend else upper[i]

    last_st = st[-1]
    if last_st == 0 or np.isnan(last_st):
        return False, np.nan

    is_bullish = float(c[-1]) > last_st
    return is_bullish, round(float(last_st), 8)


# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCHING
# ─────────────────────────────────────────────────────────────────────────────

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
        bars = days * 6
        r = _BN_SESSION.get(
            f"{_BN_BASE}/klines",
            params={"symbol": "BTCUSDT", "interval": "4h", "limit": min(bars, 1000)},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        rows = r.json()
        df = pd.DataFrame(rows, columns=[
            "ts", "open", "high", "low", "close", "base_vol",
            "close_time", "volume", "trades", "taker_base", "taker_quote", "ignore",
        ])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms")
        df = df.set_index("ts")
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df[["open", "high", "low", "close", "volume"]].dropna()
        df.to_csv(cache)
        return df
    except Exception:
        return None


def fetch_market_coins(top_n: int = 300) -> list[dict]:
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
    Uses shared cache with master_orchestrator and short_scanner.
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
        r = _BN_SESSION.get(
            f"{_BN_BASE}/klines",
            params={"symbol": f"{symbol}USDT", "interval": "4h", "limit": 200},
            timeout=10,
        )
        if r.status_code == 200 and r.json():
            rows = r.json()
            df = pd.DataFrame(rows, columns=[
                "ts", "open", "high", "low", "close", "base_vol",
                "close_time", "volume", "trades", "taker_base", "taker_quote", "ignore",
            ])
            df["ts"] = pd.to_datetime(df["ts"], unit="ms")
            df = df.set_index("ts")
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df[["open", "high", "low", "close", "volume"]].dropna()
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
                df = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close"])
                df["ts"] = pd.to_datetime(df["ts"], unit="ms")
                df = df.set_index("ts").sort_index()
                df["volume"] = np.nan
                for col in ["open", "high", "low", "close"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna(subset=["open", "high", "low", "close"])
                if len(df) >= 30:
                    df.to_csv(cache_file)
                    return df
    except Exception:
        pass

    return None


# ─────────────────────────────────────────────────────────────────────────────
# ALPHA SIGNAL DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def detect_alpha_signals(
    ohlcv:       pd.DataFrame,
    btc_7d_pct:  float,
    price:       float,
) -> dict | None:
    """
    Run all 10 alpha breakout signal layers.
    Returns dict with boolean flags, scalar values, and alpha conviction score.
    Returns None if data is insufficient.

    IMPORTANT: rs_vs_btc_strong must be True for any setup to qualify.
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
        # ── 1. RS vs BTC — 7-day outperformance ──────────────────────────────
        # Uses CoinGecko 7d change passed in (more reliable than OHLCV bars)
        token_7d_pct = float((closes.iloc[-1] / closes.iloc[max(-42, -len(closes))] - 1) * 100)
        rs_7d        = token_7d_pct - btc_7d_pct          # raw outperformance %
        s["rs_vs_btc_strong"] = rs_7d >= SIGNAL["rs_min_7d_pct"]
        s["rs_7d_value"]      = round(rs_7d, 2)

        # ── 2. RS acceleration (1-day RS vs 7-day baseline) ───────────────────
        # Measures: is the token picking up speed vs BTC right NOW?
        token_1d = float((closes.iloc[-1] / closes.iloc[max(-6, -len(closes))] - 1) * 100)
        btc_1d   = btc_7d_pct * (6 / 42)          # approx BTC 1-day from 7d data
        rs_1d    = token_1d - btc_1d               # 1-day RS
        # Accelerating if 1d RS is materially better than 7d RS on a per-day basis
        rs_7d_daily = rs_7d / 7 if rs_7d != 0 else 0
        s["rs_accel"]      = rs_1d > rs_7d_daily + SIGNAL["rs_accel_edge"]
        s["rs_1d_value"]   = round(rs_1d, 2)

        # ── 3. RS sustained (3-day RS positive) ───────────────────────────────
        token_3d = float((closes.iloc[-1] / closes.iloc[max(-18, -len(closes))] - 1) * 100)
        btc_3d   = btc_7d_pct * (18 / 42)
        rs_3d    = token_3d - btc_3d
        s["rs_sustained"]  = rs_3d >= SIGNAL["rs_3d_min"]
        s["rs_3d_value"]   = round(rs_3d, 2)

        # ── 4. Volume breakout ────────────────────────────────────────────────
        vol_breakout = False
        if has_vol:
            valid_vol = vols.dropna()
            if len(valid_vol) >= 14:
                recent_6  = float(valid_vol.iloc[-6:].mean())    # last 24h (6×4h bars)
                baseline  = float(valid_vol.iloc[-42:-6].mean())  # prior 6 days
                # Also confirm price is going up in the recent period
                recent_green = int((closes.iloc[-6:] > opens.iloc[-6:]).sum())
                vol_breakout = (
                    baseline > 0
                    and recent_6 >= baseline * SIGNAL["vol_breakout_mult"]
                    and recent_green >= 4   # at least 4 of 6 recent bars are green
                )
        s["vol_breakout"] = vol_breakout

        # ── 5. MACD bullish (histogram rising and positive) ───────────────────
        macd_hist = _macd_hist_series(closes)
        macd_bull = False
        n_bars    = SIGNAL["macd_rising_bars"]
        if len(macd_hist.dropna()) >= n_bars + 2:
            recent_hist = macd_hist.iloc[-(n_bars + 1):]
            # Rising for n_bars AND either positive or crossing zero upward
            is_rising = all(
                recent_hist.iloc[i] <= recent_hist.iloc[i + 1]
                for i in range(len(recent_hist) - 1)
            )
            is_positive_or_crossing = float(recent_hist.iloc[-1]) > 0 or (
                float(recent_hist.iloc[-2]) < 0 and float(recent_hist.iloc[-1]) > float(recent_hist.iloc[-2])
            )
            macd_bull = is_rising and is_positive_or_crossing
        s["macd_bullish"]   = macd_bull
        s["macd_hist_last"] = round(float(macd_hist.iloc[-1]), 6) if not macd_hist.empty else None

        # ── 6. Higher lows (ascending structure) ──────────────────────────────
        s["higher_lows"] = _higher_lows(lows, window=SIGNAL["higher_lows_window"])

        # ── 7. Volume on up-moves (buying pressure dominates) ─────────────────
        vol_on_up = False
        if has_vol:
            n = SIGNAL["vol_on_up_bars"]
            recent = ohlcv.iloc[-n:].dropna(subset=["volume", "open", "close"])
            if len(recent) >= 8:
                up_vol   = recent.loc[recent["close"] >= recent["open"], "volume"].sum()
                down_vol = recent.loc[recent["close"] <  recent["open"], "volume"].sum()
                vol_on_up = up_vol > down_vol * 1.2    # buyers 20% more active than sellers
        s["vol_on_up"] = vol_on_up

        # ── 8. Stealth accumulation (OBV rising during consolidation) ─────────
        stealth_acc = False
        if has_vol:
            valid = ohlcv[["close", "volume"]].dropna()
            n = SIGNAL["stealth_accum_bars"]
            if len(valid) >= n:
                obv       = _obv(valid["close"], valid["volume"])
                price_chg = (valid["close"].iloc[-1] / valid["close"].iloc[-n]) - 1
                avg_vol   = float(valid["volume"].mean())
                obv_chg   = (obv.iloc[-1] - obv.iloc[-n]) / (avg_vol * n + 1)
                # OBV rising materially while price is in a tight range
                stealth_acc = obv_chg > 0.015 and abs(price_chg) < 0.05
        s["stealth_accum"] = stealth_acc

        # ── 9. RSI in healthy zone (45–72) ────────────────────────────────────
        rsi_val = _rsi(closes)
        s["rsi_range"] = (
            not np.isnan(rsi_val)
            and SIGNAL["rsi_min"] <= rsi_val <= SIGNAL["rsi_max"]
        )
        s["rsi_value"] = round(rsi_val, 1) if not np.isnan(rsi_val) else None

        # ── 10. ADX bullish (trend strength with +DI > -DI) ───────────────────
        adx_val, plus_di, minus_di = _adx(highs, lows, closes)
        s["adx_bullish"] = (
            not any(np.isnan(v) for v in (adx_val, plus_di, minus_di))
            and adx_val > SIGNAL["adx_min"]
            and plus_di > minus_di
        )
        s["adx_value"]   = round(adx_val,  1) if not np.isnan(adx_val)  else None
        s["plus_di"]     = round(plus_di,  1) if not np.isnan(plus_di)  else None
        s["minus_di"]    = round(minus_di, 1) if not np.isnan(minus_di) else None

        # ── 11. SuperTrend bullish (price above SuperTrend line) ──────────────
        st_bull, st_val = _supertrend(
            highs, lows, closes,
            period=SIGNAL["supertrend_period"],
            multiplier=SIGNAL["supertrend_mult"],
        )
        s["supertrend_bull"]  = st_bull
        s["supertrend_value"] = st_val

    except Exception as e:
        log.debug(f"Signal error: {e}")
        return None

    # ── Conviction score ──────────────────────────────────────────────────────
    score = sum(
        w for k, w in _ALPHA_WEIGHTS.items()
        if s.get(k, False)
    )
    conviction = max(0.0, round((score / _TOTAL_ALPHA_WEIGHT) * 100, 1))

    active = [k for k in _ALPHA_WEIGHTS if s.get(k, False)]
    s["conviction"]     = conviction
    s["signal_count"]   = len(active)
    s["active_signals"] = active

    return s


# ─────────────────────────────────────────────────────────────────────────────
# ALPHA TRADE PLAN BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_alpha_plan(
    symbol:       str,
    rank:         int,
    entry:        float,
    ohlcv:        pd.DataFrame,
    account_size: float,
    regime:       str = "SIDEWAYS",
    risk_pct:     float | None = None,
) -> dict:
    """
    Build an alpha breakout long trade plan:
      Stop    = entry - ATR × mult  (BELOW entry)
      TP1/2/3 = entry + risk × R    (ABOVE entry)
    """
    atr_val      = _atr(ohlcv["high"], ohlcv["low"], ohlcv["close"])
    raw_stop_pct = (atr_val / entry) * 100 * SIGNAL["atr_stop_mult"] if entry > 0 else 7.0

    # Clamp stop
    if raw_stop_pct < SIGNAL["stop_min_pct"]:
        stop_pct = SIGNAL["stop_min_pct"]
    elif raw_stop_pct > SIGNAL["stop_max_pct"]:
        stop_pct = SIGNAL["stop_max_pct"]
    else:
        stop_pct = raw_stop_pct

    stop          = entry * (1 - stop_pct / 100)   # BELOW entry for longs
    risk_per_unit = entry - stop                    # always positive

    # Position sizing — regime-adjusted risk
    effective_risk_pct = risk_pct if risk_pct is not None else ACCOUNT["risk_per_trade_pct"]
    risk_usd  = account_size * (effective_risk_pct / 100)
    quantity  = risk_usd / risk_per_unit if risk_per_unit > 0 else 0
    pos_value = quantity * entry
    pos_pct   = (pos_value / account_size) * 100

    if pos_pct > ACCOUNT["max_single_pos_pct"]:
        pos_pct   = ACCOUNT["max_single_pos_pct"]
        pos_value = account_size * (pos_pct / 100)
        quantity  = pos_value / entry
        risk_usd  = quantity * risk_per_unit

    # Take profits — ABOVE entry
    tps = []
    for rr, sell_pct in zip(SIGNAL["tp_rr"], SIGNAL["tp_exit_pct"]):
        tp_price    = entry + (risk_per_unit * rr)
        tp_gain_pct = ((tp_price - entry) / entry) * 100
        tp_qty      = quantity * (sell_pct / 100)
        tp_usdt     = tp_qty * tp_price
        tps.append({
            "price":    round(tp_price,    8),
            "gain_pct": round(tp_gain_pct, 1),
            "rr":       rr,
            "sell_pct": sell_pct,
            "usdt":     round(tp_usdt,     2),
        })

    # Expected value — alpha plays with RS filter have decent win rate
    win_prob = MACRO["win_rates"].get(regime, 0.48)
    avg_gain = sum(tp["gain_pct"] for tp in tps) / len(tps)
    avg_loss = stop_pct
    ev_pct   = round((win_prob * avg_gain) - ((1 - win_prob) * avg_loss), 2)

    return {
        "symbol":       symbol,
        "rank":         rank,
        "direction":    "LONG",
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
    gate:         dict | None = None,
) -> str:
    sep  = "=" * 80
    dash = "-" * 40

    regime_icon = {"BULL": "🟢 BULL", "SIDEWAYS": "🟡 SIDEWAYS", "BEAR": "🔴 BEAR"}.get(regime, regime)
    gate        = gate or REGIME_GATE.get(regime, REGIME_GATE["SIDEWAYS"])
    active_risk = gate["risk_pct"]
    active_conv = gate["min_conviction"]

    lines = [
        "",
        sep,
        "  ALPHA BREAKOUT SCANNER v1.2 — RS DECOUPLING PLAYS",
        f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        sep,
        "",
        "MARKET CONTEXT",
        dash,
        f"  BTC Price    :  $ {btc_price:>12,.2f}",
        f"  BTC 7-day    :  {btc_7d:>+10.2f}%",
        f"  BTC 24-hour  :  {btc_24h:>+10.2f}%",
        f"  Regime       :  {regime_icon}",
        "",
        f"  REGIME GATE  :  {gate['label']}",
        f"  Conviction   :  ≥ {active_conv if active_conv < 999 else 'N/A (BEAR — no longs)'}",
        f"  RS required  :  ≥ 5% vs BTC (7d)",
        "",
        "ACCOUNT (alpha sizing)",
        dash,
        f"  Balance       : $ {account_size:>12,.2f} USDT",
        f"  Risk / trade  : {active_risk}%  "
        f"(${account_size * active_risk / 100:,.0f} USDT per trade)",
        f"  Max positions : {ACCOUNT['max_positions']}  (max heat {ACCOUNT['max_heat_pct']}%)",
        "",
    ]

    if setups:
        lines += [sep, f"  {len(setups)} ALPHA BREAKOUT SETUP(S) FOUND", sep, ""]
        for i, setup in enumerate(setups, 1):
            plan = setup["plan"]
            sig  = setup["signals"]
            active_str = ", ".join(sig["active_signals"][:4])
            if len(sig["active_signals"]) > 4:
                active_str += f" +{len(sig['active_signals'])-4}"

            lines += [
                f"[{i}]  ▲  {setup['symbol']}  (Rank #{setup['rank']})",
                f"     Conviction   : {sig['conviction']:.0f} / 100",
                f"     Signals      : {sig['signal_count']} / {len(_ALPHA_WEIGHTS)} active",
                f"     Active       : {active_str}",
                f"     RS vs BTC 7d : {sig.get('rs_7d_value', 'N/A'):>+.2f}%  "
                f"(1d: {sig.get('rs_1d_value', 'N/A'):>+.2f}%  3d: {sig.get('rs_3d_value', 'N/A'):>+.2f}%)",
                f"     RSI          : {sig.get('rsi_value', 'N/A')}",
                f"     ADX          : {sig.get('adx_value', 'N/A')}  "
                f"(+DI {sig.get('plus_di', 'N/A')} / -DI {sig.get('minus_di', 'N/A')})",
                f"     SuperTrend   : {'▲ BULLISH' if sig.get('supertrend_bull') else '▼ BEARISH'}  "
                f"(line: {sig.get('supertrend_value', 'N/A')})",
                "",
                "     ── ALPHA TRADE PLAN ──────────────────────────────",
                f"     Entry (BUY)  : $ {plan['entry']:>14,.6f}",
                f"     Stop (SELL)  : $ {plan['stop']:>14,.6f}  (-{plan['stop_pct']:.1f}%)",
                f"     Risk         : $ {plan['risk_usd']:>10,.2f}  ({plan['risk_pct']:.2f}% of account)",
                f"     Position     : $ {plan['pos_value']:>10,.2f}  ({plan['pos_pct']:.1f}% of account)",
                f"     Quantity     :   {plan['quantity']:>10,.4f}  {setup['symbol']}",
                "",
            ]
            for j, tp in enumerate(plan["take_profits"], 1):
                lines.append(
                    f"     TP{j} ({tp['rr']:.1f}R) : $ {tp['price']:>14,.6f}"
                    f"  (+{tp['gain_pct']:.1f}%)  → sell {tp['sell_pct']}% = ${tp['usdt']:,.0f}"
                )
            lines += [
                f"     E[V]         :  {plan['ev_pct']:+.2f}% per trade",
                "",
                f"  Note: Alpha plays use 0.75% risk (half normal). The RS edge is the",
                f"  primary filter — these tokens have genuine buyers vs BTC flatness.",
                dash,
                "",
            ]
    else:
        if regime == "BEAR":
            lines += [
                sep,
                "  🔴 BEAR REGIME — NO NEW LONGS",
                "",
                "  Alpha scanner standing down. Cash is the position.",
                "  Watchlist below shows tokens to monitor for when regime recovers.",
                sep,
            ]
        else:
            lines += [
                sep,
                "  NO QUALIFYING ALPHA SETUPS FOUND",
                "",
                f"  Regime: {regime}  |  Conviction threshold: ≥ {active_conv}",
                "  No tokens cleared both the RS filter and conviction gate.",
                "  This is correct behaviour — do not lower the bar.",
                sep,
            ]

    # Watchlist
    if watchlist:
        lines += [
            "",
            "ALPHA WATCHLIST — strong RS, building signal stack",
            dash,
            f"  {'#':<4}  {'SYMBOL':<10}  {'RANK':>4}  {'PRICE':>12}  {'RS 7d':>7}  {'CONV':>5}  {'SIGS':>4}  KEY SIGNALS",
            "  " + "-" * 75,
        ]
        for i, w in enumerate(watchlist[:10], 1):
            sig    = w["signals"]
            active = ", ".join(sig["active_signals"][:3])
            if len(sig["active_signals"]) > 3:
                active += f" +{len(sig['active_signals'])-3}"
            lines.append(
                f"  {i:<4}  {w['symbol']:<10}  #{w['rank']:<4}  "
                f"${w['price']:>11,.5f}  {sig.get('rs_7d_value', 0):>+6.1f}%  "
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
    log.info("  ALPHA BREAKOUT SCANNER v1.1")
    log.info("  Tokens decoupling upward from BTC — no regime gate")
    log.info("=" * 80)

    # ── 1. BTC regime (for context + win rate selection, NOT a gate) ──────────
    log.info("\n[1/4] Fetching BTC data for regime context...")
    btc_df = fetch_btc_data(30)
    if btc_df is None:
        log.error("Cannot fetch BTC data. Aborting.")
        return

    btc_price = float(btc_df["close"].iloc[-1])
    btc_7d    = float((btc_df["close"].iloc[-1] / btc_df["close"].iloc[max(-42, -len(btc_df))] - 1) * 100)
    btc_24h   = float((btc_df["close"].iloc[-1] / btc_df["close"].iloc[max(-6,  -len(btc_df))] - 1) * 100)

    if btc_7d >= MACRO["bull_7d_pct"]:
        regime = "BULL"
    elif btc_7d >= MACRO["neutral_7d_pct"]:
        regime = "SIDEWAYS"
    else:
        regime = "BEAR"

    log.info(f"  BTC ${btc_price:,.0f}  |  7d {btc_7d:+.2f}%  |  Regime: {regime}")

    gate         = REGIME_GATE[regime]
    eff_min_conv = gate["min_conviction"]
    eff_risk_pct = gate["risk_pct"]
    eff_rsi_max  = gate["rsi_max"]

    log.info(f"  Regime gate  : {gate['label']}")
    log.info(f"  Min conviction: {eff_min_conv if eff_min_conv < 999 else 'BLOCKED (BEAR)'}  "
             f"|  Risk/trade: {eff_risk_pct}%  |  RSI max: {eff_rsi_max}")

    if regime == "BEAR":
        log.warning("  🔴 BEAR regime — no new alpha longs. Watchlist only.")

    # ── 2. Market coins ───────────────────────────────────────────────────────
    log.info(f"\n[2/4] Fetching top {SCAN['top_n_coins']} coins from CoinGecko...")
    coins = fetch_market_coins(SCAN["top_n_coins"])
    log.info(f"  {len(coins)} coins fetched")

    # ── 3. Scan ───────────────────────────────────────────────────────────────
    log.info(f"\n[3/4] Scanning for alpha breakouts (RS ≥{SIGNAL['rs_min_7d_pct']}% vs BTC)...\n")

    setups    = []
    watchlist = []
    rs_skipped = 0

    for i, coin in enumerate(coins, 1):
        symbol    = coin.get("symbol", "").upper()
        coin_id   = coin.get("id", "")
        rank      = coin.get("market_cap_rank") or 9999
        price     = float(coin.get("current_price") or 0)
        vol_24h   = float(coin.get("total_volume") or 0)
        change_7d = float(coin.get("price_change_percentage_7d_in_currency") or 0)
        change_24h = float(coin.get("price_change_percentage_24h") or 0)

        # Basic filters
        if symbol in STABLECOINS or symbol in EXCLUDED_SYMBOLS:
            continue
        if rank < SCAN["min_rank"] or rank > SCAN["max_rank"]:
            continue
        if price < SCAN["min_price"]:
            continue
        if vol_24h < SCAN["min_volume_24h"]:
            continue

        # RS pre-filter: token must show meaningful outperformance vs BTC in market data
        # This is a FAST pre-filter before fetching OHLCV — saves API calls
        coin_rs_approx = change_7d - btc_7d
        watchlist_rs_threshold = SIGNAL["rs_min_7d_pct"] * 0.5   # 2.5% for watchlist
        if coin_rs_approx < watchlist_rs_threshold:
            rs_skipped += 1
            continue

        log.info(f"  [{i}]  {symbol:<10} (#{rank})  ${price:.5f}  7d: {change_7d:+.1f}%  RS: {coin_rs_approx:+.1f}%")

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

        # Detect alpha signals
        signals = detect_alpha_signals(ohlcv, btc_7d, price)
        if signals is None:
            log.info(f"          → skip (signal computation failed)")
            continue

        conv = signals["conviction"]
        nsig = signals["signal_count"]
        rs7d = signals.get("rs_7d_value", 0)
        log.info(
            f"          → conviction {conv:.0f}/100  |  signals {nsig}/{len(_ALPHA_WEIGHTS)}"
            f"  |  RS {rs7d:+.1f}%"
        )

        # RSI ceiling — tighter in SIDEWAYS/BEAR
        rsi_val = signals.get("rsi_value")
        if rsi_val and rsi_val > eff_rsi_max:
            log.info(f"          → skip (RSI {rsi_val:.1f} > {eff_rsi_max} gate for {regime})")
            if conv >= SIGNAL["watchlist_min_conv"] and nsig >= SIGNAL["min_signals"] - 1:
                watchlist.append({
                    "symbol":    symbol,
                    "rank":      rank,
                    "price":     price,
                    "change_7d": change_7d,
                    "signals":   signals,
                })
            time.sleep(SCAN["api_delay_s"])
            continue

        # REQUIRE rs_vs_btc_strong for any entry (the whole point of this scanner)
        if not signals.get("rs_vs_btc_strong", False):
            if conv >= SIGNAL["watchlist_min_conv"] and nsig >= SIGNAL["min_signals"] - 1:
                watchlist.append({
                    "symbol":    symbol,
                    "rank":      rank,
                    "price":     price,
                    "change_7d": change_7d,
                    "signals":   signals,
                })
            time.sleep(SCAN["api_delay_s"])
            continue

        # Regime gate — BEAR blocks all longs; SIDEWAYS requires higher conviction
        if conv >= eff_min_conv and nsig >= SIGNAL["min_signals"]:
            plan = build_alpha_plan(symbol, rank, price, ohlcv, account_size, regime,
                                    risk_pct=eff_risk_pct)
            setups.append({
                "symbol":    symbol,
                "rank":      rank,
                "price":     price,
                "change_7d": change_7d,
                "signals":   signals,
                "plan":      plan,
            })
        elif conv >= SIGNAL["watchlist_min_conv"] and nsig >= SIGNAL["min_signals"] - 1:
            watchlist.append({
                "symbol":    symbol,
                "rank":      rank,
                "price":     price,
                "change_7d": change_7d,
                "signals":   signals,
            })

        time.sleep(SCAN["api_delay_s"])

    # ── 4. Report ─────────────────────────────────────────────────────────────
    log.info(f"\n[4/4] Building alpha scan report...")
    log.info(f"  Coins skipped (RS < {SIGNAL['rs_min_7d_pct']*0.5:.1f}% threshold): {rs_skipped}")
    log.info(f"  Alpha setups found: {len(setups)}")
    log.info(f"  Watchlist entries : {len(watchlist)}")

    setups.sort(key=lambda x: -x["signals"]["conviction"])
    watchlist.sort(key=lambda x: (-x["signals"].get("rs_7d_value", 0)))

    report = build_report(
        setups, watchlist, btc_price, btc_7d, btc_24h, regime, account_size, gate=gate,
    )

    ts          = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = _OUTPUT_DIR / f"alpha_scan_{ts}.txt"
    latest_path = _OUTPUT_DIR / "alpha_scan_LATEST.txt"
    report_path.write_text(report, encoding="utf-8")
    latest_path.write_text(report, encoding="utf-8")

    # JSON output
    output_json = {
        "generated":  datetime.now().isoformat(),
        "regime":     regime,
        "btc_price":  btc_price,
        "btc_7d":     btc_7d,
        "rs_min_pct": SIGNAL["rs_min_7d_pct"],
        "setups": [
            {**s, "signals": {k: v for k, v in s["signals"].items()
                               if not isinstance(v, bool) or v}}
            for s in setups
        ],
    }
    json_path = _OUTPUT_DIR / "alpha_scan_LATEST.json"
    json_path.write_text(json.dumps(output_json, indent=2, default=str), encoding="utf-8")

    elapsed = (datetime.now() - t0).total_seconds()
    log.info(f"\n  Report → {latest_path}")
    log.info(f"  JSON   → {json_path}")
    log.info(report)
    log.info(f"\n  Done in {elapsed:.0f}s.  {len(setups)} alpha setup(s)  |  {len(watchlist)} watchlist.")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Alpha Breakout Scanner — RS decoupling plays")
    parser.add_argument("--account", type=float, default=None, help="Account size in USDT")
    parser.add_argument("--top",     type=int,   default=None, help="Scan top N coins (default 300)")
    args = parser.parse_args()

    if args.top:
        SCAN["top_n_coins"] = args.top
        SCAN["max_rank"]    = args.top

    run(account_size=args.account)
