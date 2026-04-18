"""
================================================================================
TRADE JOURNAL  v1.0
================================================================================
Logs every trade taken from any scanner and produces hard performance analytics.

Answers the core question: "Is the scanner actually making money?"

Tracks:
  - Entry / exit prices, scanner source, regime, conviction score, signals fired
  - R-multiple per trade (P&L normalised to initial risk)
  - Win rate, expectancy, avg winner/loser, max drawdown, equity curve
  - Breakdown by scanner, regime, and top-performing signals

Storage: CSV flat file — human-readable, auditable, importable into Excel.

Commands:
  python trade_journal.py log   --symbol SUI --scanner alpha --conviction 72
                                --entry 3.45 --stop 3.18
                                --tp1 3.80 --tp2 4.15 --tp3 4.75
                                --risk 726 --pos 3867
                                --regime bull
                                --signals "rs_vs_btc_strong,supertrend_bull,vol_burst"
                                [--notes "clean breakout, thin book above"]

  python trade_journal.py close --id 5 --exit 3.80 --reason tp1
  python trade_journal.py close --id 5 --exit 3.21 --reason stop

  python trade_journal.py list           # all trades
  python trade_journal.py list --open    # only open positions
  python trade_journal.py list --closed  # only closed positions

  python trade_journal.py stats          # full performance analytics
  python trade_journal.py stats --scanner alpha
  python trade_journal.py stats --regime bull

  python trade_journal.py delete --id 5 --confirm
================================================================================
"""

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

# Fix Windows cp1252 encoding issues with Unicode box-drawing characters
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ─────────────────────────────────────────────────────────────────────────────
# PATHS & SCHEMA
# ─────────────────────────────────────────────────────────────────────────────
_ENGINE_DIR  = Path(__file__).resolve().parent
_PROJECT_ROOT = _ENGINE_DIR.parent.parent
_JOURNAL_DIR  = _PROJECT_ROOT / "outputs" / "journal"
_JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
JOURNAL_FILE  = _JOURNAL_DIR / "trade_journal.csv"

ACCOUNT_START = 95_255.0   # starting capital USDT

COLUMNS = [
    "id",
    "date_opened",
    "date_closed",
    "symbol",
    "scanner",        # master / alpha / ignition / short
    "regime",         # bull / sideways / bear
    "conviction",     # 0–100 score from scanner
    "signals",        # comma-separated fired signals
    "entry_price",
    "stop_price",
    "tp1_price",
    "tp2_price",
    "tp3_price",
    "pos_value_usdt", # position size in USDT
    "risk_usdt",      # dollars at risk (entry→stop × qty)
    "exit_price",
    "exit_reason",    # tp1 / tp2 / tp3 / stop / manual
    "pnl_usdt",       # realised P&L
    "r_multiple",     # pnl_usdt / risk_usdt
    "notes",
]


# ─────────────────────────────────────────────────────────────────────────────
# CSV HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _load() -> list[dict]:
    if not JOURNAL_FILE.exists():
        return []
    with JOURNAL_FILE.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _save(rows: list[dict]) -> None:
    with JOURNAL_FILE.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _next_id(rows: list[dict]) -> int:
    if not rows:
        return 1
    return max(int(r["id"]) for r in rows) + 1


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


# ─────────────────────────────────────────────────────────────────────────────
# CIRCUIT BREAKER HELPER
# ─────────────────────────────────────────────────────────────────────────────

def get_today_pnl() -> float:
    """
    Returns the sum of all closed trade P&L (pnl_usdt) for today (UTC).
    Used by master_orchestrator's daily circuit breaker (-5% daily loss limit).
    Returns 0.0 if no trades exist or the journal file is missing.
    """
    rows  = _load()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total = 0.0
    for r in rows:
        if r.get("date_closed", "").startswith(today) and r.get("pnl_usdt"):
            try:
                total += float(r["pnl_usdt"])
            except (ValueError, TypeError):
                pass
    return total


