# Crypto Scanner — System Map

A complete map of the system: **5 signal scanners** (what to trade) + **6 support tools**
(whether / when / how much to trade, and whether it actually worked).

Last verified against the codebase: 2026-06-21.

---

## The 5 scanners

**Ignition scanner** — your early-warning watchlist. Scans ~600 coins (Bybit + Binance, cap 700) on the 1h
timeframe and looks for 13 bullish signals like BB squeeze, whale candles, OBV stealth accumulation,
and RSI divergence. Surfaces coins as **WATCH NOW** (≥40 conviction, 5+ signals) or **ON RADAR**
(≥25 conviction, 3+ signals) — the answer to "what's about to move?" before it moves.
File: `python-scanners/engine/scanner_v3/ignition_scanner.py`

**Perp scanner** — derivatives-side confirmation. Scans Bybit perpetual futures looking specifically at
positioning data: open interest building, negative funding (shorts paying longs = squeeze fuel), OI
unwinding, and volume/OI surges. Adds a layer that pure technicals can't see — when positioning aligns
with a setup, conviction is higher.
File: `python-scanners/engine/scanner_v3/perp_scanner.py`

**Trend scanner** — multi-timeframe confirmation, the slowest but most thorough. Scans ~214 Bybit perps
across 6 timeframes (1h/2h/4h/6h/12h/1d) using ~17 indicators (ADX, MACD, EMA stack, SuperTrend,
Ichimoku, Aroon, etc) and produces a composite trend score per coin. Also detects market regime
(BULL = BTC 7d > +3%, BEAR = BTC 7d < −7%, SIDEWAYS in between) which gates the other scanners'
aggressiveness and position sizing.
File: `python-scanners/engine/scanner_v3/trend_scanner.py`

**Short scanner** — bearish mirror of ignition. Scans Bybit perps only (need a perp venue to short)
using 11 core bearish signals (distribution candles, OBV/RSI bearish divergence, lower highs, failed
breakouts, CMF selling) plus a couple reused long-side signals inverted. Hard-excludes coins with funding rate ≤ −0.01% to avoid shorting into squeeze
setups — the system refuses to let you stand in front of a freight train.
File: `python-scanners/engine/scanner_v3/short_scanner.py`

**Spot scanner (legacy)** — pre-v3 system, semi-retired. 15-signal scan over the CoinGecko universe
(~500 coins) with regime-aware conviction gating, 30–40 minute runtime. Different signal logic than v3
— kept as a cross-check and historical reference, slated for retirement once v3 has 4–8 weeks of
validated track record.
File: `python-scanners/engine/spot_scanner.py`

### How the scanners fit together

The first four scanners run via **`run_radar.bat`** in sequence: longs orchestrator
(ignition + perp + trend) → short scanner → signal tracker. The orchestrator is
`python-scanners/engine/scanner_v3/run_scan.py`. The tracker dumps every WATCH NOW signal into a SQLite
DB for forward outcome tracking, flags conflicts (a coin firing both long AND short = avoid both), and
after 4–8 weeks tells you per-signal win rates so you can prove which signals actually pay. The spot
scanner runs separately via **`run_spot.bat`** when wanted.

### System layers (scanners)

| Layer | What it sees | Scanner |
|---|---|---|
| Price/volume technicals | TA patterns on 1h | ignition |
| Multi-TF trend | Direction across 6 TFs | trend |
| Derivatives positioning | Who's leveraged where | perp |
| Bearish technicals | TA reversal patterns | short |

---

## The support tools (non-scanners)

These don't surface coins — they gate, time, and verify your trades. They're the other half of the
system.

**Macro watch** — the steering wheel. Pulls DXY (US Dollar Index) + Treasury yields (5Y/10Y/30Y) + Gold
+ BTC from Yahoo Finance and prints a risk-on/risk-off verdict for crypto. Crypto is macro-driven right
now — it can't sustainably bottom until the dollar and yields roll over. One-shot via `run_macro.bat`,
or continuous via `run_macro_watch.bat` (refreshes every 5 min and **beeps + pops a window when
DXY < 99 OR 10Y < 4.30%** = the macro turn). Note: DXY/yields freeze on weekends (TradFi closed), so the
gauge cannot change until Monday's open.
File: `python-scanners/launchers/macro_watch.py`

**Daily-loss gate** — the mechanical entry blocker. `run_gate.bat` pulls today's realized PnL from Bybit
and writes `today.json` (daily loss limit −$1,940, status OK / LIMIT_HIT). Before ANY entry: the gate
must be dated *today* and status OK, or it's a hard refuse. Stale date = stale gate = no entries until
re-synced.
Files: `python-scanners/engine/scanner_v3/daily_pnl_tracker.py` → `python-scanners/outputs/daily_pnl/today.json`

**Price alert** — `run_alert.bat` watches a coin and beeps + pops a window when it hits a level you've
defined (e.g. a breakout or pullback trigger). Lets you stop staring at a chart waiting for a level.
File: `python-scanners/launchers/price_alert_watch.py`

**Bybit sync** — `run_bybit_sync.bat` / `run_sync.bat` pulls your account, positions, and trade history
from Bybit so the gate and journal work off live data.

**Signal tracker** — the forward-testing DB. Every WATCH NOW signal gets dumped into a SQLite database
(`outputs/tracker/signals.sqlite`), logs conflicts when a coin fires long AND short, and after 4–8 weeks
tells you the per-signal win rate so you can prove which signals actually pay. Runs automatically at the
end of `run_radar.bat`; standalone via `run_tracker.bat`.

**Journal** — `run_journal.bat` records executed trades with outcomes for the trade record.

### System layers (support tools)

| Layer | What it sees | Tool |
|---|---|---|
| Macro regime | Dollar + yields (the real driver) | macro watch |
| Risk gate | Today's PnL vs daily loss limit | daily-loss gate |
| Timing | Price hitting a defined trigger | price alert |
| Account state | Live positions / trade history | bybit sync |
| Forward proof | Do the signals actually pay? | signal tracker |

---

## Launcher quick reference

| Launcher | Runs |
|---|---|
| `run_radar.bat` | Master radar: ignition + perp + trend → short → signal tracker |
| `run_spot.bat` | Legacy spot scanner (~30–40 min) |
| `run_macro.bat` | Macro gauge (one-shot) |
| `run_macro_watch.bat` | Macro gauge (continuous, with turn alarm) |
| `run_gate.bat` | Refresh daily-loss gate (`today.json`) |
| `run_alert.bat` | Price-trigger alert watcher |
| `run_bybit_sync.bat` / `run_sync.bat` | Sync Bybit account/trade data |
| `run_tracker.bat` | Signal forward-tracking DB (standalone) |
| `run_journal.bat` | Trade journal |
