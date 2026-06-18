"""
================================================================================
WEEKLY PIPELINE  v1.5  —  Friday Weekend Setups Video (fully automated)
================================================================================
CHANGES vs v1.4:
  - Fixed: trade_plan now correctly parses 'take_profits' array (the real
    schema) and exposes flat tp1/tp2/tp3 fields to the LLM. Previously the
    LLM saw "n/a" because the code looked for non-existent tp1/tp2/tp3 keys.
  - Prompt: hook and first segment no longer duplicate (now distinct in
    purpose — hook is teaser, segment 1 is full context).
  - Prompt: risk + CTA word targets clarified; LLM more likely to hit them.
  - Prompt: title rule encourages variety when all 3 coins tie at same conf.
================================================================================
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ingest import load_scanner_data, build_market_summary
from scriptgen import generate_script
from voiceover import generate_voiceover
from visuals import render_all_frames
from compose import compose_video
from thumbnail import generate_thumbnail
from upload import upload_to_youtube, get_youtube_credentials

# ─────────────────────────────────────────────────────────────────────────────
# DEFAULT PROVIDER — Grok (higher token limits than Gemini)
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_PROVIDER = "grok"

# ─────────────────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────────────────
_THIS_DIR     = Path(__file__).resolve().parent
_YT_DIR       = _THIS_DIR.parent
_PROJECT_ROOT = _YT_DIR.parent

_SCANNER_OUT  = _PROJECT_ROOT / "outputs" / "scanner-results"
_VIDEO_OUT    = _YT_DIR / "Videos"
_VOICE_OUT    = _YT_DIR / "Voice-Overs"
_SCRIPT_OUT   = _YT_DIR / "Video Scripts"
_FRAMES_OUT   = _YT_DIR / "Images for Videos"
_BGM_DIR      = _YT_DIR / "Content"
_LOG_DIR      = _PROJECT_ROOT / "outputs" / "logs"

for d in (_VIDEO_OUT, _VOICE_OUT, _SCRIPT_OUT, _FRAMES_OUT, _LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
log = logging.getLogger("weekly_pipeline")
if not log.handlers:
    hf = logging.FileHandler(
        _LOG_DIR / f"weekly_pipeline_{datetime.now().strftime('%Y%m%d')}.log",
        encoding="utf-8",
    )
    hf.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    hs = logging.StreamHandler(sys.stdout)
    hs.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(hf)
    log.addHandler(hs)
    log.setLevel(logging.INFO)


# ─────────────────────────────────────────────────────────────────────────────
# LLM PROMPT — v1.5 refinements
# ─────────────────────────────────────────────────────────────────────────────

WEEKLY_SYSTEM_PROMPT = """\
You produce a Friday "Weekend Setups" YouTube video script for an active
crypto-trading audience. Length: 6-8 minutes (1000-1300 words of narration).

CONTEXT — This is a WEEKLY video, posted Fridays. The 3 setups featured were
aggregated from the past 7 days of scanner output. Each coin has a
"weekly_confluence_max" (highest score seen all week) and "appearances"
(how many of the past 7 daily scans surfaced it). These are PERSISTENT
setups, not one-day spikes.

═══════════════════════════════════════════════════════════════════════════
🔒 ANTI-HALLUCINATION RULES (NON-NEGOTIABLE)
═══════════════════════════════════════════════════════════════════════════

The user message contains an "AUTHORIZED FACTS" block with structured data
for each coin. You may ONLY reference facts that appear inside that block.

YOU MAY NOT:
  - Invent signal names. ONLY use signal strings that appear verbatim in the
    "signals" list for that coin. If a coin's signals list has 3 items,
    mention those 3. Don't add a 4th.
  - Invent prices. Use exact "price", "entry", "stop", "tp1", "tp2", "tp3"
    numbers from the data. Round to 4 significant figures when speaking
    aloud (e.g. $86.48 stays "$86.48"; $0.19657 becomes "$0.197").
  - Invent BTC price. If "btc_price" is null/unknown, say "Bitcoin in the
    current range" — do NOT make up a specific number.
  - Invent 7d % changes, 24h % changes, volumes, or volatility figures.

YOU MUST:
  - For each setup, quote the actual signals from that coin's "signals" array.
  - For each setup, state the actual entry, stop, AND ALL THREE TPs from
    "trade_plan". TPs are real numbers — use them. Format as:
    "Entry $86.48, stop $82.16. TP1 $92.97 for a 7.5% move, TP2 $99.45 at
    15%, TP3 $108.10 at 25%."
  - Reference the EXACT "appearances" count.
  - Use the actual MAX confluence score.

═══════════════════════════════════════════════════════════════════════════
STRUCTURE — 8 segments, in this exact order
═══════════════════════════════════════════════════════════════════════════

1. HOOK (15-20s, 40-60 words)
   - The hook is a TEASER, not a summary. Distinct from segment 1.
   - First 10 words MUST contain a concrete number or ticker.
   - End with curiosity, not answers. Example pattern:
     "Three coins. Same setup. Same score. My scanner caught them all week —
     here's the one that's about to break first."
   - DO NOT just restate setup #1 verbatim. DO NOT use the same opening
     sentence as segment 1.
   - DO NOT open with "Welcome", "Today", "In this video", "Hey traders"

