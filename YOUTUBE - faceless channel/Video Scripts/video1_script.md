# VIDEO #1 SCRIPT — "I Built a 21-Indicator Crypto Scanner — Here's What's Inside"

**Tone:** Tutorial / teacher, warm, audience-aware
**Target length:** 12-15 minutes

**How to read this script:**
- Lines in `>` blockquotes = scripted narration. Read these close to verbatim.
  Line breaks inside them are breathing/pacing marks — pause at each break.
- Bullet points under each indicator = talk to these naturally in your own
  words. Do NOT read them verbatim, or you'll sound like you're reading a
  textbook. Glance at the bullet, look up, say it like you'd explain it to a
  friend. This is what keeps you sounding human.
- "Honesty beat" markers = the moments where you admit a limitation. Keep
  these. They build more trust than anything else in the video.

---

## STRUCTURE MAP

| Section | Target time | Status |
|---------|-------------|--------|
| Opening (hook + setup + structure) | 0:00 - 1:30 | DONE |
| Family 1: TREND (6 indicators) | 1:30 - 6:00 | DONE |
| Family 2: MOMENTUM (4 indicators) | 6:00 - 9:00 | DONE |
| Family 3: VOLUME & FLOW (3 indicators) | 9:00 - 11:00 | DONE |
| Family 4: VOLATILITY (4 indicators) | 11:00 - 13:00 | DONE |
| Glassnode regime segment | within trend transition | optional (video1_glassnode_segment.md) |
| Dormant 3 + orphan signals + close | 13:00 - 15:00 | DONE |

**Full script ready to record.** Read it through once out loud first to find
any phrasing that doesn't fit your natural voice, and edit freely.

---

## OPENING (0:00 - 1:30)

### [0:00 - 0:25] HOOK

> Most crypto traders look at one chart, see RSI crossing 30, and call it a signal.
>
> Then the trade goes against them and they wonder what happened.
>
> The honest answer? One indicator on its own isn't a signal. It's a question.
>
> And answering it properly takes more inputs than any single screen can give you.
>
> So I built a scanner that runs 21 of them at once, across 580 coins.

### [0:25 - 0:55] WHAT THIS VIDEO IS

> I'm Bruno, and this is the foundation video for the channel — what's actually inside the scanner I use every day.
>
> Today I'll walk you through every single indicator, why it's there, what it actually catches, and just as importantly, the ones I keep around but don't use yet — and why.
>
> If you're a trader who's tired of indicator soup and wants to see what a real, working multi-signal system looks like underneath the hood, you're in the right place.

### [0:55 - 1:30] STRUCTURE + TRANSITION

> Quick map of what's coming. I've grouped the 21 indicators into four families: trend, momentum, volume and money flow, and volatility.
>
> We'll go through each family, hit the indicators inside it, and I'll show you which ones the scanner relies on most heavily — because not all of them carry equal weight.
>
> At the end I'll show you the three indicators that are coded but inactive right now, and explain why they're still in the system instead of deleted.
>
> Let's start with trend, because everything else depends on getting trend right.

---

## FAMILY 1: TREND (1:30 - 6:00)

**Indicators covered:** EMA, SuperTrend, MACD, ADX, Hull MA, Ichimoku
**Key teaching point:** trend tells you DIRECTION and whether direction is
worth trusting. The trend module runs across 6 timeframes (1h/2h/4h/6h/12h/1d)
and produces a composite score that gates how aggressive the other scanners are.

### [1:30 - 1:55] FAMILY INTRO  *(scripted — read this)*

> Trend indicators answer two questions: which way is price going, and is that direction strong enough to trust?
>
> In my scanner, the trend module is special — it runs across six timeframes at once, from one hour all the way up to daily, and it produces a single composite score.
>
> That score then controls how aggressive every other part of the scanner is allowed to be.
>
> If the trend says we're in a bear regime, the scanner gets stricter about what it'll flag.
>
> So trend isn't just one input — it's the filter that sits over everything else.

