"""
================================================================================
LIVE POSITION MONITOR  v1.0
================================================================================
Polls Bybit every 60s. Shows open positions with live indicators + alerts.

Indicators (1h candles):
  RSI 7 + MA14  |  DMI 14  |  MACD 12/26/9  |  Supertrend 10/3  |  VWAP session

Alerts:
  STOP NEAR (<1.5%)  |  TP NEAR (<1%)  |  Supertrend flip  |  VWAP breakdown
  RSI overbought >75  |  MACD bearish cross

Usage:
  python live_monitor.py              # 60s interval
  python live_monitor.py --interval 30
  python live_monitor.py --watchlist  # also show watchlist from last scan
================================================================================
"""

import argparse
import hashlib
import hmac
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

_ENGINE_DIR   = Path(__file__).resolve().parent
_PROJECT_ROOT = _ENGINE_DIR.parent.parent
_RESULTS_DIR  = _PROJECT_ROOT / "outputs" / "scanner-results"

sys.path.insert(0, str(_ENGINE_DIR))
from indicators import compute_rsi, compute_macd, compute_adx, compute_supertrend

# ── Bybit auth ────────────────────────────────────────────────────────────────
BYBIT_API   = "https://api.bybit.com"
RECV_WINDOW = "5000"
API_KEY     = os.environ.get("BYBIT_API_KEY",    "1GECtl5qxu33yvHbnQ")
API_SECRET  = os.environ.get("BYBIT_API_SECRET", "HJ54DDAyCDbyQSaHr65HVaGvx4gChopA4jgp")

# ── Alert thresholds ──────────────────────────────────────────────────────────
STOP_WARN_PCT = 1.5
TP_NEAR_PCT   = 1.0


def _sign(query: str) -> dict:
    ts      = str(int(time.time() * 1000))
    payload = ts + API_KEY + RECV_WINDOW + query
    sig     = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return {
        "X-BAPI-API-KEY":     API_KEY,
        "X-BAPI-TIMESTAMP":   ts,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN":        sig,
    }


def _get(endpoint: str, query: str, signed: bool = False, retries: int = 3) -> dict | None:
    url = f"{BYBIT_API}{endpoint}?{query}"
    for attempt in range(retries):
        headers = _sign(query) if signed else {}  # fresh timestamp each attempt
        try:
            r = requests.get(url, headers=headers, timeout=10)
            d = r.json()
            if d.get("retCode") == 0:
                return d
            code = d.get("retCode")
            msg  = d.get("retMsg", "")
            print(f"  Bybit error [{code}] {msg}  ({endpoint}, attempt {attempt+1})")
            if code in (10006, 10018):  # rate limit
                time.sleep(2 ** attempt)
            else:
                time.sleep(0.5)
        except Exception as e:
            print(f"  API error {endpoint} attempt {attempt+1}: {e}")
            time.sleep(1)
    return None


def fetch_balance() -> float:
    d = _get("/v5/account/wallet-balance", "accountType=UNIFIED&coin=USDT", signed=True)
    if not d:
        return 0.0
    try:
        return float(d["result"]["list"][0]["totalEquity"])
    except Exception:
        return 0.0


def fetch_positions() -> list[dict]:
    d = _get("/v5/position/list", "category=linear&settleCoin=USDT", signed=True)
    if not d:
        return []
    return [p for p in d["result"]["list"] if float(p.get("size", 0)) > 0]


def fetch_klines(symbol: str, interval: str = "60", limit: int = 150) -> pd.DataFrame | None:
    query = f"category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    d = _get("/v5/market/kline", query)
    if not d:
        return None
    rows = d["result"]["list"]
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "turnover"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["ts"] = pd.to_datetime(df["ts"].astype(np.int64), unit="ms", utc=True)
    return df.sort_values("ts").reset_index(drop=True)


def compute_vwap_session(df: pd.DataFrame) -> tuple[float, float, float]:
    """VWAP from today's session start (00:00 UTC), with 1-std bands."""
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    session = df[df["ts"] >= pd.Timestamp(today)]
    if len(session) < 2:
        session = df.tail(24)  # fallback: last 24 candles
    tp  = (session["high"] + session["low"] + session["close"]) / 3
    vol = session["volume"]
    cum_tv  = (tp * vol).cumsum()
    cum_v   = vol.cumsum().replace(0, np.nan)
    vwap    = cum_tv / cum_v
    dev     = ((tp - vwap) ** 2 * vol).cumsum() / cum_v
    std     = np.sqrt(dev)
    return float(vwap.iloc[-1]), float((vwap + std).iloc[-1]), float((vwap - std).iloc[-1])


