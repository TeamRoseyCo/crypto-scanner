"""
backtest_regime.py — does a multi-horizon (term-structure) regime rule beat the
single BTC-7d threshold, and does it EVER loosen the gate?

Current live rule (data.py:201, get_market_regime):
    bull     if btc_7d_pct >= 3.0
    sideways if btc_7d_pct >= -7.0
    bear     otherwise
where btc_7d_pct = close[i] / close[i-7] - 1  (point-to-point, path-blind).

Proposed rule = the SAME base label, plus a DOWNGRADE-ONLY overlay that turns a
BULL into SIDEWAYS when the recent 1-3 days show the weekly bull is fading /
rolling over (encodes L57 deceleration-not-turn, L62 turn-came-and-faded). It
can NEVER upgrade sideways->bull or touch bear — so it is structurally incapable
of loosening the gate. We then measure BTC's FORWARD return on the downgrade
days: if forward returns are lower/negative there, the downgrade was protective;
if they're similar/higher, it cost us.

No lookahead: every label at index i uses only closes[0..i] (closed daily bars).
Forward-return validation obviously peeks ahead — that's the scoring, not the
signal.

Run:  python backtest_regime.py            (fetches BTC 1D from Binance)
      python backtest_regime.py --days 365
"""
import argparse
import sys
import requests

# --- live thresholds, mirrored from data.py:201 -----------------------------
BULL_7D = 3.0
BEAR_7D = -7.0


def base_regime(ret7_pct: float) -> str:
    """The current single-7d rule, verbatim."""
    return "bull" if ret7_pct >= BULL_7D else "sideways" if ret7_pct >= BEAR_7D else "bear"


def ret(closes, i, n):
    """% return of close[i] vs n bars earlier — mirrors close[-1]/close[-1-n]."""
    return (closes[i] / closes[i - n] - 1) * 100


def proposed_regime(closes, i):
    """Base label + downgrade-only overlay. Returns (label, reason)."""
    r7 = ret(closes, i, 7)
    base = base_regime(r7)
    if base != "bull":
        return base, "base"                        # never touch sideways/bear
    d1 = ret(closes, i, 1)
    d2 = ret(closes, i, 2)
    d3 = ret(closes, i, 3)
    # Trigger A: the last 48h has net given back ground despite a +3% week.
    if d2 <= 0:
        return "sideways", "fading_2d"
    # Trigger B: rolling over — today red AND the 3-day net is red.
    if d1 < 0 and d3 < 0:
        return "sideways", "rolling_3d"
    return "bull", "base"


def fetch_btc_1d(bars=500):
    """Direct Binance 1D klines, ascending, closed bars only (drop forming day)."""
    r = requests.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": "BTCUSDT", "interval": "1d", "limit": min(bars, 1000)},
        timeout=20,
    )
    r.raise_for_status()
    rows = r.json()
    closes = [float(x[4]) for x in rows]
    times = [int(x[0]) for x in rows]
    # Binance's last row is the still-forming current day → drop it (closed-only).
    return times[:-1], closes[:-1]


