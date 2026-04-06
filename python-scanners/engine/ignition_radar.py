"""
================================================================================
IGNITION RADAR  v1.1
================================================================================
Early warning scanner — catches volume spikes BEFORE RS has had time to
accumulate (the "ONT problem": alpha_scanner fires AFTER a 30-50% move because
it requires 7d RS ≥5%). This scanner watches for the first signs on the 1h
timeframe and outputs a watchlist only.

NO position sizing, NO stop/TP calculations — purely a watchlist.

8 ignition signals (1h timeframe):
  vol_spike_6h       — last 6h avg volume ≥ 2.5× 7-day hourly baseline
  vol_spike_24h      — last 24h total volume ≥ 1.8× typical 24h
  obv_turning        — OBV regression slope positive + OBV[-1] > OBV[-12]
  price_range_break  — current close is highest close in last 120 bars (5d)
  rsi_reversal       — RSI ≤ 42 within last 24 bars AND RSI now rising
  btc_decoupling_6h  — (token 6h return) - (BTC 6h return) ≥ 1.5%
  whale_candle_recent — any of last 6 bars: bullish body ≥ 1.8× ATR(14)
  bb_squeeze         — BB bandwidth in bottom 20th pct of 7-day window

Two watchlist tiers:
  WATCH NOW  — 3+ signals fired
  ON RADAR   — 2 signals fired

Usage:
  python ignition_radar.py
  python ignition_radar.py --top 300
  python ignition_radar.py --account 96700
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
_log_file = _LOG_DIR / f"ignition_radar_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("ignition_radar")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

CG_PRO  = bool(os.environ.get("CG_API_KEY"))
CG_DEMO = bool(os.environ.get("CG_DEMO_KEY"))

SCAN = {
    "top_n_coins":     700,
    "min_rank":          5,
    "max_rank":        700,
    "min_volume_24h":  150_000,   # lower floor — catches rank 500-700 small caps
    "min_price":       0.0001,
    "cache_max_age_h": 1.5,       # 1h data must be fresh
    "api_delay_s":     (1.2 if CG_PRO else 4.5 if CG_DEMO else 6.5),
}

ACCOUNT = {
    "size_usdt":   96_700.0,
    "pos_pct":          4.0,   # 4% per ignition play — small, speculative entry
    "stop_pct":         3.0,   # tight stop — momentum play, bail fast if wrong
}

SIGNAL = {
    "vol_spike_mult":        2.5,   # 6h avg vol ≥ 2.5× 7-day hourly baseline
    "vol_spike_24h_mult":    1.8,   # 24h vol ≥ 1.8× 7-day daily avg
    "btc_decoupling_6h_min": 1.5,   # token 6h return > BTC 6h return + 1.5%
    "whale_candle_atr_mult": 1.8,   # candle body ≥ 1.8× ATR in last 6 bars
    "bb_squeeze_pct":       20,     # BB width in bottom 20th pct of 7-day window
    "rsi_reversal_low":     42,     # RSI was ≤ 42 within 24 bars, now rising
    "obv_slope_bars":       12,     # OBV regression over last 12 bars
    "min_signals_watch":     3,     # WATCH NOW threshold
    "min_signals_radar":     2,     # ON RADAR threshold
}

MACRO = {
    "bull_7d_pct":    3.0,
    "neutral_7d_pct": -7.0,
}

STABLECOINS = {
    "USDT", "USDC", "DAI", "BUSD", "TUSD", "USDD", "FDUSD", "PYUSD",
    "USDE", "SUSDE", "BFUSD", "RLUSD", "USDG", "USD0", "GHO", "USDAI",
    "WBTC", "WETH", "STETH", "RETH", "CBETH", "PAXG", "XAUT", "TBTC",
    "WBNB", "JITOSOL", "MSOL", "BNSOL", "EURC", "FRAX", "LUSD", "SUSD",
}

EXCLUDED_SYMBOLS = {"BTC", "ETH"}

# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL WEIGHTS
# ─────────────────────────────────────────────────────────────────────────────
_IGNITION_WEIGHTS = {
    "vol_spike_6h":        3.0,
    "whale_candle_recent": 2.5,
    "btc_decoupling_6h":   2.5,
    "obv_turning":         2.0,
    "price_range_break":   2.0,
    "vol_spike_24h":       2.0,
    "rsi_reversal":        1.5,
    "bb_squeeze":          1.5,
}
_TOTAL_IGNITION_WEIGHT = sum(_IGNITION_WEIGHTS.values())

# ─────────────────────────────────────────────────────────────────────────────
# API SESSIONS
# ─────────────────────────────────────────────────────────────────────────────
_CG_KEY     = os.environ.get("CG_API_KEY", "") or os.environ.get("CG_DEMO_KEY", "")
_CG_HEADERS = {"x-cg-pro-api-key": _CG_KEY} if os.environ.get("CG_API_KEY") else \
              {"x-cg-demo-api-key": _CG_KEY} if os.environ.get("CG_DEMO_KEY") else {}

_CG_SESSION = requests.Session()
_CG_SESSION.headers.update({**_CG_HEADERS, "User-Agent": "crypto-ignition-radar/1.0"})

_BN_SESSION = requests.Session()
_BN_SESSION.headers.update({"User-Agent": "crypto-ignition-radar/1.0"})

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


def _atr(highs: pd.Series, lows: pd.Series, closes: pd.Series, window: int = 14) -> float:
    prev_close = closes.shift(1)
    tr = pd.concat([
        highs - lows,
        (highs - prev_close).abs(),
        (lows  - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_s = tr.rolling(window).mean()
    return float(atr_s.iloc[-1]) if not atr_s.empty else np.nan


def _obv(closes: pd.Series, volumes: pd.Series) -> pd.Series:
    direction = np.sign(closes.diff().fillna(0))
    return (direction * volumes).cumsum()


def _bb_bandwidth(closes: pd.Series, window: int = 20) -> pd.Series:
    """Bollinger Band bandwidth series: (upper - lower) / middle."""
    mid   = closes.rolling(window).mean()
    std   = closes.rolling(window).std()
    upper = mid + 2 * std
    lower = mid - 2 * std
    return (upper - lower) / mid.replace(0, np.nan)


# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCHING
# ─────────────────────────────────────────────────────────────────────────────

def fetch_btc_1h() -> pd.DataFrame | None:
    """Fetch BTC 1h OHLCV from Binance (168 bars = 7 days). Cached as BTC_1h.csv."""
    cache = _CACHE_DIR / "BTC_1h.csv"
    if cache.exists() and (time.time() - cache.stat().st_mtime) / 3600 < SCAN["cache_max_age_h"]:
        try:
            df = pd.read_csv(cache, index_col=0, parse_dates=True)
            if len(df) >= 100:
                return df
        except Exception:
            pass
    try:
        r = _BN_SESSION.get(
            f"{_BN_BASE}/klines",
            params={"symbol": "BTCUSDT", "interval": "1h", "limit": 168},
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


def fetch_btc_4h_regime() -> pd.DataFrame | None:
    """Fetch BTC 4h OHLCV from Binance for regime classification (30 days)."""
    cache = _CACHE_DIR / "BTC_regime_4h.csv"
    age_h = (time.time() - cache.stat().st_mtime) / 3600 if cache.exists() else 999
    if age_h < SCAN["cache_max_age_h"]:
        try:
            df = pd.read_csv(cache, index_col=0, parse_dates=True)
            if len(df) >= 42:
                return df
        except Exception:
            pass
    try:
        r = _BN_SESSION.get(
            f"{_BN_BASE}/klines",
            params={"symbol": "BTCUSDT", "interval": "4h", "limit": 180},
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


def fetch_market_coins(top_n: int = 500) -> list[dict]:
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
                        "price_change_percentage": "24h,7d",
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


def fetch_ohlcv_1h(coin_id: str, symbol: str) -> pd.DataFrame | None:
    """
    Fetch 1h OHLCV from Binance (168 bars = 7 days). Binance only — no CG fallback.
    Cache key: {coin_id}_1h.csv, max age 1.5h.
    """
    cache_file = _CACHE_DIR / f"{coin_id}_1h.csv"
    if cache_file.exists():
        age_h = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_h < SCAN["cache_max_age_h"]:
            try:
                df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
                if len(df) >= 100:
                    return df
            except Exception:
                pass

    try:
        r = _BN_SESSION.get(
            f"{_BN_BASE}/klines",
            params={"symbol": f"{symbol}USDT", "interval": "1h", "limit": 168},
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
            if len(df) >= 100:
                df.to_csv(cache_file)
                return df
    except Exception:
        pass

    return None


# ─────────────────────────────────────────────────────────────────────────────
# IGNITION SIGNAL DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def detect_ignition_signals(
    ohlcv:        pd.DataFrame,
    btc_6h_return: float,
    coin_24h_pct:  float,
    coin_7d_pct:   float,
) -> dict | None:
    """
    Run all 8 ignition signal layers on 1h OHLCV data.
    Returns dict with boolean flags, scalar values, and conviction score.
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
        # ── 1. Volume spike 6h ────────────────────────────────────────────────
        # Last 6 bars avg volume ≥ 2.5× prior 7-day hourly avg (bars[-168:-6])
        vol_spike_6h = False
        vol_spike_ratio = 0.0
        if has_vol:
            valid_vol = vols.dropna()
            if len(valid_vol) >= 30:
                recent_6h  = float(valid_vol.iloc[-6:].mean())
                baseline   = float(valid_vol.iloc[:-6].mean())
                if baseline > 0:
                    vol_spike_ratio = recent_6h / baseline
                    vol_spike_6h    = vol_spike_ratio >= SIGNAL["vol_spike_mult"]
        s["vol_spike_6h"]    = vol_spike_6h
        s["vol_spike_ratio"] = round(vol_spike_ratio, 2)

        # ── 2. Volume spike 24h ───────────────────────────────────────────────
        # Last 24 bars total volume ≥ 1.8× rolling 7-day mean of 24-bar windows
        vol_spike_24h = False
        if has_vol:
            valid_vol = vols.dropna()
            if len(valid_vol) >= 48:
                recent_24h_vol = float(valid_vol.iloc[-24:].sum())
                # Compute rolling 24-bar window sums for the prior period
                windows = [
                    float(valid_vol.iloc[i:i + 24].sum())
                    for i in range(0, len(valid_vol) - 24, 24)
                    if i + 24 <= len(valid_vol) - 24
                ]
                if windows:
                    typical_24h = float(np.mean(windows))
                    if typical_24h > 0:
                        vol_spike_24h = recent_24h_vol >= typical_24h * SIGNAL["vol_spike_24h_mult"]
        s["vol_spike_24h"] = vol_spike_24h

        # ── 3. OBV turning ────────────────────────────────────────────────────
        # Linear regression slope of OBV over last 12 bars is positive
        # AND OBV[-1] > OBV[-12] (direction change)
        obv_turning = False
        if has_vol:
            valid = ohlcv[["close", "volume"]].dropna()
            n = SIGNAL["obv_slope_bars"]
            if len(valid) >= n:
                obv_series = _obv(valid["close"], valid["volume"])
                obv_window = obv_series.iloc[-n:].values
                x          = np.arange(len(obv_window), dtype=float)
                slope      = float(np.polyfit(x, obv_window, 1)[0])
                obv_turning = slope > 0 and float(obv_series.iloc[-1]) > float(obv_series.iloc[-n])
        s["obv_turning"] = obv_turning

        # ── 4. Price range break ──────────────────────────────────────────────
        # Current close is highest close in last 120 bars (5 days on 1h)
        price_range_break = False
        if len(closes) >= 20:
            lookback   = min(120, len(closes))
            window_cls = closes.iloc[-lookback:]
            price_range_break = float(closes.iloc[-1]) >= float(window_cls.max())
        s["price_range_break"] = price_range_break

        # ── 5. RSI reversal ───────────────────────────────────────────────────
        # RSI(14) had a value ≤ 42 within last 24 bars AND current RSI > prior RSI
        rsi_reversal = False
        rsi_val      = np.nan
        if len(closes) >= 20:
            rsi_s    = _rsi_series(closes)
            rsi_val  = float(rsi_s.iloc[-1]) if not rsi_s.empty else np.nan
            if not np.isnan(rsi_val) and len(rsi_s.dropna()) >= 3:
                rsi_window = rsi_s.iloc[-24:].dropna()
                had_low    = (rsi_window <= SIGNAL["rsi_reversal_low"]).any()
                rising     = float(rsi_s.iloc[-1]) > float(rsi_s.iloc[-2])
                rsi_reversal = had_low and rising
        s["rsi_reversal"] = rsi_reversal
        s["rsi_value"]    = round(rsi_val, 1) if not np.isnan(rsi_val) else None

        # ── 6. BTC decoupling 6h ──────────────────────────────────────────────
        # (token 6h return) - (BTC 6h return) ≥ 1.5%
        token_6h_return = float((closes.iloc[-1] / closes.iloc[max(-6, -len(closes))] - 1) * 100)
        decoupling_6h   = token_6h_return - btc_6h_return
        s["btc_decoupling_6h"]  = decoupling_6h >= SIGNAL["btc_decoupling_6h_min"]
        s["decoupling_6h_val"]  = round(decoupling_6h, 2)
        s["token_6h_return"]    = round(token_6h_return, 2)

        # ── 7. Whale candle recent ────────────────────────────────────────────
        # Any of last 6 bars: abs(close-open) ≥ 1.8× ATR(14), AND bullish
        whale_candle = False
        if len(closes) >= 20:
            atr_val = _atr(highs, lows, closes)
            if not np.isnan(atr_val) and atr_val > 0:
                recent = ohlcv.iloc[-6:]
                for _, bar in recent.iterrows():
                    body = float(bar["close"]) - float(bar["open"])
                    if body >= atr_val * SIGNAL["whale_candle_atr_mult"]:
                        whale_candle = True
                        break
        s["whale_candle_recent"] = whale_candle

        # ── 8. BB squeeze ─────────────────────────────────────────────────────
        # Current BB bandwidth in bottom 20th percentile of last 168 bars
        bb_squeeze = False
        if len(closes) >= 25:
            bw_series  = _bb_bandwidth(closes)
            bw_valid   = bw_series.dropna()
            if len(bw_valid) >= 20:
                threshold  = float(np.percentile(bw_valid.values, SIGNAL["bb_squeeze_pct"]))
                bb_squeeze = float(bw_valid.iloc[-1]) <= threshold
        s["bb_squeeze"] = bb_squeeze

    except Exception as e:
        log.debug(f"Signal error: {e}")
        return None

    # ── Conviction score ──────────────────────────────────────────────────────
    score      = sum(w for k, w in _IGNITION_WEIGHTS.items() if s.get(k, False))
    conviction = max(0.0, round((score / _TOTAL_IGNITION_WEIGHT) * 100, 1))

    active = [k for k in _IGNITION_WEIGHTS if s.get(k, False)]
    s["conviction"]     = conviction
    s["signal_count"]   = len(active)
    s["active_signals"] = active

    return s


