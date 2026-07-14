"""
================================================================================
TREND BACKTESTER  —  replay the TREND scanner's STRONG/LONG signals on history
================================================================================
Answers the question the ignition backtest could NOT: "if I actually took the
trend-tier trade plans I see on the board (BZ / VANA / CL STRONG+PERP), would
they have made money?"

How it stays faithful (no drift):
  - Entry SIGNAL  : trend_scanner.score_coin() — the EXACT live multi-TF score
                    + bonuses + regime-aware tier gate.
  - Trade PLAN    : trend_scanner.build_trade_plan() — the EXACT live ATR stop +
                    TP1/TP2/TP3 levels (regime-aware sizing).
  - Exit ENGINE   : backtester.walk_trade_forward() — the SAME bar-by-bar exit
                    sim as the ignition + short backtests, so the avg-R here is
                    directly comparable to those numbers.

Method:
  - Fetch deep 1H history per coin, RESAMPLE to 2H/4H/6H/12H/1D at each eval bar
    (from 1H bars up to that bar only — the partial current higher-TF bar is
    built the same way the live scanner sees it; no lookahead).
  - Regime is recomputed dynamically from BTC's trailing 7d at each eval bar
    (bull >+3%, bear <-7%, else sideways) — so BEAR bars correctly block
    STRONG/LONG, exactly like live.
  - Only STRONG and LONG tiers open trades (the actionable plans); WATCH is
    skipped. Configurable via --min-tier.

Honest caveats (same family as the other backtests):
  - funding_rate is None in replay (cache has no historical funding), so the
    funding_negative bonus / extreme-long penalty are inactive — slightly
    OPTIMISTIC on borderline scores.
  - No fees/slippage modeled — subtract ~0.1-0.15R in your head.
  - SURVIVORSHIP BIAS: coins are today's top-volume names replayed backward →
    these numbers are an UPPER bound. Real forward performance is lower.
  - Eval cadence is every --eval-every 1H bars (default 4 = 4h) for runtime;
    trend setups persist for many hours so this closely tracks what a live
    ~hourly scan would have surfaced.

Run:
  python backtest_trend.py --top 80 --days 150 --min-tier long
  python backtest_trend.py --coins BZ,VANA,CL --days 150
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
import trend_scanner as T
from backtester import Trade, walk_trade_forward, PLAN, _resolve_coins


# ─────────────────────────────────────────────────────────────────────────────
# PATHS / LOGGING
# ─────────────────────────────────────────────────────────────────────────────
_THIS_DIR     = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent.parent.parent
_OUTPUT_DIR   = _PROJECT_ROOT / "outputs" / "backtests"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("backtest_trend")
if not log.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
EVAL_EVERY   = 4              # evaluate every N 1H bars (4 = every 4h)
COOLDOWN_BARS = 24            # after an entry, wait this many 1H bars before re-entering a coin
MIN_1D_BARS  = 60            # score_coin needs >=60 daily bars to be meaningful
ACCOUNT_SIZE = 100_000.0     # matches trend_scanner ACCOUNT default (sizing is irrelevant to R)

# 1H → intraday-TF resample rules (pandas offset aliases). The 1D frame is
# fetched DIRECTLY (deep) rather than resampled, because the 1H cache caps at
# 999 bars (~42d) — far short of score_coin's 60-daily-bar minimum.
_RESAMPLE = [("2H", "2h"), ("4H", "4h"), ("6H", "6h"), ("12H", "12h")]
_TF_DELTA = pd.Timedelta("1D")   # a daily bar is closed 1 day after its open-time

_TIER_RANK = {"below": 0, "watch": 1, "long": 2, "strong": 3}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _resample_ohlcv(df1h: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Aggregate 1H bars into a higher timeframe. The final bar may be partial
    (built from the 1H bars seen so far) — this mirrors what the live scanner
    sees mid-period and introduces NO lookahead because df1h is already sliced
    to the eval bar."""
    agg = {
        "open":  df1h["open"].resample(rule).first(),
        "high":  df1h["high"].resample(rule).max(),
        "low":   df1h["low"].resample(rule).min(),
        "close": df1h["close"].resample(rule).last(),
    }
    if "volume" in df1h.columns:
        agg["volume"] = df1h["volume"].resample(rule).sum()
    return pd.DataFrame(agg).dropna(subset=["open", "high", "low", "close"])


