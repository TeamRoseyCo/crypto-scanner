"""
================================================================================
CONFLUENCE BACKTESTER  —  the ACTUAL validated edge: spot-accumulation AND trend
================================================================================
Closes the evidence set. The other backtests scored each scanner ALONE:
  shorts −0.07R (n=915) · ignition +0.13R (n=288) · trend-tier +0.11R (n=230)
— all ~breakeven standalone. This one tests the CONFLUENCE (the INJ pattern):
a coin must BOTH (a) qualify on the spot accumulation scanner AND (b) be a
trend-tier LONG/STRONG at the same bar. That two-layer agreement is the live
edge (52.7% WR / +2.82% on the real trade record). Does the confluence beat
either scanner alone on historical data?

Faithful (no drift):
  - Spot signal  : spot_scanner.detect_signals() — the EXACT 18 live accumulation
                   signals + weighted conviction, on 4h bars (spot's native TF).
  - Trend signal : trend_scanner.score_coin() — the EXACT live multi-TF tier gate.
  - Trade plan   : trend_scanner.build_trade_plan() (the plan the board prints).
  - Exit engine  : backtester.walk_trade_forward() — SAME as every other backtest,
                   so avg-R is directly comparable.

Confluence gate (regime-aware, mirrors spot_scanner MACRO thresholds):
  - bull     : spot conv >= 45
  - sideways : spot conv >= 60  (full)   OR  conv >= 50 AND signals >= 6 (exception)
  - bear     : blocked
  AND trend tier in {long, strong}.

Honest caveats:
  - funding=None in replay (spot funding_neg / crowded penalty inactive) → slightly optimistic.
  - Live spot ALSO needs a 2-scan persistence (⏳) confirm + conviction-trend +
    cooldown + correlation checks — NOT replayed here, so this is an UPPER bound
    on entry frequency (more permissive than live qualification).
  - No fees/slippage (subtract ~0.1-0.15R). Survivorship = today's names replayed
    backward = UPPER bound on R.
  - Confluence is RARE by design (spot 0-qualifies for weeks live) → expect a SMALL
    n. Low frequency IS part of the finding.

Run:
  python backtest_confluence.py --top 80 --days 400 --min-tier long
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

# spot_scanner lives one dir up (engine/), not in scanner_v3/
_ENGINE_DIR = Path(__file__).resolve().parent.parent
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))
import spot_scanner as SP  # noqa: E402

from backtester import Trade, walk_trade_forward, PLAN, _resolve_coins  # noqa: E402


_PROJECT_ROOT = _ENGINE_DIR.parent.parent
_OUTPUT_DIR   = _PROJECT_ROOT / "outputs" / "backtests"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("backtest_confluence")
if not log.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)

# ─────────────────────────────────────────────────────────────────────────────
EVAL_EVERY    = 6
COOLDOWN_BARS = 24
MIN_1D_BARS   = 60
ACCOUNT_SIZE  = 100_000.0
_RESAMPLE     = [("2H", "2h"), ("4H", "4h"), ("6H", "6h"), ("12H", "12h")]
_TF_DELTA     = pd.Timedelta("1D")
_TIER_RANK    = {"below": 0, "watch": 1, "long": 2, "strong": 3}


def _resample_ohlcv(df1h: pd.DataFrame, rule: str) -> pd.DataFrame:
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
    if btc_1d is None or len(btc_1d) < 8:
        return "sideways"
    c = btc_1d["close"]
    btc_7d = float(c.iloc[-1]) / float(c.iloc[-8]) - 1.0
    if btc_7d > T.REGIME["bull_btc_7d_pct"] / 100.0:
        return "bull"
    if btc_7d < T.REGIME["neutral_btc_7d_pct"] / 100.0:
        return "bear"
    return "sideways"


def _spot_qualifies(conv: float, sigs: int, regime: str) -> bool:
    """Mirror spot_scanner's regime-aware entry gate."""
    if regime == "bear":
        return False
    if regime == "bull":
        return conv >= SP.SIGNAL["min_conviction"]           # 45
    # sideways
    if conv >= SP.MACRO["sideways_min_conviction"]:          # 60 full
        return True
    return conv >= SP.MACRO["sideways_exception_conviction"] and sigs >= 6   # 50 + 6 sigs


