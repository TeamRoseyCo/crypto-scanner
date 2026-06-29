# Short-Strategy Research & Validation Scope

**Status:** Phase 1 (in progress) · **Created:** 2026-06-29 · **Owner:** Bruno

> **Why this exists:** The live edge (93 trades, 52.7% WR, 2.84:1, +2.82% expectancy) is
> entirely on **longs**. There is *no* validated short track record. This is a disciplined
> R&D track to find out whether a short edge exists **before** a single dollar of capital
> touches it. The live **no-shorts rule stays active** through Phases 1–3.

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

### Phase 3 — Forward paper-trade *(catches what backtests miss)*
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
