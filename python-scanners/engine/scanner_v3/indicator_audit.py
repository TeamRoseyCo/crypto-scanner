"""
================================================================================
INDICATOR AUDIT  v1.0
================================================================================
Walks indicators.py and signals.py to report:
  - Every compute_* / find_* function defined in indicators.py
  - Which signal functions in signals.py import each one
  - Which indicators are dormant (defined but no signal calls them)
  - Which indicators are over-used (high coupling — refactoring risk)

Read-only. Doesn't modify either file. Useful for:
  - Spotting dead code before cleanup
  - Identifying easy wins ("indicator X is already implemented — what signal
    could use it?")
  - Documenting the actual indicator-to-signal dependency graph

Run from anywhere in the project tree (auto-locates the files):
    python indicator_audit.py
    python indicator_audit.py --verbose   # show which signal uses each indicator
    python indicator_audit.py --dormant   # only list unused indicators
================================================================================
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# LOCATE FILES
# ─────────────────────────────────────────────────────────────────────────────

def _locate_files() -> tuple[Path, Path]:
    """Find indicators.py and signals.py relative to this script's location.
    Assumes the standard scanner_v3 layout."""
    this_dir = Path(__file__).resolve().parent
    # Walk up looking for scanner_v3 directory
    for candidate in [this_dir, *this_dir.parents]:
        v3 = candidate / "scanner_v3"
        if v3.is_dir() and (v3 / "indicators.py").exists():
            return v3 / "indicators.py", v3 / "signals.py"
        # Maybe we're already in scanner_v3
        if (candidate / "indicators.py").exists() and (candidate / "signals.py").exists():
            return candidate / "indicators.py", candidate / "signals.py"
    # Last resort: hunt for them anywhere under the project root
    project_root = this_dir.parent.parent.parent
    indicators_hits = list(project_root.rglob("scanner_v3/indicators.py"))
    signals_hits = list(project_root.rglob("scanner_v3/signals.py"))
    if indicators_hits and signals_hits:
        return indicators_hits[0], signals_hits[0]
    raise FileNotFoundError("Could not locate indicators.py and signals.py")


# ─────────────────────────────────────────────────────────────────────────────
# PARSE INDICATORS.PY
# ─────────────────────────────────────────────────────────────────────────────

def parse_indicators(path: Path) -> dict[str, dict]:
    """Returns { function_name: {'kind': 'indicator'|'helper', 'lineno': N} }.
    Distinguishes indicators (compute_*) from helpers (find_*, math utilities)."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    funcs: dict[str, dict] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.col_offset != 0:        # skip nested functions
            continue
        name = node.name
        if name.startswith("_"):        # skip private helpers
            continue

        if name.startswith("compute_") and not name.endswith("_series"):
            kind = "indicator"
        elif name.startswith("compute_") and name.endswith("_series"):
            kind = "helper"             # _series variants are math helpers
        elif name == "find_pivots":
            kind = "helper"
        elif name in {"compute_slope"}:
            kind = "helper"
        else:
            kind = "other"

        funcs[name] = {"kind": kind, "lineno": node.lineno}

    return funcs


# ─────────────────────────────────────────────────────────────────────────────
# PARSE SIGNALS.PY  —  who imports/calls what?
# ─────────────────────────────────────────────────────────────────────────────

