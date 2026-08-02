"""
================================================================================
CHART-FILTER BACKTEST  —  does the L71 "want" band actually predict outcome?
================================================================================
The spot conviction gate is anti-predictive: over 623 trend-tier entries the
LOWEST conviction quintile returned +0.195R and the HIGHEST returned -0.197R,
and every tightening of the floor made results worse. Its live floors (50/60)
have literally zero qualifying trades. So the question stops being "where should
the floor sit" and becomes "what should select trades instead".

The obvious candidate is the L71 chart band, because it has been doing the real
discriminating work by eye and it has never been tested:

    WANT   Vol Ratio 1.1-1.8 · ADX 22-30 · RSI 50-65 · 1D not bearish
    AVOID  Vol >=2.5 + ADX >=45 + vertical  (blow-off)
    AVOID  Vol <1.0                          (dead-cat)

Live evidence that prompted this (2026-08-01/02): BEAT was declined on Vol 0.84
and round-tripped -28.5% in 10h; BABY was declined on Vol 0.76 and died; both
were ranked *highly* by spot conviction (BABY at 45, its drought high).

⚠️ THIS TESTS A PROXY, NOT THE PANEL. Bruno's TradingView panel (ATRibbon Pro —
Confidence %, Confirm Passed/Filtered, Ribbon Bull Stack/Mixed) is proprietary
and CANNOT be reproduced here. What is reproducible from OHLCV, using the
project's own indicators.py so it matches the live scanners:

    vol_ratio  = volume / SMA(volume, 20)     <- DEFINED HERE, a proxy for the
                                                 panel's "Vol Ratio"; the panel's
                                                 exact formula is unknown
    adx        = indicators.compute_adx(14)
    rsi        = indicators.compute_rsi(14)
    d1_bullish = 1D close above its 1D SuperTrend(10,3)

"higher lows on the ribbon" is deliberately NOT modelled — it cannot be defined
robustly without the ribbon, and four honest conditions beat five with one fudged.
So a PASS here is a weaker claim than a live L71 pass.

What it reports, in order:
  Q1  each condition ALONE, by quintile — which of the four carries signal, if any
  Q2  each condition as a binary gate — n / avg R for PASSED vs BLOCKED
  Q3  the full band, and the two AVOID rules, against the rest

Run:
  python backtest_chart_filter.py --top 10 --days 400 --min-tier long --eval-every 24
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
import indicators as IND
import trend_scanner as T
import backtest_confluence as BC
from backtester import Trade, walk_trade_forward, PLAN, _resolve_coins

log = logging.getLogger("backtest_chart_filter")
if not log.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)

ACCOUNT_SIZE = 100_000.0
MIN_N_TO_TRUST = 30

# L71 bands, verbatim from the lesson
VOL_LO, VOL_HI = 1.1, 1.8
ADX_LO, ADX_HI = 22.0, 30.0
RSI_LO, RSI_HI = 50.0, 65.0
BLOWOFF_VOL, BLOWOFF_ADX = 2.5, 45.0
DEADCAT_VOL = 1.0


def chart_metrics(sl: pd.DataFrame, df_1d: pd.DataFrame) -> dict | None:
    """The reproducible half of the L71 panel, from the project's own indicators."""
    if len(sl) < 60 or len(df_1d) < 20:
        return None
    vol = sl["volume"]
    vol_ma = vol.rolling(20).mean().iloc[-1]
    if not vol_ma or pd.isna(vol_ma) or vol_ma <= 0:
        return None
    try:
        adx_s, _, _ = IND.compute_adx(sl, 14)
        rsi_s = IND.compute_rsi(sl["close"], 14)
        st_1d = IND.compute_supertrend(df_1d, 10, 3.0)
    except Exception:
        return None
    if not len(adx_s.dropna()) or not len(rsi_s.dropna()) or not len(st_1d):
        return None
    return {
        "vol_ratio": float(vol.iloc[-1] / vol_ma),
        "adx":       float(adx_s.dropna().iloc[-1]),
        "rsi":       float(rsi_s.dropna().iloc[-1]),
        "d1_bull":   bool(st_1d.iloc[-1]),
    }


