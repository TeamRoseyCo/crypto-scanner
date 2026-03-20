"""
================================================================================
CRYPTO SCANNER PIPELINE
================================================================================
Stage 1a — pump_hunter pre-filter (≥3 signals) → candidate set A
Stage 1b — rsps_prime_key pre-filter (alpha)    → candidate set B
Stage 2  — master_orchestrator deep analysis    → trade plan (A ∪ B only)

Benefit: ~66% fewer OHLCV API calls. ~130 min → ~50 min.
Usage:
  python pipeline.py
  python pipeline.py --account 96700
================================================================================
"""

import os, sys, time, argparse
from pathlib import Path

# Fix Windows cp1252 encoding issues with Unicode characters
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ENGINE_DIR   = Path(__file__).resolve().parent
_SCANNERS_DIR = _ENGINE_DIR.parent / "scanners"
sys.path.insert(0, str(_SCANNERS_DIR))
sys.path.insert(0, str(_ENGINE_DIR))

import pump_hunter      as ph
import rsps_prime_key   as pk
import master_orchestrator as orch


def run_pipeline(account_size: float | None = None) -> None:
    t0 = time.time()

    _key = os.environ.get("CG_API_KEY", "")
    _demo = os.environ.get("CG_DEMO_KEY", "")
    _mode = ("PRO key active — delay 1.2s/coin" if _key
             else "Demo key active — delay 2.0s/coin" if _demo
             else "Free tier (no key!) — will likely get rate-limited")

    print("\n" + "=" * 80)
    print("  CRYPTO SCANNER PIPELINE  (pump_hunter → prime_key → orchestrator)")
    print(f"  API mode : {_mode}")
    print("=" * 80 + "\n")

    # ── Stage 0: Shared data fetch ────────────────────────────────────────────
    print("[Stage 0] Fetching shared BTC + market data...")
    btc_data  = ph.fetch_btc_data(30)
    btc_close = btc_data["close"] if btc_data is not None else None
    btc_7d    = (
        float(((btc_data["close"].iloc[-1] / btc_data["close"].iloc[-42]) - 1) * 100)
        if btc_data is not None else 0.0
    )

    if btc_7d < ph.CONFIG["BTC_MIN_7D_CHANGE"]:
        print(f"\n  BTC 7d = {btc_7d:.1f}% — bear regime. Pipeline aborted.")
        return

    coins = ph.fetch_market_coins()
    print(f"  BTC 7d: {btc_7d:+.1f}% OK  |  {len(coins)} market coins fetched\n")

    # ── Stage 1a: Pump Hunter pre-filter ─────────────────────────────────────
    t1 = time.time()
    print("[Stage 1a] Pump Hunter pre-filter (>=3 signals) — populating OHLCV cache...")
    pump_cands = ph.pre_filter(coins, btc_close, min_layers=3)
    print(f"  -> {len(pump_cands)} candidates  ({time.time() - t1:.0f}s)\n")

    # ── Stage 1b: Prime Key pre-filter ───────────────────────────────────────
    t1 = time.time()
    print("[Stage 1b] Prime Key pre-filter (alpha checks) — reading from cache...")
    pk_cands = pk.pre_filter(coins, btc_7d)
    print(f"  -> {len(pk_cands)} candidates  ({time.time() - t1:.0f}s)\n")

    # ── Merge ─────────────────────────────────────────────────────────────────
    whitelist = pump_cands | pk_cands
    print(
        f"[Merge]  {len(pump_cands)} pump  +  {len(pk_cands)} prime-key"
        f"  =  {len(whitelist)} unique candidates\n"
    )

    if not whitelist:
        print("No pre-filter candidates. Falling back to full master_orchestrator scan...\n")
        orch.run(account_size=account_size)
        print(f"\nTotal pipeline time: {time.time() - t0:.0f}s")
        return

    # ── Stage 2: Master Orchestrator on whitelist only ────────────────────────
    t1 = time.time()
    print(f"[Stage 2] Master Orchestrator — deep analysis on {len(whitelist)} coins...")
    orch.run(account_size=account_size, coin_whitelist=whitelist)
    print(f"  Stage 2: {time.time() - t1:.0f}s")

    elapsed   = time.time() - t0
    est_full  = len(coins) * 6.5 * 3          # 3 scripts x all coins x 6.5s
    print(f"\nTotal pipeline time    : {elapsed:.0f}s  ({elapsed/60:.1f} min)")
    print(f"Est. standalone time   : {est_full:.0f}s  ({est_full/60:.1f} min)")
    print(f"Time saved             : ~{est_full - elapsed:.0f}s  ({(1 - elapsed/est_full)*100:.0f}%)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crypto Scanner Pipeline")
    parser.add_argument("--account", type=float, default=None, help="Account size in USDT")
    args = parser.parse_args()
    run_pipeline(account_size=args.account)