def _regime_from_btc(btc_1d: Optional[pd.DataFrame]) -> str:
    """Dynamic regime from BTC trailing 7d — mirrors trend_scanner.REGIME."""
    if btc_1d is None or len(btc_1d) < 8:
        return "sideways"
    c = btc_1d["close"]
    btc_7d = float(c.iloc[-1]) / float(c.iloc[-8]) - 1.0
    if btc_7d > T.REGIME["bull_btc_7d_pct"] / 100.0:
        return "bull"
    if btc_7d < T.REGIME["neutral_btc_7d_pct"] / 100.0:
        return "bear"
    return "sideways"


# ─────────────────────────────────────────────────────────────────────────────
# PER-COIN BACKTEST
# ─────────────────────────────────────────────────────────────────────────────

def backtest_coin(
    base:        str,
    df1h:        pd.DataFrame,
    df1d_full:   pd.DataFrame,
    btc1d_full:  Optional[pd.DataFrame],
    min_tier:    str,
) -> list[Trade]:
    trades: list[Trade] = []
    if df1h is None or len(df1h) < 200 or df1d_full is None:
        return trades
    if not isinstance(df1h.index, pd.DatetimeIndex) or not isinstance(df1d_full.index, pd.DatetimeIndex):
        return trades

    min_rank = _TIER_RANK[min_tier]
    last_entry_idx = -COOLDOWN_BARS - 1

    for i in range(200, len(df1h), EVAL_EVERY):
        if i - last_entry_idx < COOLDOWN_BARS:
            continue

        sl = df1h.iloc[:i + 1]
        ts_end = sl.index[-1] + pd.Timedelta("1h")   # close-time of the entry bar

        # Intraday TFs: resample from 1H-up-to-now (partial current bar, as live sees it).
        cbt: dict[str, pd.DataFrame] = {"1H": sl}
        for lbl, rule in _RESAMPLE:
            cbt[lbl] = _resample_ohlcv(sl, rule)

        # 1D: deep direct frame, CLOSED bars only (open-time + 1D <= entry close). No lookahead.
        df_1d = df1d_full[df1d_full.index + _TF_DELTA <= ts_end]
        if len(df_1d) < MIN_1D_BARS:
            continue
        cbt["1D"] = df_1d

        btc_1d = None
        if btc1d_full is not None:
            btc_1d = btc1d_full[btc1d_full.index + _TF_DELTA <= ts_end]
            if len(btc_1d) < 8:
                btc_1d = None
        regime = _regime_from_btc(btc_1d)

        price = float(sl["close"].iloc[-1])
        p24 = 0.0
        if len(df_1d) >= 2:
            p24 = (float(df_1d["close"].iloc[-1]) / float(df_1d["close"].iloc[-2]) - 1) * 100
        coin_meta = {"turnover_24h": 0.0, "volume_24h": 0.0, "price_24h_pct": p24}

        res = T.score_coin(
            base=base, symbol=f"{base}USDT", candles_by_tf=cbt,
            btc_1d=btc_1d, funding_rate=None, regime=regime, coin_meta=coin_meta,
        )
        if res is None or _TIER_RANK.get(res.tier, 0) < min_rank:
            continue

        atr_1d = T._safe_atr(df_1d, 14)
        plan = T.build_trade_plan(price, atr_1d, regime, ACCOUNT_SIZE, macro_mult=1.0)
        if plan is None:
            continue

        tps = plan.take_profits
        trade = Trade(
            base           = base,
            entered_at_idx = i,
            entry_price    = price,
            stop_price     = plan.stop,
            tp1_price      = tps[0]["price"],
            tp2_price      = tps[1]["price"],
            tp3_price      = tps[2]["price"],
            conviction     = res.total_score,
            signal_count   = res.st_aligned,
            direction      = "long",
            fired_signals  = [res.tier, regime],   # tag tier + regime for breakdown
        )
        trade = walk_trade_forward(trade, df1h, PLAN["time_stop_bars"])
        if trade.outcome is not None:
            trades.append(trade)
            last_entry_idx = i

    return trades


