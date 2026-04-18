"""
================================================================================
BYBIT SYNC  v1.0
================================================================================
Auto-imports closed trades from Bybit into Trade Journal.
Fixes the circuit breaker gap — no more manual logging.

How it works:
  1. Fetches closed P&L records from Bybit's V5 API
  2. Matches them against existing journal entries (deduplication)
  3. Creates new journal rows for any fills not yet logged
  4. Updates open journal trades that were closed on-exchange

Prerequisites:
  pip install pybit --break-system-packages

  Set environment variables (or use .env file):
    BYBIT_API_KEY=your_key_here
    BYBIT_API_SECRET=your_secret_here

  Generate keys at: https://www.bybit.com/app/user/api-management
  Required permissions: "Read-Only" on "Positions" and "Orders"
  ⚠️  Do NOT enable "Trade" permission — this module only reads.

Usage:
  python bybit_sync.py                    # sync last 7 days
  python bybit_sync.py --days 30          # sync last 30 days
  python bybit_sync.py --dry-run          # preview without writing
  python bybit_sync.py --once             # single sync, no loop

Drop into your engine/ folder alongside trade_journal.py.
================================================================================
"""

import os
import sys
import json
import time
import argparse
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Paths (same structure as your other engine files) ────────────────────────
_ENGINE_DIR    = Path(__file__).resolve().parent
_PROJECT_ROOT  = _ENGINE_DIR.parent.parent
_CACHE_DIR     = _PROJECT_ROOT / "cache" / "shared_ohlcv"
_LOG_DIR       = _PROJECT_ROOT / "outputs" / "logs"
_SYNC_STATE    = _CACHE_DIR / "bybit_sync_state.json"

