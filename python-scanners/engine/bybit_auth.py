"""
================================================================================
BYBIT AUTHENTICATED API  — shared utility
================================================================================
Provides authenticated calls to Bybit's private API endpoints.

Setup (one-time):
  Set environment variables before running any scanner:
    set BYBIT_API_KEY=1GECtl5qxu33yvHbnQ
    set BYBIT_API_SECRET=HJ54DDAyCDbyQSaHr65HVaGvx4gChopA4jgp

  Or add them to a .env file and load with python-dotenv.

Functions:
  fetch_live_balance(coin="USDT") -> float | None
      Returns live wallet balance for the given coin from Bybit Unified account.
      Returns None if credentials are missing or API call fails.

  fetch_live_balance_with_fallback(fallback: float, coin="USDT") -> float
      Same as above, but returns `fallback` if live fetch fails.
      Use this in scanners so they still run without credentials.
================================================================================
"""

import hashlib
import hmac
import logging
import os
import time

import requests

log = logging.getLogger(__name__)

BYBIT_API   = "https://api.bybit.com"
RECV_WINDOW = "5000"


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL — signature builder
# ─────────────────────────────────────────────────────────────────────────────

def _sign(api_key: str, api_secret: str, timestamp: str, params: str) -> dict:
    """Build Bybit v5 HMAC-SHA256 signed headers."""
    payload = timestamp + api_key + RECV_WINDOW + params
    signature = hmac.new(
        api_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-BAPI-API-KEY":     api_key,
        "X-BAPI-TIMESTAMP":   timestamp,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN":        signature,
        "Content-Type":       "application/json",
    }


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC
# ─────────────────────────────────────────────────────────────────────────────

def fetch_live_balance(coin: str = "USDT") -> float | None:
    """
    Fetch live wallet balance from Bybit Unified Trading Account.

    Returns the total equity for `coin`, or None if:
      - BYBIT_API_KEY / BYBIT_API_SECRET env vars are not set
      - API call fails or returns an error

    Args:
        coin: The coin to check balance for (default: "USDT")

    Returns:
        float balance, or None on failure
    """
    api_key    = os.environ.get("BYBIT_API_KEY",    "1GECtl5qxu33yvHbnQ")
    api_secret = os.environ.get("BYBIT_API_SECRET", "HJ54DDAyCDbyQSaHr65HVaGvx4gChopA4jgp")

    if not api_key or not api_secret:
        log.debug("BYBIT_API_KEY / BYBIT_API_SECRET not set — skipping live balance fetch")
        return None

    endpoint   = "/v5/account/wallet-balance"
    query      = f"accountType=UNIFIED&coin={coin}"
    timestamp  = str(int(time.time() * 1000))
    headers    = _sign(api_key, api_secret, timestamp, query)

    for attempt in range(3):
        try:
            r = requests.get(
                f"{BYBIT_API}{endpoint}?{query}",
                headers=headers,
                timeout=10,
            )
            if r.status_code != 200:
                log.warning(f"Bybit balance API HTTP {r.status_code} (attempt {attempt+1})")
                time.sleep(2 ** attempt)
                continue

            data = r.json()
            if data.get("retCode") != 0:
                log.warning(f"Bybit balance API error: {data.get('retMsg')} (attempt {attempt+1})")
                time.sleep(2 ** attempt)
                continue

            # Navigate: result.list[0].coin[].equity  (UNIFIED account)
            accounts = data.get("result", {}).get("list", [])
            if not accounts:
                log.warning("Bybit balance: empty account list")
                return None

            for coin_entry in accounts[0].get("coin", []):
                if coin_entry.get("coin") == coin:
                    equity = coin_entry.get("equity", "0")
                    balance = float(equity)
                    log.info(f"  Live Bybit balance: {balance:,.2f} {coin}")
                    return balance

            # coin not found in list — try totalEquity at account level
            total = accounts[0].get("totalEquity", "")
            if total:
                balance = float(total)
                log.info(f"  Live Bybit balance (totalEquity): {balance:,.2f} {coin}")
                return balance

            log.warning(f"Bybit balance: {coin} not found in wallet")
            return None

        except requests.exceptions.Timeout:
            log.warning(f"Bybit balance API timeout (attempt {attempt+1})")
            time.sleep(2 ** attempt)
        except Exception as e:
            log.warning(f"Bybit balance API exception: {e} (attempt {attempt+1})")
            time.sleep(2 ** attempt)

    log.error("Bybit balance fetch failed after 3 attempts — using fallback")
    return None


def fetch_live_balance_with_fallback(fallback: float, coin: str = "USDT") -> float:
    """
    Fetch live balance, returning `fallback` if credentials are missing or fetch fails.

    This is the safe version for scanner use — scanners still run without credentials.

    Args:
        fallback: Value to use if live fetch fails (usually the hardcoded config value)
        coin:     Coin to check (default: "USDT")

    Returns:
        Live balance if available, otherwise fallback
    """
    live = fetch_live_balance(coin)
    if live is not None and live > 0:
        return live
    if not (os.environ.get("BYBIT_API_KEY") and os.environ.get("BYBIT_API_SECRET")):
        log.debug(f"  No Bybit credentials — using config balance: ${fallback:,.2f}")
    else:
        log.warning(f"  Live balance unavailable — using config balance: ${fallback:,.2f}")
    return fallback
