# RULE 3 ENFORCEMENT SETUP — STEP BY STEP

You're building a two-layer mechanical block on the -$1,940 daily loss limit.
Once set up, it works like this:

1. A scheduled Windows task runs `daily_pnl_tracker.py` every 5 minutes
2. The tracker fetches today's realized PnL from Bybit and writes a status file
3. When you ask Sonnet 4.6 (in PowerShell Claude) "should I take this trade?",
   Sonnet reads the status file FIRST. If limit is hit → refuses.

Total setup time: ~10 minutes.

---

## STEP 1 — Confirm Bybit credentials exist

Open `engine/scanner_v3/bybit_credentials.json`. It should look roughly like:

```json
{
  "api_key": "...",
  "api_secret": "..."
}
```

If it's missing or empty: you already have these env vars set from running
`run_sync.bat` earlier. The tracker will use them as fallback if the file
doesn't exist. No action needed.

**Important — API key permissions:**
The Bybit API key needs **read-only** position permissions. Specifically:
- Read: Positions ✓
- Read: Order History (helpful, not strictly required)
- Trade: NOT NEEDED (this script doesn't place trades)
- Withdraw: NOT NEEDED

If your key has trade or withdraw permissions, **make a new read-only key**
on https://www.bybit.com/app/user/api-management before continuing.
Read-only keys can't lose you money if they leak.

---

## STEP 2 — Drop the tracker into place

Copy `daily_pnl_tracker.py` to:
```
crypto-scanner\python-scanners\engine\scanner_v3\
```

(Same folder as the other v3 scanners.)

---

## STEP 3 — Run it once manually to verify

Open PowerShell, navigate to the scanner_v3 folder:

```powershell
cd "C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\crypto scanner\crypto-scanner\python-scanners\engine\scanner_v3"
& "C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\ankh\.venv\Scripts\python.exe" daily_pnl_tracker.py
```

Expected output (when no loss limit is breached):

```
2026-06-02 22:30:01 [INFO] Fetching closed PnL from 2026-06-02T00:00:00+00:00 → now
2026-06-02 22:30:02 [INFO]   Records returned: N
2026-06-02 22:30:02 [INFO]   Wrote today.json
2026-06-02 22:30:02 [INFO]   Realized PnL: $+XXX.XX   Headroom: $XXXX.XX   Status: OK
```

Then check the file was written:

```powershell
type ..\..\..\outputs\daily_pnl\today.json
```

You should see structured JSON with `realized_pnl_usd`, `status`, etc.

**If it fails:** the most likely cause is the API key. Make sure
`bybit_credentials.json` exists with `api_key` and `api_secret` fields, OR
that `$env:BYBIT_API_KEY` and `$env:BYBIT_API_SECRET` are set in your shell.

---

## STEP 4 — Schedule it to run every 5 minutes

Open Task Scheduler (`taskschd.msc` in Start menu). Create a new task:

**General tab:**
- Name: `Daily PnL Tracker`
- Description: `Reads today's Bybit realized PnL for Rule 3 enforcement`
- Run whether user is logged on or not: ✓
- Run with highest privileges: not needed

**Triggers tab — add one trigger:**
- Begin the task: On a schedule
- Daily, start at 00:01, recur every 1 day
- Repeat task every: 5 minutes
- For a duration of: 1 day
- Enabled: ✓

**Actions tab — add one action:**
- Action: Start a program
- Program/script:
  ```
  C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\ankh\.venv\Scripts\python.exe
  ```
- Add arguments:
  ```
  daily_pnl_tracker.py
  ```
- Start in:
  ```
  C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\crypto scanner\crypto-scanner\python-scanners\engine\scanner_v3
  ```

**Conditions tab:**
- Start the task only if the computer is on AC power: uncheck
  (you want this to run on battery too)

**Settings tab:**
- If the task fails, restart every: 1 minute, attempt 3 times
- If the running task does not end when requested: Stop the task

Save (it'll prompt for your Windows password).

---

## STEP 5 — Add the system prompt block to Sonnet 4.6

You said you use PowerShell Claude (Sonnet 4.6) as your trading co-pilot.

**Open the file:** `sonnet46_rule3_prompt_block.md`

**Copy the section** under "## DAILY LOSS LIMIT ENFORCEMENT" (everything
from that header to the end of the file).

**Paste it into Sonnet 4.6's system prompt or custom instructions**, at
or near the top, before any other trading guidance.

If you use Claude Code, the equivalent location is `CLAUDE.md` in the
project root — paste the block there.

If you're not sure where to put it: open the conversation where you
normally ask Sonnet about trades and start the next session with:

```
SYSTEM RULE — Rule 3 daily loss limit enforcement:

[paste the prompt block here]

This rule applies for the rest of our trading conversations.
```

---

## STEP 6 — Test the enforcement

The cleanest way to test without actually losing $1,940 is to manually
write a fake LIMIT_HIT status file:

```powershell
cd "C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\crypto scanner\crypto-scanner\python-scanners\outputs\daily_pnl"
'{"date_utc":"2026-06-02","checked_at":"2026-06-02T22:30:00+00:00","realized_pnl_usd":-2500.00,"daily_loss_limit":-1940.0,"headroom_to_limit":-560.00,"status":"LIMIT_HIT","closed_trade_count":3,"per_symbol":{},"trades":[]}' | Out-File today.json -Encoding utf8
```

Now ask Sonnet 4.6: "Should I open a long on HYPE?"

Expected response: a refusal mentioning the -$2,500 PnL and -$1,940 limit,
with the language from the prompt block.

After testing, reset the file:

```powershell
& "C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\ankh\.venv\Scripts\python.exe" daily_pnl_tracker.py --reset
& "C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\ankh\.venv\Scripts\python.exe" daily_pnl_tracker.py
```

That clears the fake LIMIT_HIT flag and re-fetches your real PnL.

---

## STEP 7 — Watch mode for live trading (optional)

When you're actively trading and want sub-5-minute updates, open a
PowerShell window and run:

```powershell
cd "C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\crypto scanner\crypto-scanner\python-scanners\engine\scanner_v3"
& "C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\ankh\.venv\Scripts\python.exe" daily_pnl_tracker.py --watch
```

This loops every 5 minutes and updates the status file in real time. Leave
the window open while you trade. Ctrl-C to stop.

---

## HOW IT WORKS IN PRACTICE — A FULL EXAMPLE

It's 14:00 UTC. You've had a rough morning: -$1,650 realized so far. The
Bybit task ran 8 minutes ago and the tracker wrote:

```json
{
  "realized_pnl_usd": -1650.00,
  "headroom_to_limit": -290.00,
  "status": "OK"
}
```

Wait — headroom is NEGATIVE? That means you're ALREADY past the limit by
the time it shows OK. Let me re-explain the math:

- daily_loss_limit = -$1,940
- realized_pnl = -$1,650
- headroom = realized_pnl - daily_loss_limit = -$1,650 - (-$1,940) = +$290

So headroom is +$290 (you have $290 left before hitting the limit). OK.

Now you ask Sonnet: "I see a great long on EDEN at 0.14, stop at 0.135.
That's a 3.6% stop. At 1% risk that's a $970 position. Approve?"

Sonnet reads today.json. Status is OK. Headroom is +$290. Math:
proposed position $970 with a 3.6% stop = $35 risk. $35 < $290 headroom. OK.

Sonnet's response:
> Headroom to daily limit: $290 remaining.
> Proposed position: $970 with 3.6% stop = $35 at risk. Within headroom.
> Checking other rules: EDEN is not banned. Position size within Rule 4.
> [Sonnet evaluates regime, scanner output, etc.]
> Verdict: APPROVE the entry.

You take the trade. EDEN moves against you. -$200. Tracker updates:
realized PnL is now -$1,850. Headroom: $90.

You ask Sonnet about another trade. Sonnet reads the file:
> Headroom: $90 remaining.
> Proposed position size at 1% risk would put you over the limit.
> Refuse. Wait for tomorrow.

EDEN drops further. -$350. Realized PnL: -$2,000. Status flips to LIMIT_HIT.
The flag file `LIMIT_HIT.txt` is written.

You ask Sonnet "should I add to the EDEN long, it's oversold":

> Daily loss limit of -$1,940 was hit today.
> Current realized PnL: -$2,000.
> Limit breached by: $60.
> Per TRADING_RULES.md Rule 3, no new entries until 00:00 UTC tomorrow.
> Reviewing tomorrow.

You argue: "But it's bouncing now, I need to recover."

> The Rule 3 enforcement is mechanical. I won't approve. Run the trade
> manually if you choose to — I won't be the one who said yes.

Tomorrow at 00:01 UTC the scheduled task runs again. Today's window is now
"yesterday." It fetches the new (empty so far) day's PnL, status returns
to OK, the LIMIT_HIT.txt flag gets deleted. Trading resumes normally.

---

## TROUBLESHOOTING

**Tracker fails with "Bybit API error: Sign verification failed"**
→ API key/secret mismatch. Regenerate the key on Bybit, update bybit_credentials.json.

**Tracker returns 0 records even though I traded today**
→ Make sure your trades are PERPS (UM linear), not spot. The script
   queries category=linear only. Spot trades aren't counted toward Rule 3.

**Sonnet 4.6 isn't reading the file**
→ Two possibilities:
   1. The prompt block wasn't placed in the system prompt. Check.
   2. Sonnet doesn't have file-read capability in your setup. If you use
      Claude Code, this works natively. If you use plain chat, you may
      need to paste the file contents at the start of each session.

**I want to manually mark today as LIMIT_HIT (e.g. emotional break, taking
the day off)**
→ Just create the flag file by hand:
   ```powershell
   "Manual day-off — limit hit by my choice." | Out-File "...outputs\daily_pnl\LIMIT_HIT.txt"
   ```
   Sonnet will refuse entries as long as that file exists.

---

## WHAT IT DOESN'T DO (BE HONEST WITH YOURSELF)

This system can only enforce Rule 3 when you ASK SONNET BEFORE A TRADE.

It does NOT:
- Place orders on Bybit
- Cancel orders on Bybit
- Reduce your account leverage
- Prevent you from typing into the Bybit UI directly

If you decide to bypass Sonnet and just hit "Market Buy" on Bybit when
the limit is hit, no code can stop that. The enforcement is
behavioral — you choose to consult Sonnet first; Sonnet then enforces.

The only real bypass-proof block would be reducing your Bybit account
balance below the size needed to lose $1,940. That's a real option if you
ever feel you can't trust yourself to consult Sonnet first — but it's a
heavier choice. Discuss with Claude (web interface) if you want to
consider it.
