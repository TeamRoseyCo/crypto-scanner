# TRADING RULES — Bruno's Discipline Brief (read at session start)

**Last updated:** 2026-06-02
**Owner:** Bruno
**Co-pilot:** Claude Sonnet 4.6 (PowerShell / Claude Code)
**Authored by:** Claude (web interface) based on Bybit transaction log analysis

This file establishes hard trading rules derived from real PnL data.
Sonnet 4.6 (PowerShell Claude) is responsible for enforcing these in
daily trading decisions and reminding Bruno of them BEFORE any new entry.

**Changes 2026-06-02:**
- Rule 1: ban lifted, replaced with **conditional re-entry framework**
- Rule 3: now enforced **mechanically** via daily_pnl_tracker.py + Sonnet 4.6 prompt block
- Rule 7 (shorts) and Rule 8 (conflicts) — unchanged from 2026-05-24

---

## CONTEXT — what the data showed

Original analysis (32 days, 2026-04-17 → 2026-05-19):
- **Trading PnL: -$3,351.55** on a ~$96,800 account (-3.5% in 1 month)
- 14.8 orders/day, 474 BTC trades for net +$316
- Top 10 losing symbols cost -$11,100 combined

14-day follow-up (2026-05-19 → 2026-06-02):
- **Realized PnL: +$301** — direction reversed
- 1.9 orders/day (87% drop from historical)
- Banned symbols: PERFECT compliance — zero trades on INIT/MERL/HAEDAL/LINK
- BTC trade cap: respected — zero BTC trades
- Daily loss limit: **3 breaches in 14 days** (May 23: -$16,725, May 29: -$11,278, May 31: -$2,859)

The behavioral improvement is real. The daily loss limit being aspirational
rather than operational is the remaining gap — addressed by Rule 3 below.

---

## RULE 1 — CONDITIONAL RE-ENTRY (INIT/MERL/HAEDAL/LINK)

The original ban (2026-05-19 → 2026-06-02) was respected. The ban is **lifted**,
replaced with a graduated re-entry framework.

| Symbol  | Historical PnL | Why originally banned                    |
|---------|----------------|------------------------------------------|
| INIT    | -$1,889 (208 trades) | Excessive re-entries / revenge trading |
| MERL    | -$1,556 (94 trades)  | Same pattern                           |
| HAEDAL  | -$1,248 (58 trades)  | Same pattern                           |
| LINK    | -$1,417 (86 trades)  | High-volume losses                     |

### Re-entry constraints (2026-06-02 → 2026-06-30, 4 weeks)

For these four coins specifically:
- **Maximum risk per trade: $485** (0.5% of $97K, half the normal 1%)
- **Maximum 2 trades per week per coin** (was: unlimited; historical: 208 on INIT)
- Standard rules still apply (regime gating, position sizing math, stops mandatory)

### Graduation criteria (review 2026-06-30)

If at the 4-week mark:
- All sizing and trade-count constraints were respected, AND
- Net P&L on these four coins is positive or breakeven

→ Constraints lift entirely. The coins return to normal rules.

If a coin's trade-count or sizing limit was breached:
→ That coin returns to the full-banned list for 30 days.

If constraints were respected but net P&L is materially negative (worse
than -$500 combined across the four coins):
→ Extend the conditional re-entry period another 4 weeks at current constraints.

