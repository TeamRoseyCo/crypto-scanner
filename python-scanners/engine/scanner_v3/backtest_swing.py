"""
================================================================================
SWING / MEAN-REVERSION BACKTESTER  —  "scalp the daily volatility, regime aside"
================================================================================
Tests Bruno's challenge (2026-07-27): regime is a slow filter; ETH/majors swing
1800<->1960 all day; why not buy the oversold dip and take profit into the
bounce — hours-to-a-day holds — and make money even when regime isn't bullish?

Faithful test of exactly that idea:
  - Entry  : RSI(14) oversold-REVERSAL — RSI was below `rsi_low`, ticks back up
             through it (the dip is turning). The classic "buy the dip" trigger.
  - Exit   : backtester.walk_trade_forward — SWING targets (TP RR 1/2/3, closer
             than the trend engine) + ATR stop + short time-stop = "TP levels
             hit and walk out." SAME exit engine as every other backtest.
  - Universe: majors + liquid alts (--top / --coins). ETH/BTC/SOL by default.

THE WHOLE POINT: it runs in ALL regimes and buckets the result BY regime at
entry, so we can see directly whether the daily-vol scalp has an edge when BTC
is NOT bullish — which is Bruno's specific claim.

Honest caveats (same family as the sibling engines):
  - No fees/slippage (subtract ~0.1-0.15R; mean-reversion trades a LOT, so fee
    drag hurts MORE here than on low-frequency strategies).
  - Fills at close-of-bar; one position per coin at a time (cooldown).
  - Survivorship: today's universe replayed backward = UPPER bound.

Run:
  python backtest_swing.py --coins ETH,BTC,SOL --days 400
  python backtest_swing.py --top 40 --days 400 --rsi-low 35 --tp-rr 1,2,3
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

import pandas as pd

import data
import trend_scanner as T
from backtester import Trade, walk_trade_forward, _atr_local, _resolve_coins

_THIS   = Path(__file__).resolve().parent
_ROOT   = _THIS.parent.parent.parent
_OUTPUT = _ROOT / "outputs" / "backtests"
_OUTPUT.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("backtest_swing")
if not log.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)

COOLDOWN_BARS = 12          # don't re-enter the same dip
_TF_DELTA     = pd.Timedelta("1D")


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    ru = up.ewm(alpha=1 / period, adjust=False).mean()
    rd = down.ewm(alpha=1 / period, adjust=False).mean()
    rs = ru / rd.replace(0, 1e-9)
    return 100 - 100 / (1 + rs)


def _regime_from_btc(btc_1d) -> str:
    if btc_1d is None or len(btc_1d) < 8:
        return "sideways"
    c = btc_1d["close"]
    btc_7d = float(c.iloc[-1]) / float(c.iloc[-8]) - 1.0
    if btc_7d > T.REGIME["bull_btc_7d_pct"] / 100.0:
        return "bull"
    if btc_7d < T.REGIME["neutral_btc_7d_pct"] / 100.0:
        return "bear"
    return "sideways"


def backtest_coin(base, df, btc1d, cfg) -> list[Trade]:
    trades: list[Trade] = []
    if df is None or len(df) < 120 or not isinstance(df.index, pd.DatetimeIndex):
        return trades
    rsi = _rsi(df["close"], cfg["rsi_period"])
    lo = cfg["rsi_low"]
    last_entry = -COOLDOWN_BARS - 1

    for i in range(50, len(df) - 1):
        if i - last_entry < COOLDOWN_BARS:
            continue
        r_prev, r_now = rsi.iloc[i - 1], rsi.iloc[i]
        if not (r_prev < lo <= r_now):          # oversold reversal cross-up
            continue
        sl = df.iloc[:i + 1]
        entry = float(sl["close"].iloc[-1])
        atr = _atr_local(sl.tail(50), 14)
        if atr <= 0:
            continue
        stop = entry - atr * cfg["stop_mult"]
        if (stop - entry) / entry < -cfg["stop_cap"]:
            stop = entry * (1 - cfg["stop_cap"])
        risk = entry - stop
        if risk <= 0:
            continue

        # regime at entry (closed-only 1D, no lookahead)
        regime = "sideways"
        if btc1d is not None:
            ts_end = sl.index[-1] + pd.Timedelta("1h")
            b = btc1d[btc1d.index + _TF_DELTA <= ts_end]
            regime = _regime_from_btc(b)

        rr = cfg["tp_rr"]
        tr = Trade(
            base=base, entered_at_idx=i, entry_price=entry, stop_price=stop,
            tp1_price=entry + risk * rr[0], tp2_price=entry + risk * rr[1],
            tp3_price=entry + risk * rr[2], conviction=float(round(r_now, 1)),
            signal_count=1, direction="long", fired_signals=[f"regime:{regime}"],
        )
        tr = walk_trade_forward(tr, df, cfg["hold_bars"])
        if tr.outcome is not None:
            trades.append(tr)
            last_entry = i
    return trades


def _stats(rs):
    n = len(rs)
    if not n:
        return (0, 0.0, 0.0, 0.0)
    wins = sum(1 for r in rs if r > 0)
    return (n, wins / n * 100, sum(rs) / n, sum(rs))


def build_report(trades, elapsed, cfg, coins_n, fee_note=0.12) -> str:
    L, sep, dash = [], "=" * 88, "-" * 88
    L.append(sep)
    L.append(f"  SWING / MEAN-REVERSION BACKTESTER  —  {len(trades)} trades  ({elapsed:.0f}s)")
    L.append(f"  Entry: RSI({cfg['rsi_period']}) reversal up through {cfg['rsi_low']}  |  "
             f"stop {cfg['stop_mult']}xATR (cap -{cfg['stop_cap']*100:.0f}%)  |  "
             f"TP RR {'/'.join(str(x) for x in cfg['tp_rr'])}  |  hold {cfg['hold_bars']}h")
    L.append(sep)
    if not trades:
        L.append("  No entries — no oversold reversals in-sample.")
        L.append(sep)
        return "\n".join(L)

    rs = [t.r_multiple for t in trades if t.r_multiple is not None]
    n, wr, avg, tot = _stats(rs)
    L.append("")
    L.append(f"  OVERALL:    trades={n}   win_rate={wr:.1f}%   avg_R={avg:+.2f}   total_R={tot:+.2f}")
    L.append(f"  AFTER FEES: avg_R ~{avg - fee_note:+.2f}  (−{fee_note}R/trade est; mean-reversion "
             f"trades a LOT so fee drag bites hardest here)")
    L.append(f"  RISK:       worst={min(rs):+.2f}R   best={max(rs):+.2f}R")

    # ── THE KEY TABLE: does it work when regime is NOT bullish? ──────────────
    L.append("")
    L.append(dash)
    L.append("  BY REGIME AT ENTRY  (Bruno's question: is there an edge when NOT bullish?)")
    L.append(f"  {'Regime':<10} {'Trades':>7} {'Win %':>7} {'Avg R':>8} {'Total R':>9} {'AfterFees':>10}")
    L.append(dash)
    byreg = defaultdict(list)
    for t in trades:
        if t.r_multiple is None:
            continue
        reg = next((s.split(":")[1] for s in t.fired_signals if s.startswith("regime:")), "?")
        byreg[reg].append(t.r_multiple)
    for reg in ("bull", "sideways", "bear"):
        b = byreg.get(reg, [])
        if not b:
            L.append(f"  {reg:<10} {0:>7}       —        —         —          —")
            continue
        rn, rwr, ravg, rtot = _stats(b)
        L.append(f"  {reg:<10} {rn:>7} {rwr:>6.1f}% {ravg:>+7.2f}R {rtot:>+8.2f}R {ravg-fee_note:>+9.2f}R")
    L.append(dash)

    out = defaultdict(int)
    for t in trades:
        out[t.outcome or "?"] += 1
    L.append("  Outcomes:   " + "   ".join(f"{k}={v}" for k, v in sorted(out.items())))
    L.append("")
    L.append("  Comparable: shorts -0.07R (n=915) · ignition +0.13R · trend +0.11R · coil -0.19R")
    L.append("             · coil+confluence +0.25R. All standalone signals ~breakeven-neg.")
    L.append("  ** No fees/slippage in avg_R (see AFTER FEES). Survivorship = UPPER bound. **")
    L.append(sep)
    return "\n".join(L)


def run(coins, days, cfg):
    t0 = time.time()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log.info("=" * 64)
    log.info(f"SWING BACKTEST  —  {len(coins)} coins, {days}d, RSI({cfg['rsi_period']}) "
             f"reversal<{cfg['rsi_low']}, TP {cfg['tp_rr']}, hold {cfg['hold_bars']}h")
    log.info("=" * 64)

    total_bars = days * 24 + 120
    btc1d = data.get_btc("1d", max(days, 400))
    if btc1d is not None and not isinstance(btc1d.index, pd.DatetimeIndex):
        btc1d = None

    all_trades, failed = [], 0
    for i, base in enumerate(coins, 1):
        df = data.get_ohlcv(base, "bybit", "1h", total_bars, use_cache=True)
        if df is None or len(df) < 120:
            df = data.get_ohlcv(base, "binance", "1h", total_bars, use_cache=True)
        if df is None or len(df) < 120 or not isinstance(df.index, pd.DatetimeIndex):
            failed += 1
            continue
        all_trades.extend(backtest_coin(base, df, btc1d, cfg))
        if i % 10 == 0 or i == len(coins):
            log.info(f"  ...{i}/{len(coins)}  trades={len(all_trades)}")

    elapsed = time.time() - t0
    report = build_report(all_trades, elapsed, cfg, len(coins) - failed)
    log.info("\n" + report)
    if failed:
        log.info(f"  ({failed} coins skipped — no/short data)")
    (_OUTPUT / f"swing_backtest_{ts}.txt").write_text(report, encoding="utf-8")
    (_OUTPUT / "swing_backtest_LATEST.txt").write_text(report, encoding="utf-8")
    (_OUTPUT / "swing_backtest_LATEST.json").write_text(json.dumps({
        "generated_at": datetime.now().isoformat(), "elapsed_s": round(elapsed, 2),
        "coins": coins, "days": days, "cfg": cfg, "n_trades": len(all_trades),
        "trades": [t.__dict__ for t in all_trades],
    }, indent=2, default=str), encoding="utf-8")
    log.info(f"  Saved → swing_backtest_LATEST.txt + .json + swing_backtest_{ts}.txt")


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Swing / mean-reversion backtester (buy the dip, TP into the bounce)")
    ap.add_argument("--coins")
    ap.add_argument("--top", type=int)
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--rsi-period", type=int, default=14)
    ap.add_argument("--rsi-low", type=float, default=35.0, help="oversold reversal threshold")
    ap.add_argument("--stop-mult", type=float, default=1.5, help="ATR stop multiple")
    ap.add_argument("--stop-cap", type=float, default=0.12, help="max stop distance (0.12=12%)")
    ap.add_argument("--tp-rr", default="1,2,3", help="swing TP risk-multiples, comma-sep")
    ap.add_argument("--hold-bars", type=int, default=48, help="max hold in 1h bars (48=2d)")
    args = ap.parse_args()
    cfg = {
        "rsi_period": args.rsi_period, "rsi_low": args.rsi_low,
        "stop_mult": args.stop_mult, "stop_cap": args.stop_cap,
        "tp_rr": [float(x) for x in args.tp_rr.split(",")], "hold_bars": args.hold_bars,
    }
    coins = _resolve_coins(args.coins, args.top) or ["ETH", "BTC", "SOL"]
    log.info(f"Coins ({len(coins)}): {', '.join(coins[:10])}" + ("..." if len(coins) > 10 else ""))
    run(coins, args.days, cfg)