2. MARKET REGIME (45-60s, 120-160 words)
   - State regime (BULL/SIDEWAYS/BEAR), BTC price, 7d %
   - Weekend framing: liquidity drops 25-40%, position sizing matters more
   - One sentence on what kind of moves to expect in this regime
   - "stat_card" visual

3. SETUP #1 — HIGHEST WEEKLY CONFLUENCE (90-120s, 250-330 words)
   - Lead with the coin's exact symbol and weekly_confluence_max
   - State exact appearances count ("on the scanner X of the last 7 days")
   - Walk through ONLY the signals in the "signals" array
   - For each signal, briefly explain what it means in plain English
   - State exact entry/stop/TP1/TP2/TP3 from trade_plan
   - Position size guidance: "weekend volume is lower, reduce size to half"
   - Invalidation: state the EXACT stop price from trade_plan
   - "price_chart" visual

4. SETUP #2 — SECOND HIGHEST (90-120s, 250-330 words)
   - Same structure as Setup #1, using SETUP #2's data

5. SETUP #3 — THIRD HIGHEST (90-120s, 250-330 words)
   - Same structure as Setup #1, using SETUP #3's data

6. WEEKEND RISK MANAGEMENT (30-45s, 80-110 words — HIT THIS RANGE)
   - Lower liquidity = wider spreads, easier wicks
   - Why position sizing matters more on weekends (specific: half size)
   - Set alerts at entries/stops, don't screen-stare for 48 hours
   - One concrete tip: e.g. "stagger limit orders below entry — wicks
     fill them at better prices than market orders"
   - "signal_stack" visual

7. WHAT WOULD INVALIDATE ALL 3 (30-45s, 80-110 words — HIT THIS RANGE)
   - Reference each coin's EXACT stop price
   - One BTC level to watch (round number near current BTC price)
   - One macro event (Sunday CME gap, Monday Asia open)
   - Close with "this is when I close all three"
   - "stat_card" visual

8. OUTRO / CTA (15-20s, 40-60 words — HIT THIS RANGE)
   - Tease Monday's recap: "next video tracks how these played out"
   - Ask which coin they're watching
   - Casual subscribe ask
   - Close with one short memorable line

═══════════════════════════════════════════════════════════════════════════
TITLE RULES
═══════════════════════════════════════════════════════════════════════════
- Under 70 characters
- Pick the pattern that fits today's data:
  • Default: "3 Crypto Setups for the Weekend ({REGIME})"
  • If #1 coin has appearances >= 5: "Why My Scanner Keeps Flagging {TICKER1} — Plus 2 More ({REGIME})"
  • If all 3 coins tie at same confluence: "3 Coins, Same Score: My Weekend Setups ({REGIME})"
  • If #1 coin is mid-cap or under-the-radar: "The {TICKER1} Setup Most Traders Missed ({REGIME})"
- DO NOT lead with "Weekly", "Daily", "My Top", "Best"

═══════════════════════════════════════════════════════════════════════════
NARRATION STYLE
═══════════════════════════════════════════════════════════════════════════
- Analytical, not hype. Intermediate-to-advanced trader audience.
- Front-load specifics: "INJ scored 8.0 with funding_negative and vol_oi_surge firing."
- Avoid hedge words: "might", "could", "perhaps".
- Avoid hype: "incredible", "massive", "huge", "insane".
- Use: "the scanner flagged", "the data shows", "the signal fired".
- Never give financial advice — analysis only.

