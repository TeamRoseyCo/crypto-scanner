# Applied Patches — May 2026

Archive of one-shot patch scripts that were applied to the crypto-scanner
codebase during a debugging sprint in mid-May 2026. Each patch was
idempotent and self-guarded against re-application. The fixes are now
permanently baked into the target files in `video_pipeline/` and
`python-scanners/`.

These files are kept here purely as a historical record of **what changed
and why**. Safe to delete after ~30 days of stable pipeline runs.

---

## Timeline

| Date     | Patch                            | Target file(s)              | Backup created                  |
|----------|----------------------------------|-----------------------------|----------------------------------|
| 5/15/26  | `patch_scriptgen.ps1`            | `scriptgen.py`              | `scriptgen.py.bak`               |
| 5/15/26  | `patch_grok_model.ps1`           | `scriptgen.py`              | `scriptgen.py.bak-grok`          |
| 5/15/26  | `patch_grok_call.ps1`            | `scriptgen.py`              | `scriptgen.py.bak-grokcall`     |
| 5/15/26  | `patch_phase1_titles.py`         | `visuals.py`                | `visuals.py.bak-phase1`          |
| 5/15/26  | `patch_video_layout.py`          | `visuals.py`, `thumbnail.py`, `voiceover.py`, `compose.py` | `*.bak` (per file)  |
| 5/17/26  | `patch_unique_descriptions.py`   | `upload.py`                 | `upload.py.bak`                  |
| 5/18/26  | `patch_llm_retry.py`             | `scriptgen.py`              | `scriptgen.py.bak-retry`         |

All targets live under
`YOUTUBE - faceless channel/video_pipeline/`.

---

## What each patch fixed

### `patch_scriptgen.ps1` — Gemini token limit
**Problem:** Gemini's default 4096 output token cap was truncating longer
weekly scripts, producing invalid JSON and the "Script has no segments"
error.
**Fix:** `maxOutputTokens: 4096 → 8192` in `scriptgen.py`.

### `patch_grok_model.ps1` — deprecated model
**Problem:** `scriptgen.py` defaulted to `grok-3-mini`, which xAI
deprecated.
**Fix:** Renamed to `grok-4-1-fast-non-reasoning` (drop-in, same
OpenAI-compatible endpoint).

### `patch_grok_call.ps1` — Grok-4 API parameters
**Problem:** Grok-4-family models require `max_completion_tokens` rather
than `max_tokens`; sending the wrong parameter returned a 400 error.
Token cap (2000) was too low for the weekly script's 1000-1300 words.
Timeout of 60s was too short for reasoning calls.
**Fix:** In `_call_grok()`:
- Use `max_completion_tokens` for grok-4 models, `max_tokens` for older grok-3.
- Token cap raised to **8000**.
- Timeout raised from **60s → 180s**.
- 4XX errors now log the actual x.ai response body for easier debugging.

