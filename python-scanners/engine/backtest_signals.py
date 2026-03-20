"""
================================================================================
SIGNAL BACKTEST ENGINE  v1.0
================================================================================
Tests each of the 17 computable signals in master_orchestrator independently,
per regime, and in pairs/triplets against 2 years of Binance 4h OHLCV data.

What it produces:
  outputs/backtest/backtest_trades_{ts}.csv    — every simulated trade
  outputs/backtest/backtest_summary_{ts}.txt   — ranked signal stats
  outputs/backtest/backtest_summary_LATEST.txt — always the most recent run

What it answers:
  - Which signals have positive real expectancy on rank 50–600 alts?
  - Do signals work differently in BULL vs SIDEWAYS vs BEAR regimes?
  - Which signal combinations (pairs/triplets) produce the best outcomes?
  - What should the weights in master_orchestrator actually be?

Key limitations (read before acting on results):
  - Survivorship bias: universe = coins alive today at rank 50–600.
    Coins that crashed to zero are excluded. All numbers are inflated.
    Use results for RELATIVE ranking (which signals beat others),
    not ABSOLUTE win rates (actual 45% win rate in live trading will be lower).
  - No slippage, spread, or exchange fees modelled.
  - funding_neg excluded (real-time only, no historical perp funding data in cache).

Usage:
  python backtest_signals.py                        # default: 50 coins, 730 days
  python backtest_signals.py --coins 30 --days 365  # faster run
  python backtest_signals.py --signal rsi_divergence # test one signal only
================================================================================
"""

import os
import sys
import re
import json
import shutil
import time
import argparse
import itertools
import requests
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────────────────
_SCRIPT_DIR   = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent
_CACHE_DIR    = _PROJECT_ROOT / "cache"   / "backtest_ohlcv"
_OUTPUT_DIR   = _PROJECT_ROOT / "outputs" / "backtest"

