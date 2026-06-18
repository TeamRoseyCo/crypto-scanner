"""
================================================================================
HTTP CLIENT — resilient session with fast-fail DNS, visible retries, jitter
================================================================================
Drop-in replacement for `requests.Session()` for the crypto scanners.

Why this exists:
  The bare requests.Session in the scanners blocks for ~30s per DNS lookup on
  Windows, then bare except: time.sleep(5) loops add another 5s, with NO log
  line in between. Result: a single network hiccup mid-scan looks like a
  6-minute hang to the user (witnessed 2026-05-12 at coin #219).

What this gives you:
  - urllib3 Retry adapter: retries connect/read errors AND 429/5xx automatically
  - Aggressive connect timeouts (5s) — fail fast on dead DNS instead of 30s
  - Generous read timeouts (20s) — CoinGecko is sometimes slow but answering
  - Exponential backoff WITH JITTER — avoids thundering-herd retries
  - Per-request logging hook so you SEE retries happen instead of silent hangs
  - Optional global circuit breaker — after N consecutive failures, pause
    the whole scanner for a configurable cool-off instead of fighting a dead
    network for 10 minutes

Usage:
    from http_client import make_session, fetch_json

    session = make_session(api_key=os.environ.get("CG_API_KEY"))
    data = fetch_json(session, f"{CG_BASE}/coins/markets", params={...})

    # Or use the session directly — it has retries baked in:
    r = session.get(url, params=params, timeout=(5, 20))
================================================================================
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

# Timeouts: (connect, read). Connect MUST be short — that's where DNS lives.
DEFAULT_TIMEOUT: tuple[float, float] = (5.0, 20.0)

# Status codes worth retrying. 408 = request timeout, 425 = too early,
# 429 = rate limited, 500/502/503/504 = server problems.
RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


class LoggingRetry(Retry):
    """Retry subclass that logs each retry instead of failing silently.

    Standard urllib3.Retry retries silently and exits without telling you why.
    This subclass prints one log line per retry so you can see in real time
    that the scanner is fighting a flaky network, not hung.
    """

    def increment(self, method=None, url=None, response=None, error=None,
                  _pool=None, _stacktrace=None):
        if error is not None:
            log.warning(
                f"  ↻ retry: {type(error).__name__} on {method} {url} "
                f"(retries left: {self.total - 1 if self.total else 0})"
            )
        elif response is not None:
            log.warning(
                f"  ↻ retry: HTTP {response.status} on {method} {url} "
                f"(retries left: {self.total - 1 if self.total else 0})"
            )
        return super().increment(method, url, response, error, _pool, _stacktrace)


def make_session(
    *,
    api_key: str | None = None,
    api_key_header: str = "x-cg-pro-api-key",
    user_agent: str = "crypto-scanner/2.0",
    total_retries: int = 4,
    backoff_factor: float = 1.5,
    pool_connections: int = 10,
    pool_maxsize: int = 20,
) -> requests.Session:
    """Build a Session with retries, sensible pool size, and a User-Agent.

    backoff_factor=1.5 with 4 retries gives waits of roughly:
        1.5s, 3s, 6s, 12s   = ~22s worst case before final failure
    plus jitter (urllib3 adds 0–1s on top by default in modern versions).

    Compare to the current scanner behaviour: 30s DNS timeout × 5 attempts ≈
    150s+ before the loop even gives up. This is ~7× faster to fail-fast.
    """
    retry = LoggingRetry(
        total=total_retries,
        connect=total_retries,         # retry on connect errors (covers DNS)
        read=total_retries,             # retry on read timeouts
        status=total_retries,           # retry on retryable HTTP statuses
        status_forcelist=RETRY_STATUSES,
        allowed_methods=frozenset({"GET", "HEAD"}),  # don't retry POSTs blindly
        backoff_factor=backoff_factor,
        backoff_jitter=1.0,             # urllib3 ≥2.0 supports this
        respect_retry_after_header=True,
        raise_on_status=False,
    )

    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=pool_connections,
        pool_maxsize=pool_maxsize,
    )

    s = requests.Session()
    s.mount("https://", adapter)
    s.mount("http://", adapter)

    headers: dict[str, str] = {"User-Agent": user_agent}
    if api_key:
        headers[api_key_header] = api_key
    s.headers.update(headers)

    return s


# ─────────────────────────────────────────────────────────────────────────────
# CIRCUIT BREAKER — stop fighting a dead network
# ─────────────────────────────────────────────────────────────────────────────
class CircuitBreaker:
    """After N consecutive failures, pause for `cooloff_s` before resuming.

    Use case: if your Wi-Fi drops for real (not a 1-request blip), the retry
    adapter will dutifully waste ~22s per coin × hundreds of coins fighting
    a dead network. The breaker detects sustained failure and pauses ONCE
    for a long cool-off, then resumes — much better UX.

    Usage:
        breaker = CircuitBreaker(threshold=5, cooloff_s=60)
        for coin in coins:
            breaker.wait_if_open()
            try:
                fetch_ohlcv(coin)
                breaker.record_success()
            except Exception:
                breaker.record_failure()
    """

    def __init__(self, threshold: int = 5, cooloff_s: float = 60.0):
        self.threshold = threshold
        self.cooloff_s = cooloff_s
        self._fails = 0
        self._opened_at: float | None = None

    def record_success(self) -> None:
        if self._fails or self._opened_at:
            log.info(f"  ✓ network recovered after {self._fails} failure(s)")
        self._fails = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._fails += 1
        if self._fails >= self.threshold and self._opened_at is None:
            self._opened_at = time.time()
            log.warning(
                f"  ⚠ circuit breaker OPEN — {self._fails} consecutive failures. "
                f"Pausing {self.cooloff_s:.0f}s before resuming."
            )

    def wait_if_open(self) -> None:
        if self._opened_at is None:
            return
        elapsed = time.time() - self._opened_at
        remaining = self.cooloff_s - elapsed
        if remaining > 0:
            time.sleep(remaining)
        # half-open: let the next request try; if it succeeds, breaker closes
        self._opened_at = None
        log.info("  ↻ circuit breaker HALF-OPEN — trying one request...")


# ─────────────────────────────────────────────────────────────────────────────
# CONVENIENCE WRAPPER
# ─────────────────────────────────────────────────────────────────────────────
def fetch_json(
    session: requests.Session,
    url: str,
    *,
    params: dict | None = None,
    timeout: tuple[float, float] = DEFAULT_TIMEOUT,
    breaker: CircuitBreaker | None = None,
) -> Any | None:
    """GET a URL and return parsed JSON, or None on failure.

    Retries are handled by the session's adapter; this wrapper just adds
    the optional circuit breaker and uniform error logging.
    """
    if breaker is not None:
        breaker.wait_if_open()

    try:
        r = session.get(url, params=params, timeout=timeout)
    except requests.exceptions.ConnectionError as e:
        log.warning(f"  ✗ connection failed: {url} — {type(e.__cause__).__name__ if e.__cause__ else 'ConnectionError'}")
        if breaker:
            breaker.record_failure()
        return None
    except requests.exceptions.Timeout:
        log.warning(f"  ✗ timeout: {url}")
        if breaker:
            breaker.record_failure()
        return None
    except requests.exceptions.RequestException as e:
        log.warning(f"  ✗ request error: {url} — {e}")
        if breaker:
            breaker.record_failure()
        return None

    if r.status_code == 429:
        wait = int(r.headers.get("Retry-After", 60))
        log.warning(f"  ⚠ rate-limited on {url} — sleeping {wait}s")
        time.sleep(wait + random.uniform(0, 2))  # jitter so concurrent scanners don't sync
        if breaker:
            breaker.record_failure()
        return None

    if r.status_code != 200:
        log.warning(f"  ✗ HTTP {r.status_code} on {url}")
        if breaker:
            breaker.record_failure()
        return None

    try:
        data = r.json()
    except ValueError:
        log.warning(f"  ✗ non-JSON response from {url}")
        if breaker:
            breaker.record_failure()
        return None

    if breaker:
        breaker.record_success()
    return data
