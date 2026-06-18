"""
longform_pipeline.py — Automated long-form video pipeline (7-12 min).

Plugs into the existing video_pipeline modules (scriptgen, visuals,
voiceover, compose, upload). Produces 3 long-form videos/week + 4 Shorts
auto-cut from the long-form content (one Short every non-long-form day).

SCHEDULE:
  Monday    → Scanner Report  (7-10 min)  — aggregates past 7 days
  Wednesday → Educational     (8-12 min)  — deep dive on one concept
  Friday    → Coin Breakdown  (7-10 min)  — highest-conviction coin today

SHORTS (auto-cut, daily on non-long-form days):
  Sunday    → Best segment from Friday's video
  Tuesday   → Best segment from Monday's video
  Thursday  → Best segment from Wednesday's video
  Saturday  → Best segment from Friday's video

USAGE:
  # Auto-detect day and run the right pipeline:
  python longform_pipeline.py

  # Force a specific type:
  python longform_pipeline.py --type scanner_report
  python longform_pipeline.py --type educational
  python longform_pipeline.py --type coin_breakdown

  # Preview script without producing video:
  python longform_pipeline.py --preview

  # Skip upload:
  python longform_pipeline.py --no-upload

  # Auto-cut a Short from the latest long-form:
  python longform_pipeline.py --type auto_short

REQUIRES:
  Same env vars as daily pipeline:
    - XAI_API_KEY or GEMINI_API_KEY (free) or ANTHROPIC_API_KEY (paid)
    - ELEVEN_API_KEY or ELEVENLABS_API_KEY
    - YOUTUBE_CLIENT_SECRET (for upload)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# PATHS — match your existing folder structure
# ─────────────────────────────────────────────────────────────────────────────

HERE         = Path(__file__).resolve().parent
_PIPELINE    = HERE / "video_pipeline"
_SCANNER_OUT = HERE.parent / "outputs" / "scanner-results"
_SCRIPT_OUT  = HERE / "Video Scripts"
_VOICE_OUT   = HERE / "Voice-Overs"
_FRAMES_OUT  = HERE / "Images for Videos"
_VIDEO_OUT   = HERE / "Videos"
_BGM_DIR     = HERE / "Content"
_LONGFORM_DATA = HERE / "longform_data"
_CHARTS_DIR    = _FRAMES_OUT / "longform_charts"

# Ensure directories exist
for d in [_SCRIPT_OUT, _VOICE_OUT, _FRAMES_OUT, _VIDEO_OUT, _LONGFORM_DATA, _CHARTS_DIR]:
    d.mkdir(exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("longform_pipeline")

# ─────────────────────────────────────────────────────────────────────────────
# IMPORT PIPELINE MODULES
# ─────────────────────────────────────────────────────────────────────────────

sys.path.insert(0, str(_PIPELINE))
sys.path.insert(0, str(HERE))

from video_pipeline.scriptgen import generate_script, _detect_provider, _call_llm_with_retry
from video_pipeline.visuals import render_all_frames
from video_pipeline.voiceover import generate_voiceover
from video_pipeline.compose import compose_video
from video_pipeline.upload import upload_to_youtube, get_youtube_credentials
from video_pipeline.ingest import load_scanner_data, build_market_summary

# Weekly pipeline for aggregation
try:
    from video_pipeline.weekly_pipeline import (
        aggregate_weekly_picks,
        select_top_three_weekly,
        build_weekly_summary,
        _flatten_trade_plan,
    )
    HAS_WEEKLY = True
except ImportError:
    HAS_WEEKLY = False
    log.warning("  weekly_pipeline not importable — scanner_report mode disabled")

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

SIZE_LANDSCAPE = (1920, 1080)
DEFAULT_PROVIDER = os.environ.get("SCRIPT_PROVIDER", "grok")

# Day-of-week to video type mapping (0=Monday)
SCHEDULE = {
    0: "scanner_report",    # Monday    — long-form
    2: "educational",       # Wednesday — long-form
    4: "coin_breakdown",    # Friday    — long-form
    6: "auto_short",        # Sunday    — Short from Friday
    1: "auto_short",        # Tuesday   — Short from Monday
    3: "auto_short",        # Thursday  — Short from Wednesday
    5: "auto_short",        # Saturday  — Short from Friday
}

# ─────────────────────────────────────────────────────────────────────────────
# PLAYLIST IDs — auto-assign videos to the right playlist on upload
# ─────────────────────────────────────────────────────────────────────────────

PLAYLIST_IDS = {
    "scanner_report": "PLB1RuY1oe4b9RzVhYwRIl9MQKf8-VjZ-q",   # Weekly Scanner Reports
    "educational":    "PLB1RuY1oe4b_vq8u47AKJZZVfjVHk_BTF",   # Trading Signals Explained
    "coin_breakdown": "PLB1RuY1oe4b-dDrNWqAFmaWgo4smqg-kI",   # Scanner Alert Breakdowns
}

# ─────────────────────────────────────────────────────────────────────────────
# PINNED COMMENT TEMPLATES — auto-posted and pinned on every upload
# ─────────────────────────────────────────────────────────────────────────────

PINNED_COMMENTS = {
    "scanner_report": (
        "📊 This scanner runs on 1000+ coins daily. "
        "Want the full system? Watch the complete trading system breakdown "
        "in the playlist linked above.\n\n"
        "🔗 Trade on Bybit: https://shorturl.at/L3TkD\n"
        "🔗 TradingView charts: https://shorturl.at/ZAxY6\n\n"
        "👇 Drop a coin you want me to scan next week."
    ),
    "educational": (
        "📚 This is part of my Trading Signals Explained series. "
        "Every Wednesday I break down one concept the scanner uses.\n\n"
        "🔗 Trade on Bybit: https://shorturl.at/L3TkD\n"
        "🔗 TradingView charts: https://shorturl.at/ZAxY6\n\n"
        "👇 What concept should I explain next?"
    ),
    "coin_breakdown": (
        "🔍 I'll track this coin in next Monday's scanner report "
        "and show you what happened — win or loss.\n\n"
        "🔗 Trade on Bybit: https://shorturl.at/L3TkD\n"
        "🔗 TradingView charts: https://shorturl.at/ZAxY6\n\n"
        "👇 Should I do a deep dive on another coin? Drop the ticker below."
    ),
    "auto_short": (
        "📊 Full breakdown with entry, stop, and 3 take profit levels "
        "in the long-form video on this channel.\n\n"
        "🔗 Bybit: https://shorturl.at/L3TkD\n\n"
        "👇 Subscribe for scanner reports every Monday."
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# YOUTUBE HELPERS — playlist assignment + pinned comments
# ─────────────────────────────────────────────────────────────────────────────

def _add_to_playlist(credentials, video_id: str, playlist_id: str):
    """Add a video to a YouTube playlist."""
    try:
        from googleapiclient.discovery import build
    except ImportError:
        log.warning("  Could not import googleapiclient — skipping playlist")
        return

    try:
        youtube = build("youtube", "v3", credentials=credentials)
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {
                        "kind": "youtube#video",
                        "videoId": video_id,
                    },
                }
            },
        ).execute()
        log.info(f"  Added to playlist: {playlist_id}")
    except Exception as e:
        log.warning(f"  Playlist assignment failed: {e}")


def _pin_comment(credentials, video_id: str, comment_text: str):
    """Post a comment on a video and pin it."""
    try:
        from googleapiclient.discovery import build
    except ImportError:
        log.warning("  Could not import googleapiclient — skipping comment")
        return

    try:
        youtube = build("youtube", "v3", credentials=credentials)

        # Post the comment
        result = youtube.commentThreads().insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId": video_id,
                    "topLevelComment": {
                        "snippet": {
                            "textOriginal": comment_text,
                        }
                    },
                }
            },
        ).execute()

        comment_id = result["snippet"]["topLevelComment"]["id"]
        log.info(f"  Comment posted: {comment_id}")

        # Pin it (set as channel owner's pinned comment)
        # Note: YouTube API doesn't have a direct "pin" endpoint.
        # The comment will appear as the channel owner's comment,
        # which you can manually pin later, or it stays as top comment
        # since it's from the channel owner.
        log.info(f"  Comment posted as channel owner (pin manually in Studio if needed)")

    except Exception as e:
        log.warning(f"  Comment posting failed: {e}")

# Educational topics — rotates through these
EDUCATIONAL_TOPICS = [
    {
        "topic": "RSI Divergence",
        "title_template": "RSI Divergence Explained — The Signal I Check Before Every Trade",
        "focus": "bullish and bearish RSI divergence, how it shows momentum exhaustion, real examples from scanner",
    },
    {
        "topic": "Funding Rates",
        "title_template": "Funding Rates Explained — The Free Edge Most Traders Miss",
        "focus": "perpetual futures funding mechanism, how negative funding creates squeeze setups, scanner integration",
    },
    {
        "topic": "Whale Candles",
        "title_template": "Whale Candles Explained — How I Spot Smart Money Before It Moves",
        "focus": "volume spikes revealing institutional accumulation, distinguishing accumulation from liquidation, scanner detection",
    },
    {
        "topic": "Conviction Scoring",
        "title_template": "How I Score Every Trade Before Entering — Conviction System Explained",
        "focus": "multi-signal confluence scoring, why single signals fail, how stacking 5+ signals changes win rate",
    },
    {
        "topic": "Position Sizing",
        "title_template": "Position Sizing — How Much to Risk Per Crypto Trade",
        "focus": "ATR-based position sizing, Kelly criterion simplified, why most traders oversize, scanner risk_pct field",
    },
    {
        "topic": "Volume Expansion",
        "title_template": "Volume Expansion — The Signal That Separates Real Breakouts From Fakeouts",
        "focus": "comparing current volume to 20-day average, pre-breakout volume building, compression zones, scanner vol_expansion signal",
    },
    {
        "topic": "Relative Strength vs BTC",
        "title_template": "Relative Strength vs BTC — How I Find Coins That Move Independently",
        "focus": "14-day RS calculation, why independent strength matters in sideways markets, scanner btc_decoupling signal",
    },
    {
        "topic": "Stop Losses",
        "title_template": "How to Set Your Stop Loss Like a Professional",
        "focus": "ATR-based stops vs fixed percentage, where NOT to place stops, scanner stop levels, trailing stop strategies",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPTS — LONG-FORM (7-12 min, 900-1500 words)
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT_SCANNER_REPORT = """\
You produce long-form YouTube scripts (7-10 minutes, 900-1200 words) for
weekly crypto scanner reports. Your audience: active crypto traders who want
a thorough, data-driven weekly recap with actionable setups.

STRUCTURE — 7-8 segments, in this exact order:
1. HOOK (20-30s, 50-80 words)
   - Pattern interrupt with a concrete number in the first 8 words
   - Tease the best finding of the week
   - "I scanned 600 coins every day this week. Only [X] scored above 8.0."

2. MARKET CONTEXT (60-90s, 150-200 words)
   - BTC price, regime, 7d movement
   - What kind of setups to expect in this regime
   - One sentence framing why this week matters

3-5. TOP 3 SETUPS (90-120s each, 200-280 words each)
   - Lead with coin symbol + confluence score
   - State how many days it appeared on the scanner this week
   - Walk through ONLY the authorized signals
   - State exact entry/stop/TP levels from trade_plan
   - One sentence on what would invalidate the setup

6. WHAT DIDN'T WORK (45-60s, 100-140 words)
   - Name a SPECIFIC coin that was flagged but didn't play out
   - State its confluence score and what happened
   - Why — was it already extended? Wrong regime? Thin volume?
   - "Not every signal works. That's why we track the data."
   - Use "coin": "MISS" in the JSON

7. WIN RATE UPDATE (30-45s, 60-90 words)
   - State the win rate percentage and average return
   - Mention EACH coin discussed: "[COIN1] up X%, [COIN2] up X%, [COIN3] up X%"
   - If performance data is provided, use real numbers
   - If not, reference that tracking is ongoing
   - Use "coin": "STATS" in the JSON
   - The "stat" field MUST show: "XX% win rate" (not "XX% 24h")

8. OUTRO/CTA (20-30s, 50-70 words)
   - Tease next week's scanner report
   - "Drop a coin you want me to track"
   - Subscribe ask

ANTI-HALLUCINATION: Use ONLY data from the AUTHORIZED FACTS block.
Never invent signal names, prices, or percentages.

TITLE RULES:
- Under 80 characters
- Must contain a specific coin ticker AND a number in the first 8 words
- The title must be SEARCHABLE — include keywords real traders would search for
- Patterns: "[COIN] Just Hit [SCORE] — Full Scanner Report",
  "I Scanned 600 Coins — Only [X] Scored Above [Y]",
  "[COIN] Scored [X] on My Scanner — Here's the Setup"