def _sanitize_csv(value: str) -> str:
    """Prevent CSV injection by escaping formula-starting characters."""
    if value and value[0] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + value
    return value


def _float(val: str | None, default: float = 0.0) -> float:
    try:
        return float(val) if val not in (None, "", "None") else default
    except (ValueError, TypeError):
        return default


# ─────────────────────────────────────────────────────────────────────────────
# COMMANDS
# ─────────────────────────────────────────────────────────────────────────────

def cmd_log(args) -> None:
    """Add a new trade."""
    rows = _load()
    trade_id = _next_id(rows)

    # Derive risk from pos_value and stop if not provided explicitly
    entry = args.entry
    stop  = args.stop
    pos   = args.pos if args.pos else 0.0
    risk  = args.risk
    if risk is None:
        if entry and stop and pos:
            stop_pct = abs(entry - stop) / entry
            risk = round(pos * stop_pct, 2)
        else:
            risk = 0.0

    row = {
        "id":            trade_id,
        "date_opened":   _now(),
        "date_closed":   "",
        "symbol":        args.symbol.upper(),
        "scanner":       args.scanner.lower(),
        "regime":        args.regime.lower() if args.regime else "",
        "conviction":    args.conviction if args.conviction else "",
        "signals":       _sanitize_csv(args.signals) if args.signals else "",
        "entry_price":   round(entry, 8) if entry else "",
        "stop_price":    round(stop, 8) if stop else "",
        "tp1_price":     round(args.tp1, 8) if args.tp1 else "",
        "tp2_price":     round(args.tp2, 8) if args.tp2 else "",
        "tp3_price":     round(args.tp3, 8) if args.tp3 else "",
        "pos_value_usdt": round(pos, 2),
        "risk_usdt":     round(risk, 2),
        "exit_price":    "",
        "exit_reason":   "",
        "pnl_usdt":      "",
        "r_multiple":    "",
        "notes":         _sanitize_csv(args.notes) if args.notes else "",
    }

    rows.append(row)
    _save(rows)

    print(f"\n  Trade #{trade_id} logged.")
    print(f"  {row['symbol']}  |  entry {row['entry_price']}  stop {row['stop_price']}"
          f"  |  risk ${row['risk_usdt']}  |  scanner: {row['scanner']}\n")


def cmd_close(args) -> None:
    """Close an open trade."""
    rows = _load()
    found = [r for r in rows if int(r["id"]) == args.id]
    if not found:
        print(f"\n  [ERROR] Trade #{args.id} not found.\n")
        sys.exit(1)

    row = found[0]
    if row["date_closed"]:
        print(f"\n  [ERROR] Trade #{args.id} is already closed ({row['date_closed']}).\n")
        sys.exit(1)

    entry    = _float(row["entry_price"])
    pos      = _float(row["pos_value_usdt"])
    risk     = _float(row["risk_usdt"])
    exit_p   = args.exit
    reason   = args.reason.lower()

    if entry > 0 and pos > 0:
        qty     = pos / entry
        pnl     = round((exit_p - entry) * qty, 2)
    else:
        pnl = 0.0

    r_mult = round(pnl / risk, 2) if risk and risk > 0 else None

    row["date_closed"]  = _now()
    row["exit_price"]   = round(exit_p, 8)
    row["exit_reason"]  = reason
    row["pnl_usdt"]     = pnl
    row["r_multiple"]   = r_mult
    if args.notes:
        row["notes"] = (row["notes"] + " | " + args.notes).strip(" | ")

    _save(rows)

    pnl_str = f"+${pnl:,.2f}" if pnl >= 0 else f"-${abs(pnl):,.2f}"
    r_str   = ("N/A" if r_mult is None else
               f"+{r_mult:.2f}R" if r_mult >= 0 else f"{r_mult:.2f}R")
    print(f"\n  Trade #{args.id} closed.  {row['symbol']}  @{exit_p}  [{reason}]")
    print(f"  P&L: {pnl_str}   R: {r_str}\n")


