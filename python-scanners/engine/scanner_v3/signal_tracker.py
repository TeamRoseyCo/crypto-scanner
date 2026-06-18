"""
================================================================================
SIGNAL TRACKER  v1.0
================================================================================
Forward-tracks every WATCH NOW signal from ignition / short / trend scanners
to measure REAL outcomes over time. After 4-6 weeks you'll have empirical
data on which signals predict winners.

Two modes:

  python signal_tracker.py record
    → Reads the latest scanner JSON files, inserts new entries into the DB.
      Call this from run_scan.py after the scanners finish (or from
      run_radar.bat as a post-step).

  python signal_tracker.py update
    → For every still-OPEN entry in the DB, fetches current price and marks
      the outcome (TP hit / stop hit / time-stopped / still open).
      Call this once a day from a scheduled task.

  python signal_tracker.py report
    → Prints aggregate stats by signal:
        signal_name   |  win_rate  |  avg_R  |  trades  |  expectancy
        whale_candle  |    62%     |  +1.4R  |    47    |   +0.85R
        ...
      Tells you which signals are paying and which aren't.

Schema (SQLite, single file):
  signals(id, scanner, base, direction, entered_at, entry_price, stop, tp1,
          tp2, tp3, conviction, signal_count, fired_signals_json,
          status, exited_at, exit_price, outcome, r_multiple)

The "trade plan" (entry/stop/TPs) is derived using a simple ATR-based recipe
mirroring trend_scanner.py — but you do NOT need a real position; this just
records what WOULD have happened. Paper-tracking, no broker.

Run:
  python signal_tracker.py record
  python signal_tracker.py update
  python signal_tracker.py report
  python signal_tracker.py report --signal whale_candle
  python signal_tracker.py report --since 2026-01-01
================================================================================
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

import data


# ─────────────────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────────────────
_THIS_DIR     = Path(__file__).resolve().parent
_ENGINE_DIR   = _THIS_DIR.parent
_PYTHON_DIR   = _ENGINE_DIR.parent
_PROJECT_ROOT = _PYTHON_DIR.parent
_OUTPUT_DIR   = _PROJECT_ROOT / "outputs" / "scanner-results"
_TRACKER_DIR  = _PROJECT_ROOT / "outputs" / "tracker"
_TRACKER_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = _TRACKER_DIR / "signals.sqlite"


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
log = logging.getLogger("tracker")
if not log.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)


# ─────────────────────────────────────────────────────────────────────────────
# TRADE PLAN PARAMS (mirrors trend_scanner.py's ATR-based recipe)
# ─────────────────────────────────────────────────────────────────────────────
PLAN = {
    "atr_period":      14,
    "atr_stop_mult":   1.5,
    "stop_min_pct":   -0.15,   # cap stop at -15%
    "stop_max_pct":   -0.05,   # never tighter than -5%
    "tp_rr":          [1.5, 3.0, 5.0],
    # Fraction of the position taken off at TP1 / TP2 / TP3 (must sum to 1.0).
    # Used to compute a realistic BLENDED R that matches the staged exit, rather
    # than crediting the full TP3 distance to every winner.
    "tp_exit_pct":    [0.30, 0.40, 0.30],
    # If True, the stop is moved to entry (breakeven) once TP1 is filled. This
    # mirrors a common discretionary rule; leave False for a faithful fixed-stop
    # model. Tune to match how you actually trade.
    "move_stop_to_breakeven_after_tp1": False,
    # Time stop in bars (1h timeframe) — exit if no result in 7 days
    "time_stop_bars": 24 * 7,
}


def _parse_dt_utc_naive(s: str) -> datetime:
    """Parse an ISO timestamp to a NAIVE-UTC datetime.

    Scanner JSON may write timestamps with or without a timezone. Bybit/Binance
    OHLCV indices are naive-UTC (epoch-ms converted). To compare the two without
    a 'can't subtract offset-naive and offset-aware' crash — or a silent local-vs-
    UTC skew — we normalise everything to naive-UTC here.
    """
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        # Fall back to "now" in naive-UTC if the field is missing/garbage
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _now_utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ─────────────────────────────────────────────────────────────────────────────
# DB
# ─────────────────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    """Create schema if it doesn't exist."""
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                scanner         TEXT    NOT NULL,
                base            TEXT    NOT NULL,
                direction       TEXT    NOT NULL,    -- 'long' or 'short'
                entered_at      TEXT    NOT NULL,    -- ISO timestamp
                entry_price     REAL    NOT NULL,
                stop_price      REAL    NOT NULL,
                tp1_price       REAL    NOT NULL,
                tp2_price       REAL    NOT NULL,
                tp3_price       REAL    NOT NULL,
                stop_pct        REAL    NOT NULL,
                conviction      REAL,
                signal_count    INTEGER,
                fired_signals   TEXT,                -- JSON array
                status          TEXT    NOT NULL DEFAULT 'open',
                exited_at       TEXT,
                exit_price      REAL,
                outcome         TEXT,                -- 'tp1','tp2','tp3','stop','time'
                r_multiple      REAL,                -- realized R
                UNIQUE(scanner, base, entered_at)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_status ON signals(status)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_base ON signals(base)")


