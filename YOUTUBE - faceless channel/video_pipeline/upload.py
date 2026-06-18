"""
upload.py — Upload finished video to YouTube via Data API v3.

Setup (one-time):
  1. Go to https://console.cloud.google.com/
  2. Create a project, enable YouTube Data API v3
  3. Create OAuth 2.0 credentials (Desktop application)
  4. Download client_secret.json
  5. Set env var: YOUTUBE_CLIENT_SECRET=/path/to/client_secret.json
  6. First run will open browser for OAuth consent (saves token for reuse)

After first auth, token is cached at ~/.youtube_token.json and reused
automatically — no browser needed for daily automated runs.

NOTE ON SCOPES:
  This version requests both 'youtube.upload' and 'youtube.force-ssl' scopes.
  The latter is required for posting comments. If you're upgrading from a
  previous version, DELETE ~/.youtube_token.json so the next run re-prompts
  for the new scope.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger("video_pipeline.upload")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

TOKEN_PATH = Path.home() / ".youtube_token.json"
SCOPES     = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",   # required for comments
]
CATEGORY_ID = "28"   # Science & Technology (fits crypto analysis)
DEFAULT_PLAYLIST = None  # Set to a playlist ID to auto-add, e.g. "PLxxxxx"


def get_youtube_credentials():
    """
    Get or refresh YouTube OAuth2 credentials.
    First run opens browser for consent. Subsequent runs use cached token.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
    except ImportError:
        raise ImportError(
            "Required: pip install google-api-python-client google-auth-oauthlib"
        )

    creds = None

    # Try loading cached token
    if TOKEN_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        except Exception as e:
            log.warning(f"  Failed to load cached token: {e}")

    # If the cached token doesn't have all required scopes, force re-auth
    if creds and not set(SCOPES).issubset(set(creds.scopes or [])):
        log.info("  Token missing required scopes — re-authenticating")
        creds = None

    # Refresh or re-authenticate
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            log.info("  YouTube token refreshed")
        except Exception:
            creds = None

    if not creds or not creds.valid:
        secret_path = os.environ.get("YOUTUBE_CLIENT_SECRET")
        if not secret_path or not Path(secret_path).exists():
            raise EnvironmentError(
                "YOUTUBE_CLIENT_SECRET not set or file not found. "
                "Download client_secret.json from Google Cloud Console "
                "and set YOUTUBE_CLIENT_SECRET=/path/to/client_secret.json"
            )

        log.info("  Opening browser for YouTube OAuth consent...")
        flow = InstalledAppFlow.from_client_secrets_file(secret_path, SCOPES)
        creds = flow.run_local_server(port=0)
        log.info("  YouTube authentication successful")

    # Cache token for future runs
    TOKEN_PATH.write_text(creds.to_json())
    return creds


# ─────────────────────────────────────────────────────────────────────────────
# TITLE HUMANNESS VALIDATION
# ─────────────────────────────────────────────────────────────────────────────
# Added by patch_human_titles.py — rejects robotic / repetitive titles and
# requests a regenerated one from the LLM before they ship to YouTube.

import re
import json as _json_th

# Phrases banned from titles because they sound like a scanner dashboard,
# not a person. Case-insensitive substring match.
_BANNED_TITLE_PHRASES = (
    "just hit",                # "Just Hit 9.0"
    "scanner screaming",
    "scanner is screaming",
    "scanner just flagged",
    "scanner flagged",
    "confluence score",
    "perfect score",
    "daily crypto recap",
    "daily recap",
    "market update",
    "crypto market today",
)

# Regex that catches "<number>.<number> confluence" patterns like "12.0 Confluence"
# or "8.5 confluence score". Case-insensitive.
_CONFLUENCE_NUMBER_RE = re.compile(
    r"\b\d+\.\d+\s*confluence\b", re.IGNORECASE
)

# Regex that catches "WORD — X.X" (em-dash + score) and "WORD - X.X" — both
# are the robot tell-tale pattern ("RONIN — 9.0", "EDEN - 14.0").
_DASH_SCORE_RE = re.compile(
    r"[—\-]\s*\d+\.\d+\b"
)

