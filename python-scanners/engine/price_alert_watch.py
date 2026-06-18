"""
================================================================================
PRICE ALERT WATCHER  v1.0
================================================================================
Polls Bybit for a symbol's last price and fires a loud LOCAL alert when price
crosses a target level. Reliable, dependency-free alternative to TradingView
alerts -- no Chrome / CDP / browser automation required.

On trigger:
  1. Big console banner
  2. Repeated Windows beep (winsound)
  3. Native Windows message box (ctypes MessageBoxW -- pops over other windows)
  4. Writes outputs/alerts/<symbol>_<level>_<dir>.json (a durable trigger flag)
  5. Exits 0 (so a background runner is notified the watch is done)

Usage:
  # pullback alert -- fire when MUUSDT drops to/below 1071
  python price_alert_watch.py --symbol MUUSDT --level 1071 --dir below --label "MU pullback entry"

  # breakout alert -- fire when MUUSDT rises to/above 1135
  python price_alert_watch.py --symbol MUUSDT --level 1135 --dir above --label "MU breakout"

Flags:
  --symbol    Bybit symbol (default MUUSDT)
  --level     price level to watch (required)
  --dir       'below' (price <= level) or 'above' (price >= level)
  --category  bybit category: linear (perp, default) or spot
  --interval  poll seconds (default 30)
  --label     human label shown in the alert
  --keep      keep running after first trigger (default: exit on trigger)
================================================================================
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "outputs", "alerts")


def get_last_price(symbol: str, category: str) -> float:
    url = (
        f"https://api.bybit.com/v5/market/tickers"
        f"?category={category}&symbol={symbol}"
    )
    with urllib.request.urlopen(url, timeout=20) as r:
        data = json.load(r)
    lst = data.get("result", {}).get("list", [])
    if not lst:
        raise RuntimeError(f"no ticker for {symbol} ({category}): {data.get('retMsg')}")
    return float(lst[0]["lastPrice"])


def notify(symbol, level, direction, last, label):
    line = "=" * 70
    msg = (
        f"PRICE ALERT  ::  {symbol}  {direction.upper()} {level}\n"
        f"Last price: {last}\n"
        f"{label}\n"
        f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}"
    )
    print(f"\n{line}\n*** {msg.splitlines()[0]} ***\n{msg}\n{line}\n", flush=True)

    # 2. beep
    try:
        import winsound
        for _ in range(6):
            winsound.Beep(880, 250)
            winsound.Beep(1320, 250)
    except Exception:
        print("\a" * 5, flush=True)

    # 3. native message box (non-blocking thread so script can still exit/loop)
    try:
        import ctypes
        # MB_ICONWARNING | MB_SETFOREGROUND | MB_TOPMOST = 0x30 | 0x10000 | 0x40000
        ctypes.windll.user32.MessageBoxW(0, msg, f"PRICE ALERT: {symbol}", 0x30 | 0x10000 | 0x40000)
    except Exception:
        pass


def write_flag(symbol, level, direction, last, label):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{symbol}_{level}_{direction}.json")
    payload = {
        "symbol": symbol,
        "level": level,
        "direction": direction,
        "triggered_price": last,
        "label": label,
        "triggered_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"[INFO] Wrote trigger flag -> {path}", flush=True)


def crossed(last: float, level: float, direction: str) -> bool:
    return last <= level if direction == "below" else last >= level


def main():
    ap = argparse.ArgumentParser(description="Bybit price alert watcher")
    ap.add_argument("--symbol", default="MUUSDT")
    ap.add_argument("--level", type=float, required=True)
    ap.add_argument("--dir", dest="direction", choices=["below", "above"], required=True)
    ap.add_argument("--category", default="linear", choices=["linear", "spot"])
    ap.add_argument("--interval", type=float, default=30)
    ap.add_argument("--label", default="")
    ap.add_argument("--keep", action="store_true", help="keep running after first trigger")
    args = ap.parse_args()

    print(
        f"[INFO] Watching {args.symbol} ({args.category}) for price {args.direction.upper()} "
        f"{args.level}  (poll every {args.interval:g}s)",
        flush=True,
    )

    # Guard: warn if the condition is ALREADY true at startup (level set on wrong side)
    try:
        first = get_last_price(args.symbol, args.category)
        print(f"[INFO] Current {args.symbol} = {first}", flush=True)
        if crossed(first, args.level, args.direction):
            print(
                f"[WARN] Condition already TRUE at start (last {first} {args.direction} {args.level}). "
                f"Check your level/direction. Firing once.",
                flush=True,
            )
    except Exception as e:
        print(f"[WARN] startup price check failed: {e}", flush=True)

    while True:
        try:
            last = get_last_price(args.symbol, args.category)
            stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"[{stamp}] {args.symbol} = {last}", flush=True)
            if crossed(last, args.level, args.direction):
                notify(args.symbol, args.level, args.direction, last, args.label)
                write_flag(args.symbol, args.level, args.direction, last, args.label)
                if not args.keep:
                    return 0
                time.sleep(max(args.interval, 60))  # cooldown if kept alive
        except Exception as e:
            print(f"[WARN] poll error: {e}", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