### [1:55 - 2:35] EMA — Exponential Moving Average  *(talk to bullets)*

- The simplest, oldest trend tool — a moving average that weights recent prices more heavily than old ones
- Scanner uses three: 20, 50, and 200 period
- The relationship between them is what matters — price above all three, with the 20 above the 50 above the 200, is a clean uptrend stack
- Why it's foundational: every other trend indicator is a more sophisticated answer to the question EMA asks first — "where's the average, and which side of it are we on?"
- *Honesty beat:* EMA lags. It tells you what already happened. That's why it's a foundation, not a trigger — you build on it, you don't trade off it alone

> EMA - Exponential Moving Average is the simplest and oldest trend tool made available to anyone.

> My scanner, uses three of them, to assist signals: 20, 50 and 200 days period.

> The relantionship between all of them is where the secret sauce lies: Price has to be above all three, in a sequence of 200 above 50 above 200, to really give scanner a clean uptrend stack.

> You may ask why such a simple tool is foundational? 
> You might be right saying this, even considering pretty much every other trend indicator is a more sophisticated answer to the question EMA asks first - "Where's the average & > which sice of it are we on"?

> EMA lags!
> It tells you what already happened, that's why is foundational & NOT a trigger - we build on it, we don't trade off it alone.

### [2:35 - 3:25] SUPERTREND  *(talk to bullets)*

- This is the trend indicator the scanner leans on most heavily for direction
- Built on ATR (volatility) — it plots a line that flips above or below price
- Price above the SuperTrend line = bullish; below = bearish. Simple to read, which is the point
- The scanner checks it on all 6 timeframes. When you see "ST 6/6" in my output, SuperTrend is bullish on all six — strong multi-timeframe agreement
- Why it earns its weight: it adapts to volatility. Choppy market, the line sits further from price so you don't get whipsawed; trending market, it tightens up
- This is the closest thing the trend module has to a primary directional vote

> SuperTrend is the famous known indicator that tells anyone direction. That's exactly what I lean most heavily on this indicator for.

> This SuperTrend is built on ATR for volatility - it plots a line that flips above/below price.

> This provides a simple analise into this: Price above the SuperTrend line = Bullish, below = Bearish. Simple to read, which is the point.

> And one of the gems I execute with this scanner is it checks indicator on 6 timeframes. I will not disclose the full secret sauce but when you see
> "ST 6/6" in my output, means SuperTrend is bullish on all six timeframes, basically a strong multi-timeframe agreement.

> This is why it earns its weight: it adapts to volatility.
> In choppy market, the line sits further from price so you don't get whipsawed: In a trending market, it tightens up.

> This is the closest thing the trend module has to a primary directional vote.

> 

### [3:25 - 4:05] MACD — Moving Average Convergence Divergence  *(talk to bullets)*

- Measures the relationship between two moving averages — momentum within the trend
- Three parts: the MACD line, the signal line, and the histogram
- The scanner watches for the histogram turning and for line crossovers
- What it catches that EMA can't: acceleration. EMA tells you direction; MACD tells you whether that direction is gaining or losing steam
- *Honesty beat:* MACD is useful but it's the most over-relied-on indicator in retail trading. The scanner uses it as one vote among many, never a standalone trigger — and I'll come back to this when we talk about the signals I built but don't use

> The MACD indicator measures the relationship between two moving averages - this for momentum within a trend.

> This involves three parts: The MACD line; The signal line; and the histogram.

> The scanner specifically watches for the Histogram turning & for the line crossovers.

> It catches something that EMA can't: - Aceleration!
> EMA tells you direction: MACD tells you whether that direction is gaining or losing steam.

> MACD is useful but it's the most over-relied-on indicator in retail trading. The scanner uses it as one vote among many, never a standalone trigger — and I'll come back to this when we talk about the signals I built but don't use.

### [4:05 - 4:40] ADX — Average Directional Index  *(talk to bullets)*