═══════════════════════════════════════════════════════════════════════════
OUTPUT FORMAT — respond with ONLY this JSON, no markdown fences:
═══════════════════════════════════════════════════════════════════════════
{
  "title": "title under 70 chars",
  "hook": "TEASER opening — must be DIFFERENT wording from segment 1 (max 25 words)",
  "segments": [
    {"coin": "MARKET", "narration": "hook (40-60 words) — different from `hook` field above", "stat": "", "visual_type": "stat_card"},
    {"coin": "MARKET", "narration": "regime overview (120-160 words)", "stat": "BTC $X · 7d ±X%", "visual_type": "stat_card"},
    {"coin": "TICKER1", "narration": "setup walkthrough with full trade plan (250-330 words)", "stat": "max conf 8.0 · 3/7 days", "visual_type": "price_chart"},
    {"coin": "TICKER2", "narration": "setup walkthrough with full trade plan (250-330 words)", "stat": "max conf 8.0 · 3/7 days", "visual_type": "price_chart"},
    {"coin": "TICKER3", "narration": "setup walkthrough with full trade plan (250-330 words)", "stat": "max conf 8.0 · 3/7 days", "visual_type": "price_chart"},
    {"coin": "RISK", "narration": "weekend risk MUST be 80-110 words", "stat": "", "visual_type": "signal_stack"},
    {"coin": "INVALIDATION", "narration": "invalidation MUST be 80-110 words, mentions all 3 stops", "stat": "", "visual_type": "stat_card"},
    {"coin": "CTA", "narration": "outro MUST be 40-60 words", "stat": "", "visual_type": "stat_card"}
  ],
  "outro": "same as last segment narration",
  "tags": ["crypto", "weekend setups", "bitcoin", "altcoins", "TICKER1", "TICKER2", "TICKER3"],
  "description": "see DESCRIPTION FORMAT in user message"
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# AUTHORIZED-FACTS USER MESSAGE BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _flatten_trade_plan(tp_raw: dict) -> dict:
    """
    Normalize the scanner's trade_plan shape into flat tp1/tp2/tp3 fields.

    Scanner shape:
        {
          "entry": 86.48, "stop": 82.156, "stop_pct": -5.0,
          "take_profits": [
            {"price": 92.966, "gain_pct": 7.5, "rr": 1.5, "sell_pct": 40},
            {"price": 99.452, "gain_pct": 15.0, ...},
            {"price": 108.1,  "gain_pct": 25.0, ...}
          ]
        }

    Returns:
        {
          "entry": 86.48, "stop": 82.156, "stop_pct": -5.0,
          "tp1": 92.966, "tp1_pct": 7.5, "tp1_rr": 1.5,
          "tp2": 99.452, "tp2_pct": 15.0, "tp2_rr": 3.0,
          "tp3": 108.1,  "tp3_pct": 25.0, "tp3_rr": 5.0,
          "risk_pct": 0.75, "pos_pct": 15.0,
        }

    Backwards-compat: if tp_raw already has flat tp1/tp2/tp3 keys, just
    pass them through.
    """
    if not tp_raw or not isinstance(tp_raw, dict):
        return {}

    flat = {
        "entry":    tp_raw.get("entry"),
        "stop":     tp_raw.get("stop"),
        "stop_pct": tp_raw.get("stop_pct"),
        "risk_pct": tp_raw.get("risk_pct"),
        "pos_pct":  tp_raw.get("pos_pct"),
    }

    # Pass-through case (already flat)
    if "tp1" in tp_raw or "tp2" in tp_raw or "tp3" in tp_raw:
        flat["tp1"]     = tp_raw.get("tp1")
        flat["tp1_pct"] = tp_raw.get("tp1_pct")
        flat["tp1_rr"]  = tp_raw.get("tp1_rr")
        flat["tp2"]     = tp_raw.get("tp2")
        flat["tp2_pct"] = tp_raw.get("tp2_pct")
        flat["tp2_rr"]  = tp_raw.get("tp2_rr")
        flat["tp3"]     = tp_raw.get("tp3")
        flat["tp3_pct"] = tp_raw.get("tp3_pct")
        flat["tp3_rr"]  = tp_raw.get("tp3_rr")
        return flat

    # Real case: take_profits is an array of {price, gain_pct, rr, sell_pct}
    tps = tp_raw.get("take_profits") or []
    for i, tp in enumerate(tps[:3], start=1):
        if not isinstance(tp, dict):
            continue
        flat[f"tp{i}"]     = tp.get("price")
        flat[f"tp{i}_pct"] = tp.get("gain_pct")
        flat[f"tp{i}_rr"]  = tp.get("rr")

    return flat


def _build_weekly_user_message(summary: dict) -> str:
    coins = summary.get("top_coins", [])
    regime = summary.get("regime", "unknown").upper()
    btc_price = summary.get("btc_price")
    btc_7d = summary.get("btc_7d_pct")

    btc_str = f"${btc_price:,.0f}" if btc_price else "unknown"
    btc_7d_str = f"{btc_7d:+.2f}%" if btc_7d is not None else "unknown"

    lines = [
        "Generate the Friday weekly weekend-setups script using ONLY the",
        "facts in the AUTHORIZED FACTS block below.",
        "",
        "═══════════════════════════════════════════════════════════════════",
        "AUTHORIZED FACTS — your only source of truth",
        "═══════════════════════════════════════════════════════════════════",
        "",
        f"MARKET CONTEXT:",
        f"  regime: {regime}",
        f"  btc_price: {btc_str}",
        f"  btc_7d_pct: {btc_7d_str}",
        f"  warnings: {summary.get('warnings', [])}",
        "",
    ]

    for idx, c in enumerate(coins, 1):
        signals = c.get("signals", []) or []
        tp = _flatten_trade_plan(c.get("trade_plan"))

        lines.append(f"SETUP #{idx}:")
        lines.append(f"  symbol: {c.get('symbol', '?')}")
        lines.append(f"  weekly_confluence_max: {c.get('weekly_confluence_max', 0):.1f}")
        lines.append(f"  appearances: {c.get('appearances', 1)} of the last 7 days")
        lines.append(f"  days_seen: {c.get('days_seen', [])}")
        lines.append(f"  best_day: {c.get('best_day', '?')}")
        lines.append(f"  bucket: {c.get('bucket', '?')}")
        lines.append(f"  current_price: {c.get('price') or 'unknown'}")
        lines.append(f"  change_24h_pct: {c.get('change_24h', 0):+.2f}")
        lines.append(f"  signals (USE ONLY THESE — do not add others):")
        if signals:
            for s in signals:
                lines.append(f"    - {s}")
        else:
            lines.append(f"    (no signals listed)")

        # Print trade plan with concrete TPs (no more "n/a")
        lines.append(f"  trade_plan (use these exact numbers):")
        if tp:
            lines.append(f"    entry: {tp.get('entry', 'n/a')}")
            lines.append(f"    stop:  {tp.get('stop', 'n/a')} ({tp.get('stop_pct', 'n/a')}%)")
            for n in (1, 2, 3):
                price = tp.get(f"tp{n}")
                pct   = tp.get(f"tp{n}_pct")
                rr    = tp.get(f"tp{n}_rr")
                if price is not None:
                    lines.append(f"    tp{n}:   {price}  ({pct}% gain, R:R {rr})")
                else:
                    lines.append(f"    tp{n}:   not available")
        else:
            lines.append(f"    (no trade_plan — say 'scanner didn't generate a trade plan; "
                         f"entry near current with tight stop below recent support')")
        lines.append("")

    lines.extend([
        "═══════════════════════════════════════════════════════════════════",
        "END OF AUTHORIZED FACTS",
        "═══════════════════════════════════════════════════════════════════",
        "",
        "DESCRIPTION FORMAT — use this exact structure in the description field:",
        "",
        "🚨 Three crypto setups my scanner kept flagging all week.",
        "",
        "[2-3 sentence summary: regime, BTC, the 3 coins, and how many days each appeared.]",
        "",
        "📈 Inside the video:",
        "• Market regime: [REGIME] · BTC [PRICE_OR_RANGE]",
        "• Setup 1 — [TICKER1] (peaked at [CONF] conf, [N]/7 days) — [top signal from data]",
        "• Setup 2 — [TICKER2] (peaked at [CONF] conf, [N]/7 days) — [top signal from data]",
        "• Setup 3 — [TICKER3] (peaked at [CONF] conf, [N]/7 days) — [top signal from data]",
        "• Weekend risk management + invalidation levels",
        "",
        "These setups come from a multi-scanner system that monitors 600+ coins",
        "across Bybit. By aggregating over 7 days, we filter out one-day noise",
        "and surface the coins with persistent signal strength.",
        "",
        "⏱️ TIMESTAMPS:",
        "0:00 Hook",
        "0:20 Market regime",
        "1:05 Setup 1 — [TICKER1]",
        "2:45 Setup 2 — [TICKER2]",
        "4:25 Setup 3 — [TICKER3]",
        "6:05 Weekend risk management",
        "6:45 What would invalidate all 3",
        "7:25 Monday recap + CTA",
        "",
        "🔗 TOOLS I USE:",
        "→ Trade on Bybit: https://shorturl.at/L3TkD",
        "→ TradingView charts: https://shorturl.at/ZAxY6",
        "→ CoinLedger: https://shorturl.at/73iQn",
        "",
        "👇 Drop a comment with the coin you're watching this weekend.",
        "",
        "#crypto #bitcoin #weekendsetups #cryptotrading #altcoins #technicalanalysis #cryptosignals #[TICKER1] #[TICKER2] #[TICKER3]",
    ])

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 7-DAY ROLLING SELECTION
# ─────────────────────────────────────────────────────────────────────────────

def _parse_script_date(filename: str) -> Optional[datetime]:
    try:
        stem = filename.replace("script_", "").replace(".json", "")
        for fmt in ("%Y%m%d_%H%M%S", "%Y%m%d"):
            try:
                return datetime.strptime(stem[:len(datetime.now().strftime(fmt))], fmt)
            except ValueError:
                continue
    except Exception:
        pass
    return None


def _parse_master_radar_date(path: Path) -> Optional[datetime]:
    try:
        stem = path.stem.replace("master_radar_", "")
        return datetime.strptime(stem, "%Y%m%d_%H%M%S")
    except Exception:
        return None


def _find_source_master_radar(script_date: datetime,
                               max_lookback_hours: int = 12) -> Optional[Path]:
    if not _SCANNER_OUT.exists():
        return None
    candidates: list[tuple[float, Path]] = []
    for p in _SCANNER_OUT.glob("master_radar_*.json"):
        if "LATEST" in p.name:
            continue
        d = _parse_master_radar_date(p)
        if d is None:
            continue
        delta = (script_date - d).total_seconds()
        if delta <= 0 or delta > max_lookback_hours * 3600:
            continue
        candidates.append((delta, p))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def _coins_from_master_radar(data: dict) -> list[dict]:
    """
    Pull every coin (with confluence score) out of a master_radar JSON.
    Preserves the FULL trade_plan dict — the flattening to tp1/tp2/tp3
    happens later in _flatten_trade_plan when we build the LLM message.
    """
    coins = []
    for bucket_name in ("convergence", "strong_setup", "single_scanner"):
        for c in data.get(bucket_name, []) or []:
            all_signals = []
            for key in ("ignition_signals", "perp_signals", "spot_signals"):
                all_signals.extend(c.get(key, []) or [])
            seen = set()
            uniq_signals = [s for s in all_signals if not (s in seen or seen.add(s))]
            coins.append({
                "symbol":     c.get("base", "?"),
                "confluence": c.get("confluence", 0),
                "bucket":     bucket_name,
                "scanners":   c.get("scanner_count", 1),
                "signals":    uniq_signals[:8],
                "price":      c.get("price"),
                "change_24h": c.get("price_24h_pct", 0),
                "volume_24h": c.get("volume_24h", 0),
                "trade_plan": c.get("trade_plan"),  # preserves take_profits array
            })
    return coins


def aggregate_weekly_picks(lookback_days: int = 7) -> tuple[dict, list]:
    cutoff = datetime.now() - timedelta(days=lookback_days)
    if not _SCRIPT_OUT.exists():
        log.error(f"  Scripts directory not found: {_SCRIPT_OUT}")
        return {}, []

    aggregated: dict[str, dict] = {}
    scripts_found = []
    scripts_skipped_no_radar = 0

    for script_file in sorted(_SCRIPT_OUT.glob("script_*.json")):
        script_date = _parse_script_date(script_file.name)
        if script_date is None or script_date < cutoff:
            continue
        radar_path = _find_source_master_radar(script_date)
        if radar_path is None:
            scripts_skipped_no_radar += 1
            continue
        try:
            radar_data = json.loads(radar_path.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"  Could not parse {radar_path.name}: {e}")
            continue

        day_str = script_date.strftime("%Y-%m-%d")
        scripts_found.append({
            "script_date": day_str,
            "script_path": str(script_file),
            "radar_path":  str(radar_path),
        })

        for coin in _coins_from_master_radar(radar_data):
            sym = coin["symbol"]
            conf = float(coin.get("confluence") or 0)
            if sym not in aggregated:
                aggregated[sym] = {
                    "symbol":                sym,
                    "weekly_confluence_max": conf,
                    "appearances":           1,
                    "days_seen":             [day_str],
                    "best_day":              day_str,
                    "best_bucket":           coin["bucket"],
                    "best_signals":          coin["signals"],
                    "best_price":            coin.get("price"),
                    "best_change_24h":       coin.get("change_24h"),
                    "best_volume_24h":       coin.get("volume_24h"),
                    "best_trade_plan":       coin.get("trade_plan"),
                }
            else:
                a = aggregated[sym]
                if day_str not in a["days_seen"]:
                    a["days_seen"].append(day_str)
                    a["appearances"] += 1
                if conf > a["weekly_confluence_max"]:
                    a["weekly_confluence_max"] = conf
                    a["best_day"]        = day_str
                    a["best_bucket"]     = coin["bucket"]
                    a["best_signals"]    = coin["signals"]
                    a["best_price"]      = coin.get("price")
                    a["best_change_24h"] = coin.get("change_24h")
                    a["best_volume_24h"] = coin.get("volume_24h")
                    a["best_trade_plan"] = coin.get("trade_plan")

    for a in aggregated.values():
        a["composite_score"] = a["weekly_confluence_max"] * (
            1 + 0.15 * (a["appearances"] - 1)
        )

    log.info(f"  Aggregated {len(aggregated)} unique coins "
             f"from {len(scripts_found)} daily scripts in past {lookback_days} days")
    if scripts_skipped_no_radar:
        log.info(f"  Skipped {scripts_skipped_no_radar} scripts (no matching scanner JSON)")
    return aggregated, scripts_found


def select_top_three_weekly(aggregated: dict, min_appearances: int = 2) -> list:
    qualified = [
        a for a in aggregated.values()
        if a["appearances"] >= min_appearances
        and a["best_bucket"] in ("convergence", "strong_setup")
    ]
    qualified.sort(key=lambda a: a["composite_score"], reverse=True)
    if len(qualified) >= 3:
        return qualified[:3]

    log.warning(f"  Only {len(qualified)} strong/convergence picks meet "
                f"min_appearances={min_appearances} — adding single_scanner")
    all_picks = [a for a in aggregated.values() if a["appearances"] >= min_appearances]
    all_picks.sort(key=lambda a: a["composite_score"], reverse=True)
    if len(all_picks) >= 3:
        return all_picks[:3]

    log.warning(f"  Only {len(all_picks)} coins meet min_appearances={min_appearances}. "
                f"Relaxing to fill 3 slots.")
    everyone = list(aggregated.values())
    everyone.sort(key=lambda a: a["composite_score"], reverse=True)
    return everyone[:3]


def build_weekly_summary(top_three: list, today_summary: dict) -> dict:
    top_coins_formatted = []
    for a in top_three:
        top_coins_formatted.append({
            "symbol":                a["symbol"],
            "confluence":            a["weekly_confluence_max"],
            "bucket":                a["best_bucket"],
            "scanners":              len(a.get("best_signals", [])),
            "signals":               a["best_signals"],
            "price":                 a["best_price"],
            "change_24h":            a.get("best_change_24h", 0),
            "volume_24h":            a.get("best_volume_24h", 0),
            "trade_plan":            a.get("best_trade_plan"),
            "weekly_confluence_max": a["weekly_confluence_max"],
            "appearances":           a["appearances"],
            "days_seen":             a["days_seen"],
            "best_day":              a["best_day"],
            "composite_score":       round(a["composite_score"], 2),
        })

    return {
        "date":                today_summary.get("date"),
        "regime":              today_summary.get("regime", "unknown"),
        "btc_price":           today_summary.get("btc_price"),
        "btc_7d_pct":          today_summary.get("btc_7d_pct"),
        "warnings":            today_summary.get("warnings", []),
        "top_coins":           top_coins_formatted,
        "extended_coins":      [],
        "ignition_watch_now":  [],
        "_video_type":         "weekly_friday",
        "_lookback_days":      7,
        "_day_of_week":        datetime.now().strftime("%A"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# SCRIPT GENERATION
# ─────────────────────────────────────────────────────────────────────────────

def generate_weekly_script(summary: dict, provider: str = None) -> dict:
    import scriptgen
    original_prompt = scriptgen.SYSTEM_PROMPT_LANDSCAPE
    original_msg_builder = getattr(scriptgen, "_build_user_message", None)
    try:
        scriptgen.SYSTEM_PROMPT_LANDSCAPE = WEEKLY_SYSTEM_PROMPT
        scriptgen._build_user_message = _build_weekly_user_message
        chosen_provider = provider or DEFAULT_PROVIDER
        return generate_script(summary, landscape=True, provider=chosen_provider)
    finally:
        scriptgen.SYSTEM_PROMPT_LANDSCAPE = original_prompt
        if original_msg_builder is not None:
            scriptgen._build_user_message = original_msg_builder


# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION  (unchanged from v1.4 — only flags signals not in authorized list)
# ─────────────────────────────────────────────────────────────────────────────

_SIGNAL_TOKEN_RE = re.compile(r"\b([a-z]+(?:_[a-z0-9]+){1,4})\b")

_COMMON_SIGNAL_PHRASES = {
    "btc decoupling":      "btc_decoupling",
    "higher lows":         "higher_lows",
    "higher highs":        "higher_highs",
    "bullish engulfing":   "bullish_engulfing",
    "rsi divergence":      "rsi_divergence",
    "rsi reset":           "rsi_reset",
    "obv divergence":      "obv_divergence",
    "obv stealth":         "obv_stealth_accum",
    "stealth accumulation":"obv_stealth_accum",
    "funding negative":    "funding_negative",
    "whale candle":        "whale_candle",
    "bollinger squeeze":   "bb_squeeze",
    "bb squeeze":          "bb_squeeze",
    "volume expansion":    "vol_expansion",
    "golden cross":        "golden_cross",
    "ema cross":           "ema_cross",
    "cmf positive":        "cmf_positive",
}


def _signals_mentioned_in_narration(narration: str) -> set:
    text = narration.lower()
    mentioned = set()
    for m in _SIGNAL_TOKEN_RE.finditer(text):
        token = m.group(1)
        signal_keywords = (
            "rsi", "obv", "cmf", "macd", "ema", "sma", "vol", "oi",
            "funding", "whale", "squeeze", "divergence", "decoupling",
            "expansion", "accumulation", "engulfing", "stealth",
            "bb_", "_signal", "candle",
        )
        if any(kw in token for kw in signal_keywords):
            mentioned.add(token)
    for phrase, normalized in _COMMON_SIGNAL_PHRASES.items():
        if phrase in text:
            mentioned.add(normalized)
    return mentioned


def validate_script_against_data(script: dict, summary: dict) -> list:
    warnings = []
    coins = {c["symbol"]: c for c in summary.get("top_coins", [])}

    if summary.get("btc_price") is None:
        for seg in script.get("segments", []):
            narration = seg.get("narration", "")
            for m in re.finditer(r"\$(\d{2,3}[,.]?\d{3})", narration):
                price_text = m.group(0)
                window_start = max(0, m.start() - 60)
                window_end   = min(len(narration), m.end() + 60)
                window = narration[window_start:window_end].lower()
                if "bitcoin" in window or "btc" in window:
                    warnings.append(
                        f"❌ FABRICATED BTC PRICE in {seg.get('coin', '?')} segment: "
                        f"data says BTC unknown, but script mentions {price_text}"
                    )

    for seg in script.get("segments", []):
        coin_sym = seg.get("coin", "").upper()
        if coin_sym not in coins:
            continue
        narration = seg.get("narration", "")
        authorized = set(s.lower() for s in coins[coin_sym].get("signals", []))
        mentioned = _signals_mentioned_in_narration(narration)
        fabricated = mentioned - authorized
        fabricated = {f for f in fabricated if len(f) >= 8 or "_" in f}
        if fabricated:
            warnings.append(
                f"⚠️ UNAUTHORIZED SIGNALS in {coin_sym} segment: {sorted(fabricated)} "
                f"(authorized: {sorted(authorized)})"
            )

    return warnings


# ─────────────────────────────────────────────────────────────────────────────
# AUDIO REUSE
# ─────────────────────────────────────────────────────────────────────────────

def find_latest_weekly_audio() -> Optional[Path]:
    if not _VOICE_OUT.exists():
        return None
    candidates = list(_VOICE_OUT.glob("weekly_audio_*.mp3"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _find_bgm(explicit_path: str = None) -> Path | None:
    if explicit_path:
        p = Path(explicit_path)
        if p.exists():
            return p
    if not _BGM_DIR.exists():
        return None
    candidates = []
    for ext in ("*.mp3", "*.wav", "*.m4a"):
        candidates.extend(list(_BGM_DIR.glob(ext)))
    preferred = ["cinematic", "documentary", "ambient", "lofi"]
    for kw in preferred:
        for c in candidates:
            if kw in c.stem.lower():
                return c
    return candidates[0] if candidates else None


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run(
    no_upload:       bool = False,
    preview:         bool = False,
    portrait:        bool = False,
    voice_id:        str  = None,
    bgm_path:        str  = None,
    provider:        str  = None,
    lookback_days:   int  = 7,
    min_appearances: int  = 2,
    skip_voice:      bool = False,
    reuse_audio:     str  = None,
    strict:          bool = False,
) -> dict:
    t0 = time.time()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    size = (1080, 1920) if portrait else (1920, 1080)
    chosen_provider = provider or DEFAULT_PROVIDER

    log.info("=" * 64)
    log.info("WEEKLY PIPELINE v1.5 — Friday Weekend Setups")
    log.info(f"  Format: {'portrait 9:16' if portrait else 'landscape 16:9'}")
    log.info(f"  Upload: {'disabled' if no_upload else 'enabled'}")
    log.info(f"  Day: {datetime.now().strftime('%A %Y-%m-%d')}")
    log.info(f"  Lookback: {lookback_days} days, min_appearances: {min_appearances}")
    log.info(f"  LLM provider: {chosen_provider}")
    if strict:
        log.info(f"  STRICT mode: abort on validation warnings")
    log.info("=" * 64)

    if chosen_provider == "grok" and not os.environ.get("XAI_API_KEY"):
        log.warning("  XAI_API_KEY not set — Grok will fail. Use --provider gemini")

    log.info("\n[1/8] Loading today's market context...")
    raw_data = load_scanner_data(_SCANNER_OUT)
    today_summary = build_market_summary(raw_data)
    btc_p = today_summary.get('btc_price')
    log.info(f"  Today: {today_summary.get('regime', '?')} regime, "
             f"BTC {'$'+format(btc_p, ',.0f') if btc_p else 'unknown'}")

    log.info(f"\n[2/8] Aggregating coins from past {lookback_days} days...")
    aggregated, scripts_found = aggregate_weekly_picks(lookback_days=lookback_days)
    if not scripts_found:
        return {"error": "no_scripts_in_window"}
    if not aggregated:
        return {"error": "no_coins_aggregated"}

    log.info(f"  Daily scripts used: {len(scripts_found)}")
    log.info(f"  Date range: {scripts_found[0]['script_date']} → {scripts_found[-1]['script_date']}")

    log.info(f"\n[3/8] Selecting top 3 (min_appearances={min_appearances})...")
    top_three = select_top_three_weekly(aggregated, min_appearances=min_appearances)
    if len(top_three) < 3:
        return {"error": "insufficient_coins"}

    log.info("  ┌─────────┬───────┬───────┬─────────┬────────────")
    log.info("  │ Rank    │ Sym   │ MaxC  │ Days/7  │ Composite")
    log.info("  ├─────────┼───────┼───────┼─────────┼────────────")
    for i, a in enumerate(top_three, 1):
        log.info(f"  │ #{i}      │ {a['symbol']:<5} │ {a['weekly_confluence_max']:>5.1f} │ "
                 f"  {a['appearances']}/7   │ {a['composite_score']:>6.2f}")
    log.info("  └─────────┴───────┴───────┴─────────┴────────────")

    log.info("\n  Top 3 data being passed to LLM:")
    for c in top_three:
        sigs = c.get("best_signals", []) or []
        tp = _flatten_trade_plan(c.get("best_trade_plan"))
        tp_summary = (f"entry={tp.get('entry')} stop={tp.get('stop')} "
                      f"tp1={tp.get('tp1')} tp2={tp.get('tp2')} tp3={tp.get('tp3')}"
                      if tp else "NONE")
        log.info(f"    {c['symbol']}: signals={sigs[:5]}")
        log.info(f"           trade_plan: {tp_summary}")

    summary = build_weekly_summary(top_three, today_summary)

    log.info(f"\n[4/8] Generating Friday narration script (provider={chosen_provider})...")
    try:
        script = generate_weekly_script(summary, provider=chosen_provider)
    except ValueError as e:
        log.error(f"  Script generation failed: {e}")
        return {"error": "script_generation_failed", "exception": str(e)}
    except Exception as e:
        log.error(f"  Script generation crashed: {e}")
        return {"error": "script_generation_crashed", "exception": str(e)}

    log.info(f"  Title: {script.get('title', '?')}")
    log.info(f"  Segments: {len(script.get('segments', []))}")

    validation_warnings = validate_script_against_data(script, summary)
    if validation_warnings:
        log.warning("  ┌─ VALIDATION WARNINGS ──────────────────────────")
        for w in validation_warnings:
            log.warning(f"  │ {w}")
        log.warning("  └────────────────────────────────────────────────")
        if strict:
            return {"error": "validation_failed", "warnings": validation_warnings}
    else:
        log.info("  ✓ Validation passed — no fabricated signals/prices detected")

    if len(script.get("segments", [])) < 6:
        log.error(f"  Script has only {len(script.get('segments', []))} segments. Aborting.")
        return {"error": "too_few_segments"}

    if preview:
        log.info("\n── PREVIEW MODE ──")
        print(json.dumps(script, indent=2))
        return {"script": script, "summary": summary,
                "validation_warnings": validation_warnings}

    script_path = _SCRIPT_OUT / f"weekly_script_{ts}.json"
    script_path.write_text(json.dumps(script, indent=2), encoding="utf-8")
    log.info(f"  Saved: {script_path.name}")

    # Voiceover
    audio_path: Optional[Path] = None
    if reuse_audio:
        candidate = Path(reuse_audio)
        if not candidate.exists():
            return {"error": "reuse_audio_not_found"}
        audio_path = candidate
        log.info(f"\n[5/8] Reusing audio from --reuse-audio: {audio_path}")
    elif skip_voice:
        audio_path = find_latest_weekly_audio()
        if audio_path is None:
            return {"error": "no_audio_to_reuse"}
        log.info(f"\n[5/8] Reusing latest audio: {audio_path.name}")
    else:
        log.info(f"\n[5/8] Generating voiceover...")
        audio_path = _VOICE_OUT / f"weekly_audio_{ts}.mp3"
        bgm = _find_bgm(bgm_path)
        if bgm:
            log.info(f"  Background music: {bgm.name}")
        audio_duration = generate_voiceover(
            script["segments"], output_path=audio_path,
            voice_id=voice_id, bgm_path=bgm,
        )
        log.info(f"  Audio: {audio_duration:.1f}s ({audio_duration/60:.1f}min)")

    log.info(f"\n[6/8] Rendering visual frames...")
    frames_dir = _FRAMES_OUT / f"weekly_frames_{ts}"
    frames_dir.mkdir(exist_ok=True)
    frame_paths = render_all_frames(
        script=script, summary=summary, output_dir=frames_dir, size=size,
    )
    log.info(f"  Frames: {len(frame_paths)}")

    log.info(f"\n[7/8] Composing video + thumbnail...")
    video_path = _VIDEO_OUT / f"weekly_setups_{ts}.mp4"
    compose_video(
        frame_paths=frame_paths, audio_path=audio_path,
        script=script, output_path=video_path, size=size,
    )
    log.info(f"  Video: {video_path.name}")

    thumb_path = _VIDEO_OUT / f"weekly_thumb_{ts}.png"
    try:
        generate_thumbnail(script=script, summary=summary, output_path=thumb_path)
        log.info(f"  Thumbnail: {thumb_path.name}")
    except Exception as e:
        log.warning(f"  Thumbnail failed: {e}")
        thumb_path = None

    upload_result = None
    if not no_upload:
        log.info(f"\n[8/8] Uploading to YouTube...")
        try:
            script["_summary"] = summary
            script["_video_type"] = "weekly_friday"
            creds = get_youtube_credentials()
            upload_result = upload_to_youtube(
                video_path=video_path, script=script,
                credentials=creds, thumbnail_path=thumb_path,
            )
            log.info(f"  Uploaded: https://youtu.be/{upload_result.get('id', '?')}")
        except Exception as e:
            log.error(f"  Upload failed: {e}")
    else:
        log.info(f"\n[8/8] Upload skipped (--no-upload)")

    elapsed = time.time() - t0
    log.info(f"\n{'=' * 64}")
    log.info(f"WEEKLY PIPELINE COMPLETE in {elapsed:.1f}s ({elapsed/60:.1f}min)")
    log.info(f"  Video: {video_path}")
    log.info(f"  Top 3: {', '.join(a['symbol'] for a in top_three)}")
    if validation_warnings:
        log.warning(f"  ⚠ Script had {len(validation_warnings)} validation warning(s)")
    log.info(f"{'=' * 64}")

    return {
        "video_path":  str(video_path),
        "script_path": str(script_path),
        "audio_path":  str(audio_path),
        "upload":      upload_result,
        "elapsed_s":   round(elapsed, 1),
        "top_three":   [a["symbol"] for a in top_three],
        "scripts_aggregated": len(scripts_found),
        "validation_warnings": validation_warnings,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Weekly Pipeline v1.5 — Friday Weekend Setups"
    )
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--portrait", action="store_true")
    parser.add_argument("--voice-id", type=str, default=None)
    parser.add_argument("--bgm", type=str, default=None)
    parser.add_argument("--provider", type=str, default=None,
                        choices=["grok", "gemini", "claude", "openai"])
    parser.add_argument("--lookback", type=int, default=7)
    parser.add_argument("--min-appearances", type=int, default=2)
    parser.add_argument("--skip-voice", action="store_true")
    parser.add_argument("--reuse-audio", type=str, default=None)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    run(
        no_upload=args.no_upload,
        preview=args.preview,
        portrait=args.portrait,
        voice_id=args.voice_id,
        bgm_path=args.bgm,
        provider=args.provider,
        lookback_days=args.lookback,
        min_appearances=args.min_appearances,
        skip_voice=args.skip_voice,
        reuse_audio=args.reuse_audio,
        strict=args.strict,
    )
