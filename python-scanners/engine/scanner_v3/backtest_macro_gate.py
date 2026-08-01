"""
================================================================================
MACRO GATE BACKTESTER  —  does "DXY <99 AND 10Y <4.30" earn its keep?
================================================================================
The live system refuses every entry unless BOTH macro levels print. That gate has
produced ~35 consecutive no-trade days. This asks the only question that settles
whether that is discipline or superstition:

  Of the confluence trades the system WOULD have taken, do the ones during an
  OPEN macro gate actually outperform the ones during a SHUT gate?

If gated trades are materially better, the drought is the price of a real edge.
If they are not, the gate is filtering on noise and should be replaced with
something relative (level vs its own trend) instead of two fixed numbers.

Method — no new signal logic, no drift:
  - Trades come from backtest_confluence.backtest_coin() UNCHANGED: the exact
    spot-accumulation AND trend-tier confluence that is the validated live edge.
  - Each trade is then tagged with the macro state ON ITS ENTRY DATE, using daily
    DXY (DX-Y.NYB) and 10Y (^TNX) closes from the same Yahoo endpoint macro_watch
    uses live. Only closes at or before the entry date are used — no lookahead.
  - Buckets are compared on avg R, win rate and n.

Also tests RELATIVE gate variants, because the live gate is absolute and absolute
levels can simply fail to print for a quarter:
  - dxy_below_ma20 / y10_below_ma20 : the level under its own 20-day mean
  - both_below_ma20                 : the relative twin of the live gate
  - dxy_falling_5d / y10_falling_5d : 5-day rate of change negative

Honest caveats (inherited from backtest_confluence, plus this file's own):
  - Survivorship: today's coin list replayed backward = UPPER bound on R.
  - No fees/slippage — subtract ~0.1-0.15R before believing any bucket.
  - Small n is the expected outcome: confluence is rare BY DESIGN. A bucket under
    ~30 trades is an anecdote, and the report labels it as such rather than
    ranking it.
  - Macro series are daily closes; a trade entered intraday is tagged with the
    prior close. That is the same information the live gate had that morning.

Run:
  python backtest_macro_gate.py --top 80 --days 400
  python backtest_macro_gate.py --coins BTC,ETH,SOL --days 200 --min-tier long
================================================================================
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

import data
from backtester import Trade, _resolve_coins
from backtest_confluence import (
    backtest_coin,
    MIN_1D_BARS,
    _OUTPUT_DIR,
)

log = logging.getLogger("backtest_macro_gate")
if not log.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)

# The live gate, from precommitted_entry_trigger_macro_turn / feedback_macro_gauge_check
LIVE_DXY_MAX = 99.0
LIVE_Y10_MAX = 4.30

# A bucket smaller than this is reported but never ranked or acted on.
MIN_N_TO_TRUST = 30


# ─────────────────────────────────────────────────────────────────────────────
# MACRO SERIES  — same source macro_watch.py uses live
# ─────────────────────────────────────────────────────────────────────────────

def fetch_macro_series(symbol: str, rng: str = "2y") -> Optional[pd.Series]:
    """Daily closes for a Yahoo symbol, indexed by date. None on failure."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?range={rng}&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.loads(r.read().decode())
        result = payload["chart"]["result"][0]
        stamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except Exception as e:
        log.error(f"  macro fetch failed for {symbol}: {e}")
        return None
    idx, vals = [], []
    for t, c in zip(stamps, closes):
        if c is None:
            continue                      # market holidays come back as null
        idx.append(pd.Timestamp(t, unit="s").normalize())
        vals.append(float(c))
    if not vals:
        return None
    return pd.Series(vals, index=pd.DatetimeIndex(idx)).sort_index()


def build_macro_frame(rng: str = "2y") -> Optional[pd.DataFrame]:
    """DXY + 10Y daily closes with the derived gate columns."""
    dxy = fetch_macro_series("DX-Y.NYB", rng)
    y10 = fetch_macro_series("^TNX", rng)
    if dxy is None or y10 is None:
        return None
    df = pd.DataFrame({"dxy": dxy, "y10": y10}).sort_index()
    # Forward-fill: crypto trades weekends, the macro tape does not. A Saturday
    # entry is correctly judged against Friday's close — exactly what the live
    # gate sees on a Saturday morning.
    df = df.ffill().dropna()
    df["dxy_ma20"] = df["dxy"].rolling(20).mean()
    df["y10_ma20"] = df["y10"].rolling(20).mean()
    df["dxy_chg5"] = df["dxy"].diff(5)
    df["y10_chg5"] = df["y10"].diff(5)
    return df


