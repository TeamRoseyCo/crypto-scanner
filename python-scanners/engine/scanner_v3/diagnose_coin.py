"""
Diagnostic: inspect a single coin through the new scanner's signal stack
and show every signal's outcome with diagnostic details.

Usage:
  python diagnose_coin.py TVK
  python diagnose_coin.py PARTI
  python diagnose_coin.py WIF
"""
from __future__ import annotations
import sys
from pathlib import Path

# Make sure we can find sibling modules
_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))

import data
import signals as S
import ignition_scanner as IG


def diagnose(base: str) -> None:
    print("=" * 70)
    print(f"DIAGNOSTIC: {base}")
    print("=" * 70)

    # ── Pick source ─────────────────────────────────────────────────────────
    universe = data.get_universe("both")
    coin     = next((c for c in universe if c["base"] == base), None)
    if coin is None:
        print(f"  ✗ {base} not in universe")
        return

    sources = []
    if coin.get("on_bybit"):   sources.append("bybit")
    if coin.get("on_binance"): sources.append("binance")
    print(f"\n  Sources: {', '.join(sources) or 'NONE'}")
    print(f"  Price:   ${coin['price']:.6f}")
    print(f"  24h:     {coin.get('price_24h_pct', 0):+.2f}%")
    print(f"  Vol:     ${max(coin.get('volume_24h',0), coin.get('turnover_24h',0))/1e6:.2f}M")
    print(f"  Funding: {coin.get('funding_rate')}")

    source = "bybit" if coin.get("on_bybit") else "binance" if coin.get("on_binance") else None
    if source is None:
        print("  ✗ No source available")
        return

    df = data.get_ohlcv(base, source, "1h", 200, use_cache=True)
    if df is None or len(df) < 50:
        print(f"  ✗ Insufficient OHLCV data (got {len(df) if df is not None else 0} bars)")
        return

    print(f"  Bars:    {len(df)} (1h, source={source})")

    btc = data.get_btc("1h", 200)
    btc_closes = btc["close"] if btc is not None else None

    # ── Run every signal individually with full detail ──────────────────────
    print(f"\n  {'Signal':<22} {'Fired':<7} {'Strength':<10} {'Value':<20} Extras")
    print("  " + "-" * 92)

    sig_specs = [
        ("bb_squeeze",        S.sig_bb_squeeze,        IG.SIGNAL_PARAMS["bb_squeeze"]),
        ("vol_in_window",     S.sig_vol_in_window,     IG.SIGNAL_PARAMS["vol_in_window"]),
        ("vol_expansion",     S.sig_vol_expansion,     IG.SIGNAL_PARAMS["vol_expansion"]),
        ("whale_candle",      S.sig_whale_candle,      IG.SIGNAL_PARAMS["whale_candle"]),
        ("obv_stealth_accum", S.sig_obv_stealth_accum, IG.SIGNAL_PARAMS["obv_stealth_accum"]),
        ("obv_divergence",    S.sig_obv_divergence,    IG.SIGNAL_PARAMS["obv_divergence"]),
        ("rsi_divergence",    S.sig_rsi_divergence,    IG.SIGNAL_PARAMS["rsi_divergence"]),
        ("rsi_reset",         S.sig_rsi_reset,         IG.SIGNAL_PARAMS["rsi_reset"]),
        ("rsi_in_zone",       S.sig_rsi_in_zone,       IG.SIGNAL_PARAMS["rsi_in_zone"]),
        ("cmf_positive",      S.sig_cmf_positive,      IG.SIGNAL_PARAMS["cmf_positive"]),
        ("higher_lows",       S.sig_higher_lows,       IG.SIGNAL_PARAMS["higher_lows"]),
        ("price_range_break", S.sig_price_range_break, IG.SIGNAL_PARAMS["price_range_break"]),
    ]

    fired_count = 0
    for name, fn, params in sig_specs:
        res = fn(df, **params)
        if res.fired:
            fired_count += 1
        mark = "  ●  " if res.fired else "  ○  "
        val_str = (f"{res.value:.4f}" if isinstance(res.value, (int, float)) and res.value is not None
                   else str(res.value)[:18])
        ex = (", ".join(f"{k}={v}" for k, v in (res.extras or {}).items()))[:60]
        print(f"  {name:<22} {mark}  {res.strength:<8.2f}  {val_str:<20} {ex}")

    # BTC decoupling
    if btc_closes is not None:
        res = S.sig_btc_decoupling(df["close"], btc_closes, **IG.SIGNAL_PARAMS["btc_decoupling"])
        mark = "  ●  " if res.fired else "  ○  "
        val_str = f"{res.value*100:+.2f}%" if res.value is not None else "—"
        if res.fired: fired_count += 1
        print(f"  {'btc_decoupling':<22} {mark}  {res.strength:<8.2f}  {val_str:<20}")

    # Funding negative (perp data)
    if coin.get("funding_rate") is not None:
        res = S.sig_funding_negative(coin["funding_rate"], **IG.SIGNAL_PARAMS["funding_negative"])
        mark = "  ●  " if res.fired else "  ○  "
        val_str = f"{res.value*100:+.4f}%" if res.value is not None else "—"
        if res.fired: fired_count += 1
        print(f"  {'funding_negative':<22} {mark}  {res.strength:<8.2f}  {val_str:<20}")

    # ── Now run through the actual scoring path ─────────────────────────────
    print()
    print("  --- Scoring through ignition_scanner.score_coin() ---")
    result = IG.score_coin(
        base         = base,
        df           = df,
        btc_closes   = btc_closes,
        funding_rate = coin.get("funding_rate"),
    )
    if result is None:
        print("  ✗ score_coin returned None")
        return

    print(f"  Conviction:    {result.conviction}")
    print(f"  Signal count:  {result.signal_count}")
    print(f"  Tier:          {result.tier}")
    print(f"  Fired:         {', '.join(result.fired_signals)}")
    print()
    print(f"  Strengths:")
    for k, v in sorted(result.signal_strengths.items(), key=lambda kv: -kv[1]):
        print(f"    {k:<22} {v:.2f}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python diagnose_coin.py <SYMBOL>  [SYMBOL2 ...]")
        print("  e.g. python diagnose_coin.py TVK PARTI WIF COCOS")
        sys.exit(1)

    for base in sys.argv[1:]:
        diagnose(base.upper())
        print()
