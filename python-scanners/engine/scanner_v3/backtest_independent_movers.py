"""
backtest_independent_movers.py — can we catch, PRE-PUMP, the alts that rip
while BTC is flat/down? Does the "independent mover" signal LIFT the base rate,
or does it fire on duds just as often (= L65 breakeven, no edge)?

Hypothesis (Bruno, 07-18): coins ACCUMULATING and OUTPERFORMING BTC on
flat/down-BTC days precede the sticky independent pumps at a better-than-random
rate — the rs_vs_btc + rising-OBV signature the spot scanner already tries to
catch, isolated on down-BTC days.

Method (OHLCV only, no funding → no L65 replay limitation; no lookahead):
  Universe: top ~liquid USDT pairs by 24h quote volume (ex stables/leveraged).
  Walk each coin day by day. At day i, using ONLY closes[..i]:
    PRE-SIGNAL fires if ALL of:
      - coin 3d return > BTC 3d return by >= RS_EDGE   (relative strength)
      - OBV[i] > OBV[i-5]                              (accumulation / rising OBV)
      - coin NOT already pumped: 3d return < PUMP_PCT  (we want PRE-, not mid-)
      - [independent mode] BTC 3d return <= BTC_MAX    (BTC flat/down)
    OUTCOME (peeks ahead — that's scoring, not signal):
      - pump  = max high over next FWD days / close[i]-1 >= PUMP_PCT
      - fwd_ret = close[i+FWD]/close[i]-1           (what a hold actually banked)
      - round_trip = pumped intrabar BUT closed the window back < +5%

  Then the 2x2 that settles it:
      precision = P(pump | signal)   vs   base rate = P(pump) overall
    If precision >> base rate → real predictive lift. If ~equal → no edge.

Run:  python backtest_independent_movers.py                (independent mode)
      python backtest_independent_movers.py --any-btc      (ignore BTC state)
      python backtest_independent_movers.py --pump 20 --fwd 3 --universe 150
"""
import argparse
import sys
import requests

STABLES = {"USDC","FDUSD","TUSD","DAI","BUSD","USDP","USDD","PYUSD","EURT","EUR","USDE"}


def top_universe(n):
    r = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=30)
    r.raise_for_status()
    rows = [x for x in r.json() if x["symbol"].endswith("USDT")]
    def keep(sym):
        base = sym[:-4]
        if base in STABLES: return False
        if any(t in base for t in ("UP","DOWN","BULL","BEAR")) and len(base) > 4: return False
        return True
    rows = [x for x in rows if keep(x["symbol"])]
    rows.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
    return [x["symbol"] for x in rows[:n]]


def klines(symbol, limit):
    try:
        r = requests.get("https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": "1d", "limit": limit}, timeout=20)
        if r.status_code != 200: return None
        rows = r.json()
        if len(rows) < 40: return None
        return rows
    except Exception:
        return None


def obv(closes, vols):
    o = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]: o.append(o[-1] + vols[i])
        elif closes[i] < closes[i-1]: o.append(o[-1] - vols[i])
        else: o.append(o[-1])
    return o