# ─────────────────────────────────────────────────────────────────────────────
# IGNITION TRADE PLAN BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_ignition_plan(price: float, account_size: float) -> dict:
    """
    Tight intraday trade plan for WATCH NOW ignition plays.
    Uses flat % stop (not ATR-based) — these are fast momentum moves.
      Position : 4% of account (small, speculative early entry)
      Stop     : -3% (tight — bail fast if the pump fails)
      TP1/2/3  : +5% / +10% / +18%  (1.67R / 3.33R / 6R)
      Splits   : 40% / 35% / 25%
    """
    stop_pct  = ACCOUNT["stop_pct"]
    pos_pct   = ACCOUNT["pos_pct"]
    tp_gains  = [5.0, 10.0, 18.0]
    tp_splits = [40,   35,   25]

    pos_value  = account_size * (pos_pct / 100)
    quantity   = pos_value / price if price > 0 else 0
    stop_price = price * (1 - stop_pct / 100)
    risk_usd   = pos_value * (stop_pct / 100)

    tps = []
    for gain, split in zip(tp_gains, tp_splits):
        tp_price = price * (1 + gain / 100)
        tp_usdt  = quantity * (split / 100) * tp_price
        tps.append({
            "price":    round(tp_price, 8),
            "gain_pct": gain,
            "rr":       round(gain / stop_pct, 1),
            "sell_pct": split,
            "usdt":     round(tp_usdt, 2),
        })

    return {
        "entry":        round(price,      8),
        "stop":         round(stop_price, 8),
        "stop_pct":     stop_pct,
        "pos_value":    round(pos_value,  2),
        "pos_pct":      pos_pct,
        "quantity":     round(quantity,   4),
        "risk_usd":     round(risk_usd,   2),
        "risk_pct":     round((risk_usd / account_size) * 100, 2),
        "take_profits": tps,
    }


