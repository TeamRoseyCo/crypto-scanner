"""
================================================================================
DAILY PNL TRACKER  v1.0
================================================================================
Fetches today's realized PnL from Bybit UM (perps) and writes a small status
file that Sonnet 4.6 reads before approving any entry.

This is Layer A of the Rule 3 enforcement mechanism. Layer B is the system
prompt block that tells Sonnet 4.6 to read this file before answering any
"should I take this trade?" question.

How it works:
  1. Pulls the closed-pnl endpoint from Bybit for today (00:00 UTC → now)
  2. Sums realized PnL across all symbols
  3. Compares against the daily loss limit from TRADING_RULES.md
  4. Writes outputs/daily_pnl/today.json with:
       - current realized PnL
       - status: "OK" or "LIMIT_HIT"
       - timestamp
       - which trades contributed (for transparency)
  5. If status is LIMIT_HIT, also writes a flag file outputs/daily_pnl/LIMIT_HIT.txt
     so Sonnet 4.6 can refuse entries even if today.json hasn't been read yet

Run via Task Scheduler every 5 minutes during trading hours, or manually:
    python daily_pnl_tracker.py             # one-shot update
    python daily_pnl_tracker.py --watch     # loops every 5 minutes
    python daily_pnl_tracker.py --reset     # clears today's data (use carefully)
================================================================================
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

# Hard limit from TRADING_RULES.md Rule 3
DAILY_LOSS_LIMIT_USD = -1940.0

# Polling interval in seconds when --watch is used
WATCH_INTERVAL_SECS = 300   # 5 minutes

# Bybit API endpoints
BYBIT_BASE = "https://api.bybit.com"
CLOSED_PNL_ENDPOINT = "/v5/position/closed-pnl"

# Paths
_THIS_DIR     = Path(__file__).resolve().parent
# scanner_v3/ → engine/ → python-scanners/  (the project root for outputs)
_PROJECT_ROOT = _THIS_DIR.parent.parent
_PNL_DIR      = _PROJECT_ROOT / "outputs" / "daily_pnl"
_PNL_DIR.mkdir(parents=True, exist_ok=True)

_TODAY_FILE  = _PNL_DIR / "today.json"
_LIMIT_FLAG  = _PNL_DIR / "LIMIT_HIT.txt"
_LOG_FILE    = _PROJECT_ROOT / "outputs" / "logs" / "daily_pnl_tracker.log"
_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# Credentials file (re-used from existing infra)
_CREDS_FILE = _THIS_DIR / "bybit_credentials.json"

# Logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("daily_pnl")


# ─────────────────────────────────────────────────────────────────────────────
# CREDS
# ─────────────────────────────────────────────────────────────────────────────

def _load_credentials() -> tuple[str, str]:
    """Read Bybit API key/secret from bybit_credentials.json.
    Falls back to BYBIT_API_KEY / BYBIT_API_SECRET env vars."""
    import os

    if _CREDS_FILE.exists():
        try:
            creds = json.loads(_CREDS_FILE.read_text(encoding="utf-8"))
            api_key    = creds.get("api_key")    or creds.get("key")
            api_secret = creds.get("api_secret") or creds.get("secret")
            if api_key and api_secret:
                return api_key, api_secret
        except Exception as e:
            log.warning(f"Could not parse {_CREDS_FILE.name}: {e}")

    api_key    = os.environ.get("BYBIT_API_KEY")
    api_secret = os.environ.get("BYBIT_API_SECRET")
    if api_key and api_secret:
        return api_key, api_secret

    raise RuntimeError(
        "Bybit credentials not found. Either put them in bybit_credentials.json "
        "or set BYBIT_API_KEY and BYBIT_API_SECRET env vars."
    )


# ─────────────────────────────────────────────────────────────────────────────
# BYBIT API (signed request, V5 spec)
# Pattern lifted from trade_journal_sync.py which is known-working.
# Key requirement: build ONE canonical query string and use it for BOTH the
# signature payload AND the URL. If the two differ at all (encoding, ordering),
# Bybit returns retCode=10004 (Error sign).
# ─────────────────────────────────────────────────────────────────────────────

from urllib.parse import urlencode

DEFAULT_RECV_WINDOW = 10_000   # matches trade_journal_sync.py


def _bybit_signed_get(
    endpoint:    str,
    params:      dict,
    api_key:     str,
    api_secret:  str,
    recv_window: int = DEFAULT_RECV_WINDOW,
) -> Optional[dict]:
    """Bybit V5 signed GET. Returns parsed JSON or None on error.

    Signature: HMAC_SHA256(timestamp + api_key + recv_window + query_string)
    The CRITICAL detail: query_string is built once via urlencode(sorted(...))
    and used identically in the signature AND the URL. Any divergence between
    the two breaks the signature."""
    timestamp     = str(int(time.time() * 1000))
    query_string  = urlencode(sorted(params.items()))
    sign_payload  = f"{timestamp}{api_key}{recv_window}{query_string}"
    signature     = hmac.new(
        api_secret.encode("utf-8"),
        sign_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    headers = {
        "X-BAPI-API-KEY":     api_key,
        "X-BAPI-TIMESTAMP":   timestamp,
        "X-BAPI-RECV-WINDOW": str(recv_window),
        "X-BAPI-SIGN":        signature,
        "User-Agent":         "scanner_v3/daily_pnl_tracker",
    }
    url = f"{BYBIT_BASE}{endpoint}?{query_string}"

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        body = ""
        try:
            if e.response is not None:
                body = e.response.text[:300]
        except Exception:
            pass
        status = e.response.status_code if e.response is not None else "?"
        log.error(f"Bybit HTTP error {status}: {body}")
        return None
    except requests.RequestException as e:
        log.error(f"Bybit request failed: {type(e).__name__}: {e}")
        return None


def _fetch_closed_pnl(api_key: str, api_secret: str,
                     start_ms: int, end_ms: int) -> list[dict]:
    """Fetch all closed-PnL records for the given UTC time window.
    Bybit returns at most 200 per page; we paginate via the cursor field."""
    all_records: list[dict] = []
    cursor: Optional[str] = None
    page = 0

    while True:
        page += 1
        params = {
            "category":  "linear",
            "startTime": str(start_ms),
            "endTime":   str(end_ms),
            "limit":     "200",
        }
        if cursor:
            params["cursor"] = cursor

        payload = _bybit_signed_get(
            CLOSED_PNL_ENDPOINT, params, api_key, api_secret,
        )
        if payload is None:
            raise RuntimeError("Bybit API request failed (see logs above)")

        ret_code = payload.get("retCode")
        if ret_code != 0:
            ret_msg = payload.get("retMsg", "?")
            log.error(f"Bybit returned retCode={ret_code}: {ret_msg}")
            raise RuntimeError(f"Bybit API error: {ret_msg}")

        result  = payload.get("result", {})
        records = result.get("list", []) or []
        all_records.extend(records)

        cursor = result.get("nextPageCursor")
        if not cursor or len(records) < 200:
            break

        if page > 10:                       # safety cap
            log.warning("Pagination cap reached at 10 pages — stopping")
            break

    return all_records


# ─────────────────────────────────────────────────────────────────────────────
# TIME WINDOW
# ─────────────────────────────────────────────────────────────────────────────

def _today_window_utc() -> tuple[int, int, datetime]:
    """Returns (start_ms, end_ms, start_dt) for 'today in UTC'.
    Trading day is defined as 00:00 UTC → now UTC, matching the original
    TRADING_RULES.md analysis."""
    now_utc = datetime.now(timezone.utc)
    start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        int(start.timestamp() * 1000),
        int(now_utc.timestamp() * 1000),
        start,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CORE UPDATE
# ─────────────────────────────────────────────────────────────────────────────

def update() -> dict:
    """Pull today's closed PnL, compute totals, write status file.
    Returns the status dict for the caller."""
    api_key, api_secret = _load_credentials()
    start_ms, end_ms, start_dt = _today_window_utc()

    log.info(f"Fetching closed PnL from {start_dt.isoformat()} → now")
    records = _fetch_closed_pnl(api_key, api_secret, start_ms, end_ms)
    log.info(f"  Records returned: {len(records)}")

    # Sum realized PnL across all closed positions today.
    # Bybit's closedPnl field is the net realized P&L for the closed position
    # (already net of fees).
    realized_pnl = 0.0
    per_symbol: dict[str, float] = {}
    trade_summaries: list[dict] = []

    for r in records:
        try:
            pnl = float(r.get("closedPnl", 0.0))
        except (TypeError, ValueError):
            pnl = 0.0

        symbol = r.get("symbol", "?")
        realized_pnl += pnl
        per_symbol[symbol] = per_symbol.get(symbol, 0.0) + pnl

        trade_summaries.append({
            "symbol":     symbol,
            "side":       r.get("side"),
            "qty":        r.get("qty"),
            "entry":      r.get("avgEntryPrice"),
            "exit":       r.get("avgExitPrice"),
            "pnl":        round(pnl, 4),
            "closed_at":  r.get("updatedTime"),
        })

    # Status decision
    if realized_pnl <= DAILY_LOSS_LIMIT_USD:
        status = "LIMIT_HIT"
    else:
        status = "OK"

    # Headroom before limit (negative if already breached)
    headroom = realized_pnl - DAILY_LOSS_LIMIT_USD

    out = {
        "date_utc":           start_dt.strftime("%Y-%m-%d"),
        "checked_at":         datetime.now(timezone.utc).isoformat(),
        "realized_pnl_usd":   round(realized_pnl, 2),
        "daily_loss_limit":   DAILY_LOSS_LIMIT_USD,
        "headroom_to_limit":  round(headroom, 2),
        "status":             status,
        "closed_trade_count": len(records),
        "per_symbol":         {k: round(v, 2) for k, v in per_symbol.items()},
        "trades":             trade_summaries,
    }

    # Write status file
    _TODAY_FILE.write_text(json.dumps(out, indent=2), encoding="utf-8")
    log.info(f"  Wrote {_TODAY_FILE.name}")

    # Manage the LIMIT_HIT flag file
    if status == "LIMIT_HIT":
        _LIMIT_FLAG.write_text(
            f"LIMIT HIT at {out['checked_at']}\n"
            f"Realized PnL: ${realized_pnl:+,.2f}\n"
            f"Limit:        ${DAILY_LOSS_LIMIT_USD:+,.2f}\n"
            f"Breach by:    ${-headroom:+,.2f}\n"
            f"\n"
            f"NO NEW ENTRIES UNTIL 00:00 UTC TOMORROW.\n",
            encoding="utf-8",
        )
        log.warning("=" * 64)
        log.warning(f"  DAILY LOSS LIMIT HIT")
        log.warning(f"  Realized PnL: ${realized_pnl:+,.2f}  Limit: ${DAILY_LOSS_LIMIT_USD:+,.2f}")
        log.warning(f"  No new entries until 00:00 UTC tomorrow.")
        log.warning("=" * 64)
    else:
        # Remove stale LIMIT_HIT flag if today's status is OK
        if _LIMIT_FLAG.exists():
            _LIMIT_FLAG.unlink()
            log.info("  Cleared stale LIMIT_HIT flag (today is OK)")
        log.info(f"  Realized PnL: ${realized_pnl:+,.2f}   "
                 f"Headroom: ${headroom:+,.2f}   Status: OK")

    return out


def reset_today() -> None:
    """Delete today's status file and any LIMIT_HIT flag.
    Use when you know there's stale data (e.g. testing, or you just want to
    re-fetch from scratch). Doesn't touch Bybit — only the local files."""
    for p in (_TODAY_FILE, _LIMIT_FLAG):
        if p.exists():
            p.unlink()
            log.info(f"  Deleted {p.name}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Daily PnL tracker for Rule 3 enforcement")
    parser.add_argument("--watch", action="store_true",
                        help=f"Loop every {WATCH_INTERVAL_SECS}s instead of one-shot")
    parser.add_argument("--reset", action="store_true",
                        help="Clear today's data files (does NOT touch Bybit)")
    args = parser.parse_args()

    if args.reset:
        reset_today()
        return 0

    if args.watch:
        log.info(f"Watch mode — updating every {WATCH_INTERVAL_SECS}s. Ctrl-C to stop.")
        while True:
            try:
                update()
            except Exception as e:
                log.error(f"Update failed: {e}")
            time.sleep(WATCH_INTERVAL_SECS)

    try:
        update()
        return 0
    except Exception as e:
        log.error(f"Update failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
