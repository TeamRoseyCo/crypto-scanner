"""
================================================================================
PERFORMANCE TRACKER  v1.1  —  How did last week's published picks actually do?
================================================================================
For each daily Short script published in the past 7 days:
  1. Read the script JSON to find which coins were featured
  2. Read the corresponding master_radar_*.json to get scan-time prices
  3. Fetch current price from Binance (free, fast, no rate limits)
  4. Compute return %
  5. Build a weekly_performance_YYYYMMDD.json with winners + losers

Changes vs v1.0:
  - Reads master_radar_*.json (the actual scanner output) — not master_trade_plan
  - Uses 'base' field for ticker (matches ingest.py)
  - Looks across all bucket fields: convergence, strong_setup, single_scanner, extended
  - Uses Binance API for current prices (no API key needed)

Run:
  python performance_tracker.py                # past 7 days
  python performance_tracker.py --days 14      # past 14 days
  python performance_tracker.py --preview      # print only, don't save
================================================================================
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Force UTF-8 output on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import requests

# ─────────────────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────────────────
_THIS_FILE = Path(__file__).resolve()

def _find_project_root() -> Path:
    """Walk up the tree to find the crypto-scanner root."""
    for cand in [_THIS_FILE.parent, _THIS_FILE.parent.parent, _THIS_FILE.parent.parent.parent]:
        if (cand / "outputs" / "scanner-results").exists():
            return cand
    return _THIS_FILE.parent.parent.parent

_ROOT        = _find_project_root()
_SCRIPTS_DIR = _ROOT / "YOUTUBE - faceless channel" / "Video Scripts"
_SCANNER_DIR = _ROOT / "outputs" / "scanner-results"
_PERF_DIR    = _ROOT / "outputs" / "performance"
_PERF_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
log = logging.getLogger("performance_tracker")
if not log.handlers:
    hs = logging.StreamHandler(sys.stdout)
    hs.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(hs)
    log.setLevel(logging.INFO)

# ─────────────────────────────────────────────────────────────────────────────
# BINANCE PRICE FETCH
# ─────────────────────────────────────────────────────────────────────────────
_BINANCE_API = "https://api.binance.com/api/v3"
_BN_SESSION  = requests.Session()
_BN_SESSION.headers.update({"User-Agent": "performance-tracker/1.1"})

_TICKER_OVERRIDES: dict[str, str] = {
    # "OLDNAME": "NEWNAME",  # add if/when discovered
}


def fetch_current_prices_binance(tickers: list[str]) -> dict[str, float]:
    """
    Batch-fetch current USDT prices from Binance.

    Uses /api/v3/ticker/price (no auth, 1200 req/min limit).
    Returns {ticker_upper: current_usd_price}. Skips tickers not on Binance.
    """
    if not tickers:
        return {}

    unique = sorted({t.upper() for t in tickers})
    log.info(f"  Fetching current prices for {len(unique)} unique tickers from Binance...")

    candidate_symbols = []
    symbol_to_ticker = {}
    for t in unique:
        actual = _TICKER_OVERRIDES.get(t, t)
        sym = f"{actual}USDT"
        candidate_symbols.append(sym)
        symbol_to_ticker[sym] = t

    results: dict[str, float] = {}
    missing: list[str] = []

    for i in range(0, len(candidate_symbols), 100):
        chunk = candidate_symbols[i:i + 100]
        try:
            symbols_param = json.dumps(chunk).replace(" ", "")
            r = _BN_SESSION.get(
                f"{_BINANCE_API}/ticker/price",
                params={"symbols": symbols_param},
                timeout=15,
            )
            if r.status_code == 400:
                # One bad symbol in the batch — fall back to per-symbol
                log.debug("  Batch failed (likely one bad symbol) — querying individually")
                for sym in chunk:
                    try:
                        r2 = _BN_SESSION.get(
                            f"{_BINANCE_API}/ticker/price",
                            params={"symbol": sym},
                            timeout=10,
                        )
                        if r2.status_code == 200:
                            d = r2.json()
                            results[symbol_to_ticker[sym]] = float(d["price"])
                        else:
                            missing.append(symbol_to_ticker[sym])
                    except Exception:
                        missing.append(symbol_to_ticker[sym])
                    time.sleep(0.1)
                continue

            r.raise_for_status()
            data = r.json()
            for item in data:
                sym = item.get("symbol", "")
                if sym in symbol_to_ticker:
                    try:
                        results[symbol_to_ticker[sym]] = float(item["price"])
                    except (TypeError, ValueError):
                        missing.append(symbol_to_ticker[sym])

        except requests.exceptions.HTTPError as e:
            log.warning(f"  HTTP error fetching prices: {e}")
        except Exception as e:
            log.warning(f"  Unexpected error: {e}")

        time.sleep(0.2)

    if missing:
        log.warning(
            f"  Could not fetch {len(missing)} coins (not on Binance or delisted): "
            f"{', '.join(missing[:10])}{'...' if len(missing) > 10 else ''}"
        )

    log.info(f"  Got prices for {len(results)}/{len(unique)} tickers")
    return results


# ─────────────────────────────────────────────────────────────────────────────
# READING PUBLISHED SCRIPTS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_script_date(filename: str) -> Optional[datetime]:
    """Extract datetime from 'script_YYYYMMDD_HHMMSS.json' filename."""
    try:
        stem = filename.replace("script_", "").replace(".json", "")
        for fmt in ("%Y%m%d_%H%M%S", "%Y%m%d"):
            try:
                return datetime.strptime(stem[:len(datetime.now().strftime(fmt))], fmt)
            except ValueError:
                continue
    except Exception:
        pass
    return None


def collect_published_picks(days: int = 7) -> list[dict]:
    """Walk Video Scripts/ and collect every script from the past `days`."""
    cutoff = datetime.now() - timedelta(days=days)

    if not _SCRIPTS_DIR.exists():
        log.error(f"  Scripts directory not found: {_SCRIPTS_DIR}")
        return []

    picks: list[dict] = []

    for script_file in sorted(_SCRIPTS_DIR.glob("script_*.json")):
        script_date = _parse_script_date(script_file.name)
        if script_date is None:
            log.debug(f"  Could not parse date from {script_file.name} — skipping")
            continue
        if script_date < cutoff:
            continue

        try:
            data = json.loads(script_file.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"  Could not parse {script_file.name}: {e}")
            continue

        segments = data.get("segments", [])
        coins = []
        for seg in segments:
            symbol = (seg.get("coin") or "").strip().upper()
            if not symbol or symbol in ("MARKET", "MARKET REGIME", "BTC OVERVIEW"):
                continue
            coins.append({
                "symbol":    symbol,
                "stat":      seg.get("stat", ""),
                "narration": seg.get("narration", ""),
            })

        if not coins:
            log.debug(f"  {script_file.name}: no coins featured — skipping")
            continue

        picks.append({
            "script_date": script_date,
            "script_path": script_file,
            "title":       data.get("title", ""),
            "coins":       coins,
        })

    log.info(f"  Found {len(picks)} published scripts in last {days} days")
    return picks


# ─────────────────────────────────────────────────────────────────────────────
# MATCHING SCRIPTS TO SCANNER OUTPUTS
# ─────────────────────────────────────────────────────────────────────────────

def _file_date_from_name(path: Path) -> Optional[datetime]:
    """Extract datetime from master_radar_YYYYMMDD_HHMMSS.json."""
    try:
        stem = path.stem.replace("master_radar_", "")
        return datetime.strptime(stem, "%Y%m%d_%H%M%S")
    except Exception:
        return None


def find_scanner_output_for_script(script_date: datetime) -> Optional[Path]:
    """
    Find the master_radar JSON that the pipeline ACTUALLY used to generate
    the script — i.e. the most recent scanner output BEFORE the script was
    written. Never matches scanner runs that happened after the script.

    If no pre-script JSON exists, returns None (caller should skip the script).
    This is correct behavior — you cannot measure performance on a script
    whose source scanner data wasn't preserved.

    The window is also capped at 12 hours: if the closest pre-script JSON
    is more than 12 hours older, we assume something went wrong (e.g. the
    1 AM scanner failed to save JSON that night) and skip rather than match
    against stale data.
    """
    if not _SCANNER_DIR.exists():
        return None

    MAX_LOOKBACK_HOURS = 12

    # Collect every master_radar JSON written strictly BEFORE the script,
    # within the lookback window.
    candidates: list[tuple[float, Path]] = []
    for p in _SCANNER_DIR.glob("master_radar_*.json"):
        d = _file_date_from_name(p)
        if d is None:
            continue
        delta = (script_date - d).total_seconds()
        if delta <= 0:
            continue  # scanner ran AFTER the script — ignore
        if delta > MAX_LOOKBACK_HOURS * 3600:
            continue  # too old — script wasn't based on this data
        candidates.append((delta, p))

    if not candidates:
        return None

    # Pick the most recent pre-script JSON (smallest delta).
    candidates.sort()
    return candidates[0][1]


def _iter_all_coins_from_master_radar(data: dict):
    """Yield (bucket, coin) for every coin entry across all buckets."""
    for bucket_name in ("convergence", "strong_setup", "single_scanner", "extended"):
        bucket = data.get(bucket_name)
        if isinstance(bucket, list):
            for coin in bucket:
                yield bucket_name, coin


def load_scanner_candidates(scanner_path: Path) -> dict[str, dict]:
    """Load master_radar JSON, return {ticker_upper: {price, confluence, bucket, ...}}."""
    try:
        data = json.loads(scanner_path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"  Could not parse {scanner_path.name}: {e}")
        return {}

    bucket_priority = {
        "convergence": 4,
        "strong_setup": 3,
        "single_scanner": 2,
        "extended": 1,
    }

    out: dict[str, dict] = {}
    for bucket, coin in _iter_all_coins_from_master_radar(data):
        ticker = (coin.get("base") or "").upper()
        if not ticker:
            continue
        price = coin.get("price")
        if price is None or price <= 0:
            continue

        # Prefer higher-priority bucket if duplicated
        if ticker in out:
            if bucket_priority.get(bucket, 0) <= bucket_priority.get(out[ticker]["bucket"], 0):
                continue

        out[ticker] = {
            "price":         float(price),
            "confluence":    float(coin.get("confluence") or 0),
            "bucket":        bucket,
            "scanner_count": coin.get("scanner_count", 1),
            "change_24h":    coin.get("price_24h_pct", 0),
            "volume_24h":    coin.get("volume_24h", 0),
        }
    return out


# ─────────────────────────────────────────────────────────────────────────────
# CORE: BUILD THE PERFORMANCE REPORT
# ─────────────────────────────────────────────────────────────────────────────

def build_performance_report(days: int = 7) -> dict:
    """Main entry point. Returns a structured report dict."""
    log.info("=" * 64)
    log.info(f"PERFORMANCE TRACKER — past {days} days")
    log.info("=" * 64)

    # ── Step 1: Collect published scripts ────────────────────────────────────
    log.info(f"\n[1/4] Reading published scripts from {_SCRIPTS_DIR}")
    picks = collect_published_picks(days=days)

    if not picks:
        log.warning("  No published scripts found — nothing to track.")
        return {"error": "no_scripts", "picks": []}

    # ── Step 2: Match each script to its scanner output ──────────────────────
    log.info(f"\n[2/4] Matching scripts to scanner outputs in {_SCANNER_DIR}")

    enriched_picks = []
    skipped_no_scanner = []
    for p in picks:
        scanner_path = find_scanner_output_for_script(p["script_date"])
        if not scanner_path:
            skipped_no_scanner.append(p)
            log.info(
                f"  SKIP: {p['script_date'].strftime('%Y-%m-%d %H:%M')} "
                f"— no master_radar JSON in 12h preceding script "
                f"({p['title'][:50]})"
            )
            continue

        scan_data = load_scanner_candidates(scanner_path)
        if not scan_data:
            log.warning(f"  Empty scanner data: {scanner_path.name}")
            continue

        matched_coins = []
        unmatched = []
        for c in p["coins"]:
            sym = c["symbol"]
            if sym not in scan_data:
                unmatched.append(sym)
                continue
            entry = scan_data[sym]
            matched_coins.append({
                "symbol":          sym,
                "scan_price":      entry["price"],
                "scan_conviction": entry["confluence"],
                "bucket":          entry["bucket"],
                "scanner_count":   entry["scanner_count"],
                "stat_at_scan":    c["stat"],
            })

        if unmatched:
            log.debug(
                f"  {p['script_date'].strftime('%Y-%m-%d')}: featured but not in scanner: "
                f"{', '.join(unmatched)}"
            )

        if matched_coins:
            enriched_picks.append({
                "script_date":  p["script_date"].isoformat(),
                "script_path":  str(p["script_path"]),
                "scanner_path": str(scanner_path),
                "title":        p["title"],
                "coins":        matched_coins,
            })

    log.info(f"  Successfully matched {len(enriched_picks)}/{len(picks)} scripts")
    if skipped_no_scanner:
        log.warning(
            f"  Skipped {len(skipped_no_scanner)} scripts (no scanner JSON for those dates "
            f"— expected, JSON outputs only started 2026-05-12)"
        )

    if not enriched_picks:
        log.error("  No scripts could be matched to scanner data.")
        return {"error": "no_matches", "picks_found": len(picks)}

    # ── Step 3: Fetch current prices ─────────────────────────────────────────
    log.info(f"\n[3/4] Fetching current prices from Binance...")
    all_tickers = list({c["symbol"] for p in enriched_picks for c in p["coins"]})
    current_prices = fetch_current_prices_binance(all_tickers)

    # ── Step 4: Compute returns ──────────────────────────────────────────────
    log.info(f"\n[4/4] Computing returns...")

    all_results: list[dict] = []
    for p in enriched_picks:
        for c in p["coins"]:
            curr = current_prices.get(c["symbol"])
            if curr is None or curr <= 0:
                continue
            ret_pct = ((curr - c["scan_price"]) / c["scan_price"]) * 100
            days_since = (
                datetime.now() - datetime.fromisoformat(p["script_date"])
            ).total_seconds() / 86400

            all_results.append({
                "symbol":         c["symbol"],
                "script_date":    p["script_date"][:10],
                "script_title":   p["title"],
                "scan_price":     c["scan_price"],
                "current_price":  curr,
                "return_pct":     round(ret_pct, 2),
                "days_since":     round(days_since, 1),
                "conviction":     c["scan_conviction"],
                "bucket":         c["bucket"],
                "scanner_count":  c["scanner_count"],
            })

    all_results.sort(key=lambda r: r["return_pct"], reverse=True)

    winners = [r for r in all_results if r["return_pct"] > 0]
    losers  = [r for r in all_results if r["return_pct"] <= 0]

    summary = {
        "total_picks":      len(all_results),
        "winners_count":    len(winners),
        "losers_count":     len(losers),
        "win_rate_pct":     round(len(winners) / max(1, len(all_results)) * 100, 1),
        "avg_return_pct":   round(sum(r["return_pct"] for r in all_results) / max(1, len(all_results)), 2),
        "best_return_pct":  all_results[0]["return_pct"] if all_results else 0,
        "worst_return_pct": all_results[-1]["return_pct"] if all_results else 0,
        "best_pick":        all_results[0]["symbol"] if all_results else None,
        "worst_pick":       all_results[-1]["symbol"] if all_results else None,
    }

    return {
        "generated":     datetime.now().isoformat(),
        "window_days":   days,
        "summary":       summary,
        "winners":       winners[:10],
        "losers":        losers[-10:],
        "all_results":   all_results,
        "scripts":       [
            {
                "date":           p["script_date"][:10],
                "title":          p["title"],
                "coins_featured": [c["symbol"] for c in p["coins"]],
            }
            for p in enriched_picks
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT
# ─────────────────────────────────────────────────────────────────────────────

def print_human_summary(report: dict) -> None:
    """Print a readable summary to the console."""
    if "error" in report:
        print(f"\n  ERROR: {report['error']}")
        if "picks_found" in report:
            print(f"  Found {report['picks_found']} scripts but no matching scanner JSON yet.")
            print(f"  This is expected — JSON outputs only started on 2026-05-12.")
            print(f"  Wait until 2026-05-19 for a full week of data.")
        return

    s = report["summary"]
    print("\n" + "=" * 72)
    print(f"  WEEKLY PERFORMANCE — past {report['window_days']} days")
    print(f"  Generated: {report['generated']}")
    print("=" * 72)

    print(f"\n  Total picks:    {s['total_picks']}")
    print(f"  Win rate:       {s['win_rate_pct']}%  ({s['winners_count']}W / {s['losers_count']}L)")
    print(f"  Average return: {s['avg_return_pct']:+.2f}%")
    print(f"  Best pick:      {s['best_pick']}  ({s['best_return_pct']:+.2f}%)")
    print(f"  Worst pick:     {s['worst_pick']}  ({s['worst_return_pct']:+.2f}%)")

    print("\n  TOP WINNERS")
    print("  " + "-" * 70)
    print(f"  {'Symbol':<10} {'Return':>10}  {'Days':>5}  {'Conv':>5}  Title")
    print("  " + "-" * 70)
    for r in report["winners"][:5]:
        title_short = (r["script_title"][:35] + "...") if len(r["script_title"]) > 38 else r["script_title"]
        print(f"  {r['symbol']:<10} {r['return_pct']:>+9.2f}%  {r['days_since']:>5.1f}  "
              f"{r['conviction']:>5.1f}  {title_short}")

    if report["losers"]:
        print("\n  TOP LOSERS")
        print("  " + "-" * 70)
        for r in reversed(report["losers"][-5:]):
            title_short = (r["script_title"][:35] + "...") if len(r["script_title"]) > 38 else r["script_title"]
            print(f"  {r['symbol']:<10} {r['return_pct']:>+9.2f}%  {r['days_since']:>5.1f}  "
                  f"{r['conviction']:>5.1f}  {title_short}")

    print("\n" + "=" * 72)


def save_report(report: dict) -> tuple[Path, Path]:
    """Save to timestamped and LATEST files."""
    ts = datetime.now().strftime("%Y%m%d")
    ts_path     = _PERF_DIR / f"weekly_performance_{ts}.json"
    latest_path = _PERF_DIR / "weekly_performance_LATEST.json"

    ts_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    latest_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    return ts_path, latest_path


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Performance tracker for published Shorts")
    parser.add_argument("--days", type=int, default=7,
                        help="Window in days (default: 7)")
    parser.add_argument("--preview", action="store_true",
                        help="Print summary but don't save JSON")
    args = parser.parse_args()

    report = build_performance_report(days=args.days)
    print_human_summary(report)

    if not args.preview and "error" not in report:
        ts_path, latest_path = save_report(report)
        print(f"\n  Saved:  {ts_path}")
        print(f"  Latest: {latest_path}\n")
    elif args.preview:
        print("\n  --preview mode: not saving JSON.\n")