def ret(c, i, n):
    return (c[i] / c[i-n] - 1) * 100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pump", type=float, default=15.0, help="pump threshold %% (fwd high)")
    ap.add_argument("--fwd", type=int, default=3, help="forward window (days)")
    ap.add_argument("--rs-edge", type=float, default=5.0, help="coin must beat BTC 3d by this %%")
    ap.add_argument("--btc-max", type=float, default=1.0, help="BTC 3d return must be <= this %% (independent)")
    ap.add_argument("--any-btc", action="store_true", help="ignore BTC state (control)")
    ap.add_argument("--universe", type=int, default=150, help="top-N liquid coins")
    ap.add_argument("--days", type=int, default=120)
    args = ap.parse_args()

    print(f"Fetching universe (top {args.universe} by 24h volume)...", file=sys.stderr)
    try:
        btc = klines("BTCUSDT", args.days + 10)
        universe = top_universe(args.universe)
    except Exception as e:
        print(f"fetch failed: {e}", file=sys.stderr); sys.exit(1)
    if not btc:
        print("no BTC data", file=sys.stderr); sys.exit(1)
    bc = [float(x[4]) for x in btc]
    # align BTC by open time for per-coin lookup
    btc_by_ts = {int(x[0]): float(x[4]) for x in btc}

    # accumulators
    TP=FP=FN=TN=0          # signal x pump 2x2
    sig_fwd=[]; all_fwd=[]  # forward returns
    round_trips=0; sig_pumps=0
    fired_coins=0; scanned=0

    for k, sym in enumerate(universe):
        kl = klines(sym, args.days + 10)
        if not kl: continue
        scanned += 1
        ts = [int(x[0]) for x in kl]
        c  = [float(x[4]) for x in kl]
        h  = [float(x[2]) for x in kl]
        v  = [float(x[5]) for x in kl]
        o  = obv(c, v)
        n = len(c)
        start = max(6, n - args.days)
        fired_here = False
        for i in range(start, n - args.fwd):
            # BTC 3d return aligned to this bar's timestamp
            b_now = btc_by_ts.get(ts[i]); b_3ago = btc_by_ts.get(ts[i-3])
            if b_now is None or b_3ago is None: continue
            btc_3d = (b_now / b_3ago - 1) * 100
            coin_3d = ret(c, i, 3)
            rs = coin_3d - btc_3d
            obv_rising = o[i] > o[i-5]
            not_pumped = coin_3d < args.pump
            btc_ok = True if args.any_btc else (btc_3d <= args.btc_max)
            signal = (rs >= args.rs_edge) and obv_rising and not_pumped and btc_ok

            fwd_high = (max(h[i+1:i+1+args.fwd]) / c[i] - 1) * 100
            fwd_close = (c[i+args.fwd] / c[i] - 1) * 100
            pumped = fwd_high >= args.pump

            all_fwd.append(fwd_close)
            if signal:
                sig_fwd.append(fwd_close); fired_here = True
                if pumped:
                    TP += 1; sig_pumps += 1
                    if fwd_close < 5.0: round_trips += 1     # spiked then gave it back
                else:
                    FP += 1
            else:
                if pumped: FN += 1
                else: TN += 1
        if fired_here: fired_coins += 1
        if (k+1) % 25 == 0:
            print(f"  ...{k+1}/{len(universe)} scanned", file=sys.stderr)

    tot = TP+FP+FN+TN
    if tot == 0:
        print("no observations", file=sys.stderr); sys.exit(1)
    base_rate = (TP+FN)/tot*100
    precision = TP/(TP+FP)*100 if (TP+FP) else float("nan")
    recall = TP/(TP+FN)*100 if (TP+FN) else float("nan")
    lift = precision/base_rate if base_rate else float("nan")

    def mean(xs): return sum(xs)/len(xs) if xs else float("nan")

    mode = "ANY-BTC (control)" if args.any_btc else f"INDEPENDENT (BTC 3d <= {args.btc_max}%)"
    print("="*74)
    print(f"  INDEPENDENT-MOVER BACKTEST   mode={mode}")
    print(f"  {scanned} coins, ~{args.days}d each   |   pump=+{args.pump:.0f}% within {args.fwd}d")
    print(f"  signal = coin 3d beats BTC by >={args.rs_edge:.0f}% + OBV rising + not-yet-pumped")
    print("="*74)
    print(f"\n  Observations (coin-days) : {tot:,}")
    print(f"  Signal fired             : {TP+FP:,}  (on {fired_coins}/{scanned} coins)")
    print(f"\n  2x2   (pump = +{args.pump:.0f}% high within {args.fwd}d)")
    print(f"                 pump=YES   pump=NO")
    print(f"    signal=YES   {TP:8d}   {FP:8d}")
    print(f"    signal=NO    {FN:8d}   {TN:8d}")
    print("\n  " + "-"*60)
    print(f"  BASE RATE  P(pump)            = {base_rate:5.1f}%   <- random dart")
    print(f"  PRECISION  P(pump | signal)   = {precision:5.1f}%   <- the signal")
    print(f"  LIFT       precision/base     = {lift:5.2f}x")
    print(f"  RECALL     P(signal | pump)   = {recall:5.1f}%   <- pumps we'd catch")
    print("  " + "-"*60)
    print(f"\n  Forward {args.fwd}d close return:")
    print(f"    signal days : {mean(sig_fwd):+6.2f}%   (n={len(sig_fwd):,})")
    print(f"    all days    : {mean(all_fwd):+6.2f}%   (n={len(all_fwd):,})")
    if sig_pumps:
        print(f"\n  Round-trips: {round_trips}/{sig_pumps} signalled pumps "
              f"({round_trips/sig_pumps*100:.0f}%) spiked +{args.pump:.0f}% then closed < +5%")
    print("\n" + "="*74)
    if precision != precision or base_rate == 0:
        print("  inconclusive")
    elif lift >= 1.5 and precision > base_rate + 3:
        print(f"  ✓ REAL LIFT: signal roughly {lift:.1f}x the base rate. Worth adding to confluence.")
        print(f"    (but check recall {recall:.0f}% and round-trip rate before trusting it live.)")
    elif lift >= 1.15:
        print(f"  ~ MARGINAL: {lift:.2f}x lift — weak, likely fragile. Treat as L65: breakeven-ish.")
    else:
        print(f"  ✗ NO EDGE: signal ~{lift:.2f}x base rate = fires on duds as often as winners.")
        print(f"    The pre-pump signature is NOT distinguishable pre-hoc from OHLCV. (L65 confirmed.)")
    print("="*74)


if __name__ == "__main__":
    main()
