param([string]$Python = 'python', [string]$Model = 'gemini-3.5-flash-lite', [switch]$UseClipboard, [switch]$Explanation)
$ErrorActionPreference = 'Stop'
if ($UseClipboard) {
    $env:GEMINI_API_KEY = ([string](Get-Clipboard -Raw)).Trim()
} else {
    $secretValue = Read-Host 'Paste Gemini API key (hidden; not saved)' -AsSecureString
    $env:GEMINI_API_KEY = [System.Net.NetworkCredential]::new('', $secretValue).Password.Trim()
}
if ($env:GEMINI_API_KEY.Length -lt 20 -or $env:GEMINI_API_KEY -match '\s') {
    Remove-Item Env:GEMINI_API_KEY -ErrorAction SilentlyContinue
    throw 'Key input is too short or contains whitespace. Copy the full key and retry with -UseClipboard.'
}
$env:GEMINI_MODEL_NAME = $Model
try {
    if ($Explanation) {
        & $Python -X utf8 -B (Join-Path $PSScriptRoot 'scripts/check_gemini.py') --explanation
    } else {
        & $Python -X utf8 -B (Join-Path $PSScriptRoot 'scripts/check_gemini.py')
    }
    $resultCode = $LASTEXITCODE
} finally {
    Remove-Item Env:GEMINI_API_KEY -ErrorAction SilentlyContinue
}
exit $resultCode
