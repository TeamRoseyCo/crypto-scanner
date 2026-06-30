# Short-Strategy Research & Validation Scope

**Status:** ⛔ CONCLUDED (2026-06-30) — Phases 1–2 done, **NO tradeable edge**, do not proceed to Phase 3 · **Created:** 2026-06-29 · **Owner:** Bruno

> **Why this exists:** The live edge (93 trades, 52.7% WR, 2.84:1, +2.82% expectancy) is
> entirely on **longs**. There is *no* validated short track record. This is a disciplined
> R&D track to find out whether a short edge exists **before** a single dollar of capital
> touches it. The live **no-shorts rule stays active** through Phases 1–3.

## ⛔ CONCLUSION (2026-06-30) — NO tradeable edge; do NOT proceed to Phase 3
Phase 2 at scale (**ON-RADAR tier, 80 coins × 90d = 915 short trades**): **win rate 33.7%,
avg −0.07R, total −60.35R.** Marginally losing — it *converged toward breakeven-minus as n grew*
(small samples were −0.78R / −0.17R; the apparent strong per-signal edges were **NOISE that
regressed to ~0** — e.g. `lower_highs` +0.88R → +0.01R, `cmf_negative` +0.11R → −0.07R).
- **Relative ordering held but is tiny:** distribution/structure signals have slightly positive lift
  (`bear_distribution_candle` best at +0.14 lift / +0.07R, n=218; `bear_obv_distribution` /
  `lower_highs` / `vol_distribution` ~+0.07 lift); divergence/squeeze are confirmed anti-edge
  (`bb_squeeze` −0.18, `bear_failed_breakout` −0.13, `bear_obv_divergence` / `bear_rsi_divergence` neg).
- Even the **single best signal is +0.07R** — nowhere near the +0.5R bar, and that's *before* real
  funding and *before* the live squeeze-exclusion filter. A structure-only variant is ~breakeven at best.
- **squeeze-tail = 0** across all 915 trades — the `max_24h_change 30%` entry filter keeps shorts OUT
  of the violent parabolas (TAC/RAVE), so shorts die by **attrition** (33.7% WR), not squeezes (worst −1.14R).

**Decision: the no-shorts rule is now empirically validated at n=915. Do NOT paper-trade (Phase 3) or
deploy a general/selective short strategy.** The engine (Phases 1–2, branch `feat/short-backtest-engine`)
is kept for future signal research; revisit only if `short_scanner` gains materially new signals.

## Substrate already in the repo
- `python-scanners/engine/scanner_v3/short_scanner.py` — short signal generation
  (WATCH NOW conv≥45 / ON RADAR conv≥28; already excludes funding ≤ −0.01% to skip obvious squeezes).
- `python-scanners/engine/scanner_v3/backtester.py` — replay spine, **long-only** today
  (replays ignition→long paper trades; models **no** slippage/fees). **Phase 1 extends this.**
- `python-scanners/outputs/tracker/signals.sqlite` — tracker with a `direction` column +
  `outcome` / `r_multiple`; built to log forward short outcomes (Phase 3).
- `cache/backtest_ohlcv` — cached OHLCV to replay against.

## Success criteria — the bar shorts must clear to earn live capital
Defined **before** building, so results cannot be rationalized after the fact.
Over **≥150 historical short signals across ≥2 distinct down-legs**, net of modeled costs:
1. **Win rate ≥ 45%** AND **avg ≥ +0.5R / trade** (after funding + ~0.2R slippage/fee drag).
2. **Positive expectancy that survives the squeeze tail** — the decisive test. Measure the
   **worst single trade** and the **fat right-tail** (violent pumps, e.g. TAC +166%/day). If one
   squeeze erases ~20 wins, it is not an edge. Mean alone is not sufficient.
3. **Additive vs the long edge** — shorts must beat doing nothing *and* complement longs, not
   merely be non-negative.

## Phases
### Phase 1 — Extend the backtester for shorts *(engineering — current)*
Add `direction='short'`:
- Invert trade plan: **stop above** entry, **TPs below** entry.
- Model **funding cost per bar held** (shorts pay funding in a bid market).
- Model **gap/squeeze risk honestly** — let the stop **gap through** on a violent up-bar so a
  loss can exceed the nominal stop. Modeling stops as always-filling-at-level would make the
  backtest lie about exactly the risk that kills shorts.

### Phase 2 — Historical backtest *(first real signal)*
Replay `short_scanner` WATCH NOW / ON RADAR signals over cached down-legs. Output: win rate,
avg R, expectancy, **worst trade + squeeze-tail impact**, broken down by conviction tier and by
signal. If no hint of edge → **stop here** (a valid, money-saving outcome).

### Phase 3 — Forward paper-trade *(catches what backtests miss)* — ⛔ SHELVED (Phase 2 showed no edge)
If Phase 2 promises: log live short signals to `signals.sqlite` (`direction=short, status=paper`)
and track **real** forward outcomes ~4 weeks / ≥30 signals. Zero capital. Real funding, real
squeezes, real signal lag.

### Phase 4 — Go/No-Go → probation size
Only if **both** backtest *and* paper clear the bar: deploy at **0.25% risk (quarter size)** for a
probation window; scale only on continued performance. Fail any gate → shelve it.

## Guardrails
- **Zero live short capital until Phase 4.** No-shorts rule stays active for live trading in 1–3.
- Every result reported **net of modeled costs**, with the **worst-case tail** shown — never just
  the headline average.
