import subprocess
import os
import sys
import glob
import tempfile

DEBUG_PORT = 9222
TV_URL = "https://www.tradingview.com"

# Edge preferred (user's normal TV browser), Chrome as fallback
BROWSER_PATHS = [
    (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "Edge"),
    (r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", "Edge"),
    (r"C:\Program Files\Google\Chrome\Application\chrome.exe", "Chrome"),
    (r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", "Chrome"),
    (os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"), "Chrome MSIX"),
]


def find_browser():
    msix_dir = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps")
    for pattern in ["msedge.exe", "*/msedge.exe"]:
        for match in glob.glob(os.path.join(msix_dir, pattern)):
            if os.path.exists(match):
                return match, "Edge MSIX"

    for path, name in BROWSER_PATHS:
        if os.path.exists(path):
            return path, name

    return None, None


def launch():
    browser_path, browser_name = find_browser()
    if not browser_path:
        print("ERROR: No supported browser found.")
        sys.exit(1)

    print(f"Browser: {browser_name} — {browser_path}")

    profile_dir = os.path.join(tempfile.gettempdir(), "tv_cdp_profile")
    os.makedirs(profile_dir, exist_ok=True)

    cmd = [
        browser_path,
        f"--remote-debugging-port={DEBUG_PORT}",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        TV_URL,
    ]

    subprocess.Popen(cmd)
    print(f"TradingView launched — CDP at http://localhost:{DEBUG_PORT}")
    print("Run /tv_health_check to verify the connection.")


if __name__ == "__main__":
    launch()