def analyze_position(pos: dict) -> dict | None:
    symbol = pos["symbol"]
    side   = pos["side"]        # "Buy" or "Sell"
    entry  = float(pos["avgPrice"])
    size   = float(pos["size"])
    price  = float(pos["markPrice"])
    upnl   = float(pos["unrealisedPnl"])
    lev    = pos.get("leverage", "1")
    stop   = float(pos.get("stopLoss", 0) or 0)
    tp1    = float(pos.get("takeProfit", 0) or 0)
    liq    = float(pos.get("liqPrice", 0) or 0)

    df = fetch_klines(symbol)
    if df is None or len(df) < 50:
        return None

    rsi_s    = compute_rsi(df["close"], 7)
    rsi_ma_s = compute_rsi(df["close"], 14)
    adx_s, pdi_s, mdi_s = compute_adx(df, 14)
    macd_l_s, macd_s_s, _ = compute_macd(df["close"])
    st_bull  = compute_supertrend(df, period=10, multiplier=3.0)

    rsi      = float(rsi_s.iloc[-1])
    rsi_ma   = float(rsi_ma_s.iloc[-1])
    adx      = float(adx_s.iloc[-1])
    pdi      = float(pdi_s.iloc[-1])
    mdi      = float(mdi_s.iloc[-1])
    macd_l   = float(macd_l_s.iloc[-1])
    macd_sig = float(macd_s_s.iloc[-1])
    st_up    = bool(st_bull.iloc[-1])
    prev_st  = bool(st_bull.iloc[-2]) if len(st_bull) > 1 else st_up

    vwap, vwap_up, vwap_dn = compute_vwap_session(df)

    pct = (price - entry) / entry * 100
    if side == "Sell":
        pct = -pct  # for shorts, price going down is profit

    alerts = []
    is_long = side == "Buy"

    if stop > 0:
        dist_stop = abs(price - stop) / price * 100
        if dist_stop <= STOP_WARN_PCT:
            alerts.append(f"STOP NEAR {dist_stop:.1f}%")

    if tp1 > 0:
        dist_tp = abs(tp1 - price) / price * 100
        if dist_tp <= TP_NEAR_PCT:
            alerts.append(f"TP NEAR {dist_tp:.1f}%")

    if is_long and not st_up:
        alerts.append("ST RED")
    if not is_long and st_up:
        alerts.append("ST GREEN (short!)")

    if is_long and price < vwap_dn:
        alerts.append("< VWAP LOWER")
    if not is_long and price > vwap_up:
        alerts.append("> VWAP UPPER")

    if is_long and not st_up and prev_st:
        alerts.append("ST FLIP BEAR")
    if not is_long and st_up and not prev_st:
        alerts.append("ST FLIP BULL")

    if is_long and rsi > 75:
        alerts.append(f"RSI OB {rsi:.0f}")
    if not is_long and rsi < 25:
        alerts.append(f"RSI OS {rsi:.0f}")

    if is_long and macd_l < macd_sig:
        alerts.append("MACD BEAR")

    return {
        "sym":      symbol.replace("USDT", ""),
        "side":     side,
        "lev":      lev,
        "entry":    entry,
        "price":    price,
        "upnl":     upnl,
        "pct":      pct,
        "stop":     stop,
        "tp1":      tp1,
        "liq":      liq,
        "rsi":      rsi,
        "rsi_ma":   rsi_ma,
        "adx":      adx,
        "pdi":      pdi,
        "mdi":      mdi,
        "macd_l":   macd_l,
        "macd_sig": macd_sig,
        "st_up":    st_up,
        "vwap":     vwap,
        "vwap_up":  vwap_up,
        "vwap_dn":  vwap_dn,
        "alerts":   alerts,
    }


def _price_fmt(val: float) -> str:
    if val == 0:
        return "—"
    if val < 0.001:
        return f"{val:.8f}"
    if val < 1:
        return f"{val:.4f}"
    if val < 100:
        return f"{val:.3f}"
    return f"{val:,.2f}"