def macro_at(macro: pd.DataFrame, when: pd.Timestamp) -> Optional[pd.Series]:
    """Last macro row at or before `when` — no lookahead."""
    day = pd.Timestamp(when).normalize()
    prior = macro.loc[:day]
    if prior.empty:
        return None
    return prior.iloc[-1]


# ─────────────────────────────────────────────────────────────────────────────
# GATE DEFINITIONS  — each maps a macro row to True (would allow the trade)
# ─────────────────────────────────────────────────────────────────────────────

GATES: dict[str, callable] = {
    "LIVE GATE  dxy<99 AND y10<4.30": lambda m: m.dxy < LIVE_DXY_MAX and m.y10 < LIVE_Y10_MAX,
    "  dxy<99 only":                  lambda m: m.dxy < LIVE_DXY_MAX,
    "  y10<4.30 only":                lambda m: m.y10 < LIVE_Y10_MAX,
    "REL  dxy < its 20d mean":        lambda m: pd.notna(m.dxy_ma20) and m.dxy < m.dxy_ma20,
    "REL  y10 < its 20d mean":        lambda m: pd.notna(m.y10_ma20) and m.y10 < m.y10_ma20,
    "REL  BOTH < their 20d mean":     lambda m: (pd.notna(m.dxy_ma20) and pd.notna(m.y10_ma20)
                                                 and m.dxy < m.dxy_ma20 and m.y10 < m.y10_ma20),
    "ROC  dxy falling 5d":            lambda m: pd.notna(m.dxy_chg5) and m.dxy_chg5 < 0,
    "ROC  y10 falling 5d":            lambda m: pd.notna(m.y10_chg5) and m.y10_chg5 < 0,
    "ROC  BOTH falling 5d":           lambda m: (pd.notna(m.dxy_chg5) and pd.notna(m.y10_chg5)
                                                 and m.dxy_chg5 < 0 and m.y10_chg5 < 0),
}


# ─────────────────────────────────────────────────────────────────────────────
# STATS
# ─────────────────────────────────────────────────────────────────────────────

def summarise(trades: list[dict]) -> dict:
    rs = [t["r"] for t in trades if t["r"] is not None]
    if not rs:
        return {"n": 0, "avg_r": 0.0, "wr": 0.0, "median_r": 0.0, "total_r": 0.0}
    wins = [r for r in rs if r > 0]
    return {
        "n":        len(rs),
        "avg_r":    sum(rs) / len(rs),
        "median_r": statistics.median(rs),
        "wr":       100.0 * len(wins) / len(rs),
        "total_r":  sum(rs),
    }


