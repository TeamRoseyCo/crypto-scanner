import urllib.request
import json
import sys

CDP_PORT = 9222


def health_check():
    url = f"http://localhost:{CDP_PORT}/json"
    print(f"Connecting to CDP at {url} ...")

    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            targets = json.loads(resp.read())
    except Exception as exc:
        print(f"FAIL: {exc}")
        print("Make sure you ran /tv_launch first and Chrome is open.")
        sys.exit(1)

    tv_tabs = [t for t in targets if "tradingview" in t.get("url", "").lower()]

    print(f"CDP OK — {len(targets)} tab(s) open")

    if tv_tabs:
        tab = tv_tabs[0]
        print(f"TradingView tab: {tab['url']}")
        print(f"WebSocket: {tab['webSocketDebuggerUrl']}")
        print("STATUS: ready")
    else:
        urls = [t.get("url", "") for t in targets]
        print(f"WARNING: TradingView not in open tabs: {urls}")
        print("Navigate to TradingView in the browser and retry.")
        sys.exit(1)


if __name__ == "__main__":
    health_check()