def display(results: list[dict], balance: float, interval: int, cycle: int) -> None:
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    sys.stdout.write("\033[H\033[J")  # clear screen

    print("=" * 100)
    print(f"  LIVE POSITION MONITOR   {now}   Balance: ${balance:,.2f} USDT   "
          f"Cycle #{cycle}   Next in {interval}s")
    print("=" * 100)

    if not results:
        print("\n  No open positions.\n")
        print("=" * 100)
        return

    H = (f"  {'SYM':<7} {'S':<4} {'L':>2}  {'ENTRY':>12} {'PRICE':>12} "
         f"{'UPNL':>9} {'%':>6}  {'RSI':>5}/{'MA':>4}  "
         f"{'ADX':>5} {'+DI':>5} {'-DI':>5}  {'ST':>5}  VWAP  ALERTS")
    print(H)
    print("  " + "-" * 96)

    total_pnl = 0.0
    for r in results:
        upnl_s  = f"${r['upnl']:+,.1f}"
        pct_s   = f"{r['pct']:+.2f}%"
        st_s    = "UP" if r["st_up"] else "DN"
        vwap_s  = ("^" if r["price"] > r["vwap_up"] else
                   "v" if r["price"] < r["vwap_dn"] else "~")
        alert_s = " | ".join(r["alerts"]) if r["alerts"] else "ok"
        lev_s   = f"{r['lev']}x"

        print(f"  {r['sym']:<7} {r['side'][0]:<4} {lev_s:>2}  "
              f"{_price_fmt(r['entry']):>12} {_price_fmt(r['price']):>12} "
              f"{upnl_s:>9} {pct_s:>6}  "
              f"{r['rsi']:>5.1f}/{r['rsi_ma']:>4.1f}  "
              f"{r['adx']:>5.1f} {r['pdi']:>5.1f} {r['mdi']:>5.1f}  "
              f"{st_s:>5}  {vwap_s:>4}  {alert_s}")
        total_pnl += r["upnl"]

    print("  " + "-" * 96)
    print(f"  {'TOTAL':>7}                                        ${total_pnl:+,.2f} unrealized")

    active_alerts = [(r["sym"], a) for r in results for a in r["alerts"]]
    if active_alerts:
        print()
        print("  !! ALERTS !!")
        for sym, a in active_alerts:
            print(f"     [{sym}]  {a}")

    print()
    print("  STOP/TP LEVELS:")
    for r in results:
        stop_s = _price_fmt(r["stop"]) if r["stop"] > 0 else "not set"
        tp1_s  = _price_fmt(r["tp1"])  if r["tp1"]  > 0 else "not set"
        liq_s  = _price_fmt(r["liq"])  if r["liq"]  > 0 else "—"
        print(f"     {r['sym']:<7}  Stop: {stop_s:<14}  TP1: {tp1_s:<14}  Liq: {liq_s}")

    print("=" * 100)
    print("  Ctrl+C to stop")


def load_watchlist() -> list[str]:
    """Read watchlist symbols from latest master scan output."""
    try:
        latest = _RESULTS_DIR / "master_trade_plan_LATEST.txt"
        if not latest.exists():
            return []
        text  = latest.read_text(encoding="utf-8")
        start = text.find("WATCHLIST")
        if start < 0:
            return []
        section = text[start:]
        syms = []
        for line in section.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0].isdigit():
                raw = parts[1].lstrip("\U0001F534\U0001F7E0\U0001F538\U0001F4D8\U0001F534\U0001F7E1")
                syms.append(raw.upper())
        return syms[:10]
    except Exception:
        return []


def display_watchlist(syms: list[str]) -> None:
    if not syms:
        return
    print()
    print("  WATCHLIST PRICES:")
    print(f"  {'SYM':<8} {'PRICE':>12}  {'24h%':>7}")
    print("  " + "-" * 34)
    for sym in syms:
        query = f"category=linear&symbol={sym}USDT"
        d = _get("/v5/market/tickers", query)
        if not d:
            continue
        try:
            t = d["result"]["list"][0]
            px   = float(t["lastPrice"])
            chg  = float(t["price24hPcnt"]) * 100
            print(f"  {sym:<8} {_price_fmt(px):>12}  {chg:>+7.2f}%")
        except Exception:
            continue
    print("=" * 100)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval",  type=int, default=60)
    parser.add_argument("--watchlist", action="store_true", help="Show watchlist prices")
    args = parser.parse_args()

    print(f"  Live Monitor starting — polling every {args.interval}s. Ctrl+C to stop.")
    cycle = 0

    watchlist = load_watchlist() if args.watchlist else []

    while True:
        try:
            cycle += 1
            balance   = fetch_balance()
            positions = fetch_positions()
            results   = []
            for p in positions:
                r = analyze_position(p)
                if r:
                    results.append(r)
                time.sleep(0.3)  # avoid rate limits between kline fetches

            display(results, balance, args.interval, cycle)

            if args.watchlist:
                display_watchlist(watchlist)

        except KeyboardInterrupt:
            print("\n  Monitor stopped.")
            sys.exit(0)
        except Exception as e:
            print(f"\n  Error in cycle {cycle}: {e}")

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
