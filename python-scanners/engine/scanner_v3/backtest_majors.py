"""
================================================================================
MAJORS TREND BACKTESTER  —  does a disciplined major-trend long have an edge?
================================================================================
The question Bruno raised (2026-07-21): "money is staying in the majors during
this BTC-dominance melt-up while our alt scanner sits at 0-qualifying. Are we
leaving money on the table by only trading alts? Should we trade BTC/ETH/SOL in
a confirmed BULL instead of waiting for alts?"

This tests it the same way we settled shorts (L61), ignition (L65) and regime
(L67): build the rule, replay it on history, read the avg-R. No freestyling a
BTC long at the top of day 3.

It pits THREE entry styles against each other, all in a BTC-confirmed BULL, all
on the SAME liquid-majors universe, all exited by the SAME shared engine:

  chase     BULL + close>EMA50 + RSI>=65        buy strength / momentum.
            ^ this is what the "MAJOR LEVERAGE" alert does (it fires at RSI 65-82).
  flip      BULL + SuperTrend flips green + RSI<70    buy the FRESH turn, not
            the extension.
  pullback  BULL + uptrend (close>EMA50, EMA50 rising) + RSI dipped<45 then
            crosses back >50            buy the DIP inside the uptrend.

If "chase" is breakeven/negative and "flip"/"pullback" are positive, that's the
empirical case that the leverage alert's timing is the problem, not majors per se
— and it tells us WHICH entry to build. If all three are ~0, majors-trend is not
an edge for us and the discipline (wait for alts) stands.

How it stays faithful:
  - Exit ENGINE : backtester.walk_trade_forward() — the SAME bar-by-bar staged
                  30/40/30 scale-out as the ignition / trend / short backtests,
                  so avg-R is directly comparable to +0.13R / +0.11R / -0.07R.
  - Trade PLAN  : backtester.build_long_plan() — the SAME ATR(1.5x) stop + 1.5/
                  3/5 RR TPs.
  - Regime      : BTC trailing-7d recomputed at each 4H eval bar (bull >+3%),
                  so entries only open when the majors regime is genuinely BULL —
                  exactly the condition the leverage alert claims.

Timeframe: 4H (999 bars ~= 166 days). Majors-trend is a swing, not a scalp; 4H
also buys ~4x the history of the 1H-capped alt backtests, so more BULL coverage.

Honest caveats (same family as the other backtests):
  - No fees/slippage modeled — subtract ~0.1-0.15R in your head.
  - SURVIVORSHIP BIAS: today's majors replayed backward. Milder than the alt
    backtests (majors rarely delist) but still an UPPER bound.
  - Indicators are causal (RSI/EMA/ADX/ATR/SuperTrend all read only past bars),
    so precompute-then-index-at-i introduces NO lookahead.

Run:
  python backtest_majors.py                       # all 3 modes, default majors
  python backtest_majors.py --mode pullback
  python backtest_majors.py --coins BTC,ETH,SOL --mode flip
================================================================================
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

import data
import indicators as I
from backtester import Trade, walk_trade_forward, build_long_plan, _atr_local


# ─────────────────────────────────────────────────────────────────────────────
# PATHS / LOGGING
# ─────────────────────────────────────────────────────────────────────────────
_THIS_DIR     = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent.parent.parent
_OUTPUT_DIR   = _PROJECT_ROOT / "outputs" / "backtests"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("backtest_majors")
if not log.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
# Liquid large-caps only — the "majors" the leverage alert would touch. NOT the
# alt-accumulation universe. Fixed list keeps the test stable run-to-run.
DEFAULT_MAJORS = [
    "BTC", "ETH", "SOL", "XRP", "BNB", "LINK", "AAVE", "SUI", "DOGE", "ADA",
    "AVAX", "DOT", "LTC", "XLM", "TRX", "NEAR", "ATOM", "UNI", "ARB", "OP",
]

TF            = "4h"
BARS          = 999                 # ~166 days on 4h
WARMUP        = 60                  # bars before first eval (EMA50/ADX warmup)
COOLDOWN_BARS = 12                  # 12 * 4h = 2 days between entries on a coin
TIME_STOP_BARS = 60                 # 60 * 4h = 10 days max hold (let trend run)
BARS_7D       = 42                  # 42 * 4h = 7 days, for the BTC regime calc
BULL_7D_PCT   = 3.0                 # BTC trailing-7d > +3% = confirmed BULL

MODES = ("chase", "flip", "pullback")


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR BUNDLE (precomputed once per coin — all causal, no lookahead)
# ─────────────────────────────────────────────────────────────────────────────

def _bundle(df: pd.DataFrame) -> dict:
    adx, plus_di, minus_di = I.compute_adx(df, 14)
    return {
        "rsi":   I.compute_rsi(df["close"], 14),
        "ema21": I.compute_ema(df["close"], 21),
        "ema50": I.compute_ema(df["close"], 50),
        "adx":   adx,
        "st":    I.compute_supertrend(df, 10, 3.0),   # bool Series: True = bullish
    }


def _entry_fires(mode: str, i: int, df: pd.DataFrame, b: dict) -> bool:
    """Does `mode` open a long at bar i? (Regime gate applied by the caller.)"""
    close = float(df["close"].iloc[i])
    rsi   = float(b["rsi"].iloc[i])
    ema50 = float(b["ema50"].iloc[i])
    if pd.isna(rsi) or pd.isna(ema50):
        return False

    if mode == "chase":
        # Buy strength — what the leverage alert does (RSI 65-82, price in uptrend).
        return close > ema50 and rsi >= 65.0

    if mode == "flip":
        # Fresh SuperTrend flip green, not already overbought.
        st_now  = bool(b["st"].iloc[i])
        st_prev = bool(b["st"].iloc[i - 1])
        return st_now and (not st_prev) and rsi < 70.0

    if mode == "pullback":
        # Established uptrend + a dip that just turned back up.
        ema50_rising = ema50 > float(b["ema50"].iloc[i - 10])
        if not (close > ema50 and ema50_rising):
            return False
        rsi_prev  = float(b["rsi"].iloc[i - 1])
        dipped    = float(b["rsi"].iloc[i - 6:i].min()) < 45.0     # dipped in last 6 bars
        cross_up  = rsi_prev <= 50.0 < rsi                          # crossing back up now
        return dipped and cross_up

    return False


# ─────────────────────────────────────────────────────────────────────────────
# PER-COIN BACKTEST (one mode)
# ─────────────────────────────────────────────────────────────────────────────

def backtest_coin(base: str, df: pd.DataFrame, btc_7d: pd.Series, mode: str) -> list[Trade]:
    trades: list[Trade] = []
    if df is None or len(df) < WARMUP + 20:
        return trades
    b = _bundle(df)
    last_entry_idx = -COOLDOWN_BARS - 1

    for i in range(WARMUP, len(df) - 1):          # -1: need >=1 forward bar to exit
        if i - last_entry_idx < COOLDOWN_BARS:
            continue
        reg7 = float(btc_7d.iloc[i]) if not pd.isna(btc_7d.iloc[i]) else 0.0
        if reg7 <= BULL_7D_PCT / 100.0:           # BULL-only, exactly like the alert claims
            continue
        if not _entry_fires(mode, i, df, b):
            continue

        entry = float(df["close"].iloc[i])
        atr   = _atr_local(df.iloc[:i + 1].tail(50), 14)
        plan  = build_long_plan(entry, atr)
        if plan is None:
            continue

        trade = Trade(
            base=base, entered_at_idx=i, entry_price=entry, stop_price=plan["stop"],
            tp1_price=plan["tp1"], tp2_price=plan["tp2"], tp3_price=plan["tp3"],
            conviction=round(reg7 * 100, 1), signal_count=1, direction="long",
            fired_signals=[mode, base],
        )
        trade = walk_trade_forward(trade, df, TIME_STOP_BARS)
        if trade.outcome is not None:
            trades.append(trade)
            last_entry_idx = i

    return trades


# ─────────────────────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────────────────────

def _stats(trades: list[Trade]) -> dict:
    rs = [t.r_multiple for t in trades if t.r_multiple is not None]
    if not rs:
        return {"n": 0, "wr": 0.0, "avg": 0.0, "total": 0.0, "worst": 0.0, "best": 0.0}
    return {
        "n": len(rs),
        "wr": sum(1 for r in rs if r > 0) / len(rs) * 100,
        "avg": sum(rs) / len(rs),
        "total": sum(rs),
        "worst": min(rs),
        "best": max(rs),
    }


def build_report(by_mode: dict[str, list[Trade]], coins: list[str], elapsed_s: float) -> str:
    lines, sep, dash = [], "=" * 88, "-" * 88
    lines.append(sep)
    lines.append(f"  MAJORS TREND BACKTESTER  —  {len(coins)} majors, {TF} {BARS}b (~166d), "
                 f"BULL-only  ({elapsed_s:.0f}s)")
    lines.append(sep)
    lines.append(f"  Universe: {', '.join(coins)}")
    lines.append("")
    lines.append("  HEAD-TO-HEAD  (same universe, same BULL gate, same exit engine)")
    lines.append(dash)
    lines.append(f"  {'Entry mode':<12} {'Trades':>7} {'Win %':>7} {'Avg R':>8} "
                 f"{'Total R':>9} {'Worst':>7} {'Best':>7}")
    lines.append(dash)
    for mode in MODES:
        s = _stats(by_mode.get(mode, []))
        if s["n"] == 0:
            lines.append(f"  {mode:<12} {0:>7}   (no trades)")
            continue
        lines.append(f"  {mode:<12} {s['n']:>7} {s['wr']:>6.1f}% {s['avg']:>+7.2f}R "
                     f"{s['total']:>+8.2f}R {s['worst']:>+6.2f} {s['best']:>+6.2f}")
    lines.append(dash)
    lines.append("")
    lines.append("  Benchmarks (same exit engine, from prior backtests):")
    lines.append("    ignition WATCH NOW  +0.13R   |   trend LONG/STRONG  +0.11R   |   short  -0.07R")
    lines.append("    → anything not comfortably above ~+0.10R after fees is NOT an edge.")
    lines.append("")

    # Per-coin breakdown for the best mode — exposes single-coin survivorship.
    best_mode = max(MODES, key=lambda m: _stats(by_mode.get(m, [])).get("avg", 0)
                    if _stats(by_mode.get(m, []))["n"] >= 10 else -9)
    trades = by_mode.get(best_mode, [])
    if trades:
        lines.append(dash)
        lines.append(f"  PER-COIN  (mode = {best_mode}, best avg-R with n>=10) — "
                     f"is it one coin carrying it?")
        lines.append(f"  {'Coin':<8} {'Trades':>7} {'Win %':>7} {'Avg R':>8} {'Total R':>9}")
        lines.append(dash)
        per: dict[str, list[float]] = defaultdict(list)
        for t in trades:
            if t.r_multiple is not None:
                per[t.base].append(t.r_multiple)
        for coin in sorted(per, key=lambda c: sum(per[c]), reverse=True):
            r = per[coin]
            wr = sum(1 for x in r if x > 0) / len(r) * 100
            lines.append(f"  {coin:<8} {len(r):>7} {wr:>6.1f}% "
                         f"{sum(r)/len(r):>+7.2f}R {sum(r):>+8.2f}R")
        lines.append(dash)

    lines.append("")
    lines.append("  R = blended 30/40/30 scale-out (shared exit engine) — comparable to the")
    lines.append("  ignition/trend/short backtests. ** NO fees/slippage (subtract ~0.1-0.15R) **")
    lines.append("  ** SURVIVORSHIP: today's majors replayed backward = UPPER bound **")
    lines.append(sep)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run(coins: list[str], modes: tuple[str, ...]) -> None:
    t0 = time.time()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log.info("=" * 64)
    log.info(f"MAJORS TREND BACKTESTER — {len(coins)} coins, {TF}, BULL-only, modes={list(modes)}")
    log.info("=" * 64)

    btc = data.get_btc(TF, BARS)
    if btc is None or not isinstance(btc.index, pd.DatetimeIndex):
        log.error("BTC reference unavailable — cannot compute regime. Abort.")
        return
    btc_close = btc["close"]
    btc_7d_global = btc_close / btc_close.shift(BARS_7D) - 1.0

    by_mode: dict[str, list[Trade]] = {m: [] for m in modes}
    failed = 0
    for idx, base in enumerate(coins, 1):
        df = data.get_ohlcv(base, "bybit", TF, BARS, use_cache=True)
        if df is None or len(df) < WARMUP + 20:
            df = data.get_ohlcv(base, "binance", TF, BARS, use_cache=True)
        if df is None or len(df) < WARMUP + 20 or not isinstance(df.index, pd.DatetimeIndex):
            failed += 1
            continue
        # Align BTC's trailing-7d onto this coin's 4H index (ffill across any gaps).
        btc_7d = btc_7d_global.reindex(df.index).ffill()
        for mode in modes:
            by_mode[mode].extend(backtest_coin(base, df, btc_7d, mode))
        if idx % 5 == 0 or idx == len(coins):
            counts = "  ".join(f"{m}={len(by_mode[m])}" for m in modes)
            log.info(f"  ...{idx}/{len(coins)}  {counts}")

    elapsed = time.time() - t0
    report = build_report(by_mode, coins, elapsed)
    log.info("\n" + report)
    if failed:
        log.info(f"  ({failed} coins skipped — no/short data)")

    (_OUTPUT_DIR / "majors_backtest_LATEST.txt").write_text(report, encoding="utf-8")
    (_OUTPUT_DIR / f"majors_backtest_{ts}.txt").write_text(report, encoding="utf-8")
    payload = {
        "generated_at": datetime.now().isoformat(), "elapsed_s": round(elapsed, 2),
        "coins": coins, "tf": TF, "bars": BARS, "bull_7d_pct": BULL_7D_PCT,
        "time_stop_bars": TIME_STOP_BARS, "modes": list(modes),
        "stats": {m: _stats(by_mode[m]) for m in modes},
        "trades": {m: [t.__dict__ for t in by_mode[m]] for m in modes},
    }
    (_OUTPUT_DIR / "majors_backtest_LATEST.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8")
    log.info(f"  Saved → majors_backtest_LATEST.txt + .json + majors_backtest_{ts}.txt")


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="Majors trend backtester — chase vs flip vs pullback in a BTC BULL")
    ap.add_argument("--coins", help="Comma-separated bases (default: liquid majors list)")
    ap.add_argument("--mode", choices=MODES, help="Run one mode only (default: all three head-to-head)")
    args = ap.parse_args()

    coins = [c.strip().upper() for c in args.coins.split(",")] if args.coins else DEFAULT_MAJORS
    modes = (args.mode,) if args.mode else MODES
    run(coins=coins, modes=modes)