# ─────────────────────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────────────────────

def build_report(all_trades: list[Trade], elapsed_s: float, min_tier: str) -> str:
    lines, sep, dash = [], "=" * 88, "-" * 88
    lines.append(sep)
    lines.append(f"  TREND BACKTESTER  —  {len(all_trades)} LONG trades  "
                 f"(min-tier={min_tier.upper()}, {elapsed_s:.0f}s)")
    lines.append(sep)

    if not all_trades:
        lines.append("  No trades generated. Check date range / coin selection / tier.")
        lines.append(sep)
        return "\n".join(lines)

    rs = [t.r_multiple for t in all_trades if t.r_multiple is not None]
    wins = sum(1 for r in rs if r > 0)
    avg_r = sum(rs) / len(rs) if rs else 0
    win_rate = wins / len(rs) * 100 if rs else 0
    total_r = sum(rs)

    lines.append("")
    lines.append(f"  OVERALL:   trades={len(rs)}   win_rate={win_rate:.1f}%   "
                 f"avg_R={avg_r:+.2f}   total_R={total_r:+.2f}")
    worst_r = min(rs) if rs else 0.0
    best_r  = max(rs) if rs else 0.0
    tail    = sum(1 for r in rs if r <= -1.5)
    lines.append(f"  RISK:      worst={worst_r:+.2f}R   best={best_r:+.2f}R   "
                 f"tail(R<=-1.5)={tail} ({tail/len(rs)*100:.1f}%)")

    out_counts = defaultdict(int)
    for t in all_trades:
        out_counts[t.outcome or "?"] += 1
    lines.append(f"  Outcomes:  " + "   ".join(f"{k}={v}" for k, v in sorted(out_counts.items())))
    lines.append("")
    lines.append(dash)

    # Breakdown by tier and by regime (tags stashed in fired_signals)
    lines.append("  BY TIER / REGIME AT ENTRY:")
    lines.append(f"  {'Bucket':<16} {'Trades':>7} {'Win %':>7} {'Avg R':>8} {'Total R':>9}")
    lines.append(dash)
    bucket: dict[str, list[float]] = defaultdict(list)
    for t in all_trades:
        if t.r_multiple is None:
            continue
        for tag in t.fired_signals:
            bucket[tag].append(t.r_multiple)
    order = ["strong", "long", "watch", "bull", "sideways", "bear"]
    for tag in sorted(bucket, key=lambda x: (order.index(x) if x in order else 99)):
        b = bucket[tag]
        wr = sum(1 for r in b if r > 0) / len(b) * 100
        lines.append(f"  {tag:<16} {len(b):>7} {wr:>6.1f}% "
                     f"{sum(b)/len(b):>+7.2f}R {sum(b):>+8.2f}R")

    lines.append(dash)
    lines.append("")
    lines.append("  R is the blended 30/40/30 scale-out from the shared exit engine — directly")
    lines.append("  comparable to the ignition (+0.13R) and short (-0.07R) backtests.")
    lines.append("  ** funding inactive in replay + NO fees/slippage (subtract ~0.1-0.15R) **")
    lines.append("  ** SURVIVORSHIP BIAS: today's top-volume names replayed backward = UPPER bound **")
    lines.append(sep)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def _save(all_trades: list[Trade], elapsed: float, coins: list[str], days: int,
          min_tier: str, ts: str, final: bool) -> None:
    """Write cumulative report + json. Called incrementally so a kill mid-run
    still leaves a valid partial result on disk."""
    report = build_report(all_trades, elapsed, min_tier)
    if final:
        (_OUTPUT_DIR / f"trend_backtest_{ts}.txt").write_text(report, encoding="utf-8")
    (_OUTPUT_DIR / "trend_backtest_LATEST.txt").write_text(report, encoding="utf-8")
    payload = {
        "generated_at": datetime.now().isoformat(), "elapsed_s": round(elapsed, 2),
        "coins": coins, "days": days, "min_tier": min_tier,
        "eval_every_bars": EVAL_EVERY, "n_trades": len(all_trades), "final": final,
        "trades": [t.__dict__ for t in all_trades],
    }
    (_OUTPUT_DIR / "trend_backtest_LATEST.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8")