def backtest_coin(base, df1h, df1d_full, btc1d_full, btc4h_full, min_tier) -> list[Trade]:
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
        ts_end = sl.index[-1] + pd.Timedelta("1h")

        # 1D deep, closed-only (no lookahead)
        df_1d = df1d_full[df1d_full.index + _TF_DELTA <= ts_end]
        if len(df_1d) < MIN_1D_BARS:
            continue
        btc_1d = None
        if btc1d_full is not None:
            btc_1d = btc1d_full[btc1d_full.index + _TF_DELTA <= ts_end]
            if len(btc_1d) < 8:
                btc_1d = None
        regime = _regime_from_btc(btc_1d)
        if regime == "bear":
            continue

        # ── SPOT layer: detect_signals on 4h (spot's native TF) ──────────────
        df_4h = _resample_ohlcv(sl, "4h")
        if len(df_4h) < 42:
            continue
        btc_4h = btc4h_full[btc4h_full.index <= sl.index[-1]] if btc4h_full is not None else None
        price = float(sl["close"].iloc[-1])
        spot = SP.detect_signals(df_4h, btc_4h["close"] if btc_4h is not None else None,
                                 price, binance_source=True, funding_rate=None)
        if spot is None or not _spot_qualifies(spot["conviction"], spot["signal_count"], regime):
            continue

        # ── TREND layer: must ALSO be a tradeable tier ───────────────────────
        cbt: dict[str, pd.DataFrame] = {"1H": sl}
        for lbl, rule in _RESAMPLE:
            cbt[lbl] = _resample_ohlcv(sl, rule)
        cbt["1D"] = df_1d
        p24 = (float(df_1d["close"].iloc[-1]) / float(df_1d["close"].iloc[-2]) - 1) * 100 if len(df_1d) >= 2 else 0.0
        res = T.score_coin(base=base, symbol=f"{base}USDT", candles_by_tf=cbt, btc_1d=btc_1d,
                           funding_rate=None, regime=regime, coin_meta={"price_24h_pct": p24})
        if res is None or _TIER_RANK.get(res.tier, 0) < min_rank:
            continue

        # ── CONFLUENCE confirmed → enter with the trend plan ─────────────────
        atr_1d = T._safe_atr(df_1d, 14)
        plan = T.build_trade_plan(price, atr_1d, regime, ACCOUNT_SIZE, macro_mult=1.0)
        if plan is None:
            continue
        tps = plan.take_profits
        trade = Trade(
            base=base, entered_at_idx=i, entry_price=price, stop_price=plan.stop,
            tp1_price=tps[0]["price"], tp2_price=tps[1]["price"], tp3_price=tps[2]["price"],
            conviction=res.total_score, signal_count=int(spot["signal_count"]),
            direction="long", fired_signals=[res.tier, regime, f"spot{int(spot['conviction'])}"],
        )
        trade = walk_trade_forward(trade, df1h, PLAN["time_stop_bars"])
        if trade.outcome is not None:
            trades.append(trade)
            last_entry_idx = i
    return trades


def build_report(all_trades: list[Trade], elapsed_s: float, min_tier: str) -> str:
    lines, sep, dash = [], "=" * 88, "-" * 88
    lines.append(sep)
    lines.append(f"  CONFLUENCE BACKTESTER  —  {len(all_trades)} LONG trades  "
                 f"(spot-qualifying AND trend≥{min_tier.upper()}, {elapsed_s:.0f}s)")
    lines.append(sep)
    if not all_trades:
        lines.append("  No confluence trades generated — spot + trend rarely agreed in-sample")
        lines.append("  (that low frequency IS the finding: the validated edge is rare).")
        lines.append(sep)
        return "\n".join(lines)

    rs = [t.r_multiple for t in all_trades if t.r_multiple is not None]
    wins = sum(1 for r in rs if r > 0)
    lines.append("")
    lines.append(f"  OVERALL:   trades={len(rs)}   win_rate={wins/len(rs)*100:.1f}%   "
                 f"avg_R={sum(rs)/len(rs):+.2f}   total_R={sum(rs):+.2f}")
    lines.append(f"  RISK:      worst={min(rs):+.2f}R   best={max(rs):+.2f}R")
    out = defaultdict(int)
    for t in all_trades:
        out[t.outcome or "?"] += 1
    lines.append("  Outcomes:  " + "   ".join(f"{k}={v}" for k, v in sorted(out.items())))
    lines.append("")
    lines.append(dash)
    lines.append("  BY TIER / REGIME AT ENTRY:")
    lines.append(f"  {'Bucket':<16} {'Trades':>7} {'Win %':>7} {'Avg R':>8} {'Total R':>9}")
    lines.append(dash)
    bucket: dict[str, list[float]] = defaultdict(list)
    for t in all_trades:
        if t.r_multiple is None:
            continue
        for tag in t.fired_signals:
            if tag.startswith("spot"):
                continue
            bucket[tag].append(t.r_multiple)
    order = ["strong", "long", "bull", "sideways"]
    for tag in sorted(bucket, key=lambda x: (order.index(x) if x in order else 99)):
        b = bucket[tag]
        lines.append(f"  {tag:<16} {len(b):>7} {sum(1 for r in b if r>0)/len(b)*100:>6.1f}% "
                     f"{sum(b)/len(b):>+7.2f}R {sum(b):>+8.2f}R")
    lines.append(dash)
    lines.append("")
    lines.append("  Comparable to: shorts -0.07R (n=915) · ignition +0.13R (n=288) · trend +0.11R (n=230).")
    lines.append("  ** spot 2-scan-persistence NOT replayed (upper bound on frequency); funding inactive;")
    lines.append("     no fees/slippage (subtract ~0.1-0.15R); survivorship = UPPER bound. **")
    lines.append(sep)
    return "\n".join(lines)