- The "is this trend worth trusting?" indicator
- Doesn't tell you direction — tells you STRENGTH. Above ~25 usually means a real trend; below ~20 means chop, no clear trend
- This is the filter that keeps the scanner from treating sideways noise as a trend setup
- Pairs with everything: a bullish SuperTrend with ADX at 15 is weak; the same SuperTrend with ADX at 35 is a trend you can lean on
- Why it matters system-wide: most bad trades happen in low-ADX chop, where indicators flash signals that mean nothing. ADX is the bouncer at the door

> ADX - Average Directional Index is the "is this trend worth trusting?" indicator.

> This indicator doesn't tell you direction - it tells you STRENGTH.
> Above 25 means a real trend.
> Below 20 means chop, no clear trend.

> This is basically the filter that keeps the scanner from treating sideways noise as a trend setup.

> This pairs with everything: a bullish SuperTrend with ADX at 15 is weak.
> The same SuperTrend with ADX at 35 is a trend you can lean on.

> You might be thinking: Why it matters system-wide?
> Let me tell you based on experience: most bad trades happen in a low-ADX chop, where indicators flash signals that mean nothing.
> ADX is the bouncer at the door in the nightclubs.

### [4:40 - 5:15] HULL MOVING AVERAGE  *(talk to bullets — keep brief)*

- A moving average designed to reduce lag — the main weakness of EMA
- Uses a weighted calculation that responds faster to price while staying smoother than a raw price line
- The scanner uses it as a faster-reacting confirmation alongside the EMA stack
- Why both Hull and EMA: EMA is the slow reliable baseline; Hull is the faster read. When they agree, the trend signal is cleaner. When they diverge, it's an early warning the trend may be shifting

> in general, a moving average is designed to reduce lag - which is the main weakness of EMA.
> The HULL Moving Average uses a weighted calculation that responds faster to price while staying smoother than a raw price line.

> The scanner uses it as a faster-reacting confirmation alongside the EMA stack.

> You might ask: Why use both Hull and EMA?
> Well, EMA is the slow reliable baseline;
> HULL is the faster read.
> When they both agree, the trend signal is cleaner.
> When they diverge, it's an early warning the trend may be shifting.

### [5:15 - 6:00] ICHIMOKU CLOUD  *(talk to bullets)*

- The most complex trend indicator in the system, and the most complete
- It's a whole framework: support and resistance, direction, momentum, and trend strength all in one overlay — the "cloud," or kumo
- Price above the cloud = bullish structure; below = bearish; inside = no-man's-land, indecision
- Why include something this heavy: when price is cleanly above or below the cloud across timeframes, it's one of the highest-confidence trend reads available. The cloud also projects forward, showing where future support/resistance sits
- *Honesty beat:* Ichimoku is intimidating and most people either ignore it or misuse it. The scanner only uses the part that matters — price's relationship to the cloud — and ignores the rest of the noise people get lost in

> Now we reach the most complex trend indicator in the system, and the most complete, let me introduce you to Ichimoku Cloud.

> It's a whole framework: support and resistance, direction, momentum, and trend strength all in one overlay — the "cloud," or kumo

> scoring it pretty much looks like this: Price above the cloud = bullish structure; below = bearish; inside = no-man's-land, indecision

> Why include something this heavy you may ask: when price is cleanly above or below the cloud across timeframes, it's one of the highest-confidence trend reads available. 
> The cloud also projects forward, showing where future support/resistance sits

> Ichimoku is intimidating and most people either ignore it or misuse it. 
> The scanner only uses the part that matters — price's relationship to the cloud — and ignores the rest of the noise people get lost in.

### [TRANSITION OUT OF TREND]  *(scripted — read this)*

