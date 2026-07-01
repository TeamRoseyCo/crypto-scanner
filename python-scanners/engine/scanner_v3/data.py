"""
================================================================================
DATA LAYER  v3.1
================================================================================
Single fetcher for all OHLCV + ticker + funding + OI data.
"""

from __future__ import annotations
import json, logging, os, sys, time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
import pandas as pd, requests

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent.parent
_CACHE_DIR = _PROJECT_ROOT / "cache" / "shared_ohlcv"
_LOG_DIR = _PROJECT_ROOT / "outputs" / "logs"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_LOG_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("scanner_v3.data")
if not log.handlers:
    handler = logging.FileHandler(_LOG_DIR / f"data_{datetime.now().strftime('%Y%m%d')}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)

@dataclass(frozen=True)
class DataConfig:
    bybit_api: str = "https://api.bybit.com"
    binance_api: str = "https://api.binance.com/api/v3"
    binance_fapi: str = "https://fapi.binance.com/fapi/v1"
    user_agent: str = "scanner_v3/2.1"
    cache_max_age_h: float = 1.5
    btc_cache_max_age_h: float = 1.0
    request_timeout_s: tuple = (5, 15)
    bybit_min_volume: float = 500_000
    binance_min_volume: float = 200_000

CFG = DataConfig()
STABLECOINS_AND_WRAPPED = frozenset({"USDT", "USDC", "DAI", "BUSD", "TUSD", "USDD", "FDUSD", "PYUSD", "USDE", "SUSDE", "BFUSD", "RLUSD", "USDG", "USD0", "GHO", "USDAI", "WBTC", "WETH", "STETH", "RETH", "CBETH", "PAXG", "XAUT", "TBTC", "WBNB", "JITOSOL", "MSOL", "BNSOL", "EURC", "FRAX", "LUSD", "SUSD", "CRVUSD", "GUSD", "USDS", "SUSDS", "FRXETH", "OETH", "SUPRETH"})

from http_client import make_session, CircuitBreaker
_BYBIT_SESSION = make_session(user_agent=CFG.user_agent)
_BINANCE_SESSION = make_session(user_agent=CFG.user_agent)
_BREAKER = CircuitBreaker(threshold=5, cooloff_s=60)

TF_BYBIT = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720", "1d": "D"}
TF_BINANCE = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "12h": "12h", "1d": "1d"}
TF_DELTA = {"1m": timedelta(minutes=1), "5m": timedelta(minutes=5), "15m": timedelta(minutes=15), "30m": timedelta(minutes=30), "1h": timedelta(hours=1), "2h": timedelta(hours=2), "4h": timedelta(hours=4), "6h": timedelta(hours=6), "12h": timedelta(hours=12), "1d": timedelta(days=1)}

def _drop_unclosed_bars(df, tf):
    if df is None or len(df) == 0: return df
    d = TF_DELTA.get(tf)
    if d is None: return df
    now = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
    return df[(df.index + d) <= now]

def _bybit_universe():
    try:
        r = _BYBIT_SESSION.get(f"{CFG.bybit_api}/v5/market/tickers", params={"category": "linear"}, timeout=CFG.request_timeout_s)
        if r.status_code != 200: return []
        data = r.json()
        if data.get("retCode") != 0: return []
    except: return []
    out = []
    for t in data["result"]["list"]:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"): continue
        base = sym[:-4]
        if base in STABLECOINS_AND_WRAPPED: continue
        try:
            turnover = float(t.get("turnover24h") or 0)
            price = float(t.get("lastPrice") or 0)
        except: continue
        if turnover < CFG.bybit_min_volume or price <= 0: continue
        out.append({"base": base, "symbol_bybit": sym, "price": price, "turnover_24h": turnover, "open_interest": float(t.get("openInterest") or 0), "oi_value": float(t.get("openInterestValue") or 0), "funding_rate": float(t.get("fundingRate") or 0), "price_24h_pct": float(t.get("price24hPcnt") or 0) * 100, "source": "bybit"})
    out.sort(key=lambda x: x["turnover_24h"], reverse=True)
    return out

