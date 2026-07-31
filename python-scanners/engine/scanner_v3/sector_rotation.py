"""
================================================================================
SECTOR ROTATION  —  which corners of the market are actually being bid
================================================================================
Answers the one question neither the exchanges nor the scanners can: WHERE to
look when the macro gate finally opens. CoinGecko categories, ranked by strength
RELATIVE TO BTC, with a persistence count so a single green day can't masquerade
as rotation.

This is a READ, NOT A GATE.
  - L67: a regime overlay used as a filter turned out to be noise. Nothing here
    may veto or authorise an entry. The 7-gate protocol is unchanged.
  - L76: CoinGecko publishes a cross-venue AVERAGE price. Never take a level
    from this file — charts for levels, scanners for reasons. No price is
    printed here for exactly that reason, only percentages.
  - A sector that is up on ONE run is noise. The DAYS column (how many of the
    last N distinct days it led) is the only column worth acting on.
  - MEDIAN, not MCAP%, is the real move: a category's market cap also jumps when
    a coin JOINS it, which is not rotation. Rows whose headline disagrees with
    their members are marked and sunk; sectors under MIN_MEMBERS coins are
    marked 'thin' because one coin is not a sector.

Run (after run_radar, so the cross-reference has fresh names):
  python sector_rotation.py
  python sector_rotation.py --top 8 --min-mcap 50 --history-days 10
  python sector_rotation.py --no-members        # skip per-sector membership calls

Writes:
  outputs/scanner-results/sector_rotation_LATEST.txt   (+ .json)
  outputs/sector_rotation/history.jsonl                (one record per run)
================================================================================
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_THIS   = Path(__file__).resolve().parent
_ROOT   = _THIS.parent.parent.parent           # crypto-scanner
_PYSCAN = _THIS.parent.parent                  # python-scanners
sys.path.insert(0, str(_THIS))                 # data.py / http_client.py live here

RADAR    = _ROOT / "outputs" / "scanner-results" / "master_radar_LATEST.json"
OUT_TXT  = _ROOT / "outputs" / "scanner-results" / "sector_rotation_LATEST.txt"
OUT_JSON = _ROOT / "outputs" / "scanner-results" / "sector_rotation_LATEST.json"
HIST     = _PYSCAN / "outputs" / "sector_rotation" / "history.jsonl"

_CG_PRO_KEY  = os.environ.get("CG_API_KEY", "")
_CG_DEMO_KEY = os.environ.get("CG_DEMO_KEY", "")
CG_API = "https://pro-api.coingecko.com/api/v3" if _CG_PRO_KEY else "https://api.coingecko.com/api/v3"

from http_client import make_session

_CG = make_session(
    api_key=(_CG_PRO_KEY or _CG_DEMO_KEY or None),
    api_key_header=("x-cg-pro-api-key" if _CG_PRO_KEY else "x-cg-demo-api-key"),
    user_agent="crypto-scanner/2.0",
)

# A category has to clear this to be ranked at all — CoinGecko carries a long
# tail of 3-coin categories whose "sector move" is one illiquid token.
DEFAULT_MIN_MCAP_M = 100.0
# How far the headline market-cap move may differ from the members median before
# the row is treated as a category-composition artifact rather than a real move.
ARTIFACT_GAP = 15.0
# Fewest members a category needs before its median counts as a SECTOR move.
MIN_MEMBERS = 5


# ─────────────────────────────────────────────────────────────────────────────
# FETCH
# ─────────────────────────────────────────────────────────────────────────────

def fetch_categories() -> list[dict]:
    r = _CG.get(f"{CG_API}/coins/categories", timeout=(5, 20))
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def fetch_members(category_id: str, limit: int = 50) -> list[dict]:
    """Members of a category, largest first: symbol + its own 24h price move.

    One call serves both jobs — the radar cross-reference AND the honest sector
    move (see member_median), so the shortlist costs one request per category.
    """
    try:
        r = _CG.get(
            f"{CG_API}/coins/markets",
            params={"vs_currency": "usd", "category": category_id,
                    "order": "market_cap_desc", "per_page": min(limit, 250), "page": 1},
            timeout=(5, 20),
        )
        r.raise_for_status()
        out = []
        for c in r.json():
            sym = c.get("symbol")
            if not sym:
                continue
            out.append({"symbol": str(sym).upper(),
                        "chg_24h": c.get("price_change_percentage_24h")})
        return out
    except Exception:
        return []


def member_median(members: list[dict]) -> float | None:
    """Median 24h price move across a category's members — the HONEST move.

    market_cap_change_24h conflates price with COMPOSITION: when CoinGecko adds
    a coin to a category the category's market cap jumps, and that shows up as a
    'sector move' that never happened. On 2026-07-31 that put "JPY Stablecoin"
    at +138% and Real World Assets at -33% on the same day. The median of what
    the members actually did cannot be moved by a membership change, and the
    median (not the mean) keeps one 400% micro-cap from carrying the sector.
    """
    vals = sorted(float(m["chg_24h"]) for m in members if m.get("chg_24h") is not None)
    if not vals:
        return None
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2


def btc_24h_pct() -> float | None:
    """BTC 24h from the project's live source of truth; CoinGecko as fallback."""
    try:
        import data
        chg = data.get_live_market_change()
        if chg:
            return float(chg[1])
    except Exception:
        pass
    try:
        r = _CG.get(f"{CG_API}/coins/markets",
                    params={"vs_currency": "usd", "ids": "bitcoin"}, timeout=(5, 15))
        r.raise_for_status()
        rows = r.json()
        if rows:
            return float(rows[0].get("price_change_percentage_24h") or 0.0)
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# HISTORY  — persistence is the entire point; one green day is noise
# ─────────────────────────────────────────────────────────────────────────────