# ─────────────────────────────────────────────────────────────────────────────
# TRADE PLAN
# ─────────────────────────────────────────────────────────────────────────────

def _atr_from_df(df: pd.DataFrame, period: int = 14) -> float:
    """ATR calculation matching indicators.compute_atr — duplicated here so
    this script has zero dependency on indicators.py (keeps coupling small)."""
    if df is None or len(df) < period + 1:
        return 0.0
    h = df["high"]; l = df["low"]; c = df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l).abs(), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().dropna().iloc[-1])


def build_plan(
    entry_price: float,
    atr:         float,
    direction:   str,    # 'long' or 'short'
) -> Optional[dict]:
    """Build entry/stop/TP plan for tracking purposes."""
    if entry_price <= 0 or atr <= 0:
        return None

    atr_dist = atr * PLAN["atr_stop_mult"]
    if direction == "long":
        stop = entry_price - atr_dist
        stop_pct = (stop - entry_price) / entry_price
        # Cap stop pct
        if stop_pct < PLAN["stop_min_pct"]:
            stop = entry_price * (1 + PLAN["stop_min_pct"])
            stop_pct = PLAN["stop_min_pct"]
        elif stop_pct > PLAN["stop_max_pct"]:
            stop = entry_price * (1 + PLAN["stop_max_pct"])
            stop_pct = PLAN["stop_max_pct"]
        risk = entry_price - stop
        tps  = [entry_price + risk * rr for rr in PLAN["tp_rr"]]
    else:  # short
        stop = entry_price + atr_dist
        stop_pct = (stop - entry_price) / entry_price       # positive for short stop
        if stop_pct > abs(PLAN["stop_min_pct"]):
            stop = entry_price * (1 + abs(PLAN["stop_min_pct"]))
            stop_pct = abs(PLAN["stop_min_pct"])
        elif stop_pct < abs(PLAN["stop_max_pct"]):
            stop = entry_price * (1 + abs(PLAN["stop_max_pct"]))
            stop_pct = abs(PLAN["stop_max_pct"])
        risk = stop - entry_price
        tps  = [entry_price - risk * rr for rr in PLAN["tp_rr"]]

    return {
        "stop":     stop,
        "tp1":      tps[0],
        "tp2":      tps[1],
        "tp3":      tps[2],
        "stop_pct": stop_pct,
    }


# ─────────────────────────────────────────────────────────────────────────────
# RECORD — ingest scanner outputs into DB
# ─────────────────────────────────────────────────────────────────────────────

SCANNER_FILES = {
    "ignition": _OUTPUT_DIR / "ignition_v3_LATEST.json",
    "perp":     _OUTPUT_DIR / "perp_v3_LATEST.json",
    "trend":    _OUTPUT_DIR / "trend_v3_LATEST.json",
    "short":    _OUTPUT_DIR / "short_v3_LATEST.json",
}


