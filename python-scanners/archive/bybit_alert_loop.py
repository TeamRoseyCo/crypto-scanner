"""
================================================================================
BYBIT ALERT LOOP v1.0
================================================================================
Runs bybit_radar every N minutes. Fires a Telegram alert the moment a new
high-score setup appears — giving early warning before the pump.

Why this works:
  OI builds BEFORE price moves. Funding goes negative BEFORE the squeeze.
  This loop catches those signals at ignition, not after.

Usage:
  python bybit_alert_loop.py                  # 2-min interval, threshold 7.0
  python bybit_alert_loop.py --interval 3     # every 3 minutes
  python bybit_alert_loop.py --threshold 8    # stricter filter
  python bybit_alert_loop.py --no-telegram    # log only, no Telegram

Alert logic:
  - Score >= threshold  → alert immediately
  - Same coin cooldown  → 3 hours (no spam)
  - Re-alert if score improves 2+ points OR OI jumps another 50%+
  - Heartbeat every 30 min to confirm loop is alive
================================================================================
"""

import os
import sys
import time
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path

# Ensure engine dir is on path for sibling imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "7665303397:AAHDXF0giiuTNCfbjdimfTthDp2keTnTGtA")
os.environ.setdefault("TELEGRAM_CHAT_ID",   "1287299443")

import bybit_radar
from alerts import send_alert, is_configured

# ── Config ────────────────────────────────────────────────────────────────────
DEFAULT_INTERVAL_SEC  = 600    # 10 minutes
DEFAULT_THRESHOLD     = 7.0    # score >= this → alert
COOLDOWN_HOURS        = 3      # re-alert same coin only after this window
RESCORE_DELTA         = 2.0    # re-alert early if score jumps by this much
RESCORE_OI_DELTA      = 50.0   # re-alert early if OI jumps another 50%+
HEARTBEAT_MINUTES     = 30     # send proof-of-life this often

# ── Paths / logging ───────────────────────────────────────────────────────────
_ROOT    = Path(__file__).resolve().parent.parent.parent
_LOG_DIR = _ROOT / "outputs" / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

_log_file = _LOG_DIR / f"bybit_alert_loop_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("alert_loop")


# ─────────────────────────────────────────────────────────────────────────────
# ALERT STATE  —  tracks what's been sent so we don't spam
# ─────────────────────────────────────────────────────────────────────────────

class AlertState:
    def __init__(self):
        self._sent: dict[str, dict] = {}   # symbol → {time, score, oi_pct}

    def should_alert(self, symbol: str, score: float, oi_pct: float) -> bool:
        if symbol not in self._sent:
            return True
        last = self._sent[symbol]
        age  = datetime.now() - last["time"]
        if age >= timedelta(hours=COOLDOWN_HOURS):
            return True
        # Re-alert early on significant new development
        if score    >= last["score"]  + RESCORE_DELTA:
            return True
        if oi_pct   >= last["oi_pct"] + RESCORE_OI_DELTA:
            return True
        return False

    def mark(self, symbol: str, score: float, oi_pct: float):
        self._sent[symbol] = {"time": datetime.now(), "score": score, "oi_pct": oi_pct}

    def purge_old(self):
        cutoff = datetime.now() - timedelta(hours=24)
        self._sent = {k: v for k, v in self._sent.items() if v["time"] > cutoff}


# ─────────────────────────────────────────────────────────────────────────────
# MESSAGE BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def _alert_msg(r: dict, scan_num: int) -> str:
    base      = r["base"]
    price     = r["price"]
    score     = r["score"]
    oi_pct    = r["oi_change_pct"] * 100
    funding   = r["funding_rate"]  * 100
    p1h       = r["price_1h_pct"]  * 100
    oi_m      = r["oi_value"] / 1_000_000
    sigs      = r["active_signals"]
    warns     = r["warnings"]

    oi_icon  = "🚀" if oi_pct >= 100 else "📈" if oi_pct >= 30 else "➡️"
    fr_icon  = "🔥" if funding <= -0.05 else "✅" if funding < 0 else ("⚠️" if funding > 0.05 else "➡️")
    p_icon   = "📈" if p1h >= 2 else "↗️" if p1h >= 1 else ("↘️" if p1h < 0 else "➡️")
    sig_str  = " | ".join(s.split("(")[0] for s in sigs[:5])

    lines = [
        f"<b>⚡ BYBIT ALERT — {base}</b>",
        f"Score: <b>{score:.1f}</b>  |  Signals: {r['signal_count']}",
        "",
        f"Price:    <code>${price:,.5f}</code>  {p_icon} {p1h:+.2f}%/h",
        f"OI:       <code>${oi_m:.1f}M</code>  {oi_icon} {oi_pct:+.1f}%",
        f"Funding:  <code>{funding:+.4f}%/8h</code>  {fr_icon}",
        "",
        f"Signals: <i>{sig_str}</i>",
    ]
    if warns:
        lines.append(f"⚠️ {' | '.join(warns)}")
    lines.append(f"\n<i>Scan #{scan_num} — {datetime.now().strftime('%H:%M:%S')} UTC</i>")
    return "\n".join(lines)


