# Scanner v3 — Calibration & Key-Rotation Runbook

Two parts:

1. **Signal calibration** — how to turn the tracker's output into trustworthy weight changes.
2. **Bybit key rotation** — exact steps and files to change.

---

# Part 1 — Signal calibration workflow

The goal: replace gut-feel signal weights with weights earned from measured outcomes,
**without** fooling yourself on noise, co-firing confounds, or a single market regime.

## 1.1 The cadence (what runs when)

| When | Command | Purpose |
|---|---|---|
| Every scan | `run_radar.bat` | Step 3 calls `signal_tracker.py record` — logs each new WATCH NOW signal with its forward trade plan |
| Once a day (Task Scheduler) | `run_tracker.bat` | `signal_tracker.py update` marks outcomes of open signals, then prints the report |
| Weekly | read the report only | Observe. **Do not change anything.** |
| At the 4–8 week mark | the analysis in 1.4 | This is the only time you touch weights |

Let outcomes accumulate. The tracker has no edge until signals have actually resolved.

## 1.2 How to read the report

Run: `python signal_tracker.py report`
Filters: `--since YYYY-MM-DD` (slice a date range), `--signal NAME` (one signal).

```
OVERALL:   trades=N   win_rate=..%   avg_R=+0.xx   total_R=+x.x
  R is the BLENDED scale-out result (30/40/70 ... ) not the full TP3 distance.

Signal                        Trades   Win %    Avg R    Lift   Expectancy
...
NEAR-ISOLATED (signal fired with <=2 total signals — closest proxy to standalone edge):
Signal                        Trades   Win %    Avg R
...
```

Read it in this order:

1. **OVERALL avg_R first.** This is your baseline — the average R of every tracked signal.
   - If it's **negative**, the *system* isn't profitable yet. Stop. Do not tune individual
     signals on top of a losing base; fix entry logic / tiers / regime gating first.
   - If it's positive, proceed.

2. **NEAR-ISOLATED table is your primary input.** These are outcomes when a signal fired
   with ≤2 total signals — the closest cheap proxy to what the signal does *on its own*.
   The main table's `Avg R` is confounded: every co-firing signal shares the same trade's R,
   so a passenger riding a good signal looks good. Near-isolated strips most of that out.
   - `(low n)` = under 20 trades. **Ignore it** — it's noise.

3. **Lift column (main table) is the confirmation.** Lift = this signal's avg R − baseline.
   - `Lift > 0` → trades with this signal beat the average trade.
   - `Lift ≈ 0` → passenger, no independent edge.
   - `Lift < 0` → drag.

4. **Expectancy** = win% × avg_win + loss% × avg_loss, in R. Sanity context, not a trigger.

## 1.3 Sample-size gates (non-negotiable)

- **Per-signal n ≥ 30** before a signal is even eligible for a weight change. 50+ is better.
- **Near-isolated n ≥ 20** before you read its avg_R as anything but noise.
- **Regime matters more than you think.** 4–8 weeks of crypto is usually *one regime*.
  A signal that pays in a BULL leg can bleed in BEAR. Before acting:
  - Note which regime(s) the data covers (the trend scanner stamps regime in its output).
  - Use `report --since` to slice a known-regime window and compare.
  - Treat any weight change as *regime-conditional* until you've seen it survive a regime flip.

## 1.4 The decision matrix (run once per cycle)

For each signal that clears the n ≥ 30 gate, cross-check **three** readings:
- **A.** Near-isolated avg_R (primary)
- **B.** Lift in the main table (confirmation)
- **C.** Backtester direction (cross-check — see 1.5)

| A: near-isolated avg_R | B: lift | C: backtester | Action |
|---|---|---|---|
| > +0.30R | > +0.25R | agrees | **Bump +0.5** weight |
| −0.10R … +0.30R | −0.10 … +0.25 | — | **Hold** (no change) |
| mild negative | −0.40 … −0.10R | agrees | **Trim −0.5** (floor 0.5) |
| < −0.20R | < −0.40R | agrees | **Drop candidate** — trim first, drop only if it stays bad next cycle |
| any | any | **disagrees** | **Hold** — wait for more data |