def cmd_list(args) -> None:
    """List trades."""
    rows = _load()
    if not rows:
        print("\n  No trades logged yet.\n")
        return

    if args.open:
        rows = [r for r in rows if not r["date_closed"]]
    elif args.closed:
        rows = [r for r in rows if r["date_closed"]]

    if not rows:
        print("\n  No matching trades.\n")
        return

    # Header
    print()
    print(f"  {'#':<4}  {'Symbol':<8}  {'Scanner':<10}  {'Regime':<10}  "
          f"{'Conv':>4}  {'Entry':>10}  {'Stop':>10}  {'Exit':>10}  "
          f"{'P&L':>9}  {'R':>6}  {'Reason':<8}  {'Opened':<16}")
    print("  " + "─" * 118)

    for r in rows:
        pnl_raw = _float(r["pnl_usdt"], None)
        r_raw   = _float(r["r_multiple"], None)

        pnl_str = (f"+{pnl_raw:,.0f}" if pnl_raw and pnl_raw >= 0
                   else f"{pnl_raw:,.0f}" if pnl_raw
                   else "open")
        r_str   = (f"+{r_raw:.2f}R" if r_raw and r_raw >= 0
                   else f"{r_raw:.2f}R" if r_raw
                   else "—")

        print(f"  {r['id']:<4}  {r['symbol']:<8}  {r['scanner']:<10}  "
              f"{r['regime']:<10}  {r['conviction']:>4}  "
              f"{r['entry_price']:>10}  {r['stop_price']:>10}  "
              f"{r['exit_price'] or '—':>10}  "
              f"{pnl_str:>9}  {r_str:>6}  {r['exit_reason'] or '—':<8}  "
              f"{r['date_opened'][:16]:<16}")

    print()


def cmd_delete(args) -> None:
    """Hard-delete a trade (requires --confirm)."""
    if not args.confirm:
        print("\n  Pass --confirm to permanently delete a trade.\n")
        sys.exit(1)
    rows = _load()
    before = len(rows)
    rows = [r for r in rows if int(r["id"]) != args.id]
    if len(rows) == before:
        print(f"\n  [ERROR] Trade #{args.id} not found.\n")
        sys.exit(1)
    _save(rows)
    print(f"\n  Trade #{args.id} deleted.\n")


