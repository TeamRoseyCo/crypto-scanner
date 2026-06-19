"""
================================================================================
MACRO WATCH  v1.0  — DXY + US Treasury yields gauge for crypto regime
================================================================================
Crypto is currently driven by MACRO (real yields + the dollar), not crypto news.
This pulls the actual steering wheel — DXY and the 10Y/30Y yields — and prints a
risk-on / risk-off verdict for crypto, because crypto can't sustainably bottom
until the dollar and yields peak and roll over.

Source: Yahoo Finance chart API (free, no key). Symbols:
  DX-Y.NYB = US Dollar Index (DXY)
  ^TNX = 10Y Treasury yield   ^TYX = 30Y yield   ^FVX = 5Y yield
  GC=F = gold (context)        BTC-USD = bitcoin (context)

Usage:
  python macro_watch.py                 # one-shot dashboard
  python macro_watch.py --watch         # refresh loop (default 300s)
  python macro_watch.py --watch --interval 600
  # turn-signal alert: fire when the headwind eases
  python macro_watch.py --watch --alert-dxy-below 99.0 --alert-y10-below 4.30

Turn thesis (as of 2026-06-19): headwind = DXY ~100 + 10Y ~4.5%. The crypto turn
needs DXY rolling back under ~99 AND 10Y back under ~4.30%. Until then, bounces
are fades.
================================================================================
"""
import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone

# Windows consoles default to cp1252 and choke on the arrow glyphs — force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SYMS = [
    ("DX-Y.NYB", "DXY  (US Dollar Index)"),
    ("^TNX", "US 10Y yield %"),
    ("^TYX", "US 30Y yield %"),
    ("^FVX", "US 5Y yield %"),
    ("GC=F", "Gold $"),
    ("BTC-USD", "Bitcoin $"),
]


LOOKBACK = 5  # sessions for the trend change


def yf_quote(sym):
    """Return (last, baseline_5d_ago, pct_change_5d). Uses a multi-session trend
    because Yahoo's 1-day field is unreliable for indices (missing days, the yield
    index lags intraday) — and the macro DIRECTION over a few days is what matters."""
    u = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1mo&interval=1d"
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.load(r)
    res = d["chart"]["result"][0]
    meta = res["meta"]
    closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
    if not closes:
        return None, None, None
    last = meta.get("regularMarketPrice") or closes[-1]
    base = closes[-(LOOKBACK + 1)] if len(closes) > LOOKBACK else closes[0]
    pct = ((last - base) / base * 100) if base else None
    return last, base, pct


def fetch_all():
    out = {}
    for sym, label in SYMS:
        try:
            out[sym] = (label, *yf_quote(sym))
        except Exception as e:
            out[sym] = (label, None, None, None)
    return out


def verdict(data):
    """Risk-on/off read for crypto from DXY + 10Y direction."""
    dxy = data.get("DX-Y.NYB", (None, None, None, None))
    y10 = data.get("^TNX", (None, None, None, None))
    dxy_chg, y10_chg = dxy[3], y10[3]
    if dxy_chg is None or y10_chg is None:
        return "?", "macro data unavailable"
    dollar_up = dxy_chg > 0.15
    yields_up = y10_chg > 0.30
    dollar_dn = dxy_chg < -0.15
    yields_dn = y10_chg < -0.30
    if dollar_up and yields_up:
        return "RISK-OFF", "Dollar UP + yields UP = headwind for crypto. Bounces are fades. STAY FLAT."
    if dollar_dn and yields_dn:
        return "RISK-ON", "Dollar DOWN + yields DOWN = tailwind building. THIS is the macro turn — re-engage the scanner."
    if dollar_up or yields_up:
        return "MIXED-NEG", "One of {dollar, yields} rising = still net headwind. No turn yet."
    if dollar_dn or yields_dn:
        return "MIXED-POS", "One of {dollar, yields} easing = early thaw, not confirmation. Watch the other."
    return "NEUTRAL", "Dollar + yields flat = no macro signal either way."


def fmt(v, dec):
    return f"{v:.{dec}f}" if v is not None else "n/a"


def dashboard(data):
    line = "=" * 64
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n{line}\n MACRO WATCH  —  {stamp}\n{line}")
    for sym, label in SYMS:
        lbl, last, prev, pct = data[sym]
        dec = 3 if sym in ("^TNX", "^TYX", "^FVX", "DX-Y.NYB") else 1
        arrow = "→"
        if pct is not None:
            arrow = "▲" if pct > 0.05 else ("▼" if pct < -0.05 else "→")
        pcts = f"{pct:+.2f}% 5d" if pct is not None else "  n/a   "
        print(f"  {lbl:24} {fmt(last, dec):>10}  {arrow} {pcts}")
    tag, msg = verdict(data)
    print(line)
    print(f"  CRYPTO MACRO REGIME: {tag}")
    print(f"  {msg}")
    print(f"{line}\n", flush=True)
    return tag


def notify(title, msg):
    try:
        import winsound
        for _ in range(4):
            winsound.Beep(880, 250)
            winsound.Beep(1320, 250)
    except Exception:
        print("\a" * 4, flush=True)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, title, 0x40 | 0x10000 | 0x40000)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description="DXY + Treasury yields macro gauge for crypto")
    ap.add_argument("--watch", action="store_true", help="loop and refresh")
    ap.add_argument("--interval", type=float, default=300)
    ap.add_argument("--alert-dxy-below", type=float, default=None,
                    help="fire alert when DXY falls below this (macro turn)")
    ap.add_argument("--alert-y10-below", type=float, default=None,
                    help="fire alert when 10Y yield falls below this (macro turn)")
    args = ap.parse_args()

    fired = False
    while True:
        data = fetch_all()
        dashboard(data)
        if not fired and (args.alert_dxy_below or args.alert_y10_below):
            dxy = data.get("DX-Y.NYB", (None, None, None, None))[1]
            y10 = data.get("^TNX", (None, None, None, None))[1]
            hit = []
            if args.alert_dxy_below and dxy is not None and dxy < args.alert_dxy_below:
                hit.append(f"DXY {dxy:.3f} < {args.alert_dxy_below}")
            if args.alert_y10_below and y10 is not None and y10 < args.alert_y10_below:
                hit.append(f"10Y {y10:.3f}% < {args.alert_y10_below}%")
            if hit:
                msg = "MACRO TURN SIGNAL — headwind easing:\n" + "\n".join(hit) + \
                      "\nDollar/yields rolling over = crypto can finally bottom. Re-engage scanner."
                print("\n*** " + msg.replace("\n", " | ") + " ***\n", flush=True)
                notify("MACRO TURN SIGNAL", msg)
                fired = True
                if not args.watch:
                    return 0
        if not args.watch:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
