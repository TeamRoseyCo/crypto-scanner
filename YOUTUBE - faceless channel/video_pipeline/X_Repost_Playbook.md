# Alpha Signals — X Repost Playbook

**Goal:** Turn each daily pipeline upload into a high-engagement X post in under 15 minutes of active work. Consistency > intensity. Stick to this for 30 days before judging results.

---

## ⏱ Daily timing

| Time (UTC) | What happens |
|------------|--------------|
| 01:00 | Task Scheduler runs pipeline → YouTube auto-upload + owner comment |
| 09:00 (your morning) | Check pipeline succeeded (PowerShell or YouTube Studio) |
| 13:00–15:00 | Post on X — this window is the sweet spot for US morning + EU afternoon traffic |
| 13:30–14:00 | Active engagement window (30 minutes of replies on others' posts) |

**Why 13:00–15:00 UTC:** US East Coast traders wake up, US West Coast pre-market, and Europe is still active. Avoid posting before 12:00 UTC (US asleep) or after 18:00 UTC (Europe winding down).

**Weekends:** post anyway. Crypto doesn't sleep. Saturday posts in particular have less competition.

---

## 🔁 The 4 daily steps

### Step 1 — Verify the pipeline succeeded (1 min)

Open PowerShell, run:

```powershell
Get-Content "C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\crypto scanner\crypto-scanner\outputs\logs\daily_video_LATEST.log" -Tail 15
```

✅ Looking for: `PIPELINE COMPLETED SUCCESSFULLY` and `Uploaded: https://youtu.be/...`

If it failed, fix the pipeline first — no point posting an old video.

### Step 2 — Grab today's data (2 min)

You need 4 things from today's video to write the post. Get them from:
- **YouTube Studio** → today's video → note the **title** and **top 2-3 coins**
- **Scanner output**: `outputs/scanner-results/master_radar_LATEST.txt` → top of file shows regime, BTC, confluence count

Specifically grab:
1. **The top coin** (the one in the video title)
2. **The setup type** (confluence / strong setup / single scanner)
3. **2-3 specific signals** that fired on that coin (RSI div, BB squeeze, funding negative, etc.)
4. **Market regime + BTC price** (e.g. "SIDEWAYS · BTC $81k")

### Step 3 — Post on X (3 min)

Use one of these templates. Rotate weekly so your feed doesn't look templated.

#### Template A — Data-forward (default)

```
Scanner just flagged $[COIN] at [X.X] confluence.

[N] independent signals firing on the same coin:
• [Signal 1]
• [Signal 2]
• [Signal 3]

Multi-timeframe trend score: [N], ST [N]/6.

Full breakdown 👇

#[COIN] #crypto #altcoins
```

#### Template B — Curiosity hook (use sparingly, only if claim is true)

```
$[COIN] just printed something my scanner only sees a few times a month:

[N] scanners agreeing. Confluence score [X.X].

Last time this exact pattern hit, the coin moved before most traders noticed.

Here's what fired 👇

#[COIN] #cryptotrading
```

#### Template C — Process / educational (1× per week)

```
How my scanner found $[COIN] today.

Three independent checks ran:
1️⃣ Ignition — [signal that fired]
2️⃣ Perp — [signal that fired]  
3️⃣ Trend — [score]

When ≥2 agree on the same coin = confluence.

Today $[COIN] hit all three. Breakdown 👇

#[COIN] #crypto
```

#### Template D — Market context (use on quiet scanner days)

```
Market check: [REGIME] regime, BTC $[PRICE].

Scanner output today:
• [N] convergence setups
• [N] strong setups  
• Top conviction: $[COIN1], $[COIN2], $[COIN3]

When BTC chops, alts whisper before they shout. Here's what's whispering 👇

#crypto #altcoins #bitcoin
```

**Post mechanics:**
1. Type the post text
2. Attach the video **natively** (upload the .mp4 from `YOUTUBE - faceless channel/Videos/`) — NEVER link to YouTube in the main post
3. Post
4. **Reply to your own post** immediately with: `Daily scans drop here every morning → [your YouTube link]`

### Step 4 — Engage for 30 minutes (10 min minimum, 30 min ideal)

This is the single highest-leverage activity for a new account. Cold posts die in zero-reach hell. You break out by being visible in *other* people's threads.

**The workflow:**

1. In X search bar, type `$[TOP_COIN_TODAY]` (e.g. `$INJ`)
2. Filter results → **Latest** (top right of search results)
3. Find 5-8 posts from accounts with **1k–50k followers** that have **3+ likes**
4. Leave **substantive** replies — see examples below

**What makes a good reply:**

✅ Adds a data point: "Funding flipped negative on perps too — sometimes that's the tell"  
✅ Asks a real question: "What timeframe are you watching? My scanner's seeing this on 1D"  
✅ Confirms with nuance: "OBV is showing the same stealth accumulation here — though watch the 0.85 resistance"  
✅ Politely disagrees with reasoning: "Interesting take. The 1H RSI looks overcooked though — I'd wait for a reset"

**What to AVOID:**

❌ "Great post!" / "Nice analysis!" (low-value noise)  
❌ "Check out my analysis here [link]" (instant self-promo flag)  
❌ Dropping your video link in someone's thread  
❌ Replying to massive accounts (>500k) — your reply drowns in 1000 others  
❌ Posting the same reply on multiple threads (X detects this)

**The follower math:** if 8 thoughtful replies → 3 profile clicks → 1 follower per day, that's ~30 followers/month from engagement alone. Compounded with viral post chances, that gets you to 500+ in 3-6 months.

---

## 📅 Weekly rhythm (Mon–Sun)

| Day | Template focus | Bonus action |
|-----|----------------|--------------|
| Mon | Template A (data-forward) | Reply to weekend recap posts from big accounts |
| Tue | Template A | — |
| Wed | Template B (curiosity hook) | Quote-tweet a top trader with your own take |
| Thu | Template A | — |
| Fri | Template C (process/educational) | Write a longer 3-tweet thread explaining a signal type |
| Sat | Template D (market context) | Engage in weekend macro discussions |
| Sun | Template A | "Week ahead" framing — what scanner is watching for Monday open |

---

## 📊 What to track (weekly check, Sundays)

Open X Analytics every Sunday. Note:

- **Follower count change** (target: +20-50/week after week 4)
- **Best-performing post** of the week — what made it work? Hook? Coin? Timing?
- **Worst-performing post** — what went wrong? Wrong time? Boring opener? No cashtag?
- **Profile clicks per post** (the real engagement metric — likes are vanity)

If after 4 weeks engagement is flat: change something deliberately. Don't just keep doing the same thing harder. Try:
- Different posting time (test 20:00-22:00 UTC for one week)
- Different hook style (questions instead of data)
- Adding a chart screenshot alongside the video
- Engaging with a different sub-community (DeFi vs perps vs meme coins)

---

## 🚫 Things NOT to do

- **Don't buy followers.** Crypto-Twitter sniffs out fake-engagement accounts in days. Reputation is your only asset.
- **Don't post >2x per day.** Each post cannibalizes the reach of the previous one. Quality > quantity.
- **Don't argue with trolls.** Mute, move on. Engagement metrics from negative replies still count and you'll get pushed to more antagonistic audiences.
- **Don't shill specific entry/exit prices.** Stay analytical: "scanner flagged this setup" not "BUY NOW at $X target $Y." The second one attracts liability and worse audiences.
- **Don't make rarity claims you can't back up.** "Only happens 2x a year" needs to be actually true.
- **Don't post on Days 1-7 of a new feature you haven't validated.** Test on your own account first.

---

## 🛟 Quick reference — common signals to mention

When writing posts, these are the signal names that resonate with crypto-Twitter (vs. inside-baseball scanner terms):

| Your scanner says | Tweet it as |
|-------------------|-------------|
| `obv_stealth_accum` | "OBV stealth accumulation" or "quiet accumulation" |
| `bb_squeeze` | "Bollinger Band squeeze" or "BB squeeze" |
| `whale_candle` | "Whale candle" |
| `rsi_divergence` (hidden_bullish) | "Hidden bullish RSI divergence" |
| `funding_negative` | "Funding flipped negative" |
| `vol_oi_surge` | "Volume + OI spiking together" |
| `cmf_positive` | "Chaikin money flow positive" |
| `decoupling 1h` | "Decoupling from BTC on 1H" |

---

## 🎯 The 30-day goal

By day 30, you should have:
- 30 daily posts published (no skipped days)
- 100+ thoughtful replies to other accounts
- 50-150 followers
- 1-2 posts that broke 1000 impressions
- A clear sense of which template + timing works best for *your* audience

If you hit those, X is paying off and you can layer in TikTok. If not, diagnose specifically what's not working before scaling to other platforms.

---

*Last updated: 2026-05-14*
