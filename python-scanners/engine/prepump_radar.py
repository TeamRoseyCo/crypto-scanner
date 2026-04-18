"""
================================================================================
PRE-PUMP RADAR  v1.0
================================================================================
Early-detection scanner that surfaces coins BEFORE the pump starts.
Looks for stealth accumulation signals that fire before price breaks out.

Key differences from master_orchestrator:
  - Wider universe  : all Binance USDT spot pairs with volume > $200K
  - 1h candles      : faster detection than 4h (catches intraday coiling)
  - Pre-breakout signals only: BB/KC squeeze, OBV divergence, CMF, vol build
  - No 2-scan rule  : single-scan alert is enough to surface the token
  - No quiet hours  : runs 24/7 including Asia session
  - Binance-only    : no CoinGecko rate limits, runs in ~2 minutes

[!] NOT a trade signal -- accumulation watch only.
    Confirm on master scanner before any entry.

Usage:
  python prepump_radar.py
================================================================================
"""

import os
import sys
import requests
import pandas as pd
import numpy as np
import time
import json
import logging
from datetime import datetime
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────────────────
_ENGINE_DIR   = Path(__file__).resolve().parent
_PYTHON_DIR   = _ENGINE_DIR.parent
_PROJECT_ROOT = _PYTHON_DIR.parent
_OUTPUT_DIR   = _PROJECT_ROOT / "outputs" / "scanner-results"
_CACHE_DIR    = _PROJECT_ROOT / "cache"   / "prepump"
_LOG_DIR      = _PROJECT_ROOT / "outputs" / "logs"

for d in (_OUTPUT_DIR, _CACHE_DIR, _LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
_log_file = _LOG_DIR / f"prepump_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("prepump")

# ─────────────────────────────────────────────────────────────────────────────
# SHARED INDICATORS
# ─────────────────────────────────────────────────────────────────────────────
try:
    sys.path.insert(0, str(_ENGINE_DIR))
    from indicators import (
        compute_rsi, compute_atr, compute_obv,
        compute_cmf, compute_bb, compute_keltner,
    )
except ImportError:
    log.error("indicators.py not found — cannot run. Make sure it is in engine/")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
CFG = {
    # Universe
    "min_volume_24h_usdt":   200_000,   # Lower bar — catch coins before vol spikes
    "max_coins":                  400,   # Scan top N by 24h volume
    "ohlcv_bars":                  96,   # 1h × 96 = 4 days of history
    "cache_max_age_h":            1.0,   # Reuse 1h OHLCV cache for up to 1 hour

    # Reporting
    "min_signals":                  3,   # Minimum signals to surface a coin
    "max_report_coins":            20,   # Max rows in the output report

    # Signal thresholds
    "bb_squeeze_width_pct":      4.5,   # BB width < 4.5% of price = compressed
    "cmf_threshold":             0.03,  # CMF > 0.03 = institutional buying
    "rsi_min":                     25,  # RSI floor — not in freefall
    "rsi_max":                     62,  # RSI ceiling — not overbought
    "vol_build_min_mult":         1.2,  # Recent 6h vol ≥ 1.2× baseline = building
    "vol_build_max_mult":         4.5,  # Recent 6h vol < 4.5× baseline = not yet pumping
    "obv_lookback":                12,  # Bars for OBV divergence check (12h)
    "price_flat_threshold":      0.03,  # Price change < 3% over lookback = "flat"
    "atr_contraction_mult":       0.7,  # Current ATR < 70% of 30-bar avg = coiling
    "funding_neg_threshold":      0.0,  # Funding < 0 = shorts paying longs
}

STABLECOINS = {
    "USDT", "USDC", "DAI", "BUSD", "TUSD", "USDD", "FDUSD", "PYUSD",
    "USDE", "SUSDE", "BFUSD", "RLUSD", "USDG", "USD0", "GHO", "USDAI",
    "WBTC", "WETH", "STETH", "RETH", "CBETH", "PAXG", "XAUT", "TBTC",
    "WBNB", "JITOSOL", "MSOL", "BNSOL", "EURC", "FRAX", "LUSD", "SUSD",
    "CRVUSD", "GUSD", "USDS", "SUSDS", "FRXETH", "OETH", "SUPRETH",
}

_BN_API  = "https://api.binance.com/api/v3"
_BN_FAPI = "https://fapi.binance.com/fapi/v1"   # futures — funding rates
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "crypto-prepump-radar/1.0"})


# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCHING
# ─────────────────────────────────────────────────────────────────────────────

def fetch_binance_universe() -> list[dict]:
    """
    Pull all USDT spot pairs from Binance 24h ticker.
    Returns list sorted by volume desc, filtered by min volume.
    """
    try:
        r = _SESSION.get(f"{_BN_API}/ticker/24hr", timeout=15)
        r.raise_for_status()
        tickers = r.json()
    except Exception as e:
        log.error(f"Failed to fetch Binance universe: {e}")
        return []

    coins = []
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        base = sym[:-4]
        if base in STABLECOINS:
            continue
        try:
            volume = float(t["quoteVolume"])
            price  = float(t["lastPrice"])
            change = float(t["priceChangePercent"])
        except (ValueError, KeyError):
            continue
        if volume < CFG["min_volume_24h_usdt"]:
            continue
        if price < 0.0000001:
            continue
        if change < -50:
            continue  # skip free-falling tokens
        coins.append({
            "symbol":    base,
            "price":     price,
            "volume":    volume,
            "change_24h": change,
        })

    coins.sort(key=lambda x: x["volume"], reverse=True)
    log.info(f"  Universe: {len(coins)} coins (>${CFG['min_volume_24h_usdt']:,} vol)")
    return coins[:CFG["max_coins"]]


def fetch_funding_rates() -> dict[str, float]:
    """Current funding rates from Binance perpetuals (free endpoint)."""
    try:
        r = _SESSION.get(f"{_BN_FAPI}/premiumIndex", timeout=10)
        if r.status_code != 200:
            return {}
        return {
            d["symbol"].replace("USDT", ""): float(d.get("lastFundingRate", 0))
            for d in r.json()
            if isinstance(d, dict) and d.get("symbol", "").endswith("USDT")
        }
    except Exception as e:
        log.warning(f"Funding rate fetch failed: {e}")
        return {}


