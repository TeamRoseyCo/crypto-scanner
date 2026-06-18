"""
================================================================================
TRADE JOURNAL  —  Bybit sync (Stage 2)
================================================================================
Pulls recent executions from Bybit and imports them into the journal as
open/close trades, with scanner context auto-attached from historical
master_radar snapshots.

Setup (one-time):
  1. Bybit → Account & Security → API Management → Create New Key
     - Permission: READ ONLY (do NOT enable trading/withdrawal)
     - System-generated, no IP restriction needed for read-only
  2. Save key + secret in a file at:
       outputs/journal/bybit_credentials.json
     Format:
       {"api_key": "YOUR_KEY", "api_secret": "YOUR_SECRET"}
  3. Run: python trade_journal_sync.py
     The script will fetch your last 7 days of fills and walk you through them.

What this does NOT do:
  - It does NOT place orders. Read-only key cannot trade.
  - It does NOT auto-match without your confirmation. You see each trade.
  - It does NOT log discretionary trades on other exchanges (Bybit only).
  - It does NOT handle pyramid/scale-in (one position per coin assumed).

Run:
  python trade_journal_sync.py                  # last 7 days, interactive
  python trade_journal_sync.py --days 14        # custom window
  python trade_journal_sync.py --auto-system    # auto-accept system picks (no prompt)
  python trade_journal_sync.py --dry-run        # preview, don't write
================================================================================
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError


# ─────────────────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────────────────
_THIS_DIR     = Path(__file__).resolve().parent
_ENGINE_DIR   = _THIS_DIR.parent
_PYTHON_DIR   = _ENGINE_DIR.parent
_PROJECT_ROOT = _PYTHON_DIR.parent
_JOURNAL_DIR  = _PROJECT_ROOT / "outputs" / "journal"
_SCANNER_DIR  = _PROJECT_ROOT / "outputs" / "scanner-results"
_JOURNAL_DIR.mkdir(parents=True, exist_ok=True)

_TRADES_FILE   = _JOURNAL_DIR / "trades.json"
_CREDS_FILE    = _JOURNAL_DIR / "bybit_credentials.json"
_SYNC_STATE    = _JOURNAL_DIR / "bybit_sync_state.json"   # remembers imported execIds

KNOWN_REASONS_MENU = [
    ("hit_tp1",            "Hit take-profit 1"),
    ("hit_tp2",            "Hit take-profit 2"),
    ("hit_tp3",            "Hit take-profit 3"),
    ("hit_stop",           "Hit stop loss"),
    ("trailing_stop",      "Trailing stop hit"),
    ("manual_profit",      "Manual close (in profit)"),
    ("manual_loss",        "Manual close (cut loser)"),
    ("panicked",           "Panicked / emotional close"),
    ("gut_feel",           "Gut feel close"),
    ("thesis_invalidated", "Thesis invalidated"),
    ("expired",            "Held too long, gave up"),
]


# ─────────────────────────────────────────────────────────────────────────────
# BYBIT API
# ─────────────────────────────────────────────────────────────────────────────

_BYBIT_BASE = "https://api.bybit.com"


def _load_credentials() -> tuple[str, str]:
    """
    Read API key+secret. Two sources, in priority order:
      1. Environment variables BYBIT_API_KEY + BYBIT_API_SECRET
         (preferred — matches bybit_auth.py pattern, no file on disk)
      2. JSON file at outputs/journal/bybit_credentials.json
         (fallback — only used if env vars not set)
    Exits cleanly on missing/invalid credentials. Use a READ-ONLY key.
    """
    # Try env vars first
    env_key    = os.environ.get("BYBIT_API_KEY",    "").strip()
    env_secret = os.environ.get("BYBIT_API_SECRET", "").strip()
    if env_key and env_secret:
        return env_key, env_secret

    # Fall back to JSON file
    if _CREDS_FILE.exists():
        try:
            with open(_CREDS_FILE) as f:
                creds = json.load(f)
            return creds["api_key"], creds["api_secret"]
        except (json.JSONDecodeError, KeyError) as e:
            print(f"⚠️  Credentials file malformed: {e}")
            print(f'   Expected: {{"api_key": "...", "api_secret": "..."}}')
            sys.exit(1)

    # Neither source available — give clear setup instructions
    print(f"⚠️  No Bybit credentials found.")
    print(f"   Preferred: set environment variables once with:")
    print(f'     [System.Environment]::SetEnvironmentVariable("BYBIT_API_KEY",    "...", "User")')
    print(f'     [System.Environment]::SetEnvironmentVariable("BYBIT_API_SECRET", "...", "User")')
    print(f"     (Then close and reopen your terminal.)")
    print(f"   Or: create {_CREDS_FILE.name} at:")
    print(f"     {_CREDS_FILE}")
    print(f'     Content: {{"api_key": "...", "api_secret": "..."}}')
    print(f"   Use a READ-ONLY key. Never enable trade or withdraw scope.")
    sys.exit(1)


def _bybit_signed_get(
    endpoint: str,
    params:   dict,
    api_key:  str,
    api_secret: str,
    recv_window: int = 10_000,
) -> Optional[dict]:
    """
    Bybit V5 signed GET. Returns parsed JSON or None on error.
    Signature: HMAC_SHA256(timestamp + api_key + recv_window + query_string)
    """
    timestamp = str(int(time.time() * 1000))
    query_string = urlencode(sorted(params.items()))
    sign_payload = f"{timestamp}{api_key}{recv_window}{query_string}"
    signature = hmac.new(
        api_secret.encode(),
        sign_payload.encode(),
        hashlib.sha256,
    ).hexdigest()

    headers = {
        "X-BAPI-API-KEY":     api_key,
        "X-BAPI-TIMESTAMP":   timestamp,
        "X-BAPI-RECV-WINDOW": str(recv_window),
        "X-BAPI-SIGN":        signature,
        "User-Agent":         "scanner_v3/journal_sync",
    }
    url = f"{_BYBIT_BASE}{endpoint}?{query_string}"
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        try:
            body = e.read().decode("utf-8")
        except Exception:
            body = "(no body)"
        print(f"⚠️  Bybit API HTTP error {e.code}: {body[:300]}")
        return None
    except (URLError, json.JSONDecodeError, TimeoutError) as e:
        print(f"⚠️  Bybit API error: {type(e).__name__}: {e}")
        return None


def fetch_executions(
    api_key:    str,
    api_secret: str,
    days:       int = 7,
    categories: tuple[str, ...] = ("linear", "spot"),
) -> list[dict]:
    """
    Fetch all executions in the last `days` days across the given categories.

    Bybit V5 separates product types:
      - 'linear'  — USDT perpetuals (most altcoin trades)
      - 'spot'    — Spot market (BTC/ETH spot buys)
      - 'inverse' — Coin-margined inverse perps (rare)

    Bybit's API caps each /v5/execution/list query at a 7-day time range,
    so for `days > 7` we walk backwards from now in 7-day chunks and stitch
    the results together. The API also paginates within each chunk via cursor.

    Each returned exec is tagged with `_category` so downstream logic can
    distinguish (e.g., to display category in summary).

    Returns list of execution dicts (Bybit's raw format) tagged with category.
    Safety cap: 5000 execs per category total.
    """
    BYBIT_MAX_RANGE_MS = 7 * 24 * 60 * 60 * 1000   # API hard cap per call

    end_ms_total = int(time.time() * 1000)
    start_ms_total = end_ms_total - days * 24 * 60 * 60 * 1000
    all_execs: list[dict] = []

    for category in categories:
        cat_execs: list[dict] = []
        # Walk backwards in 7-day chunks
        chunk_end = end_ms_total
        while chunk_end > start_ms_total:
            chunk_start = max(start_ms_total, chunk_end - BYBIT_MAX_RANGE_MS + 1)
            cursor: Optional[str] = None
            chunk_count_before = len(cat_execs)
            while True:
                params = {
                    "category":  category,
                    "startTime": chunk_start,
                    "endTime":   chunk_end,
                    "limit":     100,
                }
                if cursor:
                    params["cursor"] = cursor

                data = _bybit_signed_get("/v5/execution/list", params, api_key, api_secret)
                if not data or data.get("retCode") != 0:
                    err = (data or {}).get("retMsg", "unknown error")
                    print(f"⚠️  Failed to fetch {category} executions "
                          f"(chunk {chunk_start}-{chunk_end}): {err}")
                    break

                result = data.get("result", {})
                execs = result.get("list", []) or []
                for e in execs:
                    e["_category"] = category
                cat_execs.extend(execs)

                cursor = result.get("nextPageCursor")
                if not cursor:
                    break
                if len(cat_execs) > 5000:
                    print(f"⚠️  Hit pagination cap (5000 execs) for {category}; stopping.")
                    break

            chunk_added = len(cat_execs) - chunk_count_before
            if chunk_added > 0 and days > 7:
                # Show per-chunk progress only when paginating across multiple chunks
                from datetime import datetime as _dt
                cs = _dt.fromtimestamp(chunk_start/1000).strftime("%Y-%m-%d")
                ce = _dt.fromtimestamp(chunk_end/1000).strftime("%Y-%m-%d")
                print(f"    {category} {cs} → {ce}: {chunk_added} execs")

            if len(cat_execs) > 5000:
                break
            # Move to the previous 7-day window
            chunk_end = chunk_start - 1
            if chunk_start <= start_ms_total:
                break

        if cat_execs:
            print(f"  {category}: {len(cat_execs)} executions total")
        all_execs.extend(cat_execs)

    return all_execs


# ─────────────────────────────────────────────────────────────────────────────
# EXECUTION → TRADE GROUPING
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TradePair:
    """One open + one close, derived from one or more Bybit executions."""
    symbol:        str               # e.g., "ENSOUSDT"
    base:          str               # e.g., "ENSO"
    side:          str               # "long" (Buy→Sell) — short pairs skipped
    category:      str               # "linear" or "spot"
    open_time_ms:  int
    open_price:    float
    open_qty:      float
    open_value:    float             # USDT value at open
    close_time_ms: Optional[int] = None
    close_price:   Optional[float] = None
    close_qty:     Optional[float] = None
    fees:          float = 0.0
    exec_ids:      list[str] = None
    # What kind of order produced the close fill — set when close_time_ms is set.
    # "TakeProfit" / "StopLoss" / "" (empty = manual close)
    # We use this for accurate exit-reason classification in non-interactive mode.
    close_stop_order_type: str = ""
    # Orphan close: long opened OUTSIDE the lookback window, this exec is the
    # closing fill. Open price is unknown (set to close price as placeholder).
    # PnL is unknowable from this data alone — user can fill it in later.
    orphan_close:  bool = False
    stop_order_type: str = ""        # for orphan closes only — "TakeProfit"/"StopLoss"/""


def _coalesce_split_fills(
    items: list[dict],
    time_window_s: float = 90.0,
    price_window_pct: float = 1.0,
) -> list[dict]:
    """
    Collapse consecutive same-side executions that are clearly chunks of one
    logical fill — e.g., a stop-loss broken into 150 small fills as price
    ticks down through the stop level.

    Two consecutive execs on the same symbol+side are merged into one if:
      (a) they happen within `time_window_s` seconds of each other, AND
      (b) their prices are within `price_window_pct` of each other

    Both conditions must hold AT THE BOUNDARY between consecutive execs —
    so a long sustained fill (e.g., a 2-hour ladder of small TP fills) will
    still merge as long as each consecutive pair is close enough.

    Returns a NEW list with merged executions. Each merged exec preserves
    weighted-average price, summed qty/fees, time of first fill, and a
    list of all underlying exec_ids so we can mark them all imported.
    """
    if not items:
        return []

    # items must be pre-sorted by execTime
    out: list[dict] = []
    cur: Optional[dict] = None
    cur_total_qty: float = 0.0
    cur_total_value: float = 0.0
    cur_total_fees: float = 0.0
    cur_exec_ids: list[str] = []
    cur_last_ts: int = 0
    cur_last_price: float = 0.0

    def _flush():
        if cur is None:
            return
        if cur_total_qty <= 0:
            return
        avg_price = cur_total_value / cur_total_qty
        merged = {**cur,
                  "execQty":   str(cur_total_qty),
                  "execPrice": str(avg_price),
                  "execFee":   str(cur_total_fees),
                  "execId":    cur_exec_ids[0],   # primary id (first in chunk)
                  "_merged_exec_ids": cur_exec_ids}
        out.append(merged)

    for e in items:
        try:
            qty   = float(e.get("execQty", 0))
            price = float(e.get("execPrice", 0))
            fee   = float(e.get("execFee", 0))
            ts    = int(e.get("execTime", 0))
        except (ValueError, TypeError):
            continue
        side = e.get("side", "")
        eid  = e.get("execId", "")

        if cur is None:
            cur = e
            cur_total_qty   = qty
            cur_total_value = qty * price
            cur_total_fees  = fee
            cur_exec_ids    = [eid]
            cur_last_ts     = ts
            cur_last_price  = price
            continue

        # Can we merge with current chunk?
        same_side    = (cur.get("side") == side)
        time_ok      = (ts - cur_last_ts) <= time_window_s * 1000
        if cur_last_price > 0:
            price_diff_pct = abs(price - cur_last_price) / cur_last_price * 100
            price_ok = price_diff_pct <= price_window_pct
        else:
            price_ok = False

        if same_side and time_ok and price_ok:
            # Merge into current chunk
            cur_total_qty   += qty
            cur_total_value += qty * price
            cur_total_fees  += fee
            cur_exec_ids.append(eid)
            cur_last_ts     = ts
            cur_last_price  = price
        else:
            # Flush current, start new chunk
            _flush()
            cur = e
            cur_total_qty   = qty
            cur_total_value = qty * price
            cur_total_fees  = fee
            cur_exec_ids    = [eid]
            cur_last_ts     = ts
            cur_last_price  = price

    _flush()
    return out


def group_executions_into_trades(execs: list[dict]) -> list[TradePair]:
    """
    Turn Bybit executions into open/close trade pairs.

    Strategy:
      1. Pre-pass: collapse split-fill chunks (Bybit breaks one stop-loss into
         many small fills when liquidity is thin — those are ONE trade close,
         not 150 separate trades). See _coalesce_split_fills.
      2. State machine per symbol: Buy opens a long, Sell closes it. Multiple
         Buys before a Sell aggregate into the open (pyramid). Short positions
         (Sell-first) are skipped — long-only journal.

    Returns one TradePair per closed round-trip. Open-but-not-yet-closed
    positions returned with close_* = None.
    """
    # Key by (symbol, category) — BTCUSDT exists in both spot and linear,
    # but they're separate markets with independent positions.
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in execs:
        sym = e.get("symbol", "")
        cat = e.get("_category", "linear")
        by_key[(sym, cat)].append(e)

    pairs: list[TradePair] = []
    for (symbol, category), items in by_key.items():
        # Sort by execTime ascending
        items.sort(key=lambda x: int(x.get("execTime", 0)))
        if not symbol.endswith("USDT"):
            continue
        base = symbol[:-4]

        # ── Pre-pass: collapse split-fill chunks ─────────────────────────────
        # Without this, a stop-loss filled in 150 chunks becomes 150 trades.
        original_count = len(items)
        items = _coalesce_split_fills(items)
        if len(items) < original_count:
            print(f"  {base}: coalesced {original_count} fills → {len(items)} logical executions")

        # State machine
        position_qty:   float = 0.0
        position_value: float = 0.0
        position_fees:  float = 0.0
        open_time_ms:   Optional[int] = None
        open_exec_ids:  list[str] = []
        is_short:       bool = False

        def _all_exec_ids(exec_dict: dict) -> list[str]:
            """Return all underlying exec_ids for an exec, expanding merged chunks."""
            merged = exec_dict.get("_merged_exec_ids")
            if merged:
                return list(merged)
            single = exec_dict.get("execId", "")
            return [single] if single else []

        i = 0
        while i < len(items):
            e = items[i]
            try:
                qty   = float(e.get("execQty", 0))
                price = float(e.get("execPrice", 0))
                fee   = float(e.get("execFee", 0))
                ts    = int(e.get("execTime", 0))
            except (ValueError, TypeError):
                i += 1
                continue
            side = e.get("side", "")  # "Buy" or "Sell"
            this_exec_ids = _all_exec_ids(e)

            # Bybit V5 execution fields that help distinguish trade types:
            #   closedSize    > 0 → this fill closed (part of) an existing position
            #   stopOrderType "TakeProfit"/"StopLoss" → triggered by a conditional order
            #   orderType     "Market"/"Limit"
            try:
                closed_size = float(e.get("closedSize", 0) or 0)
            except (ValueError, TypeError):
                closed_size = 0.0
            stop_order_type = e.get("stopOrderType", "") or ""

            if is_short:
                # Currently in a short position — skip until flat.
                # A Buy here closes the short (we don't journal it).
                # NOTE: this check MUST come before the position_qty == 0 check
                # below, otherwise a closing Buy would be misinterpreted as
                # opening a new long.
                if side == "Buy":
                    is_short = False
                i += 1
                continue

            if position_qty == 0:
                # Opening a new position OR closing a position from outside our window
                if side == "Buy":
                    # Could be: opening a new long, OR closing an existing short
                    # from outside the window. closedSize > 0 means it closed something.
                    if closed_size > 0:
                        # This is closing a short that opened pre-window.
                        # We don't journal shorts, skip it.
                        i += 1
                        continue
                    # Genuine new long open
                    position_qty   = qty
                    position_value = qty * price
                    position_fees  = fee
                    open_time_ms   = ts
                    open_exec_ids  = list(this_exec_ids)
                    is_short       = False
                elif side == "Sell":
                    # Could be: opening a short, OR closing a long from outside window.
                    # The key signal: closedSize > 0 means it closed an existing position.
                    # stopOrderType "TakeProfit"/"StopLoss" also strongly indicates a close.
                    if closed_size > 0 or stop_order_type in ("TakeProfit", "StopLoss"):
                        # Orphan close — long opened pre-window, closing here.
                        # Emit a special pair with open_price=close_price so it appears
                        # in the journal and we don't lose the data. PnL is unknowable
                        # without the original open, but at least we record the close.
                        pair = TradePair(
                            symbol        = symbol,
                            base          = base,
                            side          = "long",
                            category      = category,
                            open_time_ms  = ts,        # we don't know real open time
                            open_price    = price,     # placeholder = close price
                            open_qty      = qty,
                            open_value    = qty * price,
                            close_time_ms = ts,
                            close_price   = price,
                            close_qty     = qty,
                            fees          = fee,
                            exec_ids      = list(this_exec_ids),
                            orphan_close  = True,
                            stop_order_type = stop_order_type,
                        )
                        pairs.append(pair)
                        print(f"  ({base}: closing pre-window long at ${price} "
                              f"— qty {qty}, type={stop_order_type or 'manual'})")
                        i += 1
                        continue
                    # Genuine short open (no closedSize, no TP/SL trigger)
                    is_short = True
                    print(f"  (skipping short on {base})")
                i += 1
                continue

            # We have a long position open
            if side == "Buy":
                # Pyramid: aggregate
                position_value += qty * price
                position_qty   += qty
                position_fees  += fee
                open_exec_ids.extend(this_exec_ids)
                i += 1
                continue

            # side == "Sell" → close (or partial close)
            if qty >= position_qty - 1e-9:
                # Full close (allowing for tiny rounding)
                avg_open  = position_value / position_qty
                pair = TradePair(
                    symbol        = symbol,
                    base          = base,
                    side          = "long",
                    category      = category,
                    open_time_ms  = open_time_ms or ts,
                    open_price    = avg_open,
                    open_qty      = position_qty,
                    open_value    = position_value,
                    close_time_ms = ts,
                    close_price   = price,
                    close_qty     = qty,
                    fees          = position_fees + fee,
                    exec_ids      = list(open_exec_ids) + list(this_exec_ids),
                    close_stop_order_type = stop_order_type,
                )
                pairs.append(pair)
                # Reset
                position_qty = 0
                position_value = 0
                position_fees = 0
                open_time_ms = None
                open_exec_ids = []
            else:
                # Partial close — emit a pair for the closed portion, keep rest open
                portion_pct = qty / position_qty
                avg_open    = position_value / position_qty
                pair = TradePair(
                    symbol        = symbol,
                    base          = base,
                    side          = "long",
                    category      = category,
                    open_time_ms  = open_time_ms or ts,
                    open_price    = avg_open,
                    open_qty      = qty,
                    open_value    = qty * avg_open,
                    close_time_ms = ts,
                    close_price   = price,
                    close_qty     = qty,
                    fees          = position_fees * portion_pct + fee,
                    exec_ids      = list(open_exec_ids) + list(this_exec_ids),
                    close_stop_order_type = stop_order_type,
                )
                pairs.append(pair)
                position_qty   -= qty
                position_value -= qty * avg_open
                position_fees  *= (1 - portion_pct)
            i += 1

        # If position remained open, emit it as an open-only pair
        if position_qty > 0:
            avg_open = position_value / position_qty
            pair = TradePair(
                symbol        = symbol,
                base          = base,
                side          = "long",
                category      = category,
                open_time_ms  = open_time_ms or 0,
                open_price    = avg_open,
                open_qty      = position_qty,
                open_value    = position_value,
                fees          = position_fees,
                exec_ids      = open_exec_ids,
            )
            pairs.append(pair)

    pairs.sort(key=lambda p: p.open_time_ms)
    return pairs


# ─────────────────────────────────────────────────────────────────────────────
# SCANNER CONTEXT — load historical master_radar JSONs and find matches
# ─────────────────────────────────────────────────────────────────────────────

def _list_master_radar_snapshots() -> list[Path]:
    """Find all master_radar timestamped JSONs (not _LATEST). Sorted oldest first."""
    snaps = sorted(_SCANNER_DIR.glob("master_radar_*.txt"))   # txt list as time index
    # We actually want JSONs but they don't have timestamps in name — grok by mtime
    json_files = sorted(_SCANNER_DIR.glob("master_radar_*.json"))
    # We use timestamped TXT for time, but the JSON we read is the one nearest in time
    # Simpler approach: trust master_radar_LATEST.json + check its mtime
    return json_files


def find_scanner_context(
    base:       str,
    fill_time_ms: int,
    cache: dict,
) -> Optional[dict]:
    """
    Look up coin in master_radar JSONs that existed BEFORE the fill time.
    cache is {path: parsed_json} memoization.
    Returns coin's view dict + bucket name, or None.
    """
    fill_dt = datetime.fromtimestamp(fill_time_ms / 1000, tz=timezone.utc)
    snaps = _list_master_radar_snapshots()

    # Find the most recent snapshot BEFORE the fill, and within the last 24h of it
    best_path = None
    best_mtime = None
    for p in snaps:
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        if mtime > fill_dt:
            continue
        if mtime < fill_dt - timedelta(hours=24):
            continue  # too old to be relevant context
        if best_mtime is None or mtime > best_mtime:
            best_path  = p
            best_mtime = mtime

    if best_path is None:
        return None

    if best_path not in cache:
        try:
            with open(best_path) as f:
                cache[best_path] = json.load(f)
        except Exception:
            cache[best_path] = None
    data = cache[best_path]
    if not data:
        return None

    base_upper = base.upper()
    for bucket in ("convergence", "strong_setup", "single_scanner", "extended"):
        for entry in data.get(bucket, []) or []:
            if entry.get("base", "").upper() == base_upper:
                ctx = {**entry, "_bucket": bucket}
                ctx["_snapshot"] = best_path.name
                ctx["_snapshot_age_h"] = (fill_dt - best_mtime).total_seconds() / 3600
                return ctx
    return None


# ─────────────────────────────────────────────────────────────────────────────
# JOURNAL I/O — same format as trade_journal.py
# ─────────────────────────────────────────────────────────────────────────────

def load_trades() -> list[dict]:
    if not _TRADES_FILE.exists():
        return []
    try:
        with open(_TRADES_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def save_trades(trades: list[dict]) -> None:
    tmp = _TRADES_FILE.with_suffix(".tmp.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(trades, f, indent=2, default=str)
    tmp.replace(_TRADES_FILE)


def load_sync_state() -> dict:
    if not _SYNC_STATE.exists():
        return {"imported_exec_ids": []}
    try:
        with open(_SYNC_STATE) as f:
            return json.load(f)
    except Exception:
        return {"imported_exec_ids": []}


def save_sync_state(state: dict) -> None:
    tmp = _SYNC_STATE.with_suffix(".tmp.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=str)
    tmp.replace(_SYNC_STATE)


# ─────────────────────────────────────────────────────────────────────────────
# INTERACTIVE PROMPTS
# ─────────────────────────────────────────────────────────────────────────────

def _prompt_choice(prompt: str, options: list[str], default: Optional[str] = None) -> str:
    while True:
        ans = input(f"{prompt} ").strip().lower()
        if not ans and default is not None:
            return default
        if ans in options:
            return ans
        print(f"   ↪ enter one of: {', '.join(options)}")


def _prompt_reason() -> str:
    """Ask user to pick from numbered menu."""
    print("\n   Exit reason:")
    for i, (key, label) in enumerate(KNOWN_REASONS_MENU, 1):
        print(f"     {i}) {label}  [{key}]")
    while True:
        ans = input("   Choose 1-11 (or type custom): ").strip()
        if ans.isdigit():
            n = int(ans)
            if 1 <= n <= len(KNOWN_REASONS_MENU):
                return KNOWN_REASONS_MENU[n-1][0]
        elif ans:
            return ans
        print("   ↪ enter a number or custom reason")


def _prompt_bucket_override(suggested: Optional[str]) -> tuple[Optional[str], bool]:
    """
    Ask user whether to accept the suggested bucket (or None for discretionary).
    Returns (bucket_name, is_discretionary).
    """
    if suggested:
        ans = _prompt_choice(
            f"   Bucket: {suggested.upper()} — accept? (y=yes / d=discretionary / s=skip):",
            ["y", "d", "s"],
            default="y",
        )
        if ans == "y":
            return suggested, False
        if ans == "d":
            return None, True
        return None, False  # caller treats None+False as "skip"
    else:
        ans = _prompt_choice(
            "   No scanner context found. Tag as: (d=discretionary / s=skip):",
            ["d", "s"],
            default="d",
        )
        if ans == "d":
            return None, True
        return None, False


# ─────────────────────────────────────────────────────────────────────────────
# MAIN SYNC LOOP
# ─────────────────────────────────────────────────────────────────────────────

def run_sync(
    days:            int,
    auto_system:     bool,
    dry_run:         bool,
    list_only:       bool = False,
    non_interactive: bool = False,
) -> int:
    print("=" * 70)
    if list_only:
        print("BYBIT TRADE SYNC  —  LIST-ONLY PREVIEW")
    elif non_interactive:
        print("BYBIT TRADE SYNC  —  NON-INTERACTIVE (auto-classify by PnL)")
    else:
        print("BYBIT TRADE SYNC")
    print("=" * 70)

    api_key, api_secret = _load_credentials()
    print(f"Fetching last {days} days of executions from Bybit...")
    execs = fetch_executions(api_key, api_secret, days=days)
    print(f"  Got {len(execs)} executions")

    if not execs:
        print("  Nothing to sync.")
        return 0

    # Filter by already-imported execIds
    state = load_sync_state()
    imported = set(state.get("imported_exec_ids", []))
    new_execs = [e for e in execs if e.get("execId") not in imported]
    print(f"  {len(new_execs)} new (not previously imported)")

    if not new_execs:
        print("  All executions already in journal.")
        return 0

    pairs = group_executions_into_trades(new_execs)
    print(f"  Grouped into {len(pairs)} trade pair(s)")

    # Filter: skip pairs where ALL exec IDs are already imported (defensive)
    pairs = [p for p in pairs if not p.exec_ids or not all(eid in imported for eid in p.exec_ids)]

    if not pairs:
        print("  Nothing new to import.")
        return 0

    # Process each pair
    trades = load_trades()
    cache: dict = {}
    imported_now: list[str] = []
    accepted = skipped = 0

    # ── List-only mode: print a clean table and exit ─────────────────────────
    # Useful for "show me what's there" without committing to interactive prompts.
    if list_only:
        print()
        print("=" * 90)
        print("  PREVIEW — trades that WOULD be imported (no prompts, no writes)")
        print("=" * 90)
        # Header
        print(f"  {'#':<3} {'Symbol':<10} {'Cat':<7} {'Open':<16} {'Close':<16} "
              f"{'PnL%':>7} {'Size':>9} {'Bucket':<14} {'Type':<14}")
        print("  " + "-" * 86)
        # Counters
        n_round_trips = 0
        n_open = 0
        n_orphan = 0
        n_with_ctx = 0
        total_pnl_pct = 0.0
        for i, pair in enumerate(pairs, 1):
            is_open  = pair.close_time_ms is None
            is_orph  = getattr(pair, "orphan_close", False)
            open_dt  = datetime.fromtimestamp(pair.open_time_ms / 1000, tz=timezone.utc)
            close_dt = (datetime.fromtimestamp(pair.close_time_ms / 1000, tz=timezone.utc)
                        if pair.close_time_ms else None)

            # Compute pnl if we have it
            if not is_open and not is_orph and pair.close_price is not None:
                pnl_pct = (pair.close_price - pair.open_price) / pair.open_price * 100
                pnl_str = f"{pnl_pct:+6.2f}%"
                total_pnl_pct += pnl_pct
                n_round_trips += 1
            else:
                pnl_str = "    —  "
                if is_open:    n_open += 1
                elif is_orph:  n_orphan += 1

            # Scanner context (only for non-orphan)
            if is_orph:
                ctx = None
                trade_type = f"orphan/{pair.stop_order_type or 'manual'}"
            else:
                ctx = find_scanner_context(pair.base, pair.open_time_ms, cache)
                if is_open:
                    trade_type = "open"
                else:
                    trade_type = "round-trip"
            if ctx:
                bucket = ctx["_bucket"][:13]
                n_with_ctx += 1
            elif is_orph:
                bucket = "—"
            else:
                bucket = "discretionary"

            open_str  = open_dt.strftime("%m-%d %H:%M")
            close_str = close_dt.strftime("%m-%d %H:%M") if close_dt else "(open)"
            size_str  = f"${pair.open_value:>7.0f}"
            print(f"  {i:>3} {pair.base:<10} {pair.category:<7} {open_str:<16} "
                  f"{close_str:<16} {pnl_str:>7} {size_str:>9} "
                  f"{bucket:<14} {trade_type:<14}")

        print()
        print("  " + "─" * 86)
        print(f"  Total trades found    : {len(pairs)}")
        print(f"    Closed round-trips  : {n_round_trips}  "
              f"(sum of PnL%: {total_pnl_pct:+.2f}%)")
        print(f"    Still-open          : {n_open}")
        print(f"    Orphan closes       : {n_orphan}  (need longer --days to resolve)")
        print(f"  With scanner context  : {n_with_ctx}")
        print("=" * 90)
        print(f"\n  This is a preview only — nothing was written.")
        print(f"  To import: re-run without --list-only flag")
        print(f"  To extend window so orphan closes resolve: --days 30 (or larger)")
        return 0

    for pair in pairs:
        is_open_only  = pair.close_time_ms is None
        is_orphan     = getattr(pair, "orphan_close", False)
        open_dt  = datetime.fromtimestamp(pair.open_time_ms / 1000, tz=timezone.utc)
        close_dt = (datetime.fromtimestamp(pair.close_time_ms / 1000, tz=timezone.utc)
                    if pair.close_time_ms else None)

        # Auto pnl — only for trades where we have BOTH open and close data
        if not is_open_only and not is_orphan and pair.close_price is not None:
            pnl_pct  = (pair.close_price - pair.open_price) / pair.open_price * 100
            pnl_usdt = (pair.close_price - pair.open_price) * pair.open_qty - pair.fees
        else:
            pnl_pct = pnl_usdt = None

        # Look up scanner context (skip for orphan closes — open time unknown)
        if is_orphan:
            ctx = None
        else:
            ctx = find_scanner_context(pair.base, pair.open_time_ms, cache)
        suggested_bucket = ctx.get("_bucket") if ctx else None

        # Show summary
        print()
        print("─" * 70)
        print(f"  {pair.base}  {pair.symbol}  ({pair.category})")
        if is_orphan:
            # Orphan close — long that opened before our 7-day window
            tp_label = pair.stop_order_type or "manual"
            print(f"  ⚠  ORPHAN CLOSE — long opened before lookback window")
            print(f"  Close : {close_dt.strftime('%Y-%m-%d %H:%M')} UTC  "
                  f"@ ${pair.close_price:.6f} × {pair.close_qty:.4f}  "
                  f"(${pair.open_value:.0f})  trigger={tp_label}")
            print(f"  PnL   : unknown (open price not in this fetch window)")
            print(f"  Note  : extend with --days 30 to capture original open, "
                  f"or skip and re-import with longer window later")
        else:
            print(f"  Open  : {open_dt.strftime('%Y-%m-%d %H:%M')} UTC  "
                  f"@ ${pair.open_price:.6f} × {pair.open_qty:.4f}  "
                  f"(${pair.open_value:.0f})")
            if not is_open_only:
                print(f"  Close : {close_dt.strftime('%Y-%m-%d %H:%M')} UTC  "
                      f"@ ${pair.close_price:.6f}")
                print(f"  PnL   : {pnl_pct:+.2f}%   ${pnl_usdt:+,.2f}  (fees ${pair.fees:.2f})")
            else:
                print(f"  Status: STILL OPEN on Bybit (no close fill yet)")
        if ctx:
            print(f"  Scanner: {ctx['_bucket'].upper()}  "
                  f"confluence={ctx.get('confluence')}  "
                  f"scanners={ctx.get('scanner_count')}/4  "
                  f"(snapshot {ctx['_snapshot_age_h']:.1f}h before fill)")
        elif not is_orphan:
            print(f"  Scanner: no context found in last 24h of master_radar snapshots")

        # Decision flow
        if non_interactive:
            # Auto-classify everything based on what we know.
            # No scanner context = discretionary. PnL sign = exit reason for closed.
            if ctx and suggested_bucket:
                bucket, is_disc = suggested_bucket, False
                print(f"  Auto: bucket={bucket} (from scanner context)")
            else:
                bucket, is_disc = None, True
                print(f"  Auto: discretionary (no scanner context)")
        elif auto_system and ctx:
            bucket, is_disc = suggested_bucket, False
            print(f"  Auto-accepted as {bucket}")
        else:
            bucket, is_disc = _prompt_bucket_override(suggested_bucket)
            if bucket is None and not is_disc:
                print(f"  ↪ Skipped")
                skipped += 1
                continue

        # Reason for closed trades
        reason = None
        if not is_open_only:
            if is_orphan:
                # Orphan close — we don't know the open, just tag as expired
                reason = "expired"
            elif non_interactive:
                # Use Bybit's stopOrderType field to classify accurately:
                #   "TakeProfit" trigger → hit_tp1 (we can't distinguish TP1/TP2/TP3 yet)
                #   "StopLoss" trigger   → hit_stop
                #   "" (manual close)    → manual_profit / manual_loss based on PnL sign
                close_trigger = getattr(pair, "close_stop_order_type", "") or ""
                if close_trigger == "TakeProfit":
                    reason = "hit_tp1"
                elif close_trigger == "StopLoss":
                    reason = "hit_stop"
                elif pnl_pct is not None and pnl_pct > 0:
                    reason = "manual_profit"
                else:
                    reason = "manual_loss"
                # Show the classification source so user knows what happened
                source = f"trigger={close_trigger}" if close_trigger else f"manual close (PnL {pnl_pct:+.2f}%)"
                print(f"  Auto: reason={reason}  ({source})")
            else:
                reason = _prompt_reason()

        # Build journal entry
        trade = {
            "base":             pair.base,
            "category":         pair.category,   # "linear" (perp) or "spot"
            "entry_price":      round(pair.open_price, 8),
            "entry_time":       open_dt.isoformat(),
            "size_usdt":        round(pair.open_value, 2),
            "is_discretionary": is_disc,
            "bucket":           bucket if not is_disc else None,
            "confluence":       ctx.get("confluence") if (ctx and not is_disc) else None,
            "scanners_count":   ctx.get("scanner_count") if (ctx and not is_disc) else None,
            "ignition_tier":    ctx.get("ignition_tier") if (ctx and not is_disc) else None,
            "perp_tier":        ctx.get("perp_tier")     if (ctx and not is_disc) else None,
            "spot_tier":        ctx.get("spot_tier")     if (ctx and not is_disc) else None,
            "trend_tier":       ctx.get("trend_tier")    if (ctx and not is_disc) else None,
            "pct_24h_at_entry": ctx.get("price_24h_pct") if (ctx and not is_disc) else None,
            "system_stop":      None,
            "system_tp1":       None,
            "system_tp2":       None,
            "system_tp3":       None,
            "price_24h_after":  None,
            "price_48h_after":  None,
            "price_7d_after":   None,
            "closed":           not is_open_only,
            "exit_price":       round(pair.close_price, 8) if pair.close_price else None,
            "exit_time":        close_dt.isoformat() if close_dt else None,
            "exit_reason":      reason,
            "pnl_pct":          round(pnl_pct, 2)  if pnl_pct  is not None else None,
            "pnl_usdt":         round(pnl_usdt, 2) if pnl_usdt is not None else None,
            "notes":            "imported via bybit sync",
            "_bybit_exec_ids":  pair.exec_ids,
        }
        # Trade plan (if scanner ctx had one)
        if ctx and not is_disc and isinstance(ctx.get("trade_plan"), dict):
            tp = ctx["trade_plan"]
            trade["system_stop"] = tp.get("stop")
            tps = tp.get("take_profits") or []
            if len(tps) >= 1: trade["system_tp1"] = tps[0].get("price")
            if len(tps) >= 2: trade["system_tp2"] = tps[1].get("price")
            if len(tps) >= 3: trade["system_tp3"] = tps[2].get("price")

        if dry_run:
            print(f"  [dry-run] would import")
        else:
            trades.append(trade)
            imported_now.extend(pair.exec_ids or [])
        accepted += 1

    # Persist
    if not dry_run and accepted:
        save_trades(trades)
        state["imported_exec_ids"] = list(imported | set(imported_now))
        save_sync_state(state)

    print()
    print("=" * 70)
    print(f"  Imported : {accepted}")
    print(f"  Skipped  : {skipped}")
    print(f"  Total in journal : {len(trades)}")
    if dry_run:
        print(f"  (DRY RUN — nothing written to disk)")
    print("=" * 70)
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description="Bybit trade sync — import recent fills into trade journal",
    )
    parser.add_argument("--days", type=int, default=7,
                        help="How many days back to fetch (default 7)")
    parser.add_argument("--auto-system", action="store_true",
                        help="Auto-accept system picks without prompting (still asks for reason)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be imported without writing")
    parser.add_argument("--list-only", action="store_true",
                        help="Print trade table and exit. No prompts, no writes. "
                             "Quick way to preview what's there before committing time.")
    parser.add_argument("--non-interactive", action="store_true",
                        help="Auto-classify everything (bucket=discretionary, "
                             "reason=manual_profit/manual_loss by PnL sign). "
                             "No prompts. Useful for backfill imports.")
    args = parser.parse_args()

    return run_sync(
        days            = args.days,
        auto_system     = args.auto_system,
        dry_run         = args.dry_run,
        list_only       = args.list_only,
        non_interactive = args.non_interactive,
    )


if __name__ == "__main__":
    sys.exit(main())
