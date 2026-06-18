"""
================================================================================
TRADE JOURNAL  v1.0
================================================================================
Minimal-friction trade tracking. Two weeks of disciplined logging will tell us
whether the scanner system has a real timing problem or whether observed losses
are normal variance / discretionary deviation.

Design philosophy:
  - One command per action, short
  - Auto-attach scanner context (bucket, confluence, scanners) from master_radar
  - Always show open positions before new actions (forces awareness)
  - Append-only JSON storage you can hand-edit if needed
  - Auto-fetch outcome prices from Bybit/Binance (no API key needed)

Storage:
  outputs/journal/trades.json     — single source of truth

CLI:
  python trade_journal.py open ENSO 0.93                     # auto-attach context
  python trade_journal.py open RANDOM 1.00 --discretionary   # outside system
  python trade_journal.py status                             # what's open
  python trade_journal.py close ENSO 1.05 --reason hit_tp1
  python trade_journal.py review                             # all closed
  python trade_journal.py review --last 14
  python trade_journal.py review --bucket convergence

Reasons (closed trades):
  hit_tp1, hit_tp2, hit_tp3      — trade plan worked
  hit_stop                       — stop loss hit
  manual_profit                  — you took profit early
  manual_loss                    — you cut a loser early
  panicked                       — you sold on emotion
  gut_feel                       — discretionary close, no rule
  trailing_stop                  — trailing stop hit
  thesis_invalidated             — thesis broken, exit cleanly
  expired                        — held too long, gave up
================================================================================
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
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

_TRADES_FILE  = _JOURNAL_DIR / "trades.json"
_MASTER_RADAR = _SCANNER_DIR / "master_radar_LATEST.json"

KNOWN_REASONS = [
    "hit_tp1", "hit_tp2", "hit_tp3",
    "hit_stop", "trailing_stop",
    "manual_profit", "manual_loss",
    "panicked", "gut_feel",
    "thesis_invalidated", "expired",
]


# ─────────────────────────────────────────────────────────────────────────────
# TRADE RECORD
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Trade:
    base:           str
    entry_price:    float
    entry_time:     str
    size_usdt:      Optional[float] = None
    is_discretionary: bool = False

    bucket:         Optional[str]   = None
    confluence:     Optional[float] = None
    scanners_count: Optional[int]   = None
    ignition_tier:  Optional[str]   = None
    perp_tier:      Optional[str]   = None
    spot_tier:      Optional[str]   = None
    trend_tier:     Optional[str]   = None
    pct_24h_at_entry: Optional[float] = None
    system_stop:    Optional[float] = None
    system_tp1:     Optional[float] = None
    system_tp2:     Optional[float] = None
    system_tp3:     Optional[float] = None

    price_24h_after: Optional[float] = None
    price_48h_after: Optional[float] = None
    price_7d_after:  Optional[float] = None

    closed:         bool             = False
    exit_price:     Optional[float]  = None
    exit_time:      Optional[str]    = None
    exit_reason:    Optional[str]    = None
    pnl_pct:        Optional[float]  = None
    pnl_usdt:       Optional[float]  = None
    notes:          str              = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# STORAGE
# ─────────────────────────────────────────────────────────────────────────────

def load_trades() -> list[dict]:
    if not _TRADES_FILE.exists():
        return []
    try:
        with open(_TRADES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️  Could not read {_TRADES_FILE}: {e}")
        return []


def save_trades(trades: list[dict]) -> None:
    """Atomic write — temp file then rename, prevents corruption."""
    tmp = _TRADES_FILE.with_suffix(".tmp.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(trades, f, indent=2, default=str)
    tmp.replace(_TRADES_FILE)


# ─────────────────────────────────────────────────────────────────────────────
# SCANNER CONTEXT LOOKUP
# ─────────────────────────────────────────────────────────────────────────────

def find_in_master_radar(base: str) -> Optional[dict]:
    if not _MASTER_RADAR.exists():
        return None
    try:
        with open(_MASTER_RADAR, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    base_upper = base.upper()
    for bucket in ("convergence", "strong_setup", "single_scanner", "extended"):
        for entry in data.get(bucket, []) or []:
            if entry.get("base", "").upper() == base_upper:
                return {**entry, "_bucket": bucket}
    return None


# ─────────────────────────────────────────────────────────────────────────────
# PRICE FETCHING
# ─────────────────────────────────────────────────────────────────────────────

def _http_get(url: str, timeout: float = 10.0) -> Optional[dict]:
    try:
        req = Request(url, headers={"User-Agent": "scanner_v3/trade_journal"})
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (URLError, HTTPError, json.JSONDecodeError, TimeoutError):
        return None


def fetch_price_at(base: str, target_time: datetime) -> Optional[float]:
    """1h close nearest target_time, Bybit first, Binance fallback."""
    symbol = f"{base.upper()}USDT"
    target_ms = int(target_time.timestamp() * 1000)
    start_ms  = target_ms - 60 * 60 * 1000
    end_ms    = target_ms + 60 * 60 * 1000

    url_bybit = (
        f"https://api.bybit.com/v5/market/kline?"
        f"category=linear&symbol={symbol}&interval=60"
        f"&start={start_ms}&end={end_ms}&limit=10"
    )
    data = _http_get(url_bybit)
    if data and data.get("retCode") == 0:
        klines = data.get("result", {}).get("list", []) or []
        if klines:
            best = min(klines, key=lambda k: abs(int(k[0]) - target_ms))
            try:
                return float(best[4])
            except (ValueError, IndexError):
                pass

    url_binance = (
        f"https://api.binance.com/api/v3/klines?"
        f"symbol={symbol}&interval=1h"
        f"&startTime={start_ms}&endTime={end_ms}&limit=10"
    )
    data = _http_get(url_binance)
    if isinstance(data, list) and data:
        best = min(data, key=lambda k: abs(int(k[0]) - target_ms))
        try:
            return float(best[4])
        except (ValueError, IndexError):
            pass

    return None


def fetch_outcome_snapshots(base: str, entry_time_iso: str) -> dict:
    entry_dt = datetime.fromisoformat(entry_time_iso)
    if entry_dt.tzinfo is None:
        entry_dt = entry_dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)

    out = {}
    for label, hours in (("price_24h_after", 24), ("price_48h_after", 48), ("price_7d_after", 24*7)):
        target = entry_dt + timedelta(hours=hours)
        if target > now:
            out[label] = None
        else:
            out[label] = fetch_price_at(base, target)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# COMMANDS
# ─────────────────────────────────────────────────────────────────────────────

def cmd_open(args) -> int:
    base        = args.base.upper()
    entry_price = float(args.entry)
    now_iso     = datetime.now(timezone.utc).isoformat()

    trades = load_trades()
    open_existing = [t for t in trades if not t.get("closed")]
    if open_existing:
        print(f"\n📂 You currently have {len(open_existing)} open position(s):")
        for t in open_existing:
            print(f"   {t['base']:<10}  entry=${t['entry_price']:<10}  "
                  f"opened={t['entry_time'][:16]}")
        print()

    for t in open_existing:
        if t["base"] == base:
            print(f"⚠️  You already have an open position in {base} "
                  f"(entry=${t['entry_price']}, opened {t['entry_time'][:16]})")
            print(f"   Close it first with: python trade_journal.py close {base} <exit_price> --reason ...")
            return 1

    trade = Trade(
        base             = base,
        entry_price      = entry_price,
        entry_time       = now_iso,
        size_usdt        = args.size,
        is_discretionary = args.discretionary,
    )

    if not args.discretionary:
        ctx = find_in_master_radar(base)
        if ctx:
            trade.bucket           = ctx.get("_bucket")
            trade.confluence       = ctx.get("confluence")
            trade.scanners_count   = ctx.get("scanner_count")
            trade.ignition_tier    = ctx.get("ignition_tier")
            trade.perp_tier        = ctx.get("perp_tier")
            trade.spot_tier        = ctx.get("spot_tier")
            trade.trend_tier       = ctx.get("trend_tier")
            trade.pct_24h_at_entry = ctx.get("price_24h_pct")
            tp = ctx.get("trade_plan")
            if isinstance(tp, dict):
                trade.system_stop = tp.get("stop")
                tps = tp.get("take_profits") or []
                if len(tps) >= 1: trade.system_tp1 = tps[0].get("price")
                if len(tps) >= 2: trade.system_tp2 = tps[1].get("price")
                if len(tps) >= 3: trade.system_tp3 = tps[2].get("price")
            print(f"✓ Found {base} in master_radar — bucket={trade.bucket}, "
                  f"confluence={trade.confluence}, scanners={trade.scanners_count}/4")
        else:
            print(f"⚠️  {base} not found in master_radar_LATEST.json")
            print(f"   This trade will be marked discretionary. "
                  f"If unintended, run scanner first then re-log.")
            trade.is_discretionary = True

    trades.append(trade.to_dict())
    save_trades(trades)

    print(f"\n✓ Opened {base} @ ${entry_price}")
    if trade.system_stop:
        risk_pct = (trade.system_stop - entry_price) / entry_price * 100
        print(f"   System stop: ${trade.system_stop:.6f} ({risk_pct:+.2f}%)")
    if trade.system_tp1:
        gain_pct = (trade.system_tp1 - entry_price) / entry_price * 100
        print(f"   System TP1:  ${trade.system_tp1:.6f} ({gain_pct:+.2f}%)")
    return 0


def cmd_close(args) -> int:
    base       = args.base.upper()
    exit_price = float(args.exit)
    reason     = args.reason

    if reason not in KNOWN_REASONS and not args.force:
        print(f"⚠️  Reason '{reason}' is not in known list:")
        for r in KNOWN_REASONS:
            print(f"     {r}")
        print(f"   Use --force to use a custom reason anyway.")
        return 1

    trades = load_trades()
    open_match = None
    for t in trades:
        if t["base"] == base and not t.get("closed"):
            open_match = t
            break

    if open_match is None:
        print(f"⚠️  No open position in {base}")
        return 1

    now_iso  = datetime.now(timezone.utc).isoformat()
    entry    = open_match["entry_price"]
    pnl_pct  = (exit_price - entry) / entry * 100
    pnl_usdt = None
    if open_match.get("size_usdt"):
        pnl_usdt = (exit_price - entry) / entry * open_match["size_usdt"]

    print(f"  Fetching 24h/48h/7d outcome prices for {base}...")
    snaps = fetch_outcome_snapshots(base, open_match["entry_time"])

    open_match.update({
        "closed":          True,
        "exit_price":      exit_price,
        "exit_time":       now_iso,
        "exit_reason":     reason,
        "pnl_pct":         round(pnl_pct, 2),
        "pnl_usdt":        round(pnl_usdt, 2) if pnl_usdt is not None else None,
        "price_24h_after": snaps.get("price_24h_after"),
        "price_48h_after": snaps.get("price_48h_after"),
        "price_7d_after":  snaps.get("price_7d_after"),
        "notes":           args.notes or open_match.get("notes", ""),
    })
    save_trades(trades)

    sign = "+" if pnl_pct >= 0 else ""
    print(f"\n✓ Closed {base} @ ${exit_price}  →  {sign}{pnl_pct:.2f}%  ({reason})")
    if pnl_usdt is not None:
        print(f"   PnL: ${pnl_usdt:+,.2f}")

    e = entry
    for label, val in (
        ("24h", snaps.get("price_24h_after")),
        ("48h", snaps.get("price_48h_after")),
        ("7d",  snaps.get("price_7d_after")),
    ):
        if val:
            held_pct = (val - e) / e * 100
            print(f"   If held {label}: ${val:.6f} ({held_pct:+.2f}%)")
    return 0


def cmd_status(args) -> int:
    trades = load_trades()
    open_trades   = [t for t in trades if not t.get("closed")]
    closed_trades = [t for t in trades if t.get("closed")]

    print()
    print("=" * 80)
    print(f"  TRADE JOURNAL STATUS")
    print("=" * 80)
    print(f"  Open positions:   {len(open_trades)}")
    print(f"  Closed trades:    {len(closed_trades)}")

    if not open_trades:
        print("\n  (no open positions)")
        return 0

    print()
    print(f"  {'Symbol':<10} {'Entry':>12}  {'Opened':<20}  Bucket / Conf  Source")
    print("  " + "-" * 76)
    for t in open_trades:
        bucket = t.get("bucket") or "—"
        conf   = t.get("confluence")
        conf_str = f"{conf:.1f}" if conf is not None else "—"
        source = "discretionary" if t.get("is_discretionary") else "system"
        print(f"  {t['base']:<10} ${t['entry_price']:>10.6f}  "
              f"{t['entry_time'][:16]:<20}  {bucket}/{conf_str}  {source}")

    print()
    return 0


def cmd_review(args) -> int:
    trades = load_trades()
    closed = [t for t in trades if t.get("closed")]

    if args.last:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.last)
        closed = [
            t for t in closed
            if datetime.fromisoformat(t["entry_time"]).replace(tzinfo=timezone.utc) >= cutoff
        ]
    if args.bucket:
        closed = [t for t in closed if (t.get("bucket") or "").lower() == args.bucket.lower()]

    if not closed:
        print("⚠️  No closed trades match those filters.")
        return 0

    print()
    print("=" * 80)
    print(f"  TRADE JOURNAL REVIEW")
    if args.last:
        print(f"  Last {args.last} days")
    if args.bucket:
        print(f"  Bucket: {args.bucket}")
    print(f"  Closed trades in scope: {len(closed)}")
    print("=" * 80)

    wins   = [t for t in closed if (t.get("pnl_pct") or 0) > 0]
    losses = [t for t in closed if (t.get("pnl_pct") or 0) < 0]
    win_rate = len(wins) / len(closed) * 100 if closed else 0.0

    avg_win   = sum((t.get("pnl_pct") or 0) for t in wins)   / len(wins)   if wins   else 0.0
    avg_loss  = sum((t.get("pnl_pct") or 0) for t in losses) / len(losses) if losses else 0.0
    total_pct = sum((t.get("pnl_pct") or 0) for t in closed)
    expectancy = (win_rate/100 * avg_win) + ((1-win_rate/100) * avg_loss)

    print(f"\n  Win rate     : {win_rate:.1f}%   ({len(wins)} wins / {len(losses)} losses)")
    print(f"  Avg win      : {avg_win:+.2f}%")
    print(f"  Avg loss     : {avg_loss:+.2f}%")
    print(f"  Expectancy   : {expectancy:+.2f}% per trade")
    print(f"  Sum of pnl%  : {total_pct:+.2f}%")

    print(f"\n  ── By bucket ──")
    buckets: dict[str, list] = {}
    for t in closed:
        b = t.get("bucket") or "discretionary"
        buckets.setdefault(b, []).append(t)
    print(f"  {'Bucket':<18} {'Trades':>7} {'Win%':>7} {'AvgPnL':>9} {'Total':>9}")
    for b, trs in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        bw = [t for t in trs if (t.get("pnl_pct") or 0) > 0]
        win_pct = len(bw) / len(trs) * 100
        avg = sum((t.get("pnl_pct") or 0) for t in trs) / len(trs)
        tot = sum((t.get("pnl_pct") or 0) for t in trs)
        print(f"  {b:<18} {len(trs):>7} {win_pct:>6.1f}% {avg:>+8.2f}% {tot:>+8.2f}%")

    print(f"\n  ── By exit reason ──")
    reasons: dict[str, list] = {}
    for t in closed:
        r = t.get("exit_reason") or "unknown"
        reasons.setdefault(r, []).append(t)
    print(f"  {'Reason':<22} {'Trades':>7} {'Win%':>7} {'AvgPnL':>9}")
    for r, trs in sorted(reasons.items(), key=lambda kv: -len(kv[1])):
        bw = [t for t in trs if (t.get("pnl_pct") or 0) > 0]
        win_pct = len(bw) / len(trs) * 100
        avg = sum((t.get("pnl_pct") or 0) for t in trs) / len(trs)
        print(f"  {r:<22} {len(trs):>7} {win_pct:>6.1f}% {avg:>+8.2f}%")

    sys_trades  = [t for t in closed if not t.get("is_discretionary")]
    disc_trades = [t for t in closed if t.get("is_discretionary")]
    if sys_trades and disc_trades:
        sw = sum(1 for t in sys_trades  if (t.get("pnl_pct") or 0) > 0)
        dw = sum(1 for t in disc_trades if (t.get("pnl_pct") or 0) > 0)
        s_avg = sum((t.get("pnl_pct") or 0) for t in sys_trades)  / len(sys_trades)
        d_avg = sum((t.get("pnl_pct") or 0) for t in disc_trades) / len(disc_trades)
        print(f"\n  ── System vs discretionary ──")
        print(f"  System picks       : {len(sys_trades):>3} trades, "
              f"win {sw/len(sys_trades)*100:.1f}%, avg {s_avg:+.2f}%")
        print(f"  Discretionary      : {len(disc_trades):>3} trades, "
              f"win {dw/len(disc_trades)*100:.1f}%, avg {d_avg:+.2f}%")

    print(f"\n  ── Per-trade detail ──")
    print(f"  {'Symbol':<10} {'Entry':>10} {'Exit':>10}  {'PnL%':>7}  "
          f"{'Reason':<18} Bucket")
    print("  " + "-" * 76)
    for t in sorted(closed, key=lambda t: t.get("entry_time", ""), reverse=True):
        pnl = (t.get("pnl_pct") or 0)
        sign = "+" if pnl >= 0 else ""
        bucket = t.get("bucket") or ("disc" if t.get("is_discretionary") else "—")
        print(f"  {t['base']:<10} ${t['entry_price']:>8.6f} ${t.get('exit_price',0):>8.6f}  "
              f"{sign}{pnl:>6.2f}%  {(t.get('exit_reason') or '—'):<18} {bucket}")

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
        description="Trade Journal v1.0 — track entries, exits, outcomes",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_open = sub.add_parser("open", help="Open a new trade")
    p_open.add_argument("base",  help="Coin symbol (e.g., ENSO)")
    p_open.add_argument("entry", help="Entry price (e.g., 0.93)")
    p_open.add_argument("--size", type=float, default=None, help="Position size in USDT")
    p_open.add_argument("--discretionary", action="store_true",
                        help="Mark as discretionary (not from system pick)")

    p_close = sub.add_parser("close", help="Close an open trade")
    p_close.add_argument("base",   help="Coin symbol")
    p_close.add_argument("exit",   help="Exit price")
    p_close.add_argument("--reason", required=True,
                         help=f"Exit reason. Suggested: {', '.join(KNOWN_REASONS)}")
    p_close.add_argument("--notes", default="", help="Optional notes")
    p_close.add_argument("--force", action="store_true",
                         help="Allow custom exit reason not in known list")

    sub.add_parser("status", help="Show all open positions")

    p_review = sub.add_parser("review", help="Review closed trades — diagnostic stats")
    p_review.add_argument("--last",   type=int, default=None,
                          help="Only include trades from the last N days")
    p_review.add_argument("--bucket", type=str, default=None,
                          help="Filter by bucket (convergence, strong_setup, single_scanner, extended)")

    args = parser.parse_args()

    if args.cmd == "open":   return cmd_open(args)
    if args.cmd == "close":  return cmd_close(args)
    if args.cmd == "status": return cmd_status(args)
    if args.cmd == "review": return cmd_review(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