def build_report(tagged: list[dict], macro: pd.DataFrame, elapsed: float,
                 coins: int, days: int, min_tier: str, spot_gate: bool = True) -> str:
    L, sep, dash = [], "=" * 86, "-" * 86
    add = L.append
    base = summarise(tagged)

    add(sep)
    add("  MACRO GATE BACKTESTER  —  does 'DXY<99 AND 10Y<4.30' earn its keep?")
    add(f"  {coins} coins · {days}d · trend>={min_tier.upper()} · {elapsed:.0f}s")
    add(sep)
    if not spot_gate:
        add("")
        add("  " + "!" * 80)
        add("  !!  SPOT GATE BYPASSED — these are TREND-TIER entries, NOT full confluence.")
        add("  !!  Reason: spot conviction qualified 1 time in 2,210 evaluations across 10")
        add("  !!  alts over 499d (max 54.8 vs a 50/60 threshold), so full confluence yields")
        add("  !!  n=0 and no macro bucketing is possible. This run answers ONLY: did the")
        add("  !!  DXY/10Y levels ever separate good trend entries from bad?")
        add("  !!  Do NOT read these R values as the live confluence edge.")
        add("  " + "!" * 80)
    add("")
    add(f"  ALL confluence trades (NO macro gate):  n={base['n']}  "
        f"avg {base['avg_r']:+.3f}R  median {base['median_r']:+.3f}R  "
        f"WR {base['wr']:.1f}%  total {base['total_r']:+.1f}R")
    if macro is not None and len(macro):
        lo, hi = macro.index[0].date(), macro.index[-1].date()
        add(f"  Macro series: {lo} → {hi}   "
            f"DXY {macro.dxy.min():.2f}–{macro.dxy.max():.2f}   "
            f"10Y {macro.y10.min():.3f}–{macro.y10.max():.3f}")
    add("")

    if base["n"] == 0:
        add("  No confluence trades in the window — nothing to bucket.")
        add(sep)
        return "\n".join(L)

    add(dash)
    add(f"  {'GATE':34} {'PASSED n':>9} {'avg R':>8} {'WR':>7} {'BLOCKED n':>10} {'avg R':>8}")
    add(dash)
    for name, fn in GATES.items():
        passed = [t for t in tagged if t["gates"].get(name) is True]
        blocked = [t for t in tagged if t["gates"].get(name) is False]
        p, b = summarise(passed), summarise(blocked)
        thin = "  <- thin" if 0 < p["n"] < MIN_N_TO_TRUST else ""
        add(f"  {name:34} {p['n']:>9} {p['avg_r']:>+8.3f} {p['wr']:>6.1f}% "
            f"{b['n']:>10} {b['avg_r']:>+8.3f}{thin}")
    add(dash)
    add("")

    # ── The verdict on the LIVE gate, stated plainly ────────────────────────
    live = "LIVE GATE  dxy<99 AND y10<4.30"
    lp = summarise([t for t in tagged if t["gates"].get(live) is True])
    lb = summarise([t for t in tagged if t["gates"].get(live) is False])
    add("  VERDICT — the live gate")
    add(dash)
    if lp["n"] == 0:
        add(f"  The live gate would have allowed ZERO of {base['n']} confluence trades in this")
        add("  window. It is not selecting good trades from bad — it is switching the system")
        add("  OFF. Whatever edge the confluence has, this gate captured none of it.")
        add(f"  What it blocked: n={lb['n']}  avg {lb['avg_r']:+.3f}R  WR {lb['wr']:.1f}%  "
            f"total {lb['total_r']:+.1f}R")
    else:
        edge = lp["avg_r"] - lb["avg_r"]
        add(f"  PASSED : n={lp['n']:<5} avg {lp['avg_r']:+.3f}R  WR {lp['wr']:.1f}%  "
            f"total {lp['total_r']:+.1f}R")
        add(f"  BLOCKED: n={lb['n']:<5} avg {lb['avg_r']:+.3f}R  WR {lb['wr']:.1f}%  "
            f"total {lb['total_r']:+.1f}R")
        add(f"  Gate edge: {edge:+.3f}R per trade.")
        if lp["n"] < MIN_N_TO_TRUST:
            add(f"  ** n={lp['n']} is below {MIN_N_TO_TRUST} — an anecdote, not a result. **")
        elif edge > 0.10:
            add("  The gate EARNS its keep: gated trades are materially better.")
        elif edge < -0.10:
            add("  The gate COSTS you: it is blocking better trades than it allows.")
        else:
            add("  The gate is NOISE: it neither helps nor hurts, and it costs you every")
            add("  trade it blocks while adding no expectancy.")
    add("")
    add("  Caveats: survivorship (today's names replayed backward) + no fees "
        "(~0.1-0.15R).")
    add("  A bucket under 30 trades is an anecdote. Low n IS the finding for a rare gate.")
    add(sep)
    return "\n".join(L)


# ─────────────────────────────────────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────────────────────────────────────

