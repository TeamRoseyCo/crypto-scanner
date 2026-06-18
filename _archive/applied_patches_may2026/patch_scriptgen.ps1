# patch_scriptgen.ps1 — One-time patch to raise Gemini maxOutputTokens
#
# WHY: Gemini's default 4096 token limit gets exceeded by the weekly
# pipeline's longer 6-8 minute scripts, causing JSON truncation and the
# "Script has no segments" error.
#
# Run this ONCE from PowerShell in the crypto-scanner root:
#   .\patch_scriptgen.ps1
#
# It edits ONE line in scriptgen.py:
#   "maxOutputTokens": 4096   →   "maxOutputTokens": 8192

$ErrorActionPreference = "Stop"

$scriptgenPath = "C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\crypto scanner\crypto-scanner\YOUTUBE - faceless channel\video_pipeline\scriptgen.py"

if (-not (Test-Path $scriptgenPath)) {
    Write-Host "ERROR: scriptgen.py not found at expected path:" -ForegroundColor Red
    Write-Host "  $scriptgenPath"
    exit 1
}

# Read current content
$content = Get-Content $scriptgenPath -Raw

# Check if already patched
if ($content -match '"maxOutputTokens":\s*8192') {
    Write-Host "Already patched — maxOutputTokens is already 8192. Nothing to do." -ForegroundColor Yellow
    exit 0
}

if (-not ($content -match '"maxOutputTokens":\s*4096')) {
    Write-Host "WARNING: Could not find 'maxOutputTokens: 4096' in scriptgen.py." -ForegroundColor Yellow
    Write-Host "The file may have been edited or the value is already different."
    Write-Host "Manual fix: open scriptgen.py and change 'maxOutputTokens' to 8192."
    exit 1
}

# Make a backup
$backupPath = $scriptgenPath + ".bak"
Copy-Item $scriptgenPath $backupPath -Force
Write-Host "Backup saved: $backupPath" -ForegroundColor Green

# Apply patch
$patched = $content -replace '"maxOutputTokens":\s*4096', '"maxOutputTokens": 8192'
Set-Content -Path $scriptgenPath -Value $patched -NoNewline

Write-Host "Patched! maxOutputTokens raised from 4096 to 8192." -ForegroundColor Green
Write-Host ""
Write-Host "Verify with:"
Write-Host '  Select-String -Path "$scriptgenPath" -Pattern "maxOutputTokens"' -ForegroundColor Cyan