def _direction_of(scanner_name: str, entry: dict) -> str:
    """Long/short classification from scanner name + entry's own field."""
    if scanner_name == "short" or entry.get("direction") == "short":
        return "short"
    return "long"


def _load_watch_now() -> dict[str, dict]:
    """Read every scanner's LATEST.json. Returns:
        { scanner_name: { "watch_now": [...], "scan_time": "...", "available": True } }
    Missing/unparseable files are flagged available=False."""
    out: dict[str, dict] = {}
    for name, path in SCANNER_FILES.items():
        if not path.exists():
            out[name] = {"watch_now": [], "scan_time": None, "available": False}
            log.info(f"  {name}: no LATEST.json yet, skipping")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            log.error(f"  {name}: failed to parse {path.name}: {e}")
            out[name] = {"watch_now": [], "scan_time": None, "available": False}
            continue
        out[name] = {
            "watch_now": payload.get("watch_now", []),
            "scan_time": payload.get("generated_at", datetime.now().isoformat()),
            "available": True,
        }
    return out


def _detect_conflicts(by_scanner: dict[str, dict]) -> tuple[set[str], list[dict]]:
    """A 'conflict' is a base symbol that appears in WATCH NOW from at least
    one LONG scanner AND at least one SHORT scanner in the same scan cycle.

    Returns (conflicted_bases, conflict_records) where conflict_records is
    a list of dicts suitable for human reporting and persistence.
    """
    # Group WATCH NOW bases by direction across scanners
    long_bases: dict[str, list[tuple[str, dict]]] = {}     # base -> [(scanner, entry), ...]
    short_bases: dict[str, list[tuple[str, dict]]] = {}

    for scanner_name, info in by_scanner.items():
        for entry in info["watch_now"]:
            base = entry.get("base", "")
            if not base:
                continue
            direction = _direction_of(scanner_name, entry)
            target = short_bases if direction == "short" else long_bases
            target.setdefault(base, []).append((scanner_name, entry))

    conflicted = set(long_bases) & set(short_bases)
    records: list[dict] = []
    for base in sorted(conflicted):
        long_side  = long_bases[base]
        short_side = short_bases[base]
        records.append({
            "base":            base,
            "long_scanners":   [s for s, _ in long_side],
            "short_scanners":  [s for s, _ in short_side],
            "long_signals":    sorted({
                sig for _, e in long_side  for sig in e.get("fired_signals", [])
            }),
            "short_signals":   sorted({
                sig for _, e in short_side for sig in e.get("fired_signals", [])
            }),
            "long_conviction":  max((e.get("conviction") or 0) for _, e in long_side),
            "short_conviction": max((e.get("conviction") or 0) for _, e in short_side),
        })
    return conflicted, records


def _persist_conflicts(records: list[dict]) -> None:
    """Write the conflict list to outputs/tracker/conflicts_LATEST.txt and a
    timestamped copy. Empty file is written even when no conflicts so you
    can see it ran (handy for scheduled runs)."""
    sep  = "=" * 80
    dash = "-" * 80
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    lines = [
        sep,
        f"  SIGNAL CONFLICTS  —  scan at {ts}",
        sep,
        "",
    ]
    if not records:
        lines.append("  No conflicts detected in this scan.")
        lines.append("  All WATCH NOW signals point in a single direction per coin.")
    else:
        lines.append(f"  {len(records)} coin(s) flagged in BOTH long and short scanners.")
        lines.append("  These are AVOID — do not trade in either direction.")
        lines.append("  (Contradictory signals = no edge.)")
        lines.append("")
        lines.append(dash)
        for r in records:
            lines.append(f"  AVOID: {r['base']}")
            lines.append(f"    long  side ({', '.join(r['long_scanners'])}, "
                         f"conv={r['long_conviction']:.1f}):  "
                         f"{', '.join(r['long_signals'])}")
            lines.append(f"    short side ({', '.join(r['short_scanners'])}, "
                         f"conv={r['short_conviction']:.1f}):  "
                         f"{', '.join(r['short_signals'])}")
            lines.append("")
        lines.append(dash)

    lines.append("")
    text = "\n".join(lines)

    latest = _TRACKER_DIR / "conflicts_LATEST.txt"
    archive = _TRACKER_DIR / f"conflicts_{file_ts}.txt"
    latest.write_text(text, encoding="utf-8")
    archive.write_text(text, encoding="utf-8")