def append_history(record: dict) -> None:
    HIST.parent.mkdir(parents=True, exist_ok=True)
    with HIST.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")


def load_history(days: int) -> list[dict]:
    if not HIST.exists():
        return []
    out = []
    try:
        for line in HIST.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []
    dates = sorted({r.get("date", "") for r in out if r.get("date")})[-days:]
    return [r for r in out if r.get("date") in set(dates)]


def leader_streak(history: list[dict]) -> dict[str, int]:
    """For each category, how many DISTINCT DAYS it led recently.

    "Led" means it made the verified top-N of a run — artifacts and thin
    categories are never recorded, so this count cannot be inflated by a
    category-composition jump. Counted per day, not per run, otherwise a day you
    happened to scan eight times outvotes a day you scanned once.
    """
    days_led: dict[str, set] = defaultdict(set)
    for rec in history:
        d = rec.get("date")
        for name in rec.get("leaders", []):
            if d:
                days_led[name].add(d)
    return {k: len(v) for k, v in days_led.items()}


# ─────────────────────────────────────────────────────────────────────────────
# RANK
# ─────────────────────────────────────────────────────────────────────────────

def rank_categories(cats: list[dict], btc_24h: float | None,
                    min_mcap_m: float) -> list[dict]:
    rows = []
    for c in cats:
        chg = c.get("market_cap_change_24h")
        mcap = c.get("market_cap")
        if chg is None or not mcap:
            continue
        mcap_m = float(mcap) / 1e6
        if mcap_m < min_mcap_m:
            continue
        # top_3_coins_id is a list of coin-id slugs ("bitcoin"); top_3_coins is
        # a list of image URLs, not symbols.
        top3 = [str(x) for x in (c.get("top_3_coins_id") or []) if isinstance(x, str)]
        rows.append({
            "id":        c.get("id") or "",
            # Category names carry stray tabs/newlines that wreck column alignment.
            "name":      " ".join(str(c.get("name") or c.get("id") or "?").split()),
            "chg_24h":   float(chg),
            # Relative strength is the signal. An absolute +3% on a day BTC did
            # +4% is a sector LAGGING, and the absolute number hides that.
            "vs_btc":    (float(chg) - btc_24h) if btc_24h is not None else None,
            "mcap_m":    mcap_m,
            "vol_24h_m": float(c.get("volume_24h") or 0) / 1e6,
            "top3":      top3,
        })
    key = (lambda r: r["vs_btc"]) if btc_24h is not None else (lambda r: r["chg_24h"])
    rows.sort(key=key, reverse=True)
    return rows