If A, B, and C don't point the same direction, do nothing. Disagreement = not enough signal.

## 1.5 Cross-checking tracker vs backtester

The two measure different things — use them together:

- **Tracker** (`signal_tracker.py report`): live, forward, **no survivorship bias**, but small n early.
  This is the honest measure of absolute performance.
- **Backtester** (`python backtester.py --top 100 --days 120`): much bigger n, but **survivorship-biased**
  (coins chosen from today's universe → inflated, upper bound). Use it for **relative** signal
  comparison and direction only, never for absolute expectancy.

Rule: trust **directional agreement**. If the tracker says a signal has negative lift and the
backtester's lift column agrees, that's a real trim/drop candidate. If they disagree, the tracker
wins on *direction* but you wait for more n before acting.

## 1.6 Guardrails (how not to overfit)

- **One signal per cycle.** Change a single weight, then forward-track 2–4 more weeks before the next change.
- **Increment by ±0.5 only.** No large jumps. Floor any non-dropped weight at 0.5.
- **Comment the old value** when you change it, e.g. `"whale_candle": 3.0,  # was 2.5, bumped 2026-07 on +0.4R isolated lift (BULL regime)`.
- **Re-run the backtest after each change** to confirm the direction holds, *then* forward-track.
- **Don't re-tune more than once per cycle**, and don't tune at all while OVERALL avg_R is negative.
- **Leave the hardcoded estimates alone until you have robust data.** The regime win-rates in
  `spot_scanner.py build_trade_plan` (`{"BULL":0.45,"SIDEWAYS":0.38,"BEAR":0.30}`) and the EV math
  are placeholders. Only replace them with measured win-rates once you have n ≥ 50 per regime.

## 1.7 Where the weights live (keep these in sync)

A weight change is **two edits**, and they must match or your backtest stops predicting your live system:

| File | What to edit |
|---|---|
| `ignition_scanner.py` | `SIGNAL_WEIGHTS` dict (the live weights) — and `SIGNAL_PARAMS` if you change a signal's params |
| `backtester.py` | `BACKTEST_WEIGHTS` dict — must mirror `SIGNAL_WEIGHTS` exactly; `SIGNAL_PARAMS` likewise |

Tier thresholds (when to surface a coin at all) live in `ignition_scanner.py` → `TIERS`
(`watch_now`: conviction 40 / 5 signals; `on_radar`: 25 / 3) and `backtester.py` → `BACKTEST_TIERS`.
Keep those in sync too if you change them.

## 1.8 One-cycle checklist

```
[ ] 4–8 weeks of data collected; run `signal_tracker.py report`
[ ] OVERALL avg_R is positive (if not: stop, fix system, do not tune)
[ ] Note the regime(s) this window covered
[ ] For each signal with n >= 30: read near-isolated avg_R + lift
[ ] Run backtester --top 100 --days 120 for the cross-check
[ ] Pick AT MOST ONE signal where A+B+C agree
[ ] Edit SIGNAL_WEIGHTS in ignition_scanner.py (±0.5, comment old value)
[ ] Mirror the change in BACKTEST_WEIGHTS in backtester.py
[ ] Re-run backtester to confirm direction holds
[ ] Forward-track 2–4 weeks before the next change
```

---

# Part 2 — Bybit API key rotation

Do this because the current key/secret were stored in a cloud-synced plaintext file
(`bybit_credentials.json`) and shared. Treat the current key as **compromised** until revoked.

## 2.1 On the Bybit website

1. Log in → **API Management** (`https://www.bybit.com/app/user/api-management`).
2. **Delete / revoke** the existing key (the one in `bybit_credentials.json`).
3. **Create a new key** with the **minimum** permissions you actually use:
   - **Read-only** wherever possible (the trackers only read balance + closed-PnL).
   - **No withdrawal**, **no transfer/internal-transfer** permissions.
   - If you only need it for sync/reporting, do **not** enable trade permissions.
4. (Recommended) **IP-restrict** the key to your machine's IP.
5. Copy the new key + secret somewhere temporary (you'll set env vars next, then discard).

## 2.2 Set environment variables (Windows, User scope)

In PowerShell:

```powershell
[System.Environment]::SetEnvironmentVariable("BYBIT_API_KEY",    "your_new_key",    "User")
[System.Environment]::SetEnvironmentVariable("BYBIT_API_SECRET", "your_new_secret", "User")
# CoinGecko demo key (no longer hardcoded in spot_scanner.py):
[System.Environment]::SetEnvironmentVariable("CG_DEMO_KEY",      "your_cg_demo_key", "User")
```

Then **close and reopen every terminal** (and restart any scheduled-task host) so the new
environment is picked up.

## 2.3 Delete the plaintext credential files

There are **two** possible locations — delete both if present:

```
<project>\python-scanners\engine\scanner_v3\bybit_credentials.json      ← read by daily_pnl_tracker.py
<project>\outputs\journal\bybit_credentials.json                        ← read by trade_journal_sync.py
```

(`<project>` = your `crypto-scanner` root.)

> Note: because these lived in OneDrive, the secret also exists in OneDrive's version history /
> other synced devices. You can't fully purge that — which is exactly why **rotation** (2.1) is
> the real fix, not just deletion.

## 2.4 Deploy the fixed files

Replace these with the fixed versions from this session (they already read env vars first):

- `daily_pnl_tracker.py` — now env-first, plaintext JSON only as a last-resort fallback with a loud warning.
- `spot_scanner.py` — no longer hardcodes the CoinGecko key; reads `CG_DEMO_KEY` from env.

The following already use env vars and need **no change** (they consume `BYBIT_API_KEY` /
`BYBIT_API_SECRET` directly): `bybit_auth.py`, `trade_journal_sync.py`, and the `.bat` launchers
(`run_sync.bat`, `run_bybit_sync.bat`).

## 2.5 Verify nothing else hardcodes a secret

From the project root:

```powershell
findstr /S /I /C:"bybit_credentials" /C:"api_secret" /C:"CG-" *.py
```

Expectation after cleanup: the only hits are the **fallback-loader** code in `daily_pnl_tracker.py`
and `trade_journal_sync.py` (which read the file *if present*), and **no literal key/secret values**
anywhere. If you see an actual key string in a `.py`, remove it.

> `bybit_sync.py` (called by `run_bybit_sync.bat`) wasn't reviewed this session. Check it loads
> from env vars too; the launcher already checks `BYBIT_API_KEY`, so it almost certainly does.

## 2.6 Test

```
run_sync.bat            # should authenticate with the new key and import recent closed trades
run_journal.bat         # should show updated stats
python daily_pnl_tracker.py     # should fetch today's PnL with no SECURITY warning
```

If `daily_pnl_tracker.py` still prints the `SECURITY: credentials loaded from ...` warning, it means
the env vars aren't set in that shell (re-open the terminal) or a JSON file still exists.

## 2.7 Rotation checklist

```
[ ] Old Bybit key revoked on the website
[ ] New key created: read-only, no withdrawal/transfer, IP-restricted
[ ] BYBIT_API_KEY / BYBIT_API_SECRET / CG_DEMO_KEY set as User env vars
[ ] All terminals + scheduled-task host restarted
[ ] Both bybit_credentials.json files deleted
[ ] Fixed daily_pnl_tracker.py and spot_scanner.py deployed
[ ] findstr scan shows no literal secrets
[ ] run_sync.bat authenticates; daily_pnl_tracker.py shows no SECURITY warning
```
