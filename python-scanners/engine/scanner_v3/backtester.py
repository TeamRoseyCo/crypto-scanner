"""
================================================================================
BACKTESTER  v1.0  —  replay ignition signals on historical data
================================================================================
Walks bar-by-bar through historical OHLCV for a universe of coins. At each
bar, runs the ignition signals and — if WATCH NOW would have fired — opens
a paper position with the same ATR-based trade plan as signal_tracker.
Walks the position forward bar-by-bar until stop/TP/time exit. Logs every
trade and emits per-signal performance stats.

Honesty about scope:
  - This is a SPINE, not a Quantopian-grade engine.
  - It assumes you can fill at close-of-bar. Realistic-ish for 1h timeframes,
    but it does NOT model slippage or fees. Add ~0.1-0.2R drag in your head.
  - It uses the same signal weights and thresholds as your live ignition
    scanner — you can edit BACKTEST_OVERRIDES below to A/B-test variants.
  - One position per coin at a time. No portfolio-level risk constraints.

The point: turn "I think whale_candle is worth 2.5 weight" into "whale_candle
won 58% of trades at +0.9 avg R over 412 entries — try bumping it to 3.0".

Run:
  python backtester.py --coins BTC,ETH,SOL --days 90
  python backtester.py --top 50 --days 60          # top 50 by current volume
  python backtester.py --top 100 --days 180 --tf 1h
  python backtester.py --top 50 --days 90 --signal-filter whale_candle
================================================================================
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

import data
import signals as S
import short_scanner as SS   # Phase 2: reuse the live bearish signal logic (no drift)


# ─────────────────────────────────────────────────────────────────────────────
# PATHS / LOGGING
# ─────────────────────────────────────────────────────────────────────────────
_THIS_DIR     = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent.parent.parent
_OUTPUT_DIR   = _PROJECT_ROOT / "outputs" / "backtests"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("backtest")
if not log.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — mirrors ignition_scanner.py; override here to A/B test
# ─────────────────────────────────────────────────────────────────────────────

# These are the SAME defaults as ignition_scanner.SIGNAL_WEIGHTS — edit to test variants.
BACKTEST_WEIGHTS: dict[str, float] = {
    "whale_candle":      2.5,
    "obv_divergence":    2.5,
    "rsi_divergence":    2.5,
    "bb_squeeze":        2.0,
    "obv_stealth_accum": 2.0,
    "cmf_positive":      1.5,
    "vol_expansion":     1.5,
    "higher_lows":       1.5,
    "rsi_reset":         1.5,
    "rsi_in_zone":       1.0,
    "vol_in_window":     1.0,
    "btc_decoupling":    1.0,
    "price_range_break": 1.0,
}
TOTAL_WEIGHT = sum(BACKTEST_WEIGHTS.values())

BACKTEST_TIERS = {
    "watch_now_conviction": 40.0,
    "watch_now_signals":     5,
}

SIGNAL_PARAMS: dict[str, dict] = {
    "bb_squeeze":        {"width_lookback": 120, "width_pct": 20.0},
    "rsi_divergence":    {"rsi_period": 7, "lookback": 60},
    "rsi_reset":         {"rsi_period": 7, "lookback": 24, "low_thresh": 42.0},
    "rsi_in_zone":       {"rsi_period": 7, "low": 25.0, "high": 62.0},
    "vol_expansion":     {"recent": 6, "base_start": 7, "base_end": 42, "mult": 1.5},
    "vol_in_window":     {"recent": 6, "base_back": 30, "low_mult": 1.2, "high_mult": 4.5},
    "whale_candle":      {"lookback": 6, "atr_mult": 1.8, "close_upper_pct": 0.30},
    "obv_stealth_accum": {"obv_lookback": 12, "min_obv_pct": 0.015, "max_price_move": 0.03},
    "obv_divergence":    {"lookback": 40, "pivot_left": 3, "pivot_right": 3},
    "cmf_positive":      {"period": 20, "threshold": 0.05},
    "higher_lows":       {"window": 30, "pivot_left": 3, "pivot_right": 3},
    "btc_decoupling":    {"window": 6,  "min_decoupling": 0.015},
    "price_range_break": {"lookback": 120},
}

PLAN = {
    "atr_period":      14,
    "atr_stop_mult":   1.5,
    "stop_min_pct":   -0.15,
    "stop_max_pct":   -0.05,
    "tp_rr":          [1.5, 3.0, 5.0],
    # Staged scale-out (must sum to 1.0) — used to compute a realistic BLENDED R
    # that matches signal_tracker.py, instead of crediting the full TP3 distance
    # to every winner. Keeping these identical to the tracker is what makes the
    # backtest's weight suggestions trustworthy.
    "tp_exit_pct":    [0.30, 0.40, 0.30],
    "move_stop_to_breakeven_after_tp1": False,
    "time_stop_bars": 24 * 7,  # 1 week on 1h
}

# Anti-overlap: once you enter a coin, wait this many bars before opening another
COOLDOWN_BARS = 24

# ─────────────────────────────────────────────────────────────────────────────
# SHORT-SIDE COST MODELING (Phase 1) — shorts are NOT free to hold or exit.
# These are MODELED ASSUMPTIONS (the OHLCV cache has no historical funding);
# Phase 3 paper-trading measures the real numbers. Applied to SHORT trades only,
# so the existing long backtest stays byte-for-byte unchanged.
# ─────────────────────────────────────────────────────────────────────────────
SHORT_COSTS = {
    "funding_per_8h": 0.0001,   # 0.01%/8h carry paid by a short in a bid market
    "slippage_fee_r": 0.10,     # ~0.1R round-trip drag (taker fees + slippage)
}


# ─────────────────────────────────────────────────────────────────────────────
# TRADE TYPES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Trade:
    base:           str
    entered_at_idx: int
    entry_price:    float
    stop_price:     float
    tp1_price:      float
    tp2_price:      float
    tp3_price:      float
    conviction:     float
    signal_count:   int
    direction:      str = "long"          # "long" | "short"
    fired_signals:  list[str] = field(default_factory=list)
    # Filled at exit:
    exited_at_idx:  Optional[int]   = None
    exit_price:     Optional[float] = None
    outcome:        Optional[str]   = None
    r_multiple:     Optional[float] = None
    bars_held:      Optional[int]   = None


# ─────────────────────────────────────────────────────────────────────────────
# SCORING (mirrors ignition_scanner.score_coin)
# ─────────────────────────────────────────────────────────────────────────────

def _atr_local(window: pd.DataFrame, period: int = 14) -> float:
    if window is None or len(window) < period + 1:
        return 0.0
    h = window["high"]; l = window["low"]; c = window["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l).abs(), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    s = tr.rolling(period).mean().dropna()
    return float(s.iloc[-1]) if len(s) else 0.0


def evaluate_signals_at_bar(
    df_so_far:   pd.DataFrame,
    btc_so_far:  Optional[pd.Series],
) -> tuple[float, int, list[str]]:
    """Returns (conviction, signal_count, fired_signals) at the current bar."""
    if df_so_far is None or len(df_so_far) < 50:
        return 0.0, 0, []

    fired: list[str] = []
    earned: float = 0.0

    def rec(name: str, res: S.SignalResult) -> None:
        nonlocal earned
        if res.fired:
            fired.append(name)
            w = BACKTEST_WEIGHTS.get(name, 0.0)
            earned += w * (0.5 + 0.5 * res.strength)

    rec("bb_squeeze",        S.sig_bb_squeeze       (df_so_far, **SIGNAL_PARAMS["bb_squeeze"]))
    rec("vol_in_window",     S.sig_vol_in_window    (df_so_far, **SIGNAL_PARAMS["vol_in_window"]))
    rec("vol_expansion",     S.sig_vol_expansion    (df_so_far, **SIGNAL_PARAMS["vol_expansion"]))
    rec("whale_candle",      S.sig_whale_candle     (df_so_far, **SIGNAL_PARAMS["whale_candle"]))
    rec("obv_stealth_accum", S.sig_obv_stealth_accum(df_so_far, **SIGNAL_PARAMS["obv_stealth_accum"]))
    rec("obv_divergence",    S.sig_obv_divergence   (df_so_far, **SIGNAL_PARAMS["obv_divergence"]))
    rec("rsi_divergence",    S.sig_rsi_divergence   (df_so_far, **SIGNAL_PARAMS["rsi_divergence"]))
    rec("rsi_reset",         S.sig_rsi_reset        (df_so_far, **SIGNAL_PARAMS["rsi_reset"]))
    rec("rsi_in_zone",       S.sig_rsi_in_zone      (df_so_far, **SIGNAL_PARAMS["rsi_in_zone"]))
    rec("cmf_positive",      S.sig_cmf_positive     (df_so_far, **SIGNAL_PARAMS["cmf_positive"]))
    rec("higher_lows",       S.sig_higher_lows      (df_so_far, **SIGNAL_PARAMS["higher_lows"]))
    rec("price_range_break", S.sig_price_range_break(df_so_far, **SIGNAL_PARAMS["price_range_break"]))
    if btc_so_far is not None and len(btc_so_far) >= 8:
        rec("btc_decoupling", S.sig_btc_decoupling(df_so_far["close"], btc_so_far,
                                                   **SIGNAL_PARAMS["btc_decoupling"]))

    conviction = (earned / TOTAL_WEIGHT) * 100 if TOTAL_WEIGHT > 0 else 0
    return conviction, len(fired), fired


def evaluate_short_signals_at_bar(
    base:        str,
    df_so_far:   pd.DataFrame,
    btc_so_far:  Optional[pd.Series],
) -> tuple[float, int, list[str]]:
    """Bearish conviction at the current bar — reuses short_scanner.score_coin so
    the backtest uses the EXACT live short signal logic (no drift).

    funding_rate is None in replay (the OHLCV cache has no historical funding), so
    the funding exclusion + funding_extreme_long bonus are inactive here — the
    backtest is therefore slightly OPTIMISTIC on entries (it may enter shorts the
    live scanner would have funding-excluded as squeeze-risk). The gap-through-stop
    model still charges the squeeze LOSSES. Phase 3 paper measures real funding.
    """
    if df_so_far is None or len(df_so_far) < 50:
        return 0.0, 0, []
    res = SS.score_coin(base, df_so_far, btc_closes=btc_so_far, funding_rate=None)
    if res is None:
        return 0.0, 0, []
    return res.conviction, res.signal_count, list(res.fired_signals)


# ─────────────────────────────────────────────────────────────────────────────
# TRADE LIFECYCLE
# ─────────────────────────────────────────────────────────────────────────────

def build_long_plan(entry: float, atr: float) -> Optional[dict]:
    if entry <= 0 or atr <= 0:
        return None
    atr_dist = atr * PLAN["atr_stop_mult"]
    stop = entry - atr_dist
    stop_pct = (stop - entry) / entry
    if stop_pct < PLAN["stop_min_pct"]:
        stop = entry * (1 + PLAN["stop_min_pct"])
    elif stop_pct > PLAN["stop_max_pct"]:
        stop = entry * (1 + PLAN["stop_max_pct"])
    risk = entry - stop
    if risk <= 0:
        return None
    return {
        "stop": stop,
        "tp1": entry + risk * PLAN["tp_rr"][0],
        "tp2": entry + risk * PLAN["tp_rr"][1],
        "tp3": entry + risk * PLAN["tp_rr"][2],
    }


def build_short_plan(entry: float, atr: float) -> Optional[dict]:
    """Mirror of build_long_plan: stop ABOVE entry, TPs BELOW entry."""
    if entry <= 0 or atr <= 0:
        return None
    atr_dist = atr * PLAN["atr_stop_mult"]
    stop = entry + atr_dist
    stop_pct = (stop - entry) / entry           # positive distance for a short
    max_stop = -PLAN["stop_min_pct"]            # 0.15
    min_stop = -PLAN["stop_max_pct"]            # 0.05
    if stop_pct > max_stop:
        stop = entry * (1 + max_stop)
    elif stop_pct < min_stop:
        stop = entry * (1 + min_stop)
    risk = stop - entry
    if risk <= 0:
        return None
    return {
        "stop": stop,
        "tp1": entry - risk * PLAN["tp_rr"][0],
        "tp2": entry - risk * PLAN["tp_rr"][1],
        "tp3": entry - risk * PLAN["tp_rr"][2],
    }


def walk_trade_forward(
    trade:    Trade,
    df:       pd.DataFrame,
    max_bars: int,
) -> Trade:
    """From entered_at_idx+1 forward, simulate the STAGED scale-out plan and
    record the realised BLENDED R. Direction-aware:

      LONG  : stop below entry (hit on low<=stop), TPs above (hit on high>=tp).
              Fills exactly at the stop level — long path is unchanged from v1.0.
      SHORT : stop above entry (hit on high>=stop), TPs below (hit on low<=tp).
              Models squeeze GAP-THROUGH: if a bar OPENS beyond the stop, the
              fill is the (worse) open, so a violent pump costs more than 1R.
              Also charges funding carry + slippage/fee drag (SHORT_COSTS).

    Conservative within-bar assumption (both directions): if a bar's range
    touches the stop, the stop is assumed to fill before any TP in that bar.
    """
    is_short = trade.direction == "short"
    start = trade.entered_at_idx + 1
    end   = min(start + max_bars, len(df))
    entry = trade.entry_price
    stop  = trade.stop_price
    tps   = [trade.tp1_price, trade.tp2_price, trade.tp3_price]
    risk  = (stop - entry) if is_short else (entry - stop)
    if risk <= 0:
        return trade

    fracs      = list(PLAN["tp_exit_pct"])
    tps_hit    = [False, False, False]
    remaining  = 1.0
    realized_r = 0.0
    stop_level = stop
    highest_tp = 0
    last_i     = None
    terminal   = None   # (exit_idx, exit_price, outcome_label)

    for i in range(start, end):
        last_i = i
        bar = df.iloc[i]
        high = float(bar["high"]); low = float(bar["low"]); open_ = float(bar["open"])

        # ----- adverse move → stop (checked first: conservative) -----
        stop_touched = (high >= stop_level) if is_short else (low <= stop_level)
        if stop_touched:
            if is_short:
                fill = max(stop_level, open_)       # gap-through on a violent pump
                realized_r += remaining * (entry - fill) / risk
            else:
                fill = stop_level                    # unchanged long behaviour
                realized_r += remaining * (fill - entry) / risk
            remaining = 0.0
            terminal = (i, fill, "tp%d" % highest_tp if highest_tp else "stop")
            break

        # ----- favourable move → TPs -----
        for k, tp in enumerate(tps):
            if tps_hit[k]:
                continue
            tp_touched = (low <= tp) if is_short else (high >= tp)
            if not tp_touched:
                continue
            realized_r += fracs[k] * ((entry - tp) if is_short else (tp - entry)) / risk
            remaining   = max(0.0, remaining - fracs[k])
            tps_hit[k]  = True
            highest_tp  = k + 1
            if k == 0 and PLAN["move_stop_to_breakeven_after_tp1"]:
                stop_level = entry

        if all(tps_hit) or remaining <= 1e-9:
            terminal = (i, tps[-1], "tp3")
            break

    if terminal is None:
        # Ran out of bars / hit time stop with some position still open:
        # mark the remainder out at the last close.
        if last_i is None:
            return trade
        final_price = float(df["close"].iloc[last_i])
        realized_r += remaining * ((entry - final_price) if is_short else (final_price - entry)) / risk
        terminal = (last_i, final_price, "tp%d" % highest_tp if highest_tp else "time")

    exit_i, exit_price, outcome = terminal

    # ----- short carry + transaction drag (Phase 1; shorts only) -----
    if is_short:
        bars_held = exit_i - trade.entered_at_idx
        funding_frac = SHORT_COSTS["funding_per_8h"] * (bars_held / 8.0)   # 1h bars
        realized_r -= funding_frac * entry / risk
        realized_r -= SHORT_COSTS["slippage_fee_r"]

    trade.exited_at_idx = exit_i
    trade.exit_price    = exit_price
    trade.outcome       = outcome
    trade.r_multiple    = round(realized_r, 3)
    trade.bars_held     = exit_i - trade.entered_at_idx
    return trade


# ─────────────────────────────────────────────────────────────────────────────
# PER-COIN BACKTEST
# ─────────────────────────────────────────────────────────────────────────────

def backtest_coin(
    base:        str,
    df:          pd.DataFrame,
    btc_closes:  Optional[pd.Series],
    direction:   str = "long",
    tier:        str = "watch_now",
) -> list[Trade]:
    """Walk through `df` bar by bar, simulating entries.

    NOTE (Phase 1): the SIGNAL side is still long-only ignition
    (`evaluate_signals_at_bar`). `direction` selects the trade PLAN + sizing so
    the short trade lifecycle is wired and unit-tested (`--selftest`), but a real
    short replay needs Phase 2 to swap in short_scanner's bearish signals here.
    """
    trades: list[Trade] = []
    if df is None or len(df) < 200:
        return trades

    last_entry_idx = -COOLDOWN_BARS - 1   # so first bar can enter

    # Need at least 200 bars of context before we can evaluate signals.
    # Walk from i=200 forward, evaluating signals on df[:i+1]
    for i in range(200, len(df)):
        # Cooldown check
        if i - last_entry_idx < COOLDOWN_BARS:
            continue

        df_so_far  = df.iloc[:i + 1]
        btc_so_far = btc_closes.iloc[:i + 1] if btc_closes is not None else None

        if direction == "short":
            conviction, sig_count, fired = evaluate_short_signals_at_bar(base, df_so_far, btc_so_far)
            if tier == "on_radar":
                tier_conv = SS.TIERS["on_radar_conviction"]
                tier_sigs = SS.TIERS["on_radar_signals"]
            else:
                tier_conv = SS.TIERS["watch_now_conviction"]
                tier_sigs = SS.TIERS["watch_now_signals"]
        else:
            conviction, sig_count, fired = evaluate_signals_at_bar(df_so_far, btc_so_far)
            tier_conv = BACKTEST_TIERS["watch_now_conviction"]
            tier_sigs = BACKTEST_TIERS["watch_now_signals"]
        if conviction < tier_conv or sig_count < tier_sigs:
            continue

        # Entry at close of bar i
        entry = float(df["close"].iloc[i])
        atr   = _atr_local(df_so_far.tail(50), PLAN["atr_period"])
        plan  = build_short_plan(entry, atr) if direction == "short" else build_long_plan(entry, atr)
        if plan is None:
            continue

        trade = Trade(
            base           = base,
            entered_at_idx = i,
            entry_price    = entry,
            stop_price     = plan["stop"],
            tp1_price      = plan["tp1"],
            tp2_price      = plan["tp2"],
            tp3_price      = plan["tp3"],
            conviction     = round(conviction, 1),
            signal_count   = sig_count,
            direction      = direction,
            fired_signals  = list(fired),
        )
        trade = walk_trade_forward(trade, df, PLAN["time_stop_bars"])
        if trade.outcome is not None:
            trades.append(trade)
            last_entry_idx = i

    return trades


# ─────────────────────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────────────────────

def build_report(all_trades: list[Trade], elapsed_s: float, direction: str = "long") -> str:
    lines = []
    sep = "=" * 88
    dash = "-" * 88

    lines.append(sep)
    lines.append(f"  BACKTESTER v1.1  —  {len(all_trades)} {direction.upper()} trades  ({elapsed_s:.0f}s)")
    lines.append(sep)

    if not all_trades:
        lines.append("  No trades generated. Check date range / coin selection.")
        lines.append(sep)
        return "\n".join(lines)

    # Overall
    rs = [t.r_multiple for t in all_trades if t.r_multiple is not None]
    wins = sum(1 for r in rs if r > 0)
    avg_r = sum(rs) / len(rs) if rs else 0
    win_rate = wins / len(rs) * 100 if rs else 0
    total_r = sum(rs)

    lines.append("")
    lines.append(f"  OVERALL:   trades={len(rs)}   win_rate={win_rate:.1f}%   "
                 f"avg_R={avg_r:+.2f}   total_R={total_r:+.2f}")
    worst_r  = min(rs) if rs else 0.0
    best_r   = max(rs) if rs else 0.0
    tail     = sum(1 for r in rs if r <= -1.5)
    tail_pct = (tail / len(rs) * 100) if rs else 0.0
    lines.append(f"  RISK:      worst={worst_r:+.2f}R   best={best_r:+.2f}R   "
                 f"squeeze-tail(R<=-1.5)={tail} ({tail_pct:.1f}%)"
                 + ("   <- gap-through losses" if direction == "short" else ""))

    # Outcome breakdown
    out_counts = defaultdict(int)
    for t in all_trades:
        out_counts[t.outcome or "?"] += 1
    lines.append(f"  Outcomes:  " + "   ".join(f"{k}={v}" for k, v in sorted(out_counts.items())))
    lines.append("")
    lines.append(dash)

    # Per-signal performance — a trade contributes to every signal that fired.
    # NOTE: this means co-firing signals share credit (a confound). The "Lift"
    # column (signal avg R minus overall baseline) and the near-isolated table
    # below help tell a real driver apart from a passenger.
    baseline_r = avg_r
    bucket:     dict[str, list[float]] = defaultdict(list)
    iso_bucket: dict[str, list[float]] = defaultdict(list)
    for t in all_trades:
        if t.r_multiple is None:
            continue
        near_isolated = len(t.fired_signals) <= 2
        for s in t.fired_signals:
            bucket[s].append(t.r_multiple)
            if near_isolated:
                iso_bucket[s].append(t.r_multiple)

    lines.append(f"  PER-SIGNAL PERFORMANCE   (baseline avg_R = {baseline_r:+.2f})")
    lines.append(f"  R is the blended scale-out result ({'/'.join(str(int(f*100)) for f in PLAN['tp_exit_pct'])}"
                 f" across TP1/TP2/TP3), not the full TP3 distance.")
    lines.append(dash)
    lines.append(f"  {'Signal':<25} {'Trades':>7} {'Win %':>7} {'Avg R':>8} "
                 f"{'Lift':>7} {'Expect':>8} {'Weight→':>10}")
    lines.append(dash)

    rows = []
    for sig, rs in bucket.items():
        if not rs:
            continue
        w_rate = sum(1 for r in rs if r > 0) / len(rs) * 100
        avg    = sum(rs) / len(rs)
        lift   = avg - baseline_r
        winners = [r for r in rs if r > 0]
        losers  = [r for r in rs if r <= 0]
        avg_w = sum(winners)/len(winners) if winners else 0
        avg_l = sum(losers)/len(losers) if losers else 0
        expect = (w_rate/100) * avg_w + (1 - w_rate/100) * avg_l
        # Suggestion is now driven by LIFT vs baseline (does this signal beat the
        # average trade?), not raw expectancy — raw expectancy rewards passengers
        # that merely co-fire with good signals. Still ignore anything under 30 n.
        cur_w  = BACKTEST_WEIGHTS.get(sig, 0)
        if   len(rs) < 30:    suggest = f"{cur_w}→(n<30)"
        elif lift > 0.25:     suggest = f"{cur_w}→{cur_w + 0.5}"
        elif lift > -0.10:    suggest = f"{cur_w}→{cur_w}"
        elif lift > -0.40:    suggest = f"{cur_w}→{max(cur_w - 0.5, 0.5)}"
        else:                 suggest = f"{cur_w}→DROP?"
        rows.append((sig, len(rs), w_rate, avg, lift, expect, suggest))

    rows.sort(key=lambda x: x[4], reverse=True)   # sort by LIFT
    for sig, n, wr, avg, lift, exp, sug in rows:
        lines.append(f"  {sig:<25} {n:>7} {wr:>6.1f}% {avg:>+7.2f}R "
                     f"{lift:>+6.2f}R {exp:>+7.2f}R {sug:>10}")

    # Near-isolated view — closest cheap proxy to a signal's standalone edge.
    iso_rows = sorted(
        ((s, rs) for s, rs in iso_bucket.items() if rs),
        key=lambda kv: sum(kv[1]) / len(kv[1]), reverse=True,
    )
    if iso_rows:
        lines.append(dash)
        lines.append("  NEAR-ISOLATED (fired with <=2 total signals — proxy for standalone edge):")
        lines.append(f"  {'Signal':<25} {'Trades':>7} {'Win %':>7} {'Avg R':>8}")
        for sig, rs in iso_rows:
            wins = sum(1 for r in rs if r > 0)
            flag = "" if len(rs) >= 20 else "  (low n)"
            lines.append(f"  {sig:<25} {len(rs):>7} {wins/len(rs)*100:>6.1f}% "
                         f"{sum(rs)/len(rs):>+7.2f}R{flag}")

    lines.append(dash)
    lines.append("")
    lines.append("  Reading the suggestions: 'Lift' is this signal's avg R minus the overall")
    lines.append("  baseline. Positive lift = the signal beats an average trade; ~0 = passenger.")
    lines.append("  Co-firing signals share credit, so trust the near-isolated table over raw avg R.")
    lines.append("  Do NOT change weights on <30 trades per signal — statistical noise.")
    lines.append("")
    lines.append("  ** SURVIVORSHIP BIAS WARNING **")
    lines.append("  Coins were chosen from TODAY's traded universe, then replayed over history.")
    lines.append("  Coins that died or delisted are absent; coins that pumped INTO today's")
    lines.append("  top-volume list are over-represented. This inflates results. These numbers")
    lines.append("  are an UPPER bound — real forward performance will be lower. The live signal")
    lines.append("  tracker (which has no survivorship bias) is the honest measure; use this")
    lines.append("  backtest only for RELATIVE signal comparison, not absolute expectancy.")
    lines.append(sep)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run(
    coins:     list[str],
    days:      int,
    tf:        str = "1h",
    direction: str = "long",
    tier:      str = "watch_now",
) -> None:
    t0 = time.time()
    log.info("=" * 64)
    log.info(f"BACKTESTER  —  {len(coins)} coins, {days}d, {tf}, "
             f"direction={direction.upper()}, tier={tier.upper()}")
    log.info("=" * 64)

    # Bars per timeframe
    bars_per_day = {"1h": 24, "2h": 12, "4h": 6, "6h": 4, "12h": 2, "1d": 1}.get(tf, 24)
    total_bars = days * bars_per_day + 200    # +200 warmup
    log.info(f"Requesting {total_bars} bars per coin")

    # BTC reference
    btc_df = data.get_btc(tf, total_bars)
    btc_closes = btc_df["close"] if btc_df is not None else None
    if btc_closes is None:
        log.warning("BTC reference unavailable — btc_decoupling will be skipped")

    all_trades: list[Trade] = []
    failed = 0
    for i, base in enumerate(coins, 1):
        df = data.get_ohlcv(base, "bybit", tf, total_bars, use_cache=True)
        if df is None or len(df) < 200:
            df = data.get_ohlcv(base, "binance", tf, total_bars, use_cache=True)
        if df is None or len(df) < 200:
            failed += 1
            continue

        # Align BTC to this coin's index
        if btc_closes is not None:
            btc_aligned = btc_closes.reindex(df.index).ffill()
        else:
            btc_aligned = None

        trades = backtest_coin(base, df, btc_aligned, direction=direction, tier=tier)
        all_trades.extend(trades)

        if i % 5 == 0 or i == len(coins):
            log.info(f"  ...{i}/{len(coins)}  trades_so_far={len(all_trades)}")

    elapsed = time.time() - t0
    report = build_report(all_trades, elapsed, direction=direction)
    log.info("\n" + report)

    # Write outputs
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    (_OUTPUT_DIR / f"backtest_{ts}.txt").write_text(report, encoding="utf-8")
    (_OUTPUT_DIR / "backtest_LATEST.txt").write_text(report, encoding="utf-8")
    json_data = {
        "generated_at": datetime.now().isoformat(),
        "elapsed_s":    round(elapsed, 2),
        "coins":        coins,
        "days":         days,
        "tf":           tf,
        "direction":    direction,
        "n_trades":     len(all_trades),
        "weights":      BACKTEST_WEIGHTS,
        "tiers":        BACKTEST_TIERS,
        "trades":       [t.__dict__ for t in all_trades],
    }
    (_OUTPUT_DIR / "backtest_LATEST.json").write_text(
        json.dumps(json_data, indent=2, default=str), encoding="utf-8"
    )
    log.info(f"  Saved → backtest_LATEST.txt + .json + backtest_{ts}.txt")


def _resolve_coins(coins_arg: Optional[str], top: Optional[int]) -> list[str]:
    if coins_arg:
        return [c.strip().upper() for c in coins_arg.split(",") if c.strip()]
    if top:
        universe = data.get_universe("both")
        universe.sort(key=lambda x: max(x.get("turnover_24h", 0),
                                        x.get("volume_24h", 0)), reverse=True)
        return [c["base"] for c in universe[:top]]
    # Default: top 30 by current volume
    universe = data.get_universe("both")
    universe.sort(key=lambda x: max(x.get("turnover_24h", 0),
                                    x.get("volume_24h", 0)), reverse=True)
    return [c["base"] for c in universe[:30]]


# ─────────────────────────────────────────────────────────────────────────────
# SELF-TEST — verifies the direction-aware engine (Phase 1). No network/data.
# ─────────────────────────────────────────────────────────────────────────────

def _mk_df(bars: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(bars, columns=["open", "high", "low", "close"])


def _mk_trade(direction, entry, stop, tp1, tp2, tp3) -> Trade:
    return Trade(base="TEST", entered_at_idx=0, entry_price=entry, stop_price=stop,
                 tp1_price=tp1, tp2_price=tp2, tp3_price=tp3, conviction=50.0,
                 signal_count=5, direction=direction)


def _selftest() -> int:
    """Drive hand-built scenarios through walk_trade_forward and assert the R.
    Proves: short inversion, squeeze gap-through-stop, funding/slippage drag,
    and that the LONG path is unchanged. Returns a process exit code."""
    cases = []

    # 1) SHORT win — price falls TP1→TP2→TP3 (risk=7, gross blended R=3.15)
    t = _mk_trade("short", 100, 107, 89.5, 79, 65)
    walk_trade_forward(t, _mk_df([
        (100, 100, 100, 100),   # idx0 entry bar (ignored)
        (99,  100,  89,  90),   # tp1: low 89 <= 89.5
        (88,   90,  78,  79),   # tp2: low 78 <= 79
        (77,   78,  64,  65),   # tp3: low 64 <= 65 → fully closed
    ]), 100)
    cases.append(("short win (~+3.05R net)", t.r_multiple, 3.05, 0.06))

    # 2) SHORT stop WITH gap-through — bar opens 115 above stop 107 → fill 115
    t = _mk_trade("short", 100, 107, 89.5, 79, 65)
    walk_trade_forward(t, _mk_df([
        (100, 100, 100, 100),
        (115, 120, 113, 118),   # gaps up THROUGH stop; fills at the worse open 115
    ]), 100)
    cases.append(("short GAP stop (~-2.24R, worse than 1R)", t.r_multiple, -2.24, 0.06))

    # 3) SHORT stop, no gap — open 103 < stop 107 → fills at stop 107 (~-1.10R)
    t = _mk_trade("short", 100, 107, 89.5, 79, 65)
    walk_trade_forward(t, _mk_df([
        (100, 100, 100, 100),
        (103, 108, 102, 106),   # high 108 >= 107, open 103 < 107 → fill at stop
    ]), 100)
    cases.append(("short clean stop (~-1.10R)", t.r_multiple, -1.10, 0.06))

    # 4) LONG win — MUST be unchanged (no funding/slippage): blended R=3.15
    t = _mk_trade("long", 100, 93, 110.5, 121, 135)
    walk_trade_forward(t, _mk_df([
        (100, 100, 100, 100),
        (101, 111, 100, 110),   # tp1: high 111 >= 110.5
        (112, 122, 111, 121),   # tp2
        (123, 136, 122, 135),   # tp3
    ]), 100)
    cases.append(("long win (=+3.15R, unchanged)", t.r_multiple, 3.15, 0.001))

    # 5) LONG stop — MUST be unchanged: fills at stop level, R=-1.0
    t = _mk_trade("long", 100, 93, 110.5, 121, 135)
    walk_trade_forward(t, _mk_df([
        (100, 100, 100, 100),
        (99,  100,  92,  94),   # low 92 <= 93 → fill at stop 93
    ]), 100)
    cases.append(("long stop (=-1.00R, unchanged)", t.r_multiple, -1.00, 0.001))

    print("=" * 64)
    print("  BACKTESTER SHORT-ENGINE SELF-TEST (Phase 1)")
    print("=" * 64)
    ok = True
    for name, got, want, tol in cases:
        passed = got is not None and abs(got - want) <= tol
        ok = ok and passed
        print(f"  [{'PASS' if passed else 'FAIL'}] {name:<40} got={got}  want~{want:+.2f} (±{tol})")
    print("=" * 64)
    print("  " + ("ALL PASS — short engine verified" if ok
                  else "FAILURES — do NOT proceed to Phase 2"))
    print("=" * 64)
    return 0 if ok else 1


if __name__ == "__main__":
    # Windows console is cp1252 by default; the report uses → and other unicode.
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Backtester v1.1 — replay signals on history (long + short engine)")
    parser.add_argument("--coins", help="Comma-separated bases (e.g. BTC,ETH,SOL)")
    parser.add_argument("--top",   type=int, help="Take top-N by current 24h volume")
    parser.add_argument("--days",  type=int, default=90, help="Days of history (default 90)")
    parser.add_argument("--tf",    default="1h", help="Timeframe (default 1h)")
    parser.add_argument("--direction", choices=["long", "short"], default="long",
                        help="long = ignition signals (default); short = short_scanner bearish signals")
    parser.add_argument("--tier", choices=["watch_now", "on_radar"], default="watch_now",
                        help="short entry threshold: watch_now (conv>=45) or on_radar (conv>=28, more samples)")
    parser.add_argument("--selftest", action="store_true",
                        help="Run the direction-aware engine self-test and exit")
    args = parser.parse_args()

    if args.selftest:
        sys.exit(_selftest())

    coins = _resolve_coins(args.coins, args.top)
    if not coins:
        log.error("No coins to backtest.")
        sys.exit(1)
    log.info(f"Coins ({len(coins)}): {', '.join(coins[:10])}"
             + ("..." if len(coins) > 10 else ""))

    run(coins=coins, days=args.days, tf=args.tf, direction=args.direction, tier=args.tier)
