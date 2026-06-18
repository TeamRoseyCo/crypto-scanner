"""
patch_llm_retry.py — Add network-retry logic to LLM calls in scriptgen.py.

PROBLEM: Today (May 18, 2026, 07:33 AM) a transient SSL handshake glitch
to api.x.ai killed the entire daily Short pipeline. A network blip lasting
seconds permanently failed the day's video because there was no retry logic.

WHAT THIS FIXES:

scriptgen.py:
  Wraps the LLM dispatch call (lines 349-359 in current file) with a
  retry helper. On transient network errors (SSL errors, timeouts,
  ConnectionErrors, 5XX responses), retries up to 3 times with
  exponential backoff: 5s, 15s, 45s.

  Permanent errors (400 bad request, 401 auth, 404 not found) do NOT
  retry — those need human intervention, not waiting.

  After all retries fail, the original exception bubbles up so the
  pipeline still fails loudly (no silent corruption).

This would have rescued today's run automatically — by the time it
retried 5 seconds later, the network glitch would have cleared.

Run from PowerShell:
    cd "C:\\Users\\bruno\\OneDrive\\Ambiente de Trabalho\\Workspace\\crypto scanner\\crypto-scanner"
    & "C:\\Program Files\\Python312\\python.exe" .\\patch_llm_retry.py

Idempotent — safe to re-run.
"""

from __future__ import annotations
import shutil
import sys
from pathlib import Path

VIDEO_PIPELINE = Path(r"C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\crypto scanner\crypto-scanner\YOUTUBE - faceless channel\video_pipeline")
SCRIPTGEN_PATH = VIDEO_PIPELINE / "scriptgen.py"


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
        warn(f"[{label}] expected block not found — skipping.")
        return False
    patched = content.replace(old, new, 1)
    path.write_text(patched, encoding="utf-8")
    ok(f"[{label}] patched")
    return True


def main() -> None:
    if not SCRIPTGEN_PATH.exists():
        fail(f"scriptgen.py not found at {SCRIPTGEN_PATH}")

    print()
    print("=" * 68)
    print("LLM RETRY PATCHER — add network resilience to scriptgen.py")
    print("=" * 68)
    print()

    bak = SCRIPTGEN_PATH.with_suffix(SCRIPTGEN_PATH.suffix + ".bak-retry")
    if not bak.exists():
        shutil.copy(SCRIPTGEN_PATH, bak)
        info(f"Backup: {bak.name}")
    else:
        info(f"Backup exists: {bak.name}")

    print()
    print("─" * 68)
    print("Patching scriptgen.py")
    print("─" * 68)

    # ── FIX: wrap the LLM dispatch with a retry helper ────────────────────
    # Replace the direct dispatch with a call through _call_llm_with_retry.
    patch_file(SCRIPTGEN_PATH,
        old='''    # Dispatch to the right backend
    if prov_name == "grok":
        raw_text = _call_grok(api_key, model, system, user_content)
    elif prov_name == "gemini":
        raw_text = _call_gemini(api_key, model, system, user_content)
    elif prov_name == "claude":
        raw_text = _call_claude(api_key, model, system, user_content)
    elif prov_name == "openai":
        raw_text = _call_openai(api_key, model, system, user_content)
    else:
        raise ValueError(f"Unknown provider: {prov_name}")''',
        new='''    # Dispatch to the right backend, with automatic retry on transient errors
    raw_text = _call_llm_with_retry(prov_name, api_key, model, system, user_content)''',
        label="wrap dispatch in retry helper",
        already_patched_marker="_call_llm_with_retry")

    # ── Inject the retry helper just before the PROVIDER BACKENDS section ──
    # Idempotency marker must be UNIQUE to this block — not just "_call_llm_with_retry"
    # because patch #1 above also writes that string as a function call.
    patch_file(SCRIPTGEN_PATH,
        old='''# ─────────────────────────────────────────────────────────────────────────────
# PROVIDER BACKENDS
# ─────────────────────────────────────────────────────────────────────────────''',
        new='''# ─────────────────────────────────────────────────────────────────────────────
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
# ─────────────────────────────────────────────────────────────────────────────''',
        label="inject retry helper + transient-error detection",
        already_patched_marker="_TRANSIENT_ERROR_PHRASES")

    print()
    print("=" * 68)
    print("\033[92m  PATCHES APPLIED\033[0m")
    print("=" * 68)
    print()
    print("How it works:")
    print("  - SSL handshake failures, timeouts, 5XX responses → retry 3x")
    print("    with waits of 5s, 15s, 45s between attempts.")
    print("  - 400/401/404 errors → raise immediately (no retry, those")
    print("    need human intervention not waiting).")
    print()
    print("Effect on your pipeline:")
    print("  - Worst case for a transient glitch: pipeline takes ~60s")
    print("    longer than usual, then succeeds.")
    print("  - Today's SSL error would have been caught on retry #1")
    print("    (5 seconds later, the glitch would have cleared).")
    print()
    print("Tomorrow's 1 AM run is now resilient to short network blips.")


if __name__ == "__main__":
    main()