- Must contain a voice marker (I/my/wait/look/etc)
- NO "Weekly Recap", "Market Update", or generic phrases
- BAD: "Wait, this chart might be about to move big" (no keyword, no ticker)
- GOOD: "SOON Just Hit 11.0 on My Scanner — Full Chart Breakdown"

NARRATION STYLE:
- Analytical, confident, conversational — like a trading journal read aloud
- Front-load specifics: ticker → score → signal names → stat
- Avoid hedge words and hype adjectives
- "The scanner flagged", "signals fired", "data shows"
- Never give financial advice

OUTPUT FORMAT — respond with ONLY this JSON:
{
  "title": "under 80 chars — MUST contain coin ticker + number",
  "hook": "opening teaser (max 25 words)",
  "segments": [
    {"coin": "MARKET", "narration": "text", "stat": "", "visual_type": "stat_card"},
    ...
  ],
  "outro": "closing CTA — MUST include subscribe ask and tease next week",
  "tags": ["list of 15-20 SEO tags"],
  "description": "full YouTube description"
}

TAG RULES — the "tags" field is critical for YouTube SEO:
- Generate 15-20 tags minimum
- First 3 tags: the coin ticker + "crypto" (e.g. "SOON crypto", "SOON USDT", "SOON token")
- Next 5 tags: specific searchable phrases ("crypto scanner", "crypto trading setup",
  "altcoin chart breakdown", "crypto confluence", "crypto signals")
- Next 5 tags: related trading terms ("crypto technical analysis", "altcoin breakout",
  "crypto entry stop target", "trading setup today", "best altcoins now")
- Last 5 tags: broader crypto terms ("crypto swing trade", "volume expansion crypto",
  "RSI divergence crypto", "scanner alert", "crypto chart analysis")
- NEVER use single generic words like just "crypto" or "bitcoin" alone
- Each tag should be 2-4 words for maximum searchability

SEGMENT COIN FIELD NAMING RULES — use EXACTLY these values:
- Market context segment: "coin": "MARKET"
- Coin setup segments: "coin": "TICKER" (e.g. "WAVES", "EDEN")
- What didn't work segment: "coin": "MISS" (NOT "WHAT_DIDNT_WORK" or "MISSES")
- Win rate segment: "coin": "STATS" (NOT "WIN_RATE" or "WINRATE")
- Never use underscores in the coin field. Use single clean words.

OUTRO RULES — the outro field is critical:
- MUST be 50-70 words minimum
- MUST include: "subscribe" or "hit subscribe"
- MUST include: a tease for next week's video
- MUST include: "drop a coin" or viewer engagement ask
- The outro is the LAST thing viewers hear — make it count
- Example: "That wraps this week's scanner report. Three setups, real levels,
  no cherry-picking. If you want next week's report the moment it drops,
  subscribe and hit the bell. And drop a coin in the comments you want me
  to scan. I read every one. See you Monday."

VISUAL TYPES: "price_chart", "heatmap", "signal_stack", "stat_card"