> So that's the trend family. Six indicators, but they're not equal — the scanner leans hardest on SuperTrend for direction and ADX for whether that direction is worth trusting. 
> Everything else confirms or refines.
>
> And here's the thing that ties it together: the trend module runs all of this across six timeframes and rolls it into one regime call — bull, sideways, or bear. That regime then decides how strict the rest of the scanner is.
>
> "So when my trend scanner says we're in a BEAR regime, that's just BTC's
> 7-day performance talking. But it's worth checking that read against
> something deeper — and Glassnode published one of the cleanest confirmations
> this week.
>
> Their on-chain data shows Bitcoin's short-term holder cost basis just fell
> below the True Market Mean. That structure has only appeared once since
> January 2022. It marks late-stage bear conditions — when newer buyers are
> accumulating below the market's average cost, and conviction starts to
> break down at the larger end.
>
> Realized losses are running at $1.35 billion a day. Of that, $770 million
> is long-term holders capitulating. Cycle-top buyers are tapping out.
>
> That's exactly the regime my scanner is built to be cautious in. When
> the on-chain data agrees with the trend score, I tighten conviction
> thresholds. Single-scanner signals don't cut it. I want confluence."
>
> Next up: momentum. 
> If trend is the direction, momentum is the speed — and it's where most of the early signals actually come from.

---

## FAMILY 2: MOMENTUM (6:00 - 9:00)

**Indicators covered:** RSI, Stochastic RSI, CCI, Aroon
**Key teaching point:** momentum is where the EARLY signals come from. Trend
tells you what's already happening; momentum tells you what's about to shift.
This is where the ignition scanner does most of its work.

### [6:00 - 6:20] FAMILY INTRO  *(scripted — read this)*

> If trend is direction, momentum is speed — and more importantly, changes in speed. This is where early signals live.
>
> By the time the trend indicators confirm a move, the momentum indicators have usually been hinting at it for a while.
>
> My ignition scanner — the one built to catch setups early — leans heavily on this family.
>
> The catch is that momentum is noisier than trend, so the whole game here is separating real momentum shifts from random wiggle.

### [6:20 - 7:20] RSI — Relative Strength Index  *(talk to bullets)*

- The most famous momentum indicator, and the most misused
- The textbook says "above 70 overbought, below 30 oversold" — and that's exactly where most traders go wrong
- *Honesty beat:* RSI can sit above 70 for weeks in a strong uptrend. Selling every time it hits 70 is how you exit winners early and lose money
- What the scanner actually does with it — FOUR different RSI signals, not one:
  - `rsi_in_zone` — RSI in a healthy range, not stretched
  - `rsi_reset` — RSI pulled back from overbought and resetting, often a continuation setup
  - `rsi_divergence` — price makes a lower low but RSI makes a higher low. This is the big one — momentum improving while price still falls. Often precedes a reversal by days
  - `rsi_overbought_reset` — the bearish mirror, used by the short scanner
- The teaching point: RSI isn't a buy/sell line. It's a momentum story, and the divergence version is where the real edge is. I've got a whole separate video planned just on divergence

> this is The most famous momentum indicator, and the most misused.

> The textbook says "above 70 overbought, below 30 oversold" — and that's exactly where most traders go wrong.

> this is a known fact, The textbook says "above 70 overbought, below 30 oversold" — and that's exactly where most traders go wrong.

> What the scanner actually does with it — FOUR different RSI signals, not one:
  > `rsi_in_zone` — RSI in a healthy range, not stretched
  > `rsi_reset` — RSI pulled back from overbought and resetting, often a continuation setup
  > `rsi_divergence` — price makes a lower low but RSI makes a higher low. This is the big one — momentum improving while price still falls. Often precedes a reversal by days
  > `rsi_overbought_reset` — the bearish mirror, used by the short scanner

> The main teaching point here is this: RSI isn't a buy/sell line. 
> It's a momentum story, and the divergence version is where the real edge is. 
> I've got a whole separate video planned just on divergence.

### [7:20 - 8:00] STOCHASTIC RSI  *(talk to bullets — keep tight)*

- RSI applied to RSI — it measures where RSI itself sits within its own recent range. More sensitive, faster, noisier
- Why have both: regular RSI is the steady read; Stoch RSI is the early twitch. Stoch RSI fires first, RSI confirms
- The trade-off is noise — it gives more signals, and more of them are false. So the scanner uses it as a supporting vote, never a standalone
- *Honesty beat:* on its own, Stoch RSI will whipsaw you to death. It only earns its place inside a confluence system where other signals filter its noise

