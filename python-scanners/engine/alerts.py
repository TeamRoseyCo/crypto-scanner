"""
================================================================================
TELEGRAM ALERT UTILITY
================================================================================
Sends scanner alerts via Telegram bot.

Setup (one-time):
  1. Create a bot via @BotFather → get TELEGRAM_BOT_TOKEN
  2. Send /start to your bot or add it to a group → get TELEGRAM_CHAT_ID
  3. Set env vars (or add to your .bat launcher):
       set TELEGRAM_BOT_TOKEN=
       set TELEGRAM_CHAT_ID=-

If vars are not set, all functions silently no-op — scanners still work fine.
================================================================================
"""

import os
import sys
import requests
import logging

log = logging.getLogger("alerts")

_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

_TELEGRAM_API = "https://api.telegram.org"
_CONFIGURED   = bool(_BOT_TOKEN and _CHAT_ID)


def send_alert(message: str, parse_mode: str = "HTML") -> bool:
    """
    Send a Telegram message.  Returns True on success, False on failure.
    Silent no-op if TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not configured.
    """
    if not _BOT_TOKEN:
        logging.warning("Telegram alerts not configured — TELEGRAM_BOT_TOKEN is not set")
        return False
    if not _CONFIGURED:
        return False

    try:
        r = requests.post(
            f"{_TELEGRAM_API}/bot{_BOT_TOKEN}/sendMessage",
            json={
                "chat_id":    _CHAT_ID,
                "text":       message,
                "parse_mode": parse_mode,
            },
            timeout=10,
        )
        if r.status_code == 200:
            return True
        log.warning(f"Telegram alert failed: HTTP {r.status_code} — {r.text[:200]}")
        return False
    except Exception as e:
        log.warning(f"Telegram alert error: {e}")
        return False


def alert_setup(scanner: str, symbol: str, conviction: int,
                entry: float, stop: float, tp1: float,
                regime: str = "", signals: list | None = None) -> bool:
    """
    Send a structured trade setup alert.
    """
    regime_icon = {"BULL": "🟢", "SIDEWAYS": "🟡", "BEAR": "🔴"}.get(regime, "⚪")
    sig_str = ", ".join(signals[:5]) if signals else "—"
    if signals and len(signals) > 5:
        sig_str += f" +{len(signals)-5} more"

    stop_pct = (stop - entry) / entry * 100
    tp1_pct  = (tp1  - entry) / entry * 100

    msg = (
        f"<b>🚨 {scanner} — {symbol}</b>\n"
        f"Conviction: <b>{conviction}/100</b>  {regime_icon} {regime}\n\n"
        f"Entry:  <code>${entry:,.4f}</code>\n"
        f"Stop:   <code>${stop:,.4f}</code>  ({stop_pct:+.1f}%)\n"
        f"TP1:    <code>${tp1:,.4f}</code>  ({tp1_pct:+.1f}%)\n\n"
        f"Signals: {sig_str}"
    )
    return send_alert(msg)


def alert_watchlist(scanner: str, entries: list) -> bool:
    """
    Send a watchlist summary (list of dicts with symbol + conviction).
    """
    if not entries:
        return False
    lines = [f"<b>👀 {scanner} — Watchlist Update</b>"]
    for e in entries[:8]:
        sym  = e.get("symbol", "?")
        conv = e.get("conviction", 0)
        trend_icon = "↗️" if e.get("trend") == "up" else "↘️" if e.get("trend") == "down" else "➡️"
        lines.append(f"  {trend_icon} <code>{sym:<8}</code>  {conv:.0f}/100")
    return send_alert("\n".join(lines))


def alert_regime(regime: str, btc_7d: float, btc_price: float) -> bool:
    """Alert on regime change."""
    icons = {"BULL": "🟢", "SIDEWAYS": "🟡", "BEAR": "🔴"}
    icon  = icons.get(regime, "⚪")
    msg = (
        f"<b>{icon} Regime: {regime}</b>\n"
        f"BTC: <code>${btc_price:,.0f}</code>  7d: {btc_7d:+.1f}%"
    )
    return send_alert(msg)


def is_configured() -> bool:
    """Return True if Telegram credentials are available."""
    return _CONFIGURED


def send_heartbeat(scanner_name: str, coins_scanned: int,
                   top_setup: str | None = None) -> bool:
    """
    Send a heartbeat Telegram message confirming the scanner is alive.
    Silent no-op if Telegram is not configured.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg = f"✅ {scanner_name} alive | {now} | {coins_scanned} coins scanned"
    if top_setup:
        msg += f" | Top: {top_setup}"
    return send_alert(msg)


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not _CONFIGURED:
        print("Telegram not configured.")
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars.")
        sys.exit(1)

    print("Sending test message...")
    ok = send_alert("✅ <b>Crypto Scanner — Telegram alerts active</b>")
    print("Sent OK" if ok else "FAILED — check token/chat_id")