def fetch_1h_ohlcv(symbol: str) -> pd.DataFrame | None:
    """Fetch 1h OHLCV from Binance with a 1h cache."""
    cache_file = _CACHE_DIR / f"{symbol}_1h.csv"
    if cache_file.exists():
        age_h = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_h < CFG["cache_max_age_h"]:
            try:
                df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
                if len(df) >= 30:
                    return df
            except Exception:
                pass

    try:
        r = _SESSION.get(
            f"{_BN_API}/klines",
            params={
                "symbol":   f"{symbol}USDT",
                "interval": "1h",
                "limit":    CFG["ohlcv_bars"],
            },
            timeout=10,
        )
        if r.status_code != 200:
            return None
        rows = r.json()
        if not isinstance(rows, list) or len(rows) < 30:
            return None

        df = pd.DataFrame(rows, columns=[
            "ts", "open", "high", "low", "close", "base_vol",
            "close_time", "volume", "trades", "taker_base", "taker_quote", "ignore",
        ])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms")
        df.set_index("ts", inplace=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df[["open", "high", "low", "close", "volume"]].dropna()

        try:
            df.to_csv(cache_file)
        except Exception:
            pass
        return df
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL DETECTORS  (each returns bool — true = signal fires)
# ─────────────────────────────────────────────────────────────────────────────

def sig_bb_squeeze(df: pd.DataFrame) -> bool:
    """
    BB squeeze: BB width compressed OR BB bands sitting inside Keltner Channels
    (TTM Squeeze — highest quality compression signal).
    """
    try:
        upper_bb, mid_bb, lower_bb = compute_bb(df["close"], 20, 2.0)
        upper_kc, _,     lower_kc  = compute_keltner(df, 20, 10, 1.5)
        price       = df["close"].iloc[-1]
        bb_width    = (upper_bb.iloc[-1] - lower_bb.iloc[-1]) / price * 100
        inside_kc   = (lower_bb.iloc[-1] >= lower_kc.iloc[-1]) and \
                      (upper_bb.iloc[-1] <= upper_kc.iloc[-1])
        return bb_width < CFG["bb_squeeze_width_pct"] or inside_kc
    except Exception:
        return False


def sig_obv_divergence(df: pd.DataFrame) -> bool:
    """
    OBV rising while price is flat or down = smart money accumulating quietly.
    Bullish divergence: the most reliable pre-pump signal.
    """
    try:
        n   = CFG["obv_lookback"]
        obv = compute_obv(df)
        price_chg = (df["close"].iloc[-1] - df["close"].iloc[-n]) / (df["close"].iloc[-n] + 1e-9)
        obv_base  = abs(obv.iloc[-n]) + 1e-9
        obv_chg   = (obv.iloc[-1] - obv.iloc[-n]) / obv_base
        # Price flat or down, OBV rising > 2%
        return price_chg <= CFG["price_flat_threshold"] and obv_chg > 0.02
    except Exception:
        return False


def sig_cmf_positive(df: pd.DataFrame) -> bool:
    """CMF above threshold = net institutional money flow is positive."""
    try:
        cmf = compute_cmf(df, 20)
        return float(cmf.iloc[-1]) > CFG["cmf_threshold"]
    except Exception:
        return False


def sig_rsi_reset(df: pd.DataFrame) -> float | None:
    """
    RSI in the ideal pre-pump zone: not overbought, not in freefall.
    Returns the RSI value if in zone, else None.
    """
    try:
        rsi = compute_rsi(df["close"], 14)
        val = float(rsi.iloc[-1])
        return val if CFG["rsi_min"] <= val <= CFG["rsi_max"] else None
    except Exception:
        return None


def sig_vol_building(df: pd.DataFrame) -> bool:
    """
    Volume starting to build (early phase) but not yet exploded.
    Recent 6h avg is 1.2–4.5× the prior 24h baseline.
    Catches the early volume ramp before price breaks out.
    """
    try:
        vols = df["volume"].values
        if len(vols) < 30:
            return False
        recent   = vols[-6:].mean()
        baseline = vols[-30:-6].mean()
        if baseline <= 0:
            return False
        ratio = recent / baseline
        return CFG["vol_build_min_mult"] <= ratio <= CFG["vol_build_max_mult"]
    except Exception:
        return False


def sig_atr_contraction(df: pd.DataFrame) -> bool:
    """
    ATR contracting: volatility compressing = spring loading before expansion.
    Current ATR < 70% of the 30-bar rolling average ATR.
    """
    try:
        atr   = compute_atr(df, 14)
        price = df["close"].iloc[-1]
        if price <= 0:
            return False
        current = atr.iloc[-1] / price
        avg     = (atr.iloc[-30:] / price).mean()
        return current < avg * CFG["atr_contraction_mult"]
    except Exception:
        return False


def sig_higher_lows(df: pd.DataFrame) -> bool:
    """
    Three consecutive higher swing lows = early uptrend structure forming.
    Uses a simple 5-bar local minima detector.
    """
    try:
        c = df["close"].values
        lows = [
            (i, c[i])
            for i in range(2, len(c) - 2)
            if c[i] < c[i-1] and c[i] < c[i+1] and
               c[i] < c[i-2] and c[i] < c[i+2]
        ]
        return len(lows) >= 3 and lows[-1][1] > lows[-2][1] > lows[-3][1]
    except Exception:
        return False


def sig_funding_negative(symbol: str, funding_rates: dict) -> bool:
    """Funding rate negative = shorts piling in = squeeze fuel for longs."""
    rate = funding_rates.get(symbol)
    return rate is not None and rate < CFG["funding_neg_threshold"]


# ─────────────────────────────────────────────────────────────────────────────
# COIN SCANNER
# ─────────────────────────────────────────────────────────────────────────────

def _compute_rsi_display(df: pd.DataFrame) -> float | None:
    """Always return the current RSI value regardless of zone, for display."""
    try:
        rsi = compute_rsi(df["close"], 14)
        val = float(rsi.iloc[-1])
        return round(val, 1) if not np.isnan(val) else None
    except Exception:
        return None


def _rsi_display(r: dict) -> str:
    """Format RSI for display, flagging when outside the ideal zone."""
    val = r.get("rsi")
    if val is None:
        return "n/a"
    in_zone = CFG["rsi_min"] <= val <= CFG["rsi_max"]
    marker  = "" if in_zone else " (!)"
    return f"{val}{marker}"


_SIGNAL_LABELS = {
    "bb_squeeze":      "BB squeeze",
    "obv_divergence":  "OBV divergence",
    "cmf_positive":    "CMF+",
    "rsi_reset":       "RSI reset",
    "vol_building":    "vol building",
    "atr_contraction": "ATR coiling",
    "higher_lows":     "higher lows",
    "funding_neg":     "funding neg",
}


def scan_coin(coin: dict, funding_rates: dict) -> dict | None:
    """Run all pre-pump detectors on one coin. Returns result dict or None."""
    symbol = coin["symbol"]
    df     = fetch_1h_ohlcv(symbol)
    if df is None or len(df) < 30:
        return None

    rsi_val     = sig_rsi_reset(df)          # None if outside zone
    rsi_display = _compute_rsi_display(df)   # always the real value

    signals = {
        "bb_squeeze":      sig_bb_squeeze(df),
        "obv_divergence":  sig_obv_divergence(df),
        "cmf_positive":    sig_cmf_positive(df),
        "rsi_reset":       rsi_val is not None,
        "vol_building":    sig_vol_building(df),
        "atr_contraction": sig_atr_contraction(df),
        "higher_lows":     sig_higher_lows(df),
        "funding_neg":     sig_funding_negative(symbol, funding_rates),
    }

    fired = [k for k, v in signals.items() if v]
    if len(fired) < CFG["min_signals"]:
        return None

    # Extra display metrics
    try:
        obv      = compute_obv(df)
        obv_dir  = "↑" if obv.iloc[-1] > obv.iloc[-12] else "↓"
    except Exception:
        obv_dir  = "?"

    try:
        u, _, l  = compute_bb(df["close"], 20, 2.0)
        bb_width = (u.iloc[-1] - l.iloc[-1]) / coin["price"] * 100
    except Exception:
        bb_width = 0.0

    try:
        atr_pct  = compute_atr(df, 14).iloc[-1] / coin["price"] * 100
    except Exception:
        atr_pct  = 0.0

    # Volume ratio: recent 6h vs prior 24h baseline
    try:
        vols     = df["volume"].values
        vol_ratio = vols[-6:].mean() / (vols[-30:-6].mean() + 1e-9)
    except Exception:
        vol_ratio = 0.0

    return {
        "symbol":       symbol,
        "price":        coin["price"],
        "volume_24h":   coin["volume"],
        "change_24h":   coin["change_24h"],
        "signal_count": len(fired),
        "signals":      fired,
        "rsi":          rsi_display,
        "bb_width_pct": round(bb_width, 2),
        "atr_pct":      round(atr_pct, 2),
        "obv_dir":      obv_dir,
        "vol_ratio":    round(vol_ratio, 2),
        "funding_rate": funding_rates.get(symbol),
    }


# ─────────────────────────────────────────────────────────────────────────────
# REPORT BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_report(results: list[dict], universe_size: int, ts: datetime) -> str:
    sep  = "─" * 80
    wide = "=" * 80

    lines = [
        wide,
        "  PRE-PUMP RADAR  —  EARLY ACCUMULATION DETECTOR",
        f"  Generated : {ts.strftime('%Y-%m-%d %H:%M:%S')}",
        "  Data      : Binance 1h candles  (no CoinGecko, no rate limits)",
        wide,
        "",
        "  [!] NOT A TRADE SIGNAL — accumulation watch only",
        "  [!] Confirm on master scanner (>=45 conviction) before any entry",
        "",
        "  What this catches:",
        "    Coins coiling in stealth accumulation BEFORE the breakout:",
        "    BB/KC squeeze  |OBV divergence (smart money buying quietly)",
        "    CMF positive  |volume building  |ATR contracting  |higher lows",
        "",
        f"  Universe : {universe_size} Binance USDT pairs (vol > ${CFG['min_volume_24h_usdt']:,})",
        f"  Alerts   : {len(results)} coins with ≥{CFG['min_signals']} signals",
        "",
        sep,
    ]

    if not results:
        lines += ["", "  No coins meet the minimum signal threshold right now.", ""]
    else:
        for i, r in enumerate(results, 1):
            fr = r["funding_rate"]
            fr_str  = f"{fr*100:+.4f}%" if fr is not None else "n/a"
            sig_str = "  |  ".join(_SIGNAL_LABELS.get(s, s) for s in r["signals"])
            heat    = "*" * r["signal_count"]

            lines += [
                "",
                f"[{i:2d}]  {r['symbol']:<10}  [{heat}]  {r['signal_count']}/8 signals",
                f"      Price ${r['price']:.6g}   24h {r['change_24h']:+.1f}%   "
                f"Vol ${r['volume_24h']:>12,.0f}   VolRatio {r['vol_ratio']:.1f}×",
                f"      RSI {_rsi_display(r):<10}  BB {r['bb_width_pct']:.2f}%  "
                f"ATR {r['atr_pct']:.2f}%  OBV {r['obv_dir']}  Funding {fr_str}",
                f"      ► {sig_str}",
                sep,
            ]

    lines += [
        "",
        "HOW TO USE:",
        "  1. Coins with ≥4 signals are the highest-priority watches",
        "  2. Open a 1h chart — confirm price is coiling (not already pumping)",
        "  3. Run master scanner next cycle — if conviction ≥45 fires, it's live",
        "  4. NEVER enter based on this radar alone — it detects setup, not entry",
        "",
        wide,
    ]

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run() -> None:
    ts = datetime.now()
    log.info("=" * 60)
    log.info("PRE-PUMP RADAR  v1.0  —  starting")
    log.info("=" * 60)

    # ── 1. Universe ────────────────────────────────────────────────────────────
    log.info("[1/3] Fetching Binance universe...")
    coins = fetch_binance_universe()
    if not coins:
        log.error("No coins fetched — aborting")
        return

    # ── 2. Funding rates ──────────────────────────────────────────────────────
    log.info("[2/3] Fetching funding rates...")
    funding_rates = fetch_funding_rates()
    log.info(f"  {len(funding_rates)} funding rates loaded")

    # ── 3. Scan ───────────────────────────────────────────────────────────────
    log.info(f"[3/3] Scanning {len(coins)} coins...")
    results = []
    skipped = 0
    for i, coin in enumerate(coins, 1):
        if i % 50 == 0:
            log.info(f"  [{i:3d}/{len(coins)}]  alerts so far: {len(results)}")
        result = scan_coin(coin, funding_rates)
        if result:
            results.append(result)
        else:
            skipped += 1

    # Sort: most signals first, then by volume
    results.sort(key=lambda x: (-x["signal_count"], -x["volume_24h"]))
    top = results[:CFG["max_report_coins"]]

    log.info(f"  Scan complete — {len(results)} alerts from {len(coins)} coins")

    # ── Save ──────────────────────────────────────────────────────────────────
    report   = build_report(top, len(coins), ts)
    ts_str   = ts.strftime("%Y%m%d_%H%M")

    txt_latest  = _OUTPUT_DIR / "prepump_radar_LATEST.txt"
    txt_stamped = _OUTPUT_DIR / f"prepump_radar_{ts_str}.txt"
    json_latest = _OUTPUT_DIR / "prepump_radar_LATEST.json"

    for path in (txt_latest, txt_stamped):
        try:
            path.write_text(report, encoding="utf-8")
        except Exception as e:
            log.warning(f"Could not write {path}: {e}")

    try:
        json_latest.write_text(
            json.dumps({
                "generated":    ts.isoformat(),
                "universe_size": len(coins),
                "total_alerts": len(results),
                "alerts":       top,
            }, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning(f"Could not write JSON: {e}")

    # Safe console print — handle Windows cp1252 terminals gracefully
    try:
        sys.stdout.buffer.write(report.encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()
    except AttributeError:
        print(report.encode("utf-8", errors="replace").decode("ascii", errors="replace"))
    log.info(f"Output -> {txt_latest}")


if __name__ == "__main__":
    run()
