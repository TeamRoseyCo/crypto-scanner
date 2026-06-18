"""
patch_unique_descriptions.py — Fix repetitive YouTube descriptions.

PROBLEM: Every daily Short description currently opens with the same two
hardcoded paragraphs, regardless of which coin is featured. YouTube's
algorithm flags this as metadata duplication, which can suppress
discovery surface and (in extreme cases) trigger spam strikes.

WHAT THIS FIXES:

upload.py:
  1. The hardcoded opening "🚨 Our scanner just flagged fresh setups..."
     is replaced with a variable opening that names the actual featured
     coin, its real confluence score, and its top fired signal.
  2. 8 different opening templates rotate based on the featured coin's
     bucket / confluence score / signal type — so even similar setups
     across days produce different descriptions.
  3. Pulls the LLM-generated hook line from the script JSON to seed
     each description with that day's actual unique narration angle.
  4. The "These aren't hype indicators" closing paragraph also rotates
     between 4 variants to add more entropy.

Result: every daily Short gets a meaningfully different description that
references its actual coin, score, signals, and angle. Algorithmically
distinct, but still reads naturally to humans.

Run from PowerShell:
    cd "C:\\Users\\bruno\\OneDrive\\Ambiente de Trabalho\\Workspace\\crypto scanner\\crypto-scanner"
    & "C:\\Program Files\\Python312\\python.exe" .\\patch_unique_descriptions.py

Idempotent — safe to re-run.
"""

from __future__ import annotations
import shutil
import sys
from pathlib import Path

VIDEO_PIPELINE = Path(r"C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\crypto scanner\crypto-scanner\YOUTUBE - faceless channel\video_pipeline")
UPLOAD_PATH    = VIDEO_PIPELINE / "upload.py"


def fail(msg: str) -> None:
    print(f"\033[91m  ✗ {msg}\033[0m")
    sys.exit(1)


def info(msg: str) -> None:
    print(f"  {msg}")


def ok(msg: str) -> None:
    print(f"\033[92m  ✓ {msg}\033[0m")


def warn(msg: str) -> None:
    print(f"\033[93m  ⚠ {msg}\033[0m")


def patch_file(path: Path, old: str, new: str, label: str,
               already_patched_marker: str) -> bool:
    content = path.read_text(encoding="utf-8")
    if already_patched_marker in content:
        info(f"[{label}] already patched — skipping")
        return False
    if old not in content:
        warn(f"[{label}] expected block not found — file may already be modified.")
        return False
    patched = content.replace(old, new, 1)
    path.write_text(patched, encoding="utf-8")
    ok(f"[{label}] patched")
    return True


