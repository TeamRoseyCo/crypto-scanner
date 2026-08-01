"""
================================================================================
SPOT THRESHOLD SWEEP  —  where SHOULD the conviction floor sit?
================================================================================
The live spot gate needs conviction >=60 (sideways full), >=50 with >=6 signals
(sideways exception) or >=45 (bull). Measured over 499 days x 10 alts, spot
conviction qualified ONCE in 2,210 evaluations (max 54.8, median 12.9). The
threshold sits above essentially the whole distribution, which is why the
confluence path produces n=0 and why the live watchlist never crosses 50.

That could mean the threshold drifted and should come down. It could also mean
conviction carries no outcome information at all, in which case lowering it just
buys more trades of the same average quality. This tells them apart.

Method — ONE pass, filtered many ways (not N separate backtests):
  - Walk every bar exactly like backtest_confluence.backtest_coin, but with NO
    spot gate. Trend tier >= --min-tier is still required (the confluence
    partner), so this is "what the trend layer offered, ranked by spot".
  - Record each trade's spot conviction and signal_count at entry, then simulate
    it forward with the SAME exit engine every other backtest uses.
  - Post-hoc, apply each candidate floor to the SAME trade set. One pass keeps
    the comparison clean: differences come from the filter, not from re-running.

The two questions it answers, in order:
  1. DOES CONVICTION PREDICT ANYTHING?  avg R bucketed by conviction decile. If
     that curve is flat, no threshold is better than any other and the whole
     knob is cosmetic — lowering it adds trades, not edge.
  2. IF IT DOES, WHERE IS THE FLOOR BEST?  n / avg R / WR / total R at each
     candidate floor.

Read the SHAPE, not the peak. The best-looking floor on a small sample is
usually the one that got lucky; a floor is only worth adopting if avg R rises
monotonically-ish as it tightens AND the surviving n is big enough to trust.

Honest caveats:
  - Cooldown interaction: entries are generated with no spot gate, so a floor
    applied post-hoc cannot "unblock" a trade the cooldown suppressed. Slightly
    understates how many trades a low floor would really produce.
  - Survivorship (today's names replayed backward), no fees (~0.1-0.15R).
  - funding=None in replay, so spot's funding_neg / crowded penalties are
    inactive — conviction here is marginally LOWER than a live scan would print.

Run (after the macro-gate run, they compete for CPU):
  python backtest_spot_threshold.py --top 40 --days 400 --min-tier long
================================================================================
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
from datetime import datetime

import pandas as pd

import data
import trend_scanner as T
import backtest_confluence as BC
from backtester import Trade, walk_trade_forward, PLAN, _resolve_coins

import spot_scanner as SP  # after BC, which puts engine/ on sys.path

log = logging.getLogger("backtest_spot_threshold")
if not log.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)

FLOORS = [0, 10, 15, 20, 25, 30, 35, 40, 45, 50, 60]
MIN_N_TO_TRUST = 30
ACCOUNT_SIZE = 100_000.0


def collect_coin(base, df1h, df1d_full, btc1d_full, btc4h_full, min_tier, eval_every) -> list[dict]:
    """Every trend-tier entry, tagged with the spot conviction it had at entry."""
    out: list[dict] = []
    if df1h is None or len(df1h) < 400 or df1d_full is None:
        return out
    min_rank = BC._TIER_RANK[min_tier]
    last_entry_idx = -BC.COOLDOWN_BARS - 1

    for i in range(200, len(df1h), eval_every):
        if i - last_entry_idx < BC.COOLDOWN_BARS:
            continue
        sl = df1h.iloc[:i + 1]
        ts_end = sl.index[-1] + pd.Timedelta("1h")
        df_1d = df1d_full[df1d_full.index + BC._TF_DELTA <= ts_end]
        if len(df_1d) < BC.MIN_1D_BARS:
            continue
        btc_1d = None
        if btc1d_full is not None:
            btc_1d = btc1d_full[btc1d_full.index + BC._TF_DELTA <= ts_end]
            if len(btc_1d) < 8:
                btc_1d = None
        regime = BC._regime_from_btc(btc_1d)
        if regime == "bear":
            continue

        df_4h = BC._resample_ohlcv(sl, "4h")
        if len(df_4h) < 42:
            continue
        btc_4h = btc4h_full[btc4h_full.index <= sl.index[-1]] if btc4h_full is not None else None
        price = float(sl["close"].iloc[-1])
        spot = SP.detect_signals(df_4h, btc_4h["close"] if btc_4h is not None else None,
                                 price, binance_source=True, funding_rate=None)
        if spot is None:
            continue

        cbt = {"1H": sl}
        for lbl, rule in BC._RESAMPLE:
            cbt[lbl] = BC._resample_ohlcv(sl, rule)
        cbt["1D"] = df_1d
        p24 = ((float(df_1d["close"].iloc[-1]) / float(df_1d["close"].iloc[-2]) - 1) * 100
               if len(df_1d) >= 2 else 0.0)
        res = T.score_coin(base=base, symbol=f"{base}USDT", candles_by_tf=cbt, btc_1d=btc_1d,
                           funding_rate=None, regime=regime, coin_meta={"price_24h_pct": p24})
        if res is None or BC._TIER_RANK.get(res.tier, 0) < min_rank:
            continue

        atr_1d = T._safe_atr(df_1d, 14)
        plan = T.build_trade_plan(price, atr_1d, regime, ACCOUNT_SIZE, macro_mult=1.0)
        if plan is None:
            continue
        tps = plan.take_profits
        tr = Trade(base=base, entered_at_idx=i, entry_price=price, stop_price=plan.stop,
                   tp1_price=tps[0]["price"], tp2_price=tps[1]["price"], tp3_price=tps[2]["price"],
                   conviction=res.total_score, signal_count=int(spot["signal_count"]),
                   direction="long")
        tr = walk_trade_forward(tr, df1h, PLAN["time_stop_bars"])
        if tr.outcome is None:
            continue
        out.append({
            "base": base,
            "when": str(df1h.index[i]),
            "r": tr.r_multiple,
            "outcome": tr.outcome,
            "spot_conv": float(spot["conviction"]),
            "spot_sigs": int(spot["signal_count"]),
            "trend_tier": res.tier,
            "regime": regime,
        })
        last_entry_idx = i
    return out


def _stat(rows: list[dict]) -> dict:
    rs = [r["r"] for r in rows if r["r"] is not None]
    if not rs:
        return {"n": 0, "avg": 0.0, "wr": 0.0, "total": 0.0}
    return {"n": len(rs), "avg": sum(rs) / len(rs),
            "wr": 100.0 * len([x for x in rs if x > 0]) / len(rs), "total": sum(rs)}


def build_report(rows: list[dict], elapsed: float, coins: int, days: int, min_tier: str) -> str:
    L, sep, dash = [], "=" * 84, "-" * 84
    add = L.append
    add(sep)
    add("  SPOT THRESHOLD SWEEP  —  where should the conviction floor sit?")
    add(f"  {coins} coins · {days}d · trend>={min_tier.upper()} · no spot gate · {elapsed:.0f}s")
    add(sep)
    add("")
    if not rows:
        add("  No trend-tier entries at all — nothing to sweep. The blocker is upstream")
        add("  of spot, in the trend tier or the regime filter.")
        add(sep)
        return "\n".join(L)

    allr = _stat(rows)
    convs = pd.Series([r["spot_conv"] for r in rows])
    add(f"  Population: n={allr['n']}  avg {allr['avg']:+.3f}R  WR {allr['wr']:.1f}%  "
        f"total {allr['total']:+.1f}R")
    add(f"  Spot conviction at entry: min {convs.min():.1f}  median {convs.median():.1f}  "
        f"p90 {convs.quantile(.9):.1f}  max {convs.max():.1f}")
    add("")

    # ── Q1: does conviction predict anything? ───────────────────────────────
    add("  Q1 — DOES CONVICTION PREDICT OUTCOME?  (equal-count buckets)")
    add(dash)
    add(f"  {'conviction range':22} {'n':>6} {'avg R':>9} {'WR':>8}")
    add(dash)
    try:
        qs = pd.qcut(convs, 5, duplicates="drop")
        for interval in sorted(set(qs), key=lambda x: x.left):
            sel = [r for r, q in zip(rows, qs) if q == interval]
            s = _stat(sel)
            add(f"  {str(interval):22} {s['n']:>6} {s['avg']:>+9.3f} {s['wr']:>7.1f}%")
    except Exception:
        add("  (conviction too concentrated to bucket)")
    add(dash)
    add("  Flat avg R down this column = conviction carries NO outcome information,")
    add("  and no floor beats any other. Rising = the signal is real.")
    add("")

    # ── Q2: the floor sweep ─────────────────────────────────────────────────
    add("  Q2 — FLOOR SWEEP  (same trade set, filtered)")
    add(dash)
    add(f"  {'floor':>7} {'n':>6} {'avg R':>9} {'WR':>8} {'total R':>10}   {'note':<20}")
    add(dash)
    for f in FLOORS:
        sel = [r for r in rows if r["spot_conv"] >= f]
        s = _stat(sel)
        note = ""
        if s["n"] == 0:
            note = "no trades survive"
        elif s["n"] < MIN_N_TO_TRUST:
            note = "thin - anecdote"
        live = "  <- LIVE (sideways)" if f == 50 else ("  <- LIVE (bull)" if f == 45 else "")
        add(f"  {f:>7} {s['n']:>6} {s['avg']:>+9.3f} {s['wr']:>7.1f}% {s['total']:>+10.1f}   "
            f"{note:<20}{live}")
    add(dash)
    add("")
    add("  HOW TO READ: adopt a floor only if avg R rises as it tightens AND the")
    add("  surviving n clears 30. A high avg R on 4 trades is luck, not a threshold.")
    add("  Caveats: survivorship, no fees (~0.1-0.15R), cooldown means a low floor")
    add("  would really produce somewhat MORE trades than shown.")
    add(sep)
    return "\n".join(L)


def run(coins: list[str], days: int, min_tier: str, eval_every: int) -> int:
    t0 = time.time()
    log.info(f"SPOT THRESHOLD SWEEP — {len(coins)} coins, {days}d, min-tier={min_tier.upper()}")
    bars_1h, bars_1d = days * 24, max(days + 120, 400)
    btc1d = data.get_ohlcv_deep("BTC", "bybit", "1d", bars_1d)
    btc4h = data.get_ohlcv_deep("BTC", "bybit", "4h", days * 6)

    rows: list[dict] = []
    failed = 0
    for i, base in enumerate(coins, 1):
        df = data.get_ohlcv_deep(base, "bybit", "1h", bars_1h)
        src = "bybit"
        if df is None or len(df) < 400:
            df = data.get_ohlcv_deep(base, "binance", "1h", bars_1h)
            src = "binance"
        if df is None or len(df) < 400:
            failed += 1
            continue
        df1d = data.get_ohlcv_deep(base, src, "1d", bars_1d)
        if df1d is None or len(df1d) < BC.MIN_1D_BARS:
            failed += 1
            continue
        rows.extend(collect_coin(base, df, df1d, btc1d, btc4h, min_tier, eval_every))
        if i % 5 == 0 or i == len(coins):
            log.info(f"  ...{i}/{len(coins)}  entries={len(rows)}")
            # Checkpoint — see backtest_macro_gate._write for why.
            try:
                partial = build_report(rows, time.time() - t0, i, days, min_tier)
                (BC._OUTPUT_DIR / "spot_threshold_sweep_LATEST.txt").write_text(
                    partial + "  [PARTIAL — run still in progress]", encoding="utf-8")
                (BC._OUTPUT_DIR / "spot_threshold_sweep_LATEST.json").write_text(
                    json.dumps({"partial": True, "coins_done": i, "entries": rows},
                               indent=2), encoding="utf-8")
            except Exception as e:
                log.warning(f"  checkpoint write failed: {e}")

    elapsed = time.time() - t0
    report = build_report(rows, elapsed, len(coins), days, min_tier)
    log.info("\n" + report)
    if failed:
        log.info(f"  ({failed} coins skipped — no/short data)")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    (BC._OUTPUT_DIR / "spot_threshold_sweep_LATEST.txt").write_text(report, encoding="utf-8")
    (BC._OUTPUT_DIR / f"spot_threshold_sweep_{ts}.txt").write_text(report, encoding="utf-8")
    (BC._OUTPUT_DIR / "spot_threshold_sweep_LATEST.json").write_text(
        json.dumps({"generated_at": ts, "min_tier": min_tier, "days": days,
                    "entries": rows}, indent=2), encoding="utf-8")
    log.info(f"  Saved → {BC._OUTPUT_DIR / 'spot_threshold_sweep_LATEST.txt'}")
    return 0


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Where should the spot conviction floor sit?")
    ap.add_argument("--coins")
    ap.add_argument("--top", type=int)
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--min-tier", choices=["watch", "long", "strong"], default="long")
    ap.add_argument("--eval-every", type=int, default=12)
    a = ap.parse_args()
    sys.exit(run(_resolve_coins(a.coins, a.top), a.days, a.min_tier, a.eval_every))