def _heartbeat_msg(results: list, threshold: float, scan_num: int, interval: int) -> str:
    top = [r for r in results if r["score"] >= threshold][:5]
    lines = [
        "<b>✅ Bybit Alert Loop — Heartbeat</b>",
        f"Scan #{scan_num}  |  {datetime.now().strftime('%H:%M')} UTC",
        f"Interval: {interval}s  |  Threshold: {threshold:.0f}  |  Universe: 430 pairs",
        "",
    ]
    if top:
        lines.append(f"<b>Active setups (≥{threshold:.0f}):</b>")
        for r in top:
            lines.append(
                f"  • <code>{r['base']:<8}</code>"
                f"  score={r['score']:.1f}"
                f"  OI {r['oi_change_pct']*100:+.0f}%"
                f"  1h {r['price_1h_pct']*100:+.1f}%"
                f"  fund {r['funding_rate']*100:+.3f}%"
            )
    else:
        lines.append("No active setups above threshold — market quiet.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────────────────────────────────────

def run_loop(interval_sec: int, threshold: float, use_telegram: bool):
    log.info("=" * 64)
    log.info(f"BYBIT ALERT LOOP  interval={interval_sec}s  threshold={threshold}")
    log.info(f"Telegram: {'ON' if use_telegram else 'OFF'}")
    log.info("=" * 64)

    state          = AlertState()
    scan_num       = 0
    last_heartbeat = datetime.now() - timedelta(hours=1)   # trigger HB on first scan

    if use_telegram:
        send_alert(
            f"<b>🟢 Bybit Alert Loop started</b>\n"
            f"Interval: {interval_sec}s  |  Threshold score: {threshold}\n"
            f"Monitoring 430 Bybit perp pairs — you'll be notified at ignition."
        )

    while True:
        loop_start = datetime.now()
        scan_num  += 1

        try:
            log.info(f"--- Scan #{scan_num} at {loop_start.strftime('%H:%M:%S')} ---")
            results = bybit_radar.run(telegram=False)

            if not results:
                log.warning("  No results returned — skipping alert check.")
            else:
                top_score = results[0]["score"]
                log.info(f"  Top score this scan: {top_score:.1f}")

                fired = 0
                for r in results:
                    if r["score"] < threshold:
                        break   # sorted descending, no point continuing
                    sym    = r["base"]
                    oi_pct = r["oi_change_pct"] * 100

                    if state.should_alert(sym, r["score"], oi_pct):
                        log.info(
                            f"  🚨 ALERT  {sym:<8}  score={r['score']:.1f}"
                            f"  OI={oi_pct:+.1f}%"
                            f"  1h={r['price_1h_pct']*100:+.2f}%"
                            f"  fund={r['funding_rate']*100:+.4f}%"
                        )
                        if use_telegram:
                            send_alert(_alert_msg(r, scan_num))
                        state.mark(sym, r["score"], oi_pct)
                        fired += 1

                if fired == 0:
                    log.info(f"  No new alerts this scan.")

            # Heartbeat
            minutes_since_hb = (datetime.now() - last_heartbeat).total_seconds() / 60
            if minutes_since_hb >= HEARTBEAT_MINUTES:
                log.info("  Sending heartbeat.")
                if use_telegram and results:
                    send_alert(_heartbeat_msg(results, threshold, scan_num, interval_sec))
                last_heartbeat = datetime.now()

            state.purge_old()

        except KeyboardInterrupt:
            log.info("Stopped by user (Ctrl+C).")
            if use_telegram:
                send_alert("🔴 <b>Bybit Alert Loop stopped.</b>")
            sys.exit(0)
        except Exception as exc:
            log.error(f"Scan #{scan_num} error: {exc}", exc_info=True)

        # Wait out remainder of interval
        elapsed   = (datetime.now() - loop_start).total_seconds()
        sleep_for = max(5.0, interval_sec - elapsed)
        log.info(f"  Sleeping {sleep_for:.0f}s until next scan.")
        time.sleep(sleep_for)


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Bybit OI/Funding Alert Loop")
    parser.add_argument("--interval",    type=int,   default=DEFAULT_INTERVAL_SEC,
                        help=f"Seconds between scans (default: {DEFAULT_INTERVAL_SEC})")
    parser.add_argument("--threshold",   type=float, default=DEFAULT_THRESHOLD,
                        help=f"Minimum score to alert (default: {DEFAULT_THRESHOLD})")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Disable Telegram — log to console only")
    args = parser.parse_args()

    telegram_on = (not args.no_telegram) and is_configured()

    if not args.no_telegram and not is_configured():
        log.warning("Telegram not configured — running in log-only mode.")

    try:
        run_loop(
            interval_sec = args.interval,
            threshold    = args.threshold,
            use_telegram = telegram_on,
        )
    except KeyboardInterrupt:
        log.info("Exited.")
