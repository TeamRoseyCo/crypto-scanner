# YOUTUBE LONG-FORM + SHORTS — WEEKLY WORKFLOW

## WEEKLY SCHEDULE

| Day | Time  | What runs automatically | Your action needed |
|-----|-------|------------------------|--------------------|
| Mon | 06:30 | Scanner + shopping list | Take TradingView screenshots → run `longform_step2.bat` |
| Tue | 08:00 | Shorts pipeline | None |
| Wed | 06:30 | Scanner + shopping list | Take TradingView screenshots → run `longform_step2.bat` |
| Thu | 08:00 | Shorts pipeline + Auto Short Cut | None |
| Fri | 06:30 | Scanner + shopping list | Take TradingView screenshots → run `longform_step2.bat` |
| Sat | 08:00 | Auto Short Cut | None |
| Sun | —     | Nothing | Rest or prep |

---

## MON / WED / FRI — LONG-FORM VIDEO (your ~15 min involvement)

### Step 1 — Automatic (06:30)
Task Scheduler runs `longform_step1.bat` which:
1. Runs the scanner (`run_scan.py --account 96700`) → fresh data
2. Generates the script via LLM (Grok)
3. Creates a **SHOPPING_LIST.txt** with exact coins + chart specs

### Step 2 — You check the shopping list
Open this folder:
```
YOUTUBE - faceless channel\Images for Videos\longform_charts\{today's date}\
```
Open `SHOPPING_LIST.txt` — it tells you exactly which coins to screenshot.

### Step 3 — Take TradingView screenshots (~10 min)
For each coin listed:

**Screenshot 1 — `{COIN}_daily.png`**
- Pair: COIN/USDT on Bybit
- Timeframe: 1D
- Indicators: Bollinger Bands (20, 2), RSI (14) below, Volume bars
- Zoom: last 60-90 candles
- Theme: dark

**Screenshot 2 — `{COIN}_4h.png`**
- Timeframe: 4H
- Same indicators + draw entry/stop/TP lines if possible
- Zoom: last 50-80 candles

**Screenshot 3 (optional) — `{COIN}_signals.png`**
- Only if shopping list requests specific panels (OBV, CMF, funding rate)
- Adds credibility but not required

Save all screenshots to:
```
YOUTUBE - faceless channel\Images for Videos\longform_charts\{today's date}\
```

### Step 4 — Run Step 2 manually
Double-click `longform_step2.bat` (in `crypto-scanner` folder)
- It checks for your charts → uses real TradingView screenshots
- If no charts found → asks if you want to use synthetic frames instead
- Generates voice (ElevenLabs voice clone)
- Renders visual frames
- Composes final MP4
- Uploads to YouTube

**Done. ~15 min of your time total.**

---

## TUE / THU — SHORTS (fully automatic)
Task Scheduler runs `daily_video.bat` at 08:00:
1. Scanner runs
2. Script generated (60s narration)
3. Voice + visuals + video composed
4. Uploaded to YouTube
**Zero action from you.**

---

## THU / SAT — AUTO SHORT CUT (fully automatic)
Task Scheduler runs `auto_short_video.bat` at 08:00:
1. Finds latest long-form script
2. Extracts best coin segment
3. Repackages as a Short (under 60s)
4. Produces + uploads
**Zero action from you.**

---

## VIDEO TYPES PRODUCED

| Day | Type | Length | Content |
|-----|------|--------|---------|
| Mon | Scanner Report | 7-10 min | Weekly top 3 coins from 7-day aggregation |
| Tue | Short | ~60s | Daily scanner picks (existing pipeline) |
| Wed | Educational | 8-12 min | Deep dive on one concept (rotates: RSI divergence, funding rates, whale candles, etc.) |
| Thu | Short | ~60s | Daily scanner picks |
| Thu | Auto Short | ~45s | Best segment cut from Monday's long-form |
| Fri | Coin Breakdown | 7-10 min | Single highest-conviction coin deep dive |
| Sat | Auto Short | ~45s | Best segment cut from Wed/Fri long-form |

**Total weekly output: 3 long-form + 4 Shorts = 7 videos/week**

---

## FILES LOCATION

| File | Location |
|------|----------|
| `longform_step1.bat` | `crypto-scanner\` |
| `longform_step2.bat` | `crypto-scanner\` |
| `longform_pipeline.py` | `YOUTUBE - faceless channel\` |
| Shopping lists | `YOUTUBE - faceless channel\Images for Videos\longform_charts\{date}\` |
| TradingView screenshots | Same folder as shopping lists |
| Generated scripts | `YOUTUBE - faceless channel\Video Scripts\` |
| Voice-overs | `YOUTUBE - faceless channel\Voice-Overs\` |
| Final videos | `YOUTUBE - faceless channel\Videos\` |
| Logs | `crypto-scanner\outputs\logs\` |

---

## TASK SCHEDULER TASKS

| Task Name | Schedule | Batch File |
|-----------|----------|------------|
| Longform Step 1 - Scanner | Mon/Wed/Fri 06:30 | `longform_step1.bat` |
| Daily Crypto Video | Tue/Thu 08:00 | `daily_video.bat` |
| Auto Short Cut | Thu/Sat 08:00 | `auto_short_video.bat` |

---

## TROUBLESHOOTING

**Script too short (under 800 words)?**
→ Run again — LLM output varies. Or try `--provider gemini` for longer output.

**Shopping list shows wrong coins?**
→ Non-coin labels (MISSES, WINRATE, etc.) are filtered. If a new one appears, it will use synthetic frames — no harm done.

**No BTC price in script?**
→ Pipeline fetches from Binance as fallback. If Binance is down, regime context will say "unknown" but video still produces.

**ElevenLabs quota error?**
→ Check remaining chars at elevenlabs.io. Long-form uses ~6,000-8,000 chars per video. Budget: ~4 long-form videos/month on Starter ($6/mo = 30K chars).

**Want to skip charts and go fully automated?**
→ Just don't take screenshots. Pipeline uses synthetic frames automatically. Or set up `longform_video.bat` (the all-in-one version) in Task Scheduler instead.
