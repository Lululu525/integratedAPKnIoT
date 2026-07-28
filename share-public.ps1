$ErrorActionPreference = "Stop"

$portalRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$platformRoot = Join-Path (Split-Path -Parent $portalRoot) "apk-analysis-platform"
$pythonExe = Join-Path $platformRoot ".venv\Scripts\python.exe"
$cloudflaredExe = (Get-Command cloudflared.exe -ErrorAction Stop).Source
$previewRoot = Join-Path $portalRoot ".public-preview"
$frontendPreview = Join-Path $previewRoot "frontend"
$portalPreview = Join-Path $previewRoot "portal"

if (-not (Test-Path $pythonExe)) {
    throw "APK Analysis Platform virtual environment was not found: $pythonExe"
}

function Wait-TunnelUrl {
    param([string]$LogPath)

    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Milliseconds 500
        if (Test-Path $LogPath) {
            $match = Select-String -Path $LogPath -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -AllMatches |
                Select-Object -Last 1
            if ($match) {
                return $match.Matches[-1].Value
            }
        }
    }
    throw "Cloudflare Tunnel did not return a public URL. Check $LogPath"
}

New-Item -ItemType Directory -Force -Path $previewRoot | Out-Null
Copy-Item (Join-Path $platformRoot "FrontendUI\dist") $frontendPreview -Recurse -Force
New-Item -ItemType Directory -Force -Path $portalPreview | Out-Null
Copy-Item (Join-Path $portalRoot "assets") $portalPreview -Recurse -Force
Copy-Item (Join-Path $portalRoot "index.html"), (Join-Path $portalRoot "iot-system.html"), (Join-Path $portalRoot "apk-system.html"), (Join-Path $portalRoot "styles.css"), (Join-Path $portalRoot "script.js"), (Join-Path $portalRoot "config.js") $portalPreview -Force

$env:CELERY_TASK_ALWAYS_EAGER = "1"
$env:ALLOW_TUNNEL_ORIGINS = "1"
$apiProcess = Start-Process -FilePath $pythonExe -ArgumentList '-m','uvicorn','apps.api.main:app','--host','127.0.0.1','--port','8100' -WorkingDirectory (Join-Path $platformRoot 'apk-platform') -WindowStyle Hidden -PassThru

$apiLog = Join-Path $previewRoot "api-tunnel.log"
$apiTunnel = Start-Process -FilePath $cloudflaredExe -ArgumentList 'tunnel','--url','http://127.0.0.1:8100','--no-autoupdate' -RedirectStandardError $apiLog -WindowStyle Hidden -PassThru
$apiUrl = Wait-TunnelUrl $apiLog

$bundle = Get-ChildItem (Join-Path $frontendPreview 'assets\index-*.js') | Select-Object -First 1
$bundleText = [System.IO.File]::ReadAllText($bundle.FullName)
$bundleText = [regex]::Replace($bundleText, 'http://(?:127\.0\.0\.1|localhost):\d+', $apiUrl)
[System.IO.File]::WriteAllText($bundle.FullName, $bundleText, [System.Text.UTF8Encoding]::new($false))

$frontendProcess = Start-Process -FilePath $pythonExe -ArgumentList '-m','http.server','5100','--bind','127.0.0.1' -WorkingDirectory $frontendPreview -WindowStyle Hidden -PassThru
$frontendLog = Join-Path $previewRoot "frontend-tunnel.log"
$frontendTunnel = Start-Process -FilePath $cloudflaredExe -ArgumentList 'tunnel','--url','http://127.0.0.1:5100','--no-autoupdate' -RedirectStandardError $frontendLog -WindowStyle Hidden -PassThru
$frontendUrl = Wait-TunnelUrl $frontendLog

$configPath = Join-Path $portalPreview 'config.js'
$configText = [System.IO.File]::ReadAllText($configPath)
$configText = [regex]::Replace($configText, 'apkFrontendUrl:\s*"[^"]+"', "apkFrontendUrl: `"$frontendUrl`"")
[System.IO.File]::WriteAllText($configPath, $configText, [System.Text.UTF8Encoding]::new($false))

$portalProcess = Start-Process -FilePath $pythonExe -ArgumentList '-m','http.server','8101','--bind','127.0.0.1' -WorkingDirectory $portalPreview -WindowStyle Hidden -PassThru
$portalLog = Join-Path $previewRoot "portal-tunnel.log"
$portalTunnel = Start-Process -FilePath $cloudflaredExe -ArgumentList 'tunnel','--url','http://127.0.0.1:8101','--no-autoupdate' -RedirectStandardError $portalLog -WindowStyle Hidden -PassThru
$portalUrl = Wait-TunnelUrl $portalLog

$processIds = @($apiProcess.Id, $apiTunnel.Id, $frontendProcess.Id, $frontendTunnel.Id, $portalProcess.Id, $portalTunnel.Id)
$processIds | ConvertTo-Json | Set-Content (Join-Path $previewRoot 'pids.json') -Encoding UTF8

Write-Host ""
Write-Host "Public test site is ready:" -ForegroundColor Green
Write-Host $portalUrl -ForegroundColor Cyan
Write-Host ""
Write-Host "Keep this window and your computer running while teammates test."
Write-Host "Run stop-public.bat when testing is finished."