def _binance_universe():
    try:
        r = _BINANCE_SESSION.get(f"{CFG.binance_api}/ticker/24hr", timeout=CFG.request_timeout_s)
        r.raise_for_status()
        tickers = r.json()
    except: return []
    out = []
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"): continue
        base = sym[:-4]
        if base in STABLECOINS_AND_WRAPPED: continue
        try:
            volume = float(t["quoteVolume"]); price = float(t["lastPrice"]); change = float(t["priceChangePercent"])
        except: continue
        if volume < CFG.binance_min_volume or price <= 0 or change < -50: continue
        out.append({"base": base, "symbol_binance": sym, "price": price, "volume_24h": volume, "price_24h_pct": change, "source": "binance"})
    out.sort(key=lambda x: x["volume_24h"], reverse=True)
    return out

def get_universe(source: Literal["bybit", "binance", "both"] = "both"):
    if source == "bybit": return _bybit_universe()
    if source == "binance": return _binance_universe()
    bybit = {c["base"]: c for c in _bybit_universe()}
    binance = {c["base"]: c for c in _binance_universe()}
    merged = []
    for base in set(bybit) | set(binance):
        b, bn = bybit.get(base, {}), binance.get(base, {})
        merged.append({"base": base, "symbol_bybit": b.get("symbol_bybit"), "symbol_binance": bn.get("symbol_binance"), "price": bn.get("price") or b.get("price") or 0.0, "turnover_24h": b.get("turnover_24h", 0.0), "volume_24h": bn.get("volume_24h", 0.0), "open_interest": b.get("open_interest", 0.0), "oi_value": b.get("oi_value", 0.0), "funding_rate": b.get("funding_rate"), "price_24h_pct": b.get("price_24h_pct", bn.get("price_24h_pct", 0.0)), "on_bybit": bool(b), "on_binance": bool(bn)})
    merged.sort(key=lambda x: max(x.get("turnover_24h", 0.0), x.get("volume_24h", 0.0)), reverse=True)
    return merged

def get_bybit_tickers() -> list[dict]:
    """Raw Bybit ticker list (linear category). Used by perp_scanner."""
    try:
        r = _BYBIT_SESSION.get(
            f"{CFG.bybit_api}/v5/market/tickers",
            params={"category": "linear"},
            timeout=CFG.request_timeout_s,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        if data.get("retCode") != 0:
            return []
        return data["result"]["list"]
    except Exception as e:
        log.error(f"Bybit tickers fetch failed: {e}")
        return []

def _cache_path(base, source, tf): return _CACHE_DIR / f"{base}_{source}_{tf}.csv"

def _bybit_ohlcv(symbol, tf, bars):
    try:
        r = _BYBIT_SESSION.get(f"{CFG.bybit_api}/v5/market/kline", params={"category": "linear", "symbol": symbol, "interval": TF_BYBIT[tf], "limit": min(bars, 1000)}, timeout=CFG.request_timeout_s)
        if r.status_code != 200: return None
        rows = r.json().get("result", {}).get("list", [])
        if not rows: return None
        rows.reverse()
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "turnover"])
        df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms")
        df.set_index("ts", inplace=True)
        for col in ("open", "high", "low", "close", "volume", "turnover"): df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df[["open", "high", "low", "close", "volume"]].dropna()
        return _drop_unclosed_bars(df, tf) if len(df) >= 30 else None
    except: return None

def _binance_ohlcv(symbol, tf, bars):
    try:
        r = _BINANCE_SESSION.get(f"{CFG.binance_api}/klines", params={"symbol": symbol, "interval": TF_BINANCE[tf], "limit": min(bars, 1000)}, timeout=CFG.request_timeout_s)
        if r.status_code != 200: return None
        rows = r.json()
        if not rows: return None
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "base_vol", "close_time", "volume", "trades", "taker_base", "taker_quote", "ignore"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms")
        df.set_index("ts", inplace=True)
        for col in ("open", "high", "low", "close", "volume"): df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df[["open", "high", "low", "close", "volume"]].dropna()
        return _drop_unclosed_bars(df, tf) if len(df) >= 30 else None
    except: return None