def cmd_stats(args) -> None:
    """Performance analytics."""
    rows = _load()

    # Filter closed trades
    closed = [r for r in rows if r["date_closed"]]
    if args.scanner:
        closed = [r for r in closed if r["scanner"] == args.scanner.lower()]
    if args.regime:
        closed = [r for r in closed if r["regime"] == args.regime.lower()]

    open_trades = [r for r in rows if not r["date_closed"]]

    W = 62  # output width

    def line(label="", value="", width=W):
        if not label and not value:
            print(f"  {'─' * (width - 2)}")
            return
        print(f"  {label:<30}{value}")

    def header(title):
        print()
        print("  " + "═" * (W - 2))
        print(f"  {title}")
        print("  " + "═" * (W - 2))

    header("TRADE JOURNAL  —  Performance Analytics")
    print(f"  Account start : ${ACCOUNT_START:>10,.2f} USDT")
    print(f"  Total trades  : {len(rows)}  ({len(closed)} closed, {len(open_trades)} open)")
    if args.scanner or args.regime:
        filters = []
        if args.scanner:
            filters.append(f"scanner={args.scanner}")
        if args.regime:
            filters.append(f"regime={args.regime}")
        print(f"  Filter        : {', '.join(filters)}")

    if not closed:
        print("\n  No closed trades to analyse yet.\n")
        return

    # ── Core metrics ──────────────────────────────────────────────────────────
    r_vals_raw = [_float(r["r_multiple"]) if r["r_multiple"] not in (None, "", "None") else None
                  for r in closed]
    r_vals   = [r for r in r_vals_raw if r is not None]
    pnl_vals = [_float(r["pnl_usdt"])   for r in closed]
    wins     = [r for r in r_vals if r > 0]
    losses   = [r for r in r_vals if r <= 0]

    win_rate   = len(wins) / len(r_vals) * 100 if r_vals else 0.0
    avg_win    = sum(wins)   / len(wins)   if wins   else 0.0
    avg_loss   = sum(losses) / len(losses) if losses else 0.0
    expectancy = sum(r_vals) / len(r_vals) if r_vals else 0.0
    total_pnl  = sum(pnl_vals)
    total_risk = sum(_float(r["risk_usdt"]) for r in closed)
    best_trade = max(closed, key=lambda r: _float(r["r_multiple"]))
    worst_trade= min(closed, key=lambda r: _float(r["r_multiple"]))

    # Equity curve & max drawdown
    equity = ACCOUNT_START
    peak   = ACCOUNT_START
    max_dd = 0.0
    eq_curve = []
    for pnl in pnl_vals:
        equity += pnl
        peak    = max(peak, equity)
        dd      = peak - equity
        max_dd  = max(max_dd, dd)
        eq_curve.append(equity)

    current_equity = eq_curve[-1] if eq_curve else ACCOUNT_START
    total_ret_pct  = (current_equity - ACCOUNT_START) / ACCOUNT_START * 100

    # Profit factor
    gross_win  = sum(p for p in pnl_vals if p > 0)
    gross_loss = abs(sum(p for p in pnl_vals if p < 0))
    pf         = (gross_win / gross_loss) if gross_loss > 0 else float("inf")

    line()
    line("CLOSED TRADE SUMMARY")
    line()
    line("Win Rate",      f"{win_rate:.1f}%  ({len(wins)}W / {len(losses)}L)")
    line("Avg Winner",    f"+{avg_win:.2f}R")
    line("Avg Loser",     f"{avg_loss:.2f}R")
    line("Expectancy",    f"{expectancy:+.2f}R per trade")
    line("Profit Factor", f"{pf:.2f}" if pf != float('inf') else "∞ (no losing trades)")
    line()
    line("Total P&L",     f"${total_pnl:+,.2f}  ({total_ret_pct:+.2f}%)")
    line("Current Equity",f"${current_equity:,.2f}")
    line("Max Drawdown",  f"-${max_dd:,.2f}  (-{max_dd / ACCOUNT_START * 100:.2f}%)")
    line("Total Risk Used",f"${total_risk:,.2f}")
    line()
    line("Best Trade",
         f"#{best_trade['id']} {best_trade['symbol']}  {_float(best_trade['r_multiple']):+.2f}R  "
         f"${_float(best_trade['pnl_usdt']):+,.2f}")
    line("Worst Trade",
         f"#{worst_trade['id']} {worst_trade['symbol']}  {_float(worst_trade['r_multiple']):+.2f}R  "
         f"${_float(worst_trade['pnl_usdt']):+,.2f}")

    # ── By scanner ────────────────────────────────────────────────────────────
    scanners = sorted({r["scanner"] for r in closed})
    if len(scanners) > 1:
        line()
        line("BY SCANNER")
        line()
        for sc in scanners:
            sc_rows  = [r for r in closed if r["scanner"] == sc]
            sc_r     = [_float(r["r_multiple"]) for r in sc_rows]
            sc_wr    = sum(1 for x in sc_r if x > 0) / len(sc_r) * 100
            sc_exp   = sum(sc_r) / len(sc_r)
            sc_pnl   = sum(_float(r["pnl_usdt"]) for r in sc_rows)
            line(f"  {sc:<12}",
                 f"{len(sc_rows):>3} trades  |  {sc_wr:>5.1f}% WR  |  "
                 f"{sc_exp:+.2f}R avg  |  ${sc_pnl:+,.0f}")

    # ── By regime ────────────────────────────────────────────────────────────
    regimes = sorted({r["regime"] for r in closed if r["regime"]})
    if len(regimes) > 1:
        line()
        line("BY REGIME")
        line()
        for rg in regimes:
            rg_rows  = [r for r in closed if r["regime"] == rg]
            rg_r     = [_float(r["r_multiple"]) for r in rg_rows]
            rg_wr    = sum(1 for x in rg_r if x > 0) / len(rg_r) * 100
            rg_exp   = sum(rg_r) / len(rg_r)
            rg_pnl   = sum(_float(r["pnl_usdt"]) for r in rg_rows)
            line(f"  {rg:<12}",
                 f"{len(rg_rows):>3} trades  |  {rg_wr:>5.1f}% WR  |  "
                 f"{rg_exp:+.2f}R avg  |  ${rg_pnl:+,.0f}")

    # ── By exit reason ────────────────────────────────────────────────────────
    reasons = sorted({r["exit_reason"] for r in closed if r["exit_reason"]})
    if reasons:
        line()
        line("BY EXIT REASON")
        line()
        for reason in reasons:
            rr_rows = [r for r in closed if r["exit_reason"] == reason]
            rr_r    = [_float(r["r_multiple"]) for r in rr_rows]
            rr_wr   = sum(1 for x in rr_r if x > 0) / len(rr_r) * 100
            rr_exp  = sum(rr_r) / len(rr_r)
            line(f"  {reason:<12}",
                 f"{len(rr_rows):>3} trades  |  {rr_wr:>5.1f}% WR  |  {rr_exp:+.2f}R avg")

    # ── Signal hit rate ───────────────────────────────────────────────────────
    all_signals: dict[str, list[float]] = {}
    for r in closed:
        r_mult = _float(r["r_multiple"])
        for sig in r["signals"].split(","):
            sig = sig.strip()
            if sig:
                all_signals.setdefault(sig, []).append(r_mult)

    if all_signals:
        line()
        line("SIGNALS  (≥3 appearances, sorted by avg R)")
        line()
        sig_stats = [
            (sig, rs)
            for sig, rs in all_signals.items()
            if len(rs) >= 3
        ]
        sig_stats.sort(key=lambda x: sum(x[1]) / len(x[1]), reverse=True)
        for sig, rs in sig_stats[:15]:
            wr  = sum(1 for x in rs if x > 0) / len(rs) * 100
            avg = sum(rs) / len(rs)
            line(f"  {sig:<26}",
                 f"{len(rs):>3}x  |  {wr:>5.1f}% WR  |  {avg:+.2f}R avg")

    # ── Equity curve (ASCII sparkline) ────────────────────────────────────────
    if len(eq_curve) >= 2:
        line()
        line("EQUITY CURVE")
        line()
        _print_sparkline(eq_curve, width=W - 6)

    # ── Open positions summary ────────────────────────────────────────────────
    if open_trades:
        line()
        line("OPEN POSITIONS")
        line()
        heat = sum(_float(r["risk_usdt"]) for r in open_trades)
        heat_pct = heat / current_equity * 100
        line(f"  {len(open_trades)} open",
             f"total risk ${heat:,.2f}  ({heat_pct:.1f}% heat)")
        for r in open_trades:
            line(f"  #{r['id']} {r['symbol']:<8}",
                 f"{r['scanner']}  entry {r['entry_price']}  "
                 f"risk ${_float(r['risk_usdt']):,.0f}")

    # ── Recent trades ─────────────────────────────────────────────────────────
    line()
    line("RECENT CLOSED TRADES")
    line()
    for r in closed[-8:]:
        r_raw   = _float(r["r_multiple"])
        pnl_raw = _float(r["pnl_usdt"])
        r_str   = f"{r_raw:+.2f}R"
        pnl_str = f"${pnl_raw:+,.0f}"
        print(f"  #{r['id']:<4} {r['symbol']:<8} {r['scanner']:<10} "
              f"{r['date_opened'][:10]}  {r_str:>7}  {pnl_str:>9}  [{r['exit_reason']}]")

    print()