def collect_coin(base, df1h, df1d_full, btc1d_full, btc4h_full, min_tier, eval_every) -> list[dict]:
    """Trend-tier entries tagged with their chart metrics (no spot gate)."""
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

        m = chart_metrics(sl, df_1d)
        if m is None:
            continue

        price = float(sl["close"].iloc[-1])
        atr_1d = T._safe_atr(df_1d, 14)
        plan = T.build_trade_plan(price, atr_1d, regime, ACCOUNT_SIZE, macro_mult=1.0)
        if plan is None:
            continue
        tps = plan.take_profits
        tr = Trade(base=base, entered_at_idx=i, entry_price=price, stop_price=plan.stop,
                   tp1_price=tps[0]["price"], tp2_price=tps[1]["price"], tp3_price=tps[2]["price"],
                   conviction=res.total_score, signal_count=0, direction="long")
        tr = walk_trade_forward(tr, df1h, PLAN["time_stop_bars"])
        if tr.outcome is None:
            continue
        out.append({"base": base, "when": str(df1h.index[i]), "r": tr.r_multiple,
                    "regime": regime, "trend_tier": res.tier, **m})
        last_entry_idx = i
    return out


def _stat(rows) -> dict:
    rs = [r["r"] for r in rows if r.get("r") is not None]
    if not rs:
        return {"n": 0, "avg": 0.0, "wr": 0.0, "total": 0.0}
    return {"n": len(rs), "avg": sum(rs) / len(rs),
            "wr": 100.0 * len([x for x in rs if x > 0]) / len(rs), "total": sum(rs)}


def build_report(rows, elapsed, coins, days, min_tier) -> str:
    L, sep, dash = [], "=" * 84, "-" * 84
    add = L.append
    add(sep)
    add("  CHART-FILTER BACKTEST  —  does the L71 band predict outcome?")
    add(f"  {coins} coins · {days}d · trend>={min_tier.upper()} · no spot gate · {elapsed:.0f}s")
    add(sep)
    add("  PROXY WARNING: vol_ratio = volume / SMA(volume,20) — the TradingView panel's")
    add("  exact formula is unknown, and Confidence / Confirm / Ribbon are NOT modelled.")
    add("  'higher lows' is not modelled either. A PASS here is weaker than a live L71 pass.")
    add("")
    if not rows:
        add("  No entries collected — nothing to test.")
        add(sep)
        return "\n".join(L)

    base = _stat(rows)
    add(f"  Population: n={base['n']}  avg {base['avg']:+.3f}R  WR {base['wr']:.1f}%  "
        f"total {base['total']:+.1f}R")
    add("")

    # ── Q1: each metric by quintile ─────────────────────────────────────────
    add("  Q1 — DOES EACH METRIC PREDICT, ON ITS OWN?  (equal-count buckets)")
    for key, label in [("vol_ratio", "Vol Ratio"), ("adx", "ADX"), ("rsi", "RSI")]:
        add(dash)
        add(f"  {label}")
        try:
            s = pd.Series([r[key] for r in rows])
            qs = pd.qcut(s, 5, duplicates="drop")
            for interval in sorted(set(qs), key=lambda x: x.left):
                sel = [r for r, q in zip(rows, qs) if q == interval]
                st = _stat(sel)
                add(f"    {str(interval):24} n={st['n']:>5}  avg {st['avg']:>+7.3f}R  "
                    f"WR {st['wr']:>5.1f}%")
        except Exception:
            add("    (too concentrated to bucket)")
    add(dash)
    d1 = _stat([r for r in rows if r["d1_bull"]])
    d0 = _stat([r for r in rows if not r["d1_bull"]])
    add(f"  1D above SuperTrend   n={d1['n']:>5}  avg {d1['avg']:>+7.3f}R  WR {d1['wr']:>5.1f}%")
    add(f"  1D below SuperTrend   n={d0['n']:>5}  avg {d0['avg']:>+7.3f}R  WR {d0['wr']:>5.1f}%")
    add(dash)
    add("")

    # ── Q2: each condition as a gate ────────────────────────────────────────
    gates = {
        f"Vol in [{VOL_LO},{VOL_HI}]":  lambda r: VOL_LO <= r["vol_ratio"] <= VOL_HI,
        f"ADX in [{ADX_LO:.0f},{ADX_HI:.0f}]": lambda r: ADX_LO <= r["adx"] <= ADX_HI,
        f"RSI in [{RSI_LO:.0f},{RSI_HI:.0f}]": lambda r: RSI_LO <= r["rsi"] <= RSI_HI,
        "1D not bearish":              lambda r: r["d1_bull"],
        "NOT dead-cat (Vol>=1.0)":     lambda r: r["vol_ratio"] >= DEADCAT_VOL,
        "NOT blow-off (Vol<2.5 or ADX<45)": lambda r: not (r["vol_ratio"] >= BLOWOFF_VOL
                                                          and r["adx"] >= BLOWOFF_ADX),
        "FULL L71 BAND (all four)":    lambda r: (VOL_LO <= r["vol_ratio"] <= VOL_HI
                                                  and ADX_LO <= r["adx"] <= ADX_HI
                                                  and RSI_LO <= r["rsi"] <= RSI_HI
                                                  and r["d1_bull"]),
    }
    add("  Q2 — EACH CONDITION AS A GATE")
    add(dash)
    add(f"  {'condition':36} {'PASS n':>7} {'avg R':>8} {'WR':>7} {'BLOCK n':>8} {'avg R':>8}")
    add(dash)
    for name, fn in gates.items():
        p = _stat([r for r in rows if fn(r)])
        b = _stat([r for r in rows if not fn(r)])
        thin = "  <- thin" if 0 < p["n"] < MIN_N_TO_TRUST else ""
        add(f"  {name:36} {p['n']:>7} {p['avg']:>+8.3f} {p['wr']:>6.1f}% "
            f"{b['n']:>8} {b['avg']:>+8.3f}{thin}")
    add(dash)
    add("")
    add("  A condition is worth keeping only if PASS avg R clearly beats BLOCK avg R")
    add("  AND the surviving n clears 30. Caveats: survivorship, no fees (~0.1-0.15R).")
    add(sep)
    return "\n".join(L)


