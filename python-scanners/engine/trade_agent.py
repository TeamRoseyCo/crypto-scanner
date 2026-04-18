"""
ALPHA SIGNAL AGENT
------------------
Loads the latest scanner outputs and opens an interactive chat
with a Claude-powered trading analyst.

Usage:
    python trade_agent.py
    python trade_agent.py --refresh   (re-reads scan files mid-session)
"""

import os
import sys
import time
from pathlib import Path
import anthropic

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent.parent
SCAN_DIR = ROOT / "outputs" / "scanner-results"

SCAN_FILES = {
    "ALPHA SCAN":        "alpha_scan_LATEST.txt",
    "SHORT SCAN":        "short_scan_LATEST.txt",
    "MASTER TRADE PLAN": "master_trade_plan_LATEST.txt",
    "IGNITION RADAR":    "ignition_radar_LATEST.txt",
}

# ── System prompt ──────────────────────────────────────────────────────────
SYSTEM_PROMPT = """
You are an elite crypto signal analyst powered by a live quantitative scanner
that scores 500+ coins daily. Your job is to identify high-conviction trade
setups, size positions correctly, and protect capital above all else.

IDENTITY
You think like a disciplined quant, not a hype trader. Your edge is data —
conviction scores, RS vs BTC, volume structure, momentum signals.
You never guess. If the data doesn't support a trade, you say so clearly.

ACCOUNT
Balance: $95,255 USDT. All cash, no open positions at session start.

MARKET REGIMES
- BULL  (BTC 7d > +5%):  Conviction ≥ 55, full risk
- SIDEWAYS (BTC 7d -5% to +5%): Conviction ≥ 65, half risk, max 2 positions
- BEAR  (BTC 7d < -5%):  Longs off. Shorts only if conviction ≥ 50.

ENTRY RULES
- RSI gate: never enter longs above RSI 72 (momentum already extended)
- ADX > 30 required for valid trend trades
- RS vs BTC 7d ≥ +5% required for alpha longs
- Token must have a Bybit perpetual to be tradeable

POSITION SIZING
- Alpha plays (RS decoupling): 0.75% risk = $725 per trade
- High conviction setups: 1.5% risk = $1,450 per trade
- Max single position: 8% of account ($7,736)
- Max total heat: 4.5% in SIDEWAYS / 9% in BULL

TRADE PLAN FORMAT
Every setup must include:
  Entry price | Stop loss (price + %) | TP1 / TP2 / TP3 (price + % + USDT)
  Position size in USDT | Quantity | Conviction score | Active signals
  Sell splits: 40% at TP1 / 35% at TP2 / 25% at TP3

TRADE MANAGEMENT (non-negotiable)
1. TP1 hit → move stop to breakeven immediately
2. TP2 hit → move stop to TP1
3. Stop fires → cancel ALL remaining TP orders instantly
4. Never average down. Never move a stop wider.
5. BTC drops >5% in 24h → tighten all stops to -5%

NO SETUP RULE
When conviction is below threshold or RSI gate fails, say clearly:
"No qualifying setup. Cash is the position." Never force a trade.

COMMUNICATION STYLE
Precise and brief. No hype. No predictions. Lead with the signal,
follow with the plan. Numbers over words. Tables where useful.
If asked about a coin not in the scanner, say so — no discretionary
calls without data.
""".strip()


# ── Helpers ────────────────────────────────────────────────────────────────
def load_scan_data() -> str:
    sections = []
    for label, filename in SCAN_FILES.items():
        filepath = SCAN_DIR / filename
        if filepath.exists():
            content = filepath.read_text(encoding="utf-8", errors="replace").strip()
            sections.append(f"{'='*60}\n  {label}\n{'='*60}\n{content}")
        else:
            sections.append(f"{'='*60}\n  {label}\n{'='*60}\n[File not found: {filename}]")
    return "\n\n".join(sections)


def build_initial_message(scan_data: str) -> str:
    return (
        "Fresh scanner data loaded. Here is the latest output:\n\n"
        f"{scan_data}\n\n"
        "Analyse all four scans and give me:\n"
        "1. A one-line market context (BTC regime + price)\n"
        "2. Any qualifying setups under current rules (conviction + RSI gate)\n"
        "3. Anything on the watchlist worth monitoring\n"
        "Be concise. Tables preferred."
    )


def print_header():
    print("\n" + "=" * 60)
    print("  ALPHA SIGNAL AGENT  —  Powered by Claude")
    print("  Type 'refresh' to reload scan files")
    print("  Type 'exit' to quit")
    print("=" * 60 + "\n")


# ── Main loop ──────────────────────────────────────────────────────────────
def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("\n[ERROR] ANTHROPIC_API_KEY environment variable not set.")
        print("Run:  setx ANTHROPIC_API_KEY \"sk-ant-your-key-here\"")
        print("Then restart your terminal.\n")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    messages = []

    print_header()
    print("Loading scanner files...")
    scan_data = load_scan_data()

    # Inject scan data as first user turn
    initial_msg = build_initial_message(scan_data)
    messages.append({"role": "user", "content": initial_msg})

    print("Analysing setups...\n")

    for attempt in range(3):
        try:
            response = client.messages.create(
                model="claude-opus-4-6",
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                messages=messages,
            )
            break
        except anthropic.RateLimitError:
            wait = 30 * (2 ** attempt)
            print(f"Rate limited. Waiting {wait}s...")
            time.sleep(wait)
        except Exception as e:
            print(f"API error: {e}")
            if attempt == 2:
                raise
            time.sleep(5)
    reply = response.content[0].text
    messages.append({"role": "assistant", "content": reply})

    print(f"Agent:\n{reply}\n")
    print("-" * 60)

    # Interactive loop
    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nSession ended.")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "bye"):
            print("Session ended.")
            break

        if user_input.lower() == "refresh":
            print("Reloading scan files...")
            scan_data = load_scan_data()
            user_input = (
                "I've just re-run the scanners. Here is the updated data:\n\n"
                f"{scan_data}\n\n"
                "What has changed? Any new qualifying setups?"
            )

        messages.append({"role": "user", "content": user_input})

        for attempt in range(3):
            try:
                response = client.messages.create(
                    model="claude-opus-4-6",
                    max_tokens=2048,
                    system=SYSTEM_PROMPT,
                    messages=messages,
                )
                break
            except anthropic.RateLimitError:
                wait = 30 * (2 ** attempt)
                print(f"Rate limited. Waiting {wait}s...")
                time.sleep(wait)
            except Exception as e:
                print(f"API error: {e}")
                if attempt == 2:
                    raise
                time.sleep(5)
        reply = response.content[0].text
        messages.append({"role": "assistant", "content": reply})

        print(f"\nAgent:\n{reply}\n")
        print("-" * 60)


if __name__ == "__main__":
    main()