def iso(ms):
    import datetime as dt
    return dt.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365, help="lookback window to evaluate")
    ap.add_argument("--fwd", type=int, default=3, help="forward-return horizon (days) for validation")
    args = ap.parse_args()

    need = args.days + 10
    try:
        times, closes = fetch_btc_1d(bars=min(need, 1000))
    except Exception as e:
        print(f"fetch failed: {e}", file=sys.stderr)
        sys.exit(1)

    n = len(closes)
    start = max(7, n - args.days)          # need 7 prior bars for ret7
    fwd = args.fwd

    rows = []
    for i in range(start, n):
        r7 = ret(closes, i, 7)
        base = base_regime(r7)
        prop, reason = proposed_regime(closes, i)
        fwd_ret = (closes[i + fwd] / closes[i] - 1) * 100 if i + fwd < n else None
        rows.append({
            "date": iso(times[i]), "price": closes[i], "r7": r7,
            "r1": ret(closes, i, 1), "r2": ret(closes, i, 2), "r3": ret(closes, i, 3),
            "base": base, "prop": prop, "reason": reason, "fwd": fwd_ret,
        })

    # ---- sanity: proposed must NEVER be looser than base ---------------------
    RANK = {"bear": 0, "sideways": 1, "bull": 2}
    violations = [r for r in rows if RANK[r["prop"]] > RANK[r["base"]]]

    # ---- label distributions ------------------------------------------------
    def dist(key):
        d = {"bull": 0, "sideways": 0, "bear": 0}
        for r in rows:
            d[r[key]] += 1
        return d

    changed = [r for r in rows if r["prop"] != r["base"]]

    print("=" * 78)
    print(f"  REGIME BACKTEST  —  BTC 1D, {len(rows)} days evaluated "
          f"({rows[0]['date']} → {rows[-1]['date']})")
    print(f"  Forward-return horizon: {fwd}d")
    print("=" * 78)
    print(f"\n  Base (single-7d)  label distribution: {dist('base')}")
    print(f"  Proposed (overlay) label distribution: {dist('prop')}")
    print(f"\n  Days where proposed DIFFERS from base: {len(changed)} / {len(rows)}")
    print(f"  Gate-loosening violations (must be 0): {len(violations)}")

    # ---- forward-return validation on bull days -----------------------------
    bull_base = [r for r in rows if r["base"] == "bull" and r["fwd"] is not None]
    stayed = [r for r in bull_base if r["prop"] == "bull"]
    downgraded = [r for r in bull_base if r["prop"] == "sideways"]

    def mean(xs):
        return sum(xs) / len(xs) if xs else float("nan")

    def winrate(rs):
        # fraction with POSITIVE forward return
        pos = [r for r in rs if r["fwd"] > 0]
        return 100 * len(pos) / len(rs) if rs else float("nan")

    print("\n" + "-" * 78)
    print(f"  FORWARD-RETURN VALIDATION  (what BTC did over the next {fwd} days)")
    print("-" * 78)
    print(f"  All base-BULL days           n={len(bull_base):3d}  "
          f"mean fwd {mean([r['fwd'] for r in bull_base]):+6.2f}%  "
          f"pos {winrate(bull_base):5.1f}%")
    print(f"  → STAYED bull (proposed)     n={len(stayed):3d}  "
          f"mean fwd {mean([r['fwd'] for r in stayed]):+6.2f}%  "
          f"pos {winrate(stayed):5.1f}%")
    print(f"  → DOWNGRADED to sideways     n={len(downgraded):3d}  "
          f"mean fwd {mean([r['fwd'] for r in downgraded]):+6.2f}%  "
          f"pos {winrate(downgraded):5.1f}%")
    print("\n  Read: if DOWNGRADED mean-fwd < STAYED mean-fwd, the overlay pulled us")
    print("  out ahead of weaker/negative continuation = protective. If it's >=,")
    print("  the overlay cost us upside on real bull continuation.")

    # ---- the actual downgrade days ------------------------------------------
    if changed:
        print("\n" + "-" * 78)
        print("  DOWNGRADE DAYS  (base=bull → proposed=sideways)")
        print("-" * 78)
        print("  date         price      r7      r2      r1   reason        fwd")
        for r in changed:
            fwd_s = f"{r['fwd']:+6.2f}%" if r["fwd"] is not None else "   n/a"
            print(f"  {r['date']}  {r['price']:9.0f}  {r['r7']:+5.1f}%  "
                  f"{r['r2']:+5.1f}%  {r['r1']:+5.1f}%  {r['reason']:<11}  {fwd_s}")

    print("\n" + "=" * 78)
    if violations:
        print("  ✗ FAIL: overlay loosened the gate on some days (see above) — reject.")
    elif not changed:
        print("  = NEUTRAL: overlay never changed a label in this window — 7d was fine.")
    else:
        dn = mean([r["fwd"] for r in downgraded]) if downgraded else float("nan")
        st = mean([r["fwd"] for r in stayed]) if stayed else float("nan")
        verdict = "PROTECTIVE" if dn < st else "COSTLY"
        print(f"  Overlay is downgrade-only (0 violations). On bull days it flagged "
              f"{len(downgraded)},")
        print(f"  and those days' forward {fwd}d return ({dn:+.2f}%) vs stayed-bull "
              f"({st:+.2f}%) = {verdict}.")
    print("=" * 78)


if __name__ == "__main__":
    main()