for d in (_CACHE_DIR, _LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ── Logging ──────────────────────────────────────────────────────────────────
_log_file = _LOG_DIR / f"bybit_sync_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("bybit_sync")

# ── Import trade_journal ─────────────────────────────────────────────────────
sys.path.insert(0, str(_ENGINE_DIR))
try:
    import trade_journal as tj
except ImportError:
    log.error("trade_journal.py not found in engine/ directory.")
    sys.exit(1)


def _load_sync_state() -> dict:
    """Load last sync timestamp to avoid re-processing."""
    if _SYNC_STATE.exists():
        try:
            return json.loads(_SYNC_STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_sync_ts": 0, "synced_order_ids": []}


def _save_sync_state(state: dict) -> None:
    try:
        # Keep only last 500 order IDs to prevent unbounded growth
        if len(state.get("synced_order_ids", [])) > 500:
            state["synced_order_ids"] = state["synced_order_ids"][-500:]
        _SYNC_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"Could not save sync state: {e}")


def _init_bybit_client():
    """Initialize Bybit HTTP client with API keys."""
    try:
        from pybit.unified_trading import HTTP
    except ImportError:
        log.error(
            "pybit not installed. Run:\n"
            "  pip install pybit --break-system-packages\n"
        )
        sys.exit(1)

    api_key    = os.environ.get("BYBIT_API_KEY", "")
    api_secret = os.environ.get("BYBIT_API_SECRET", "")

    if not api_key or not api_secret:
        log.error(
            "BYBIT_API_KEY and BYBIT_API_SECRET must be set.\n"
            "Generate read-only keys at: https://www.bybit.com/app/user/api-management\n"
            "Then run:\n"
            '  setx BYBIT_API_KEY "your_key_here"\n'
            '  setx BYBIT_API_SECRET "your_secret_here"\n'
        )
        sys.exit(1)

    return HTTP(
        api_key=api_key,
        api_secret=api_secret,
        testnet=False,
    )


def fetch_closed_pnl(client, days: int = 7) -> list[dict]:
    """
    Fetch closed P&L records from Bybit V5 API.
    Returns a list of closed position records.
    """
    log.info(f"Fetching closed P&L from Bybit (last {days} days)...")

    start_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    all_records = []
    cursor = ""

    for page in range(20):  # safety cap
        try:
            params = {
                "category": "linear",
                "startTime": start_ts,
                "limit": 100,
            }
            if cursor:
                params["cursor"] = cursor

            result = client.get_closed_pnl(**params)

            if result["retCode"] != 0:
                log.warning(f"Bybit API error: {result['retMsg']}")
                break

            records = result.get("result", {}).get("list", [])
            if not records:
                break

            all_records.extend(records)
            cursor = result.get("result", {}).get("nextPageCursor", "")

            if not cursor:
                break

            time.sleep(0.2)  # rate limit courtesy

        except Exception as e:
            log.error(f"Error fetching closed P&L page {page}: {e}")
            break

    log.info(f"  Fetched {len(all_records)} closed P&L records from Bybit")
    return all_records


def sync_to_journal(records: list[dict], dry_run: bool = False) -> dict:
    """
    Match Bybit records to journal entries and sync.

    Strategy:
    1. For each Bybit record, check if an order ID was already synced
    2. Check if a journal entry exists with same symbol + similar entry price
    3. If open journal entry found → close it with Bybit's exit data
    4. If no match → create a new completed journal entry

    Returns stats dict with counts.
    """
    state     = _load_sync_state()
    synced    = set(state.get("synced_order_ids", []))
    journal   = tj._load()
    stats     = {"closed": 0, "created": 0, "skipped": 0, "errors": 0}

    for rec in records:
        order_id = rec.get("orderId", "")

        # Skip already synced
        if order_id in synced:
            stats["skipped"] += 1
            continue

        try:
            symbol     = rec.get("symbol", "").replace("USDT", "")
            side       = rec.get("side", "").lower()  # Buy or Sell
            entry_px   = float(rec.get("avgEntryPrice", 0))
            exit_px    = float(rec.get("avgExitPrice", 0))
            closed_pnl = float(rec.get("closedPnl", 0))
            qty        = float(rec.get("qty", 0))
            pos_value  = qty * entry_px
            created_ts = int(rec.get("createdTime", 0))
            updated_ts = int(rec.get("updatedTime", 0))

            if not symbol or entry_px <= 0 or exit_px <= 0:
                stats["errors"] += 1
                continue

            # Determine direction: Bybit "Buy" side on close = was a Short position
            direction = "SHORT" if side == "buy" else "LONG"

            # Calculate risk (estimate — Bybit doesn't store our stop)
            # Use 5% as default stop distance (conservative estimate)
            est_risk = pos_value * 0.05

            # Try to match existing open journal entry
            matched = False
            for row in journal:
                if (row["symbol"].upper() == symbol.upper()
                    and not row["date_closed"]
                    and abs(float(row.get("entry_price", 0)) - entry_px) / entry_px < 0.02):
                    # Found match — close it
                    if not dry_run:
                        risk = float(row.get("risk_usdt", 0)) or est_risk
                        row["date_closed"] = datetime.fromtimestamp(
                            updated_ts / 1000, tz=timezone.utc
                        ).strftime("%Y-%m-%d %H:%M")
                        row["exit_price"]  = round(exit_px, 8)
                        row["pnl_usdt"]    = round(closed_pnl, 2)
                        row["r_multiple"]  = round(closed_pnl / risk, 2) if risk > 0 else 0
                        row["exit_reason"] = _guess_exit_reason(row, exit_px)
                        if not row.get("notes"):
                            row["notes"] = ""
                        row["notes"] = (row["notes"] + f" | bybit_sync:{order_id}").strip(" | ")

                    log.info(
                        f"  CLOSED #{row['id']} {symbol} @ {exit_px:.4f} "
                        f"P&L: ${closed_pnl:+,.2f}"
                    )
                    stats["closed"] += 1
                    matched = True
                    break

            if not matched:
                # No open journal entry — create a completed one
                if not dry_run:
                    new_id = tj._next_id(journal)
                    opened_str = datetime.fromtimestamp(
                        created_ts / 1000, tz=timezone.utc
                    ).strftime("%Y-%m-%d %H:%M")
                    closed_str = datetime.fromtimestamp(
                        updated_ts / 1000, tz=timezone.utc
                    ).strftime("%Y-%m-%d %H:%M")

                    new_row = {col: "" for col in tj.COLUMNS}
                    new_row.update({
                        "id":             new_id,
                        "date_opened":    opened_str,
                        "date_closed":    closed_str,
                        "symbol":         symbol,
                        "scanner":        "bybit_sync",
                        "regime":         "",
                        "conviction":     "",
                        "signals":        "",
                        "entry_price":    round(entry_px, 8),
                        "stop_price":     "",
                        "tp1_price":      "",
                        "tp2_price":      "",
                        "tp3_price":      "",
                        "pos_value_usdt": round(pos_value, 2),
                        "risk_usdt":      round(est_risk, 2),
                        "exit_price":     round(exit_px, 8),
                        "exit_reason":    "manual",
                        "pnl_usdt":       round(closed_pnl, 2),
                        "r_multiple":     round(closed_pnl / est_risk, 2) if est_risk > 0 else 0,
                        "notes":          f"bybit_sync:{order_id} | {direction}",
                    })
                    journal.append(new_row)

                log.info(
                    f"  CREATED #{new_id if not dry_run else '?'} {symbol} "
                    f"{direction} {entry_px:.4f}→{exit_px:.4f} "
                    f"P&L: ${closed_pnl:+,.2f}"
                )
                stats["created"] += 1

            # Mark as synced
            synced.add(order_id)

        except Exception as e:
            log.error(f"  Error processing record {order_id}: {e}")
            stats["errors"] += 1

    # Save
    if not dry_run:
        tj._save(journal)
        state["synced_order_ids"] = list(synced)
        state["last_sync_ts"]    = int(time.time())
        _save_sync_state(state)
        log.info(f"  Journal saved. {len(journal)} total entries.")
    else:
        log.info("  DRY RUN — no changes written.")

    return stats


def _guess_exit_reason(row: dict, exit_px: float) -> str:
    """Try to match exit price to TP levels or stop."""
    tp1 = float(row.get("tp1_price", 0) or 0)
    tp2 = float(row.get("tp2_price", 0) or 0)
    tp3 = float(row.get("tp3_price", 0) or 0)
    stop = float(row.get("stop_price", 0) or 0)

    if stop > 0 and abs(exit_px - stop) / stop < 0.005:
        return "stop"
    if tp1 > 0 and abs(exit_px - tp1) / tp1 < 0.005:
        return "tp1"
    if tp2 > 0 and abs(exit_px - tp2) / tp2 < 0.005:
        return "tp2"
    if tp3 > 0 and abs(exit_px - tp3) / tp3 < 0.005:
        return "tp3"
    return "manual"


def fetch_wallet_balance(client) -> float | None:
    """Fetch USDT wallet balance for display and master_orchestrator."""
    try:
        result = client.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        if result["retCode"] == 0:
            coins = result["result"]["list"][0]["coin"]
            for c in coins:
                if c["coin"] == "USDT":
                    return float(c["walletBalance"])
    except Exception as e:
        log.warning(f"Could not fetch balance: {e}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Bybit → Trade Journal sync")
    parser.add_argument("--days",    type=int, default=7,     help="Days of history to sync")
    parser.add_argument("--dry-run", action="store_true",      help="Preview without writing")
    parser.add_argument("--once",    action="store_true",      help="Single sync (no loop)")
    parser.add_argument("--interval", type=int, default=300,   help="Sync interval in seconds (default: 5 min)")
    args = parser.parse_args()

    print()
    print("=" * 60)
    print("  BYBIT SYNC → Trade Journal")
    print(f"  Syncing last {args.days} days")
    if args.dry_run:
        print("  MODE: DRY RUN (no writes)")
    print("=" * 60)
    print()

    client = _init_bybit_client()

    # Show balance
    balance = fetch_wallet_balance(client)
    if balance is not None:
        log.info(f"  Bybit USDT balance: ${balance:,.2f}")

    while True:
        records = fetch_closed_pnl(client, days=args.days)
        stats   = sync_to_journal(records, dry_run=args.dry_run)

        log.info(
            f"\n  Sync complete: "
            f"{stats['closed']} closed, {stats['created']} created, "
            f"{stats['skipped']} skipped, {stats['errors']} errors"
        )

        if args.once or args.dry_run:
            break

        log.info(f"  Next sync in {args.interval}s...")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