### Sonnet 4.6 action:
- When asked about INIT/MERL/HAEDAL/LINK:
  1. Note the conditional re-entry constraints up front
  2. Check trade count so far this week for that specific coin (ask Bruno
     if you don't have a journal handy)
  3. Verify proposed risk is ≤ $485
  4. Then evaluate against other rules

---

## RULE 2 — BTC TRADE CAP

**Maximum 5 BTC trades per day (LONG + SHORT combined).**

Why: historical period showed 474 BTC trades in 32 days for net +$316. That's
frenetic scalping with no edge.

Recent compliance: PERFECT (zero BTC trades in 14 days). Cap stays in place.

**Sonnet 4.6 action:** if Bruno has hit 5 BTC trades today, refuse new BTC entries.

---

## RULE 3 — DAILY LOSS LIMIT (MECHANICALLY ENFORCED)

**Hard stop: -$1,940 cumulative realized PnL = no new entries.**

This rule is now enforced via a **two-layer mechanical block**:

### Layer A — daily_pnl_tracker.py
Runs every 5 minutes via Task Scheduler. Pulls today's realized PnL from Bybit
UM (perps), writes status file at `outputs/daily_pnl/today.json`.

Status field is either:
- `"OK"` — realized PnL is above -$1,940
- `"LIMIT_HIT"` — realized PnL is at or below -$1,940

When `LIMIT_HIT`, the tracker also creates `outputs/daily_pnl/LIMIT_HIT.txt`
as a hard flag.

### Layer B — Sonnet 4.6 system prompt block
Before approving ANY entry, Sonnet 4.6:
1. Reads `today.json`
2. If status is `LIMIT_HIT` → refuses, citing the rule
3. If status is `OK` → notes headroom remaining, then evaluates normally
4. Does not entertain override arguments

If proposed position size + stop loss would exceed the remaining headroom,
the entry is also refused (rule is forward-looking, not just reactive).

### Setup
See `RULE3_SETUP.md` for the step-by-step setup. Once set up:
- Scheduled task runs every 5 minutes
- You consult Sonnet before entries
- Sonnet reads the status and enforces

### Honest limitation
This system enforces Rule 3 ONLY when Bruno consults Sonnet before a trade.
If Bruno bypasses Sonnet and trades directly on Bybit, no code-level block
exists. The enforcement is behavioral — you choose to consult Sonnet; Sonnet
then mechanically enforces.

The only fully bypass-proof block would be reducing Bybit account balance
below $194K worth of leveraged exposure (so a -$1,940 loss is structurally
impossible). That's a heavier option left for explicit future decision.

### Once daily PnL hits -$1,940:
- Close any open scalps immediately (managing existing positions is allowed;
  ADDING to them is not — that's a new entry)
- No new positions in any direction for the rest of that UTC day
- 00:00 UTC the next day, the limit resets automatically

---

## RULE 4 — POSITION SIZING (1% RISK RULE — LONGS)

**Maximum risk per LONG trade: $970** (1% of $97K account).

Math:
- 3% stop = max position size $32,300
- 5% stop = max position size $19,400
- 10% stop = max position size $9,700

**Sonnet 4.6 action:**
- For every long entry signal, calculate max position size from the stop distance
- Tell Bruno: "Stop is X% away. Max position size at 1% risk = $Y."
- If Bruno wants to size bigger, require written justification in chat
- Default refuse if no justification given for oversized positions

---

## RULE 5 — REVERSE-ENGINEER WINNERS (weekly review)

Sunday evening:
1. Open last week's `outputs\journal\trades.json`
2. For each winning trade, find its entry date
3. Cross-reference with that day's scanner outputs
4. Build a running table: "Of my winners, what % were also scanner picks
   at >=8 confluence (long) or >=5 signals (short)?"

Once `signal_tracker` DB has >=4 weeks of data: also pull `signal_tracker.py report`
and cross-reference which signals produce the most R.

---

## RULE 6 — ENTRY DECISIONS GO THROUGH SONNET 4.6

All new entries (long or short) get consulted with Sonnet 4.6 first.

Workflow:
1. Bruno asks: "Should I enter [SYMBOL] [LONG/SHORT] at $X?"
2. Sonnet 4.6 checks:
   - **Read `today.json`** — if LIMIT_HIT, refuse immediately (Rule 3)
   - Is symbol on the conditional re-entry list (Rule 1)? Apply constraints
   - Is symbol in `conflicts_LATEST.txt`? Refuse (Rule 8)
   - Is it BTC and >5 trades today? Refuse (Rule 2)
   - For SHORTS: regime check, funding check, calibration sizing (Rule 7)
   - Position size within Rule 4?
   - Is proposed position size + stop loss within today's headroom?
3. Sonnet responds: APPROVE / REFUSE / DISCUSS — with reasoning
4. Bruno makes the final call. Explicit override required if going against
   Sonnet's refusal. Overrides are logged.

---

## RULE 7 — SHORT-SIDE RULES (calibration through 2026-06-21)

### 7a. Calibration sizing (until 2026-06-21)
- Max risk per SHORT trade: **$485** (0.5% of account, half of longs)

### 7b. Regime gating
- BEAR: shorts allowed normal sizing
- SIDEWAYS: shorts allowed, require >=6 signals
- BULL: shorts blocked by default (override + reasoning required)

### 7c. Funding rate hard exclude
Funding <= -0.01% -> refuse short (squeeze setup, not short setup).

### 7d. Daily cap: 3 shorts/day during calibration.

### 7e. Cross-direction cooldown: 4 hours after a stop-out in either direction.

### 7f. Anti-squeeze size discipline
For shorts on coins with 24h volume < $5M: multiply stop distance by 1.5x
when calculating position size (sizes down for gap risk).

### Graduation criteria (review 2026-06-21):
- >=30 closed short trades + positive expectancy -> lift cap to 1% sizing
- <30 closed shorts -> extend calibration 4 weeks
- Negative expectancy -> keep at 0.5%, investigate which signals are losing

---

## RULE 8 — CONFLICT FLAGGING

If a coin appears in BOTH a long scanner AND the short scanner's WATCH NOW
in the same scan cycle: that coin is OFF-LIMITS in both directions until
resolved.

The tracker writes `outputs/tracker/conflicts_LATEST.txt` after every scan.
Sonnet 4.6 reads it before approving entries.

---

## HARD STOPS — ALWAYS REFUSE THESE

**LONG side:**
- Adding to a losing long (averaging down)
- Re-entering same coin within 1 hour of stop-out
- Position size > 5% of account ($4,850)
- Leverage > 5x on alts, > 10x on BTC/ETH

**SHORT side:**
- Adding to a losing short (averaging UP)
- Re-entering same coin within 1 hour of stop-out
- Position size > 3% of account ($2,910) during calibration
- Leverage > 3x on alt shorts, > 5x on BTC/ETH shorts during calibration
- Shorting funding <= -0.01% (squeeze)
- Shorting in BULL regime without override
- Shorting a coin on the conditional re-entry list (Rule 1)
- Shorting a coin on the conflict list (Rule 8)

**Cross-direction:**
- Any trade after Rule 3 limit hit (mechanical block)
- Any trade in a coin where opposite-direction position stopped out within 4h

---

## REVIEW CADENCE

- **Daily:** automatic via daily_pnl_tracker.py (Rule 3 enforcement)
- **Weekly (Sunday):** winners review, conflict spot-check, weekly P&L log
- **2026-06-21:** SHORT calibration review (Rule 7 graduation criteria)
- **2026-06-30:** RULE 1 conditional re-entry review (graduation criteria)

---

## FILE PATHS

- This file: `crypto-scanner/TRADING_RULES.md`
- Bybit transaction log: user uploads CSV periodically
- Daily PnL status: `outputs/daily_pnl/today.json` <- Rule 3 enforcement
- Limit-hit flag: `outputs/daily_pnl/LIMIT_HIT.txt` <- Rule 3 enforcement
- Conflict flags: `outputs/tracker/conflicts_LATEST.txt`
- Tracker DB: `outputs/tracker/signals.sqlite`
- Trade journal: `outputs/journal/trades.json`
- Scanner output (longs): `outputs/scanner-results/master_radar_*.json`
- Scanner output (shorts): `outputs/scanner-results/short_v3_LATEST.json`

---

## NOTE TO SONNET 4.6

The Rule 3 enforcement is mechanical, not advisory. Read `today.json`
BEFORE responding to any entry-related question. If you can't read the file
or it's stale (>30 min old), refuse the entry until the tracker is fresh.

The conditional re-entry on Rule 1 is NEW as of 2026-06-02. The four coins
(INIT/MERL/HAEDAL/LINK) are no longer banned outright but carry tighter
constraints. Don't conflate "off the banned list" with "free to size normally."

Shorts remain in calibration through 2026-06-21. Don't let Bruno size up
because the setup is clean.

This file should be re-read whenever Bruno opens a new session.
Surface RULE 6 (consult before entering) and RULE 3 (mechanical enforcement)
within the first 3 messages of any session that involves trading talk.
