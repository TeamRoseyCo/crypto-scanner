# patch_grok_model.ps1 — Update deprecated Grok model name
#
# WHY: scriptgen.py defaults to "grok-3-mini" which xAI deprecated. The
# current cost-effective model is "grok-4-1-fast-non-reasoning" — same
# OpenAI-compatible Chat Completions endpoint, drop-in replacement.
#
# Run from the crypto-scanner root:
#   .\patch_grok_model.ps1
#
# Idempotent: safe to run multiple times.

$ErrorActionPreference = "Stop"

$scriptgenPath = "C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\crypto scanner\crypto-scanner\YOUTUBE - faceless channel\video_pipeline\scriptgen.py"

if (-not (Test-Path $scriptgenPath)) {
    Write-Host "ERROR: scriptgen.py not found at expected path." -ForegroundColor Red
    exit 1
}

$content = Get-Content $scriptgenPath -Raw

if ($content -match 'grok-4-1-fast-non-reasoning') {
    Write-Host "Already patched — Grok model is already grok-4-1-fast-non-reasoning." -ForegroundColor Yellow
    exit 0
}

if (-not ($content -match 'grok-3-mini')) {
    Write-Host "WARNING: Could not find 'grok-3-mini' in scriptgen.py." -ForegroundColor Yellow
    Write-Host "The file may have been edited manually. Check the Grok model name yourself."
    exit 1
}

# Backup
$backupPath = $scriptgenPath + ".bak-grok"
Copy-Item $scriptgenPath $backupPath -Force
Write-Host "Backup saved: $backupPath" -ForegroundColor Green

# Patch
$patched = $content -replace 'grok-3-mini', 'grok-4-1-fast-non-reasoning'
Set-Content -Path $scriptgenPath -Value $patched -NoNewline

Write-Host "Patched! Grok model updated:" -ForegroundColor Green
Write-Host "  grok-3-mini  →  grok-4-1-fast-non-reasoning" -ForegroundColor Cyan
Write-Host ""
Write-Host "Verify with:"
Write-Host '  Select-String -Path $scriptgenPath -Pattern "grok-"' -ForegroundColor Cyan
