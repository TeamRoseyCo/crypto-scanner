"""
================================================================================
RUN SCAN  v3.0  —  Master Orchestrator
================================================================================
Single entry point that reads outputs from all 4 scanners and produces ONE
unified ranked report. This is the keystone of the v3 system.

Inputs (read from outputs/scanner-results/):
  ignition_v3_LATEST.json     — alpha (1h breakouts/accumulation)
  perp_v3_LATEST.json         — alpha (OI/funding positioning)
  spot_trade_plan_LATEST.json — alpha (4h accumulation, regime-aware)
  trend_v3_LATEST.json        — confirmation (multi-TF trend confluence)

Output: ONE master report with cross-scanner confluence ranking.

Confluence philosophy:
  Alpha scanners propose setups (ignition, perp, spot).
  Trend scanner confirms whether the trend supports the setup.
  Coins surfacing in MULTIPLE scanners = strongest signal.

Dormant hooks (filled later):
  apply_tpi_gate()   — when TPI integration goes live, gates output by macro
  apply_rsps_rank()  — when RSPS integration goes live, re-ranks final list

Run:
  python run_scan.py                               # auto-runs ignition+perp+trend, reads spot's existing JSON
  python run_scan.py --no-rerun                    # don't run scanners, just orchestrate from existing JSON
  python run_scan.py --account 95000               # custom account size
  python run_scan.py --skip ignition,trend         # skip specific scanners
  python run_scan.py --include-spot                # ALSO run spot_scanner (slow: 40-50 min added)
================================================================================
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────────────────
_THIS_DIR     = Path(__file__).resolve().parent             # scanner_v3/
_ENGINE_DIR   = _THIS_DIR.parent                             # engine/
_PYTHON_DIR   = _ENGINE_DIR.parent                           # python-scanners/
_PROJECT_ROOT = _PYTHON_DIR.parent                           # crypto-scanner/  (matches spot_scanner.py)
_OUTPUT_DIR   = _PROJECT_ROOT / "outputs" / "scanner-results"
_LOG_DIR      = _PROJECT_ROOT / "outputs" / "logs"
for d in (_OUTPUT_DIR, _LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Scanner script paths
_IGNITION_PY = _THIS_DIR    / "ignition_scanner.py"
_PERP_PY     = _THIS_DIR    / "perp_scanner.py"
_TREND_PY    = _THIS_DIR    / "trend_scanner.py"
_SPOT_PY     = _ENGINE_DIR  / "spot_scanner.py"   # legacy — sibling of scanner_v3/

# Input JSON files
_IGNITION_JSON = _OUTPUT_DIR / "ignition_v3_LATEST.json"
_PERP_JSON     = _OUTPUT_DIR / "perp_v3_LATEST.json"
_TREND_JSON    = _OUTPUT_DIR / "trend_v3_LATEST.json"
_SPOT_JSON     = _OUTPUT_DIR / "spot_trade_plan_LATEST.json"


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
log = logging.getLogger("scanner_v3.master_radar")
log.propagate = False   # don't bubble to root or parent loggers — avoids file collisions
if not log.handlers:
    # Distinct filename — was 'orchestrator_v3_<date>.log' which collided with
    # spot_scanner's log naming. 'master_radar_<date>.log' is unambiguous.
    handler_file = logging.FileHandler(
        _LOG_DIR / f"master_radar_{datetime.now().strftime('%Y%m%d')}.log",
        encoding="utf-8",
    )
    handler_file.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    handler_stdout = logging.StreamHandler(sys.stdout)
    handler_stdout.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(handler_file)
    log.addHandler(handler_stdout)
    log.setLevel(logging.INFO)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIDENCE / CONFLUENCE SCORING
# ─────────────────────────────────────────────────────────────────────────────

# Per-tier points for each scanner. Alpha-heavy weighting per spec.
TIER_POINTS = {
    "ignition": {"watch_now": 6.0, "on_radar": 3.0},   # 1.5× weight already baked in
    "perp":     {"watch_now": 6.0, "on_radar": 3.0},
    "spot":     {"qualified": 6.0},                     # spot only has one tier in JSON
    "trend":    {"strong":    5.0, "long":     3.0, "watch": 1.0},
}

# Confluence buckets — final classification
CONFLUENCE_BUCKETS = {
    "convergence":     {"min_score": 10.0, "min_scanners": 3},
    "strong_setup":    {"min_score":  6.0, "min_scanners": 2},
    "single_scanner":  {"min_score":  0.0, "min_scanners": 1},
}

# Already-pumping filter — coins whose 24h move exceeds this get
# split into a separate EXTENDED bucket regardless of their confluence score.
# Rationale: by the time a coin is up 15%+ today, the entry has likely passed.
# Better to surface them visibly (so you don't ignore real breakouts) but
# separately (so they don't crowd out cleaner setups in the main buckets).
EXTENDED_THRESHOLD_24H_PCT = 15.0

# Freshness threshold — warn if any input JSON older than this
INPUT_FRESHNESS_HOURS = 2.0


# ─────────────────────────────────────────────────────────────────────────────
# RESULT TYPE
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CoinView:
    """Per-coin aggregated view across all scanners."""
    base:           str
    confluence:     float = 0.0
    scanner_count:  int   = 0
    bucket:         str   = "below"          # "convergence" | "strong_setup" | "single_scanner" | "extended"

    # Per-scanner tier — None if not surfaced by that scanner
    ignition_tier:  Optional[str] = None
    perp_tier:      Optional[str] = None
    spot_tier:      Optional[str] = None
    trend_tier:     Optional[str] = None

    # Best metadata snapshot (preferred from trend > spot > ignition > perp)
    price:          Optional[float] = None
    price_24h_pct:  Optional[float] = None
    volume_24h:     Optional[float] = None

    # Trade plan (preferred from trend, fallback to spot)
    trade_plan:     Optional[dict] = None
    trade_plan_source: Optional[str] = None

    # Diagnostic — which signals fired across scanners
    ignition_signals: list[str] = field(default_factory=list)
    perp_signals:     list[str] = field(default_factory=list)
    spot_signals:     list[str] = field(default_factory=list)
    trend_score:      Optional[float] = None
    trend_st_aligned: Optional[int]   = None

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# JSON LOADING WITH FRESHNESS CHECK
# ─────────────────────────────────────────────────────────────────────────────

def _load_json(path: Path, label: str) -> tuple[Optional[dict], Optional[str]]:
    """
    Load a scanner JSON output. Returns (data, warning).
    warning is None if file is fresh, otherwise a string describing the issue.
    """
    if not path.exists():
        return None, f"{label}: file not found ({path.name})"
    try:
        age_h = (time.time() - path.stat().st_mtime) / 3600
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return None, f"{label}: failed to read ({e})"

    warning = None
    if age_h > INPUT_FRESHNESS_HOURS:
        warning = f"{label}: stale ({age_h:.1f}h old)"
    return data, warning


def _load_previous_convictions(json_path: Path) -> dict[str, float]:
    """
    Read the previous master_radar JSON to grab per-coin conviction values.
    Used to compute trajectory arrows (rising/falling/flat) on the fresh scan.
    Returns {} if the file doesn't exist or fails to parse.
    """
    if not json_path.exists():
        return {}
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    prev: dict[str, float] = {}
    for bucket in ("convergence", "strong_setup", "single_scanner", "extended"):
        for entry in data.get(bucket, []) or []:
            base = entry.get("base")
            conf = entry.get("confluence")
            if base and conf is not None:
                prev[base] = conf
    return prev


def _trajectory_arrow(current: float, previous: Optional[float]) -> str:
    """
    Compare current conviction to previous-scan conviction.
    ↗ = rising, ↘ = falling, → = flat, blank = new (not in previous scan).
    Threshold of 0.5 ignores trivial fluctuations.
    """
    if previous is None:
        return "  "      # new pick — no previous reading to compare
    diff = current - previous
    if abs(diff) < 0.5:
        return "→ "      # flat
    return "↗ " if diff > 0 else "↘ "


# ─────────────────────────────────────────────────────────────────────────────
# PER-SCANNER PARSERS  — extract symbol → tier mapping from each JSON shape
# ─────────────────────────────────────────────────────────────────────────────

def parse_ignition(data: Optional[dict]) -> dict[str, dict]:
    """
    Extract {base: {tier, signals, price, volume, etc.}} from ignition_scanner JSON.
    Returns empty dict on missing/malformed input.
    """
    if not data:
        return {}
    out: dict[str, dict] = {}
    for tier_key in ("watch_now", "on_radar"):
        for entry in data.get(tier_key, []) or []:
            base = entry.get("base")
            if not base:
                continue
            out[base] = {
                "tier":          tier_key,
                "signals":       entry.get("fired_signals", []),
                "price":         entry.get("price"),
                "price_24h_pct": entry.get("price_24h_pct"),
                "volume_24h":    entry.get("volume_24h"),
            }
    return out


def parse_perp(data: Optional[dict]) -> dict[str, dict]:
    if not data:
        return {}
    out: dict[str, dict] = {}
    for tier_key in ("watch_now", "on_radar"):
        for entry in data.get(tier_key, []) or []:
            base = entry.get("base")
            if not base:
                continue
            out[base] = {
                "tier":          tier_key,
                "signals":       entry.get("fired_signals", []),
                "price":         entry.get("price"),
                "price_24h_pct": entry.get("price_24h_pct"),
                "volume_24h":    entry.get("turnover_24h"),
            }
    return out


def parse_trend(data: Optional[dict]) -> dict[str, dict]:
    if not data:
        return {}
    out: dict[str, dict] = {}
    for tier_key in ("strong", "long", "watch"):
        for entry in data.get(tier_key, []) or []:
            base = entry.get("base")
            if not base:
                continue
            out[base] = {
                "tier":           tier_key,
                "score":          entry.get("total_score"),
                "st_aligned":     entry.get("st_aligned"),
                "price":          entry.get("price"),
                "price_24h_pct":  entry.get("price_24h_pct"),
                "volume_24h":     entry.get("volume_24h"),
                "trade_plan":     entry.get("trade_plan"),
            }
    return out


def parse_spot(data: Optional[dict]) -> dict[str, dict]:
    """
    Spot scanner JSON has different shape — top-level 'candidates' list,
    each with embedded 'plan' and 'signals' dicts.
    """
    if not data:
        return {}
    out: dict[str, dict] = {}
    for entry in data.get("candidates", []) or []:
        base = entry.get("symbol")
        if not base:
            continue
        sig = entry.get("signals", {}) or {}
        plan = entry.get("plan")
        out[base] = {
            "tier":          "qualified",
            "signals":       sig.get("active_signals", []),
            "conviction":    sig.get("conviction"),
            "signal_count":  sig.get("signal_count"),
            "price":         entry.get("price"),
            "price_24h_pct": None,                  # not in spot JSON; would need 24h derive
            "change_7d":     entry.get("change_7d"),
            "volume_24h":    entry.get("vol_24h"),
            "trade_plan":    plan,
        }
    return out


def get_market_context(data_trend: Optional[dict],
                       data_spot:  Optional[dict]) -> dict:
    """
    Pull market context from trend+spot JSONs. Trend wins on regime since it's
    the most authoritative classification.
    """
    ctx = {
        "regime":      "unknown",
        "btc_7d_pct":  None,
        "btc_24h_pct": None,
        "spot_says_stay_out": False,
        "spot_qualified_count": 0,
    }
    if data_trend:
        ctx["regime"]      = data_trend.get("regime", "unknown")
        ctx["btc_7d_pct"]  = data_trend.get("btc_7d_pct")
        ctx["btc_24h_pct"] = data_trend.get("btc_24h_pct")
    if data_spot:
        candidates = data_spot.get("candidates", []) or []
        ctx["spot_qualified_count"] = len(candidates)
        ctx["spot_says_stay_out"]   = (len(candidates) == 0)
        # Try to fill BTC numbers from spot if trend was missing them
        spot_ctx = data_spot.get("market_context", {}) or {}
        if ctx["btc_7d_pct"] is None:
            ctx["btc_7d_pct"]  = spot_ctx.get("btc_7d_pct")
        if ctx["btc_24h_pct"] is None:
            ctx["btc_24h_pct"] = spot_ctx.get("btc_24h_pct")
    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# CONFLUENCE COMPUTATION
# ─────────────────────────────────────────────────────────────────────────────

def build_coin_views(
    ignition: dict[str, dict],
    perp:     dict[str, dict],
    spot:     dict[str, dict],
    trend:    dict[str, dict],
) -> dict[str, CoinView]:
    """Merge all four scanners by base symbol, compute confluence per coin."""
    all_bases = set(ignition) | set(perp) | set(spot) | set(trend)
    views: dict[str, CoinView] = {}

    for base in all_bases:
        v = CoinView(base=base)
        scanners_present = 0
        score = 0.0

        # ── Ignition ────────────────────────────────────────────────────────
        if base in ignition:
            scanners_present += 1
            tier = ignition[base]["tier"]
            v.ignition_tier = tier
            v.ignition_signals = ignition[base].get("signals", []) or []
            score += TIER_POINTS["ignition"].get(tier, 0.0)
            # Capture price/24h/vol if not yet
            if v.price is None:          v.price         = ignition[base].get("price")
            if v.price_24h_pct is None:  v.price_24h_pct = ignition[base].get("price_24h_pct")
            if v.volume_24h is None:     v.volume_24h    = ignition[base].get("volume_24h")

        # ── Perp ────────────────────────────────────────────────────────────
        if base in perp:
            scanners_present += 1
            tier = perp[base]["tier"]
            v.perp_tier = tier
            v.perp_signals = perp[base].get("signals", []) or []
            score += TIER_POINTS["perp"].get(tier, 0.0)
            if v.price is None:          v.price         = perp[base].get("price")
            if v.price_24h_pct is None:  v.price_24h_pct = perp[base].get("price_24h_pct")
            if v.volume_24h is None:     v.volume_24h    = perp[base].get("volume_24h")

        # ── Spot ────────────────────────────────────────────────────────────
        if base in spot:
            scanners_present += 1
            v.spot_tier = "qualified"
            v.spot_signals = spot[base].get("signals", []) or []
            score += TIER_POINTS["spot"].get("qualified", 0.0)
            if v.price is None:       v.price       = spot[base].get("price")
            if v.volume_24h is None:  v.volume_24h  = spot[base].get("volume_24h")
            # Spot has trade plan
            if v.trade_plan is None and spot[base].get("trade_plan"):
                v.trade_plan = spot[base]["trade_plan"]
                v.trade_plan_source = "spot"

        # ── Trend ───────────────────────────────────────────────────────────
        if base in trend:
            scanners_present += 1
            tier = trend[base]["tier"]
            v.trend_tier = tier
            v.trend_score = trend[base].get("score")
            v.trend_st_aligned = trend[base].get("st_aligned")
            score += TIER_POINTS["trend"].get(tier, 0.0)
            # Trend's metadata is most authoritative — overwrite
            if trend[base].get("price") is not None:
                v.price = trend[base]["price"]
            if trend[base].get("price_24h_pct") is not None:
                v.price_24h_pct = trend[base]["price_24h_pct"]
            if trend[base].get("volume_24h") is not None:
                v.volume_24h = trend[base]["volume_24h"]
            # Trend's trade plan beats spot's
            if trend[base].get("trade_plan"):
                v.trade_plan = trend[base]["trade_plan"]
                v.trade_plan_source = "trend"

        v.confluence    = round(score, 1)
        v.scanner_count = scanners_present
        v.bucket        = _classify_bucket(score, scanners_present, v.price_24h_pct)
        views[base] = v

    return views


def _classify_bucket(
    score:           float,
    scanner_count:   int,
    price_24h_pct:   Optional[float] = None,
) -> str:
    """
    Classify into convergence / strong_setup / single_scanner / extended / below.

    The 'extended' override: if the coin is up more than EXTENDED_THRESHOLD_24H_PCT
    in the last 24h, it goes to its own bucket regardless of confluence score —
    so already-pumping coins don't crowd out cleaner setups in the main buckets.
    Coin must have at least one scanner firing (score > 0) to surface at all.
    """
    # Check for "already moving" override first — but only if we know the 24h move
    # AND the coin actually had some signal fire (not a pure no-op coin)
    if (score > 0
            and price_24h_pct is not None
            and price_24h_pct >= EXTENDED_THRESHOLD_24H_PCT):
        return "extended"

    # Normal bucketing
    if (score >= CONFLUENCE_BUCKETS["convergence"]["min_score"]
            and scanner_count >= CONFLUENCE_BUCKETS["convergence"]["min_scanners"]):
        return "convergence"
    if (score >= CONFLUENCE_BUCKETS["strong_setup"]["min_score"]
            and scanner_count >= CONFLUENCE_BUCKETS["strong_setup"]["min_scanners"]):
        return "strong_setup"
    if score > 0:
        return "single_scanner"
    return "below"


# ─────────────────────────────────────────────────────────────────────────────
# DORMANT HOOKS  — TPI + RSPS integration points (no-op for now)
# ─────────────────────────────────────────────────────────────────────────────

def apply_tpi_gate(views: dict[str, CoinView], context: dict) -> dict[str, CoinView]:
    """
    TPI integration hook. Currently a no-op.

    When wired up: read your TPI score from a Sheet/CSV, derive regime
    (Strong Bullish / Bullish rising / etc.), and apply gating:
      - Strong Bullish + rising  → no change, full aggression
      - Bullish + falling        → demote convergence to strong_setup
      - Bearish + falling        → block all but watchlist
      - Bearish + rising         → cover-shorts mode

    For now: returns views unchanged. Integration is one function away.
    """
    # Future: read TPI score, apply gate
    return views


def apply_rsps_rank(views: dict[str, CoinView], context: dict) -> dict[str, CoinView]:
    """
    RSPS integration hook. Currently a no-op.

    When wired up: cross-sectional re-ranking of final candidate list using
    your RSPS Sheet. Could boost coins with strong relative performance,
    demote underperformers.
    """
    # Future: read RSPS rankings, re-rank views
    return views


# ─────────────────────────────────────────────────────────────────────────────
# REPORT BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _tier_badge(scanner: str, tier: Optional[str]) -> str:
    """Small badge string for a scanner tier."""
    if tier is None:
        return "  ·  "
    badges = {
        "ignition": {"watch_now": "I★★", "on_radar": "I★ "},
        "perp":     {"watch_now": "P★★", "on_radar": "P★ "},
        "spot":     {"qualified": "S★★"},
        "trend":    {"strong":    "T★★", "long":     "T★ ", "watch": "T· "},
    }
    return badges.get(scanner, {}).get(tier, "  ·  ")


def _signals_summary(view: CoinView, max_per_scanner: int = 3) -> str:
    """Build a compact signal summary across scanners."""
    parts = []
    if view.ignition_signals:
        sigs = ", ".join(view.ignition_signals[:max_per_scanner])
        if len(view.ignition_signals) > max_per_scanner:
            sigs += f" +{len(view.ignition_signals)-max_per_scanner}"
        parts.append(f"I:[{sigs}]")
    if view.perp_signals:
        sigs = ", ".join(view.perp_signals[:max_per_scanner])
        if len(view.perp_signals) > max_per_scanner:
            sigs += f" +{len(view.perp_signals)-max_per_scanner}"
        parts.append(f"P:[{sigs}]")
    if view.spot_signals:
        sigs = ", ".join(view.spot_signals[:max_per_scanner])
        if len(view.spot_signals) > max_per_scanner:
            sigs += f" +{len(view.spot_signals)-max_per_scanner}"
        parts.append(f"S:[{sigs}]")
    if view.trend_score is not None:
        st = view.trend_st_aligned or 0
        parts.append(f"T:[score={view.trend_score:.0f}, ST={st}/6]")
    return "  |  ".join(parts) if parts else "(no signals)"


def build_text_report(
    views_sorted: list[CoinView],
    context:      dict,
    warnings:     list[str],
    elapsed_s:    float,
    prev_convs:   Optional[dict[str, float]] = None,
) -> str:
    prev_convs = prev_convs or {}
    # Regime gating: when spot scanner says STAY IN USDT, hide entry plans
    # so the user can't accidentally treat a STRONG SETUP as entry-ready.
    # The pick info is still shown — just labeled WATCH ONLY.
    regime_locked = bool(context.get("spot_says_stay_out"))
    sep  = "═" * 80
    dash = "─" * 80
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    convergence    = [v for v in views_sorted if v.bucket == "convergence"]
    strong_setups  = [v for v in views_sorted if v.bucket == "strong_setup"]
    single_scanner = [v for v in views_sorted if v.bucket == "single_scanner"]
    extended       = [v for v in views_sorted if v.bucket == "extended"]

    lines = [
        sep,
        "  MASTER RADAR  v3.0  —  cross-scanner confluence",
        f"  Generated  : {ts}    |    Orchestration time: {elapsed_s:.1f}s",
        sep,
        "",
    ]

    # ── Warnings (stale data, etc.) ──────────────────────────────────────────
    if warnings:
        lines.append("  ⚠️  WARNINGS:")
        for w in warnings:
            lines.append(f"     - {w}")
        lines.append("")

    # ── Market context ───────────────────────────────────────────────────────
    regime_label = {
        "bull":     "🟢 BULL",
        "sideways": "🟡 SIDEWAYS",
        "bear":     "🔴 BEAR",
        "unknown":  "❓ UNKNOWN",
    }.get(context.get("regime", "unknown"), "❓ UNKNOWN")

    btc_7d  = context.get("btc_7d_pct")
    btc_24h = context.get("btc_24h_pct")
    btc_str = ""
    if btc_7d is not None:
        btc_str += f"BTC 7d {btc_7d:+.2f}%"
    if btc_24h is not None:
        btc_str += f"  |  24h {btc_24h:+.2f}%" if btc_str else f"BTC 24h {btc_24h:+.2f}%"

    lines.append(f"  Regime    : {regime_label}    {btc_str}")

    # Spot 'stay out' banner — and trade plans below get hidden
    if context.get("spot_says_stay_out"):
        lines += [
            "",
            "  ┌──────────────────────────────────────────────────────────────────┐",
            "  │  🚫  REGIME LOCK — NO ENTRIES                                      │",
            "  │     Spot scanner: STAY IN USDT (0 setups passed filter)          │",
            "  │     Picks below shown for WATCHLIST only.                        │",
            "  │     Trade plans hidden until regime clears.                      │",
            "  └──────────────────────────────────────────────────────────────────┘",
        ]
    elif context.get("spot_qualified_count", 0) > 0:
        lines.append(f"             Spot scanner: {context['spot_qualified_count']} qualified setup(s)")

    lines += [
        "",
        f"  Universe  : {len(views_sorted)} coins surfaced across scanners",
        f"  Buckets   : {len(convergence)} CONVERGENCE  |  "
        f"{len(strong_setups)} STRONG SETUP  |  "
        f"{len(single_scanner)} SINGLE-SCANNER  |  "
        f"{len(extended)} EXTENDED (24h>{EXTENDED_THRESHOLD_24H_PCT:.0f}%)",
        "",
        "  Scanner badges: I=Ignition  P=Perp  S=Spot  T=Trend",
        "                   ★★=top tier  ★=lower tier  ·=watch (trend only)",
        "",
    ]

    # ── CONVERGENCE — full detail ───────────────────────────────────────────
    lines.append(dash)
    lines.append(f"  CONVERGENCE  —  {len(convergence)} coin(s)  "
                 "(≥3 scanners agreeing, confluence ≥10)")
    lines.append(dash)
    if not convergence:
        lines.append("  (none — no high-confluence agreement across scanners today)")
    else:
        for i, v in enumerate(convergence, 1):
            _render_full_view(lines, i, v,
                              prev_conv=prev_convs.get(v.base),
                              regime_locked=regime_locked)

    # ── STRONG SETUP — full detail ──────────────────────────────────────────
    lines.append("")
    lines.append(dash)
    lines.append(f"  STRONG SETUP  —  {len(strong_setups)} coin(s)  "
                 "(≥2 scanners, confluence ≥6)")
    lines.append(dash)
    if not strong_setups:
        lines.append("  (none)")
    else:
        for i, v in enumerate(strong_setups, 1):
            _render_full_view(lines, i, v,
                              prev_conv=prev_convs.get(v.base),
                              regime_locked=regime_locked)

    # ── SINGLE SCANNER — table ──────────────────────────────────────────────
    lines.append("")
    lines.append(dash)
    lines.append(f"  SINGLE-SCANNER  —  {len(single_scanner)} coin(s)  "
                 "(only one scanner surfaced — early but unconfirmed)")
    lines.append(dash)
    lines.append(
        f"  {'#':<3} {'Symbol':<10} {'Conf':>5} {'Δ':<2} {'IGN':<3} {'PRP':<3} {'SPT':<3} {'TRD':<3}  "
        f"{'Price':>12} {'24h%':>7}  Signals snapshot"
    )
    lines.append("  " + "-" * 80)
    for i, v in enumerate(single_scanner[:30], 1):     # cap at 30
        ign  = _tier_badge("ignition", v.ignition_tier).strip() or "—"
        prp  = _tier_badge("perp",     v.perp_tier).strip()     or "—"
        spt  = _tier_badge("spot",     v.spot_tier).strip()     or "—"
        trd  = _tier_badge("trend",    v.trend_tier).strip()    or "—"
        price_str = f"${v.price:>10.6f}" if v.price else "          —"
        chg_str   = f"{v.price_24h_pct:>+6.2f}%" if v.price_24h_pct is not None else "      —"
        arrow     = _trajectory_arrow(v.confluence, prev_convs.get(v.base))
        # Compact signal text (just first scanner that fired)
        snip = ""
        if v.ignition_signals:   snip = "I:" + ",".join(v.ignition_signals[:2])
        elif v.perp_signals:     snip = "P:" + ",".join(v.perp_signals[:2])
        elif v.spot_signals:     snip = "S:" + ",".join(v.spot_signals[:2])
        elif v.trend_score:      snip = f"T:score={v.trend_score:.0f}"
        lines.append(
            f"  {i:>3} {v.base:<10} {v.confluence:>5.1f} {arrow:<2} {ign:<3} {prp:<3} {spt:<3} {trd:<3}  "
            f"{price_str:>12} {chg_str:>7}  {snip}"
        )
    if len(single_scanner) > 30:
        lines.append(f"  ... ({len(single_scanner)-30} more, see JSON)")

    # ── EXTENDED — coins already up >threshold% in 24h ──────────────────────
    # These are sorted by 24h move (biggest movers first) so you can see the
    # most-extended coins at the top. Their confluence info is preserved so
    # you can judge whether they're worth chasing despite the late entry.
    extended_sorted = sorted(extended, key=lambda v: -(v.price_24h_pct or 0))
    lines.append("")
    lines.append(dash)
    lines.append(
        f"  EXTENDED  —  {len(extended_sorted)} coin(s)  "
        f"(already up >{EXTENDED_THRESHOLD_24H_PCT:.0f}% in 24h — entry likely late)"
    )
    lines.append(dash)
    if not extended_sorted:
        lines.append("  (none — no coins above 24h move threshold)")
    else:
        lines.append(
            f"  {'#':<3} {'Symbol':<10} {'Conf':>5} {'Δ':<2} {'IGN':<3} {'PRP':<3} {'SPT':<3} {'TRD':<3}  "
            f"{'Price':>12} {'24h%':>7}  Original bucket / Signals"
        )
        lines.append("  " + "-" * 80)
        for i, v in enumerate(extended_sorted[:30], 1):
            ign  = _tier_badge("ignition", v.ignition_tier).strip() or "—"
            prp  = _tier_badge("perp",     v.perp_tier).strip()     or "—"
            spt  = _tier_badge("spot",     v.spot_tier).strip()     or "—"
            trd  = _tier_badge("trend",    v.trend_tier).strip()    or "—"
            price_str = f"${v.price:>10.6f}" if v.price else "          —"
            chg_str   = f"{v.price_24h_pct:>+6.2f}%" if v.price_24h_pct is not None else "      —"
            arrow     = _trajectory_arrow(v.confluence, prev_convs.get(v.base))
            # Tag with what bucket they WOULD have been in
            would_be = _what_bucket_without_extended(v.confluence, v.scanner_count)
            snip = f"would-be: {would_be.upper()}"
            lines.append(
                f"  {i:>3} {v.base:<10} {v.confluence:>5.1f} {arrow:<2} {ign:<3} {prp:<3} {spt:<3} {trd:<3}  "
                f"{price_str:>12} {chg_str:>7}  {snip}"
            )
        if len(extended_sorted) > 30:
            lines.append(f"  ... ({len(extended_sorted)-30} more, see JSON)")

    lines += ["", sep]
    return "\n".join(lines)


def _what_bucket_without_extended(score: float, scanner_count: int) -> str:
    """Helper: what bucket would this coin be in if we ignored the extended filter?"""
    if (score >= CONFLUENCE_BUCKETS["convergence"]["min_score"]
            and scanner_count >= CONFLUENCE_BUCKETS["convergence"]["min_scanners"]):
        return "convergence"
    if (score >= CONFLUENCE_BUCKETS["strong_setup"]["min_score"]
            and scanner_count >= CONFLUENCE_BUCKETS["strong_setup"]["min_scanners"]):
        return "strong_setup"
    if score > 0:
        return "single_scanner"
    return "below"


def _render_full_view(
    lines:         list[str],
    idx:           int,
    v:             CoinView,
    *,
    prev_conv:     Optional[float] = None,
    regime_locked: bool             = False,
) -> None:
    """
    Append a rich-format entry for CONVERGENCE/STRONG SETUP coin.

    prev_conv:     conviction from the previous scan, used for trajectory arrow
    regime_locked: when True (spot scanner says stay out), trade plan is hidden
    """
    badges = "  ".join([
        _tier_badge("ignition", v.ignition_tier),
        _tier_badge("perp",     v.perp_tier),
        _tier_badge("spot",     v.spot_tier),
        _tier_badge("trend",    v.trend_tier),
    ])
    price_str = f"${v.price:,.6f}" if v.price is not None else "—"
    chg_str   = f"{v.price_24h_pct:+.2f}%" if v.price_24h_pct is not None else "—"
    vol_str   = f"${(v.volume_24h or 0)/1e6:.1f}M"
    arrow     = _trajectory_arrow(v.confluence, prev_conv)

    lines.append("")
    lines.append(
        f"  [{idx:>2}] {v.base:<10}  conf={v.confluence:>5.1f} {arrow} "
        f"scanners={v.scanner_count}/4   "
        f"{badges}    {price_str}  24h={chg_str}  vol={vol_str}"
    )
    lines.append(f"       → {_signals_summary(v)}")

    # Trade plan rendering — gated by regime_locked
    if v.trade_plan and isinstance(v.trade_plan, dict):
        if regime_locked:
            # Show that a plan exists but suppress the actionable numbers
            lines.append(
                "       [WATCH ONLY] — trade plan suppressed by regime filter "
                "(spot says STAY IN USDT)"
            )
        else:
            tp = v.trade_plan
            src = v.trade_plan_source or "?"
            entry = tp.get("entry")
            stop  = tp.get("stop")
            stop_pct = tp.get("stop_pct")
            tps   = tp.get("take_profits") or []
            if entry is not None and stop is not None:
                stop_pct_str = f"{stop_pct:+.2f}%" if stop_pct is not None else ""
                tp_strs = [f"${t.get('price', 0):.6f}" for t in tps[:3]]
                tp_joined = ", ".join(tp_strs)
                lines.append(
                    f"       Plan ({src}): entry ${entry:.6f}  |  "
                    f"stop ${stop:.6f} {stop_pct_str}  |  "
                    f"TPs: {tp_joined}"
                )


def build_json_payload(
    views_sorted: list[CoinView],
    context:      dict,
    warnings:     list[str],
    elapsed_s:    float,
) -> dict:
    """Machine-readable orchestrator output."""
    return {
        "scanner":       "master_radar",
        "version":       "3.0",
        "generated_at":  datetime.now().isoformat(),
        "elapsed_s":     round(elapsed_s, 2),
        "warnings":      warnings,
        "context":       context,
        "tier_points":   TIER_POINTS,
        "buckets_config": CONFLUENCE_BUCKETS,
        "extended_threshold_24h_pct": EXTENDED_THRESHOLD_24H_PCT,
        "convergence":     [v.to_dict() for v in views_sorted if v.bucket == "convergence"],
        "strong_setup":    [v.to_dict() for v in views_sorted if v.bucket == "strong_setup"],
        "single_scanner":  [v.to_dict() for v in views_sorted if v.bucket == "single_scanner"],
        "extended":        [v.to_dict() for v in views_sorted if v.bucket == "extended"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# OPTIONAL: RUN THE SCANNERS BEFORE ORCHESTRATING
# ─────────────────────────────────────────────────────────────────────────────

def run_scanner_subprocess(
    label:    str,
    script:   Path,
    args:     list[str] = None,
    timeout_s: int = 1800,
) -> dict:
    """
    Run a scanner script as a subprocess.

    Streams stdout/stderr DIRECTLY to the parent terminal so you can watch
    progress live for slow scanners (especially trend_scanner). Each subprocess
    also has its own internal logger that writes to outputs/logs/, so detailed
    scanner-specific records persist independently.

    Returns a dict: {"ok": bool, "returncode": int, "elapsed_s": float, "label": str}
    """
    result = {"ok": False, "returncode": -1, "elapsed_s": 0.0, "label": label}

    if not script.exists():
        log.warning(f"  [SKIP] {label}: script not found at {script}")
        return result

    cmd = [sys.executable, str(script)] + (args or [])
    log.info(f"  Running {label} ...  (output streams below)")
    log.info(f"  {'─' * 60}")

    start = time.time()
    try:
        # capture_output=False / no stdout=PIPE → output flows directly to our terminal
        # This is critical: we want to see scanner progress live, especially for
        # trend_scanner which can take 5-15 minutes.
        proc = subprocess.run(
            cmd,
            cwd=str(_THIS_DIR),
            timeout=timeout_s,
            check=False,        # don't raise on non-zero; we handle it
        )
        elapsed = time.time() - start
        result["returncode"] = proc.returncode
        result["elapsed_s"]  = round(elapsed, 1)
        log.info(f"  {'─' * 60}")

        if proc.returncode != 0:
            log.error(f"  [FAIL] {label} exited code {proc.returncode}  ({elapsed:.1f}s)")
            log.error(f"         Check the scanner's own log file in outputs/logs/ for details")
            return result

        log.info(f"  [OK]   {label} finished in {elapsed:.1f}s")
        result["ok"] = True
        return result

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        log.error(f"  [TIMEOUT] {label} exceeded {timeout_s}s ({elapsed:.1f}s elapsed)")
        result["elapsed_s"] = round(elapsed, 1)
        return result
    except KeyboardInterrupt:
        log.warning(f"  [INTERRUPT] {label} cancelled by user")
        raise
    except Exception as e:
        log.error(f"  [ERROR] {label}: {type(e).__name__}: {e}")
        return result


def run_all_scanners(
    skip:          set[str],
    account_size:  float,
    include_spot:  bool = False,
) -> dict[str, dict]:
    """
    Run scanners in sequence (or skip per --skip flag).

    Note: spot_scanner is NOT auto-run by default because it takes 40-50 min
    (CG top-800 universe, sequential scoring). User runs it manually on their
    own schedule. Pass include_spot=True to force it (e.g., overnight runs).

    Returns a dict mapping scanner-name → run-result so the orchestrator
    can later distinguish "scanner ran fine but 0 setups" from "scanner failed".
    """
    log.info("Phase 1/2: running scanners")
    log.info("=" * 64)
    if not include_spot:
        log.info("  (spot_scanner not auto-run — pass --include-spot to enable)")
    log.info("=" * 64)
    results: dict[str, dict] = {}

    if "ignition" not in skip:
        results["ignition"] = run_scanner_subprocess("ignition_scanner", _IGNITION_PY)
    if "perp" not in skip:
        results["perp"]     = run_scanner_subprocess("perp_scanner",     _PERP_PY)
    if include_spot and "spot" not in skip:
        # Spot needs a much longer timeout — measured 40-50 min in real runs
        results["spot"]     = run_scanner_subprocess(
            "spot_scanner", _SPOT_PY,
            ["--account", str(int(account_size))],
            timeout_s=4500,   # 75 min ceiling
        )
    if "trend" not in skip:
        results["trend"]    = run_scanner_subprocess("trend_scanner",    _TREND_PY,
                                                    ["--account", str(int(account_size))])

    # Summary line
    log.info("=" * 64)
    log.info("Scanner subprocess summary:")
    for name, r in results.items():
        status = "✓ OK" if r["ok"] else f"✗ FAIL (rc={r['returncode']})"
        log.info(f"  {name:<10} {status:<20} elapsed={r['elapsed_s']}s")
    log.info("=" * 64)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run(
    no_rerun:     bool        = False,
    account_size: float       = 100_000.0,
    skip:         set[str]    = None,
    include_spot: bool        = False,
) -> dict:
    skip = skip or set()
    orchestrate_start = time.time()

    log.info("=" * 64)
    log.info("MASTER RADAR v3.0  (Phase 5 orchestrator)")
    log.info("=" * 64)

    # Step 1: optionally run scanners
    subprocess_results: dict[str, dict] = {}
    if not no_rerun:
        subprocess_results = run_all_scanners(skip, account_size, include_spot=include_spot)

    # Step 2: load each scanner's JSON output
    log.info("Phase 2/2: orchestrating")
    log.info("=" * 64)
    warnings: list[str] = []

    # Helper: combine "did the subprocess succeed" with "is the JSON fresh"
    # Three cases:
    #   1. We ran the subprocess and it failed     → loud failure warning
    #   2. We didn't run the subprocess (skipped)  → freshness warning if stale
    #   3. We ran the subprocess and it succeeded  → no warning
    def _scanner_warning(name: str, sub_warning: Optional[str]) -> Optional[str]:
        sub = subprocess_results.get(name)
        if sub and not sub.get("ok"):
            return (f"{name}: subprocess failed (rc={sub['returncode']}, "
                    f"{sub['elapsed_s']}s) — using stale or missing JSON")
        # Special case: spot wasn't auto-run and its JSON is stale → explain
        if name == "spot" and not no_rerun and not include_spot and sub_warning:
            return (f"{name}: not auto-run by orchestrator. {sub_warning}. "
                    "Run `python spot_scanner.py` manually to refresh.")
        return sub_warning

    data_ign,   w_ign   = _load_json(_IGNITION_JSON, "ignition_scanner")
    data_perp,  w_perp  = _load_json(_PERP_JSON,     "perp_scanner")
    data_spot,  w_spot  = _load_json(_SPOT_JSON,     "spot_scanner")
    data_trend, w_trend = _load_json(_TREND_JSON,    "trend_scanner")

    for name, w in (
        ("ignition", _scanner_warning("ignition", w_ign)),
        ("perp",     _scanner_warning("perp",     w_perp)),
        ("spot",     _scanner_warning("spot",     w_spot)),
        ("trend",    _scanner_warning("trend",    w_trend)),
    ):
        if w:
            warnings.append(w)

    log.info(f"  Loaded: ignition={'✓' if data_ign else '✗'}  "
             f"perp={'✓' if data_perp else '✗'}  "
             f"spot={'✓' if data_spot else '✗'}  "
             f"trend={'✓' if data_trend else '✗'}")

    # Step 3: parse each into uniform symbol→tier dicts
    ignition = parse_ignition(data_ign)
    perp     = parse_perp(data_perp)
    spot     = parse_spot(data_spot)
    trend    = parse_trend(data_trend)
    log.info(f"  Coins per scanner: ignition={len(ignition)}  perp={len(perp)}  "
             f"spot={len(spot)}  trend={len(trend)}")

    # Step 4: market context (regime, BTC moves, spot stay-out flag)
    context = get_market_context(data_trend, data_spot)
    log.info(f"  Regime: {context.get('regime', 'unknown')}  "
             f"BTC 7d: {context.get('btc_7d_pct')}  "
             f"spot stay-out: {context.get('spot_says_stay_out')}")

    # Step 5: build CoinView objects with confluence
    views = build_coin_views(ignition, perp, spot, trend)

    # Step 6: dormant TPI gate + RSPS rank (no-op for now)
    views = apply_tpi_gate(views,  context)
    views = apply_rsps_rank(views, context)

    # Step 7: sort by confluence
    views_sorted = sorted(views.values(), key=lambda v: (v.confluence, v.scanner_count), reverse=True)

    elapsed_s = time.time() - orchestrate_start

    # Step 8: load previous scan's convictions (for trajectory arrows) BEFORE we overwrite the file
    json_latest = _OUTPUT_DIR / "master_radar_LATEST.json"
    prev_convictions = _load_previous_convictions(json_latest)
    log.info(f"  Trajectory: {len(prev_convictions)} prev coins loaded for ↗/↘/→ arrows")

    # Step 9: build report + write outputs
    report_text = build_text_report(views_sorted, context, warnings, elapsed_s,
                                    prev_convs=prev_convictions)
    log.info("\n" + report_text)

    payload = build_json_payload(views_sorted, context, warnings, elapsed_s)

    ts_file     = datetime.now().strftime("%Y%m%d_%H%M%S")
    txt_ts      = _OUTPUT_DIR / f"master_radar_{ts_file}.txt"
    txt_latest  = _OUTPUT_DIR / "master_radar_LATEST.txt"

    txt_ts.write_text(report_text, encoding="utf-8")
    txt_latest.write_text(report_text, encoding="utf-8")
    json_latest.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    log.info(f"  Saved → {txt_latest.name}, {json_latest.name}, {txt_ts.name}")
    log.info("=" * 64)
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Master Radar v3.0 — cross-scanner orchestrator")
    parser.add_argument("--no-rerun", action="store_true",
                        help="Skip running scanners; only orchestrate from existing JSON outputs")
    parser.add_argument("--account",  type=float, default=100_000.0,
                        help="Account size in USDT (passed to scanners that need it)")
    parser.add_argument("--skip",     type=str,   default="",
                        help="Comma-separated scanners to skip (ignition,perp,spot,trend)")
    parser.add_argument("--include-spot", action="store_true",
                        help="Auto-run spot_scanner (slow: 40-50 min). "
                             "By default spot is read from its existing JSON only.")
    args = parser.parse_args()

    skip_set = {s.strip() for s in args.skip.split(",") if s.strip()}
    run(
        no_rerun     = args.no_rerun,
        account_size = args.account,
        skip         = skip_set,
        include_spot = args.include_spot,
    )
