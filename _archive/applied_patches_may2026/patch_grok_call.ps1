# patch_grok_call.ps1 — Fix the Grok 400 error in scriptgen.py
#
# WHAT THIS FIXES:
#   1. Grok-4-family models (including grok-4-1-fast-non-reasoning) require
#      max_completion_tokens NOT max_tokens. Sending max_tokens → 400 error.
#   2. Old max_tokens=2000 was too low for the weekly script (1000-1300 words
#      = ~2000+ output tokens before JSON overhead). Raised to 8000.
#   3. Timeout raised from 60s → 180s. Some Grok-4 calls take longer.
#   4. 400 errors now print the actual x.ai response body so debugging is
#      easier next time the API changes.
#
# Run from the crypto-scanner root:
#   .\patch_grok_call.ps1
#
# Idempotent: safe to re-run.

$ErrorActionPreference = "Stop"

$scriptgenPath = "C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\crypto scanner\crypto-scanner\YOUTUBE - faceless channel\video_pipeline\scriptgen.py"

if (-not (Test-Path $scriptgenPath)) {
    Write-Host "ERROR: scriptgen.py not found at:" -ForegroundColor Red
    Write-Host "  $scriptgenPath"
    exit 1
}

$content = Get-Content $scriptgenPath -Raw

# Idempotency check — if already patched, exit cleanly
if ($content -match 'max_completion_tokens') {
    Write-Host "Already patched — max_completion_tokens already present." -ForegroundColor Yellow
    exit 0
}

# Backup
$backupPath = $scriptgenPath + ".bak-grokcall"
Copy-Item $scriptgenPath $backupPath -Force
Write-Host "Backup saved: $backupPath" -ForegroundColor Green

# ── Replacement target: the entire _call_grok function body ──────────────────
# We find the existing requests.post(...) block inside _call_grok and replace
# it with an improved version that:
#   - uses max_completion_tokens (Grok-4 requirement)
#   - raises limits to 8000 / timeout 180s
#   - prints API error body on failure

$oldBlock = @'
def _call_grok(api_key: str, model: str, system: str, user_msg: str) -> str:
    """
    Grok via x.ai API (OpenAI-compatible endpoint).

    Free tier: console.x.ai -> API keys -> create key
    $25/month free credit — more than enough for daily scripts.
    """
    import requests

    log.info(f"  Calling Grok ({model})...")
    resp = requests.post(
        "https://api.x.ai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user_msg},
            ],
            "max_tokens": 2000,
            "temperature": 0.7,
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()
'@

$newBlock = @'
def _call_grok(api_key: str, model: str, system: str, user_msg: str) -> str:
    """
    Grok via x.ai API (OpenAI-compatible endpoint).

    Grok-4-family models require max_completion_tokens, not max_tokens.
    Free tier: console.x.ai -> API keys -> create key
    $25/month free credit.
    """
    import requests

    log.info(f"  Calling Grok ({model})...")

    # Use max_completion_tokens for grok-4-family (reasoning models),
    # max_tokens for older grok-3-family.
    if model.startswith("grok-4") or "fast" in model or "reasoning" in model:
        token_param = "max_completion_tokens"
    else:
        token_param = "max_tokens"

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user_msg},
        ],
        token_param: 8000,
        "temperature": 0.7,
    }

    resp = requests.post(
        "https://api.x.ai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=180,  # Grok-4 reasoning calls can be slow
    )

    # On error, surface the actual x.ai error body so we can debug
    if resp.status_code >= 400:
        try:
            err_body = resp.json()
        except Exception:
            err_body = resp.text
        log.error(f"  Grok API {resp.status_code}: {err_body}")
        resp.raise_for_status()

    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()
'@

# Verify the old block actually exists in the file
if (-not ($content -match [regex]::Escape($oldBlock))) {
    Write-Host "ERROR: Could not find the original _call_grok block." -ForegroundColor Red
    Write-Host "The file may have been edited manually. Open scriptgen.py and"
    Write-Host "search for '_call_grok' to apply the changes by hand."
    exit 1
}

# Apply patch
$patched = $content.Replace($oldBlock, $newBlock)
Set-Content -Path $scriptgenPath -Value $patched -NoNewline

Write-Host "Patched! Changes applied:" -ForegroundColor Green
Write-Host "  - max_tokens → max_completion_tokens (for Grok-4 models)" -ForegroundColor Cyan
Write-Host "  - Token limit: 2000 → 8000" -ForegroundColor Cyan
Write-Host "  - Timeout: 60s → 180s" -ForegroundColor Cyan
Write-Host "  - 400 errors now log the API's error body" -ForegroundColor Cyan
Write-Host ""
Write-Host "Verify with:"
Write-Host '  Select-String -Path "$scriptgenPath" -Pattern "max_completion_tokens|token_param"' -ForegroundColor Cyan