def parse_signal_usage(path: Path, indicator_names: set[str]) -> dict[str, list[str]]:
    """Returns { indicator_name: [list of sig_* functions that use it] }.
    A signal 'uses' an indicator if it calls the function by name anywhere
    in its body."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    usage: dict[str, list[str]] = defaultdict(list)

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.col_offset != 0:
            continue
        if not node.name.startswith("sig_"):
            continue

        # Collect every Name node referenced inside this function's body
        called: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                fn = child.func
                if isinstance(fn, ast.Name):
                    called.add(fn.id)
                elif isinstance(fn, ast.Attribute):
                    # e.g. np.something — not interesting here
                    pass
            # Also catch bare references (e.g. inside list comps)
            if isinstance(child, ast.Name):
                if child.id in indicator_names:
                    called.add(child.id)

        for ind in called & indicator_names:
            usage[ind].append(node.name)

    # Dedupe and sort within each indicator
    return {k: sorted(set(v)) for k, v in usage.items()}


# ─────────────────────────────────────────────────────────────────────────────
# SCANNER-LEVEL USAGE — which scanners ultimately call each signal?
# ─────────────────────────────────────────────────────────────────────────────

SCANNER_FILES = ("ignition_scanner.py", "perp_scanner.py",
                 "trend_scanner.py", "short_scanner.py")


def parse_scanner_usage(scanner_dir: Path, signal_names: set[str]) -> dict[str, set[str]]:
    """Returns { sig_name: {scanner1, scanner2, ...} } showing which scanners
    invoke each signal. A scanner 'invokes' a signal if it references S.sig_*
    or sig_* by name."""
    usage: dict[str, set[str]] = defaultdict(set)
    for fname in SCANNER_FILES:
        path = scanner_dir / fname
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8")
        for sig in signal_names:
            # Match S.sig_foo or just sig_foo with a word boundary check
            if f".{sig}" in src or f" {sig}(" in src or f"\t{sig}(" in src:
                usage[sig].add(fname.replace("_scanner.py", ""))
    return usage


def parse_scanner_direct_indicator_use(
    scanner_dir: Path,
    indicator_names: set[str],
) -> dict[str, set[str]]:
    """Some scanners (notably trend_scanner.py) call compute_* directly
    instead of going through a sig_* wrapper. This catches that.
    Returns { indicator_name: {scanner_name, ...} }."""
    usage: dict[str, set[str]] = defaultdict(set)
    for fname in SCANNER_FILES:
        path = scanner_dir / fname
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8")
        for ind in indicator_names:
            # Look for a function call: `ind(` somewhere in the source
            # (covers `compute_foo(` and `module.compute_foo(`)
            if f"{ind}(" in src:
                usage[ind].add(fname.replace("_scanner.py", ""))
    return usage


# ─────────────────────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────────────────────

def build_report(
    indicators:    dict[str, dict],
    indicator_use: dict[str, list[str]],
    signal_use:    dict[str, set[str]],
    direct_use:    dict[str, set[str]],
    show_dormant_only: bool = False,
    verbose:           bool = False,
) -> str:
    sep  = "=" * 80
    dash = "-" * 80
    lines: list[str] = []

    real_indicators = {n: v for n, v in indicators.items() if v["kind"] == "indicator"}
    helpers         = {n: v for n, v in indicators.items() if v["kind"] == "helper"}

    # An indicator is "active" if called by ANY signal OR called directly
    # by ANY scanner. The audit used to only see signal-mediated calls.
    used_indicators = set()
    for n in real_indicators:
        if indicator_use.get(n):                # called via a sig_* function
            used_indicators.add(n)
        elif direct_use.get(n):                 # called directly by a scanner
            used_indicators.add(n)
    dormant = sorted(set(real_indicators) - used_indicators)
    active  = sorted(used_indicators)

    # ── Header ──────────────────────────────────────────────────────────────
    lines.append(sep)
    lines.append("  INDICATOR AUDIT")
    lines.append(sep)
    lines.append(f"  indicators.py: {len(real_indicators)} indicator(s) + "
                 f"{len(helpers)} helper(s)")
    lines.append(f"  Active:        {len(active)} indicator(s) called by at least one signal")
    lines.append(f"  Dormant:       {len(dormant)} indicator(s) defined but never called")
    lines.append("")

    if show_dormant_only:
        lines.append(dash)
        lines.append("  DORMANT INDICATORS (defined but unused)")
        lines.append(dash)
        if not dormant:
            lines.append("  None — every indicator is referenced by at least one signal.")
        else:
            for name in dormant:
                lines.append(f"    {name}  (line {real_indicators[name]['lineno']})")
        lines.append("")
        lines.append(sep)
        return "\n".join(lines)

    # ── Active table ────────────────────────────────────────────────────────
    lines.append(dash)
    lines.append("  ACTIVE INDICATORS — used by v3 signals")
    lines.append(dash)
    lines.append(f"  {'Indicator':<28} {'#Sigs':>6}  {'Scanners':<32}  Signals")
    lines.append("  " + "-" * 78)

    for ind in active:
        sigs = indicator_use.get(ind, [])
        scanners = set()
        for sig in sigs:
            scanners |= signal_use.get(sig, set())
        # Also pick up direct-call scanners
        scanners |= direct_use.get(ind, set())

        sig_count = len(sigs)
        scanner_str = ", ".join(sorted(scanners)) if scanners else "(none directly)"

        # If indicator has no signal wrappers but IS called directly, mark it
        if not sigs and direct_use.get(ind):
            sig_preview = "(direct call from scanner)"
        else:
            sig_preview = ", ".join(sigs[:3])
            if len(sigs) > 3:
                sig_preview += f", +{len(sigs)-3}"
        lines.append(f"  {ind:<28} {sig_count:>6}  {scanner_str:<32}  {sig_preview}")

        if verbose:
            for sig in sigs:
                sig_scanners = sorted(signal_use.get(sig, set()))
                lines.append(f"      └─ {sig:<32} → {', '.join(sig_scanners) or '(unused)'}")
            for direct_scanner in sorted(direct_use.get(ind, set())):
                lines.append(f"      └─ (direct call from {direct_scanner})")

    # ── Dormant table ───────────────────────────────────────────────────────
    lines.append("")
    lines.append(dash)
    lines.append("  DORMANT INDICATORS — defined but never called by a signal")
    lines.append(dash)
    if not dormant:
        lines.append("  None — every indicator is in use.")
    else:
        lines.append("  These are easy wins if you ever want new signals:")
        lines.append("  the math is already done — you'd just write a sig_* function")
        lines.append("  that calls them.")
        lines.append("")
        for name in dormant:
            lines.append(f"    {name:<28}  (line {real_indicators[name]['lineno']})")

    # ── Helpers ─────────────────────────────────────────────────────────────
    lines.append("")
    lines.append(dash)
    lines.append("  HELPERS (not indicators — math/pivot utilities)")
    lines.append(dash)
    for name, info in sorted(helpers.items()):
        used = name in indicator_use and len(indicator_use[name]) > 0
        marker = "✓" if used else " "
        lines.append(f"    [{marker}] {name:<28}  (line {info['lineno']})")

    # ── Cross-check: are all sig_* functions actually invoked? ─────────────
    lines.append("")
    lines.append(dash)
    lines.append("  ORPHAN SIGNALS — defined in signals.py but no scanner calls them")
    lines.append(dash)
    orphans = sorted(s for s in signal_use.keys() if not signal_use[s])
    # Also list signals that exist but aren't even in our signal_use map at all
    # (signal_use was built from scanner files; missing keys = orphan candidates)
    if not orphans:
        lines.append("  None — every signal is wired into at least one scanner.")
    else:
        for s in orphans:
            lines.append(f"    {s}")

    lines.append("")
    lines.append(sep)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Indicator usage audit")
    parser.add_argument("--verbose", action="store_true",
                        help="Show signal-by-signal breakdown")
    parser.add_argument("--dormant", action="store_true",
                        help="Show only dormant indicators")
    args = parser.parse_args()

    try:
        ind_path, sig_path = _locate_files()
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("Expected to find scanner_v3/indicators.py and signals.py", file=sys.stderr)
        return 1

    print(f"Reading: {ind_path}")
    print(f"Reading: {sig_path}")
    print()

    indicators = parse_indicators(ind_path)
    indicator_names = set(indicators)

    # Parse signal usage of indicators
    indicator_use = parse_signal_usage(sig_path, indicator_names)

    # Get the list of all sig_* functions defined in signals.py
    sig_src = sig_path.read_text(encoding="utf-8")
    sig_tree = ast.parse(sig_src)
    signal_names: set[str] = set()
    for node in ast.walk(sig_tree):
        if (isinstance(node, ast.FunctionDef)
                and node.col_offset == 0
                and node.name.startswith("sig_")):
            signal_names.add(node.name)

    # Parse scanner-level usage of signals
    signal_use = parse_scanner_usage(sig_path.parent, signal_names)
    # Ensure every signal has an entry (even if empty) so we can detect orphans
    for s in signal_names:
        signal_use.setdefault(s, set())

    # Parse direct compute_* calls from scanners (trend_scanner.py does this)
    direct_use = parse_scanner_direct_indicator_use(sig_path.parent, indicator_names)

    report = build_report(
        indicators, indicator_use, signal_use, direct_use,
        show_dormant_only=args.dormant,
        verbose=args.verbose,
    )
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
