param([int]$Port = 8765, [string]$Model = 'gemini-3.5-flash-lite', [string]$Python = 'python', [switch]$UseClipboard)
$ErrorActionPreference = 'Stop'
if ($UseClipboard) {
    $env:GEMINI_API_KEY = ([string](Get-Clipboard -Raw)).Trim()
} elseif (!$env:GEMINI_API_KEY) {
    $secretValue = Read-Host 'Enter Gemini API key (hidden; not saved)' -AsSecureString
    $env:GEMINI_API_KEY = [System.Net.NetworkCredential]::new('', $secretValue).Password
}
if (!$env:GEMINI_API_KEY) { throw 'API key is required.' }
if ($env:GEMINI_API_KEY.Trim().Length -lt 20 -or $env:GEMINI_API_KEY -match '\s') { throw 'Key input is too short or contains whitespace. Copy the full key and retry with -UseClipboard.' }
$env:REVIEW_AI_PROVIDER = 'gemini'
$env:GEMINI_MODEL_NAME = $Model
$env:REVIEW_LLM_MODEL = $Model
$env:REVIEW_ENABLE_OCR = '1'
$env:REVIEW_ENABLE_LLM = '1'
Write-Host 'Gemini API configured. Connection has not been tested. Use public/synthetic documents with the free tier.'
& $Python -X utf8 -B -m uvicorn app:app --app-dir $PSScriptRoot --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