def verify_and_rerank(rows: list[dict], radar: dict | None, top_n: int,
                      btc_24h: float | None, fetch: bool) -> tuple[list[dict], dict[str, list[str]]]:
    """Re-rank a shortlist on the members' own moves, and join it to the radar.

    Ranking on market_cap_change alone surfaces composition artifacts, so the
    cheap ranking is used only to pick a shortlist (2x what we display); those
    get one members call each, which both validates the move and finds the radar
    overlap. Sectors whose headline disagrees with their members by more than
    ARTIFACT_GAP points are marked and pushed to the bottom, never silently
    dropped — a suppressed row is indistinguishable from a row that never
    existed, and this file exists to be trusted at a glance.
    """
    if not fetch:
        return rows, {}

    surfaced = set()
    for bucket in ("convergence", "strong_setup", "single_scanner", "extended"):
        for c in (radar or {}).get(bucket, []) or []:
            if c.get("base"):
                surfaced.add(str(c["base"]).upper())

    shortlist = rows[:min(len(rows), max(top_n * 2, top_n + 4))]
    hits: dict[str, list[str]] = {}
    for r in shortlist:
        if not r["id"]:
            continue
        members = fetch_members(r["id"])
        if not members:
            continue
        med = member_median(members)
        r["member_median"] = med
        r["member_n"] = len(members)
        if med is not None:
            r["vs_btc_true"] = (med - btc_24h) if btc_24h is not None else None
            r["artifact"] = abs(med - r["chg_24h"]) > ARTIFACT_GAP
            # A 1- or 3-coin "sector" is one coin wearing a category name. Its
            # median is that coin's move, and calling it rotation is how you end
            # up looking at an illiquid micro-cap because it "led a sector".
            r["thin"] = len(members) < MIN_MEMBERS
        overlap = sorted(surfaced & {m["symbol"] for m in members})
        if overlap:
            hits[r["name"]] = overlap

    def sort_key(r):
        # Verified & broad first, ranked on the members' true move. Thin and
        # artifact rows sink below them; unverified (beyond the shortlist) keep
        # their cheap ranking at the bottom.
        if r.get("member_median") is None:
            return (0, r.get("vs_btc") if r.get("vs_btc") is not None else r["chg_24h"])
        if r.get("artifact"):
            return (1, r["member_median"])
        if r.get("thin"):
            return (2, r["member_median"])
        return (3, r.get("vs_btc_true") if r.get("vs_btc_true") is not None else r["member_median"])

    rows.sort(key=sort_key, reverse=True)
    # Report overlap only for sectors actually shown as leading — listing a hit
    # for a sector the reader cannot see in the table above reads as a
    # recommendation with no context behind it.
    shown = {r["name"] for r in rows[:top_n]}
    hits = {k: v for k, v in hits.items() if k in shown}
    return rows, hits


# ─────────────────────────────────────────────────────────────────────────────
# RENDER
# ─────────────────────────────────────────────────────────────────────────────