DESCRIPTION FORMAT:
🔍 [Hook line about this week's scanner results]

[2-3 sentence summary — coins featured, regime, key findings]

📈 Inside the video:
• Market regime + BTC overview
• Setup 1 — [TICKER] ([CONF] confluence, [N]/7 days)
• Setup 2 — [TICKER] ([CONF] confluence, [N]/7 days)
• Setup 3 — [TICKER] ([CONF] confluence, [N]/7 days)
• What didn't work + honest miss
• Win rate update

⏱️ TIMESTAMPS:
0:00 Hook
0:25 Market context
1:30 Setup 1 — [TICKER]
3:30 Setup 2 — [TICKER]
5:30 Setup 3 — [TICKER]
7:00 What didn't work
7:45 Win rate + results
8:30 What I'm watching next week

🔗 TOOLS I USE:
→ Trade on Bybit: https://shorturl.at/L3TkD
→ TradingView charts: https://shorturl.at/ZAxY6
→ CoinLedger: https://shorturl.at/73iQn

👇 Drop a coin you want me to track next week.

#crypto #bitcoin #cryptotrading #altcoins #scannerresults #weeklyrecap
"""


SYSTEM_PROMPT_EDUCATIONAL = """\
You produce long-form educational YouTube scripts (8-12 minutes, 1000-1400 words)
about crypto trading concepts. Your audience: intermediate crypto traders who
want deep, practical understanding — not textbook definitions.

The channel runs a real scanner on 600+ coins daily. Every concept you teach
should connect back to how the scanner uses it in practice.

STRUCTURE — 7-8 segments:
1. HOOK (20-30s, 50-80 words)
   - Open with a real example: "This signal showed up on [COIN] two days
     before it pumped [X]%"
   - Promise a specific payoff: "By the end, you'll know exactly how to
     spot this before it happens"

2. WHAT IT IS (60-90s, 150-200 words)
   - Plain-English explanation of the concept
   - NOT the textbook definition — your version, how you actually think about it
   - One analogy that makes it click

3. HOW IT WORKS IN PRACTICE (90-120s, 200-280 words)
   - Walk through a real example step by step
   - "Here's what it looks like on a chart"
   - Reference actual signal names from the scanner

4. THE MISTAKE MOST TRADERS MAKE (60-90s, 130-180 words)
   - One common misuse or misunderstanding
   - "Most people use RSI wrong — they look for overbought/oversold"
   - What to do instead

5. HOW THE SCANNER USES IT (60-90s, 130-180 words)
   - Connect the concept to your scanner's actual signals
   - "In my system, this is one of [X] signals that feed into the
     confluence score"
   - When combined with other signals, what does it mean?

6. REAL EXAMPLE FROM SCANNER DATA (90-120s, 200-280 words)
   - If scanner data is provided, use the actual coin + signals
   - Walk through: "Here's [COIN] — the scanner flagged [signals].
     Entry at [price], stop at [price], targets at [TP1/TP2/TP3]"
   - If no data: describe a generic but realistic scenario

7. WHEN IT FAILS (45-60s, 80-120 words)
   - Honest about limitations
   - One scenario where this signal gives a false reading
   - "That's why I never trade on a single signal alone"

8. OUTRO/CTA (20-30s, 50-70 words)
   - "The scanner checks for this on 600 coins daily"
   - Tease next educational video
   - Subscribe + comment ask

NARRATION STYLE:
- Teacher mode — patient, clear, no jargon without explanation
- Conversational, not lecturing
- "Think of it like...", "Here's the thing most people miss..."
- Connect everything back to practical trading decisions

OUTPUT FORMAT — respond with ONLY this JSON:
{
  "title": "under 80 chars — [CONCEPT] Explained — [practical angle]",
  "hook": "opening with a real example (max 25 words)",
  "segments": [
    {"coin": "SEGMENT_LABEL", "narration": "text", "stat": "", "visual_type": "TYPE"},
    ...
  ],
  "outro": "closing CTA",
  "tags": ["15-20 SEO-optimized tags — see TAG RULES below"],
  "description": "full YouTube description"
}

SEGMENT COIN FIELD NAMING RULES — use EXACTLY these labels:
- Hook/intro segment: "coin": "HOOK"
- What it is explanation: "coin": "EXPLAINER"
- How it works in practice: "coin": "PRACTICE"
- Common mistake segment: "coin": "MISTAKE"
- How scanner uses it: "coin": "SCANNER_USE"
- Real coin example: "coin": "TICKER" (the actual coin symbol e.g. "FIGHT", "BAT")
- When it fails: "coin": "LIMITATION"
- Outro: "coin": "CTA"
- NEVER use the concept name as the coin field (no "FUNDING RATES", no "RSI DIVERGENCE")
- NEVER use generic words like "HOME", "CONCEPT", etc.

TAG RULES — generate 15-20 tags:
- First 3: concept-specific ("RSI divergence crypto", "RSI trading strategy", "RSI indicator explained")
- Next 5: crypto trading phrases ("crypto technical analysis", "crypto trading setup", "altcoin trading strategy", "crypto signals explained", "trading indicators crypto")
- Next 5: related concepts ("momentum trading crypto", "crypto chart patterns", "swing trading crypto", "crypto scanner", "crypto confluence")
- Last 5: broader ("best crypto indicators", "crypto education", "learn crypto trading", "trading for beginners crypto", "how to trade crypto")
- Each tag 2-4 words, never single generic words

DESCRIPTION FORMAT:
📚 [Hook line about the concept]

[2-3 sentence summary — what you'll learn and why it matters for trading]

📈 Inside the video:
• What [CONCEPT] actually is (not the textbook version)
• How it works in practice with real charts
• The mistake most traders make
• How my scanner uses this signal
• Real example from this week's data
• When it fails — honest limitations

⏱️ TIMESTAMPS:
0:00 Hook — real example
0:25 What is [CONCEPT]?
1:30 How it works in practice
3:00 The mistake most traders make
4:15 How my scanner uses it
5:30 Real example from scanner data
7:00 When it fails
8:00 What to learn next

🔗 TOOLS I USE:
→ Trade on Bybit: https://shorturl.at/L3TkD
→ TradingView charts: https://shorturl.at/ZAxY6

👇 What concept should I explain next? Drop it in the comments.

#crypto #cryptotrading #tradingeducation #[CONCEPT] #technicalanalysis
"""


SYSTEM_PROMPT_COIN_BREAKDOWN = """\
You produce long-form YouTube scripts (7-10 minutes, 900-1200 words) for
single-coin deep dive breakdowns. Your audience: active crypto traders who
want a thorough analysis of one high-conviction scanner setup.

STRUCTURE — 7 segments:
1. HOOK (20-30s, 50-80 words)
   - Name the coin and score immediately
   - "[COIN] just scored [X] on my scanner — that's the highest I've
     seen in [timeframe]"

2. SCANNER SIGNAL OVERVIEW (60-90s, 150-200 words)
   - What the scanner flagged: list every signal that fired
   - Confluence score and what it means
   - How many scanners caught this coin (convergence vs single)
   - "Let me walk you through each signal"

3. CHART BREAKDOWN — HIGHER TIMEFRAME (90-120s, 200-280 words)
   - Weekly or daily chart context
   - Trend structure, key levels, market structure
   - Where this setup sits in the bigger picture

4. CHART BREAKDOWN — ENTRY TIMEFRAME (90-120s, 200-280 words)
   - 4H or 1H chart
   - Exact entry level, why it's there
   - Each signal explained on the chart
   - "Here's the BB squeeze... here's the RSI divergence..."

5. TRADE PLAN (60-90s, 130-180 words)
   - Exact entry, stop, TP1/TP2/TP3 from scanner data
   - Position sizing guidance
   - Risk-reward ratio
   - "I'd scale out 40% at TP1, 30% at TP2, 30% at TP3"

6. WHAT WOULD INVALIDATE THIS (45-60s, 80-120 words)
   - Exact price level where the setup dies
   - What macro scenario kills it (BTC dump, high funding flip)
   - "If price closes below [stop], I'm out. No questions."

7. OUTRO/CTA (20-30s, 50-70 words)
   - "I'll track this in next week's scanner report"
   - Comment ask + subscribe

ANTI-HALLUCINATION: Use ONLY the provided coin data. Never invent signals
or prices.

TITLE RULES:
- Under 80 chars
- Must name the coin ticker in the first 3 words
- Patterns: "[COIN] Just Hit [SCORE] — Full Chart Breakdown",
  "[COIN] Scored [SCORE] on My Scanner — Full Setup",
  "Why My Scanner Won't Stop Flagging [COIN]"
- Must contain a voice marker (I/my/look/wait/etc)
- BAD: "Wait, this chart might be about to move big" (no keyword, no ticker)
- GOOD: "SOON Just Hit 11.0 on My Scanner — Full Chart Breakdown"

OUTPUT FORMAT — respond with ONLY this JSON:
{
  "title": "under 80 chars — MUST contain coin ticker + score number",
  "hook": "opening with coin + score (max 25 words)",
  "segments": [
    {"coin": "TICKER", "narration": "text", "stat": "score", "visual_type": "price_chart"},
    ...
  ],
  "outro": "closing CTA",
  "tags": ["15-20 SEO-optimized tags — see TAG RULES below"],
  "description": "full YouTube description"
}

TAG RULES — generate 15-20 tags:
- First 3: coin-specific ("[COIN] crypto", "[COIN] USDT", "[COIN] chart analysis")
- Next 3: coin + action ("[COIN] price prediction", "[COIN] trading setup", "[COIN] breakout")
- Next 5: crypto trading phrases ("crypto scanner", "crypto trading setup", "altcoin chart breakdown", "crypto confluence", "crypto signals")
- Next 5: related terms ("crypto technical analysis", "altcoin breakout", "crypto entry stop target", "trading setup today", "best altcoins now")
- Last 4: broader ("crypto swing trade", "scanner alert", "crypto chart analysis", "volume expansion crypto")
- Each tag 2-4 words, never single generic words

DESCRIPTION FORMAT:
🔍 [COIN] just scored [X] on my scanner — here's the full breakdown.

[2-3 sentence summary — signals, entry, targets]

📈 Inside the video:
• Scanner signal overview — what fired
• Higher timeframe chart breakdown
• Entry timeframe setup
• Full trade plan: entry, stop, TP1/TP2/TP3
• What would invalidate this setup

⏱️ TIMESTAMPS:
0:00 Hook
0:25 Scanner signals
1:30 Higher timeframe chart
3:00 Entry timeframe breakdown
5:00 Trade plan
6:30 Invalidation levels
7:30 Next week tracking

🔗 TOOLS I USE:
→ Trade on Bybit: https://shorturl.at/L3TkD
→ TradingView charts: https://shorturl.at/ZAxY6

👇 Should I track this coin? Comment below.

#crypto #[TICKER] #chartbreakdown #cryptotrading #technicalanalysis
"""


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADERS
# ─────────────────────────────────────────────────────────────────────────────

def _load_today_summary() -> dict:
    """Load the latest scanner data and build market summary."""
    raw_data = load_scanner_data(_SCANNER_OUT)
    summary = build_market_summary(raw_data)

    # ── Fallback: fetch BTC price from Binance if scanner didn't have it ──
    if not summary.get("btc_price"):
        try:
            import requests
            resp = requests.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": "BTCUSDT"}, timeout=10,
            )
            resp.raise_for_status()
            btc_price = float(resp.json()["price"])
            summary["btc_price"] = btc_price
            log.info(f"  BTC price fetched from Binance: ${btc_price:,.0f}")
        except Exception as e:
            log.warning(f"  Could not fetch BTC price from Binance: {e}")

    # ── Fallback: read regime from scanner context if ingest missed it ──
    if summary.get("regime", "unknown") == "unknown":
        try:
            latest = sorted(_SCANNER_OUT.glob("master_radar_LATEST.json"))
            if not latest:
                latest = sorted(_SCANNER_OUT.glob("master_radar_*.json"),
                                key=lambda p: p.stat().st_mtime, reverse=True)
            if latest:
                data = json.loads(latest[-1].read_text(encoding="utf-8"))
                ctx = data.get("context", {})
                if ctx.get("regime"):
                    summary["regime"] = ctx["regime"]
                    log.info(f"  Regime from scanner context: {ctx['regime']}")
                if ctx.get("btc_7d_pct") is not None and summary.get("btc_7d_pct") is None:
                    summary["btc_7d_pct"] = ctx["btc_7d_pct"]
        except Exception as e:
            log.warning(f"  Could not read scanner context: {e}")

    return summary


def _get_top_coin_today(summary: dict) -> Optional[dict]:
    """Get the highest-conviction coin from today's scanner."""
    top = summary.get("top_coins", [])
    return top[0] if top else None


def _get_educational_topic() -> dict:
    """Pick the next educational topic (rotates based on week number)."""
    # Track which topics have been used
    tracker_path = _LONGFORM_DATA / "educational_index.json"
    try:
        idx = json.loads(tracker_path.read_text())["index"]
    except Exception:
        idx = 0

    topic = EDUCATIONAL_TOPICS[idx % len(EDUCATIONAL_TOPICS)]

    # Save next index
    tracker_path.write_text(json.dumps({"index": (idx + 1) % len(EDUCATIONAL_TOPICS)}))
    return topic


def _find_bgm() -> Optional[Path]:
    """Find background music file."""
    if not _BGM_DIR.exists():
        return None
    for ext in ("*.mp3", "*.wav", "*.m4a"):
        for c in _BGM_DIR.glob(ext):
            name = c.stem.lower()
            if any(kw in name for kw in ("cinematic", "documentary", "ambient", "lofi", "bgm")):
                return c
        candidates = list(_BGM_DIR.glob(ext))
        if candidates:
            return candidates[0]
    return None


def _prefer_fresh_coins(coins: list, fresh_symbols: set, summary: dict) -> list:
    """Re-rank weekly-aggregated coins so today's live signals come first.

    The weekly aggregation can surface a coin that fired once several days ago
    and has since moved or gone quiet. We reorder so that coins still present
    in today's fresh scan rank ahead of weekly-only coins, and we drop any
    weekly-only coin that hasn't been seen in the last 2 days.
    """
    today = summary.get("date", "")

    def _recent(c) -> bool:
        days = c.get("days_seen") or []
        # If we have explicit dates, require one within the last 2 calendar days.
        if days and today:
            try:
                from datetime import datetime
                td = datetime.fromisoformat(str(today)[:10])
                for d in days:
                    try:
                        if abs((td - datetime.fromisoformat(str(d)[:10])).days) <= 2:
                            return True
                    except ValueError:
                        continue
                return False
            except ValueError:
                pass
        # No usable dates — fall back to appearance count.
        return c.get("appearances", 1) >= 2

    fresh, weekly_recent = [], []
    for c in coins:
        sym = c.get("symbol", "").upper()
        if sym in fresh_symbols:
            fresh.append(c)
        elif _recent(c):
            weekly_recent.append(c)
        # else: stale one-off — dropped
    ordered = fresh + weekly_recent
    return ordered or coins  # never return empty


def _results_breakdown_from_perf(perf: dict) -> list:
    """Extract a per-coin win/loss list from the performance report.

    Tolerant of a few likely shapes (picks / details / coins / trades). Each
    output row is {"symbol", "return_pct", "outcome"}. Returns [] if none found.
    """
    rows = []
    candidates = None
    for key in ("picks", "details", "coins", "trades", "results"):
        val = perf.get(key)
        if isinstance(val, list) and val:
            candidates = val
            break
    if not candidates:
        return []

    for item in candidates:
        if not isinstance(item, dict):
            continue
        sym = (item.get("symbol") or item.get("coin")
               or item.get("ticker") or "").upper()
        if not sym:
            continue
        ret = (item.get("return_pct") if item.get("return_pct") is not None
               else item.get("pnl_pct") if item.get("pnl_pct") is not None
               else item.get("return"))
        try:
            ret = float(ret)
        except (ValueError, TypeError):
            continue
        outcome = item.get("outcome") or item.get("result")
        if not outcome:
            outcome = "win" if ret >= 0 else "loss"
        rows.append({"symbol": sym, "return_pct": ret,
                     "outcome": str(outcome).lower()})

    # Most interesting first: biggest absolute moves
    rows.sort(key=lambda r: abs(r["return_pct"]), reverse=True)
    return rows


def _check_price_staleness(coins: list, threshold_pct: float = 20.0) -> list:
    """Filter out coins whose detection price is stale vs current market price.

    Fetches the live price for each top coin from CoinGecko's free public API
    (no key required). If the current price is more than `threshold_pct` above
    the scanner's detection price, the coin is flagged as stale — the move has
    already happened and publishing entry/TP levels would mislead viewers.

    Stale coins are removed from the list and a warning is logged. If the API
    call fails (network down, rate-limited) the coin is kept with a warning so
    the pipeline doesn't silently drop setups on network errors.

    Returns the filtered coin list (may be empty if all coins are stale).
    """
    import urllib.request, urllib.error

    if not coins:
        return coins

    # Build CoinGecko id map for common scanner symbols
    _CG_IDS = {
        "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin",
        "SOL": "solana", "XRP": "ripple", "ADA": "cardano",
        "DOGE": "dogecoin", "AVAX": "avalanche-2", "DOT": "polkadot",
        "MATIC": "matic-network", "LINK": "chainlink", "UNI": "uniswap",
        "LTC": "litecoin", "ATOM": "cosmos", "XLM": "stellar",
        "ALGO": "algorand", "VET": "vechain", "FIL": "filecoin",
        "HYPE": "hyperliquid", "HOME": "home-verse", "BAT": "basic-attention-token",
        "ZAMA": "zama", "FIGHT": "fight-token", "FF": "forefront",
        "SOON": "soon", "BLEND": "blend",
    }

    symbols = [c.get("symbol", "").upper() for c in coins]
    cg_ids  = [_CG_IDS.get(s) for s in symbols]

    # Only query coins we have a CoinGecko ID for
    queryable = [(i, cg_ids[i]) for i in range(len(coins)) if cg_ids[i]]
    if not queryable:
        log.warning("  Staleness check: no CoinGecko IDs for symbols %s — skipping", symbols)
        return coins

    ids_str = ",".join(set(cg_id for _, cg_id in queryable))
    url = (f"https://api.coingecko.com/api/v3/simple/price"
           f"?ids={ids_str}&vs_currencies=usd&precision=6")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AlphaSignalsCrypto/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            prices = json.loads(r.read())
    except Exception as e:
        log.warning("  Staleness check: API call failed (%s) — keeping all coins", e)
        return coins

    fresh, dropped = [], []
    for coin in coins:
        sym    = coin.get("symbol", "").upper()
        cg_id  = _CG_IDS.get(sym)
        detect = coin.get("price") or coin.get("current_price") or coin.get("close")
        if not cg_id or not detect:
            fresh.append(coin)
            continue
        live = prices.get(cg_id, {}).get("usd")
        if live is None:
            fresh.append(coin)
            continue
        try:
            pct_above = ((float(live) - float(detect)) / float(detect)) * 100
        except (ValueError, ZeroDivisionError):
            fresh.append(coin)
            continue

        if pct_above > threshold_pct:
            log.warning(
                "  ⚠️  STALE PRICE — %s detected at $%.5f, now $%.5f (+%.1f%%). "
                "Move already happened — dropping from video to avoid misleading viewers.",
                sym, float(detect), float(live), pct_above
            )
            dropped.append(sym)
        else:
            log.info("  ✅ %s price check OK: detected $%.5f, live $%.5f (%+.1f%%)",
                     sym, float(detect), float(live), pct_above)
            # Refresh the price in the coin dict so the script uses live price
            coin["price"] = round(float(live), 6)
            fresh.append(coin)

    if dropped:
        log.warning("  Staleness guard dropped %d coin(s): %s", len(dropped), ", ".join(dropped))
        if not fresh:
            log.warning("  ALL coins were stale — no video will be produced today.")
    return fresh


def _load_performance_data() -> Optional[dict]:
    """Load the latest performance report if available."""
    perf_path = HERE / "results_tracker"
    if not perf_path.exists():
        return None
    latest = perf_path / "weekly_performance_LATEST.json"
    if latest.exists():
        try:
            return json.loads(latest.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# USER MESSAGE BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def _build_scanner_report_message(summary: dict, perf: Optional[dict]) -> str:
    """Build the LLM user message for weekly scanner report."""
    lines = [
        "Generate a LONG-FORM (7-10 minute) weekly scanner report script.",
        "Use ONLY the data in the AUTHORIZED FACTS block below.",
        "",
        "═══════════════════════════════════════════════════════════════",
        "AUTHORIZED FACTS",
        "═══════════════════════════════════════════════════════════════",
        "",
        f"MARKET CONTEXT:",
        f"  regime: {summary.get('regime', 'unknown').upper()}",
        f"  btc_price: {'$'+format(summary['btc_price'], ',.0f') if summary.get('btc_price') else 'unknown'}",
        f"  btc_7d_pct: {summary.get('btc_7d_pct', 'unknown')}",
        "",
    ]

    for idx, c in enumerate(summary.get("top_coins", [])[:5], 1):
        signals = c.get("signals", [])
        tp = _flatten_trade_plan(c.get("trade_plan")) if HAS_WEEKLY else {}

        lines.append(f"SETUP #{idx}:")
        lines.append(f"  symbol: {c.get('symbol', '?')}")
        lines.append(f"  weekly_confluence_max: {c.get('weekly_confluence_max', c.get('confluence', 0)):.1f}")
        lines.append(f"  appearances: {c.get('appearances', 1)} of last 7 days")
        lines.append(f"  days_seen: {c.get('days_seen', [])}")
        lines.append(f"  bucket: {c.get('bucket', '?')}")
        lines.append(f"  price: {c.get('price', 'unknown')}")
        lines.append(f"  change_24h: {c.get('change_24h', 0):+.2f}%")
        lines.append(f"  signals: {signals}")
        if tp:
            # Check if trade plan is still valid (price within 15% of entry)
            try:
                entry_f = float(tp.get('entry', 0))
                price_f = float(c.get('price', 0))
                if entry_f > 0 and price_f > 0:
                    deviation = abs(price_f - entry_f) / entry_f
                    if deviation <= 0.15:
                        lines.append(f"  trade_plan: entry={tp.get('entry')}, stop={tp.get('stop')}, "
                                     f"tp1={tp.get('tp1')}, tp2={tp.get('tp2')}, tp3={tp.get('tp3')}")
                    else:
                        lines.append(f"  trade_plan: STALE — entry was {tp.get('entry')} but price is now {c.get('price')}.")
                        lines.append(f"    DO NOT cite these entry/stop/TP levels. Say 'the original trade plan entry "
                                     f"has been passed — the coin already moved. Watch for a pullback to re-enter.'")
                else:
                    lines.append(f"  trade_plan: entry={tp.get('entry')}, stop={tp.get('stop')}, "
                                 f"tp1={tp.get('tp1')}, tp2={tp.get('tp2')}, tp3={tp.get('tp3')}")
            except (ValueError, TypeError):
                lines.append(f"  trade_plan: entry={tp.get('entry')}, stop={tp.get('stop')}, "
                             f"tp1={tp.get('tp1')}, tp2={tp.get('tp2')}, tp3={tp.get('tp3')}")
        lines.append("")

    # Performance data if available
    if perf and "summary" in perf:
        ps = perf["summary"]
        lines.extend([
            "PERFORMANCE DATA (from tracking past picks):",
            f"  total_picks: {ps.get('total_picks', 0)}",
            f"  win_rate: {ps.get('win_rate_pct', 0)}%",
            f"  avg_return: {ps.get('avg_return_pct', 0):+.2f}%",
            f"  best_pick: {ps.get('best_pick', '?')} ({ps.get('best_return_pct', 0):+.2f}%)",
            f"  worst_pick: {ps.get('worst_pick', '?')} ({ps.get('worst_return_pct', 0):+.2f}%)",
            "",
        ])

    lines.extend([
        "═══════════════════════════════════════════════════════════════",
        "END OF AUTHORIZED FACTS",
        "═══════════════════════════════════════════════════════════════",
    ])

    return "\n".join(lines)


def _build_educational_message(topic: dict, summary: dict) -> str:
    """Build the LLM user message for educational video."""
    lines = [
        f"Generate a LONG-FORM educational script (8-12 minutes) about: {topic['topic']}",
        f"",
        f"TOPIC FOCUS: {topic['focus']}",
        f"SUGGESTED TITLE: {topic['title_template']}",
        f"",
        f"SCANNER CONTEXT (use for real examples):",
        f"  regime: {summary.get('regime', 'unknown').upper()}",
        f"  btc_price: {'$'+format(summary['btc_price'], ',.0f') if summary.get('btc_price') else 'unknown'}",
        f"",
    ]

    # Provide top coins for real examples
    for idx, c in enumerate(summary.get("top_coins", [])[:3], 1):
        signals = c.get("signals", [])
        lines.append(f"EXAMPLE COIN #{idx}:")
        lines.append(f"  symbol: {c.get('symbol', '?')}")
        lines.append(f"  confluence: {c.get('confluence', 0):.1f}")
        lines.append(f"  signals: {signals}")
        lines.append(f"  price: {c.get('price', 'unknown')}")
        lines.append(f"  change_24h: {c.get('change_24h', 0):+.2f}%")
        lines.append("")

    lines.append("Use these coins as real examples when explaining the concept.")
    lines.append("If the concept matches a signal in the coin data, reference it specifically.")

    return "\n".join(lines)


def _build_coin_breakdown_message(coin: dict, summary: dict) -> str:
    """Build the LLM user message for single-coin deep dive."""
    signals = coin.get("signals", [])
    tp = _flatten_trade_plan(coin.get("trade_plan")) if HAS_WEEKLY else {}

    lines = [
        "Generate a LONG-FORM coin breakdown script (7-10 minutes).",
        "Use ONLY the data in the AUTHORIZED FACTS block.",
        "",
        "═══════════════════════════════════════════════════════════════",
        "AUTHORIZED FACTS",
        "═══════════════════════════════════════════════════════════════",
        "",
        f"COIN: {coin.get('symbol', '?')}",
        f"  confluence: {coin.get('confluence', 0):.1f}",
        f"  bucket: {coin.get('bucket', '?')}",
        f"  scanner_count: {coin.get('scanners', 1)}",
        f"  price: {coin.get('price', 'unknown')}",
        f"  change_24h: {coin.get('change_24h', 0):+.2f}%",
        f"  volume_24h: {coin.get('volume_24h', 0)}",
        f"  signals (USE ONLY THESE): {signals}",
        "",
    ]

    if tp:
        lines.extend([
            f"TRADE PLAN (use these exact numbers):",
            f"  entry: {tp.get('entry')}",
            f"  stop: {tp.get('stop')} ({tp.get('stop_pct')}%)",
            f"  tp1: {tp.get('tp1')} ({tp.get('tp1_pct')}% gain)",
            f"  tp2: {tp.get('tp2')} ({tp.get('tp2_pct')}% gain)",
            f"  tp3: {tp.get('tp3')} ({tp.get('tp3_pct')}% gain)",
            "",
        ])

    lines.extend([
        f"MARKET CONTEXT:",
        f"  regime: {summary.get('regime', 'unknown').upper()}",
        f"  btc_price: {'$'+format(summary['btc_price'], ',.0f') if summary.get('btc_price') else 'unknown'}",
        "",
        "═══════════════════════════════════════════════════════════════",
        "END OF AUTHORIZED FACTS",
        "═══════════════════════════════════════════════════════════════",
    ])

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# SCRIPT GENERATION — patches scriptgen with the right prompt per type
# ─────────────────────────────────────────────────────────────────────────────

def _generate_longform_script(
    system_prompt: str,
    user_message: str,
    summary: dict,
    provider: Optional[str] = None,
) -> dict:
    """Generate a script using the LLM with a custom system prompt.

    Temporarily increases max_tokens in scriptgen's provider backends
    so the LLM generates 900-1400 word scripts instead of Short-length ones.
    """
    import video_pipeline.scriptgen as sg

    # Save originals
    orig_landscape = sg.SYSTEM_PROMPT_LANDSCAPE
    orig_msg_builder = sg._build_user_message

    # Monkey-patch the provider backends to use higher max_tokens.
    # The existing backends use max_tokens=2000 (Claude/OpenAI) or
    # max_completion_tokens=8000 (Grok). For long-form we need at least
    # 4000 tokens for Claude/OpenAI. Grok's 8000 is already fine but
    # the model may be lazy — we add an explicit instruction.
    orig_call_grok = sg._call_grok
    orig_call_gemini = sg._call_gemini
    orig_call_claude = sg._call_claude
    orig_call_openai = sg._call_openai

    def _call_grok_longform(api_key, model, system, user_msg):
        # Grok already uses 8000 tokens — add explicit length instruction
        user_msg_extended = (
            user_msg + "\n\n"
            "CRITICAL: This is a LONG-FORM video script (7-12 minutes). "
            "The total narration MUST be 900-1400 words across all segments. "
            "Each coin setup segment MUST be 200-280 words. "
            "Do NOT write a short/condensed version. Write the FULL long-form script."
        )
        return orig_call_grok(api_key, model, system, user_msg_extended)

    def _call_gemini_longform(api_key, model, system, user_msg):
        user_msg_extended = (
            user_msg + "\n\n"
            "CRITICAL: This is a LONG-FORM video script (7-12 minutes). "
            "The total narration MUST be 900-1400 words across all segments. "
            "Each coin setup segment MUST be 200-280 words. "
            "Do NOT write a short/condensed version. Write the FULL long-form script."
        )
        return orig_call_gemini(api_key, model, system, user_msg_extended)

    def _call_claude_longform(api_key, model, system, user_msg):
        user_msg_extended = (
            user_msg + "\n\n"
            "CRITICAL: This is a LONG-FORM video script (7-12 minutes). "
            "The total narration MUST be 900-1400 words across all segments. "
            "Each coin setup segment MUST be 200-280 words. "
            "Do NOT write a short/condensed version. Write the FULL long-form script."
        )
        return orig_call_claude(api_key, model, system, user_msg_extended)

    def _call_openai_longform(api_key, model, system, user_msg):
        user_msg_extended = (
            user_msg + "\n\n"
            "CRITICAL: This is a LONG-FORM video script (7-12 minutes). "
            "The total narration MUST be 900-1400 words across all segments. "
            "Each coin setup segment MUST be 200-280 words. "
            "Do NOT write a short/condensed version. Write the FULL long-form script."
        )
        return orig_call_openai(api_key, model, system, user_msg_extended)

    try:
        # Patch prompts
        sg.SYSTEM_PROMPT_LANDSCAPE = system_prompt
        sg._build_user_message = lambda _summary: user_message

        # Patch backends for longer output
        sg._call_grok = _call_grok_longform
        sg._call_gemini = _call_gemini_longform
        sg._call_claude = _call_claude_longform
        sg._call_openai = _call_openai_longform

        chosen = provider or DEFAULT_PROVIDER
        script = generate_script(summary, landscape=True, provider=chosen)
        return script
    finally:
        # Restore everything
        sg.SYSTEM_PROMPT_LANDSCAPE = orig_landscape
        sg._build_user_message = orig_msg_builder
        sg._call_grok = orig_call_grok
        sg._call_gemini = orig_call_gemini
        sg._call_claude = orig_call_claude
        sg._call_openai = orig_call_openai


# ─────────────────────────────────────────────────────────────────────────────
# AUTO-SHORT — extract the best segment and build a Short
# ─────────────────────────────────────────────────────────────────────────────

def _find_latest_longform_script() -> Optional[Path]:
    """Find the most recent long-form script JSON."""
    candidates = []
    for pattern in ("longform_scanner_*.json", "longform_educational_*.json",
                    "longform_coin_*.json"):
        candidates.extend(_SCRIPT_OUT.glob(pattern))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _extract_best_short_segment(script: dict) -> dict:
    """
    Pick the single most interesting segment from a long-form script
    and repackage it as a Shorts script (under 180 words / 60 seconds).
    """
    segments = script.get("segments", [])

    # Skip meta segments (MARKET, STATS, CTA, etc.)
    coin_segments = [
        s for s in segments
        if s.get("coin", "").upper() not in
        ("MARKET", "MARKET REGIME", "STATS", "RESULTS", "CTA",
         "RISK", "INVALIDATION", "CONCEPT", "MISTAKE", "FAILURE")
    ]

    if not coin_segments:
        coin_segments = segments[1:2]  # fallback: second segment

    # Pick the segment with the highest stat (usually confluence score)
    best = coin_segments[0]
    for seg in coin_segments[1:]:
        stat = seg.get("stat", "")
        # Prefer higher confluence scores
        try:
            score = float(re.search(r"(\d+\.?\d*)", stat).group(1))
            best_score = float(re.search(r"(\d+\.?\d*)", best.get("stat", "0")).group(1))
            if score > best_score:
                best = seg
        except (AttributeError, ValueError):
            pass

    # Trim narration to under 110 words (~51s at 130 WPM — safely under 60s limit).
    # YouTube Shorts are capped at 60 seconds; we target 50-55s to leave headroom
    # for the voiceover pace varying slightly.
    SHORT_MAX_WORDS = 110
    narration = best.get("narration", "")
    words = narration.split()
    if len(words) > SHORT_MAX_WORDS:
        # Try to cut at a sentence boundary within the last 15 words of the cap
        cut = " ".join(words[:SHORT_MAX_WORDS])
        last_period = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        if last_period > len(cut) * 0.6:
            narration = cut[:last_period + 1] + " Full breakdown in the long video."
        else:
            narration = cut + ". Full breakdown in the long video."

    coin = best.get("coin", "CRYPTO")
    return {
        "title": f"Look at what {coin} just did",
        "hook": f"{coin} is setting up. Here's what my scanner caught.",
        "segments": [{
            "coin": coin,
            "narration": narration,
            "stat": best.get("stat", ""),
            "visual_type": best.get("visual_type", "price_chart"),
        }],
        "outro": "Full breakdown in the long-form video — link in description.",
        "tags": script.get("tags", ["crypto", "bitcoin"]),
        "description": f"Full breakdown: check my latest long-form video.\n\n"
                       f"#crypto #{coin.lower()} #shorts #cryptotrading",
    }


# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# AUTO CHART — renders a live dark-theme candlestick chart for a coin
# using the OHLCV data the scanner already cached to disk after each run.
# No API calls needed — the scanner wrote the data; we just read it.
# ─────────────────────────────────────────────────────────────────────────────

_OHLCV_CACHE = HERE.parent / "cache" / "shared_ohlcv"

def _chart_from_cache(symbol: str, timeframe: str = "4h",
                      entry: float = None, stop: float = None,
                      tps: list = None) -> Optional[Path]:
    """Render a candlestick chart for a coin from the scanner OHLCV cache.

    The scanner writes CSV files like cache/shared_ohlcv/XLM_bybit_4h.csv
    after every run. We read that, render a dark-theme mplfinance chart with
    optional entry/stop/TP lines, and return the output PNG path.

    Returns None if the cache file is missing or the render fails.
    """
    try:
        import pandas as pd
        import mplfinance as mpf
        import matplotlib
        matplotlib.use("Agg")
    except ImportError as e:
        log.warning("  Chart render skipped — missing library: %s", e)
        return None

    tf_map = {"4h": "4h", "1d": "1D", "daily": "1D", "4H": "4h", "1D": "1D"}
    tf_key = tf_map.get(timeframe, timeframe).lower()
    csv_path = _OHLCV_CACHE / f"{symbol.upper()}_bybit_{tf_key}.csv"

    if not csv_path.exists():
        log.warning("  Chart: cache file not found — %s", csv_path.name)
        return None

    try:
        df = pd.read_csv(str(csv_path))
        # Normalise column names — scanner writes different headers across versions
        df.columns = [c.lower().strip() for c in df.columns]
        ts_col = next((c for c in df.columns if "time" in c or "date" in c or c == "ts"), None)
        if ts_col is None:
            log.warning("  Chart: no timestamp column found in %s", csv_path.name)
            return None
        # Convert timestamp (ms epoch or ISO string)
        try:
            df["_dt"] = pd.to_datetime(df[ts_col].astype(float), unit="ms", utc=True)
        except (ValueError, TypeError):
            df["_dt"] = pd.to_datetime(df[ts_col], utc=True)
        df = df.set_index("_dt").sort_index()
        df = df.rename(columns={
            "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume",
        })
        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df[["open", "high", "low", "close", "volume"]].dropna().tail(80)
        if len(df) < 10:
            log.warning("  Chart: not enough rows (%d) in %s", len(df), csv_path.name)
            return None
    except Exception as e:
        log.warning("  Chart: failed to read cache — %s", e)
        return None

    # Dark theme matching the channel aesthetic
    mc = mpf.make_marketcolors(
        up="#00e5a0", down="#ff4d6d",
        wick={"up": "#00e5a0", "down": "#ff4d6d"},
        volume={"up": "#00e5a033", "down": "#ff4d6d33"},
        edge="inherit",
    )
    style = mpf.make_mpf_style(
        marketcolors=mc,
        facecolor="#0d1117", edgecolor="#0d1117",
        figcolor="#0d1117", gridcolor="#1e2836",
        gridstyle="--", gridaxis="both", y_on_right=True,
        rc={
            "axes.labelcolor": "#8b9ab0",
            "xtick.color": "#8b9ab0",
            "ytick.color": "#8b9ab0",
        },
    )

    tf_label = {"4h": "4H", "1d": "1D"}.get(tf_key, timeframe.upper())
    out_path = _CHARTS_DIR / datetime.now().strftime("%Y-%m-%d") / f"{symbol.upper()}_{tf_label}_auto.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    hlines, hcolors, hwidths = [], [], []
    if entry is not None:
        hlines.append(float(entry)); hcolors.append("#5b9cf6"); hwidths.append(1.4)
    if stop is not None:
        hlines.append(float(stop));  hcolors.append("#ff4d6d"); hwidths.append(1.4)
    for tp in (tps or []):
        hlines.append(float(tp)); hcolors.append("#00e5a0"); hwidths.append(1.0)

    kwargs = dict(
        type="candle", style=style, volume=True,
        title=f"\n  {symbol.upper()}/USDT  ·  {tf_label}",
        figsize=(16, 9), tight_layout=True,
        warn_too_much_data=500,
        savefig=dict(fname=str(out_path), dpi=120,
                     bbox_inches="tight", facecolor="#0d1117"),
    )
    if hlines:
        kwargs["hlines"] = dict(hlines=hlines, colors=hcolors,
                                linestyle="--", linewidths=hwidths)
    try:
        mpf.plot(df, **kwargs)
        log.info("  Auto-chart: %s (%d KB)", out_path.name,
                 out_path.stat().st_size // 1024)
        return out_path
    except Exception as e:
        log.warning("  Chart render failed: %s", e)
        return None


def _auto_charts_for_script(script: dict, summary: dict) -> dict:
    """Generate auto-charts for all real-coin segments in a script.

    Returns a dict mapping slot -> Path (e.g. "S02" -> Path("XLM_4H_auto.png"))
    so the frame override loop can use them exactly like manually taken screenshots.
    """
    slot_files = {}
    segs = script.get("segments", [])
    top_coins_by_sym = {
        c.get("symbol", "").upper(): c
        for c in summary.get("top_coins", [])
    }

    for seg in segs:
        label = seg.get("coin", "").upper().strip()
        slot  = seg.get("image_slot", "")
        ctype = seg.get("capture_type", "auto")

        if ctype != "ticker" or not slot:
            continue  # skip auto-rendered cards and concept segments

        coin_data = top_coins_by_sym.get(label, {})
        entry = coin_data.get("entry") or coin_data.get("price")
        stop  = coin_data.get("stop")
        tps   = [coin_data.get(k) for k in ("tp1", "tp2", "tp3")
                 if coin_data.get(k)]

        chart = _chart_from_cache(label, "4h", entry=entry, stop=stop, tps=tps)
        if chart:
            slot_files[slot] = chart
            log.info("  Auto-chart mapped: %s -> %s", slot, chart.name)
        else:
            log.warning("  Auto-chart: no chart for %s (slot %s)", label, slot)

    return slot_files


# CHART SHOPPING LIST — tells you exactly what to screenshot in TradingView
# ─────────────────────────────────────────────────────────────────────────────

# Segment labels rendered automatically by visuals.py — never need a screenshot.
_AUTO_RENDERED_LABELS = {
    "MARKET", "MARKET REGIME", "STATS", "RESULTS", "WIN_RATE", "WINRATE",
    "CTA", "SUBSCRIBE", "RISK", "INVALIDATION", "MISS", "MISSES",
    "WHAT_DIDNT_WORK", "WHAT DIDNT WORK", "OVERVIEW", "SUMMARY", "REGIME",
}
# Educational concept segments — one full-frame screenshot each.
_CONCEPT_LABELS = {
    "HOOK", "EXPLAINER", "CONCEPT", "HOW IT WORKS", "PRACTICE", "MISTAKE",
    "SCANNER_USE", "SCANNER", "LIMITATION", "FAIL", "FAILURE",
}

_WPM_NATURAL = 130  # matches the word-count runtime estimate used elsewhere


def _segment_capture_instruction(label: str, title: str, narration: str) -> Optional[str]:
    """One-line 'what to screenshot' for a segment, or None if auto-rendered."""
    t = (title or "").lower()
    n = (narration or "").lower()
    if label in _AUTO_RENDERED_LABELS:
        return None
    if label == "HOOK":
        return "Chart of the coin used in the opening example (the hook)."
    if label in ("EXPLAINER", "CONCEPT", "HOW IT WORKS"):
        if "funding" in t or "funding" in n:
            return "coinglass.com/FundingRate heatmap — dark theme, full screen."
        if "rsi" in t or "divergence" in n:
            return "TradingView chart with clear RSI divergence + RSI panel, dark theme."
        if "whale" in t or "whale" in n:
            return "Chart with a large volume-spike (whale) candle, dark theme."
        if "volume" in t or "volume" in n:
            return "Chart showing volume expansion before a breakout, dark theme."
        if "stop" in t or "stop loss" in n:
            return "Chart annotated with a stop-loss placement example."
        return "Chart/dashboard illustrating the concept being explained."
    if label == "PRACTICE":
        if "funding" in t or "funding" in n:
            return "coinglass.com — a specific coin with strongly negative funding."
        return "Real coin chart showing the concept in practice."
    if label == "MISTAKE":
        return "Chart showing the common mistake (false signal / failed breakout)."
    if label in ("SCANNER_USE", "SCANNER"):
        return "Your scanner output showing the relevant signal column (master_radar)."
    if label in ("LIMITATION", "FAIL", "FAILURE"):
        return "Chart where the signal FAILED — price went the wrong way. (Builds trust.)"
    # Anything else is treated as a real coin ticker.
    return f"TradingView {label}/USDT — capture 1D and 4H; mark entry/stop/TP on the 4H."


def _assign_image_slots(script: dict) -> dict:
    """Tag every segment with a stable slot id + the screenshot file(s) it expects.

    Deterministic and order-based (S01, S02, ...), so the manual CapCut edit and
    the automated chart override agree on exactly one file per segment. Mutates
    and returns the script.
    """
    for i, seg in enumerate(script.get("segments", []), 1):
        label = seg.get("coin", "").upper().strip()
        slot = f"S{i:02d}"
        seg["image_slot"] = slot
        if label in _AUTO_RENDERED_LABELS:
            seg["capture_type"] = "auto"
            seg["image_file"] = None
        elif label in _CONCEPT_LABELS:
            seg["capture_type"] = "concept"
            seg["image_file"] = f"{slot}_{label.replace(' ', '_')}.png"
        else:
            # Real coin ticker — keep the proven daily/4h split-screen convention.
            seg["capture_type"] = "ticker"
            seg["image_file"] = f"{label}_daily.png + {label}_4h.png"
    return script


def _segment_timings(script: dict, audio_duration: Optional[float]) -> list:
    """Return [(start_s, end_s)] per segment.

    If the real measured audio_duration is known, distribute it across segments
    proportionally to narration word count so markers sum to the true runtime.
    Otherwise fall back to a words-per-minute estimate (preview time).
    """
    segs = script.get("segments", [])
    words = [max(1, len(s.get("narration", "").split())) for s in segs]
    total_w = sum(words) or 1
    out, t = [], 0.0
    for w in words:
        if audio_duration:
            dur = audio_duration * (w / total_w)
        else:
            dur = w / (_WPM_NATURAL / 60.0)
        out.append((t, t + dur))
        t += dur
    return out


def _fmt_ts(s: float) -> str:
    m, sec = divmod(int(round(s)), 60)
    return f"{m:02d}:{sec:02d}"


def _generate_shotlist(script: dict, video_type: str, summary: dict = None,
                       audio_duration: Optional[float] = None) -> Path:
    """Write a CapCut-ready SHOTLIST.md mapping each segment to its timecode,
    slot, expected file and capture instruction. Also writes shotlist.json.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    chart_dir = _CHARTS_DIR / today
    chart_dir.mkdir(parents=True, exist_ok=True)

    _assign_image_slots(script)
    timings = _segment_timings(script, audio_duration)
    segs = script.get("segments", [])
    title = script.get("title", "?")
    est = "measured" if audio_duration else "estimated"

    rows, manifest = [], []
    capture_count = 0
    for i, seg in enumerate(segs):
        label = seg.get("coin", "").upper().strip()
        slot = seg.get("image_slot", f"S{i+1:02d}")
        ctype = seg.get("capture_type", "auto")
        start, end = timings[i] if i < len(timings) else (0, 0)
        instr = _segment_capture_instruction(label, title, seg.get("narration", ""))
        preview = " ".join(seg.get("narration", "").split()[:11])
        if preview and len(seg.get("narration", "").split()) > 11:
            preview += " …"

        if ctype == "auto":
            file_txt = "— (auto-rendered, no capture)"
        else:
            file_txt = f"`{seg.get('image_file')}`"
            capture_count += 1

        rows.append(
            f"| {slot} | {_fmt_ts(start)}–{_fmt_ts(end)} | {label} | "
            f"{file_txt} | {instr or '—'} |"
        )
        manifest.append({
            "slot": slot, "label": label, "capture_type": ctype,
            "image_file": seg.get("image_file"),
            "start_s": round(start, 1), "end_s": round(end, 1),
            "instruction": instr,
            "narration_preview": preview,
        })

    total = timings[-1][1] if timings else 0
    md = [
        f"# SHOT LIST — {title}",
        f"_{video_type} · {today} · runtime ~{_fmt_ts(total)} ({est} timings) · "
        f"{capture_count} screenshots needed_",
        "",
        "How to use in CapCut: import the audio, then at each timecode below drop the",
        "named screenshot file onto the timeline. Rows marked *auto-rendered* already",
        "have a generated frame — leave them. Save all screenshots into:",
        f"`{chart_dir}`",
        "",
        "| Slot | Time | Segment | File to drop | What to capture |",
        "|------|------|---------|--------------|-----------------|",
        *rows,
        "",
        "## Capture summary",
        "",
    ]
    for m in manifest:
        if m["capture_type"] == "auto":
            continue
        md.append(f"- **{m['slot']} → {m['image_file']}** — {m['instruction']}")
    md_text = "\n".join(md)

    md_path = chart_dir / "SHOTLIST.md"
    md_path.write_text(md_text, encoding="utf-8")
    (chart_dir / "shotlist.json").write_text(
        json.dumps({"title": title, "video_type": video_type,
                    "runtime_s": round(total, 1), "timings": est,
                    "segments": manifest}, indent=2), encoding="utf-8")
    return md_path


def _generate_chart_shopping_list(script: dict, video_type: str, summary: dict = None) -> Path:
    """
    Generate a shopping list of TradingView screenshots needed for this video.
    Saves to longform_charts/{today}/SHOPPING_LIST.txt and prints to console.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    chart_dir = _CHARTS_DIR / today
    chart_dir.mkdir(parents=True, exist_ok=True)

    # Extract unique coin tickers from the script (skip meta segments)
    meta_labels = {"MARKET", "MARKET REGIME", "STATS", "RESULTS", "CTA",
                   "RISK", "INVALIDATION", "CONCEPT", "MISTAKE", "FAILURE",
                   "MISSES", "WINRATE", "WIN_RATE", "OVERVIEW", "SUMMARY",
                   "INTRO", "OUTRO", "HOOK", "REGIME", "BTC", "BTC OVERVIEW",
                   "WEEKLY", "PERFORMANCE", "TRACKING", "WHAT_DIDNT_WORK",
                   "WHAT DIDNT WORK", "MISS", "MISSED", "NO_MISS",
                   "EXPLAINER", "PRACTICE", "SCANNER_USE", "LIMITATION",
                   "FUNDING RATES", "RSI DIVERGENCE", "WHALE CANDLES",
                   "VOLUME EXPANSION", "POSITION SIZING", "STOP LOSSES",
                   "CONVICTION SCORING", "RELATIVE STRENGTH",
                   "HOME", "SCANNER", "FAIL", "HOW IT WORKS"}
    coins = []
    seen = set()
    for seg in script.get("segments", []):
        coin = seg.get("coin", "").upper().strip()
        if coin and coin not in meta_labels and coin not in seen:
            coins.append(coin)
            seen.add(coin)

    # ── Educational video: build a list of concept visuals needed ──
    edu_visuals = []
    if video_type == "educational":
        # Detect the topic from the script title or segments
        title_lower = script.get("title", "").lower()
        for seg in script.get("segments", []):
            coin_upper = seg.get("coin", "").upper().strip()
            narration_lower = seg.get("narration", "").lower()

            if coin_upper in meta_labels or coin_upper in ("CTA",):
                # Figure out what visual would best accompany this segment
                visual_desc = None
                if coin_upper in ("HOOK",):
                    visual_desc = ("HOOK_visual.png",
                                   "Screenshot a chart of the coin mentioned in the hook "
                                   "(the real example that opens the video)")
                elif coin_upper in ("EXPLAINER", "FUNDING RATES", "RSI DIVERGENCE",
                                     "WHALE CANDLES", "VOLUME EXPANSION", "CONCEPT",
                                     "HOME", "HOW IT WORKS"):
                    if "funding" in title_lower or "funding" in narration_lower:
                        visual_desc = ("EXPLAINER_funding_dashboard.png",
                                       "Go to coinglass.com/FundingRate → screenshot the funding rate "
                                       "heatmap showing multiple coins with positive/negative funding. "
                                       "Dark theme, full screen.")
                    elif "rsi" in title_lower or "divergence" in narration_lower:
                        visual_desc = ("EXPLAINER_rsi_chart.png",
                                       "Open TradingView → find a coin showing clear RSI divergence "
                                       "(price making lower low, RSI making higher low). "
                                       "Screenshot with RSI panel visible. Dark theme.")
                    elif "whale" in title_lower or "whale" in narration_lower:
                        visual_desc = ("EXPLAINER_whale_candle.png",
                                       "Find a chart showing a large volume spike candle "
                                       "(whale candle) on any coin. Dark theme.")
                    elif "volume" in title_lower or "volume" in narration_lower:
                        visual_desc = ("EXPLAINER_volume_chart.png",
                                       "Find a chart showing volume expansion before a breakout. "
                                       "Dark theme.")
                    else:
                        visual_desc = ("EXPLAINER_concept.png",
                                       "Screenshot a relevant chart or dashboard illustrating "
                                       "the concept being explained.")
                elif coin_upper in ("PRACTICE",):
                    if "funding" in title_lower or "funding" in narration_lower:
                        visual_desc = ("PRACTICE_funding_example.png",
                                       "Go to coinglass.com → find a specific coin with strongly "
                                       "negative funding rate → screenshot that coin's funding "
                                       "rate chart. Dark theme.")
                    else:
                        visual_desc = ("PRACTICE_chart.png",
                                       "Screenshot a chart showing the concept in practice "
                                       "on a real coin.")
                elif coin_upper in ("MISTAKE",):
                    if "funding" in title_lower or "funding" in narration_lower:
                        visual_desc = ("MISTAKE_wrong_funding.png",
                                       "Screenshot a coin where funding was positive (crowded longs) "
                                       "but price still went up — showing the mistake of blindly "
                                       "following funding. Or use coinglass liquidation data.")
                    else:
                        visual_desc = ("MISTAKE_example.png",
                                       "Screenshot a chart showing the common mistake "
                                       "(e.g. false RSI signal, failed breakout).")
                elif coin_upper in ("SCANNER_USE", "SCANNER"):
                    visual_desc = ("SCANNER_dashboard.png",
                                   "Screenshot your scanner output showing the signal column "
                                   "for this concept (e.g. funding_negative column). "
                                   "Or screenshot the master_radar output.")
                elif coin_upper in ("LIMITATION", "FAIL", "FAILURE"):
                    visual_desc = ("LIMITATION_example.png",
                                   "Screenshot a chart where this signal failed — price went "
                                   "the wrong way despite the signal firing. Shows honesty.")

                if visual_desc:
                    edu_visuals.append(visual_desc)

    if not coins and not edu_visuals:
        lines = [
            "=" * 64,
            f"CHART SHOPPING LIST — {today}",
            "=" * 64,
            "",
            "No real coin tickers found in this script.",
            "Synthetic frames will be used for the full video.",
        ]
        shopping_text = "\n".join(lines)
        list_path = chart_dir / "SHOPPING_LIST.txt"
        list_path.write_text(shopping_text, encoding="utf-8")
        print(shopping_text)
        return list_path

    lines = [
        "=" * 64,
        f"CHART SHOPPING LIST — {today}",
        f"Video type: {video_type}",
        f"Title: {script.get('title', '?')}",
        "=" * 64,
        "",
        "ONE-TIME SETUP (do this once, reuse forever):",
        "  1. Open TradingView, create a layout called 'Scanner Videos'",
        "  2. Add indicators: Bollinger Bands (20,2), RSI (14), Volume",
        "  3. Set timeframe to 1D, dark theme",
        "  4. Save the layout",
        "",
        "FOR EACH COIN BELOW:",
        "  1. Type the ticker in the search bar",
        "  2. Screenshot the 1D chart → save as COIN_daily.png",
        "  3. Switch to 4H → screenshot → save as COIN_4h.png",
        "  4. Next coin",
        "",
        f"SAVE ALL SCREENSHOTS TO:",
        f"  {chart_dir}",
        "",
        "─" * 64,
    ]

    # ── Educational concept visuals ──
    if edu_visuals:
        lines.extend([
            "",
            "═" * 50,
            "CONCEPT VISUALS (for educational segments):",
            "═" * 50,
            "These fill the screen during explanation segments.",
            "Without them, the video shows just text on black.",
            "",
        ])
        for filename, instructions in edu_visuals:
            lines.extend([
                f"  📸 {filename}",
                f"     {instructions}",
                "",
            ])
        lines.append("─" * 64)

    # ── Coin charts ──
    if coins:
        lines.extend([
            "",
            "═" * 50,
            "COIN CHARTS (for real example segments):",
            "═" * 50,
            "",
        ])

    # Extract price levels from summary data (more reliable than parsing narration)
    # Build a lookup of coin trade plans from the summary
    coin_data = {}
    for c in summary.get("top_coins", []) if summary else []:
        sym = c.get("symbol", "").upper()
        if sym:
            tp_raw = c.get("trade_plan") or {}
            # Flatten take_profits array if present
            flat_tp = {}
            if tp_raw:
                flat_tp["entry"] = tp_raw.get("entry")
                flat_tp["stop"] = tp_raw.get("stop")
                tps = tp_raw.get("take_profits") or []
                for idx_tp, tp_item in enumerate(tps[:3], 1):
                    if isinstance(tp_item, dict):
                        flat_tp[f"tp{idx_tp}"] = tp_item.get("price")
                # Also check flat format
                for key in ("tp1", "tp2", "tp3"):
                    if key not in flat_tp and key in tp_raw:
                        flat_tp[key] = tp_raw[key]
            coin_data[sym] = {
                "price": c.get("price"),
                "change_24h": c.get("change_24h", 0),
                "confluence": c.get("confluence") or c.get("weekly_confluence_max", 0),
                "trade_plan": flat_tp,
                "signals": c.get("signals", []),
            }

    for coin in coins:
        lines.append("")
        lines.append(f"▶ {coin}/USDT")

        cd = coin_data.get(coin, {})
        tp = cd.get("trade_plan", {})
        entry = tp.get("entry")
        stop = tp.get("stop")
        tp1 = tp.get("tp1")
        tp2 = tp.get("tp2")
        tp3 = tp.get("tp3")
        price = cd.get("price")
        conf = cd.get("confluence", 0)

        lines.append(f"  Screenshots: {coin}_daily.png + {coin}_4h.png")
        if price:
            lines.append(f"  Current price: ${price}")
        if conf:
            lines.append(f"  Confluence: {conf:.1f}")
        lines.append("")

        # Check if trade plan levels are still valid (price near entry)
        levels_valid = False
        if entry and price:
            try:
                entry_f = float(entry)
                price_f = float(price)
                if entry_f > 0:
                    deviation = abs(price_f - entry_f) / entry_f
                    levels_valid = deviation <= 0.15  # within 15%
            except (ValueError, TypeError):
                pass

        if levels_valid and (entry or stop or tp1):
            lines.append(f"  PRICE LEVELS (draw these as horizontal lines on 4H chart):")
            if entry:
                lines.append(f"    Entry:  ${entry}  (white or blue line)")
            if stop:
                lines.append(f"    Stop:   ${stop}  (red line)")
            if tp1:
                lines.append(f"    TP1:    ${tp1}  (green line)")
            if tp2:
                lines.append(f"    TP2:    ${tp2}  (green line)")
            if tp3:
                lines.append(f"    TP3:    ${tp3}  (green line)")
        elif entry and not levels_valid:
            lines.append(f"  ⚠ TRADE PLAN STALE — entry was ${entry} but price is now ${price}")
            lines.append(f"    Price moved too far from entry. DO NOT draw these levels.")
            lines.append(f"    Just screenshot the chart as-is.")
        else:
            lines.append(f"  No trade plan levels — just screenshot the chart as-is")

        lines.append("")

    lines.extend([
        "─" * 64,
        "",
        f"Total: {len(coins)} coins × 2 screenshots = {len(coins) * 2} files",
        f"Time estimate: ~{len(coins) * 2} minutes",
        "",
        "When done, run:",
        f"  .\\longform_step2.bat",
        "",
        "=" * 64,
    ])

    shopping_text = "\n".join(lines)

    # Save to file
    list_path = chart_dir / "SHOPPING_LIST.txt"
    list_path.write_text(shopping_text, encoding="utf-8")

    # Print to console
    print(shopping_text)

    return list_path


# ─────────────────────────────────────────────────────────────────────────────
# CHART OVERRIDE — use real TradingView screenshots when available
# ─────────────────────────────────────────────────────────────────────────────

def _find_real_charts(today: str = None) -> dict:
    """
    Scan longform_charts/{today}/ for real TradingView screenshots.
    Returns {COIN_UPPER: [list of chart Paths]} for each coin found.
    """
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")

    chart_dir = _CHARTS_DIR / today
    if not chart_dir.exists():
        return {}

    charts = {}
    edu_files = []
    slot_files = {}
    import re as _re
    for img in sorted(chart_dir.glob("*.png")):
        if img.name == "SHOPPING_LIST.txt":
            continue
        stem_upper = img.stem.upper()
        # Deterministic slot file: S03_EXPLAINER.png -> slot "S03"
        m = _re.match(r"^(S\d{2})(?:[_-].*)?$", stem_upper)
        if m:
            slot_files[m.group(1)] = img
            continue
        # Legacy educational visual (EXPLAINER_, PRACTICE_, etc.)
        if any(stem_upper.startswith(prefix) for prefix in
               ("HOOK_", "EXPLAINER_", "PRACTICE_", "MISTAKE_",
                "SCANNER_", "LIMITATION_")):
            edu_files.append(img)
            continue
        # Parse coin from filename: XDC_daily.png → XDC
        parts = stem_upper.split("_")
        if len(parts) >= 2:
            coin = parts[0]
            if coin not in charts:
                charts[coin] = []
            charts[coin].append(img)

    if edu_files:
        charts["_edu_files"] = edu_files
    if slot_files:
        charts["_slot_files"] = slot_files

    return charts


def _override_frames_with_real_charts(
    frame_paths: list,
    script: dict,
    real_charts: dict,
    size: tuple,
) -> list:
    """
    Replace synthetic frames with real TradingView screenshots.
    
    If both daily and 4H charts exist for a coin, combines them into a
    split-screen layout: daily on the left half, 4H on the right half,
    with a small label on each showing the timeframe.
    
    If only one chart exists, stretches it to fill the full frame.
    
    Returns updated frame_paths list.
    """
    from PIL import Image, ImageDraw, ImageFont

    if not real_charts:
        return frame_paths

    meta_labels = {"MARKET", "MARKET REGIME", "STATS", "RESULTS", "CTA",
                   "RISK", "INVALIDATION", "CONCEPT", "MISTAKE", "FAILURE",
                   "MISSES", "WINRATE", "WIN_RATE", "OVERVIEW", "SUMMARY",
                   "INTRO", "OUTRO", "HOOK", "REGIME", "BTC", "BTC OVERVIEW",
                   "WEEKLY", "PERFORMANCE", "TRACKING", "WHAT_DIDNT_WORK",
                   "WHAT DIDNT WORK", "MISS", "MISSED", "NO_MISS",
                   "EXPLAINER", "PRACTICE", "SCANNER_USE", "LIMITATION",
                   "FUNDING RATES", "RSI DIVERGENCE", "WHALE CANDLES",
                   "VOLUME EXPANSION", "HOME", "SCANNER", "FAIL",
                   "HOW IT WORKS"}

    # Build a mapping of educational segment labels to image files
    edu_file_map = {}
    for img_path in real_charts.get("_edu_files", []):
        stem = img_path.stem.upper()
        if "HOOK" in stem:
            edu_file_map["HOOK"] = img_path
        elif "EXPLAINER" in stem:
            for label in ("EXPLAINER", "FUNDING RATES", "RSI DIVERGENCE",
                          "WHALE CANDLES", "VOLUME EXPANSION", "CONCEPT",
                          "HOME", "HOW IT WORKS"):
                edu_file_map[label] = img_path
        elif "PRACTICE" in stem:
            edu_file_map["PRACTICE"] = img_path
        elif "MISTAKE" in stem:
            edu_file_map["MISTAKE"] = img_path
        elif "SCANNER" in stem:
            for label in ("SCANNER_USE", "SCANNER"):
                edu_file_map[label] = img_path
        elif "LIMITATION" in stem:
            for label in ("LIMITATION", "FAIL", "FAILURE"):
                edu_file_map[label] = img_path

    segments = script.get("segments", [])
    updated = list(frame_paths)

    w, h = size

    # Load font once
    label_font = None
    try:
        for fp in ["C:/Windows/Fonts/arialbd.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
            try:
                label_font = ImageFont.truetype(fp, 32)
                break
            except (OSError, IOError):
                continue
        if not label_font:
            label_font = ImageFont.load_default()
    except Exception:
        label_font = ImageFont.load_default()

    for i, seg in enumerate(segments):
        if i >= len(updated):
            break

        coin = seg.get("coin", "").upper().strip()

        # Deterministic slot match first (S01_*.png ... mapped by segment order)
        slot_files = real_charts.get("_slot_files", {})
        slot = seg.get("image_slot")
        if slot and slot in slot_files:
            try:
                img = Image.open(str(slot_files[slot])).convert("RGB")
                img.resize((w, h), Image.LANCZOS).save(str(updated[i]), "PNG", quality=95)
                log.info(f"  Frame {i}: slot {slot} -> {slot_files[slot].name}")
                continue
            except Exception as e:
                log.warning(f"  Frame {i}: slot {slot} file failed: {e}")

        # Check for educational visual first
        if coin in edu_file_map:
            edu_img_path = edu_file_map[coin]
            try:
                img = Image.open(str(edu_img_path))
                img_resized = img.resize((w, h), Image.LANCZOS)
                img_resized.save(str(updated[i]), "PNG", quality=95)
                log.info(f"  Frame {i}: replaced with educational visual {edu_img_path.name} for {coin}")
                continue
            except Exception as e:
                log.warning(f"  Frame {i}: could not process educational visual {edu_img_path.name}: {e}")

        if coin in meta_labels or coin not in real_charts:
            continue

        available = real_charts[coin]
        if not available:
            continue

        # Sort charts: daily first, then 4h, then signals
        daily_chart = None
        h4_chart = None
        for cp in available:
            stem = cp.stem.lower()
            if "daily" in stem or "1d" in stem:
                daily_chart = cp
            elif "4h" in stem:
                h4_chart = cp

        try:
            if daily_chart and h4_chart:
                # SPLIT SCREEN: daily on left, 4H on right
                img_daily = Image.open(str(daily_chart))
                img_4h = Image.open(str(h4_chart))

                half_w = w // 2
                gap = 4  # thin divider between charts

                # Resize each to fill its half
                left = img_daily.resize((half_w - gap, h), Image.LANCZOS)
                right = img_4h.resize((half_w - gap, h), Image.LANCZOS)

                # Compose onto dark background
                bg = Image.new("RGB", (w, h), (6, 6, 16))
                bg.paste(left, (0, 0))
                bg.paste(right, (half_w + gap, 0))

                # Draw divider line
                draw = ImageDraw.Draw(bg)
                draw.rectangle([half_w - gap, 0, half_w + gap, h], fill=(20, 20, 40))

                # Labels
                # Daily label
                draw.rectangle([8, 8, 180, 48], fill=(6, 6, 16, 200))
                draw.text((16, 12), f"{coin} · 1D", font=label_font, fill=(0, 212, 255))

                # 4H label
                draw.rectangle([half_w + gap + 8, 8, half_w + gap + 180, 48], fill=(6, 6, 16, 200))
                draw.text((half_w + gap + 16, 12), f"{coin} · 4H", font=label_font, fill=(0, 232, 123))

                bg.save(str(updated[i]), "PNG", quality=95)
                log.info(f"  Frame {i}: split-screen {daily_chart.name} + {h4_chart.name} for {coin}")

            else:
                # SINGLE CHART: stretch to full frame
                chart_path = daily_chart or h4_chart or available[0]
                img = Image.open(str(chart_path))
                img_resized = img.resize((w, h), Image.LANCZOS)

                # Add label
                draw = ImageDraw.Draw(img_resized)
                timeframe = "4H" if "4h" in chart_path.stem.lower() else "1D"
                draw.rectangle([8, 8, 220, 48], fill=(6, 6, 16))
                draw.text((16, 12), f"{coin}/USDT · {timeframe}", font=label_font, fill=(0, 212, 255))

                img_resized.save(str(updated[i]), "PNG", quality=95)
                log.info(f"  Frame {i}: full-screen {chart_path.name} for {coin}")

        except Exception as e:
            log.warning(f"  Frame {i}: could not process charts for {coin}: {e}")

    return updated

_THUMB_MAX_BYTES = 2 * 1024 * 1024  # YouTube hard limit is 2 MB

def _generate_thumbnail(script: dict, summary: dict, output_path: Path) -> Path:
    """Generate a landscape thumbnail for the long-form video.

    Renders one stat_card frame, then saves it as a compressed JPEG that is
    guaranteed to stay under YouTube's 2 MB limit (a lossless PNG of a
    1920x1080 gradient frame is ~3 MB and gets rejected). Returns the actual
    output path (extension forced to .jpg).
    """
    from PIL import Image

    # Force .jpg so we never hand YouTube an oversized PNG.
    if output_path.suffix.lower() != ".jpg":
        output_path = output_path.with_suffix(".jpg")

    thumb_script = {
        "segments": [{
            "coin": script.get("segments", [{}])[0].get("coin", "SCANNER"),
            "narration": "",
            "stat": script.get("title", ""),
            "visual_type": "stat_card",
        }]
    }
    frames = render_all_frames(
        script=thumb_script, summary=summary,
        output_dir=output_path.parent, size=SIZE_LANDSCAPE,
    )
    if not frames:
        return output_path

    src_frame = frames[0]
    try:
        img = Image.open(str(src_frame)).convert("RGB")
        # Step quality down until we clear the 2 MB ceiling (usually q=88 is fine).
        for q in (90, 85, 80, 72, 65, 55):
            img.save(str(output_path), "JPEG", quality=q, optimize=True,
                     progressive=True)
            if output_path.stat().st_size <= _THUMB_MAX_BYTES:
                break
    finally:
        # Clean up the temp PNG frame(s)
        for f in frames:
            try:
                if Path(f).resolve() != output_path.resolve() and Path(f).exists():
                    Path(f).unlink()
            except OSError:
                pass
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

def run(
    video_type:  str  = "auto",
    no_upload:   bool = False,
    preview:     bool = False,
    provider:    str  = None,
    skip_voice:  bool = False,
) -> dict:
    """
    Run the long-form pipeline for the given video type.

    Args:
        video_type: "scanner_report", "educational", "coin_breakdown",
                    "auto_short", or "auto" (picks from schedule)
        no_upload:  Skip YouTube upload
        preview:    Print script JSON and stop
        provider:   Force LLM provider
        skip_voice: Skip voice generation (reuse latest)
    """
    t0 = time.time()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Auto-detect from schedule
    if video_type == "auto":
        dow = datetime.now().weekday()
        video_type = SCHEDULE.get(dow)
        if video_type is None:
            log.info(f"  No long-form scheduled for {datetime.now().strftime('%A')}. "
                     f"Use --type to force.")
            return {"status": "no_schedule"}

    log.info("=" * 64)
    log.info(f"LONG-FORM PIPELINE — {video_type.upper()}")
    log.info(f"  Date: {datetime.now().strftime('%A %Y-%m-%d %H:%M')}")
    log.info(f"  Upload: {'disabled' if no_upload else 'enabled'}")
    log.info("=" * 64)

    # ── Load scanner data ────────────────────────────────────────────────
    log.info("\n[1/7] Loading scanner data...")
    summary = _load_today_summary()
    btc_p = summary.get("btc_price")
    log.info(f"  Regime: {summary.get('regime', '?')}, "
             f"BTC: {'$'+format(btc_p, ',.0f') if btc_p else 'unknown'}")

    # ── Generate script based on type ────────────────────────────────────
    log.info(f"\n[2/7] Generating {video_type} script...")

    if video_type == "scanner_report":
        # Keep today's fresh scan before the weekly aggregation overwrites it,
        # so we can prefer coins that are still live (not 6-day-old one-offs).
        fresh_top = {c.get("symbol", "").upper()
                     for c in summary.get("top_coins", [])}
        # Aggregate weekly data
        if HAS_WEEKLY:
            aggregated, scripts_found = aggregate_weekly_picks(lookback_days=7)
            # min_appearances=2 drops coins seen on only a single day last week.
            top_three = select_top_three_weekly(aggregated, min_appearances=2)
            summary = build_weekly_summary(top_three, summary)
            summary["top_coins"] = _prefer_fresh_coins(
                summary.get("top_coins", []), fresh_top, summary)
        # ── Price staleness guard ──────────────────────────────────────────
        # Check that detection prices are still within 20% of live market
        # price. Drops any coin that has already moved too far so the video
        # doesn't publish stale entry/TP levels to viewers.
        raw_top = summary.get("top_coins", [])
        fresh_coins = _check_price_staleness(raw_top, threshold_pct=20.0)
        if not fresh_coins and raw_top:
            log.warning("  No fresh coins remain after staleness check — aborting video.")
            return {"status": "aborted", "reason": "all_coins_stale"}
        summary["top_coins"] = fresh_coins

        # ── Minimum confluence guard ───────────────────────────────────────
        # Require at least one coin above MIN_CONFLUENCE before spending
        # LLM tokens + ElevenLabs quota on a weak script.
        MIN_CONFLUENCE = 8.0
        strong_coins = [
            c for c in fresh_coins
            if float(c.get("confluence", c.get("conf", c.get("score", 0))) or 0)
               >= MIN_CONFLUENCE
        ]
        if not strong_coins:
            regime_now = summary.get("regime", "unknown").upper()
            top_desc = ", ".join(
                "{} ({:.1f})".format(
                    c.get("symbol", "?"),
                    float(c.get("confluence", c.get("conf", c.get("score", 0))) or 0)
                )
                for c in fresh_coins[:5]
            )
            log.warning(
                "NO VIDEO TODAY — no coins above %.1f confluence. "
                "Regime: %s. Top available: %s. "
                "Skipping LLM + voiceover + upload.",
                MIN_CONFLUENCE, regime_now, top_desc or "none"
            )
            return {
                "status": "skipped",
                "reason": "confluence_too_low",
                "regime": regime_now,
                "details": {
                    "fresh_coins": len(fresh_coins),
                    "strong_coins": 0,
                    "min_required": MIN_CONFLUENCE,
                    "top_coins": [
                        {
                            "symbol": c.get("symbol"),
                            "confluence": float(
                                c.get("confluence",
                                      c.get("conf", c.get("score", 0))) or 0
                            )
                        }
                        for c in fresh_coins[:5]
                    ],
                },
            }

        perf = _load_performance_data()
        # Surface performance + per-coin breakdown into the summary so the
        # RESULTS stat card can render real numbers (not just feed the LLM).
        if perf:
            summary["performance"] = perf.get("summary", {})
            summary["results_breakdown"] = _results_breakdown_from_perf(perf)
            rb = summary["results_breakdown"]
            if rb:
                log.info(f"  RESULTS card: {len(rb)} per-coin rows "
                         f"({', '.join(r['symbol'] for r in rb[:5])})")
            else:
                log.warning("  RESULTS card: no per-coin list matched the perf "
                            f"JSON. Top-level keys present: {list(perf.keys())}. "
                            "Card will fall back to best/worst pick.")
        user_msg = _build_scanner_report_message(summary, perf)
        system_prompt = SYSTEM_PROMPT_SCANNER_REPORT
        script_prefix = "longform_scanner"

    elif video_type == "educational":
        topic = _get_educational_topic()
        log.info(f"  Topic: {topic['topic']}")
        user_msg = _build_educational_message(topic, summary)
        system_prompt = SYSTEM_PROMPT_EDUCATIONAL
        script_prefix = "longform_educational"

    elif video_type == "coin_breakdown":
        top_coin = _get_top_coin_today(summary)
        if not top_coin:
            log.error("  No coins in scanner output — cannot build breakdown")
            return {"error": "no_coins"}
        # Require the top coin to have enough conviction for a full breakdown.
        # A coin with confluence < 8.0 doesn't have enough signal stacking to
        # fill 7-10 minutes without padding — skip and save quota.
        top_conf = float(
            top_coin.get("confluence", top_coin.get("conf",
            top_coin.get("score", 0))) or 0
        )
        if top_conf < 8.0:
            log.warning(
                "NO VIDEO TODAY — top coin %s has confluence %.1f (need >= 8.0). "
                "Skipping coin breakdown.",
                top_coin.get("symbol", "?"), top_conf
            )
            return {"status": "skipped", "reason": "top_coin_confluence_too_low",
                    "coin": top_coin.get("symbol"), "confluence": top_conf}
        log.info(f"  Coin: {top_coin.get('symbol')} "
                 f"(confluence={top_conf:.1f})")
        user_msg = _build_coin_breakdown_message(top_coin, summary)
        system_prompt = SYSTEM_PROMPT_COIN_BREAKDOWN
        script_prefix = "longform_coin"

    elif video_type == "auto_short":
        log.info("  Finding latest long-form script for Short extraction...")
        latest = _find_latest_longform_script()
        if not latest:
            log.error("  No long-form scripts found — run a long-form first")
            return {"error": "no_longform_scripts"}
        log.info(f"  Source: {latest.name}")
        source_script = json.loads(latest.read_text(encoding="utf-8"))
        script = _extract_best_short_segment(source_script)
        # For auto_short, skip the LLM call — we already have the script
        log.info(f"  Extracted Short: {script['segments'][0]['coin']}")
        script_prefix = "longform_short"
        # Jump to production with portrait size
        size = (1080, 1920)
        script_path = _SCRIPT_OUT / f"{script_prefix}_{ts}.json"
        script_path.write_text(json.dumps(script, indent=2), encoding="utf-8")
        # Continue to voice/visual/compose/upload below
        # (handled after the if/elif chain)
        system_prompt = None  # signal to skip LLM generation

    else:
        log.error(f"  Unknown video type: {video_type}")
        return {"error": f"unknown_type:{video_type}"}

    # ── LLM script generation (skip for auto_short) ──────────────────────
    if video_type != "auto_short":
        size = SIZE_LANDSCAPE
        try:
            script = _generate_longform_script(
                system_prompt=system_prompt,
                user_message=user_msg,
                summary=summary,
                provider=provider,
            )
        except Exception as e:
            log.error(f"  Script generation failed: {e}")
            return {"error": "script_failed", "exception": str(e)}

        log.info(f"  Title: {script.get('title', '?')}")
        log.info(f"  Segments: {len(script.get('segments', []))}")

        # Word count check
        wc = sum(len(s.get("narration", "").split()) for s in script.get("segments", []))
        wc += len(script.get("hook", "").split()) + len(script.get("outro", "").split())
        log.info(f"  Word count: {wc} (~{wc * 60 // 130} seconds at natural pace)")

        if wc < 600:
            log.warning(f"  ⚠ Script is short ({wc} words). Target: 900-1400 words.")

        # ── Ensure outro is included as a segment ────────────────────────
        # The LLM sometimes puts the CTA only in the "outro" field but not
        # as a segment. The voiceover only reads segments, so the outro gets
        # silently dropped — causing the video to cut off mid-speech.
        outro_text = script.get("outro", "").strip()
        if outro_text:
            last_seg = script["segments"][-1] if script.get("segments") else {}
            last_narration = last_seg.get("narration", "").strip()
            # Only add if the outro text isn't already in the last segment
            if outro_text not in last_narration and last_seg.get("coin", "").upper() != "CTA":
                script["segments"].append({
                    "coin": "CTA",
                    "narration": outro_text,
                    "stat": "",
                    "visual_type": "stat_card",
                })
                log.info(f"  Added outro as CTA segment ({len(outro_text.split())} words)")

        # Assign deterministic image slots so the shot list and the automated
        # chart override agree on exactly one file per segment.
        _assign_image_slots(script)

        # Save script
        script_path = _SCRIPT_OUT / f"{script_prefix}_{ts}.json"
        script_path.write_text(json.dumps(script, indent=2), encoding="utf-8")
        log.info(f"  Saved: {script_path.name}")

    if preview:
        log.info("\n── PREVIEW MODE ──")
        print(json.dumps(script, indent=2))

        # Generate chart shopping list + CapCut shot list (estimated timings)
        log.info("\n── CHART SHOPPING LIST ──")
        list_path = _generate_chart_shopping_list(script, video_type, summary=summary)
        shotlist_path = _generate_shotlist(script, video_type, summary=summary)
        log.info(f"\n  Shopping list saved: {list_path}")
        log.info(f"  Shot list saved:     {shotlist_path}")
        log.info(f"  Take the screenshots (named per the shot list), then run:")
        log.info(f"    python longform_pipeline.py --type {video_type}")

        return {"script": script, "script_path": str(script_path),
                "shopping_list": str(list_path),
                "shot_list": str(shotlist_path)}

    # ── Voiceover ────────────────────────────────────────────────────────
    log.info(f"\n[3/7] Generating voiceover...")
    audio_path = _VOICE_OUT / f"{script_prefix}_audio_{ts}.mp3"

    # No background music for long-form — YouTube penalises copyright
    import video_pipeline.voiceover as vo
    orig_speed = vo.VOICE_SPEED
    if video_type != "auto_short":
        vo.VOICE_SPEED = 1.0  # Long-form: natural human pace
    try:
        audio_duration = generate_voiceover(
            script["segments"], output_path=audio_path, bgm_path=None,
        )
    finally:
        vo.VOICE_SPEED = orig_speed

    log.info(f"  Audio: {audio_duration:.1f}s ({audio_duration/60:.1f} min)")

    # Refresh the shot list with the real measured runtime (markers now exact).
    try:
        sl = _generate_shotlist(script, video_type, summary=summary,
                                audio_duration=audio_duration)
        log.info(f"  Shot list (measured timings): {sl}")
    except Exception as e:
        log.warning(f"  Shot list refresh failed: {e}")

    # ── Visual frames ────────────────────────────────────────────────────
    log.info(f"\n[4/7] Rendering visual frames...")
    frames_dir = _FRAMES_OUT / f"{script_prefix}_frames_{ts}"
    frames_dir.mkdir(exist_ok=True)
    frame_paths = render_all_frames(
        script=script, summary=summary,
        output_dir=frames_dir, size=size,
    )
    log.info(f"  Frames: {len(frame_paths)}")

    # ── Override with real TradingView charts if available ────────────────
    today = datetime.now().strftime("%Y-%m-%d")
    real_charts = _find_real_charts(today)
    if real_charts:
        log.info(f"\n  Found real charts for: {', '.join(real_charts.keys())}")
        frame_paths = _override_frames_with_real_charts(
            frame_paths, script, real_charts, size,
        )
    else:
        # No manual screenshots — attempt auto-chart from scanner OHLCV cache.
        # This renders a dark-theme candlestick chart for each real-coin segment
        # using the CSV data the scanner already wrote to disk after the scan.
        log.info(f"  No manual charts found — attempting auto-chart from scanner cache...")
        auto_slots = _auto_charts_for_script(script, summary)
        if auto_slots:
            # Merge auto-charts into a real_charts-compatible dict and override
            auto_real = {"_slot_files": auto_slots}
            frame_paths = _override_frames_with_real_charts(
                frame_paths, script, auto_real, size,
            )
            log.info(f"  Auto-charts applied for {len(auto_slots)} segment(s)")
        else:
            log.info(f"  No OHLCV cache found — using synthetic stat card frames")
            log.info(f"  TIP: run with --preview first to get a chart shopping list")

    # ── Compose video ────────────────────────────────────────────────────
    log.info(f"\n[5/7] Composing video...")
    video_path = _VIDEO_OUT / f"{script_prefix}_{ts}.mp4"
    # Long-form: static frames (no zoom). Shorts: keep zoom effect.
    use_static = (video_type != "auto_short")
    compose_video(
        frame_paths=frame_paths, audio_path=audio_path,
        script=script, output_path=video_path, size=size,
        static=use_static,
    )
    log.info(f"  Video: {video_path.name}")
    size_mb = video_path.stat().st_size / 1024 / 1024
    log.info(f"  Size: {size_mb:.1f} MB")

    # ── Thumbnail ────────────────────────────────────────────────────────
    log.info(f"\n[6/7] Generating thumbnail...")
    thumb_path = _VIDEO_OUT / f"{script_prefix}_thumb_{ts}.jpg"
    try:
        thumb_path = _generate_thumbnail(script, summary, thumb_path)
        size_kb = thumb_path.stat().st_size / 1024 if thumb_path.exists() else 0
        log.info(f"  Thumbnail: {thumb_path.name} ({size_kb:.0f} KB)")
    except Exception as e:
        log.warning(f"  Thumbnail failed: {e}")
        thumb_path = None

    # ── Upload ───────────────────────────────────────────────────────────
    upload_result = None
    if not no_upload:
        log.info(f"\n[7/7] Uploading to YouTube...")
        try:
            script["_summary"] = summary
            script["_video_type"] = video_type
            creds = get_youtube_credentials()
            upload_result = upload_to_youtube(
                video_path=video_path, script=script,
                credentials=creds, thumbnail_path=thumb_path,
            )
            video_id = upload_result.get("id", "?")
            log.info(f"  Uploaded: https://youtu.be/{video_id}")

            # ── Auto-assign to playlist ──
            playlist_id = PLAYLIST_IDS.get(video_type)
            if playlist_id and video_id != "?":
                log.info(f"  Adding to playlist...")
                _add_to_playlist(creds, video_id, playlist_id)

            # Comment is posted by upload_to_youtube() in upload.py (build_pinned_comment).
            # Do NOT post a second comment here — it creates duplicates.

        except Exception as e:
            log.error(f"  Upload failed: {e}")
    else:
        log.info(f"\n[7/7] Upload skipped (--no-upload)")

    elapsed = time.time() - t0
    log.info(f"\n{'=' * 64}")
    log.info(f"LONG-FORM PIPELINE COMPLETE — {video_type}")
    log.info(f"  Duration: {audio_duration:.0f}s ({audio_duration/60:.1f} min)")
    log.info(f"  Elapsed: {elapsed:.0f}s ({elapsed/60:.1f} min)")
    log.info(f"  Video: {video_path}")
    log.info(f"{'=' * 64}")

    return {
        "status": "success",
        "video_type": video_type,
        "video_path": str(video_path),
        "script_path": str(script_path),
        "audio_path": str(audio_path),
        "audio_duration": round(audio_duration, 1),
        "upload": upload_result,
        "elapsed_s": round(elapsed, 1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Long-Form Video Pipeline — 3 videos/week + 2 auto-Shorts"
    )
    parser.add_argument(
        "--type", choices=[
            "auto", "scanner_report", "educational",
            "coin_breakdown", "auto_short",
        ],
        default="auto",
        help="Video type (default: auto-detect from day of week)",
    )
    parser.add_argument("--no-upload", action="store_true",
                        help="Skip YouTube upload")
    parser.add_argument("--preview", action="store_true",
                        help="Print script JSON and stop")
    parser.add_argument("--provider", choices=["grok", "gemini", "claude", "openai"],
                        default=None, help="Force LLM provider")
    parser.add_argument("--skip-voice", action="store_true",
                        help="Skip voice generation")

    args = parser.parse_args()

    result = run(
        video_type=args.type,
        no_upload=args.no_upload,
        preview=args.preview,
        provider=args.provider,
        skip_voice=args.skip_voice,
    )

    if result.get("error"):
        log.error(f"\n  FAILED: {result['error']}")
        sys.exit(1)