def _print_sparkline(values: list[float], width: int = 55) -> None:
    """ASCII sparkline of the equity curve."""
    bars = "▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    rng = hi - lo or 1.0

    # Downsample to width
    step = max(1, len(values) // width)
    sampled = values[::step][-width:]

    line_chars = []
    for v in sampled:
        idx = int((v - lo) / rng * (len(bars) - 1))
        line_chars.append(bars[idx])

    print(f"  ${lo:,.0f} {''.join(line_chars)} ${hi:,.0f}")
    print(f"  {' ' * (len(sampled) // 2)}^")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="trade_journal",
        description="Trade journal with performance analytics",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── log ──────────────────────────────────────────────────────────────────
    p_log = sub.add_parser("log", help="Log a new trade")
    p_log.add_argument("--symbol",     required=True,        help="Token symbol, e.g. SUI")
    p_log.add_argument("--scanner",    required=True,
                       choices=["master", "alpha", "ignition", "short"],
                       help="Scanner that produced the signal")
    p_log.add_argument("--entry",      type=float, required=True, help="Entry price")
    p_log.add_argument("--stop",       type=float, required=True, help="Stop-loss price")
    p_log.add_argument("--tp1",        type=float, help="Take-profit 1 price")
    p_log.add_argument("--tp2",        type=float, help="Take-profit 2 price")
    p_log.add_argument("--tp3",        type=float, help="Take-profit 3 price")
    p_log.add_argument("--pos",        type=float, help="Position value in USDT")
    p_log.add_argument("--risk",       type=float, help="Risk amount in USDT (auto-derived if omitted)")
    p_log.add_argument("--conviction", type=float, help="Scanner conviction score 0-100")
    p_log.add_argument("--regime",
                       choices=["bull", "sideways", "bear"],
                       help="Market regime at entry")
    p_log.add_argument("--signals",    help="Comma-separated fired signals")
    p_log.add_argument("--notes",      help="Free-text notes")

    # ── close ────────────────────────────────────────────────────────────────
    p_close = sub.add_parser("close", help="Close an open trade")
    p_close.add_argument("--id",     type=int,   required=True, help="Trade ID to close")
    p_close.add_argument("--exit",   type=float, required=True, help="Exit price")
    p_close.add_argument("--reason", required=True,
                         choices=["tp1", "tp2", "tp3", "stop", "manual"],
                         help="Exit reason")
    p_close.add_argument("--notes",  help="Optional notes on close")

    # ── list ─────────────────────────────────────────────────────────────────
    p_list = sub.add_parser("list", help="List trades")
    grp = p_list.add_mutually_exclusive_group()
    grp.add_argument("--open",   action="store_true", help="Show only open trades")
    grp.add_argument("--closed", action="store_true", help="Show only closed trades")

    # ── stats ────────────────────────────────────────────────────────────────
    p_stats = sub.add_parser("stats", help="Performance analytics")
    p_stats.add_argument("--scanner", help="Filter by scanner (master/alpha/ignition/short)")
    p_stats.add_argument("--regime",  help="Filter by regime (bull/sideways/bear)")

    # ── delete ───────────────────────────────────────────────────────────────
    p_del = sub.add_parser("delete", help="Delete a trade (irreversible)")
    p_del.add_argument("--id",      type=int, required=True)
    p_del.add_argument("--confirm", action="store_true")

    args = parser.parse_args()

    dispatch = {
        "log":    cmd_log,
        "close":  cmd_close,
        "list":   cmd_list,
        "stats":  cmd_stats,
        "delete": cmd_delete,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