# ─────────────────────────────────────────────────────────────────────────────
# REPORT BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_report(
    watch_now:    list[dict],
    on_radar:     list[dict],
    btc_price:    float,
    btc_6h:       float,
    btc_24h:      float,
    regime:       str,
    account_size: float,
) -> str:
    """Build plain-text ignition radar report."""
    sep  = "=" * 80
    dash = "-" * 40

    lines = [
        "",
        sep,
        "  IGNITION RADAR v1.1 -- EARLY VOLUME / BREAKOUT WATCHLIST",
        f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  Regime: {regime}",
        sep,
        "",
        "MARKET CONTEXT",
        f"  BTC Price: ${btc_price:,.0f}  |  BTC 6h: {btc_6h:+.2f}%  |  BTC 24h: {btc_24h:+.2f}%",
        "",
        "  WATCH NOW  — trade plan included. Tight intraday plays: stop -3%, TP +5/10/18%.",
        "  ON RADAR   — watchlist only. Confirm with alpha_scanner once RS builds.",
        "",
    ]

    if watch_now:
        lines += [f"[WATCH NOW -- {len(watch_now)} coin(s)]"]
        for i, w in enumerate(watch_now, 1):
            sig        = w["signals"]
            active_str = ", ".join(sig["active_signals"])
            vol_str    = f"  vol_spike {sig.get('vol_spike_ratio', 0):.1f}x" if sig.get("vol_spike_6h") else ""
            plan       = build_ignition_plan(w["price"], account_size)

            lines += [
                f"  #{i}  {w['symbol']}  (rank #{w['rank']})",
                f"     Conv: {sig['conviction']:.0f}  |  Signals: {sig['signal_count']}/{len(_IGNITION_WEIGHTS)}{vol_str}",
                f"     Active : {active_str}",
                f"     RSI    : {sig.get('rsi_value', 'N/A')}",
                "",
                f"     ── IGNITION TRADE PLAN ───────────────────────────",
                f"     Entry (BUY) : $ {plan['entry']:>14,.6f}",
                f"     Stop (SELL) : $ {plan['stop']:>14,.6f}  (-{plan['stop_pct']:.1f}%)",
                f"     Position    : $ {plan['pos_value']:>10,.2f}  ({plan['pos_pct']:.1f}% of account)",
                f"     Risk        : $ {plan['risk_usd']:>10,.2f}  ({plan['risk_pct']:.2f}% of account)",
                f"     Quantity    :   {plan['quantity']:>10,.4f}  {w['symbol']}",
                "",
            ]
            for j, tp in enumerate(plan["take_profits"], 1):
                lines.append(
                    f"     TP{j} ({tp['rr']:.1f}R) : $ {tp['price']:>14,.6f}"
                    f"  (+{tp['gain_pct']:.1f}%)  → sell {tp['sell_pct']}% = ${tp['usdt']:,.0f}"
                )
            lines += [
                "",
                f"  ⚡ Intraday play — monitor closely. Move stop to breakeven at TP1.",
                dash,
                "",
            ]
    else:
        lines += ["[WATCH NOW -- 0 coins]", "  No coins reached the 3-signal threshold.", ""]

    if on_radar:
        lines += [f"[ON RADAR -- {len(on_radar)} coin(s)]"]
        for i, w in enumerate(on_radar, 1):
            sig        = w["signals"]
            active_str = ", ".join(sig["active_signals"])
            vol_str    = f"   vol_spike {sig.get('vol_spike_ratio', 0):.1f}x" if sig.get("vol_spike_6h") else ""
            lines.append(
                f"  #{i:<3} {w['symbol']:<6}  rank {w['rank']:<5} "
                f"price {w['price']:<12.6g}"
                f"{vol_str}  signals: {active_str}   conv: {sig['conviction']:.0f}"
            )
        lines.append("")
    else:
        lines += ["[ON RADAR -- 0 coins]", "  No coins reached the 2-signal threshold.", ""]

    lines += [dash, ""]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN SCAN