def record_from_latest() -> int:
    """Read every scanner's LATEST.json, insert new WATCH NOW entries.
    Skips coins that appear in BOTH a long and a short scanner (conflicts).
    Returns count of new rows inserted."""
    init_db()
    inserted = 0

    # ---- Pass 1: load everything and detect conflicts ---------------------
    by_scanner = _load_watch_now()
    conflicted, conflict_records = _detect_conflicts(by_scanner)
    _persist_conflicts(conflict_records)

    if conflicted:
        log.warning("=" * 64)
        log.warning(f"  CONFLICT WARNING — {len(conflicted)} coin(s) appear LONG and SHORT")
        log.warning("=" * 64)
        for r in conflict_records:
            log.warning(
                f"  AVOID  {r['base']:<10} "
                f"long={','.join(r['long_scanners'])} (conv {r['long_conviction']:.1f}) "
                f"vs short=({','.join(r['short_scanners'])}) (conv {r['short_conviction']:.1f})"
            )
        log.warning("  These will NOT be recorded to the tracker DB.")
        log.warning("  Full details: outputs/tracker/conflicts_LATEST.txt")
        log.warning("=" * 64)

    # ---- Pass 2: record non-conflicted entries ----------------------------
    for scanner_name, info in by_scanner.items():
        if not info["available"]:
            continue
        watch_now = info["watch_now"]
        scan_time = info["scan_time"] or datetime.now().isoformat()
        if not watch_now:
            log.info(f"  {scanner_name}: 0 WATCH NOW entries")
            continue

        skipped_conflict = 0
        for entry in watch_now:
            base  = entry.get("base", "")
            price = float(entry.get("price", 0))
            if not base or price <= 0:
                continue

            # Skip conflicted coins
            if base in conflicted:
                skipped_conflict += 1
                continue

            direction = _direction_of(scanner_name, entry)

            # Fetch a small slice of OHLCV to compute ATR
            df = data.get_ohlcv(base, "bybit", "1h", 100, use_cache=True)
            if df is None or len(df) < 20:
                df = data.get_ohlcv(base, "binance", "1h", 100, use_cache=True)
            if df is None or len(df) < 20:
                log.warning(f"  {scanner_name} {base}: no OHLCV, skipping")
                continue

            atr = _atr_from_df(df, PLAN["atr_period"])
            plan = build_plan(price, atr, direction)
            if plan is None:
                continue

            fired = entry.get("fired_signals", [])
            try:
                with _conn() as c:
                    c.execute("""
                        INSERT OR IGNORE INTO signals (
                            scanner, base, direction, entered_at, entry_price,
                            stop_price, tp1_price, tp2_price, tp3_price, stop_pct,
                            conviction, signal_count, fired_signals
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        scanner_name, base, direction, scan_time, price,
                        plan["stop"], plan["tp1"], plan["tp2"], plan["tp3"],
                        plan["stop_pct"],
                        entry.get("conviction"), entry.get("signal_count"),
                        json.dumps(fired),
                    ))
                    if c.total_changes:
                        inserted += 1
            except sqlite3.Error as e:
                log.error(f"  {scanner_name} {base}: DB error: {e}")

        msg = f"  {scanner_name}: processed {len(watch_now)} entries"
        if skipped_conflict:
            msg += f" ({skipped_conflict} skipped — conflict)"
        log.info(msg)

    log.info(f"Inserted {inserted} new tracked signals.")
    if conflicted:
        log.info(f"Skipped {len(conflicted)} coin(s) due to long/short conflict.")
    return inserted


# ─────────────────────────────────────────────────────────────────────────────
# UPDATE — mark outcomes of still-open entries
# ─────────────────────────────────────────────────────────────────────────────

def _simulate_staged_exit(
    post:      pd.DataFrame,
    direction: str,
    entry:     float,
    stop:      float,
    tps:       list[float],
    elapsed_bars_reached_time_stop: bool,
) -> Optional[dict]:
    """Walk the bars after entry and simulate the STAGED scale-out plan.

    Returns a terminal result dict (or None if the position isn't closed yet):
        { "outcome", "exit_price", "exit_at", "r_multiple" }

    Why this exists: the old logic recorded the first level touched and credited
    its full R (e.g. a coin that tagged TP3 booked +5R). But the plan scales out
    in fractions (default 30/40/30), so a TP3 winner actually realises a *blend*
    (~3.1R with 1.5/3/5 R targets), and the stop on the remainder caps the
    downside at -1R only on the un-exited fraction. This simulates that path so
    the recorded R matches what the staged plan would really bank.

    Conservative assumptions:
      - Within a single 1h bar we cannot see the path, so a stop is assumed to
        trigger before any TP touched in that same bar.
      - The stop is fixed unless PLAN['move_stop_to_breakeven_after_tp1'] is set,
        in which case it moves to entry once TP1 fills.
    """
    risk = abs(entry - stop)
    if risk <= 0:
        return None

    fracs = list(PLAN["tp_exit_pct"])
    tps_hit = [False, False, False]
    remaining = 1.0
    realized_r = 0.0
    stop_level = stop
    highest_tp = 0          # 0 = none, 1/2/3 = highest TP filled
    last_ts = None

    def _r(px: float) -> float:
        return (px - entry) / risk if direction == "long" else (entry - px) / risk

    terminal = None  # ("stop"|"tp3"|"time", exit_price, exit_at)

    for ts, bar in post.iterrows():
        last_ts = ts
        high = float(bar["high"]); low = float(bar["low"])
        ts_iso = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)

        stop_hit = (low <= stop_level) if direction == "long" else (high >= stop_level)
        if stop_hit:
            realized_r += remaining * _r(stop_level)
            remaining = 0.0
            terminal = ("tp%d" % highest_tp if highest_tp else "stop", stop_level, ts_iso)
            break

        for i, tp in enumerate(tps):
            if tps_hit[i]:
                continue
            touched = (high >= tp) if direction == "long" else (low <= tp)
            if not touched:
                continue
            realized_r += fracs[i] * _r(tp)
            remaining = max(0.0, remaining - fracs[i])
            tps_hit[i] = True
            highest_tp = i + 1
            if i == 0 and PLAN["move_stop_to_breakeven_after_tp1"]:
                stop_level = entry

        if all(tps_hit) or remaining <= 1e-9:
            terminal = ("tp3", tps[-1], ts_iso)
            break

    if terminal is None:
        # Not closed by stop/TP3. Close at the last bar only if the time stop
        # has actually elapsed; otherwise the position is still open.
        if elapsed_bars_reached_time_stop and last_ts is not None and remaining > 0:
            last_close = float(post["close"].iloc[-1])
            realized_r += remaining * _r(last_close)
            remaining = 0.0
            label = "tp%d" % highest_tp if highest_tp else "time"
            ts_iso = last_ts.isoformat() if hasattr(last_ts, "isoformat") else str(last_ts)
            terminal = (label, last_close, ts_iso)
        else:
            return None

    outcome, exit_price, exit_at = terminal
    return {
        "outcome":    outcome,
        "exit_price": exit_price,
        "exit_at":    exit_at,
        "r_multiple": round(realized_r, 3),
    }


def update_outcomes() -> dict:
    """For every open entry, fetch OHLCV since entry, determine outcome.
    Returns counts dict."""
    init_db()
    counts = {"checked": 0, "closed": 0, "still_open": 0, "tp_hits": 0, "stops": 0, "time": 0}

    with _conn() as c:
        rows = c.execute("SELECT * FROM signals WHERE status = 'open'").fetchall()

    for row in rows:
        counts["checked"] += 1
        base       = row["base"]
        direction  = row["direction"]
        entered_at = _parse_dt_utc_naive(row["entered_at"])   # naive-UTC
        entry      = row["entry_price"]
        stop       = row["stop_price"]
        tps        = [row["tp1_price"], row["tp2_price"], row["tp3_price"]]

        # Hours since entry (tz-safe: both sides naive-UTC)
        hours_since   = max(int((_now_utc_naive() - entered_at).total_seconds() // 3600), 1)
        bars_to_fetch = min(hours_since + 5, PLAN["time_stop_bars"] + 10)

        df = data.get_ohlcv(base, "bybit", "1h", bars_to_fetch, use_cache=False)
        if df is None or len(df) < 2:
            df = data.get_ohlcv(base, "binance", "1h", bars_to_fetch, use_cache=False)
        if df is None or len(df) < 2:
            counts["still_open"] += 1
            continue

        # Window by TIMESTAMP, not bar count — robust to gaps / fetch-boundary
        # drift. Only bars strictly after entry, capped at the time-stop horizon.
        post = df[df.index > pd.Timestamp(entered_at)]
        if len(post) > PLAN["time_stop_bars"]:
            post = post.iloc[:PLAN["time_stop_bars"]]
        if len(post) == 0:
            counts["still_open"] += 1
            continue

        time_stop_reached = hours_since >= PLAN["time_stop_bars"]
        result = _simulate_staged_exit(
            post, direction, entry, stop, tps, time_stop_reached
        )
        if result is None:
            counts["still_open"] += 1
            continue

        with _conn() as c:
            c.execute("""
                UPDATE signals
                SET status='closed', exited_at=?, exit_price=?, outcome=?, r_multiple=?
                WHERE id=?
            """, (result["exit_at"], result["exit_price"],
                  result["outcome"], result["r_multiple"], row["id"]))

        counts["closed"] += 1
        if result["r_multiple"] > 0:        counts["tp_hits"] += 1
        elif result["outcome"] == "time":   counts["time"]    += 1
        else:                               counts["stops"]   += 1

    log.info(f"Update complete: {counts}")
    return counts


# ─────────────────────────────────────────────────────────────────────────────
# REPORT — aggregate hit rates per signal
# ─────────────────────────────────────────────────────────────────────────────

def report(signal_filter: Optional[str] = None, since: Optional[str] = None) -> None:
    init_db()

    where = ["status = 'closed'"]
    params: list = []
    if since:
        where.append("entered_at >= ?")
        params.append(since)
    sql = f"SELECT * FROM signals WHERE {' AND '.join(where)}"

    with _conn() as c:
        rows = c.execute(sql, params).fetchall()

    if not rows:
        print("No closed entries yet. Run `signal_tracker.py update` after some time has passed.")
        return

    # Aggregate by individual signal. NOTE: a closed trade contributes its R to
    # EVERY signal that fired on it. Signals that tend to co-fire therefore share
    # credit and will look similar — co-firing is a confound, not causation. The
    # "lift vs baseline" column and the near-isolated table below help separate
    # signals that actually move outcomes from signals that merely ride along.
    from collections import defaultdict
    bucket:     dict[str, list[float]] = defaultdict(list)   # all trades a signal fired on
    iso_bucket: dict[str, list[float]] = defaultdict(list)   # trades where <=2 signals fired
    overall: list[float] = []

    for row in rows:
        try:
            sigs = json.loads(row["fired_signals"] or "[]")
        except Exception:
            sigs = []
        r = row["r_multiple"] or 0.0
        overall.append(r)
        near_isolated = len(sigs) <= 2
        for s in sigs:
            if signal_filter and s != signal_filter:
                continue
            bucket[s].append(r)
            if near_isolated:
                iso_bucket[s].append(r)

    baseline_avg_r = (sum(overall) / len(overall)) if overall else 0.0

    # Per-signal stats
    print()
    print("=" * 96)
    print(f"  SIGNAL TRACKER REPORT  —  {len(rows)} closed trades"
          + (f"  since {since}" if since else ""))
    print("=" * 96)
    if overall:
        wins = sum(1 for r in overall if r > 0)
        print(f"  OVERALL:   trades={len(overall)}   "
              f"win_rate={wins/len(overall)*100:.1f}%   "
              f"avg_R={baseline_avg_r:+.2f}   "
              f"total_R={sum(overall):+.2f}")
        print(f"  R is the BLENDED scale-out result ({'/'.join(str(int(f*100)) for f in PLAN['tp_exit_pct'])}"
              f" across TP1/TP2/TP3), not the full TP3 distance.")
    print("-" * 96)
    print("  'Lift' = this signal's avg R minus the overall baseline avg R.")
    print("  Positive lift = trades with this signal beat the average; ~0 = no edge over baseline.")
    print("-" * 96)
    print(f"  {'Signal':<28} {'Trades':>7} {'Win %':>7} {'Avg R':>8} {'Lift':>8} {'Expectancy':>11}")
    print("-" * 96)

    sorted_signals = sorted(bucket.items(),
                            key=lambda kv: (sum(kv[1])/len(kv[1])) if kv[1] else 0,
                            reverse=True)
    for sig, rs in sorted_signals:
        if not rs:
            continue
        wins     = sum(1 for r in rs if r > 0)
        win_rate = wins / len(rs) * 100
        avg_r    = sum(rs) / len(rs)
        lift     = avg_r - baseline_avg_r
        winners  = [r for r in rs if r > 0]
        losers   = [r for r in rs if r <= 0]
        avg_w = sum(winners)/len(winners) if winners else 0
        avg_l = sum(losers)/len(losers) if losers else 0
        expectancy = (win_rate/100) * avg_w + (1 - win_rate/100) * avg_l
        print(f"  {sig:<28} {len(rs):>7} {win_rate:>6.1f}% "
              f"{avg_r:>+7.2f}R {lift:>+7.2f}R {expectancy:>+9.2f}R")

    # Near-isolated view: outcomes when the signal fired with <=2 total signals.
    # This is the closest cheap proxy to a signal's standalone effect. Small
    # samples are expected early — treat anything under ~20 trades as noise.
    iso_rows = sorted(
        ((s, rs) for s, rs in iso_bucket.items() if rs),
        key=lambda kv: sum(kv[1])/len(kv[1]), reverse=True,
    )
    if iso_rows:
        print("-" * 96)
        print("  NEAR-ISOLATED (signal fired with <=2 total signals — closest proxy to standalone edge):")
        print(f"  {'Signal':<28} {'Trades':>7} {'Win %':>7} {'Avg R':>8}")
        print("  " + "-" * 54)
        for sig, rs in iso_rows:
            wins = sum(1 for r in rs if r > 0)
            flag = "" if len(rs) >= 20 else "  (low n)"
            print(f"  {sig:<28} {len(rs):>7} {wins/len(rs)*100:>6.1f}% "
                  f"{sum(rs)/len(rs):>+7.2f}R{flag}")

    print("=" * 96)
    print()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Signal Tracker v1.0")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("record", help="Ingest WATCH NOW entries from latest scanner outputs")
    sub.add_parser("update", help="Mark outcomes of open entries")
    p_report = sub.add_parser("report", help="Print per-signal win-rate stats")
    p_report.add_argument("--signal", help="Filter to one signal name")
    p_report.add_argument("--since",  help="ISO date (YYYY-MM-DD) lower bound")

    args = parser.parse_args()
    if args.cmd == "record":
        record_from_latest()
    elif args.cmd == "update":
        update_outcomes()
    elif args.cmd == "report":
        report(signal_filter=getattr(args, "signal", None),
               since=getattr(args, "since", None))