def _save(all_trades, elapsed, coins, days, min_tier, ts, final):
    report = build_report(all_trades, elapsed, min_tier)
    if final:
        (_OUTPUT_DIR / f"confluence_backtest_{ts}.txt").write_text(report, encoding="utf-8")
    (_OUTPUT_DIR / "confluence_backtest_LATEST.txt").write_text(report, encoding="utf-8")
    (_OUTPUT_DIR / "confluence_backtest_LATEST.json").write_text(json.dumps({
        "generated_at": datetime.now().isoformat(), "elapsed_s": round(elapsed, 2),
        "coins": coins, "days": days, "min_tier": min_tier, "eval_every_bars": EVAL_EVERY,
        "n_trades": len(all_trades), "final": final,
        "trades": [t.__dict__ for t in all_trades],
    }, indent=2, default=str), encoding="utf-8")


def run(coins: list[str], days: int, min_tier: str) -> None:
    t0 = time.time()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log.info("=" * 64)
    log.info(f"CONFLUENCE BACKTESTER  —  {len(coins)} coins, {days}d, "
             f"min-tier={min_tier.upper()}, eval {EVAL_EVERY}h  (spot AND trend)")
    log.info("=" * 64)

    bars_1h = 999
    bars_1d = max(days, 400)
    btc1d = data.get_btc("1d", bars_1d)
    btc4h = data.get_btc("4h", bars_1h)
    if btc1d is not None and not isinstance(btc1d.index, pd.DatetimeIndex):
        btc1d = None
    if btc4h is not None and not isinstance(btc4h.index, pd.DatetimeIndex):
        btc4h = None

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
        all_trades.extend(backtest_coin(base, df, df1d, btc1d, btc4h, min_tier))
        if i % 5 == 0 or i == len(coins):
            log.info(f"  ...{i}/{len(coins)}  confluence_trades={len(all_trades)}")
            _save(all_trades, time.time() - t0, coins[:i], days, min_tier, ts, final=False)

    elapsed = time.time() - t0
    report = build_report(all_trades, elapsed, min_tier)
    log.info("\n" + report)
    if failed:
        log.info(f"  ({failed} coins skipped — no/short data)")
    _save(all_trades, elapsed, coins, days, min_tier, ts, final=True)
    log.info(f"  Saved → confluence_backtest_LATEST.txt + .json + confluence_backtest_{ts}.txt")


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Confluence backtester — spot-accumulation AND trend-tier")
    ap.add_argument("--coins")
    ap.add_argument("--top", type=int)
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--min-tier", choices=["watch", "long", "strong"], default="long")
    ap.add_argument("--eval-every", type=int, default=EVAL_EVERY)
    args = ap.parse_args()
    EVAL_EVERY = args.eval_every
    coins = _resolve_coins(args.coins, args.top)
    if not coins:
        log.error("No coins.")
        sys.exit(1)
    log.info(f"Coins ({len(coins)}): {', '.join(coins[:10])}" + ("..." if len(coins) > 10 else ""))
    run(coins=coins, days=args.days, min_tier=args.min_tier)