# ─────────────────────────────────────────────────────────────────────────────

def run(account_size: float | None = None) -> None:
    t0 = datetime.now()

    log.info("")
    log.info("=" * 80)
    log.info("  IGNITION RADAR v1.1")
    log.info("  Early volume/breakout watchlist — catches moves before RS builds")
    log.info("=" * 80)

    # ── 1. BTC data ───────────────────────────────────────────────────────────
    log.info("\n[1/4] Fetching BTC 1h data...")
    btc_1h = fetch_btc_1h()
    if btc_1h is None:
        log.error("Cannot fetch BTC 1h data. Aborting.")
        return

    btc_price   = float(btc_1h["close"].iloc[-1])
    btc_6h_ret  = float((btc_1h["close"].iloc[-1] / btc_1h["close"].iloc[max(-6, -len(btc_1h))] - 1) * 100)
    btc_24h_ret = float((btc_1h["close"].iloc[-1] / btc_1h["close"].iloc[max(-24, -len(btc_1h))] - 1) * 100)

    # Regime from 4h data (same logic as other scanners)
    btc_4h = fetch_btc_4h_regime()
    if btc_4h is not None and len(btc_4h) >= 42:
        btc_7d = float((btc_4h["close"].iloc[-1] / btc_4h["close"].iloc[max(-42, -len(btc_4h))] - 1) * 100)
    else:
        btc_7d = btc_24h_ret * 3.5   # rough estimate if 4h unavailable

    if btc_7d >= MACRO["bull_7d_pct"]:
        regime = "BULL"
    elif btc_7d >= MACRO["neutral_7d_pct"]:
        regime = "SIDEWAYS"
    else:
        regime = "BEAR"

    log.info(f"  BTC ${btc_price:,.0f}  |  6h {btc_6h_ret:+.2f}%  |  24h {btc_24h_ret:+.2f}%  |  Regime: {regime}")

    # ── 2. Market coins ───────────────────────────────────────────────────────
    log.info(f"\n[2/4] Fetching top {SCAN['top_n_coins']} coins from CoinGecko...")
    coins = fetch_market_coins(SCAN["top_n_coins"])
    log.info(f"  {len(coins)} coins fetched")

    # ── 3. Scan ───────────────────────────────────────────────────────────────
    log.info(f"\n[3/4] Scanning for ignition setups on 1h data...\n")

    watch_now = []
    on_radar  = []
    skipped   = 0

    for i, coin in enumerate(coins, 1):
        symbol     = coin.get("symbol", "").upper()
        coin_id    = coin.get("id", "")
        rank       = coin.get("market_cap_rank") or 9999
        price      = float(coin.get("current_price") or 0)
        vol_24h    = float(coin.get("total_volume") or 0)
        change_24h = float(coin.get("price_change_percentage_24h") or 0)
        change_7d  = float(coin.get("price_change_percentage_7d_in_currency") or 0)

        # Basic filters
        if symbol in STABLECOINS or symbol in EXCLUDED_SYMBOLS:
            continue
        if rank < SCAN["min_rank"] or rank > SCAN["max_rank"]:
            continue
        if price < SCAN["min_price"]:
            continue
        if vol_24h < SCAN["min_volume_24h"]:
            skipped += 1
            continue

        log.info(f"  [{i}]  {symbol:<10} (#{rank})  ${price:.5g}  24h: {change_24h:+.1f}%")

        ohlcv = fetch_ohlcv_1h(coin_id, symbol)
        if ohlcv is None or len(ohlcv) < 30:
            log.info(f"          -> skip (no 1h Binance data)")
            time.sleep(SCAN["api_delay_s"])
            continue

        signals = detect_ignition_signals(ohlcv, btc_6h_ret, change_24h, change_7d)
        if signals is None:
            log.info(f"          -> skip (signal computation failed)")
            continue

        nsig = signals["signal_count"]
        conv = signals["conviction"]
        active_str = ", ".join(signals["active_signals"]) if signals["active_signals"] else "none"
        log.info(f"          -> signals {nsig}/{len(_IGNITION_WEIGHTS)}  conv {conv:.0f}  [{active_str}]")

        entry = {
            "symbol":     symbol,
            "rank":       rank,
            "price":      price,
            "change_24h": change_24h,
            "change_7d":  change_7d,
            "signals":    signals,
        }

        if nsig >= SIGNAL["min_signals_watch"]:
            watch_now.append(entry)
        elif nsig >= SIGNAL["min_signals_radar"]:
            on_radar.append(entry)

        time.sleep(SCAN["api_delay_s"])

    # ── 4. Report ─────────────────────────────────────────────────────────────
    log.info(f"\n[4/4] Building ignition radar report...")
    log.info(f"  Low-volume skipped: {skipped}")
    log.info(f"  WATCH NOW : {len(watch_now)}")
    log.info(f"  ON RADAR  : {len(on_radar)}")

    watch_now.sort(key=lambda x: -x["signals"]["conviction"])
    on_radar.sort(key=lambda x: -x["signals"]["conviction"])

    report = build_report(watch_now, on_radar, btc_price, btc_6h_ret, btc_24h_ret, regime, ACCOUNT["size_usdt"])

    ts          = datetime.now().strftime("%Y%m%d_%H%M")
    report_path = _OUTPUT_DIR / f"ignition_radar_{ts}.txt"
    latest_path = _OUTPUT_DIR / "ignition_radar_LATEST.txt"
    report_path.write_text(report, encoding="utf-8")
    latest_path.write_text(report, encoding="utf-8")

    # JSON output
    def _sig_safe(sig: dict) -> dict:
        return {k: v for k, v in sig.items() if not isinstance(v, (pd.Series, pd.DataFrame))}

    output_json = {
        "generated":  datetime.now().isoformat(),
        "regime":     regime,
        "btc_price":  btc_price,
        "btc_6h":     btc_6h_ret,
        "btc_24h":    btc_24h_ret,
        "watch_now":  [{**w, "signals": _sig_safe(w["signals"])} for w in watch_now],
        "on_radar":   [{**w, "signals": _sig_safe(w["signals"])} for w in on_radar],
    }
    json_path = _OUTPUT_DIR / f"ignition_radar_{ts}.json"
    json_path.write_text(json.dumps(output_json, indent=2, default=str), encoding="utf-8")

    elapsed = (datetime.now() - t0).total_seconds()
    log.info(f"\n  Report -> {latest_path}")
    log.info(f"  JSON   -> {json_path}")
    log.info(report)
    log.info(f"\n  Done in {elapsed:.0f}s.  {len(watch_now)} WATCH NOW  |  {len(on_radar)} ON RADAR.")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ignition Radar — early volume/breakout watchlist")
    parser.add_argument("--account", type=float, default=None, help="Account size in USDT (reserved, unused)")
    parser.add_argument("--top",     type=int,   default=None, help="Scan top N coins (default 500)")
    args = parser.parse_args()

    if args.top:
        SCAN["top_n_coins"] = args.top
        SCAN["max_rank"]    = args.top

    run(account_size=args.account)
