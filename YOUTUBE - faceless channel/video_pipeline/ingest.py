"""
ingest.py — Read scanner_v3 JSON outputs and build a structured summary.

v1.2 changes:
  - Searches MANY key locations for BTC price (top level, context, nested)
  - When BTC price still can't be found, prints a DIAGNOSTIC dump of the
    top-level keys + first-level nested keys in each scanner JSON, so you
    can see exactly where the BTC price is hiding in your data.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("video_pipeline.ingest")


def load_scanner_data(output_dir: Path) -> dict:
    result = {"loaded_at": datetime.now().isoformat()}

    files = {
        "master_radar": "master_radar_LATEST.json",
        "ignition":     "ignition_v3_LATEST.json",
        "perp":         "perp_v3_LATEST.json",
        "trend":        "trend_v3_LATEST.json",
        "spot":         "spot_trade_plan_LATEST.json",
    }

    for name, fname in files.items():
        path = output_dir / fname
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    result[name] = json.load(f)
                log.info(f"  Loaded {fname}")
            except Exception as e:
                log.warning(f"  Failed to load {fname}: {e}")
        else:
            log.debug(f"  {fname} not found — skipping")

    return result


# All the key names BTC price might hide under
BTC_PRICE_KEY_CANDIDATES = (
    "btc_price", "btc_close", "btc_usd", "btc_last",
    "price", "close", "btc", "bitcoin_price",
    "btc_usd_price", "btc_spot",
)

BTC_7D_KEY_CANDIDATES = (
    "btc_7d_pct", "btc_7d", "btc_change_7d", "change_7d",
    "btc_7day_pct", "btc_week_pct",
)


def _try_extract_btc_price(obj) -> Optional[float]:
    """
    Recursively look for a BTC price in a dict.
    Tries top level, then any nested dict (context, market, summary, etc).
    """
    if not isinstance(obj, dict):
        return None

    # Try direct keys at this level
    for key in BTC_PRICE_KEY_CANDIDATES:
        v = obj.get(key)
        if v is None:
            continue
        try:
            f = float(v)
            if f >= 1000:  # sanity filter: BTC is at least $1000
                return f
        except (TypeError, ValueError):
            continue

    # Try one level of nested dicts (don't recurse arbitrarily deep — too risky)
    for nested_key in ("context", "market", "summary", "regime", "btc",
                       "market_context", "data", "meta"):
        nested = obj.get(nested_key)
        if isinstance(nested, dict):
            for key in BTC_PRICE_KEY_CANDIDATES:
                v = nested.get(key)
                if v is None:
                    continue
                try:
                    f = float(v)
                    if f >= 1000:
                        return f
                except (TypeError, ValueError):
                    continue

    return None


def _try_extract_btc_7d(obj) -> Optional[float]:
    if not isinstance(obj, dict):
        return None
    for key in BTC_7D_KEY_CANDIDATES:
        v = obj.get(key)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    for nested_key in ("context", "market", "summary", "regime", "btc"):
        nested = obj.get(nested_key)
        if isinstance(nested, dict):
            for key in BTC_7D_KEY_CANDIDATES:
                v = nested.get(key)
                if v is None:
                    continue
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
    return None


def _diagnostic_dump(raw_data: dict) -> None:
    """
    Print top-level keys (and a peek at common nested dicts) for each scanner.
    Called only when BTC extraction fails — gives you exact field names
    to extend BTC_PRICE_KEY_CANDIDATES with.
    """
    log.warning("  ┌─ BTC DIAGNOSTIC ────────────────────────────────────────")
    log.warning("  │ Could not find BTC price. Here's what's available in each scanner.")
    log.warning("  │ If you see a key that looks like the BTC price, add it to")
    log.warning("  │ BTC_PRICE_KEY_CANDIDATES in ingest.py.")
    log.warning("  │")
    for scanner_name in ("master_radar", "trend", "perp", "ignition", "spot"):
        data = raw_data.get(scanner_name)
        if data is None:
            continue
        if not isinstance(data, dict):
            log.warning(f"  │ {scanner_name}: (not a dict, type={type(data).__name__})")
            continue

        top_keys = list(data.keys())[:25]
        log.warning(f"  │ {scanner_name} top-level keys: {top_keys}")

        # Peek inside common context-like sub-objects
        for nested_key in ("context", "market", "summary", "regime", "meta"):
            nested = data.get(nested_key)
            if isinstance(nested, dict):
                nested_keys = list(nested.keys())[:25]
                log.warning(f"  │   {scanner_name}.{nested_key}: {nested_keys}")

                # If we find anything numeric that's BTC-shaped, highlight it
                for k, v in nested.items():
                    if isinstance(v, (int, float)) and 1000 < v < 1_000_000:
                        log.warning(f"  │      ↪ {nested_key}.{k} = {v}  ← could be BTC price?")
    log.warning("  └─────────────────────────────────────────────────────────")


def build_market_summary(raw_data: dict) -> dict:
    summary = {
        "date":          datetime.now().strftime("%Y-%m-%d"),
        "time_utc":      datetime.utcnow().strftime("%H:%M UTC"),
        "regime":        "unknown",
        "btc_price":     None,
        "btc_7d_pct":    None,
        "warnings":      [],
        "top_coins":     [],
        "extended_coins": [],
        "ignition_watch_now": [],
    }

    # ── Extract from master_radar (best source — has confluence) ─────────────
    master = raw_data.get("master_radar")
    if master:
        ctx = master.get("context", {}) if isinstance(master, dict) else {}
        summary["regime"]   = ctx.get("regime", "unknown") if isinstance(ctx, dict) else "unknown"
        summary["warnings"] = master.get("warnings", []) if isinstance(master, dict) else []

        # Try multiple paths for BTC
        summary["btc_price"]  = _try_extract_btc_price(master)
        summary["btc_7d_pct"] = _try_extract_btc_7d(master)

        if isinstance(master, dict):
            for bucket_name in ("convergence", "strong_setup"):
                for coin in master.get(bucket_name, []) or []:
                    summary["top_coins"].append(_normalize_master_coin(coin, bucket_name))

            for coin in master.get("extended", []) or []:
                summary["extended_coins"].append(_normalize_master_coin(coin, "extended"))

            for coin in (master.get("single_scanner", []) or [])[:5]:
                summary["top_coins"].append(_normalize_master_coin(coin, "single_scanner"))

    # ── ignition fallback ────────────────────────────────────────────────────
    ignition = raw_data.get("ignition")
    if ignition and isinstance(ignition, dict):
        for coin in ignition.get("watch_now", []) or []:
            summary["ignition_watch_now"].append({
                "symbol":     coin.get("base", "?"),
                "conviction": coin.get("conviction", 0),
                "signals":    coin.get("fired_signals", []),
                "price":      coin.get("price"),
                "change_24h": coin.get("price_24h_pct", 0),
            })

        if not master and ignition.get("watch_now"):
            for coin in ignition["watch_now"][:8]:
                summary["top_coins"].append({
                    "symbol":     coin.get("base", "?"),
                    "confluence": coin.get("conviction", 0),
                    "bucket":     "ignition_watch_now",
                    "scanners":   1,
                    "signals":    coin.get("fired_signals", [])[:5],
                    "price":      coin.get("price"),
                    "change_24h": coin.get("price_24h_pct", 0),
                    "volume_24h": coin.get("volume_24h", 0),
                    "trade_plan": None,
                })

    # ── BTC fallback chain — try every scanner ───────────────────────────────
    if summary["btc_price"] is None:
        for scanner_name in ("trend", "perp", "ignition", "spot"):
            scanner_data = raw_data.get(scanner_name)
            price = _try_extract_btc_price(scanner_data) if scanner_data else None
            if price is not None:
                summary["btc_price"] = price
                log.info(f"  BTC price recovered from {scanner_name}: ${price:,.0f}")
                break

    if summary["btc_7d_pct"] is None:
        for scanner_name in ("trend", "perp", "ignition", "spot"):
            scanner_data = raw_data.get(scanner_name)
            pct = _try_extract_btc_7d(scanner_data) if scanner_data else None
            if pct is not None:
                summary["btc_7d_pct"] = pct
                break

    if summary["btc_price"] is None:
        log.warning("  BTC price not found in any scanner output.")
        _diagnostic_dump(raw_data)

    summary["top_coins"] = summary["top_coins"][:8]
    return summary


def _normalize_master_coin(coin: dict, bucket: str) -> dict:
    all_signals = []
    for key in ("ignition_signals", "perp_signals", "spot_signals"):
        all_signals.extend(coin.get(key, []) or [])
    seen = set()
    unique_signals = []
    for s in all_signals:
        if s not in seen:
            seen.add(s)
            unique_signals.append(s)

    return {
        "symbol":     coin.get("base", "?"),
        "confluence": coin.get("confluence", 0),
        "bucket":     bucket,
        "scanners":   coin.get("scanner_count", 1),
        "signals":    unique_signals[:6],
        "price":      coin.get("price"),
        "change_24h": coin.get("price_24h_pct", 0),
        "volume_24h": coin.get("volume_24h", 0),
        "trade_plan": coin.get("trade_plan"),
    }