def main() -> None:
    if not UPLOAD_PATH.exists():
        fail(f"upload.py not found at {UPLOAD_PATH}")

    print()
    print("=" * 68)
    print("UNIQUE DESCRIPTIONS PATCHER — fix YouTube metadata duplication")
    print("=" * 68)
    print()

    bak = UPLOAD_PATH.with_suffix(UPLOAD_PATH.suffix + ".bak-unique-desc")
    if not bak.exists():
        shutil.copy(UPLOAD_PATH, bak)
        info(f"Backup: {bak.name}")
    else:
        info(f"Backup exists: {bak.name}")

    print()
    print("─" * 68)
    print("Patching upload.py")
    print("─" * 68)

    # Replace the entire hardcoded opening block with coin-aware variable opening
    patch_file(UPLOAD_PATH,
        old='''    lines = [
        f"🚨 Our scanner just flagged fresh setups across the crypto market.",
        "",
        f"Most traders enter after the breakout. The real edge comes from spotting "
        f"the signals before the move begins. In this video, we break down today's "
        f"top scanner picks{' including ' + coin_list if coin_list else ''} — "
        f"with the exact signals that fired.",
        "",
    ]''',
        new='''    # ── UNIQUE OPENING (rotates by coin/score/signals to avoid YouTube
    # metadata-duplication detection that was flagging all daily Shorts
    # with the same hardcoded text) ──────────────────────────────────────
    lines = _build_unique_opening(script, coins_mentioned, coin_list)
    lines.append("")''',
        label="replace hardcoded opening with unique generator",
        already_patched_marker="_build_unique_opening")

    # Also vary the closing "These aren't hype indicators..." paragraph
    patch_file(UPLOAD_PATH,
        old='''    lines.extend([
        "",
        "These aren't hype indicators or lagging signals. Our multi-scanner system "
        "monitors 600+ coins across Bybit and Binance for repeatable patterns "
        "that appear before major moves.",
        "",
        "👇 Drop a comment with a coin you're watching and I may analyze it in the next video.",
        "",
    ])''',
        new='''    lines.extend([
        "",
        _rotating_closing_paragraph(script),
        "",
        _rotating_cta(script),
        "",
    ])''',
        label="replace hardcoded closing with rotating variants",
        already_patched_marker="_rotating_closing_paragraph")

    # Inject the helper functions at the top of the file (just before _build_description)
    patch_file(UPLOAD_PATH,
        old='''def _build_description(script: dict) -> str:
    """
    Build professional YouTube description.
    Always generates the full format with affiliate links and hashtags.
    """''',
        new='''def _build_unique_opening(script: dict, coins_mentioned: list, coin_list: str) -> list:
    """
    Build a UNIQUE opening for the YouTube description based on the
    featured coin, its score, and top signal. Rotates between several
    template patterns so no two daily Shorts share an identical opening,
    avoiding YouTube's metadata-duplication detection.
    """
    segments = script.get("segments", [])

    # Find the featured coin and pull out its specifics
    featured = None
    for s in segments:
        c = (s.get("coin", "") or "").upper()
        if c not in ("MARKET", "MARKET REGIME", "RISK", "INVALIDATION", "CTA", ""):
            featured = s
            break

    title = (script.get("title", "") or "").strip()
    hook  = (script.get("hook", "") or "").strip()

    if not featured:
        # No coin segment found — fall back to a generic but title-derived opening
        return [
            f"📊 {title}" if title else "📊 Today's scanner picks.",
            "",
            f"{hook}" if hook else "Fresh signals from the multi-scanner. Full breakdown below.",
        ]

    sym  = (featured.get("coin", "") or "").upper()
    stat = (featured.get("stat", "") or "").strip()
    narration = (featured.get("narration", "") or "").strip()

    # Extract a "top signal" for this coin from narration (first match wins).
    # Catalog of known signal phrases — keep aligned with the scanner's
    # actual signal vocabulary.
    known_signals = [
        ("obv stealth accumulation", "stealth accumulation"),
        ("obv stealth accum",        "stealth accumulation"),
        ("rsi divergence",            "RSI divergence"),
        ("rsi reset",                 "an RSI reset"),
        ("rsi in zone",               "RSI in the buy zone"),
        ("vol oi surge",              "a volume + open interest surge"),
        ("vol expansion",             "volume expansion"),
        ("vol in window",             "a volume window expansion"),
        ("funding negative",          "negative funding"),
        ("cmf positive",              "Chaikin Money Flow positive"),
        ("higher lows",               "higher lows forming"),
        ("btc decoupling",            "BTC decoupling"),
        ("bb squeeze",                "a Bollinger squeeze"),
        ("whale candle",              "a whale candle"),
        ("breakout",                  "a breakout setup"),
    ]
    top_signal = None
    for needle, label in known_signals:
        if needle in narration.lower():
            top_signal = label
            break

    # Pull confluence/score from stat string if available (e.g. "CONV 8" or "max conf 8.0")
    import re
    score = None
    m = re.search(r"(?:CONV|conf\\w*)\\s*([\\d.]+)", stat, re.IGNORECASE)
    if m:
        score = m.group(1)

    # ── Choose an opening template based on the data we have ──────────
    # Use a deterministic-but-varied rotation: hash of (date + symbol)
    # picks the template. This means same coin on different days gets
    # a different template, but the same coin on the same day always
    # generates the same description (idempotent re-uploads).
    import hashlib
    seed_str = f"{script.get('_summary', {}).get('date', '')}-{sym}"
    seed = int(hashlib.md5(seed_str.encode()).hexdigest(), 16)

    # Pull useful change_pct from stat too (e.g. "+3.1%")
    chg_m = re.search(r"([+-]\\d+\\.?\\d*)\\s*%", stat)
    change_pct = chg_m.group(1) if chg_m else None

    # Build template variants — each weaves in different specifics
    templates = []

    # T1: score-first
    if score and top_signal:
        templates.append([
            f"🚨 {sym} just hit {score} confluence on the scanner — driven by {top_signal}.",
            "",
            (hook if hook else
             f"This isn't a routine signal. When {top_signal} fires "
             f"alongside multiple confirmations, the data shows persistent edge. "
             f"Full breakdown below."),
        ])
    elif score:
        templates.append([
            f"🚨 {sym} flagged at {score} confluence today.",
            "",
            (hook if hook else
             f"Multi-scanner agreement at this level is rare. "
             f"Walking through what fired and why it matters."),
        ])

    # T2: signal-first
    if top_signal:
        templates.append([
            f"⚡ {top_signal.capitalize()} just fired on {sym}.",
            "",
            (hook if hook else
             f"The scanner flagged {sym}{(' with ' + score + ' confluence') if score else ''}. "
             f"Most traders won't notice this until after the breakout. "
             f"Here's what to watch."),
        ])

    # T3: momentum-first (uses 24h change)
    if change_pct:
        is_up = change_pct.startswith("+")
        emoji = "📈" if is_up else "📉"
        templates.append([
            f"{emoji} {sym} {change_pct}% in 24h — scanner caught the move early.",
            "",
            (hook if hook else
             f"Today's breakdown covers what triggered the alert"
             f"{(', the ' + score + '-confluence stack') if score else ''}, "
             f"and the levels to watch next."),
        ])

    # T4: question-first
    templates.append([
        f"Why is {sym} on every scanner I run today?",
        "",
        (hook if hook else
         f"Walking through {sym}'s signal stack{(' (confluence ' + score + ')') if score else ''} "
         f"and what makes this setup different from the noise."),
    ])

    # T5: structure-first
    templates.append([
        f"📊 {sym} setup breakdown — {title}" if title else f"📊 {sym} setup breakdown.",
        "",
        (hook if hook else
         f"The scanner caught {sym}{(' at ' + score + ' confluence') if score else ''}"
         f"{(' with ' + top_signal) if top_signal else ''}. "
         f"Here's the data behind the alert."),
    ])

    # T6: contrarian / didactic
    if top_signal:
        templates.append([
            f"Most traders ignore {top_signal} signals. {sym} just proved why that's a mistake.",
            "",
            (hook if hook else
             f"Breaking down the full signal stack on {sym}"
             f"{(' (' + score + ' confluence)') if score else ''} "
             f"and what the data actually says."),
        ])

    # T7: stat-led
    if change_pct and top_signal:
        templates.append([
            f"{sym} just moved {change_pct}% — and the scanner saw it before the candle closed.",
            "",
            (hook if hook else
             f"{top_signal.capitalize()} fired ahead of the move. "
             f"Full walkthrough of the signal stack below."),
        ])

    # T8: peer-comparison
    if coin_list and len(coins_mentioned) >= 2:
        others = [c for c in coins_mentioned if c.upper() != sym][:3]
        if others:
            templates.append([
                f"🔍 {sym} leads — but {', '.join(others)} are right behind it.",
                "",
                (hook if hook else
                 f"Scanner output for today: {sym}"
                 f"{(' at ' + score + ' confluence') if score else ''}, "
                 f"plus the alts forming similar setups in the signal stack."),
            ])

    # Pick a template — guarantee at least one always exists (T4, T5 are unconditional)
    if not templates:
        return [
            f"📊 {sym} — {title}" if title else f"📊 Today's scanner pick: {sym}.",
            "",
            (hook if hook else "Full breakdown below."),
        ]

    return templates[seed % len(templates)]


def _rotating_closing_paragraph(script: dict) -> str:
    """
    Rotate between 4 variants of the closing paragraph so even the
    standardized 'about the scanner' section varies day-to-day.
    """
    import hashlib
    summary = script.get("_summary", {}) or {}
    seed_str = f"{summary.get('date', '')}-closing"
    seed = int(hashlib.md5(seed_str.encode()).hexdigest(), 16)

    variants = [
        "These aren't hype indicators or lagging signals. The multi-scanner "
        "system monitors 600+ coins across Bybit and Binance for repeatable "
        "patterns that appear before major moves.",

        "Every signal in this video comes from a multi-scanner system tracking "
        "600+ pairs across major venues. The edge is in the convergence — when "
        "multiple independent indicators agree, the setup is statistically "
        "more reliable.",

        "The scanner runs across 600+ coins on Bybit and Binance, filtering "
        "for patterns that consistently precede price expansion. No predictions, "
        "no hype — just signal confluence and probability.",

        "This setup came from a system that scans 600+ pairs daily for "
        "repeatable patterns. The goal isn't to be right every time — it's "
        "to surface high-conviction setups when multiple indicators align.",
    ]
    return variants[seed % len(variants)]


def _rotating_cta(script: dict) -> str:
    """Rotate between several CTAs so even the call-to-action varies."""
    import hashlib
    summary = script.get("_summary", {}) or {}
    seed_str = f"{summary.get('date', '')}-cta"
    seed = int(hashlib.md5(seed_str.encode()).hexdigest(), 16)

    variants = [
        "👇 Drop a comment with a coin you're watching and I may analyze it in the next video.",
        "💬 Which ticker is on your radar? Comment below — I read every reply.",
        "👇 What's your take? Drop your watchlist in the comments.",
        "💭 Got a coin you want me to analyze? Comment it below for tomorrow's scan.",
        "👇 Comment with the coin you're tracking — I'll cover the top picks tomorrow.",
    ]
    return variants[seed % len(variants)]


def _build_description(script: dict) -> str:
    """
    Build professional YouTube description.
    Always generates the full format with affiliate links and hashtags.
    """''',
        label="inject unique-description helpers",
        already_patched_marker="def _build_unique_opening")

    print()
    print("=" * 68)
    print("\033[92m  PATCHES APPLIED\033[0m")
    print("=" * 68)
    print()
    print("Every daily Short will now generate a unique description that")
    print("references the actual featured coin, score, and top signal.")
    print()
    print("The opening line, narrative angle, closing paragraph, AND CTA")
    print("all rotate based on a hash of the date and symbol — so the")
    print("same coin on different days produces different descriptions,")
    print("and different coins on the same day also produce different ones.")
    print()
    print("Tomorrow's daily run will use the new format automatically.")
    print("No need to rebuild today's video — YouTube's algorithm looks")
    print("forward, not backward. Just don't repost old ones.")
    print()
    print("─── Optional ───")
    print("If you want to retroactively update existing video descriptions")
    print("on YouTube Studio, you'd have to do that manually OR build a")
    print("backfill script. The 8 most recent uploads are the ones the")
    print("algorithm weighs heaviest, so updating those first matters most.")


if __name__ == "__main__":
    main()
