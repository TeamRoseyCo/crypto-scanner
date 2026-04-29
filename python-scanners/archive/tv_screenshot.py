import urllib.request
import json
import websocket
import base64
import sys
import os

CDP_PORT = 9222

def get_tv_tab():
    with urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json", timeout=5) as r:
        targets = json.loads(r.read())
    tv = [t for t in targets if "tradingview" in t.get("url","").lower() and t.get("type") == "page"]
    if not tv:
        print("No TradingView page tab found")
        sys.exit(1)
    return tv[0]

def screenshot(out_path):
    tab = get_tv_tab()
    ws_url = tab["webSocketDebuggerUrl"]
    print(f"Connecting to {ws_url}")

    ws = websocket.create_connection(ws_url, timeout=10, origin="http://localhost:9222")

    ws.send(json.dumps({"id": 1, "method": "Page.captureScreenshot", "params": {"format": "png", "captureBeyondViewport": False}}))
    resp = json.loads(ws.recv())
    ws.close()

    if "error" in resp:
        print(f"CDP error: {resp['error']}")
        sys.exit(1)

    data = base64.b64decode(resp["result"]["data"])
    with open(out_path, "wb") as f:
        f.write(data)
    print(f"Screenshot saved: {out_path}")

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("TEMP","C:/Temp"), "tv_screenshot.png")
    screenshot(out)
