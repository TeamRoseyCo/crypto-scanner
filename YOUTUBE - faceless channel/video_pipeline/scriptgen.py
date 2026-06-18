"""
scriptgen.py — Generate a narration script using an LLM API.

Supports multiple providers (auto-detected from environment variables):
  1. Grok     (free tier)  — set XAI_API_KEY
  2. Gemini   (free tier)  — set GEMINI_API_KEY
  3. Claude   (paid)       — set ANTHROPIC_API_KEY
  4. OpenAI   (paid)       — set OPENAI_API_KEY

Priority: checks in that order, uses the first key found.
Override with SCRIPT_PROVIDER env var (e.g. SCRIPT_PROVIDER=gemini).

Takes the market summary from ingest.py and produces a structured
JSON script with title, hook, segments, outro, tags, and description.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

log = logging.getLogger("video_pipeline.scriptgen")

# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT — controls the style and structure of generated scripts
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT_SHORTS = """\
You produce YouTube Shorts scripts for daily crypto market recaps.
Your audience: active crypto traders who want a fast, data-driven update.
Goal: maximize the % of viewers who watch past the first 3 seconds AND return tomorrow.

═══════════════════════════════════════════════════════════════════
PERFORMANCE DATA (from this channel's actual analytics)
═══════════════════════════════════════════════════════════════════
TITLES THAT PERFORM:
- "ORCA Screams 10.0! Sideways Market Setups"  → 63 views, 38% retention (ticker + number)
- "Sideways Market? Not for THESE Alts!"        → 50 views, 61.6% retention (curiosity gap)
- "My Scanner Just Flagged Something Weird"     → curiosity gap winner

TITLES THAT UNDERPERFORM:
- "CRABBING MARKET? Top Crypto Setups Today!"   → 21 views (generic regime label)
- "Choppy Market, Alts Ripping!"                → led with regime word, weak hook

═══════════════════════════════════════════════════════════════════
RULES
═══════════════════════════════════════════════════════════════════
- Total narration MUST be under 180 words (fits ~60-90 seconds when spoken).
- The HOOK is the most important part — it determines whether viewers swipe away or stay.
- Use ONE of these high-retention hook patterns for every video:
  1. PATTERN INTERRUPT: "This coin just hit a perfect 10/10 on my scanner."
  2. CONTRARIAN: "Everyone's watching BTC. They're missing what's happening underneath."
  3. URGENCY: "Smart money is moving on this coin RIGHT NOW."
  4. CURIOSITY GAP: "My scanner flagged something weird this morning."
  5. SOCIAL PROOF: "Three coins my scanner caught BEFORE the breakout last week..."
  6. STAKES: "If you missed the ORCA pump, you'll want to see this."
  7. NUMBERS: "I scanned 600 coins this morning. Only 4 made the cut."

HOOK RULES (the first 3 seconds determine swipe rate):
- The hook MUST contain a concrete noun or number in the first 8 words
  (a ticker like ORCA, a score like "10.0", a count like "600 coins", "3 setups").
- The hook MUST NOT start with: "Today", "Welcome", "In this video", "Hey traders",
  "Let's", "Alright", "So", or any greeting or filler.
- If the data shows a HEADLINE PICK flag, the hook MUST name that ticker.
- If no headline pick exists, use a curiosity gap referencing the SCANNER
  (e.g. "My scanner just flagged something most traders are missing").

NARRATION STYLE:
- After the hook, lead with the single most interesting finding.
- Be punchy and conversational — not robotic. Use trader slang naturally.
- Front-load specifics: ticker → score → signal name → stat.
  Example: "JTO just hit 8.2 confluence with a volume expansion and RSI ignition."
- Avoid hedge words ("might", "could potentially", "seems to", "perhaps").
- Avoid hype adjectives ("amazing", "incredible", "huge", "massive") — let the numbers do the work.
- Every claim must come from the data provided. NEVER hallucinate stats, prices, or signals.
- Do NOT say "let's dive in", "without further ado", "today we're talking about" or any generic filler.
- Do NOT give financial advice. Use phrases like "the scanner flagged" or "signals are showing".
- End with a CTA that creates a return visit:
  "Tomorrow's pick drops at 7am" or "Drop a coin to feature next."

OUTPUT FORMAT — respond with ONLY this JSON, no markdown fences, no preamble:
{
  "title": "Title under 60 chars — see TITLE RULES below",
  "hook": "Pattern-interrupt opener (max 15 words, follows ONE of the 7 patterns above)",
  "segments": [
    {
      "coin": "BTC",
      "narration": "spoken text for this segment (2-4 sentences)",
      "stat": "+2.3% 7d",
      "visual_type": "price_chart"
    }
  ],
  "outro": "Return-visit CTA (1-2 sentences)",
  "tags": ["crypto", "bitcoin", "altcoins", "trading", ...],
  "description": "Full YouTube description — see DESCRIPTION FORMAT below"
}

SEGMENT VISUAL TYPES (pick one per segment):
- "price_chart"    — candlestick chart with signal overlays
- "heatmap"        — market overview heatmap
- "signal_stack"   — list of fired signals with conviction bar
- "stat_card"      — big number with context (BTC price, regime, etc.)

═══════════════════════════════════════════════════════════════════
TITLE RULES — sound like a real human, not a scanner output
═══════════════════════════════════════════════════════════════════

CRITICAL: The video uses a cloned human voice. The TITLE must match
that voice. Read it out loud. If it sounds like something a dashboard
would print, rewrite it.

BANNED PHRASES (never use these in the title):
- "Just Hit X.X" / "Hit X.X Confluence" / "X.X Confluence"
- "Scanner Screaming" / "Scanner Just Flagged" / "Scanner Flagged"
- "Confluence Score" / "Perfect Score"
- Generic recap phrases ("Daily Crypto Recap", "Market Update")

The word "scanner" itself is allowed but use it sparingly — at most
1 title in every 4-5 should mention "scanner". The channel brand
already covers that. Lead with the COIN or the FEELING instead.

═══════════════════════════════════════════════════════════════════
VOICE MODES — rotate based on the dominant signal in the data
═══════════════════════════════════════════════════════════════════

Pick ONE voice mode per video. Mode selection guidance:

CASUAL FRIEND MODE — use when setup is solid but not extreme
  Tone: like Discord voice chat, slightly informal, conversational
  Voice markers: "okay so", "we need to talk about", "look at",
                 "anyone else seeing"
  Examples (do NOT copy — these are the style, not templates):
    "Okay so we need to talk about COOKIE"
    "Anyone else watching what HYPER is doing?"
    "Look at SUI before it moves"
    "Quick one on ORCA before tomorrow"
    "I keep coming back to this RONIN setup"
    "JTO is the one I'd watch this week"

CONFIDENT ANALYST MODE — use when conviction is high (conf >= 10)
  Tone: opinionated but measured, like a trading journal entry
  Voice markers: "might be", "this could be", "the cleanest", "rare",
                 "I'd trust", "every time I see"
  Examples (style only — never copy verbatim):
    "This POLYX setup might be the cleanest one this month"
    "EDEN is doing something I've only seen a few times"
    "Why I'd trust this HYPE breakout"
    "Every time I see this XAG pattern, it works"
    "INJ has the rare combo I look for"
    "The KITE setup that's actually worth taking"

URGENT REACTIVE MODE — use when timing matters (24h move pending,
  perp funding flipping, breakout candle just printed)
  Tone: high-energy, leans into the discovery moment
  Voice markers: "wait", "I can't ignore", "before", "right now",
                 "almost missed", "I'm not waiting"
  Examples (style only):
    "I cannot ignore what RONIN just did"
    "Wait — look at HYPER right now"
    "I almost missed XDC this morning"
    "Before BTC moves, watch this"
    "I'm not waiting on this SQD signal"
    "This GOAT thing is happening right now"

═══════════════════════════════════════════════════════════════════
WHEN NO HEADLINE PICK EXISTS — count or curiosity, still human
═══════════════════════════════════════════════════════════════════
  "I went through 600 coins. Only 3 looked real."
  "3 setups I'm actually watching this week"
  "Most of today was noise. These 3 weren't."
  "Nobody is talking about this group of coins"

═══════════════════════════════════════════════════════════════════
HARD CONSTRAINTS
═══════════════════════════════════════════════════════════════════
- Under 60 characters (Shorts) / under 80 (landscape).
- Specific coin or specific number in the first 5 words when possible.
- One emoji MAX — only if it adds something the words don't.
- NO em-dash + score pattern ("X — 9.0" is the robot tell).
- Must contain at least ONE voice marker from the chosen mode above
  ("I", "my", "okay so", "wait", "look", "before", "might", etc).

Before you finalize the title, ask yourself: "Would I actually say
this out loud to a friend over coffee?" If not, rewrite.

═══════════════════════════════════════════════════════════════════
DESCRIPTION FORMAT — the description field MUST follow this exact structure:

🚨 [One compelling hook line about today's market]

[2-3 sentence summary of what the video covers — reference specific coins and signals]

📈 Inside the video:
• [Key point 1 — specific coin + signal]
• [Key point 2]
• [Key point 3]
• [Key point 4 if applicable]

These aren't hype indicators or lagging signals. They're repeatable patterns our scanner flags before major moves.

👇 Drop a comment with a coin you're watching and I may analyze it in the next video.

🔗 TOOLS I USE:
→ Trade on Bybit: https://bybit.com
→ TradingView charts: https://tradingview.com

⏱️ TIMESTAMPS:
[Generate timestamps based on segment count, ~10-15s per segment]

#crypto #bitcoin #cryptotrading #altcoins #trading #technicalanalysis #cryptosignals #marketupdate

AIM FOR: 1 stat_card (BTC/regime opener), 3-5 coin segments, 1 outro.
"""

SYSTEM_PROMPT_LANDSCAPE = """\
You produce YouTube video scripts for daily crypto market analysis (3-5 minutes).
Your audience: crypto traders who want a thorough but efficient daily briefing.
Goal: maximize click-through (title + thumbnail) AND watch time past 30 seconds.

═══════════════════════════════════════════════════════════════════
PERFORMANCE DATA (from this channel's actual analytics)
═══════════════════════════════════════════════════════════════════
LONG-FORM PROBLEM: Long-form videos on this channel get very low CTR (1-8 views)
but high retention when viewers DO click. This means titles and hooks are too generic.
The fix: make the title and hook concrete and specific, not vague summaries.

═══════════════════════════════════════════════════════════════════
RULES
═══════════════════════════════════════════════════════════════════
- Total narration: 400-600 words (fits ~3-4 minutes when spoken).
- Structure: hook → market overview → top setups (3-6 coins) → watchlist → outlook.
- Be analytical and specific — reference actual signal names and conviction scores.
- Every claim must come from the data provided. NEVER hallucinate.
- Do NOT give financial advice. Frame everything as "the scanner shows" or "signals suggest".

HOOK RULES (first 15 seconds keep viewers from clicking off):
- Must contain a ticker, score, or specific number in the first 12 words.
- Must NOT start with: "Welcome back", "Today", "In this video", "Hey traders", "Hello".
- Must promise a specific payoff: "I'll show you the 3 coins my scanner flagged before today's move."

NARRATION STYLE:
- Front-load specifics: ticker → confluence score → signal name → stat.
- Avoid hedge words and hype adjectives.
- Use "the scanner caught", "signals fired", "data shows" — not "I think" or "I feel".

OUTPUT FORMAT — respond with ONLY this JSON, no markdown fences, no preamble:
{
  "title": "Title under 80 chars — see TITLE RULES below",
  "hook": "opening line that frames today's narrative (max 20 words, follows HOOK RULES)",
  "segments": [
    {
      "coin": "BTC",
      "narration": "spoken text for this segment (3-6 sentences)",
      "stat": "key stat to display",
      "visual_type": "price_chart"
    }
  ],
  "outro": "closing summary + CTA (2-3 sentences)",
  "tags": ["crypto", "bitcoin", "market analysis", ...],
  "description": "Full YouTube description — see DESCRIPTION FORMAT below"
}

SEGMENT VISUAL TYPES: "price_chart", "heatmap", "signal_stack", "stat_card"

═══════════════════════════════════════════════════════════════════
TITLE RULES — sound like a real human, not a scanner output
═══════════════════════════════════════════════════════════════════

CRITICAL: The video uses a cloned human voice. The TITLE must match
that voice. Read it out loud. If it sounds like something a dashboard
would print, rewrite it.

BANNED PHRASES (never use these in the title):
- "Just Hit X.X" / "Hit X.X Confluence" / "X.X Confluence"
- "Scanner Screaming" / "Scanner Just Flagged" / "Scanner Flagged"
- "Confluence Score" / "Perfect Score"
- Generic recap phrases ("Daily Crypto Recap", "Market Update")

The word "scanner" itself is allowed but use it sparingly — at most
1 title in every 4-5 should mention "scanner". The channel brand
already covers that. Lead with the COIN or the FEELING instead.

═══════════════════════════════════════════════════════════════════
VOICE MODES — rotate based on the dominant signal in the data
═══════════════════════════════════════════════════════════════════

Pick ONE voice mode per video. Mode selection guidance:

CASUAL FRIEND MODE — use when setup is solid but not extreme
  Tone: relaxed, conversational, like talking to another trader
  Voice markers: "okay so", "we need to talk about", "look at",
                 "honestly", "I keep coming back to"
  Examples (style only — do not copy):
    "Okay so we need to talk about this week in crypto"
    "I keep coming back to these 3 setups"
    "Honestly the only coins worth watching right now"
    "What's actually setting up for next week"

CONFIDENT ANALYST MODE — use when conviction is high
  Tone: opinionated, measured, like a trading journal entry
  Voice markers: "might be", "the cleanest", "rare", "I'd trust",
                 "every time I see"
  Examples (style only):
    "These 3 setups might be the cleanest of the month"
    "Every signal I trust just fired on the same coin"
    "The rare combo I've been waiting for"
    "Why this week's picks are different"

URGENT REACTIVE MODE — use when timing matters
  Tone: high-energy, leans into the discovery moment
  Voice markers: "wait", "I cannot ignore", "before", "right now",
                 "almost missed", "I'm not waiting"
  Examples (style only):
    "I cannot ignore what just happened in alts"
    "Wait — look at what these 3 coins did today"
    "Before next week opens, watch these 3"
    "I almost missed all three of these"

═══════════════════════════════════════════════════════════════════
HARD CONSTRAINTS
═══════════════════════════════════════════════════════════════════
- Under 80 characters.
- Specific coins or numbers in the first 5 words when possible.
- One emoji MAX — only if it adds something the words don't.
- NO em-dash + score pattern ("X — 9.0" is the robot tell).
- Must contain at least ONE voice marker from the chosen mode above.

Before you finalize the title, ask yourself: "Would I actually say
this out loud to a friend over coffee?" If not, rewrite.

═══════════════════════════════════════════════════════════════════
DESCRIPTION FORMAT — the description field MUST follow this exact structure:

🚨 [One compelling hook line about today's market action]

[2-3 sentence summary — reference specific coins, signals, and market regime]

📈 Inside the video:
• [Key setup 1 — coin + signal + stat]
• [Key setup 2]
• [Key setup 3]
• [Key setup 4]
• [Any warnings about extended coins]

These aren't opinions or hype. Our multi-scanner system flags repeatable patterns before major moves — across 600+ coins on Bybit and Binance.

👇 Drop a comment with a coin you're watching and I may analyze it in the next video.

🔗 TOOLS I USE:
→ Trade on Bybit: https://bybit.com
→ TradingView charts: https://tradingview.com

⏱️ TIMESTAMPS:
[Generate timestamps based on segments, estimate ~30-45s per segment]

#crypto #bitcoin #cryptotrading #altcoins #trading #technicalanalysis #cryptosignals #marketupdate #dailycrypto
"""


# ─────────────────────────────────────────────────────────────────────────────
# PROVIDER DETECTION  — first key found wins
# ─────────────────────────────────────────────────────────────────────────────

PROVIDERS = [
    # (env_var,            provider_name, default_model)
    ("XAI_API_KEY",        "grok",    "grok-4-1-fast-non-reasoning"),
    ("GEMINI_API_KEY",     "gemini",  "gemini-2.5-flash"),
    ("ANTHROPIC_API_KEY",  "claude",  "claude-sonnet-4-20250514"),
    ("OPENAI_API_KEY",     "openai",  "gpt-4o-mini"),
]


def _detect_provider() -> tuple[str, str, str]:
    """
    Auto-detect which provider to use based on available env vars.
    Returns (provider_name, api_key, default_model).
    """
    # Allow explicit override
    forced = os.environ.get("SCRIPT_PROVIDER", "").lower()
    if forced:
        for env_var, name, model in PROVIDERS:
            if name == forced:
                key = os.environ.get(env_var, "")
                if key:
                    return name, key, model
                raise EnvironmentError(
                    f"SCRIPT_PROVIDER={forced} but {env_var} is not set"
                )
        raise EnvironmentError(
            f"Unknown SCRIPT_PROVIDER={forced}. Options: grok, gemini, claude, openai"
        )

    # Auto-detect: first key found wins
    for env_var, name, model in PROVIDERS:
        key = os.environ.get(env_var, "")
        if key:
            return name, key, model

    raise EnvironmentError(
        "No LLM API key found. Set one of:\n"
        "  XAI_API_KEY        — Grok (free at console.x.ai)\n"
        "  GEMINI_API_KEY     — Gemini (free at aistudio.google.com)\n"
        "  ANTHROPIC_API_KEY  — Claude (paid at console.anthropic.com)\n"
        "  OPENAI_API_KEY     — OpenAI (paid at platform.openai.com)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def generate_script(
    summary:    dict,
    landscape:  bool = False,
    provider:   Optional[str] = None,
    model:      Optional[str] = None,
) -> dict:
    """
    Generate a narration script using whichever LLM API is available.

    Provider priority (auto): Grok -> Gemini -> Claude -> OpenAI
    Override with provider= or SCRIPT_PROVIDER env var.

    Args:
        summary:   Market summary from ingest.build_market_summary()
        landscape: If True, use longer-form landscape prompt
        provider:  Force a specific provider ("grok", "gemini", "claude", "openai")
        model:     Override the default model for the chosen provider

    Returns:
        Parsed script dict with title, hook, segments, outro, tags, description
    """
    if provider:
        os.environ["SCRIPT_PROVIDER"] = provider

    prov_name, api_key, default_model = _detect_provider()
    model = model or default_model

    system = SYSTEM_PROMPT_LANDSCAPE if landscape else SYSTEM_PROMPT_SHORTS
    user_content = _build_user_message(summary)

    log.info(f"  Using provider: {prov_name} (model={model})")

    # Dispatch to the right backend, with automatic retry on transient errors
    raw_text = _call_llm_with_retry(prov_name, api_key, model, system, user_content)

    # Parse and validate
    script = _parse_script(raw_text)

    log.info(f"  Script generated: {len(script['segments'])} segments, "
             f"~{_word_count(script)} words")
    return script


# ─────────────────────────────────────────────────────────────────────────────
# RETRY WRAPPER — transient network errors retry up to 3 times
# ─────────────────────────────────────────────────────────────────────────────

# Errors that indicate a TRANSIENT network blip — worth retrying
_TRANSIENT_ERROR_PHRASES = (
    "ssl",                                    # SSLError, SSLEOFError
    "eof occurred",                           # SSLEOFError specifically
    "max retries exceeded",                   # urllib3 max retries
    "connection aborted",
    "connection reset",
    "connection refused",
    "remote end closed",
    "read timed out",
    "timeout",
    "temporarily unavailable",                # 503
    "bad gateway",                            # 502
    "service unavailable",                    # 503
    "gateway timeout",                        # 504
)

# HTTP status codes that are TRANSIENT (server-side, retry might succeed)
_TRANSIENT_STATUS_CODES = (500, 502, 503, 504, 408, 429)


def _is_transient_error(exc: Exception) -> bool:
    """Decide if an exception is a transient network issue worth retrying."""
    msg = str(exc).lower()
    if any(phrase in msg for phrase in _TRANSIENT_ERROR_PHRASES):
        return True
    # Check for HTTPError with transient status code
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in _TRANSIENT_STATUS_CODES:
        return True
    return False


def _call_llm_with_retry(prov_name: str, api_key: str, model: str,
                          system: str, user_content: str,
                          max_attempts: int = 3) -> str:
    """
    Dispatch to the LLM backend with automatic retry on transient errors.

    Retries up to max_attempts times with exponential backoff (5s, 15s, 45s).
    Only retries on transient errors (SSL handshake, timeouts, 5XX). Permanent
    errors (400, 401, 404) raise immediately.

    This rescues runs from short network blips like the SSL handshake glitch
    that killed the May 18 2026 daily Short — by the time it retries 5s later,
    most transient issues have already cleared.
    """
    import time as _time

    backoff_seconds = [5, 15, 45]  # cumulative wait between attempts

    for attempt in range(1, max_attempts + 1):
        try:
            if prov_name == "grok":
                return _call_grok(api_key, model, system, user_content)
            elif prov_name == "gemini":
                return _call_gemini(api_key, model, system, user_content)
            elif prov_name == "claude":
                return _call_claude(api_key, model, system, user_content)
            elif prov_name == "openai":
                return _call_openai(api_key, model, system, user_content)
            else:
                raise ValueError(f"Unknown provider: {prov_name}")
        except Exception as e:
            is_last = (attempt >= max_attempts)
            transient = _is_transient_error(e)

            if not transient:
                # Permanent error — don't retry, re-raise immediately
                log.error(f"  LLM call failed with non-transient error: {e}")
                raise

            if is_last:
                # Out of retries
                log.error(f"  LLM call failed after {max_attempts} attempts. "
                          f"Last error: {e}")
                raise

            # Transient + retries remaining — wait and try again
            wait_s = backoff_seconds[attempt - 1]
            log.warning(f"  LLM call attempt {attempt}/{max_attempts} failed "
                        f"with transient error: {type(e).__name__}: "
                        f"{str(e)[:100]}")
            log.warning(f"  Retrying in {wait_s}s...")
            _time.sleep(wait_s)

    # Should never reach here, but defensively raise
    raise RuntimeError(f"LLM call exhausted all retries without success")


# ─────────────────────────────────────────────────────────────────────────────
# PROVIDER BACKENDS
# ─────────────────────────────────────────────────────────────────────────────

def _call_grok(api_key: str, model: str, system: str, user_msg: str) -> str:
    """
    Grok via x.ai API (OpenAI-compatible endpoint).

    Grok-4-family models require max_completion_tokens, not max_tokens.
    Free tier: console.x.ai -> API keys -> create key
    $25/month free credit.
    """
    import requests

    log.info(f"  Calling Grok ({model})...")

    # Use max_completion_tokens for grok-4-family (reasoning models),
    # max_tokens for older grok-3-family.
    if model.startswith("grok-4") or "fast" in model or "reasoning" in model:
        token_param = "max_completion_tokens"
    else:
        token_param = "max_tokens"

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user_msg},
        ],
        token_param: 8000,
        "temperature": 0.7,
    }

    resp = requests.post(
        "https://api.x.ai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=180,  # Grok-4 reasoning calls can be slow
    )

    # On error, surface the actual x.ai error body so we can debug
    if resp.status_code >= 400:
        try:
            err_body = resp.json()
        except Exception:
            err_body = resp.text
        log.error(f"  Grok API {resp.status_code}: {err_body}")
        resp.raise_for_status()

    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def _call_gemini(api_key: str, model: str, system: str, user_msg: str) -> str:
    """
    Google Gemini via REST API.

    Free tier: aistudio.google.com -> Get API key
    15 RPM / 1M tokens per day on free tier — plenty for 1 script/day.
    """
    import requests
    import time as _time

    # Try multiple models in case one is down
    models_to_try = [model, "gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"]
    # Deduplicate while preserving order
    seen = set()
    models_to_try = [m for m in models_to_try if not (m in seen or seen.add(m))]

    last_error = None
    for attempt_model in models_to_try:
        for attempt in range(3):  # up to 3 retries per model
            log.info(f"  Calling Gemini ({attempt_model})... (attempt {attempt+1})")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{attempt_model}:generateContent"

            try:
                resp = requests.post(
                    url,
                    params={"key": api_key},
                    headers={"Content-Type": "application/json"},
                    json={
                        "system_instruction": {
                            "parts": [{"text": system}]
                        },
                        "contents": [
                            {
                                "role": "user",
                                "parts": [{"text": user_msg}],
                            }
                        ],
                        "generationConfig": {
                            "maxOutputTokens": 8192,
                            "temperature": 0.7,
                        },
                    },
                    timeout=60,
                )

                if resp.status_code == 503:
                    log.warning(f"  503 Service Unavailable on {attempt_model} — retrying in 15s...")
                    last_error = f"503 on {attempt_model}"
                    _time.sleep(15)
                    continue
                elif resp.status_code == 429:
                    log.warning(f"  429 Rate limited on {attempt_model} — waiting 30s then trying next model...")
                    last_error = f"429 on {attempt_model}"
                    _time.sleep(30)
                    break  # skip to next model
                
                resp.raise_for_status()
                data = resp.json()

                # Gemini response structure
                candidates = data.get("candidates", [])
                if not candidates:
                    raise ValueError(f"Gemini returned no candidates: {data}")

                parts = candidates[0].get("content", {}).get("parts", [])
                if not parts:
                    raise ValueError(f"Gemini returned empty parts: {data}")

                return parts[0].get("text", "").strip()

            except requests.exceptions.HTTPError as e:
                last_error = str(e)
                log.warning(f"  HTTP error: {e}")
                break  # try next model

    raise RuntimeError(
        f"All Gemini models failed. Last error: {last_error}\n"
        "Google's API may be temporarily down. Try again in a few minutes."
    )


def _call_claude(api_key: str, model: str, system: str, user_msg: str) -> str:
    """Claude via Anthropic API (paid)."""
    try:
        import anthropic
    except ImportError:
        raise ImportError("anthropic package required: pip install anthropic")

    log.info(f"  Calling Claude ({model})...")
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    return msg.content[0].text.strip()


def _call_openai(api_key: str, model: str, system: str, user_msg: str) -> str:
    """OpenAI via official API (paid)."""
    import requests

    log.info(f"  Calling OpenAI ({model})...")
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user_msg},
            ],
            "max_tokens": 2000,
            "temperature": 0.7,
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


# ─────────────────────────────────────────────────────────────────────────────
# PARSING & VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

def _parse_script(raw_text: str) -> dict:
    """Parse and validate the LLM's JSON output."""

    # Strip markdown fences if the model included them
    text = raw_text.strip()
    if text.startswith("```"):
        # Remove opening fence (```json or ```)
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    try:
        script = json.loads(text)
    except json.JSONDecodeError:
        # Attempt to repair truncated JSON
        log.warning("  JSON truncated — attempting repair...")
        script = _repair_truncated_json(text)
        if script is None:
            log.error(f"  Failed to repair JSON")
            log.error(f"  Raw response:\n{text[:1500]}")
            raise ValueError("LLM returned invalid JSON that could not be repaired")

    # Validate required fields
    required = {"title", "hook", "segments", "outro"}
    missing = required - set(script.keys())
    if missing:
        # If we have segments but missing outro, add a default
        if "outro" in missing and "segments" in script:
            script["outro"] = "Like and subscribe for daily crypto scanner updates!"
            missing.discard("outro")
        if missing:
            raise ValueError(f"Script missing required fields: {missing}")

    if not script.get("segments"):
        raise ValueError("Script has no segments")

    # Ensure segments have required subfields
    for i, seg in enumerate(script["segments"]):
        if "narration" not in seg:
            raise ValueError(f"Segment {i} missing 'narration' field")
        seg.setdefault("coin", "MARKET")
        seg.setdefault("stat", "")
        seg.setdefault("visual_type", "stat_card")

    # Ensure tags is a list
    if isinstance(script.get("tags"), str):
        script["tags"] = [t.strip() for t in script["tags"].split(",")]
    script.setdefault("tags", ["crypto", "bitcoin", "altcoins", "trading"])

    # Add description fallback
    script.setdefault("description", "")

    return script


def _repair_truncated_json(text: str) -> dict | None:
    """
    Attempt to repair JSON that was cut off mid-stream.
    Common with LLMs hitting token limits.
    """
    import re

    # Strategy 1: Try to find the last complete segment and close the JSON
    # Look for the last complete "}" that closes a segment
    try:
        # Find where segments array content ends cleanly
        # We need: segments array closed, then outro + tags + description
        
        # First, try progressively trimming from the end
        # Remove incomplete trailing content and try to close brackets
        for trim in range(len(text) - 1, max(0, len(text) - 500), -1):
            chunk = text[:trim].rstrip().rstrip(",")
            
            # Count unclosed brackets
            open_braces = chunk.count("{") - chunk.count("}")
            open_brackets = chunk.count("[") - chunk.count("]")
            
            # Try closing them
            suffix = "]" * open_brackets + "}" * open_braces
            candidate = chunk + suffix
            
            try:
                result = json.loads(candidate)
                if isinstance(result, dict) and "segments" in result:
                    log.info("  JSON repair succeeded (trimmed + closed brackets)")
                    return result
            except json.JSONDecodeError:
                continue

    except Exception:
        pass

    # Strategy 2: Extract what we can with regex
    try:
        title_m = re.search(r'"title"\s*:\s*"([^"]*)"', text)
        hook_m = re.search(r'"hook"\s*:\s*"([^"]*)"', text)
        outro_m = re.search(r'"outro"\s*:\s*"([^"]*)"', text)

        # Extract complete segment objects
        seg_pattern = r'\{\s*"coin"\s*:\s*"([^"]*)"\s*,\s*"narration"\s*:\s*"([^"]*)"\s*,\s*"stat"\s*:\s*"([^"]*)"\s*,\s*"visual_type"\s*:\s*"([^"]*)"\s*\}'
        segments = []
        for m in re.finditer(seg_pattern, text):
            segments.append({
                "coin": m.group(1),
                "narration": m.group(2),
                "stat": m.group(3),
                "visual_type": m.group(4),
            })

        if segments:
            log.info(f"  JSON repair succeeded (regex extracted {len(segments)} segments)")
            return {
                "title": title_m.group(1) if title_m else "Crypto Scanner Daily Recap",
                "hook": hook_m.group(1) if hook_m else "Here are today's top scanner picks",
                "segments": segments,
                "outro": outro_m.group(1) if outro_m else "Like and subscribe for daily updates!",
                "tags": ["crypto", "bitcoin", "altcoins", "trading"],
                "description": "",
            }
    except Exception:
        pass

    return None


# ─────────────────────────────────────────────────────────────────────────────
# USER MESSAGE BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _build_user_message(summary: dict) -> str:
    """Format the market summary into a clear prompt."""

    lines = [
        f"Date: {summary['date']} {summary.get('time_utc', '')}",
        f"Market regime: {summary['regime']}",
    ]

    if summary.get("btc_price"):
        lines.append(f"BTC price: ${summary['btc_price']:,.0f}")
    if summary.get("btc_7d_pct") is not None:
        lines.append(f"BTC 7-day change: {summary['btc_7d_pct']:+.1f}%")

    if summary.get("warnings"):
        lines.append(f"Scanner warnings: {'; '.join(summary['warnings'][:3])}")

    # ── HEADLINE PICK detection (drives the title & hook) ────────────────────
    # If the #1 coin has a high confluence score, flag it for the LLM so it
    # uses the ticker in the title/hook instead of a generic regime title.
    top_coins = summary.get("top_coins", [])
    headline_pick = None
    if top_coins:
        hero = top_coins[0]
        hero_conf = hero.get("confluence", 0) or 0
        # Headline-worthy if confluence >= 8.0 OR confluence is >=1.5x the runner-up
        runner_conf = (top_coins[1].get("confluence", 0) or 0) if len(top_coins) > 1 else 0
        is_dominant = hero_conf >= 8.0 or (
            runner_conf > 0 and hero_conf >= 1.5 * runner_conf and hero_conf >= 6.0
        )
        if is_dominant:
            headline_pick = hero

    lines.append("")
    if headline_pick:
        lines.append(
            f">>> HEADLINE PICK: {headline_pick['symbol']} "
            f"(confluence={headline_pick.get('confluence', 0):.1f}) <<<"
        )
        lines.append(
            f">>> The title and hook MUST feature {headline_pick['symbol']} prominently. "
            f"Do NOT lead with the market regime. <<<"
        )
    else:
        lines.append(
            ">>> NO HEADLINE PICK — no single coin dominates. <<<"
        )
        lines.append(
            ">>> Use a SCANNER-curiosity-gap title (e.g. 'My Scanner Just Flagged Something Weird') "
            ">>> OR a count-based title ('I Scanned 600 Coins — Only 3 Passed'). <<<"
        )
        lines.append(
            ">>> Do NOT lead with the market regime word. <<<"
        )

    lines.append("")
    lines.append("=== TOP SCANNER PICKS (by confluence score) ===")

    for i, coin in enumerate(top_coins, 1):
        parts = [
            f"#{i} {coin['symbol']}",
            f"confluence={coin.get('confluence', 0):.1f}",
            f"bucket={coin.get('bucket', '?')}",
            f"scanners={coin.get('scanners', 1)}/4",
        ]
        if coin.get("price"):
            parts.append(f"price=${coin['price']:.6f}")
        if coin.get("change_24h") is not None:
            parts.append(f"24h={coin['change_24h']:+.1f}%")
        if coin.get("volume_24h"):
            parts.append(f"vol=${coin['volume_24h']/1e6:.1f}M")
        if coin.get("signals"):
            parts.append(f"signals=[{', '.join(coin['signals'][:5])}]")

        lines.append("  ".join(parts))

    if summary.get("extended_coins"):
        lines.append("")
        lines.append("=== ALREADY EXTENDED (up >8% today — potentially late entry) ===")
        for coin in summary["extended_coins"][:5]:
            lines.append(
                f"  {coin['symbol']}  24h={coin.get('change_24h', 0):+.1f}%  "
                f"confluence={coin.get('confluence', 0):.1f}"
            )

    if summary.get("ignition_watch_now"):
        lines.append("")
        lines.append("=== IGNITION WATCH NOW (early accumulation signals) ===")
        for coin in summary["ignition_watch_now"][:5]:
            sigs = ", ".join(coin.get("signals", [])[:4])
            lines.append(
                f"  {coin['symbol']}  conv={coin.get('conviction', 0):.0f}  "
                f"24h={coin.get('change_24h', 0):+.1f}%  [{sigs}]"
            )

    lines.append("")
    lines.append("Generate the video script based on this data.")
    lines.append("Remember: follow the TITLE RULES strictly. Specificity beats generic regime labels.")

    return "\n".join(lines)


def _word_count(script: dict) -> int:
    """Rough word count of all narration text."""
    count = 0
    count += len(script.get("hook", "").split())
    for seg in script.get("segments", []):
        count += len(seg.get("narration", "").split())
    count += len(script.get("outro", "").split())
    return count
