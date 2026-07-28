$previewRoot = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) ".public-preview"
$pidFile = Join-Path $previewRoot "pids.json"

if (-not (Test-Path $pidFile)) {
    Write-Host "No public test session was found."
    exit 0
}

$processIds = Get-Content -Raw $pidFile | ConvertFrom-Json
foreach ($processId in $processIds) {
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath $pidFile -Force
Write-Host "Public test links have been stopped."