### `patch_phase1_titles.py` — title overflow + stat overlap
**Problem:** Long meta-segment titles ("WHAT KILLS THESE", "WATCHING THIS
WEEKEND?") were cut off at the screen edge because hero font was sized for
short tickers. Meta segments (RISK / INVALIDATION / CTA / MARKET) also had
a placeholder `-0.62% 24h` stat being drawn on top of the title.
**Fix in `visuals.py`:**
- Added `_fit_font()` helper — auto-shrinks hero font in 5% increments
  until text fits within `screen_width × 0.84`.
- Increased title→stat vertical gap from `h*0.13 → h*0.20` so they never
  overlap regardless of font size.
- Suppressed the stat line entirely on meta segments where the % is bogus.

### `patch_video_layout.py` — 9 visual + audio-sync fixes
**Problem set (cumulative — supersedes earlier visual patchers):**

In `visuals.py`:
1. ACTIVE SIGNALS lines overlapping (37px spacing vs 61px font).
2. SIGNAL STACK bars overlapping each other.
3. Internal labels (RISK / INVALIDATION / CTA) shown as raw uppercase
   titles — translated to "WEEKEND RISK" / "WHAT KILLS THESE" /
   "WATCHING THIS WEEKEND?".
4. SIGNAL STACK page title (RISK) crashing into "SIGNAL STACK" subhead —
   top-of-page gap bumped from `h*0.08 → h*0.13`.
5. "SCANNER DAILY" footer corrected to "WEEKEND SETUPS" on weekly videos.

In `thumbnail.py`:

6. "SETUP SETUP" duplication on weekly title.
7. Thumbnail rebuilt to show all 3 coins (XAG / INJ / KITE) stacked on
   the right side with "WEEKEND SETUPS" on the left.

In `voiceover.py`:

8. Now writes a `<audio>.durations.json` sidecar file recording the
   real duration of every segment audio file (previously only the total
   was stored). This enables per-segment image timing.

In `compose.py`:

9. Reads the `.durations.json` sidecar and uses real per-segment timings
   to keep each image on screen for its actual narration duration. Falls
   back to even-division if the sidecar is missing (backward compatible
   with older audio files).

### `patch_unique_descriptions.py` — YouTube metadata duplication
**Problem:** Every daily Short description was opening with the same two
hardcoded paragraphs ("🚨 Our scanner just flagged fresh setups..."),
regardless of which coin was featured. YouTube flags metadata duplication
as a discovery suppressor and (in extreme cases) a spam strike risk.
**Fix in `upload.py`:**
- Replaced hardcoded opening with a variable opening naming the actual
  featured coin, its confluence score, and its top fired signal.
- 8 different opening templates rotate based on bucket / score / signal
  type.
- LLM-generated hook line pulled from the script JSON to seed each
  description with that day's unique narration angle.
- Closing paragraph rotates between 4 variants for additional entropy.

Result: every daily Short produces a meaningfully different,
algorithmically distinct description that still reads naturally.

### `patch_llm_retry.py` — network resilience
**Problem:** On May 18 at 07:33, a transient SSL handshake glitch to
`api.x.ai` killed the entire daily Short pipeline. A network blip lasting
seconds permanently failed the day's video because there was no retry
logic.
**Fix in `scriptgen.py`:**
- Wrapped the LLM dispatch call with `_call_llm_with_retry()`.
- On transient errors (SSL handshake, timeouts, ConnectionError, 5XX
  responses), retries up to **3 times** with exponential backoff:
  **5s → 15s → 45s**.
- Permanent errors (400, 401, 404) raise immediately — those need human
  intervention, not waiting.
- Transient detection covers: `ssl`, `eof occurred`, `max retries`,
  `connection aborted/reset/refused`, `remote end closed`,
  `read timed out`, `timeout`, `temporarily unavailable`, `bad gateway`,
  `service unavailable`, `gateway timeout`, plus HTTP status codes
  500/502/503/504/408/429.

The May 18 SSL error would have been caught on retry #1 (5 seconds
later, the glitch had cleared).

---

## Backup files

Each patch wrote a `.bak*` file next to its target before modifying it.
Those backups still live in `video_pipeline/`:

```
scriptgen.py.bak           — pre-Gemini-token-bump
scriptgen.py.bak-grok      — pre-Grok-model-rename
scriptgen.py.bak-grokcall  — pre-Grok-API-param-fix
scriptgen.py.bak-retry     — pre-LLM-retry-wrapper
visuals.py.bak-phase1      — pre-title-autoshrink
visuals.py.bak             — pre-video-layout
thumbnail.py.bak           — pre-video-layout
voiceover.py.bak           — pre-video-layout
compose.py.bak             — pre-video-layout
upload.py.bak              — pre-unique-descriptions
```

These provide emergency rollback if any patch turns out to have regressed
something subtle. Safe to delete after ~2 weeks of stable runs.

---

## Cleanup checklist

- [ ] Run `daily_video.bat` successfully for 7 consecutive days.
- [ ] Verify weekly_video.bat fires cleanly on a Friday.
- [ ] Delete the `.bak*` files in `video_pipeline/` after 2 weeks.
- [ ] Delete this entire `_archive/applied_patches_may2026/` folder after
      30 days.