> 

### [8:00 - 8:35] CCI — Commodity Channel Index  *(talk to bullets)*

- Despite the name, nothing to do with commodities specifically — works on anything
- Measures how far price has deviated from its statistical average
- Readings beyond +100 or -100 flag unusually strong moves — either the start of a real trend or an overextension about to snap back
- How the scanner uses it: confirmation of momentum strength alongside RSI and the trend family. When CCI agrees with RSI, the momentum read is more reliable
- A secondary confirmation, not a headline. It earns its spot by occasionally catching extensions the others miss

### [8:35 - 9:00] AROON  *(talk to bullets)*

- A different angle on momentum — it measures TIME, not price
- Specifically: how recently did price make a new high vs a new low, within a lookback window
- Aroon-Up high and Aroon-Down low means new highs are happening frequently — a young, healthy uptrend
- Why it's useful: it catches the BEGINNING of trends, when price has just started making fresh highs but the moving averages haven't caught up yet
- Pairs beautifully with the trend family — Aroon flags the early trend, ADX confirms when it's strong enough to trust

### [TRANSITION OUT OF MOMENTUM]  *(scripted — read this)*

> So momentum gives us the early read — RSI divergence especially is one of the most valuable early signals in the whole system.
>
> But momentum can lie. Price can twitch upward on no real buying.
>
> So the next question the scanner asks is the most important one: is there actual money behind this move? And that's what volume tells us.

---

## FAMILY 3: VOLUME & FLOW (9:00 - 11:00)

**Indicators covered:** OBV, CMF, MFI
**Key teaching point:** volume is the footprint money leaves. This family
catches accumulation and distribution price alone doesn't show. This is your
STRONGEST differentiator — most retail TA ignores volume flow entirely.

### [9:00 - 9:20] FAMILY INTRO  *(scripted — read this)*

> Here's where my scanner does something most retail traders skip entirely.
>
> Price tells you what happened. Volume tells you who was behind it — and whether it was real.
>
> A price move on thin volume is a whisper; the same move on heavy volume is a statement.
>
> This family is built to spot the footprints big money leaves before a move becomes obvious.
>
> If I had to point to the part of the scanner that gives the most genuine edge, it's this one.

### [9:20 - 10:10] OBV — On-Balance Volume  *(talk to bullets)*

- The cleanest volume tool. Adds volume on up-days, subtracts it on down-days, tracks the running total
- The magic is in divergence — same idea as RSI divergence, but with money instead of momentum
- The scanner has FIVE OBV-based signals — more than any other indicator:
  - `obv_stealth_accum` — OBV rising while price stays flat. Translation: someone is accumulating quietly without moving the price. The "stealth" is the point
  - `obv_divergence` — price falling but OBV holding or rising. Buyers absorbing the selling
  - plus the bearish mirrors used by the short scanner (`obv_bear_distribution`, `obv_bear_divergence`)
- Why it matters: stealth accumulation is the closest thing TA has to seeing institutional buying before it shows up in price. By the time price moves, OBV often moved days earlier
- This is genuinely one of the highest-value signals in the system — say so

### [10:10 - 10:45] CMF — Chaikin Money Flow  *(talk to bullets)*

- Takes OBV's idea further — it weights volume by WHERE in the candle's range price closed
- A candle closing near its high on big volume = strong buying pressure. Near its low = selling pressure
- CMF turns this into a single oscillator above or below zero. Above zero = money flowing in; below = flowing out
- Scanner uses two signals: `cmf_positive` (accumulation, ignition) and `cmf_negative` (distribution, short scanner)
- How it pairs with OBV: OBV tells you the direction of volume; CMF tells you the conviction within each candle. Together, a much stronger read than either alone
- *Honesty beat:* CMF can be choppy on low-volume coins. The scanner cross-checks it against actual volume levels so it doesn't get fooled by thin books

### [10:45 - 11:00] MFI — Money Flow Index  *(talk to bullets — keep brief)*

