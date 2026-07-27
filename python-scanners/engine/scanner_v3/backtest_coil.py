"""
================================================================================
COIL-BREAKOUT BACKTESTER  —  "someone is quietly loading, so catch the pump"
================================================================================
Tests Bruno's hypothesis directly (2026-07-25, born from EUL +72%): a coin that
flags obv_stealth_accum REPEATEDLY over days is being accumulated on flat price;
enter when volume finally expands (the breakout) while still EARLY, then hold for
days with a WIDE stop + WIDE targets to ride the pump.

The trap this checks for is survivorship: EUL pumped, but 509 different coins
flew the SAME multi-day stealth_accum footprint. This replays ALL of them — the
winners AND the ~98% that coiled and died — so the question is answered on the
whole population, not the one screenshot.

Faithful (no drift):
  - Coil    : signals.sig_obv_stealth_accum — the EXACT live signal + params used
              by ignition_scanner (obv_lookback 12, min_obv_pct 0.015,
              max_price_move 0.03). Persistence = fired on >= COIL_MIN of the last
              COIL_LOOKBACK evals (~ the "flagged 12 times" coil).
  - Breakout: signals.sig_vol_expansion (live params) fires NOW, AND the coin is
              still early (24h move <= EARLY_CAP) AND ticking up (no chasing a
              vertical candle — the EUL lesson baked into the entry).
  - Exit    : backtester.walk_trade_forward (SAME engine as every other backtest)
              with a configurable WIDE plan (default 3x ATR stop capped -20%,
              TP RR 3/6/10, 7-day hold) — Bruno's exact "wide SL/TP, hold days".

Two configs worth running to separate ENTRY edge from EXIT choice:
  Bruno  : --stop-mult 3.0 --stop-cap 0.20 --tp-rr 3,6,10 --hold-days 7   (the ask)
  Control: --stop-mult 1.5 --stop-cap 0.15 --tp-rr 1.5,3,5 --hold-days 7  (house plan)
Same entries, different management — if Bruno-mode isn't clearly better, the wide
plan isn't buying anything.

Honest caveats (same as the sibling engines):
  - No fees/slippage (subtract ~0.1-0.15R). Funding inactive in replay.
  - SURVIVORSHIP: coins = today's traded universe replayed backward = UPPER bound.
    Dead/delisted coils are absent, so the TRUE base rate is worse than this.
  - Fills at close-of-bar; one position per coin at a time (COOLDOWN_BARS).

Run:
  python backtest_coil.py --top 120 --days 400
  python backtest_coil.py --top 120 --days 400 --stop-mult 1.5 --stop-cap 0.15 --tp-rr 1.5,3,5
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
import signals as S
import trend_scanner as T   # confluence layer: the EXACT live multi-TF tier gate
from backtester import Trade, walk_trade_forward, _atr_local, _resolve_coins


_THIS_DIR     = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent.parent.parent
_OUTPUT_DIR   = _PROJECT_ROOT / "outputs" / "backtests"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("backtest_coil")
if not log.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)

# ─────────────────────────────────────────────────────────────────────────────
EVAL_EVERY    = 6          # evaluate the coil/breakout every 6h
COOLDOWN_BARS = 24         # one position per coin per day (anti-overlap)
MOMENTUM_BARS = 6          # breakout bar must close above this many bars ago

# Live signal params (identical to ignition_scanner / backtester)
STEALTH_PARAMS = {"obv_lookback": 12, "min_obv_pct": 0.015, "max_price_move": 0.03}
VOLEXP_PARAMS  = {"recent": 6, "base_start": 7, "base_end": 42}   # mult supplied below

# ── Confluence layer (mirrors backtest_confluence.py, no drift) ───────────────
MIN_1D_BARS = 60
_RESAMPLE   = [("2H", "2h"), ("4H", "4h"), ("6H", "6h"), ("12H", "12h")]
_TF_DELTA   = pd.Timedelta("1D")
_TIER_RANK  = {"below": 0, "watch": 1, "long": 2, "strong": 3}


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


def _confluence_ok(base, sl, i_ts, df1d, btc1d, min_rank) -> bool:
    """At the breakout bar, does the coin ALSO pass the live trend-tier gate on a
    non-bear regime? Closed-only 1D + resampled MTF, no lookahead — mirrors
    backtest_confluence.py exactly. Returns True only if tradeable confluence."""
    if df1d is None:
        return False
    ts_end = i_ts + pd.Timedelta("1h")
    df_1d = df1d[df1d.index + _TF_DELTA <= ts_end]
    if len(df_1d) < MIN_1D_BARS:
        return False
    btc_1d = None
    if btc1d is not None:
        btc_1d = btc1d[btc1d.index + _TF_DELTA <= ts_end]
        if len(btc_1d) < 8:
            btc_1d = None
    regime = _regime_from_btc(btc_1d)
    if regime == "bear":
        return False
    cbt = {"1H": sl}
    for lbl, rule in _RESAMPLE:
        cbt[lbl] = _resample_ohlcv(sl, rule)
    cbt["1D"] = df_1d
    p24 = (float(df_1d["close"].iloc[-1]) / float(df_1d["close"].iloc[-2]) - 1) * 100 if len(df_1d) >= 2 else 0.0
    res = T.score_coin(base=base, symbol=f"{base}USDT", candles_by_tf=cbt, btc_1d=btc_1d,
                       funding_rate=None, regime=regime, coin_meta={"price_24h_pct": p24})
    if res is None or _TIER_RANK.get(res.tier, 0) < min_rank:
        return False
    return True


def backtest_coin(base: str, df: pd.DataFrame, cfg: dict,
                  df1d=None, btc1d=None) -> tuple[list[Trade], int, int]:
    """Return (trades, n_coil_windows, n_confluence_rejects). coil_windows is how
    many times this coin presented a qualifying coil (base-rate denominator);
    confluence_rejects is how many breakouts the trend+regime gate vetoed."""
    trades: list[Trade] = []
    confl_rejects = 0
    if df is None or len(df) < 260 or not isinstance(df.index, pd.DatetimeIndex):
        return trades, 0, 0
    min_rank = _TIER_RANK[cfg.get("min_tier", "long")]

    eval_idxs = list(range(200, len(df), EVAL_EVERY))

    # Pass 1: precompute the stealth_accum flag at every eval bar (O(n), no lookahead)
    stealth = {i: S.sig_obv_stealth_accum(df.iloc[:i + 1], **STEALTH_PARAMS).fired
               for i in eval_idxs}

    coil_windows = 0
    last_entry_idx = -COOLDOWN_BARS - 1
    lb   = cfg["coil_lookback"]
    mine = cfg["coil_min"]

    # Pass 2: walk forward, entering on breakout out of a persistent coil
    for pos, i in enumerate(eval_idxs):
        if pos < lb:
            continue
        hits = sum(stealth[j] for j in eval_idxs[pos - lb:pos + 1])
        if hits < mine:
            continue
        coil_windows += 1                       # a real "someone loading" footprint
        if i - last_entry_idx < COOLDOWN_BARS:
            continue

        sl = df.iloc[:i + 1]
        # breakout NOW: volume expansion firing this bar
        if not S.sig_vol_expansion(sl, mult=cfg["breakout_mult"], **VOLEXP_PARAMS).fired:
            continue
        c = sl["close"]
        # still EARLY (not already vertical) and ticking UP (don't chase — EUL rule)
        mv24 = float(c.iloc[-1]) / float(c.iloc[-25]) - 1.0 if len(c) >= 25 else 0.0
        if mv24 > cfg["early_cap"]:
            continue
        if float(c.iloc[-1]) <= float(c.iloc[-1 - MOMENTUM_BARS]):
            continue

        # ── CONFLUENCE gate: also require a tradeable trend-tier on a non-bear
        #    regime at this bar (mirrors backtest_confluence.py). Off by default. ──
        if cfg.get("confluence") and not _confluence_ok(base, sl, sl.index[-1], df1d, btc1d, min_rank):
            confl_rejects += 1
            continue

        entry = float(c.iloc[-1])
        atr = _atr_local(sl.tail(50), 14)
        if atr <= 0:
            continue
        stop = entry - atr * cfg["stop_mult"]
        if (stop - entry) / entry < -cfg["stop_cap"]:      # cap the wide stop
            stop = entry * (1 - cfg["stop_cap"])
        risk = entry - stop
        if risk <= 0:
            continue
        rr = cfg["tp_rr"]
        trade = Trade(
            base=base, entered_at_idx=i, entry_price=entry, stop_price=stop,
            tp1_price=entry + risk * rr[0], tp2_price=entry + risk * rr[1],
            tp3_price=entry + risk * rr[2], conviction=float(hits),
            signal_count=int(hits), direction="long",
            fired_signals=[f"coil{hits}", "vol_expansion"] + (["confluence"] if cfg.get("confluence") else []),
        )
        trade = walk_trade_forward(trade, df, cfg["hold_days"] * 24)
        if trade.outcome is not None:
            trades.append(trade)
            last_entry_idx = i
    return trades, coil_windows, confl_rejects


def build_report(trades: list[Trade], coil_windows: int, coils_hit: int,
                 coins_n: int, elapsed: float, cfg: dict, confl_rejects: int = 0) -> str:
    L, sep, dash = [], "=" * 88, "-" * 88
    mode = f"CONFLUENCE-GATED (trend>={cfg.get('min_tier','long').upper()})" if cfg.get("confluence") else "STANDALONE"
    L.append(sep)
    L.append(f"  COIL-BREAKOUT BACKTESTER [{mode}]  —  {len(trades)} trades  ({elapsed:.0f}s)")
    L.append(f"  Plan: {cfg['stop_mult']}x ATR stop (cap -{cfg['stop_cap']*100:.0f}%)  |  "
             f"TP RR {'/'.join(str(x) for x in cfg['tp_rr'])}  |  hold {cfg['hold_days']}d  |  "
             f"coil {cfg['coil_min']}/{cfg['coil_lookback']} evals  |  breakout vol x{cfg['breakout_mult']}")
    L.append(sep)
    L.append("")
    L.append(f"  BASE RATE:  {coils_hit}/{coins_n} coins presented a qualifying coil; "
             f"{coil_windows} coil-windows total.")
    if cfg.get("confluence"):
        gated = len(trades) + confl_rejects
        pct = (len(trades) / gated * 100) if gated else 0.0
        L.append(f"  CONFLUENCE:  {confl_rejects} breakouts VETOED by trend+regime gate; "
                 f"{len(trades)} passed ({pct:.0f}% of {gated}).")
    if not trades:
        L.append("  No breakout entries triggered — coils rarely broke out early on volume.")
        L.append(sep)
        return "\n".join(L)

    rs = [t.r_multiple for t in trades if t.r_multiple is not None]
    wins = sum(1 for r in rs if r > 0)
    L.append(f"  OVERALL:    trades={len(rs)}   win_rate={wins/len(rs)*100:.1f}%   "
             f"avg_R={sum(rs)/len(rs):+.2f}   total_R={sum(rs):+.2f}")
    L.append(f"  RISK:       worst={min(rs):+.2f}R   best={max(rs):+.2f}R   "
             f"median={sorted(rs)[len(rs)//2]:+.2f}R")
    # R distribution — does a rare huge pump pay for the pile of losers?
    buckets = [("R <= -1  (full stop)", lambda r: r <= -1),
               ("-1 < R <= 0 (bled)",   lambda r: -1 < r <= 0),
               ("0 < R <= 1",           lambda r: 0 < r <= 1),
               ("1 < R <= 3",           lambda r: 1 < r <= 3),
               ("R > 3  (the pump)",    lambda r: r > 3)]
    L.append("")
    L.append("  R DISTRIBUTION:")
    for label, fn in buckets:
        n = sum(1 for r in rs if fn(r))
        bar = "#" * int(round(n / len(rs) * 40))
        L.append(f"    {label:<22} {n:>4} ({n/len(rs)*100:>4.1f}%)  {bar}")
    out = defaultdict(int)
    for t in trades:
        out[t.outcome or "?"] += 1
    L.append("")
    L.append("  Outcomes:   " + "   ".join(f"{k}={v}" for k, v in sorted(out.items())))
    # expectancy math, laid out so the survivorship point is explicit
    winners = [r for r in rs if r > 0]
    losers  = [r for r in rs if r <= 0]
    L.append("")
    L.append(dash)
    L.append(f"  EXPECTANCY:  {len(winners)} winners avg {sum(winners)/max(len(winners),1):+.2f}R   |   "
             f"{len(losers)} losers avg {sum(losers)/max(len(losers),1):+.2f}R")
    huge = [r for r in rs if r > 3]
    L.append(f"  The 'catch the pump' payoff: {len(huge)} trades ({len(huge)/len(rs)*100:.1f}%) "
             f"returned >3R, avg {sum(huge)/max(len(huge),1):+.2f}R.")
    L.append(f"  → they contributed {sum(huge):+.1f}R of the {sum(rs):+.1f}R total; "
             f"the other {len(rs)-len(huge)} trades netted {sum(rs)-sum(huge):+.1f}R.")
    L.append(dash)
    L.append("")
    L.append("  Comparable: shorts -0.07R (n=915) · ignition +0.13R (n=288) · trend +0.11R (n=230)")
    L.append("             · confluence = the validated edge. All standalone signals ~breakeven.")
    L.append("  ** SURVIVORSHIP: today's universe replayed backward = UPPER bound. Dead coils that")
    L.append("     accumulated-then-died are ABSENT, so the real base rate is WORSE. No fees/slippage")
    L.append("     (subtract ~0.1-0.15R). Treat as a ceiling, not a forecast. **")
    L.append(sep)
    return "\n".join(L)


def run(coins: list[str], days: int, cfg: dict) -> None:
    t0 = time.time()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log.info("=" * 64)
    log.info(f"COIL-BREAKOUT  —  {len(coins)} coins, {days}d, "
             f"stop {cfg['stop_mult']}xATR/-{cfg['stop_cap']*100:.0f}%, "
             f"TP {cfg['tp_rr']}, hold {cfg['hold_days']}d")
    log.info("=" * 64)

    total_bars = days * 24 + 260
    confluence = cfg.get("confluence", False)
    bars_1d = max(days, 400)
    btc1d = None
    if confluence:
        btc1d = data.get_btc("1d", bars_1d)
        if btc1d is not None and not isinstance(btc1d.index, pd.DatetimeIndex):
            btc1d = None
        log.info(f"  confluence gate ON (min-tier={cfg.get('min_tier','long').upper()}, "
                 f"BTC 1d ref {'loaded' if btc1d is not None else 'MISSING'})")

    all_trades: list[Trade] = []
    coil_windows = coils_hit = failed = confl_rejects = 0
    for i, base in enumerate(coins, 1):
        df = data.get_ohlcv(base, "bybit", "1h", total_bars, use_cache=True)
        src = "bybit"
        if df is None or len(df) < 260:
            df = data.get_ohlcv(base, "binance", "1h", total_bars, use_cache=True)
            src = "binance"
        if df is None or len(df) < 260 or not isinstance(df.index, pd.DatetimeIndex):
            failed += 1
            continue
        df1d = None
        if confluence:
            df1d = data.get_ohlcv(base, src, "1d", bars_1d, use_cache=True)
            if df1d is not None and (len(df1d) < MIN_1D_BARS or not isinstance(df1d.index, pd.DatetimeIndex)):
                df1d = None
        trades, cw, cr = backtest_coin(base, df, cfg, df1d=df1d, btc1d=btc1d)
        all_trades.extend(trades)
        coil_windows += cw
        confl_rejects += cr
        if cw > 0:
            coils_hit += 1
        if i % 10 == 0 or i == len(coins):
            log.info(f"  ...{i}/{len(coins)}  trades={len(all_trades)}  coils_hit={coils_hit}"
                     + (f"  confl_rejects={confl_rejects}" if confluence else ""))

    elapsed = time.time() - t0
    report = build_report(all_trades, coil_windows, coils_hit, len(coins) - failed, elapsed, cfg,
                          confl_rejects=confl_rejects)
    log.info("\n" + report)
    if failed:
        log.info(f"  ({failed} coins skipped — no/short data)")
    (_OUTPUT_DIR / f"coil_backtest_{ts}.txt").write_text(report, encoding="utf-8")
    (_OUTPUT_DIR / "coil_backtest_LATEST.txt").write_text(report, encoding="utf-8")
    (_OUTPUT_DIR / "coil_backtest_LATEST.json").write_text(json.dumps({
        "generated_at": datetime.now().isoformat(), "elapsed_s": round(elapsed, 2),
        "coins": coins, "days": days, "cfg": cfg,
        "coil_windows": coil_windows, "coils_hit": coils_hit,
        "n_trades": len(all_trades), "trades": [t.__dict__ for t in all_trades],
    }, indent=2, default=str), encoding="utf-8")
    log.info(f"  Saved → coil_backtest_LATEST.txt + .json + coil_backtest_{ts}.txt")


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Coil-breakout backtester — accumulation → volume breakout")
    ap.add_argument("--coins")
    ap.add_argument("--top", type=int)
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--coil-lookback", type=int, default=20, help="evals to look back (20*6h=5d)")
    ap.add_argument("--coil-min", type=int, default=5, help="min stealth_accum hits in lookback")
    ap.add_argument("--breakout-mult", type=float, default=1.8, help="vol_expansion mult for the breakout")
    ap.add_argument("--early-cap", type=float, default=0.08, help="max 24h move at entry (0.08=8%)")
    ap.add_argument("--stop-mult", type=float, default=3.0, help="ATR multiple for the wide stop")
    ap.add_argument("--stop-cap", type=float, default=0.20, help="max stop distance (0.20=20%)")
    ap.add_argument("--tp-rr", default="3,6,10", help="TP risk-multiples, comma-sep")
    ap.add_argument("--hold-days", type=int, default=7)
    ap.add_argument("--confluence", action="store_true",
                    help="require a tradeable trend-tier on non-bear regime at the breakout")
    ap.add_argument("--min-tier", choices=["watch", "long", "strong"], default="long",
                    help="min trend tier for the confluence gate (default long)")
    args = ap.parse_args()

    cfg = {
        "coil_lookback": args.coil_lookback, "coil_min": args.coil_min,
        "breakout_mult": args.breakout_mult, "early_cap": args.early_cap,
        "stop_mult": args.stop_mult, "stop_cap": args.stop_cap,
        "tp_rr": [float(x) for x in args.tp_rr.split(",")], "hold_days": args.hold_days,
        "confluence": args.confluence, "min_tier": args.min_tier,
    }
    coins = _resolve_coins(args.coins, args.top)
    if not coins:
        log.error("No coins.")
        sys.exit(1)
    log.info(f"Coins ({len(coins)}): {', '.join(coins[:10])}" + ("..." if len(coins) > 10 else ""))
    run(coins=coins, days=args.days, cfg=cfg)