def run(coins: list[str], days: int, min_tier: str) -> None:
    t0 = time.time()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log.info("=" * 64)
    log.info(f"TREND BACKTESTER  —  {len(coins)} coins, {days}d 1H history, "
             f"min-tier={min_tier.upper()}, eval every {EVAL_EVERY}h")
    log.info("=" * 64)

    bars_1h = 999                       # data layer caps 1H at ~999 bars (~42d entry window)
    bars_1d = max(days, 400)            # deep daily history for the 60-bar warmup + RS/regime
    btc1d = data.get_btc("1d", bars_1d)
    if btc1d is None or not isinstance(btc1d.index, pd.DatetimeIndex):
        log.warning("BTC 1D reference unavailable/unindexed — regime falls back to sideways")
        btc1d = None

    all_trades: list[Trade] = []
    failed = 0
    for i, base in enumerate(coins, 1):
        df = data.get_ohlcv(base, "bybit", "1h", bars_1h, use_cache=True)
        src = "bybit"
        if df is None or len(df) < 200:
            df = data.get_ohlcv(base, "binance", "1h", bars_1h, use_cache=True)
            src = "binance"
        if df is None or len(df) < 200 or not isinstance(df.index, pd.DatetimeIndex):
            failed += 1
            continue
        df1d = data.get_ohlcv(base, src, "1d", bars_1d, use_cache=True)
        if df1d is None or len(df1d) < MIN_1D_BARS:
            failed += 1
            continue
        all_trades.extend(backtest_coin(base, df, df1d, btc1d, min_tier))
        if i % 5 == 0 or i == len(coins):
            log.info(f"  ...{i}/{len(coins)}  trades_so_far={len(all_trades)}")
            _save(all_trades, time.time() - t0, coins[:i], days, min_tier, ts, final=False)  # checkpoint

    elapsed = time.time() - t0
    report = build_report(all_trades, elapsed, min_tier)
    log.info("\n" + report)
    if failed:
        log.info(f"  ({failed} coins skipped — no/short data)")
    _save(all_trades, elapsed, coins, days, min_tier, ts, final=True)
    log.info(f"  Saved → trend_backtest_LATEST.txt + .json + trend_backtest_{ts}.txt")


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="Trend-tier backtester — replay trend_scanner STRONG/LONG plans on history")
    ap.add_argument("--coins", help="Comma-separated bases (e.g. BZ,VANA,CL)")
    ap.add_argument("--top", type=int, help="Take top-N by current 24h volume")
    ap.add_argument("--days", type=int, default=150, help="Days of 1H history to fetch (default 150; ~first 60d are 1D warmup)")
    ap.add_argument("--min-tier", choices=["watch", "long", "strong"], default="long",
                    help="Minimum trend tier to trade (default long = LONG + STRONG)")
    ap.add_argument("--eval-every", type=int, default=EVAL_EVERY,
                    help=f"Evaluate every N 1H bars (default {EVAL_EVERY}; higher = faster, coarser)")
    args = ap.parse_args()

    EVAL_EVERY = args.eval_every   # override module default (read by backtest_coin)

    coins = _resolve_coins(args.coins, args.top)
    if not coins:
        log.error("No coins to backtest.")
        sys.exit(1)
    log.info(f"Coins ({len(coins)}): {', '.join(coins[:10])}" + ("..." if len(coins) > 10 else ""))
    run(coins=coins, days=args.days, min_tier=args.min_tier)