- Think of it as "volume-weighted RSI" — RSI that accounts for volume, not just price
- Same 0-100 scale, same overbought/oversold concept, but a move only counts if there's volume behind it
- Why include it alongside RSI: it filters out low-conviction moves RSI would flag. A price spike on no volume won't move MFI much
- The volume-aware confirmation of the momentum family, bridging momentum and flow

### [TRANSITION OUT OF VOLUME]  *(scripted — read this)*

> So now we've got direction from trend, speed from momentum, and conviction from volume.
>
> There's one dimension left, and it's the one that tells you timing — when a move is actually likely to happen. That's volatility.

---

## FAMILY 4: VOLATILITY (11:00 - 13:00)

**Indicators covered:** ATR, Bollinger Bands, Keltner Channels, Parabolic SAR
**Key teaching point:** volatility tells you WHEN, not which way. The headline
is the BB+Keltner squeeze (TTM setup) — compression precedes expansion. This
family also does the practical work of setting stop distances.

### [11:00 - 11:20] FAMILY INTRO  *(scripted — read this)*

> The last family doesn't tell you direction at all. It tells you timing — and it does a lot of the practical work behind the scenes, like setting where stops go.
>
> The big idea here is simple but powerful: markets breathe. They compress, then they expand.
>
> If you can spot the compression, you can be positioned before the expansion. That's the headline setup in this whole section.

### [11:20 - 11:55] ATR — Average True Range  *(talk to bullets)*

- The workhorse of the volatility family. Measures how much an asset typically moves in a given period — pure volatility, no direction
- Where it does quiet, essential work: setting stop distances. A 5% stop on a low-volatility coin is huge; on a high-volatility coin it's nothing. ATR normalizes this
- The scanner uses ATR to size stops and targets proportionally to each coin's actual volatility — so the same risk rule works across calm and wild coins
- Also feeds two signals: `whale_candle` (a candle far larger than recent ATR = someone with size moved the market) and `atr_expanding` (volatility ramping up, often precedes a big move)
- Teaching point: ATR is the unsexy indicator that makes everything else practical. Without it, your risk management is guesswork

### [11:55 - 12:35] BOLLINGER BANDS + KELTNER CHANNELS — the squeeze  *(talk to bullets — headline, give it airtime)*

- Bollinger Bands: a moving average with bands at standard deviations above and below. They widen when volatility rises, narrow when it falls
- Keltner Channels: similar idea, but built on ATR instead of standard deviation
- The magic is overlaying them: when Bollinger Bands contract INSIDE the Keltner Channels, that's "the squeeze" — volatility compressed to an extreme
- This is the TTM Squeeze setup, one of the most reliable timing signals in trading. Compression doesn't tell you direction — but it tells you a big move is coming, and soon
- Scanner signal: `bb_squeeze` — and notice it fires across ignition, short, AND trend. One of the few signals all three scanners care about, because timing matters regardless of direction
- The play: spot the squeeze, then use the trend and momentum families to call which way the expansion breaks
- This is your second genuinely strong differentiator after volume — give it the airtime

### [12:35 - 13:00] PARABOLIC SAR  *(talk to bullets)*

- "Stop And Reverse" — plots dots above or below price that flip when the trend changes
- Primarily a trailing-stop and trend-following tool — the dots give you a visual trail to ride a trend and a clear flip point when it ends
- How the scanner uses it: confirmation of trend direction, and a reference for where momentum has structurally shifted
- *Honesty beat:* SAR is great in trending markets and terrible in chop — it flips back and forth and stops you out repeatedly. The scanner only weights it when ADX confirms a real trend is present. On its own it's a chop machine

### [TRANSITION TO CLOSE]  *(scripted — read this)*

> And that's all 21 — but I told you at the start there were some I keep around and don't actually use. Let's talk about those, because it says something about how you build a system that lasts.

---

## CLOSING: DORMANT + ORPHANS + CTA (13:00 - 15:00)