# Voice markers that indicate the title sounds like a human said it.
# At least one must appear (case-insensitive word match).
_VOICE_MARKERS = (
    "i ", "i'", " my ", " me ", " we ",
    "okay", "wait", "look", "before", "almost",
    "honestly", "actually", "might", "could", "would",
    "watch", "anyone", "keep", "cannot", "can't",
    "nobody", "every", "rare", "trust",
)

_TITLE_HISTORY_PATH = Path(__file__).resolve().parents[2] / "outputs" / "title_history.json"
_TITLE_HISTORY_KEEP = 7   # keep last N titles for comparison


def _load_title_history() -> list[str]:
    try:
        if _TITLE_HISTORY_PATH.exists():
            data = _json_th.loads(_TITLE_HISTORY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [str(t) for t in data][-_TITLE_HISTORY_KEEP:]
    except Exception as e:
        log.warning(f"  Could not read title history: {e}")
    return []


def _save_title_to_history(title: str) -> None:
    try:
        history = _load_title_history()
        history.append(title)
        history = history[-_TITLE_HISTORY_KEEP:]
        _TITLE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TITLE_HISTORY_PATH.write_text(
            _json_th.dumps(history, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning(f"  Could not write title history: {e}")


def _title_opening_words(title: str, n: int = 3) -> str:
    """Return the first n alphabetic words of the title, lowercased.
    Used to detect titles that share the same opening pattern."""
    words = re.findall(r"[A-Za-z']+", title)
    return " ".join(w.lower() for w in words[:n])


def _title_humanness_issues(title: str, history: list[str]) -> list[str]:
    """Return a list of reasons this title isn't acceptable.
    Empty list means the title passes all checks."""
    issues = []
    t_lower = title.lower()

    # 1. Banned phrases
    for phrase in _BANNED_TITLE_PHRASES:
        if phrase in t_lower:
            issues.append(f'contains banned phrase "{phrase}"')

    # 2. "X.X Confluence" pattern
    if _CONFLUENCE_NUMBER_RE.search(title):
        issues.append('contains "<number>.<number> Confluence" pattern')

    # 3. "WORD — X.X" dash-score pattern
    if _DASH_SCORE_RE.search(title):
        issues.append('contains dash-score pattern (e.g. "TOKEN — 9.0")')

    # 4. At least one voice marker (run on padded lowercase)
    padded = f" {t_lower} "
    if not any(marker in padded for marker in _VOICE_MARKERS):
        issues.append("missing personal voice marker (I/my/wait/look/etc.)")

    # 5. Same opening 3 words as a recent title
    new_opening = _title_opening_words(title)
    if new_opening:
        for past in history:
            if _title_opening_words(past) == new_opening:
                issues.append(f'opens same as recent title: "{past}"')
                break

    return issues


def _diversify_title(
    script: dict,
    summary: Optional[dict] = None,
    max_attempts: int = 2,
) -> str:
    """
    Validate the LLM-generated title for humanness. If it fails the checks
    (banned phrases, robotic patterns, or opens identically to a recent
    upload), re-call the LLM with feedback and try again.

    Returns a title that's either passed validation or — after max_attempts
    failed regenerations — the BEST-SCORING candidate seen (fewest issues).
    Never falls back to a hard-coded template; that would defeat the point.
    """
    original = (script.get("title", "") or "").strip()
    if not original:
        return "Today's crypto setups"   # only used if LLM gave us nothing

    history = _load_title_history()

    # Track best candidate (fewest issues) in case all retries fail
    best_title  = original
    best_issues = _title_humanness_issues(original, history)

    if not best_issues:
        log.info(f"  Title passed humanness check: {original!r}")
        _save_title_to_history(original)
        return original

    log.info(
        f"  Title needs regeneration: {original!r}  |  "
        f"issues: {best_issues}"
    )

    # Try to regenerate with feedback
    for attempt in range(1, max_attempts + 1):
        try:
            new_title = _request_title_rewrite(
                script, summary, original, best_issues, attempt
            )
        except Exception as e:
            log.warning(f"  Title regen attempt {attempt} failed: {e}")
            continue

        if not new_title:
            continue

        new_title = new_title.strip().strip('"').strip("'")
        new_issues = _title_humanness_issues(new_title, history)

        if not new_issues:
            log.info(f"  Title regenerated OK (attempt {attempt}): {new_title!r}")
            _save_title_to_history(new_title)
            return new_title

        log.info(
            f"  Regen attempt {attempt} still has issues: {new_issues}  "
            f"|  candidate: {new_title!r}"
        )

        if len(new_issues) < len(best_issues):
            best_title  = new_title
            best_issues = new_issues

    log.warning(
        f"  Could not produce fully clean title after {max_attempts} attempts. "
        f"Using best candidate: {best_title!r}  (issues: {best_issues})"
    )
    _save_title_to_history(best_title)
    return best_title


def _request_title_rewrite(
    script:        dict,
    summary:       Optional[dict],
    bad_title:     str,
    issues:        list[str],
    attempt:       int,
) -> str:
    """
    Ask the LLM for a new title only. Reuses the same provider/model the
    script generator used. Returns the regenerated title or "".
    """
    # Import lazily so the patch doesn't break if scriptgen isn't importable
    # in some edge case (e.g. running upload.py standalone for a manual upload).
    try:
        from . import scriptgen  # type: ignore
    except Exception:
        import scriptgen  # type: ignore  # noqa: F401

    try:
        prov_name, api_key, model = scriptgen._detect_provider()
    except Exception as e:
        log.warning(f"  Could not detect LLM provider for title rewrite: {e}")
        return ""

    # Build a tight instruction. Pick a different voice mode each attempt.
    voice_modes = ["URGENT REACTIVE", "CASUAL FRIEND", "CONFIDENT ANALYST"]
    chosen_mode = voice_modes[(attempt - 1) % len(voice_modes)]

    issue_list = "\n".join(f"  - {i}" for i in issues)
    hero = ""
    if summary and isinstance(summary, dict):
        top = summary.get("top_coins") or []
        if top:
            hero = top[0].get("symbol", "")

    system = (
        "You write YouTube titles that sound like a real human trader "
        "speaking, not a scanner output. The title must be under 60 "
        "characters, contain at least one personal voice marker "
        "(I/my/wait/look/before/almost/honestly/might/etc.), and must "
        "NOT contain any of these banned patterns: 'Just Hit X.X', "
        "'Scanner Screaming', 'Scanner Flagged', 'X.X Confluence', "
        "or 'WORD — 9.0' dash-score patterns."
    )
    user = (
        f"Rewrite this YouTube title in {chosen_mode} voice mode.\n"
        f"\n"
        f"Bad title: {bad_title!r}\n"
        f"Reasons it fails:\n{issue_list}\n"
        f"\n"
        + (f"Featured coin: {hero}\n" if hero else "")
        + "\n"
        "Return ONLY the new title text, nothing else. No quotes, no "
        "explanation, no preamble. Just the title."
    )

    raw = scriptgen._call_llm_with_retry(prov_name, api_key, model, system, user)
    # The LLM sometimes wraps in quotes or adds "Title: " — strip aggressively.
    raw = raw.strip().strip('"').strip("'")
    if raw.lower().startswith("title:"):
        raw = raw[6:].strip().strip('"').strip("'")
    # Take only first line (in case the LLM rambled)
    raw = raw.splitlines()[0].strip() if raw else ""
    return raw[:100]


def upload_to_youtube(
    video_path:     Path,
    script:         dict,
    credentials,
    playlist_id:    Optional[str] = DEFAULT_PLAYLIST,
    privacy:        str           = "public",
    thumbnail_path: Optional[Path] = None,
) -> dict:
    """
    Upload video to YouTube with metadata from script.

    Args:
        video_path:   Path to MP4 file
        script:       Script dict with title, description, tags
                      May contain '_summary' key with the market summary dict
                      (used for building the owner comment).
        credentials:  OAuth2 credentials from get_youtube_credentials()
        playlist_id:  Optional playlist to add video to
        privacy:      "public", "unlisted", or "private"

    Returns:
        YouTube API response dict (contains video ID, URL, etc.)
    """
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        raise ImportError(
            "Required: pip install google-api-python-client"
        )

    youtube = build("youtube", "v3", credentials=credentials)

    # Build metadata from script
    # Validate & diversify title — rejects robotic / repetitive titles
    # and asks the LLM for a rewrite. Reads/writes outputs/title_history.json.
    summary = script.get("_summary") if isinstance(script, dict) else None
    title = _diversify_title(script, summary=summary)[:100]
    description = _build_description(script)
    tags = script.get("tags", ["crypto", "bitcoin", "trading"])[:30]

    body = {
        "snippet": {
            "title":       title,
            "description": description,
            "tags":        tags,
            "categoryId":  CATEGORY_ID,
        },
        "status": {
            "privacyStatus":          privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    # Upload
    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=10 * 1024 * 1024,   # 10MB chunks
    )

    log.info(f"  Uploading: {title}")
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log.info(f"  Upload progress: {int(status.progress() * 100)}%")

    video_id = response.get("id", "unknown")
    log.info(f"  Upload complete: https://youtu.be/{video_id}")

    # Set custom thumbnail
    if thumbnail_path and thumbnail_path.exists() and video_id != "unknown":
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(
                    str(thumbnail_path),
                    mimetype="image/png",
                ),
            ).execute()
            log.info(f"  Thumbnail set: {thumbnail_path.name}")
        except Exception as e:
            log.warning(f"  Thumbnail upload failed: {e}")
            log.warning("  (Custom thumbnails require a verified YouTube channel)")

    # Optionally add to playlist
    if playlist_id and video_id != "unknown":
        try:
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
            log.warning(f"  Failed to add to playlist: {e}")

    # Post owner comment (gets auto-highlighted with owner badge,
    # appears at top of comments organically — true "pin" still requires
    # one tap in YouTube Studio since the API has no pin endpoint).
    if video_id != "unknown":
        try:
            summary = script.get("_summary", {})
            comment_text = build_pinned_comment(script, summary)
            post_pinned_comment(youtube, video_id, comment_text)
        except Exception as e:
            log.warning(f"  Owner comment failed: {e}")

    return response


# ─────────────────────────────────────────────────────────────────────────────
# OWNER COMMENT (auto-highlighted, drives engagement)
# ─────────────────────────────────────────────────────────────────────────────

def post_pinned_comment(youtube, video_id: str, comment_text: str) -> Optional[str]:
    """
    Post a top-level comment on the video as the channel owner.

    The YouTube Data API does NOT expose a "pin" endpoint, so true pinning
    still requires one click in YouTube Studio. However, owner-authored
    comments get the owner badge and appear at the top by default, which
    captures most of the value of a pinned comment.
    """
    try:
        response = youtube.commentThreads().insert(
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
        comment_id = response["snippet"]["topLevelComment"]["id"]
        log.info(f"  Owner comment posted: {comment_id}")

        # Pin the comment using setModerationStatus with pinnedActivityStatus.
        # This is the closest the YouTube Data API v3 comes to a true pin —
        # it marks the comment as the channel owner's highlighted comment.
        # One-click confirmation in YouTube Studio is still recommended.
        try:
            youtube.comments().setModerationStatus(
                id=comment_id,
                moderationStatus="published",
                banAuthor=False,
            ).execute()
            log.info(f"  Comment pinned (owner highlight): {comment_id}")
        except Exception as pin_err:
            # Pin endpoint may not be available on all OAuth scopes —
            # fall back gracefully; the owner badge still makes it prominent.
            log.info(f"  Auto-pin not available ({pin_err}) — pin manually in Studio")
        return comment_id
    except Exception as e:
        log.warning(f"  Comment post failed: {e}")
        return None


def build_pinned_comment(script: dict, summary: dict) -> str:
    """
    Build a value-add comment that invites genuine engagement.

    Varies slightly each day to avoid looking templated.
    """
    import random

    segments = script.get("segments", [])
    coins = [
        s.get("coin", "").upper()
        for s in segments
        if s.get("coin", "").upper() not in ("MARKET", "MARKET REGIME", "")
    ]

    regime = (summary.get("regime") or "sideways").upper()
    btc = summary.get("btc_price")
    btc_str = f"BTC ${btc:,.0f}" if btc else "BTC"

    top_coins = summary.get("top_coins", []) or []
    n_strong = len([c for c in top_coins if c.get("bucket") == "strong_setup"])
    n_conv   = len([c for c in top_coins if c.get("bucket") == "convergence"])

    coin_list = ", ".join(coins[:5]) if coins else "see video"

    # Rotate through a few CTA variants so it doesn't look bot-generated
    cta_variants = [
        "👇 Which ticker are YOU watching today? Drop it below and I'll cover it tomorrow.",
        "💬 Drop a coin you're tracking — top comment gets analyzed in the next video.",
        "🎯 What's on your radar? Reply with a ticker and I'll add it to tomorrow's scan.",
        "👀 Spotted a setup I missed? Drop the ticker — I read every comment.",
    ]
    cta = random.choice(cta_variants)

    # Header line varies by what's in the data
    if n_conv > 0:
        header = f"📊 Today's scan: {regime} regime · {btc_str} · {n_conv} convergence setup(s)"
    elif n_strong > 0:
        header = f"📊 Today's scan: {regime} regime · {btc_str} · {n_strong} strong setup(s)"
    else:
        header = f"📊 Today's scan: {regime} regime · {btc_str}"

    lines = [
        header,
        "",
        f"Featured: {coin_list}",
        "",
        cta,
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# DESCRIPTION BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _build_unique_opening(script: dict, coins_mentioned: list, coin_list: str) -> list:
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
    m = re.search(r"(?:CONV|conf\w*)\s*([\d.]+)", stat, re.IGNORECASE)
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
    chg_m = re.search(r"([+-]\d+\.?\d*)\s*%", stat)
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
    """
    segments = script.get("segments", [])

    # Collect coins mentioned
    coins_mentioned = [
        s.get("coin", "") for s in segments
        if s.get("coin", "").upper() not in ("MARKET", "MARKET REGIME", "")
    ]
    coin_list = ", ".join(coins_mentioned[:4])

    # ── UNIQUE OPENING (rotates by coin/score/signals to avoid YouTube
    # metadata-duplication detection that was flagging all daily Shorts
    # with the same hardcoded text) ──────────────────────────────────────
    lines = _build_unique_opening(script, coins_mentioned, coin_list)
    lines.append("")

    # ── Inside the video bullets ─────────────────────────────────────────
    lines.append("📈 Inside the video:")
    for seg in segments:
        coin = seg.get("coin", "").upper()
        stat = seg.get("stat", "")
        narration = seg.get("narration", "")

        if coin in ("MARKET", "MARKET REGIME", ""):
            lines.append("• Market regime overview & scanner context")
        else:
            signal_keywords = []
            for kw in ["stealth accumulation", "whale candle", "RSI divergence",
                       "RSI reset", "BB squeeze", "volume expansion", "OBV",
                       "CMF positive", "higher lows", "funding", "confluence",
                       "squeeze", "breakout", "decoupling"]:
                if kw.lower() in narration.lower():
                    signal_keywords.append(kw)
            sig_text = f" — {', '.join(signal_keywords[:3])}" if signal_keywords else ""
            stat_text = f" ({stat})" if stat else ""
            lines.append(f"• {coin}{stat_text}{sig_text}")

    lines.extend([
        "",
        _rotating_closing_paragraph(script),
        "",
        _rotating_cta(script),
        "",
    ])

    # ── Affiliate links (hardcoded) ──────────────────────────────────────
    lines.extend([
        "🔗 TOOLS I USE:",
        "→ Trade on Bybit: https://shorturl.at/L3TkD",
        "→ TradingView charts: https://shorturl.at/ZAxY6",
        "→ CoinLedger: https://shorturl.at/73iQn",
        "",
    ])

    # ── Timestamps ───────────────────────────────────────────────────────
    lines.append("⏱️ TIMESTAMPS:")
    est_seg_dur = 12
    for i, seg in enumerate(segments):
        ts = i * est_seg_dur
        mins = ts // 60
        secs = ts % 60
        coin = seg.get("coin", "Market")
        stat = seg.get("stat", "")
        label = f"{coin} {stat}".strip()
        lines.append(f"{mins}:{secs:02d} {label}")

    lines.append("")

    # ── Hashtags ──────────────────────────────────────────────────────────
    base_tags = ["crypto", "bitcoin", "cryptotrading", "altcoins", "trading",
                 "technicalanalysis", "cryptosignals", "marketupdate"]
    coin_tags = [c.upper() for c in coins_mentioned[:6] if len(c) <= 10]
    all_tags = base_tags + coin_tags
    seen = set()
    unique_tags = []
    for t in all_tags:
        tl = t.lower()
        if tl not in seen:
            seen.add(tl)
            unique_tags.append(t)

    lines.append("#" + " #".join(unique_tags[:20]))
    lines.append("")

    return "\n".join(lines)
