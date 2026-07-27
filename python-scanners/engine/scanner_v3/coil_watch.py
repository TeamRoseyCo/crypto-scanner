"""
================================================================================
COIL WATCH  —  the pre-PEOPLE confluence-coil watchlist (+ entry levels)
================================================================================
Reads master_radar_LATEST.json and surfaces the ONLY coil profile the backtest
says has an edge: a persistent stealth_accum coil that is (a) still EARLY (low
24h%, not yet vertical) AND (b) already carries a tradeable trend tier — i.e.
CONFLUENCE. Standalone coils are −0.19R (survivorship, n=459); the trend+regime
gate flips them to +0.25R (see reference_coil_breakout_backtest / L72).

For each qualifying coil it prints entry / stop / TP levels using the
backtest-validated WIDE targets (RR 3/6/10 — trend-tier coils RUN, so wide TPs
capture what the live plan's 1.5/3/5 caps out of) plus 1%-risk position sizing.

This is a WATCHLIST tool, not a trade signal. It prints the macro/regime/daily
gate at the top precisely because none of these are live entries until the gate
opens (DXY<99 AND 10Y<4.30) AND the coil actually breaks out on volume.

Run (after run_radar):
  python coil_watch.py
  python coil_watch.py --account 96000 --risk 1.0 --max-24h 6 --min-tier long
================================================================================
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_THIS   = Path(__file__).resolve().parent
_ROOT   = _THIS.parent.parent.parent          # crypto-scanner
_PYSCAN = _THIS.parent.parent                  # python-scanners

RADAR  = _ROOT / "outputs" / "scanner-results" / "master_radar_LATEST.json"
REGIME = _ROOT / "outputs" / "scanner-results" / "last_regime.json"
MACRO  = _PYSCAN / "outputs" / "macro" / "latest.json"
DAILY  = _PYSCAN / "outputs" / "daily_pnl" / "today.json"

WIDE_RR = [3.0, 6.0, 10.0]        # backtest-validated targets for confluence coils
TIER_RANK = {"below": 0, "watch": 1, "long": 2, "strong": 3, None: -1}


def _load(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def _fmt(x: float) -> str:
    """Adaptive price precision for both $60,000 and $0.0000x coins."""
    if x == 0:
        return "0"
    ax = abs(x)
    dec = 2 if ax >= 100 else 4 if ax >= 1 else 6 if ax >= 0.01 else 8
    return f"{x:.{dec}f}"


def _sig(c, key):
    v = c.get(key) or []
    return [str(x) for x in v] if isinstance(v, list) else []


def _has(c, name):
    return any(name in s for s in _sig(c, "ignition_signals") + _sig(c, "spot_signals"))


def gate_header(lines):
    macro = _load(MACRO) or {}
    reg   = _load(REGIME) or {}
    daily = _load(DAILY) or {}
    verdict = macro.get("verdict", "?")
    open_macro = verdict != "RISK-OFF" and (macro.get("dxy", 999) < 99 and macro.get("y10", 9) < 4.30)
    lines.append("=" * 84)
    lines.append("  COIL WATCH  —  confluence-coil watchlist  (pre-breakout, the +0.25R subset)")
    lines.append(f"  Generated: {datetime.now():%Y-%m-%d %H:%M}")
    lines.append("=" * 84)
    lines.append(f"  MACRO  : {verdict}   DXY {macro.get('dxy','?')}   10Y {macro.get('y10','?')}%"
                 f"   {'(gate OPEN)' if open_macro else '(gate SHUT — DXY<99 & 10Y<4.30 needed)'}")
    lines.append(f"  REGIME : {str(reg.get('regime','?')).upper()}   "
                 f"BTC 7d {reg.get('btc_7d_pct',0):+.2f}%  24h {reg.get('btc_24h_pct',0):+.2f}%")
    lines.append(f"  DAILY  : {daily.get('status','?')}   headroom ${daily.get('headroom_to_limit',0):,.0f}"
                 f"   trades {daily.get('closed_trade_count','?')}")
    if not open_macro:
        lines.append("  >>> WATCHLIST ONLY — macro gate shut. No entries regardless of chart. <<<")
    lines.append("")
    return open_macro


def run(account: float, risk_pct: float, max_24h: float, min_tier: str, max_pos_pct: float):
    d = _load(RADAR)
    lines: list[str] = []
    gate_header(lines)
    if not d:
        lines.append("  master_radar_LATEST.json not found — run run_radar first.")
        print("\n".join(lines)); return

    pool = d.get("convergence", []) + d.get("strong_setup", []) + d.get("single_scanner", [])
    min_rank = TIER_RANK.get(min_tier, 2)

    confl, forming = [], []
    for c in pool:
        if not _has(c, "stealth"):
            continue
        if (c.get("price_24h_pct") or 0) > max_24h:      # still early only
            continue
        rank = TIER_RANK.get(c.get("trend_tier"), -1)
        (confl if rank >= min_rank else forming).append(c)

    confl.sort(key=lambda c: (c.get("trend_score") or 0), reverse=True)
    forming.sort(key=lambda c: (c.get("trend_score") or 0), reverse=True)

    risk_budget = account * risk_pct / 100.0

    # ── the edge subset ──────────────────────────────────────────────────────
    lines.append("-" * 84)
    lines.append(f"  CONFLUENCE COILS  (stealth + early + trend≥{min_tier})  —  the tradeable subset")
    lines.append("-" * 84)
    if not confl:
        lines.append("  (none right now — no early stealth-coil currently carries a trend tier)")
    else:
        lines.append(f"  {'COIN':<9}{'24h%':>6}  {'trend':<7}{'score':>6}{'ST':>4}  {'vol$M':>7}  {'state':<13} signals")
        for c in confl:
            brk = "BREAKING OUT" if _has(c, "vol_expansion") else "coiling"
            extra = [s for s in ("bb_squeeze", "higher_lows", "whale_candle", "cmf_positive")
                     if _has(c, s)]
            lines.append(f"  {c['base']:<9}{c.get('price_24h_pct',0):>5.1f}%  "
                         f"{str(c.get('trend_tier')):<7}{c.get('trend_score') or 0:>6.0f}"
                         f"{str(c.get('trend_st_aligned') or '-'):>4}  {(c.get('volume_24h') or 0)/1e6:>6.1f}  "
                         f"{brk:<13} {', '.join(extra)}")
    lines.append("")

    # ── entry levels for each confluence coil that has a plan ────────────────
    if confl:
        lines.append("-" * 84)
        lines.append(f"  ENTRY LEVELS  (wide TP 3/6/10 — backtest-validated; risk {risk_pct:.1f}% = ${risk_budget:,.0f})")
        lines.append("-" * 84)
        for c in confl:
            tp = c.get("trade_plan") or {}
            entry, stop = tp.get("entry"), tp.get("stop")
            if not entry or not stop or entry <= stop:
                lines.append(f"  {c['base']:<9} no valid plan in radar — check chart for stop before sizing")
                continue
            risk_u = entry - stop
            stop_pct = (stop - entry) / entry * 100.0
            pos_value = risk_budget / (abs(stop_pct) / 100.0)
            pos_pct = pos_value / account * 100.0
            capped = pos_pct > max_pos_pct
            if capped:
                pos_value = account * max_pos_pct / 100.0
                real_risk = pos_value * abs(stop_pct) / 100.0
            tps = [entry + risk_u * rr for rr in WIDE_RR]
            lines.append(f"  {c['base']}   entry {_fmt(entry)}   stop {_fmt(stop)} ({stop_pct:+.1f}%)   "
                         f"{'[COILING — no breakout yet]' if not _has(c,'vol_expansion') else '[breakout firing]'}")
            lines.append(f"     TP1 (3R) {_fmt(tps[0])}  +{(tps[0]/entry-1)*100:.0f}%   "
                         f"TP2 (6R) {_fmt(tps[1])}  +{(tps[1]/entry-1)*100:.0f}%   "
                         f"TP3 (10R) {_fmt(tps[2])}  +{(tps[2]/entry-1)*100:.0f}%")
            if capped:
                lines.append(f"     size: ${pos_value:,.0f} ({max_pos_pct:.0f}% pos-CAP binds; tight stop → "
                             f"actual risk ~${real_risk:,.0f}, under 1%)")
            else:
                lines.append(f"     size @{risk_pct:.0f}% risk: ${pos_value:,.0f} ({pos_pct:.1f}% of acct)")
            lines.append("")

    # ── forming (trend not yet tradeable) — watch, no levels ─────────────────
    lines.append("-" * 84)
    lines.append("  FORMING  (early stealth-coils, trend tier not yet ≥long — watch only, NO edge yet)")
    lines.append("-" * 84)
    if forming:
        lines.append("  " + ", ".join(f"{c['base']}({c.get('price_24h_pct',0):+.0f}%/"
                     f"{str(c.get('trend_tier') or '-')})" for c in forming[:24]))
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("=" * 84)
    lines.append("  ENTRY TRIGGER (all must be TRUE):  macro gate OPEN  +  coil BREAKING OUT (vol_expansion,")
    lines.append("  RSI still <65, not vertical)  +  spot qualifies the name.  Until then: WATCH.")
    lines.append("  Edge is fat-tailed (real ~+0.1R after fees) — take EVERY qualifier, don't cherry-pick.")
    lines.append("=" * 84)

    report = "\n".join(lines)
    print(report)
    out = _ROOT / "outputs" / "scanner-results" / "coil_watch_LATEST.txt"
    try:
        out.write_text(report, encoding="utf-8")
        print(f"\n  Saved → {out}")
    except Exception:
        pass


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Coil watch — confluence-coil watchlist + entry levels")
    ap.add_argument("--account", type=float, default=96000, help="account size USDT (default 96000)")
    ap.add_argument("--risk", type=float, default=1.0, help="risk %% per trade (default 1.0)")
    ap.add_argument("--max-24h", type=float, default=6.0, help="max 24h%% to still count as 'early' (default 6)")
    ap.add_argument("--min-tier", choices=["watch", "long", "strong"], default="long",
                    help="min trend tier for the confluence subset (default long)")
    ap.add_argument("--max-pos-pct", type=float, default=8.0, help="max position %% of account (default 8)")
    args = ap.parse_args()
    run(args.account, args.risk, args.max_24h, args.min_tier, args.max_pos_pct)