def get_ohlcv(base, source, tf="1h", bars=200, use_cache=True):
    cache = _cache_path(base, source, tf)
    if use_cache and cache.exists():
        if (time.time() - cache.stat().st_mtime) / 3600 < CFG.cache_max_age_h:
            try:
                df = pd.read_csv(cache, index_col=0, parse_dates=True)
                if len(df) >= 30: return df
            except: pass
    df = _bybit_ohlcv(f"{base}USDT", tf, bars) if source == "bybit" else _binance_ohlcv(f"{base}USDT", tf, bars)
    if df is not None:
        try: df.to_csv(cache)
        except: pass
    return df

def get_btc(tf="1h", bars=200):
    cache = _cache_path("BTC", "binance", tf)
    if cache.exists() and (time.time() - cache.stat().st_mtime) / 3600 < CFG.btc_cache_max_age_h:
        try:
            df = pd.read_csv(cache, index_col=0, parse_dates=True)
            if len(df) >= 50: return df
        except: pass
    df = _binance_ohlcv("BTCUSDT", tf, bars)
    if df is not None:
        try: df.to_csv(cache)
        except: pass
    return df

# FIX: Unified market regime detection so all scanners use the exact same logic
def get_market_regime() -> tuple[str, float, float]:
    """Unified market regime detection based on BTC 7d performance."""
    btc_1d = get_btc("1d", 50)
    if btc_1d is None or len(btc_1d) < 8:
        log.warning("BTC 1D unavailable for regime detection. Defaulting to sideways.")
        return "sideways", 0.0, 0.0
    closes = btc_1d["close"]
    btc_7d_pct = (float(closes.iloc[-1]) / float(closes.iloc[-8]) - 1) * 100
    btc_24h_pct = (float(closes.iloc[-1]) / float(closes.iloc[-2]) - 1) * 100
    regime = "bull" if btc_7d_pct >= 3.0 else "sideways" if btc_7d_pct >= -7.0 else "bear"
    log.info(f"Market regime: {regime.upper()} (BTC 7d: {btc_7d_pct:+.2f}%, 24h: {btc_24h_pct:+.2f}%)")
    return regime, btc_7d_pct, btc_24h_pct


def get_live_market_change():
    """Unified LIVE BTC 7d/24h change — the SINGLE source of truth for regime.

    Fresh Bybit 4h klines INCLUDING the current forming bar (so iloc[-1] is the
    LIVE price), current close vs exactly 168h ago (7d) and 24h ago. This mirrors
    spot_scanner._market_ctx_from_bybit exactly, so the radar and spot boards
    report ONE consistent, live regime — fixing the stale closed-1D read that
    froze the radar's 7d for a full day.

    No CSV cache and no dropped bars (unlike get_btc/_bybit_ohlcv). Returns
    (btc_7d_pct, btc_24h_pct, btc_price) or None on a fetch miss (caller decides
    the fallback).
    """
    try:
        r = _BYBIT_SESSION.get(
            f"{CFG.bybit_api}/v5/market/kline",
            params={"category": "linear", "symbol": "BTCUSDT", "interval": "240", "limit": 60},
            timeout=CFG.request_timeout_s,
        )
        if r.status_code != 200:
            return None
        rows = r.json().get("result", {}).get("list", [])
        if len(rows) < 43:
            return None
        rows.reverse()                              # Bybit is newest-first → ascending
        closes = [float(x[4]) for x in rows]
        price = closes[-1]                          # live forming 4h bar
        btc_7d  = (price / closes[-43] - 1) * 100   # 42 bars × 4h = 168h = 7d
        btc_24h = (price / closes[-7]  - 1) * 100   # 6 bars × 4h = 24h
        return btc_7d, btc_24h, price
    except Exception:
        return None