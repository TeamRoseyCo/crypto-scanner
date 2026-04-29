# Scanner v3 — Setup & Run Guide

You now have **Phase 1 + Phase 2 complete**. This guide tells you what to do.

## What's in this folder

```
scanner_v3/
├── indicators.py          — Phase 1: all technical indicators
├── data.py                — Phase 1: unified Bybit + Binance fetcher
├── signals.py             — Phase 1: 26 canonical signals
└── ignition_scanner.py    — Phase 2: NEW — replaces ignition_radar + prepump_radar
```

## Phase 1 setup (already done — you confirmed it works)

You already ran `python data.py` and saw the universe + funding + OI output.
Phase 1 is locked in.

## Phase 2 setup — Run the new ignition scanner

This replaces `ignition_radar.py` AND `prepump_radar.py` in one cleaner scanner.

### Step 1: Run it once with `--no-cache` to force a fresh fetch

```bash
cd C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\crypto scanner\crypto-scanner\python-scanners\engine\scanner_v3
python ignition_scanner.py --no-cache
```

**Expected:**
- Takes 5–10 minutes the first run (fetching ~600 coins of 1h OHLCV)
- Subsequent runs cached, take under a minute
- Prints progress every ~30 coins ("...30/604  (surfaced so far: 5)")
- Prints the full report at the end
- Writes 3 files to `outputs/scanner-results/`:
  - `ignition_v3_LATEST.txt` — human-readable report
  - `ignition_v3_LATEST.json` — machine-readable (orchestrator will use later)
  - `ignition_v3_YYYYMMDD_HHMMSS.txt` — timestamped archive

### Step 2: Run the OLD scanners alongside for comparison

Don't retire them yet. Run both for a week or two and compare:

```bash
# old (still works, untouched)
python ../ignition_radar.py
python ../prepump_radar.py

# new (Phase 2)
python ignition_scanner.py
```

Compare the WATCH NOW lists. They should overlap heavily. If the new scanner
misses a coin the old ones flagged, **tell me which coin** — that's diagnostic
info I need to tune.

### Step 3: Tell me what you see

Three things specifically:
1. Did the scan complete without crashing? (paste the last few lines of output)
2. How many coins ended up in WATCH NOW vs ON RADAR? (sanity check on tier thresholds)
3. Anything obviously wrong with the report format?

That's all. I'll use that to either green-light Phase 3 or tune Phase 2.

---

## What Phase 2 does differently from the old scanners

| Aspect | Old (ignition + prepump) | New (ignition_scanner) |
|---|---|---|
| Code | ~1500 lines combined | ~370 lines |
| Universe | CG top 700 + Binance USDT separately | Merged Bybit + Binance, ~600 deduped |
| BB Squeeze definition | Two different ones | Single canonical TTM Squeeze |
| RSI Divergence | Window-based, RSI(14) | Pivot-based, RSI(7) |
| OBV signals | 1 per scanner, both different | 3 separate signals (slope/stealth/divergence) |
| Volume signals | 4 different definitions | 2 distinct signals |
| Output | 2 separate text files | 1 LATEST.txt + 1 LATEST.json + timestamped TXT |
| Fetch caching | Per-scanner, separate caches | Shared `cache/shared_ohlcv/` |
| Trade plans | ignition has them, prepump doesn't | Removed — those go in trend_scanner (Phase 4) |

## Tier thresholds (current settings)

```
WATCH NOW:  conviction >= 50 AND signals >= 4
ON RADAR :  conviction >= 30 AND signals >= 3
(below)  :  filtered out
```

Conviction is normalized 0-100 from weighted signal sum (max ~22 weight points).
A coin firing ALL 13 signals at full strength would score 100.
Realistically WATCH NOW coins fire 5-8 signals.

## What's coming in Phase 3

`perp_scanner.py` — refactor of `bybit_radar.py`. Same job (OI/funding
positioning) but uses the same foundation as Phase 2 so the data is shared
and the output format is consistent. Quick build, ~150 lines.

After Phase 3 you'll have all 3 alpha scanners running. Phase 4
(`trend_scanner.py`) is the confirmation layer (replaces spot + enhanced).
Phase 5 is the orchestrator that joins it all together.