def _write(tagged, macro, elapsed, coins_done, days, min_tier, spot_gate, final=True) -> None:
    """Write report + json. Called every 5 coins so a killed run keeps its work."""
    report = build_report(tagged, macro, elapsed, coins_done, days, min_tier, spot_gate)
    tag = "" if final else "  [PARTIAL — run still in progress]"
    try:
        (_OUTPUT_DIR / "macro_gate_backtest_LATEST.txt").write_text(report + tag, encoding="utf-8")
        (_OUTPUT_DIR / "macro_gate_backtest_LATEST.json").write_text(
            json.dumps({"partial": not final, "coins_done": coins_done, "days": days,
                        "min_tier": min_tier, "spot_gate": spot_gate,
                        "trades": tagged}, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"  checkpoint write failed: {e}")


def run(coins: list[str], days: int, min_tier: str, spot_gate: bool = True) -> int:
    t0 = time.time()
    log.info("=" * 64)
    log.info(f"MACRO GATE BACKTEST — {len(coins)} coins, {days}d, min-tier={min_tier.upper()}")
    log.info("=" * 64)

    macro = build_macro_frame("2y")
    if macro is None or macro.empty:
        log.error("Could not build the macro series — aborting rather than "
                  "reporting buckets with no macro data.")
        return 1
    log.info(f"  Macro: {len(macro)} daily rows "
             f"{macro.index[0].date()} → {macro.index[-1].date()}")

    # DEEP history — get_ohlcv() caps at the venues' 1000-bar per-request limit
    # (~41 days of 1H), which is far too short to span periods when the gate was
    # open. get_ohlcv_deep pages backwards past that cap.
    bars_1h = days * 24
    bars_1d = max(days + 120, 400)
    btc1d = data.get_ohlcv_deep("BTC", "bybit", "1d", bars_1d)
    btc4h = data.get_ohlcv_deep("BTC", "bybit", "4h", days * 6)
    if btc1d is not None and not isinstance(btc1d.index, pd.DatetimeIndex):
        btc1d = None
    if btc4h is not None and not isinstance(btc4h.index, pd.DatetimeIndex):
        btc4h = None
    log.info(f"  BTC context: 1d={0 if btc1d is None else len(btc1d)} bars, "
             f"4h={0 if btc4h is None else len(btc4h)} bars")

    tagged: list[dict] = []
    failed = 0
    for i, base in enumerate(coins, 1):
        df = data.get_ohlcv_deep(base, "bybit", "1h", bars_1h)
        src = "bybit"
        if df is None or len(df) < 400:
            df = data.get_ohlcv_deep(base, "binance", "1h", bars_1h)
            src = "binance"
        if df is None or len(df) < 400 or not isinstance(df.index, pd.DatetimeIndex):
            failed += 1
            continue
        df1d = data.get_ohlcv_deep(base, src, "1d", bars_1d)
        if df1d is None or len(df1d) < MIN_1D_BARS:
            failed += 1
            continue
        if i == 1:
            log.info(f"  depth check {base}: 1h={len(df)} bars "
                     f"({(df.index[-1] - df.index[0]).days}d), 1d={len(df1d)} bars")

        for tr in backtest_coin(base, df, df1d, btc1d, btc4h, min_tier):
            # entered_at_idx is positional into df — map it back to a timestamp
            if tr.entered_at_idx is None or tr.entered_at_idx >= len(df):
                continue
            when = df.index[tr.entered_at_idx]
            m = macro_at(macro, when)
            if m is None:
                continue                     # entry predates the macro series
            tagged.append({
                "base": tr.base,
                "when": str(when),
                "r": tr.r_multiple,
                "outcome": tr.outcome,
                "dxy": float(m.dxy),
                "y10": float(m.y10),
                "gates": {name: bool(fn(m)) for name, fn in GATES.items()},
            })
        if i % 5 == 0 or i == len(coins):
            log.info(f"  ...{i}/{len(coins)}  tagged_trades={len(tagged)}")
            # Checkpoint. Three runs today were killed externally ~90 min in and
            # lost everything because results were only written at the end. A
            # partial table from 15 coins is a usable result; nothing is not.
            _write(tagged, macro, time.time() - t0, i, days, min_tier, spot_gate, final=False)

    elapsed = time.time() - t0
    report = build_report(tagged, macro, elapsed, len(coins), days, min_tier, spot_gate)
    log.info("\n" + report)
    if failed:
        log.info(f"  ({failed} coins skipped — no/short data)")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    (_OUTPUT_DIR / "macro_gate_backtest_LATEST.txt").write_text(report, encoding="utf-8")
    (_OUTPUT_DIR / f"macro_gate_backtest_{ts}.txt").write_text(report, encoding="utf-8")
    (_OUTPUT_DIR / "macro_gate_backtest_LATEST.json").write_text(
        json.dumps({"generated_at": ts, "coins": len(coins), "days": days,
                    "min_tier": min_tier, "trades": tagged}, indent=2), encoding="utf-8")
    log.info(f"  Saved → {_OUTPUT_DIR / 'macro_gate_backtest_LATEST.txt'}")
    return 0


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Does the DXY/10Y macro gate earn its keep?")
    ap.add_argument("--coins")
    ap.add_argument("--top", type=int)
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--min-tier", choices=["watch", "long", "strong"], default="long")
    ap.add_argument("--no-spot-gate", action="store_true",
                    help="bypass the spot-conviction requirement (trend-tier entries only)")
    ap.add_argument("--eval-every", type=int, default=12,
                    help="hours between confluence evaluations (higher = faster, coarser)")
    a = ap.parse_args()
    # backtest_coin reads this module-level constant at call time
    import backtest_confluence as BC
    BC.EVAL_EVERY = a.eval_every
    if a.no_spot_gate:
        BC._spot_qualifies = lambda conv, sigs, regime: True   # noqa: E731
        log.info("  SPOT GATE BYPASSED — trend-tier entries only")
    sys.exit(run(_resolve_coins(a.coins, a.top), a.days, a.min_tier, spot_gate=not a.no_spot_gate))