for _d in (_CACHE_DIR, _OUTPUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# APIs
# ─────────────────────────────────────────────────────────────────────────────
_BINANCE_API = "https://api.binance.com/api/v3"
_CG_API      = "https://api.coingecko.com/api/v3"
_CG_KEY      = os.environ.get("CG_DEMO_KEY", "")
_CG_HEADERS  = {"x-cg-demo-api-key": _CG_KEY} if _CG_KEY else {}

_BN_SESSION = requests.Session()
_BN_SESSION.headers.update({"User-Agent": "crypto-backtest/1.0"})
_CG_SESSION = requests.Session()
_CG_SESSION.headers.update(_CG_HEADERS)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
CONFIG = {
    # Universe
    "n_coins":          50,       # Coins to backtest
    "min_rank":         50,       # Skip top 49 (too large, low volatility)
    "max_rank":        600,       # Skip micro-caps (illiquid)
    "days":            730,       # History depth (~2 years of 4h bars)
    "min_bars":        120,       # Skip coin if fewer bars available

    # Signal warmup — bars before any signal can fire
    "warmup_bars":      50,

    # Trade simulation
    "atr_window":       14,
    "atr_stop_mult":   1.5,
    "stop_min_pct":   -15.0,      # Stop never wider than 15%
    "stop_max_pct":    -5.0,      # Stop never tighter than 5%
    "tp_rr":       [2.0, 3.0, 5.0],
    "tp_exit_pct": [ 30,  40,  30],
    "max_hold_bars":    42,       # Max hold = 7 days at 4h bars

    # Regime thresholds (BTC rolling 42-bar return)
    "bull_7d":   3.0,             # BTC up >3% over 7d → BULL
    "bear_7d":  -7.0,             # BTC down >7% over 7d → BEAR

    # Combination analysis
    "min_combo_fires":  20,       # Discard combos with fewer fires
    "top_n_combos":     25,       # Show top N combinations
    "max_combo_size":    3,       # Test pairs (2) and triplets (3)

    # Cache freshness
    "cache_max_age_h":  12,
}

# Signals included in combination analysis (exclude noisy single-check ones)
_COMBO_SIGNALS = [
    "rsi_divergence", "rs_vs_btc", "macd_turning", "stealth_accum",
    "cmf", "vol_expansion", "bb_squeeze", "higher_lows",
    "rs_acceleration", "declining_sell_vol", "rsi_ignition",
    "whale_candles", "macd_crossover", "vol_velocity", "trend_strong",
]

STABLECOINS = {
    "USDT", "USDC", "DAI", "BUSD", "TUSD", "USDD", "FDUSD", "PYUSD",
    "USDE", "SUSDE", "WBTC", "WETH", "STETH", "RETH", "CBETH", "PAXG",
    "XAUT", "TBTC", "WBNB", "JITOSOL", "MSOL", "BNSOL", "EURC", "FRAX",
    "LUSD", "SUSD", "CRVUSD", "GUSD", "USDS", "USDAI",
}


# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCHING
# ─────────────────────────────────────────────────────────────────────────────

def fetch_binance_ohlcv(symbol: str, days: int = 730) -> pd.DataFrame | None:
    """
    Fetch up to `days` of 4h OHLCV from Binance klines.
    Paginates backwards to collect the full history. Cached to disk.
    Returns DataFrame with columns: open, high, low, close, volume (index=datetime).
    """
    cache_file = _CACHE_DIR / f"{symbol}_{days}d_4h.csv"
    if cache_file.exists():
        age_h = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_h < CONFIG["cache_max_age_h"]:
            try:
                df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
                if len(df) >= CONFIG["min_bars"]:
                    return df
            except Exception:
                pass

    bars_needed = days * 6  # 6 × 4h bars per day
    all_rows    = []
    end_time    = None

    while len(all_rows) < bars_needed:
        params = {
            "symbol":   f"{symbol}USDT",
            "interval": "4h",
            "limit":    min(1000, bars_needed - len(all_rows)),
        }
        if end_time:
            params["endTime"] = end_time

        try:
            r = _BN_SESSION.get(f"{_BINANCE_API}/klines", params=params, timeout=15)
            if r.status_code == 429:
                time.sleep(30)
                continue
            if r.status_code != 200:
                break
            rows = r.json()
            if not rows:
                break
            all_rows = rows + all_rows
            end_time = int(rows[0][0]) - 1
            if len(rows) < 1000:
                break  # reached earliest available history
            time.sleep(0.12)
        except Exception:
            break

    if len(all_rows) < CONFIG["min_bars"]:
        return None

    df = pd.DataFrame(all_rows, columns=[
        "ts", "open", "high", "low", "close", "base_vol",
        "close_time", "volume", "trades", "taker_base", "taker_quote", "ignore",
    ])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    df = df.set_index("ts").sort_index()
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    df = df[~df.index.duplicated(keep="last")]

    try:
        df.to_csv(cache_file)
    except Exception:
        pass

    return df


def fetch_coin_universe(n: int = 50) -> list[dict]:
    """Fetch rank min_rank–max_rank coins from CoinGecko, excluding stablecoins."""
    coins = []
    page  = 1
    while len(coins) < n + 100:
        try:
            r = _CG_SESSION.get(
                f"{_CG_API}/coins/markets",
                params={
                    "vs_currency": "usd",
                    "order":       "market_cap_desc",
                    "per_page":    250,
                    "page":        page,
                },
                timeout=20,
            )
            if r.status_code == 429:
                time.sleep(60)
                continue
            if r.status_code != 200:
                break
            data = r.json()
            if not data:
                break
            coins.extend(data)
            if len(coins) >= (CONFIG["max_rank"] + 50):
                break
            page += 1
            time.sleep(1.5)
        except Exception:
            break

    filtered = [
        c for c in coins
        if CONFIG["min_rank"] <= (c.get("market_cap_rank") or 9999) <= CONFIG["max_rank"]
        and c["symbol"].upper() not in STABLECOINS
    ]
    return filtered[:n]


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR SERIES  (all vectorised, no look-ahead)
# ─────────────────────────────────────────────────────────────────────────────

def _rsi_s(closes: pd.Series, window: int = 9) -> pd.Series:
    d    = closes.diff()
    gain = d.clip(lower=0).rolling(window).mean()
    loss = (-d.clip(upper=0)).rolling(window).mean()
    rs   = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr_s(high: pd.Series, low: pd.Series, close: pd.Series,
           window: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window).mean()


def _macd_hist_s(closes: pd.Series,
                 fast: int = 12, slow: int = 26, sig: int = 9) -> pd.Series:
    macd = (closes.ewm(span=fast, adjust=False).mean()
            - closes.ewm(span=slow, adjust=False).mean())
    return macd - macd.ewm(span=sig, adjust=False).mean()


def _obv_s(closes: pd.Series, vols: pd.Series) -> pd.Series:
    direction = closes.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (direction * vols).cumsum()


def _bb_width_s(closes: pd.Series, window: int = 20) -> pd.Series:
    sma = closes.rolling(window).mean()
    std = closes.rolling(window).std()
    return std / sma.replace(0, np.nan)


def _cmf_s(ohlcv: pd.DataFrame, window: int = 20) -> pd.Series:
    hl  = (ohlcv["high"] - ohlcv["low"]).replace(0, np.nan)
    mfm = ((ohlcv["close"] - ohlcv["low"]) - (ohlcv["high"] - ohlcv["close"])) / hl
    mfv = mfm * ohlcv["volume"]
    return (mfv.rolling(window).sum()
            / ohlcv["volume"].rolling(window).sum().replace(0, np.nan))


def _adx_s(high: pd.Series, low: pd.Series, close: pd.Series,
           window: int = 10) -> tuple[pd.Series, pd.Series, pd.Series]:
    up       = high.diff()
    down     = -low.diff()
    plus_dm  = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr       = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr      = tr.rolling(window).mean().replace(0, np.nan)
    plus_di  = 100 * (plus_dm.rolling(window).mean()  / atr)
    minus_di = 100 * (minus_dm.rolling(window).mean() / atr)
    dx       = 100 * ((plus_di - minus_di).abs()
                      / (plus_di + minus_di).replace(0, np.nan))
    adx      = dx.rolling(window).mean()
    return plus_di, minus_di, adx


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL COMPUTATION  (one boolean Series per signal, aligned to ohlcv.index)
# ─────────────────────────────────────────────────────────────────────────────

def compute_all_signals(
    ohlcv:     pd.DataFrame,
    btc_close: pd.Series | None,
    verbose:   bool = False,
) -> pd.DataFrame:
    """
    Returns a DataFrame of boolean signal columns aligned to ohlcv.index.
    Uses only past data at each bar — zero look-ahead bias.
    """
    c  = ohlcv["close"]
    h  = ohlcv["high"]
    l  = ohlcv["low"]
    o  = ohlcv["open"]
    v  = ohlcv["volume"]

    rsi_s   = _rsi_s(c, 9)
    atr_s   = _atr_s(h, l, c, 14)
    macd_h  = _macd_hist_s(c)
    obv_s   = _obv_s(c, v)
    bb_w    = _bb_width_s(c, 20)
    bb_wavg = bb_w.rolling(20).mean()
    cmf_s   = _cmf_s(ohlcv, 20)
    plus_di, minus_di, adx_s = _adx_s(h, l, c, 10)

    sigs = pd.DataFrame(index=ohlcv.index, dtype=bool)

    # ── 1. rsi_in_zone ────────────────────────────────────────────────────────
    sigs["rsi_in_zone"] = (rsi_s > 32) & (rsi_s < 65)

    # ── 2. rsi_ignition (RSI crossed out of oversold) ─────────────────────────
    sigs["rsi_ignition"] = (rsi_s.shift(3) < 22) & (rsi_s >= 22) & (rsi_s < 42)

    # ── 3. rsi_divergence (price lower-low, RSI higher-low) ───────────────────
    # Rolling window implementation — no look-ahead. The most expensive signal.
    DIV_WIN = 30
    div_vals = np.zeros(len(c), dtype=bool)
    c_arr    = c.values
    r_arr    = rsi_s.values
    for i in range(DIV_WIN + 9 + 2, len(c_arr)):
        c_w = c_arr[i - DIV_WIN: i + 1]
        r_w = r_arr[i - DIV_WIN: i + 1]
        if np.sum(np.isnan(r_w)) > DIV_WIN // 2:
            continue
        mid  = DIV_WIN // 2
        c1, c2 = c_w[:mid], c_w[mid:]
        r1, r2 = r_w[:mid], r_w[mid:]
        try:
            i1, i2   = int(np.nanargmin(c1)), int(np.nanargmin(c2))
            p1, p2   = c1[i1], c2[i2]
            rv1, rv2 = r1[i1], r2[i2]
            if not any(np.isnan([p1, p2, rv1, rv2])):
                div_vals[i] = (p2 <= p1 * 0.98) and (rv2 >= rv1 + 5.0)
        except Exception:
            pass
    if verbose:
        print(f"    rsi_divergence fires: {div_vals.sum()}")
    sigs["rsi_divergence"] = div_vals

    # ── 4. macd_crossover ─────────────────────────────────────────────────────
    sigs["macd_crossover"] = (macd_h > 0) & (macd_h.shift(1) <= 0)

    # ── 5. macd_turning (negative, rising 4 bars straight) ────────────────────
    sigs["macd_turning"] = (
        (macd_h < 0)
        & (macd_h > macd_h.shift(1))
        & (macd_h.shift(1) > macd_h.shift(2))
        & (macd_h.shift(2) > macd_h.shift(3))
    )

    # ── 6. trend_strong (ADX > 25, +DI > -DI) ────────────────────────────────
    sigs["trend_strong"] = (adx_s > 25) & (plus_di > minus_di)

    # ── 7. rs_vs_btc (7-day 42-bar outperformance) ───────────────────────────
    # ── 8. rs_acceleration (28h 7-bar acceleration) ──────────────────────────
    if btc_close is not None and len(btc_close) > 0:
        btc_al     = btc_close.reindex(c.index, method="ffill")
        tok_42     = c / c.shift(42) - 1
        btc_42     = btc_al / btc_al.shift(42) - 1
        rs_42      = tok_42 - btc_42
        tok_7      = c / c.shift(7) - 1
        btc_7      = btc_al / btc_al.shift(7) - 1
        rs_7       = tok_7 - btc_7
        sigs["rs_vs_btc"]      = rs_42 >= 0.03
        sigs["rs_acceleration"] = (rs_7 >= 0.03) & (rs_7 > rs_42)
    else:
        sigs["rs_vs_btc"]       = False
        sigs["rs_acceleration"] = False

    # ── 9. atr_expanding ──────────────────────────────────────────────────────
    atr_pct   = atr_s / c.replace(0, np.nan)
    atr_slope = atr_pct - atr_pct.shift(5)
    sigs["atr_expanding"] = (atr_slope > 0) & (atr_pct > 0.025)

    # ── 10. bb_squeeze ────────────────────────────────────────────────────────
    sigs["bb_squeeze"] = (bb_w < 0.038) & (bb_w < bb_wavg * 0.8)

    # ── 11. vol_velocity ──────────────────────────────────────────────────────
    short_ma   = v.rolling(5).mean()
    long_ma    = v.rolling(10).mean()
    ratio_now  = short_ma / long_ma.replace(0, np.nan)
    ratio_prev = short_ma.shift(3) / long_ma.shift(3).replace(0, np.nan)
    sigs["vol_velocity"] = (ratio_now > 1.4) & (ratio_now > ratio_prev)

    # ── 12. vol_expansion ────────────────────────────────────────────────────
    recent_vol   = v.rolling(6).mean()
    baseline_vol = v.shift(7).rolling(35).mean()
    sigs["vol_expansion"] = recent_vol >= baseline_vol.replace(0, np.nan) * 1.5

    # ── 13. stealth_accum (OBV rising while price flat <2%) ──────────────────
    avg_vol_10 = v.rolling(10).mean()
    obv_chg    = (obv_s - obv_s.shift(10)) / (avg_vol_10 * 10 + 1)
    price_chg  = c / c.shift(10) - 1
    sigs["stealth_accum"] = (obv_chg > 0.015) & (price_chg.abs() < 0.02)

    # ── 14. cmf ───────────────────────────────────────────────────────────────
    sigs["cmf"] = cmf_s > 0.05

    # ── 15. whale_candles (large green candle, close in top 30%) ─────────────
    ranges  = h - l
    avg_rng = ranges.rolling(20).mean()
    pos_pct = (c - l) / ranges.clip(lower=1e-12)
    sigs["whale_candles"] = (
        (ranges > avg_rng * 2.0)
        & (c > o)
        & (pos_pct >= 0.70)
    )

    # ── 16. higher_lows (3-bar minimum each side) ────────────────────────────
    # Vectorised approximation: check if each bar is a 7-bar local minimum,
    # then confirm the last 3 qualifying lows are ascending.
    HL_WIN    = 30
    SIDE_BARS = 3
    is_swing  = pd.Series(False, index=l.index)
    l_arr     = l.values
    for i in range(SIDE_BARS, len(l_arr) - SIDE_BARS):
        window_min = np.min(l_arr[i - SIDE_BARS: i + SIDE_BARS + 1])
        if l_arr[i] == window_min:
            is_swing.iloc[i] = True

    hl_vals = np.zeros(len(l), dtype=bool)
    swing_idx = np.where(is_swing.values)[0]
    for i in range(len(l)):
        # Find last 3 swing lows within HL_WIN bars before bar i
        recent_swings = swing_idx[(swing_idx >= i - HL_WIN) & (swing_idx < i)]
        if len(recent_swings) >= 3:
            last3 = [float(l_arr[j]) for j in recent_swings[-3:]]
            hl_vals[i] = last3[2] > last3[1] > last3[0]
    sigs["higher_lows"] = hl_vals

    # ── 17. declining_sell_vol ────────────────────────────────────────────────
    # Vectorised: avg sell-candle volume in recent 5 bars vs prior 5 bars
    is_red     = (c < o).astype(float)
    sell_vol   = v * is_red
    recent_sv  = sell_vol.rolling(5).mean()
    earlier_sv = sell_vol.shift(5).rolling(5).mean()
    # Only fire when both halves have meaningful sell volume
    has_both   = (recent_sv > 0) & (earlier_sv > 0)
    sigs["declining_sell_vol"] = has_both & (recent_sv <= earlier_sv * 0.80)

    # ── Fill NaN and cast to bool ──────────────────────────────────────────────
    for col in sigs.columns:
        sigs[col] = sigs[col].fillna(False).infer_objects(copy=False).astype(bool)

    return sigs


# ─────────────────────────────────────────────────────────────────────────────
# REGIME CLASSIFICATION
# ─────────────────────────────────────────────────────────────────────────────

def classify_regime(btc_close: pd.Series) -> pd.Series:
    """
    At each bar, classify market regime from BTC's rolling 42-bar (7-day) return.
    Returns a Series of 'BULL' | 'SIDEWAYS' | 'BEAR'.
    """
    pct = btc_close / btc_close.shift(42) - 1
    bull_thr = CONFIG["bull_7d"]  / 100
    bear_thr = CONFIG["bear_7d"]  / 100

    def _label(x):
        if pd.isna(x):    return "SIDEWAYS"
        if x >= bull_thr: return "BULL"
        if x <= bear_thr: return "BEAR"
        return "SIDEWAYS"

    return pct.apply(_label)


# ─────────────────────────────────────────────────────────────────────────────
# TRADE SIMULATION
# ─────────────────────────────────────────────────────────────────────────────

def simulate_trade(ohlcv: pd.DataFrame, entry_bar: int, atr_val: float) -> dict:
    """
    Simulate a trade entered at the close of `entry_bar`.
    Scans forward bar-by-bar for ATR-based stop or R:R take-profit hits.
    Exits at timeout (max_hold_bars) if neither triggers.
    """
    entry = float(ohlcv["close"].iloc[entry_bar])

    # Stop distance
    raw_stop_pct = max(
        CONFIG["stop_min_pct"],
        min(CONFIG["stop_max_pct"],
            -(atr_val * CONFIG["atr_stop_mult"] / entry) * 100)
    )
    stop        = entry * (1 + raw_stop_pct / 100)
    risk_unit   = entry - stop          # always positive

    tps          = [entry + risk_unit * rr for rr in CONFIG["tp_rr"]]
    tp_hit       = [False, False, False]
    remaining    = 1.0
    realized_pnl = 0.0
    exit_reason  = "timeout"
    n            = len(ohlcv)
    last_bar     = min(entry_bar + CONFIG["max_hold_bars"], n - 1)

    for bar_i in range(entry_bar + 1, last_bar + 1):
        bar_h = float(ohlcv["high"].iloc[bar_i])
        bar_l = float(ohlcv["low"].iloc[bar_i])

        # Stop check (low <= stop)
        if bar_l <= stop:
            realized_pnl += remaining * (stop / entry - 1) * 100
            exit_reason   = "stop"
            last_bar      = bar_i
            break

        # TP checks (high >= tp)
        for j, (tp, ex_pct) in enumerate(zip(tps, CONFIG["tp_exit_pct"])):
            if not tp_hit[j] and bar_h >= tp:
                sell_frac     = ex_pct / 100
                realized_pnl += remaining * sell_frac * (tp / entry - 1) * 100
                remaining    -= remaining * sell_frac
                tp_hit[j]     = True

        if all(tp_hit):
            exit_reason = "all_tps"
            last_bar    = bar_i
            break
    else:
        # Timeout — exit at close of last bar
        realized_pnl += remaining * (float(ohlcv["close"].iloc[last_bar]) / entry - 1) * 100

    return {
        "entry":       round(entry,        8),
        "stop":        round(stop,         8),
        "stop_pct":    round(raw_stop_pct, 2),
        "pnl_pct":     round(realized_pnl, 4),
        "win":         realized_pnl > 0,
        "tp1_hit":     tp_hit[0],
        "tp2_hit":     tp_hit[1],
        "tp3_hit":     tp_hit[2],
        "exit_reason": exit_reason,
        "hold_bars":   last_bar - entry_bar,
    }


# ─────────────────────────────────────────────────────────────────────────────
# STATISTICS
# ─────────────────────────────────────────────────────────────────────────────

def compute_stats(trades: list[dict], label: str = "") -> dict:
    """Compute core trading statistics from a list of simulated trade dicts."""
    if not trades:
        return {
            "label": label, "n": 0, "win_rate": 0.0, "expectancy": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0, "sharpe": 0.0, "max_dd": 0.0,
            "tp1_rate": 0.0, "tp2_rate": 0.0,
        }

    pnls     = [t["pnl_pct"] for t in trades]
    wins     = [p for p in pnls if p > 0]
    losses   = [p for p in pnls if p <= 0]
    win_rate = len(wins) / len(pnls)
    avg_win  = float(np.mean(wins))   if wins   else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    expect   = win_rate * avg_win + (1 - win_rate) * avg_loss

    # Sharpe-like: annualised per-trade return / volatility
    if len(pnls) >= 3:
        mu     = float(np.mean(pnls))
        sigma  = float(np.std(pnls, ddof=1))
        sharpe = (mu / sigma * np.sqrt(len(pnls))) if sigma > 0 else 0.0
    else:
        sharpe = 0.0

    # Max drawdown on cumulative P&L curve
    cum    = np.cumsum(pnls)
    peak   = np.maximum.accumulate(cum)
    max_dd = float(np.min(cum - peak)) if len(cum) > 0 else 0.0

    tp1_rate = sum(1 for t in trades if t.get("tp1_hit")) / len(trades)
    tp2_rate = sum(1 for t in trades if t.get("tp2_hit")) / len(trades)

    return {
        "label":      label,
        "n":          len(pnls),
        "win_rate":   round(win_rate * 100, 1),
        "expectancy": round(expect,         2),
        "avg_win":    round(avg_win,        2),
        "avg_loss":   round(avg_loss,       2),
        "sharpe":     round(sharpe,         2),
        "max_dd":     round(max_dd,         2),
        "tp1_rate":   round(tp1_rate * 100, 1),
        "tp2_rate":   round(tp2_rate * 100, 1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# REPORT GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def _build_report(
    signal_stats:  list[dict],
    combo_stats:   list[dict],
    regime_stats:  dict,
    n_coins:       int,
    days:          int,
    btc_bars:      int,
    ts_str:        str,
) -> str:
    sep  = "=" * 80
    dash = "-" * 80

    lines = [
        sep,
        "  SIGNAL BACKTEST REPORT  —  master_orchestrator v2.1",
        f"  Generated : {ts_str}",
        f"  Universe  : {n_coins} coins, rank {CONFIG['min_rank']}–{CONFIG['max_rank']}",
        f"  History   : {days} days (~{btc_bars} 4h bars per coin) from Binance",
        "",
        "  ⚠️  SURVIVORSHIP BIAS: Universe = coins alive today at rank 50–600.",
        "  All win rates are inflated vs live trading. Use for RELATIVE ranking.",
        sep,
        "",
        "INDIVIDUAL SIGNAL PERFORMANCE  (ranked by expectancy)",
        dash,
        f"  {'SIGNAL':<22}  {'N':>5}  {'WIN%':>6}  {'E[PnL]':>7}  "
        f"{'AVG_WIN':>7}  {'AVG_LOSS':>8}  {'SHARPE':>6}  {'TP1%':>5}  {'MAX_DD':>7}",
        dash,
    ]

    for s in signal_stats:
        flag = "✅" if s["expectancy"] > 0 else "❌"
        lines.append(
            f"  {flag} {s['label']:<20}  {s['n']:>5}  {s['win_rate']:>5.1f}%  "
            f"{s['expectancy']:>+7.2f}  {s['avg_win']:>+7.2f}  {s['avg_loss']:>+8.2f}  "
            f"{s['sharpe']:>6.2f}  {s['tp1_rate']:>4.0f}%  {s['max_dd']:>+7.2f}"
        )

    # ── Per-regime breakdown ──────────────────────────────────────────────────
    lines += [
        "",
        "PER-REGIME BREAKDOWN  (expectancy  |  n fires per regime)",
        dash,
        f"  {'SIGNAL':<22}  {'BULL E':>8}  {'BULL N':>7}  "
        f"{'SIDE E':>8}  {'SIDE N':>7}  {'BEAR E':>8}  {'BEAR N':>7}",
        dash,
    ]
    for s in signal_stats:
        sk = s["label"]
        rb = regime_stats["BULL"].get(sk,     {"expectancy": 0, "n": 0})
        rs = regime_stats["SIDEWAYS"].get(sk, {"expectancy": 0, "n": 0})
        rr = regime_stats["BEAR"].get(sk,     {"expectancy": 0, "n": 0})
        b_flag = "✅" if rb["expectancy"] > 0 else "❌"
        s_flag = "✅" if rs["expectancy"] > 0 else "❌"
        r_flag = "✅" if rr["expectancy"] > 0 else "❌"
        lines.append(
            f"  {sk:<22}  "
            f"{b_flag}{rb['expectancy']:>+6.2f}  {rb['n']:>7}  "
            f"{s_flag}{rs['expectancy']:>+6.2f}  {rs['n']:>7}  "
            f"{r_flag}{rr['expectancy']:>+6.2f}  {rr['n']:>7}"
        )

    # ── Combination analysis ──────────────────────────────────────────────────
    if combo_stats:
        lines += [
            "",
            f"TOP {CONFIG['top_n_combos']} SIGNAL COMBINATIONS  "
            f"(pairs + triplets | min {CONFIG['min_combo_fires']} fires | ranked by expectancy)",
            dash,
            f"  {'COMBINATION':<52}  {'N':>5}  {'WIN%':>6}  {'E[PnL]':>7}  {'SHARPE':>6}",
            dash,
        ]
        for s in combo_stats[:CONFIG["top_n_combos"]]:
            flag = "✅" if s["expectancy"] > 0 else "❌"
            lines.append(
                f"  {flag} {s['label']:<50}  {s['n']:>5}  {s['win_rate']:>5.1f}%  "
                f"{s['expectancy']:>+7.2f}  {s['sharpe']:>6.2f}"
            )

    # ── How to use ────────────────────────────────────────────────────────────
    lines += [
        "",
        "HOW TO USE THESE RESULTS",
        dash,
        "  1. Signals with positive expectancy AND n >= 50 are worth keeping.",
        "     Signals with negative expectancy should have their weight reduced.",
        "",
        "  2. Check the per-regime table — some signals only work in BULL.",
        "     A signal with E[PnL] = +2% BULL but -1% SIDEWAYS should be",
        "     weighted more conservatively or gated by regime in the scanner.",
        "",
        "  3. Winning combinations reveal which signals have compounding edge.",
        "     If 'rsi_divergence + rs_vs_btc' shows E[PnL] >> either alone,",
        "     requiring BOTH before entering is worth the reduced signal count.",
        "",
        "  4. Update _WEIGHTS in master_orchestrator.py to reflect reality:",
        "       High expectancy in SIDEWAYS → increase weight",
        "       Negative expectancy in SIDEWAYS → reduce weight",
        "       Only works in BULL → keep weight but be regime-aware",
        "",
        "  5. Re-run every 30–60 days. Market regimes shift and so does edge.",
        "",
        "  6. These numbers are absolute best-case (survivorship bias,",
        "     no fees, perfect fills). Discount all expectancies by ~30%",
        "     for a realistic live-trading estimate.",
        sep,
    ]

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# AUTO-CALIBRATION  — derive weights from backtest results, patch orchestrator
# ─────────────────────────────────────────────────────────────────────────────

_ORCHESTRATOR_PATH = _SCRIPT_DIR / "master_orchestrator.py"

# Signals that cannot be backtested — keep fixed regardless of results.
_FIXED_WEIGHTS: dict[str, float] = {
    "funding_neg": 2.0,   # Real-time perp funding only — no historical data available.
}

# Original weights — used for comparison table and confidence-blend fallback.
_ORIGINAL_WEIGHTS: dict[str, float] = {
    "rsi_divergence":     3.5,
    "rs_vs_btc":          3.0,
    "macd_turning":       2.5,
    "stealth_accum":      2.5,
    "funding_neg":        2.0,
    "cmf":                2.0,
    "vol_expansion":      2.0,
    "bb_squeeze":         2.0,
    "higher_lows":        2.0,
    "rs_acceleration":    1.5,
    "declining_sell_vol": 1.5,
    "rsi_ignition":       1.5,
    "whale_candles":      1.5,
    "macd_crossover":     1.5,
    "vol_velocity":       1.5,
    "trend_strong":       1.5,
    "atr_expanding":      1.0,
    "rsi_in_zone":        0.5,
}

_WEIGHT_MIN     = 0.3    # Floor — signals never fully disabled, just de-weighted
_WEIGHT_MAX     = 4.5    # Ceiling — even outstanding signals get capped
_TARGET_TOTAL   = sum(_ORIGINAL_WEIGHTS.values())   # ~33.5 — keep total stable


def calibrate_weights(
    signal_stats:  list[dict],
    regime_stats:  dict[str, dict[str, dict]],
    dry_run:       bool = False,
) -> dict[str, float]:
    """
    Derive new _WEIGHTS for master_orchestrator.py from backtest results.

    Weight formula (per signal):
      quality = 0.55 × E[SIDEWAYS] + 0.35 × E[BULL] + 0.10 × E[BEAR]

    SIDEWAYS weighted highest because that is the strictest and most common
    regime in the scanner — a signal must earn its place there.

    Confidence blending:
      If a signal fired < 50 times in total the backtest data is thin.
      We blend 50% toward the original normalized weight to avoid over-reacting
      to small samples.

    Scaling:
      Quality scores are mapped linearly to [_WEIGHT_MIN, _WEIGHT_MAX].
      The entire set is then scaled so the total equals _TARGET_TOTAL,
      keeping the conviction score distribution stable in the scanner.

    Fixed signals (funding_neg) are not touched.
    """
    # Quick lookup: signal_name → overall stats dict
    stat_lookup = {s["signal"]: s for s in signal_stats}

    calibratable = [s for s in _ORIGINAL_WEIGHTS if s not in _FIXED_WEIGHTS]

    # ── Step 1: compute quality score per signal ─────────────────────────────
    qualities: dict[str, float] = {}
    for sig in calibratable:
        bull_e = side_e = bear_e = 0.0

        bull_stats = regime_stats.get("BULL",     {}).get(sig, {})
        side_stats = regime_stats.get("SIDEWAYS", {}).get(sig, {})
        bear_stats = regime_stats.get("BEAR",     {}).get(sig, {})

        if bull_stats.get("n", 0) >= 10:
            bull_e = float(bull_stats.get("expectancy", 0.0))
        if side_stats.get("n", 0) >= 10:
            side_e = float(side_stats.get("expectancy", 0.0))
        if bear_stats.get("n", 0) >= 10:
            bear_e = float(bear_stats.get("expectancy", 0.0))

        # If no regime has enough fires, fall back to overall expectancy
        overall_n = stat_lookup.get(sig, {}).get("n", 0)
        if bull_stats.get("n", 0) < 10 and side_stats.get("n", 0) < 10:
            overall_e = float(stat_lookup.get(sig, {}).get("expectancy", 0.0))
            qualities[sig] = overall_e
        else:
            qualities[sig] = 0.55 * side_e + 0.35 * bull_e + 0.10 * bear_e

    # ── Step 2: confidence blend (thin data → stay near original) ────────────
    # Compute what the "original normalised quality" looks like so we can blend.
    # Original weights normalised to [0,1] relative to their own range give us
    # a reference expectancy proxy per signal.
    orig_vals = [_ORIGINAL_WEIGHTS[s] for s in calibratable]
    orig_min, orig_max = min(orig_vals), max(orig_vals)
    orig_range = orig_max - orig_min or 1.0

    q_vals_raw = list(qualities.values())
    q_min = min(q_vals_raw)
    q_max = max(q_vals_raw)
    q_range = q_max - q_min if q_max != q_min else 1.0

    for sig in calibratable:
        n_fires = stat_lookup.get(sig, {}).get("n", 0)
        confidence = min(1.0, n_fires / 50)   # full confidence at 50+ total fires

        # Original weight expressed on the same 0→1 normalised scale as quality
        orig_norm = (_ORIGINAL_WEIGHTS[sig] - orig_min) / orig_range
        # Rescale to quality range so the blend is in the same units
        orig_as_quality = q_min + orig_norm * q_range

        qualities[sig] = confidence * qualities[sig] + (1 - confidence) * orig_as_quality

    # ── Step 3: map quality → raw weight ─────────────────────────────────────
    q_vals   = [qualities[s] for s in calibratable]
    q_min    = min(q_vals)
    q_max    = max(q_vals)
    q_range  = q_max - q_min if q_max != q_min else 1.0

    raw_weights: dict[str, float] = {}
    for sig in calibratable:
        q = qualities[sig]
        if q <= 0:
            # Negative-expectancy signals get floor weight (barely alive)
            raw_weights[sig] = _WEIGHT_MIN
        else:
            # Linear map [0, q_max] → [_WEIGHT_MIN, _WEIGHT_MAX]
            q_pos_max = max(q_max, 1e-9)
            raw_weights[sig] = _WEIGHT_MIN + (q / q_pos_max) * (_WEIGHT_MAX - _WEIGHT_MIN)

    # ── Step 4: scale total to _TARGET_TOTAL ─────────────────────────────────
    fixed_total = sum(_FIXED_WEIGHTS.values())
    target_cal  = _TARGET_TOTAL - fixed_total
    raw_total   = sum(raw_weights.values())
    scale       = target_cal / raw_total if raw_total > 0 else 1.0

    for sig in raw_weights:
        # Scale, round to nearest 0.5, clamp to [_WEIGHT_MIN, _WEIGHT_MAX]
        w = raw_weights[sig] * scale
        w = round(w * 2) / 2          # snap to 0.5 grid
        raw_weights[sig] = max(_WEIGHT_MIN, min(_WEIGHT_MAX, w))

    # Merge calibrated + fixed
    new_weights = {**raw_weights, **_FIXED_WEIGHTS}

    # ── Step 5: print comparison table ───────────────────────────────────────
    sep  = "=" * 72
    dash = "-" * 72
    print(f"\n{sep}")
    print(f"  AUTO-CALIBRATION RESULTS")
    print(f"  Regime weight: SIDEWAYS 55% + BULL 35% + BEAR 10%")
    print(sep)
    print(f"  {'Signal':<25} {'Old':>5}  {'New':>5}  {'Quality':>9}  {'Change':>8}")
    print(f"  {dash}")

    ordered = sorted(_ORIGINAL_WEIGHTS, key=lambda s: -new_weights.get(s, 0))
    for sig in ordered:
        old = _ORIGINAL_WEIGHTS[sig]
        new = new_weights.get(sig, old)
        q   = qualities.get(sig, 0.0)
        chg = new - old
        arrow   = " ↑" if chg > 0.4 else (" ↓" if chg < -0.4 else "  ")
        fixed_m = " [fixed]" if sig in _FIXED_WEIGHTS else ""
        n_fires = stat_lookup.get(sig, {}).get("n", 0)
        conf    = min(100, int(n_fires / 50 * 100))
        print(
            f"  {sig:<25} {old:>5.1f}  {new:>5.1f}  {q:>+9.5f}"
            f"  {chg:>+7.1f}{arrow}  (n={n_fires}, conf={conf}%){fixed_m}"
        )

    old_total = sum(_ORIGINAL_WEIGHTS.values())
    new_total = sum(new_weights.values())
    print(f"  {dash}")
    print(f"  {'TOTAL':<25} {old_total:>5.1f}  {new_total:>5.1f}")
    print(sep)

    if dry_run:
        print("\n  [--dry-run] No changes written. Pass without --dry-run to apply.")
        return new_weights

    # ── Step 6: patch master_orchestrator.py ─────────────────────────────────
    _patch_orchestrator_weights(new_weights)

    # ── Step 7: save calibration record ──────────────────────────────────────
    record = {
        "timestamp":       datetime.now().isoformat(),
        "old_weights":     _ORIGINAL_WEIGHTS,
        "new_weights":     new_weights,
        "quality_scores":  {s: round(qualities.get(s, 0.0), 6) for s in new_weights},
        "signal_n_fires":  {s: stat_lookup.get(s, {}).get("n", 0) for s in new_weights},
    }
    record_path = _OUTPUT_DIR / "calibration_record_LATEST.json"
    record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"\n  Calibration record saved → {record_path.name}")

    return new_weights


def _patch_orchestrator_weights(new_weights: dict[str, float]) -> None:
    """
    Replace the _WEIGHTS = {...} block in master_orchestrator.py in-place.
    Creates a .bak backup of the original before writing.
    """
    if not _ORCHESTRATOR_PATH.exists():
        print(f"  WARNING: master_orchestrator.py not found at {_ORCHESTRATOR_PATH}")
        return

    content = _ORCHESTRATOR_PATH.read_text(encoding="utf-8")

    # ── Backup ────────────────────────────────────────────────────────────────
    bak = _ORCHESTRATOR_PATH.with_suffix(".py.bak")
    shutil.copy2(_ORCHESTRATOR_PATH, bak)

    # ── Build replacement block ───────────────────────────────────────────────
    ts_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    ordered = sorted(new_weights.items(), key=lambda kv: -kv[1])
    max_key_len = max(len(k) for k in new_weights)

    lines = [
        "_WEIGHTS = {",
        f'    # Auto-calibrated by backtest_signals.py on {ts_str}',
        f'    # Backup: {bak.name}  |  Re-run backtest_signals.py to recalibrate.',
        f'    # ── Sorted by weight (highest → lowest) ────────────────────────────────────',
    ]
    for k, v in ordered:
        # Preserve inline comment so humans know what each signal does
        orig_comment = _get_original_comment(k)
        padding = max_key_len - len(k)
        lines.append(f'    "{k}":{" " * (padding + 1)}{v},{orig_comment}')
    lines.append("}")

    new_block = "\n".join(lines)

    # ── Regex replace ─────────────────────────────────────────────────────────
    # Match from "_WEIGHTS = {" through the closing lone "}" line
    pattern = r'_WEIGHTS\s*=\s*\{[^}]*\}'
    if not re.search(pattern, content, re.DOTALL):
        print("  WARNING: Could not locate _WEIGHTS block. No changes written.")
        return

    new_content = re.sub(pattern, new_block, content, flags=re.DOTALL)
    _ORCHESTRATOR_PATH.write_text(new_content, encoding="utf-8")

    total = sum(new_weights.values())
    print(f"\n  master_orchestrator.py updated successfully.")
    print(f"    Backup  : {bak.name}")
    print(f"    _TOTAL_WEIGHT will recompute to {total:.1f} on next import.")


def _get_original_comment(signal: str) -> str:
    """Return the inline comment for each signal (for documentation in patched file)."""
    comments = {
        "rsi_divergence":     "   # Price lower-low, RSI higher-low — earliest signal",
        "rs_vs_btc":          "   # Token outperforming BTC 7-day (alpha rotation)",
        "macd_turning":       "   # Histogram rising from its trough before zero-cross",
        "stealth_accum":      "   # OBV rising while price flat (smart money)",
        "funding_neg":        "   # Negative perp funding = shorts paying longs (free carry)",
        "cmf":                "   # Chaikin Money Flow > 0.05 — institutional buying",
        "vol_expansion":      "   # Recent 24h vol ≥ 1.5× 1-week baseline (fresh capital)",
        "bb_squeeze":         "   # Volatility compression — coiling before explosion",
        "higher_lows":        "   # Ascending swing lows: base-building structure",
        "rs_acceleration":    "   # Short-term RS (28h) confirms momentum building",
        "declining_sell_vol": "   # Red-candle volume shrinking — sellers exhausting",
        "rsi_ignition":       "   # RSI leaving oversold zone",
        "whale_candles":      "   # Large bullish candles, close in upper 30% of range",
        "macd_crossover":     "   # MACD histogram just crossed zero from below",
        "vol_velocity":       "   # Volume accelerating (short MA > long MA)",
        "trend_strong":       "   # ADX > threshold and +DI > -DI",
        "atr_expanding":      "   # Volatility expanding (energy building)",
        "rsi_in_zone":        "   # RSI in 32–65 sweet-spot (broad filter only)",
    }
    return comments.get(signal, "")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN BACKTEST LOOP
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest(
    n_coins:       int | None  = None,
    days:          int | None  = None,
    signal_filter: str | None  = None,
    auto_calibrate: bool        = True,
    dry_run:        bool        = False,
) -> tuple[list, list]:

    n_coins = n_coins or CONFIG["n_coins"]
    days    = days    or CONFIG["days"]
    ts_str  = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"\n{'='*70}")
    print(f"  SIGNAL BACKTEST ENGINE  v1.0")
    print(f"  {n_coins} coins | {days} days | ATR stop {CONFIG['atr_stop_mult']}× | "
          f"R:R {CONFIG['tp_rr']}")
    print(f"{'='*70}\n")

    # ── 1. BTC history for regime + RS ───────────────────────────────────────
    print("[1/4] Fetching BTC history...")
    btc_df = fetch_binance_ohlcv("BTC", days)
    if btc_df is None:
        print("  ERROR: Cannot fetch BTC data. Aborting.")
        sys.exit(1)

    btc_close = btc_df["close"]
    regime_s  = classify_regime(btc_close)
    n_bull = (regime_s == "BULL").sum()
    n_side = (regime_s == "SIDEWAYS").sum()
    n_bear = (regime_s == "BEAR").sum()
    print(f"  BTC: {len(btc_close)} bars | "
          f"BULL {n_bull} ({n_bull/len(btc_close)*100:.0f}%) | "
          f"SIDEWAYS {n_side} ({n_side/len(btc_close)*100:.0f}%) | "
          f"BEAR {n_bear} ({n_bear/len(btc_close)*100:.0f}%)\n")

    # ── 2. Coin universe ──────────────────────────────────────────────────────
    print("[2/4] Fetching coin universe from CoinGecko...")
    coins = fetch_coin_universe(n_coins)
    print(f"  {len(coins)} coins fetched\n")

    # ── 3. Per-coin signal computation + trade simulation ────────────────────
    print("[3/4] Computing signals + simulating trades...\n")

    all_signal_keys = [
        "rsi_in_zone", "rsi_ignition", "rsi_divergence",
        "macd_crossover", "macd_turning", "trend_strong",
        "rs_vs_btc", "rs_acceleration", "atr_expanding",
        "bb_squeeze", "vol_velocity", "vol_expansion",
        "stealth_accum", "cmf", "whale_candles",
        "higher_lows", "declining_sell_vol",
    ]
    if signal_filter:
        active_keys = [s for s in all_signal_keys if s == signal_filter]
        if not active_keys:
            print(f"  ERROR: unknown signal '{signal_filter}'. "
                  f"Valid: {all_signal_keys}")
            sys.exit(1)
    else:
        active_keys = all_signal_keys

    # Storage: signal → list of trade dicts
    per_signal: dict[str, list] = {sk: [] for sk in active_keys}
    per_regime: dict[str, dict[str, list]] = {
        r: {sk: [] for sk in active_keys}
        for r in ["BULL", "SIDEWAYS", "BEAR"]
    }
    # Combination storage: frozenset of signal names → list of trade dicts
    combo_dict: dict[frozenset, list] = {}

    combo_keys = [k for k in active_keys if k in _COMBO_SIGNALS]

    warmup    = CONFIG["warmup_bars"]
    processed = 0

    for coin in coins:
        symbol = coin["symbol"].upper()
        rank   = coin.get("market_cap_rank", "?")
        processed += 1
        print(f"  [{processed}/{len(coins)}] {symbol:<10} (#{rank})")

        ohlcv = fetch_binance_ohlcv(symbol, days)
        if ohlcv is None or len(ohlcv) < CONFIG["min_bars"]:
            print(f"    skip — insufficient data")
            continue

        sigs     = compute_all_signals(ohlcv, btc_close, verbose=False)
        atr_vals = _atr_s(ohlcv["high"], ohlcv["low"], ohlcv["close"],
                          CONFIG["atr_window"])

        # Align regime to coin's timestamps
        reg_al = regime_s.reindex(ohlcv.index, method="ffill").fillna("SIDEWAYS")

        n_bars     = len(ohlcv)
        coin_fires = 0

        for bar_i in range(warmup, n_bars - 1):
            atr_val = float(atr_vals.iloc[bar_i])
            if np.isnan(atr_val) or atr_val <= 0:
                continue

            regime   = str(reg_al.iloc[bar_i])
            bar_sigs = {sk: bool(sigs[sk].iloc[bar_i]) for sk in active_keys}
            fired    = [sk for sk, v in bar_sigs.items() if v]

            if not fired:
                continue

            # Simulate the trade once per bar
            trade = simulate_trade(ohlcv, bar_i, atr_val)
            trade["symbol"]  = symbol
            trade["bar_ts"]  = str(ohlcv.index[bar_i])
            trade["regime"]  = regime

            coin_fires += 1

            # Record per-signal
            for sk in fired:
                trade_copy = {**trade, "signal": sk}
                per_signal[sk].append(trade_copy)
                per_regime[regime][sk].append(trade_copy)

            # Record per-combination (pairs + triplets among combo-eligible signals)
            combo_fired = [sk for sk in fired if sk in combo_keys]
            if len(combo_fired) >= 2:
                for size in range(2, min(CONFIG["max_combo_size"] + 1,
                                        len(combo_fired) + 1)):
                    for combo in itertools.combinations(sorted(combo_fired), size):
                        key = frozenset(combo)
                        if key not in combo_dict:
                            combo_dict[key] = []
                        combo_dict[key].append(trade)

        print(f"    {n_bars} bars | {coin_fires} signal fires")
        time.sleep(0.05)

    # ── 4. Statistics + report ────────────────────────────────────────────────
    print(f"\n[4/4] Computing statistics and building report...")

    # Per-signal overall stats
    signal_stats = [compute_stats(per_signal[sk], sk) for sk in active_keys]
    signal_stats.sort(key=lambda x: x["expectancy"], reverse=True)

    # Per-regime stats (stored as nested dict for report builder)
    regime_stats_for_report: dict[str, dict[str, dict]] = {
        r: {
            sk: compute_stats(per_regime[r][sk])
            for sk in active_keys
        }
        for r in ["BULL", "SIDEWAYS", "BEAR"]
    }

    # Combination stats
    combo_stats = []
    for key, trades in combo_dict.items():
        if len(trades) < CONFIG["min_combo_fires"]:
            continue
        label = " + ".join(sorted(key))
        combo_stats.append(compute_stats(trades, label))
    combo_stats.sort(key=lambda x: x["expectancy"], reverse=True)

    # Save all trades to CSV
    all_rows = []
    for sk, trades in per_signal.items():
        all_rows.extend(trades)
    if all_rows:
        df_trades  = pd.DataFrame(all_rows)
        trades_csv = _OUTPUT_DIR / f"backtest_trades_{ts_str}.csv"
        df_trades.to_csv(trades_csv, index=False)
        print(f"  Trades CSV  → {trades_csv}  ({len(all_rows)} rows)")

    # Save signal stats CSV
    stats_df  = pd.DataFrame(signal_stats)
    stats_csv = _OUTPUT_DIR / f"backtest_stats_{ts_str}.csv"
    stats_df.to_csv(stats_csv, index=False)
    print(f"  Stats CSV   → {stats_csv}")

    # Build and save text report
    report = _build_report(
        signal_stats, combo_stats, regime_stats_for_report,
        n_coins, days, len(btc_close), ts_str,
    )
    report_path = _OUTPUT_DIR / f"backtest_summary_{ts_str}.txt"
    latest_path = _OUTPUT_DIR / "backtest_summary_LATEST.txt"
    report_path.write_text(report, encoding="utf-8")
    latest_path.write_text(report, encoding="utf-8")

    print(f"  Report      → {report_path}\n")
    print(report)

    # ── Auto-calibration ──────────────────────────────────────────────────────
    # Skipped when --signal is set (partial run — not enough data to recalibrate).
    if auto_calibrate and signal_filter is None:
        print("\n" + "=" * 70)
        print("  STEP 5 — AUTO-CALIBRATING master_orchestrator.py weights...")
        print("=" * 70)
        calibrate_weights(
            signal_stats,
            regime_stats_for_report,
            dry_run=dry_run,
        )
    elif signal_filter:
        print("\n  [--signal mode] Auto-calibration skipped (partial run).")
    else:
        print("\n  [--no-calibrate] Auto-calibration skipped.")

    return signal_stats, combo_stats


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Signal Backtest Engine — validates scanner signals and auto-calibrates weights",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python backtest_signals.py                         # full run, auto-calibrate
  python backtest_signals.py --coins 20 --days 365   # faster run, auto-calibrate
  python backtest_signals.py --dry-run               # show new weights, don't write
  python backtest_signals.py --no-calibrate          # backtest only, skip weight update
  python backtest_signals.py --signal rsi_divergence # single signal, no calibration
        """,
    )
    parser.add_argument(
        "--coins", type=int, default=None,
        help=f"Coins to test (default: {CONFIG['n_coins']})",
    )
    parser.add_argument(
        "--days", type=int, default=None,
        help=f"Days of history (default: {CONFIG['days']})",
    )
    parser.add_argument(
        "--signal", type=str, default=None,
        help="Test a single signal by name (default: all 17)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Compute and display new weights but do NOT write master_orchestrator.py",
    )
    parser.add_argument(
        "--no-calibrate", action="store_true", default=False,
        help="Skip auto-calibration entirely — backtest report only",
    )
    args = parser.parse_args()
    run_backtest(
        n_coins=args.coins,
        days=args.days,
        signal_filter=args.signal,
        auto_calibrate=not args.no_calibrate,
        dry_run=args.dry_run,
    )
