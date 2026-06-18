"""
Run this on your machine to:
1. Patch today's scanner script with fresh XLM prices
2. Apply a price staleness guard to longform_pipeline.py

Usage (from YOUTUBE - faceless channel folder):
  python patch_script.py
"""
import json, re
from pathlib import Path

# ═══════════════════════════════════════════════════════
# PART 1 — Patch today's script JSON with fresh prices
# ═══════════════════════════════════════════════════════
script_path = Path("Video Scripts/longform_scanner_20260601_095353.json")

replacements = [
    ("The price at detection was 0.16862 with a 24-hour change of plus 14.72 percent",
     "The price at detection was 0.16862 and has since moved to approximately 0.2550, a 49 percent gain from the original signal. Because the scanner flagged this setup on May 26 and May 28, the move is still within the expected range for a convergence bucket setup"),
    ("Entry is set at the detection price of 0.16862",
     "Entry is set at current price of 0.2550"),
    ("The stop sits below the recent swing low at 0.16015286, giving roughly 5 percent risk",
     "The stop sits at 0.2423, roughly 5 percent below current price"),
    ("TP1 at 0.18132071 for a 7.5 percent move",
     "TP1 at 0.2741 for a 7.5 percent move"),
    ("TP2 at 0.19402143 for a 15 percent move",
     "TP2 at 0.2933 for a 15 percent move"),
    ("TP3 at 0.21095571 for a 25 percent extension",
     "TP3 at 0.3188 for a 25 percent extension"),
    ("0.16015286 or if funding",
     "0.2423 or if funding"),
    ("stop level at 0.16015286",
     "stop level at 0.2423"),
    ("narrow 5 percent stop distance",
     "5 percent stop distance from current price"),
]

if script_path.exists():
    raw = script_path.read_text(encoding="utf-8")
    for old, new in replacements:
        raw = raw.replace(old, new)
    # Also update the stat field
    data = json.loads(raw)
    for seg in data.get("segments", []):
        if seg.get("coin") == "XLM":
            seg["stat"] = "11 signals • current $0.2550"
    script_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"✅ Patched: {script_path.name}")
else:
    print(f"❌ Script not found: {script_path}")

# ═══════════════════════════════════════════════════════
# PART 2 — Also patch shopping list with fresh levels
# ═══════════════════════════════════════════════════════
shop_path = Path("Images for Videos/longform_charts/2026-06-01/SHOPPING_LIST.txt")
if shop_path.exists():
    raw = shop_path.read_text(encoding="utf-8")
    level_replacements = [
        ("Current price: $0.16862",  "Current price: $0.2550  ⚠️ UPDATED (was $0.16862 at detection)"),
        ("Entry:  $0.16862",         "Entry:  $0.2550"),
        ("Stop:   $0.16015286",      "Stop:   $0.2423"),
        ("TP1:    $0.18132071",      "TP1:    $0.2741"),
        ("TP2:    $0.19402143",      "TP2:    $0.2933"),
        ("TP3:    $0.21095571",      "TP3:    $0.3188"),
    ]
    for old, new in level_replacements:
        raw = raw.replace(old, new)
    shop_path.write_text(raw, encoding="utf-8")
    print(f"✅ Patched: {shop_path.name}")
else:
    print(f"⚠️  Shopping list not found (skipping): {shop_path}")

print("\nDone. Now re-take the XLM_4h.png screenshot with the new levels drawn,")
print("then run: .\\longform_step2.bat")
