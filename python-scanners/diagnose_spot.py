"""
================================================================================
Diagnostic: run spot_scanner the same way the orchestrator does, but with all
output visible so we can see what actually happens.
================================================================================

Layout assumed:
    crypto-scanner/
        python-scanners/
            diagnose_spot.py        ← THIS FILE
            engine/
                spot_scanner.py     ← target
                master_orchestrator.py
                alpha_scanner.py
                ...
"""
from __future__ import annotations
import sys
import subprocess
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────────────────
# This file lives in python-scanners/, NOT in engine/.
# The scanners themselves live in python-scanners/engine/.
_THIS_DIR     = Path(__file__).resolve().parent          # python-scanners/
_ENGINE_DIR   = _THIS_DIR / "engine"                     # python-scanners/engine/
_PYTHON_DIR   = _THIS_DIR                                # python-scanners/
_PROJECT_ROOT = _PYTHON_DIR.parent                       # crypto-scanner/
_SPOT_PY      = _ENGINE_DIR / "spot_scanner.py"

# ─────────────────────────────────────────────────────────────────────────────
# DIAGNOSTIC HEADER
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 70)
print("DIAGNOSTIC — spot_scanner subprocess test")
print("=" * 70)
print(f"  Looking for spot_scanner.py at: {_SPOT_PY}")
print(f"  Exists?                         {_SPOT_PY.exists()}")
print(f"  CWD for subprocess:             {_PROJECT_ROOT}")
print(f"  Python interpreter:             {sys.executable}")
print()

# ─────────────────────────────────────────────────────────────────────────────
# SANITY CHECK — if path is wrong, hunt for spot_scanner.py
# ─────────────────────────────────────────────────────────────────────────────
if not _SPOT_PY.exists():
    print("✗ spot_scanner.py not found at expected path")
    print("  Searching for spot_scanner.py in nearby directories...")
    # Search up to 3 levels up from this file
    search_root = _PROJECT_ROOT
    found_paths = list(search_root.rglob("spot_scanner.py"))
    if found_paths:
        # dedupe (rglob can return the same resolved path multiple times via symlinks)
        unique = sorted({p.resolve() for p in found_paths})
        for p in unique:
            print(f"    found at: {p}")
        print()
        print("  → Update _ENGINE_DIR in this script to match the real location.")
    else:
        print("    No spot_scanner.py found anywhere under the project root.")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# RUN THE SCANNER
# ─────────────────────────────────────────────────────────────────────────────
print("─" * 70)
print("Launching spot_scanner.py as subprocess...")
print("─" * 70)
print()

cmd = [sys.executable, "-u", str(_SPOT_PY)]   # -u = unbuffered, so we see output live
print(f"  Command: {' '.join(cmd)}")
print(f"  CWD:     {_PROJECT_ROOT}")
print()
print("─" * 70)
print("BEGIN SUBPROCESS OUTPUT")
print("─" * 70)

try:
    result = subprocess.run(
        cmd,
        cwd=str(_PROJECT_ROOT),
        # Don't capture — let stdout/stderr stream live to this terminal
        check=False,
    )
except FileNotFoundError as e:
    print(f"\n✗ Could not launch Python: {e}")
    sys.exit(2)
except KeyboardInterrupt:
    print("\n\n⚠ Interrupted by user")
    sys.exit(130)

print("─" * 70)
print("END SUBPROCESS OUTPUT")
print("─" * 70)
print()
print(f"Subprocess exit code: {result.returncode}")
if result.returncode == 0:
    print("✓ spot_scanner.py completed cleanly")
else:
    print(f"✗ spot_scanner.py exited with code {result.returncode}")
    print("  Scroll up to see the last lines of output before the failure.")
sys.exit(result.returncode)
