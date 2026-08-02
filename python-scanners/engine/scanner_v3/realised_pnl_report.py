"""
================================================================================
REALISED P&L REPORT  —  the first honest performance number this system has had
================================================================================
Every performance claim in this project so far has been a SUM OF PERCENTAGES —
"93 trades, +267.29%", "145 round-trips, +323.62%". That metric is arithmetic on
unrelated quantities: the live record contains positions from $155 to $270,188,
so a +37% on $2.5k counts the same as a -5% on $102k. It cannot be reconciled
with an account balance, and when checked against one it wasn't
(see memory: feedback_system_performance_validated, retracted 2026-08-02).

This asks Bybit for realised P&L in DOLLARS instead.

  /v5/position/closed-pnl returns each CLOSED position already netted by the
  exchange: closedPnl (USD), cumEntryValue, avgEntryPrice, avgExitPrice, side,
  leverage, timestamps. No fill-pairing, so none of the failure modes of the
  execution-list approach: no phantom "still open" trades, no orphan closes, no
  double-counted partial exits.

What it reports:
  - Total realised P&L in USD — the number that must reconcile with the balance
  - Win rate, avg win $, avg loss $, expectancy $/trade, profit factor
  - Return on RISK-FREE basis: PnL as % of the position's entry value, so a
    small winner cannot masquerade as a large one
  - Concentration: biggest winners and losers, and what share of total P&L the
    top 5 of each represent
  - Monthly breakdown — did it ever work, or was it always negative?
  - Position sizing vs the documented 1-1.5% risk rule

⚠️ Scope and honesty notes:
  - closed-pnl is DERIVATIVES ONLY (linear/inverse). Bybit does not track spot
    P&L this way because spot has no position lifecycle. Spot trades are NOT in
    this report, and the report says so rather than quietly omitting them.
  - Bybit caps closed-pnl history (typically ~2 years, 7-day query windows).
    If a chunk hits its pagination cap the report FLAGS the run as incomplete
    rather than printing a total that silently understates.
  - This measures what was TRADED, not what the scanner suggested. The execution
    record carries no scanner context (0 of 216 trades had any), so it cannot
    attribute results to the system. It establishes a baseline, nothing more.

Run:
  python realised_pnl_report.py --days 200
  python realised_pnl_report.py --days 400 --json
================================================================================
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

from trade_journal_sync import _bybit_signed_get, _BYBIT_BASE  # noqa: F401

_OUT_DIR = None
try:
    from backtest_confluence import _OUTPUT_DIR as _BT_DIR
    _OUT_DIR = _BT_DIR.parent / "journal"
except Exception:
    pass

CHUNK_MS = 7 * 24 * 60 * 60 * 1000          # Bybit closed-pnl max window
PAGE_LIMIT = 100


def fetch_closed_pnl(days: int, api_key: str, api_secret: str,
                     category: str = "linear") -> tuple[list[dict], bool]:
    """All closed positions in the window. Returns (rows, complete?)."""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 24 * 60 * 60 * 1000
    rows: list[dict] = []
    complete = True

    chunk_end = end_ms
    while chunk_end > start_ms:
        chunk_start = max(start_ms, chunk_end - CHUNK_MS + 1)
        cursor = None
        pages = 0
        while True:
            params = {"category": category, "startTime": chunk_start,
                      "endTime": chunk_end, "limit": PAGE_LIMIT}
            if cursor:
                params["cursor"] = cursor
            data = _bybit_signed_get("/v5/position/closed-pnl", params, api_key, api_secret)
            if not data or data.get("retCode") != 0:
                print(f"  ⚠️  fetch failed {_d(chunk_start)}→{_d(chunk_end)}: "
                      f"{(data or {}).get('retMsg', 'no response')}")
                complete = False
                break
            result = data.get("result", {}) or {}
            batch = result.get("list", []) or []
            rows.extend(batch)
            cursor = result.get("nextPageCursor")
            pages += 1
            if not cursor or not batch:
                break
            if pages >= 50:                  # runaway guard
                print(f"  ⚠️  pagination cap in {_d(chunk_start)}→{_d(chunk_end)} "
                      f"— window INCOMPLETE")
                complete = False
                break
            time.sleep(0.12)                 # stay under the rate limit
        chunk_end = chunk_start - 1
        time.sleep(0.12)
    return rows, complete


def _d(ms) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def normalise(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        try:
            pnl = float(r.get("closedPnl") or 0)
            entry_val = float(r.get("cumEntryValue") or 0)
            out.append({
                "symbol":   r.get("symbol", "?"),
                "side":     r.get("side", "?"),          # side that CLOSED it
                "pnl_usd":  pnl,
                "entry_val": entry_val,
                "pct":      (100.0 * pnl / entry_val) if entry_val else 0.0,
                "leverage": float(r.get("leverage") or 0),
                "entry_px": float(r.get("avgEntryPrice") or 0),
                "exit_px":  float(r.get("avgExitPrice") or 0),
                "closed_at": int(r.get("updatedTime") or r.get("createdTime") or 0),
            })
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["closed_at"])
    return out


def build_report(trades: list[dict], days: int, complete: bool, account: float) -> str:
    L, sep, dash = [], "=" * 86, "-" * 86
    add = L.append
    add(sep)
    add("  REALISED P&L  —  actual dollars from Bybit closed positions")
    add(f"  window: last {days} days · derivatives (linear) only · "
        f"generated {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    add(sep)
    if not complete:
        add("  ⚠️  INCOMPLETE — at least one window hit its pagination cap or failed.")
        add("     Totals below UNDERSTATE the true figures. Do not quote them as final.")
        add("")
    add("  ⚠️  SPOT IS NOT INCLUDED — Bybit tracks closed P&L for derivatives only.")
    add("     A full picture needs spot handled separately.")
    add("")

    if not trades:
        add("  No closed derivative positions in this window.")
        add(sep)
        return "\n".join(L)

    pnls = [t["pnl_usd"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total = sum(pnls)
    gross_win, gross_loss = sum(wins), abs(sum(losses))

    add("  HEADLINE")
    add(dash)
    add(f"  Closed positions   : {len(trades)}")
    add(f"  TOTAL REALISED P&L : ${total:+,.2f}")
    if account:
        add(f"  As % of account    : {100.0 * total / account:+.2f}%  (on ${account:,.0f})")
    add(f"  Win rate           : {100.0 * len(wins) / len(trades):.1f}%  "
        f"({len(wins)}W / {len(losses)}L)")
    if wins:
        add(f"  Avg win            : ${statistics.mean(wins):+,.2f}   "
            f"(median ${statistics.median(wins):+,.2f})")
    if losses:
        add(f"  Avg loss           : ${statistics.mean(losses):+,.2f}   "
            f"(median ${statistics.median(losses):+,.2f})")
    add(f"  Expectancy         : ${total / len(trades):+,.2f} per trade")
    if gross_loss:
        add(f"  Profit factor      : {gross_win / gross_loss:.2f}   "
            f"(gross +${gross_win:,.0f} / −${gross_loss:,.0f})")
    add("")

    add("  CONCENTRATION  —  is the result one trade or a process?")
    add(dash)
    ranked = sorted(trades, key=lambda t: t["pnl_usd"])
    top5 = ranked[-5:][::-1]
    bot5 = ranked[:5]
    t5 = sum(t["pnl_usd"] for t in top5)
    b5 = sum(t["pnl_usd"] for t in bot5)
    add("  Biggest winners:")
    for t in top5:
        add(f"    {t['symbol']:12} ${t['pnl_usd']:>+10,.2f}  ({t['pct']:>+6.2f}% on "
            f"${t['entry_val']:>10,.0f})  {_d(t['closed_at'])}")
    add("  Biggest losers:")
    for t in bot5:
        add(f"    {t['symbol']:12} ${t['pnl_usd']:>+10,.2f}  ({t['pct']:>+6.2f}% on "
            f"${t['entry_val']:>10,.0f})  {_d(t['closed_at'])}")
    if gross_win:
        add(f"  Top 5 winners = {100.0 * t5 / gross_win:.0f}% of all gross profit")
    if gross_loss:
        add(f"  Top 5 losers  = {100.0 * abs(b5) / gross_loss:.0f}% of all gross loss")
    add("")

    add("  BY MONTH  —  did it ever work?")
    add(dash)
    by_month: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        by_month[datetime.fromtimestamp(t["closed_at"] / 1000, tz=timezone.utc)
                 .strftime("%Y-%m")].append(t["pnl_usd"])
    add(f"  {'month':10} {'n':>5} {'P&L $':>14} {'WR':>7}")
    for m in sorted(by_month):
        v = by_month[m]
        wr = 100.0 * len([x for x in v if x > 0]) / len(v)
        add(f"  {m:10} {len(v):>5} {sum(v):>+14,.2f} {wr:>6.1f}%")
    add("")

    add("  POSITION SIZING  vs the documented 1–1.5% risk rule")
    add(dash)
    vals = sorted(t["entry_val"] for t in trades if t["entry_val"] > 0)
    if vals and account:
        add(f"  Entry value: min ${vals[0]:,.0f} · median ${statistics.median(vals):,.0f} · "
            f"max ${vals[-1]:,.0f}")
        add(f"  Largest position = {100.0 * vals[-1] / account:.0f}% of a "
            f"${account:,.0f} account")
        big = [v for v in vals if v > account]
        add(f"  Positions exceeding full account equity: {len(big)} of {len(vals)}")
    add("")
    add("  ⚠️  This measures what was TRADED, not what the scanner suggested — the")
    add("     execution record carries no scanner context. It is a baseline, not an")
    add("     attribution of results to the system.")
    add(sep)
    return "\n".join(L)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Realised P&L in dollars from Bybit closed positions")
    ap.add_argument("--days", type=int, default=200)
    ap.add_argument("--account", type=float, default=93696.54,
                    help="account size for the %%-of-equity lines")
    ap.add_argument("--json", action="store_true", help="also dump the raw trade list")
    a = ap.parse_args()

    key, secret = os.environ.get("BYBIT_API_KEY"), os.environ.get("BYBIT_API_SECRET")
    if not key or not secret:
        print("ERROR: BYBIT_API_KEY / BYBIT_API_SECRET not set in this terminal.")
        return 1

    print(f"Fetching closed positions for the last {a.days} days...")
    rows, complete = fetch_closed_pnl(a.days, key, secret)
    trades = normalise(rows)
    print(f"  {len(trades)} closed positions\n")

    report = build_report(trades, a.days, complete, a.account)
    print(report)

    if _OUT_DIR:
        try:
            _OUT_DIR.mkdir(parents=True, exist_ok=True)
            (_OUT_DIR / "realised_pnl_LATEST.txt").write_text(report, encoding="utf-8")
            if a.json:
                (_OUT_DIR / "realised_pnl_LATEST.json").write_text(
                    json.dumps(trades, indent=2), encoding="utf-8")
            print(f"\nSaved → {_OUT_DIR / 'realised_pnl_LATEST.txt'}")
        except Exception as e:
            print(f"(could not save: {e})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