**Key teaching point:** a real, living system has spare parts. Being honest
about what you DON'T use builds more trust than pretending everything is
perfectly optimized. This separates you from every "my scanner is perfect,
buy my signals" channel.

### [13:00 - 13:40] THE THREE DORMANT INDICATORS  *(talk to bullets)*

- I told you at the start I'd be honest about this. Of the 21 indicators, three are coded, tested, and sitting there unused: DEMA, TEMA, and WMA — three variations of moving averages
- Why they're not active: they're refinements of EMA and Hull MA that, in testing, didn't add enough beyond what those two already give me
- Why I don't delete them: the math is already written and tested. If I ever want to build a new signal that needs them, it's a five-minute job instead of starting from scratch. They're capability sitting in reserve
- *The honest framing:* this is what a real codebase looks like. Anyone who shows you a "perfectly optimized" system with zero spare parts either isn't being straight with you, or hasn't built much

### [13:40 - 14:20] THE ORPHAN SIGNALS  *(talk to bullets)*

- There's a deeper layer. Beyond the dormant indicators, I've got eight signal functions written that aren't currently wired into any scanner
- Things like ADX trend strength, MACD crossover, SuperTrend direction, relative-strength-versus-Bitcoin
- Some of these the trend scanner gets a different way — it calls the underlying math directly instead of through the signal wrapper
- Others are genuinely staged for future use — built ahead of need
- *The real lesson of the whole video:* you don't build a trading system perfect on day one. You build capability, test what works, wire in what earns its place, and leave the rest in reserve. The system is alive. It changes as the data teaches me what actually works
- This is also why I built a signal tracker that records every flagged setup and checks the outcome later — so the decision about what to wire in is made on data, not on which indicators sound impressive

### [14:20 - 15:00] CLOSE + CTA  *(scripted — read this)*

> So that's the whole thing — 21 indicators across four families, plus the spare parts I keep in reserve.
>
> The point I want to leave you with is the one I opened with: no single indicator is a signal. The edge isn't in any one of these tools. It's in what happens when several of them agree at the same time, on the same coin, across multiple timeframes.
>
> That agreement is what I call confluence, and it's the next thing I want to break down — how the scanner scores it, and why I don't act on anything below a certain threshold.
>
> If you found this useful, the best thing you can do is tell me which indicator you want me to go deeper on. Drop it in the comments — RSI divergence, the volume signals, the squeeze setup, whatever you're most curious about. That's what I'll make next.
>
> Thanks for watching. I'll see you in the next one.

**CTA mechanics after recording:**
- Pinned comment: "Which indicator should I deep-dive on first? Drop it below — RSI divergence, OBV stealth accumulation, the squeeze, or something else."
- End screen: just the subscribe prompt (no other videos worth linking yet).
- Next-video tease in description: "Next up — Confluence Scoring: why I don't trade anything below 7."

---

## RECORDING CHECKLIST

- [ ] Read hook + transitions from script (tight, word-for-word)
- [ ] Talk to bullet points for indicators (loose, natural, look up from the page)
- [ ] Keep every "honesty beat" — they build the most trust
- [ ] Have indicators.py open on screen to show code
- [ ] Have 2-3 charts ready: a SuperTrend flip, an RSI divergence, a BB squeeze
- [ ] Stand up while recording, slight smile — both audibly improve your voice
- [ ] Don't restart the whole video on a flubbed line — pause, redo that sentence, fix in edit
- [ ] Target 12-15 min final cut. If raw is 20+, cut hard
- [ ] Use YOUR voice, not ElevenLabs

## PUBLISH CHECKLIST

- [ ] Title: "I Built a 21-Indicator Crypto Scanner — Here's What's Inside"
- [ ] Thumbnail: indicator_audit.py output screenshot + "21 INDICATORS" overlay
- [ ] Description: see YOUTUBE_STRATEGY.md template + Glassnode attribution if used
- [ ] Pinned comment (see CTA mechanics above)
- [ ] Publish Sunday evening (best discovery window)
- [ ] Do NOT post the X thread — just publish