def run(coins, days, min_tier, eval_every) -> int:
    t0 = time.time()
    log.info(f"CHART-FILTER BACKTEST — {len(coins)} coins, {days}d, min-tier={min_tier.upper()}")
    bars_1h, bars_1d = days * 24, max(days + 120, 400)
    btc1d = data.get_ohlcv_deep("BTC", "bybit", "1d", bars_1d)
    btc4h = data.get_ohlcv_deep("BTC", "bybit", "4h", days * 6)

    rows, failed = [], 0
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
            try:    # checkpoint — external kills have cost three runs already
                (BC._OUTPUT_DIR / "chart_filter_LATEST.txt").write_text(
                    build_report(rows, time.time() - t0, i, days, min_tier)
                    + "  [PARTIAL — run still in progress]", encoding="utf-8")
            except Exception as e:
                log.warning(f"  checkpoint failed: {e}")

    elapsed = time.time() - t0
    report = build_report(rows, elapsed, len(coins), days, min_tier)
    log.info("\n" + report)
    if failed:
        log.info(f"  ({failed} coins skipped — no/short data)")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    (BC._OUTPUT_DIR / "chart_filter_LATEST.txt").write_text(report, encoding="utf-8")
    (BC._OUTPUT_DIR / f"chart_filter_{ts}.txt").write_text(report, encoding="utf-8")
    (BC._OUTPUT_DIR / "chart_filter_LATEST.json").write_text(
        json.dumps({"generated_at": ts, "entries": rows}, indent=2), encoding="utf-8")
    log.info(f"  Saved → {BC._OUTPUT_DIR / 'chart_filter_LATEST.txt'}")
    return 0


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Does the L71 chart band predict outcome?")
    ap.add_argument("--coins")
    ap.add_argument("--top", type=int)
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--min-tier", choices=["watch", "long", "strong"], default="long")
    ap.add_argument("--eval-every", type=int, default=24)
    a = ap.parse_args()
    sys.exit(run(_resolve_coins(a.coins, a.top), a.days, a.min_tier, a.eval_every))