def render(rows: list[dict], streaks: dict[str, int], hits: dict[str, list[str]],
           btc_24h: float | None, top_n: int, hist_days: int, n_days: int,
           verified: bool = True) -> str:
    L: list[str] = []
    add = L.append
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    add("=" * 78)
    add("  SECTOR ROTATION  —  where the bid is (READ ONLY, never a gate)")
    add(f"  Generated : {now}")
    btc_s = f"{btc_24h:+.2f}%" if btc_24h is not None else "unavailable"
    add(f"  BTC 24h   : {btc_s}   |   ranked by strength RELATIVE to BTC")
    add(f"  History   : {n_days} distinct day(s) on file (window {hist_days}d)")
    add("=" * 78)
    add("")

    if not verified:
        add("  " + "!" * 74)
        add("  !!  UNVERIFIED RUN (--no-members) — DO NOT READ AS ROTATION.")
        add("  !!  Ranked on raw market-cap change, which also moves when a coin JOINS")
        add("  !!  a category. On 2026-07-31 that put 'JPY Stablecoin' top at +138%.")
        add("  !!  No history was recorded. Re-run without --no-members to trust this.")
        add("  " + "!" * 74)
        add("")

    if btc_24h is None:
        add("  ⚠️  BTC 24h unavailable — rows below are ABSOLUTE moves, not relative.")
        add("      A sector 'leading' on an absolute basis while BTC outruns it is")
        add("      not leading. Treat this run as unranked.")
        add("")

    add(f"  LEADING  —  top {top_n} of {len(rows)} categories")
    add("  " + "-" * 74)
    add(f"  {'#':>2}  {'SECTOR':26} {'MED vs BTC':>11} {'MEDIAN':>8} {'MCAP%':>8} {'N':>3} {'DAYS':>5}")
    add("  " + "-" * 74)
    for i, r in enumerate(rows[:top_n], 1):
        med = r.get("member_median")
        vst = r.get("vs_btc_true")
        vs_s  = f"{vst:+.2f}%" if vst is not None else "     —  "
        med_s = f"{med:+.2f}%" if med is not None else "    —  "
        streak = streaks.get(r["name"], 0)
        flag = " ⚠" if r.get("artifact") else (" ←" if streak >= 3 else "")
        add(f"  {i:>2}  {r['name'][:26]:26} {vs_s:>11} {med_s:>8} "
            f"{r['chg_24h']:>+7.2f}% {r.get('member_n', 0):>3} {streak:>5}{flag}")
    add("")
    if any(r.get("artifact") for r in rows[:top_n]):
        add("  ⚠ = headline MCAP% disagrees with the members' own median by >"
            f"{ARTIFACT_GAP:.0f} points:")
        add("      a coin joined or left the category. The MEDIAN column is the real move.")
        add("")

    add("  LAGGING  —  bottom 5 (unverified: ranked on raw MCAP%, may be artifacts)")
    add("  " + "-" * 74)
    for r in rows[-5:]:
        vs = f"{r['vs_btc']:+.2f}%" if r["vs_btc"] is not None else "   —  "
        add(f"      {r['name'][:28]:28} {vs:>8} {r['chg_24h']:>+7.2f}%")
    add("")

    if hits:
        add("  RADAR NAMES INSIDE LEADING SECTORS  —  where to look, not what to buy")
        add("  " + "-" * 74)
        for sector, names in hits.items():
            add(f"    {sector[:34]:34} {', '.join(names[:10])}")
        add("")
    else:
        add("  RADAR NAMES INSIDE LEADING SECTORS  —  none")
        add("    (no overlap, or run_radar output missing / --no-members set)")
        add("")

    add("-" * 78)
    add("  HOW TO READ THIS")
    add("    • DAYS is the only column worth acting on. It counts DISTINCT DAYS the")
    add("      sector was top-quartile. 1 day = noise. '←' marks 3+ days.")
    add("    • Relative to BTC, always. A sector up +3% while BTC did +4% is a")
    add("      sector being SOLD, and its absolute number hides that.")
    add("    • This CANNOT authorise an entry (L67: an overlay used as a filter was")
    add("      noise). Macro gate, spot gate and the 7-gate protocol are unchanged.")
    add("    • No prices here by design — CoinGecko is a cross-venue average and a")
    add("      level taken from an average is how XMR read 195% wrong (L76).")
    add("=" * 78)
    return "\n".join(L)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    # The report uses box-drawing and arrows; a bare Windows console is cp1252
    # and would raise on print() even though the file write is UTF-8.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="Sector rotation read (CoinGecko categories)")
    ap.add_argument("--top", type=int, default=10, help="how many leading sectors to show")
    ap.add_argument("--min-mcap", type=float, default=DEFAULT_MIN_MCAP_M,
                    help="minimum category market cap in $M (default 100)")
    ap.add_argument("--history-days", type=int, default=14,
                    help="window for the DAYS persistence count")
    ap.add_argument("--no-members", action="store_true",
                    help="skip per-sector membership calls (no radar cross-reference)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    try:
        cats = fetch_categories()
    except Exception as e:
        print(f"ERROR: CoinGecko categories fetch failed: {e}")
        print("       Nothing written — an empty sector read is worse than none.")
        return 1
    if not cats:
        print("ERROR: CoinGecko returned no categories. Nothing written.")
        return 1

    btc = btc_24h_pct()
    rows = rank_categories(cats, btc, args.min_mcap)
    if not rows:
        print(f"ERROR: no category cleared --min-mcap {args.min_mcap}$M. Nothing written.")
        return 1

    radar = None
    try:
        radar = json.loads(RADAR.read_text(encoding="utf-8"))
    except Exception:
        pass
    verified = not args.no_members
    rows, hits = verify_and_rerank(rows, radar, args.top, btc, fetch=verified)

    # History is written AFTER verification and only from a verified run. The
    # DAYS column is the one number this file asks you to act on, so it must not
    # be built from the raw market-cap ranking — that would have recorded "JPY
    # Stablecoin +138%" as a leader. An unverified run is a look, not a record.
    if verified:
        leaders = [r["name"] for r in rows[:args.top]
                   if not r.get("artifact") and not r.get("thin")]
        append_history({
            "ts":      datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "date":    datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "btc_24h": btc,
            "leaders": leaders,
            "top":     [{"name": r["name"], "vs_btc_true": r.get("vs_btc_true"),
                         "member_median": r.get("member_median"), "chg_24h": r["chg_24h"]}
                        for r in rows[:args.top]],
        })

    history = load_history(args.history_days)
    streaks = leader_streak(history)
    n_days  = len({r.get("date") for r in history if r.get("date")})

    report = render(rows, streaks, hits, btc, args.top, args.history_days, n_days, verified)
    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUT_TXT.write_text(report, encoding="utf-8")
    OUT_JSON.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "btc_24h_pct":  btc,
        "ranked_by":    "vs_btc" if btc is not None else "absolute_24h",
        "history_days": n_days,
        "members_verified": verified,
        "categories":   rows,
        "streaks":      streaks,
        "radar_overlap": hits,
    }, indent=2), encoding="utf-8")

    if not args.quiet:
        print(report)
        print(f"\nWritten: {OUT_TXT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
