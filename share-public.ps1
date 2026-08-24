$ErrorActionPreference = "Stop"

# Codex and some Windows launchers can expose both Path and PATH.  PowerShell's
# Start-Process treats them as duplicate dictionary keys, so normalize only the
# current process environment before starting npm, Python, and cloudflared.
$processPath = $env:Path
[Environment]::SetEnvironmentVariable("PATH", $null, "Process")
[Environment]::SetEnvironmentVariable("Path", $processPath, "Process")

$portalRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$platformRoot = Join-Path (Split-Path -Parent $portalRoot) "apk-analysis-platform"
$iotRoot = Join-Path (Split-Path -Parent $portalRoot) "ESP-Firmware-Over-The-Air"
$pythonExe = Join-Path $platformRoot ".venv\Scripts\python.exe"
$iotPythonExe = Join-Path $iotRoot ".venv\Scripts\python.exe"
$staticServer = Join-Path $portalRoot "serve_static.py"
$cloudflaredExe = (Get-Command cloudflared.exe -ErrorAction Stop).Source
$previewRoot = Join-Path $portalRoot ".public-preview"
$frontendPreview = Join-Path $previewRoot "frontend"
$iotFrontendPreview = Join-Path $previewRoot "iot-frontend"
$portalPreview = Join-Path $previewRoot "portal"

if (-not (Test-Path $pythonExe)) {
    throw "APK Analysis Platform virtual environment was not found: $pythonExe"
}
if (-not (Test-Path $iotPythonExe)) {
    throw "IoT Platform virtual environment was not found: $iotPythonExe"
}

$frontendRoot = Join-Path $platformRoot "FrontendUI"
$iotFrontendRoot = Join-Path $iotRoot "frontend"
if ($env:SKIP_FRONTEND_BUILD -eq "1") {
    Write-Host "Using the existing APK frontend build..."
    if (-not (Test-Path (Join-Path $frontendRoot "dist\index.html"))) {
        throw "Frontend dist was not found. Run npm.cmd run build first."
    }
} else {
    Write-Host "Building the latest APK frontend..."
    $buildProcess = Start-Process -FilePath "npm.cmd" -ArgumentList "run","build" -WorkingDirectory $frontendRoot -Wait -PassThru -NoNewWindow
    if ($buildProcess.ExitCode -ne 0) {
        throw "Frontend build failed. Fix the build error before creating public links."
    }
}

if ($env:SKIP_FRONTEND_BUILD -eq "1") {
    Write-Host "Using the existing IoT frontend build..."
    if (-not (Test-Path (Join-Path $iotFrontendRoot "dist\index.html"))) {
        throw "IoT frontend dist was not found. Run npm.cmd run build first."
    }
} else {
    Write-Host "Building the latest IoT frontend..."
    $iotBuildProcess = Start-Process -FilePath "npm.cmd" -ArgumentList "run","build" -WorkingDirectory $iotFrontendRoot -Wait -PassThru -NoNewWindow
    if ($iotBuildProcess.ExitCode -ne 0) {
        throw "IoT frontend build failed. Fix the build error before creating public links."
    }
}

function Wait-TunnelUrl {
    param([string]$LogPath)

    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Milliseconds 500
        if (Test-Path $LogPath) {
            $match = Select-String -Path $LogPath -Pattern 'https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com' -AllMatches |
                Select-Object -Last 1
            if ($match) {
                return $match.Matches[-1].Value
            }
        }
    }
    throw "Cloudflare Tunnel did not return a public URL. Check $LogPath"
}

function Start-QuickTunnel {
    param(
        [string]$LocalUrl,
        [string]$LogPath
    )

    for ($attempt = 1; $attempt -le 3; $attempt++) {
        if (Test-Path $LogPath) {
            Remove-Item -LiteralPath $LogPath -Force
        }

        $tunnelProcess = Start-Process -FilePath $cloudflaredExe -ArgumentList 'tunnel','--url',$LocalUrl,'--no-autoupdate' -RedirectStandardError $LogPath -WindowStyle Hidden -PassThru
        try {
            $publicUrl = Wait-TunnelUrl $LogPath
            return [PSCustomObject]@{
                Process = $tunnelProcess
                Url = $publicUrl
            }
        } catch {
            Stop-Process -Id $tunnelProcess.Id -Force -ErrorAction SilentlyContinue
            if ($attempt -eq 3) {
                throw "Cloudflare Tunnel failed after 3 attempts. Check $LogPath"
            }
            Start-Sleep -Seconds 2
        }
    }
}

New-Item -ItemType Directory -Force -Path $previewRoot | Out-Null
if (Test-Path $frontendPreview) {
    Remove-Item -LiteralPath $frontendPreview -Recurse -Force
}
if (Test-Path $portalPreview) {
    Remove-Item -LiteralPath $portalPreview -Recurse -Force
}
if (Test-Path $iotFrontendPreview) {
    Remove-Item -LiteralPath $iotFrontendPreview -Recurse -Force
}
Copy-Item (Join-Path $frontendRoot "dist") $frontendPreview -Recurse -Force
Copy-Item (Join-Path $iotFrontendRoot "dist") $iotFrontendPreview -Recurse -Force
New-Item -ItemType Directory -Force -Path $portalPreview | Out-Null
Copy-Item (Join-Path $portalRoot "assets") $portalPreview -Recurse -Force
Copy-Item (Join-Path $portalRoot "index.html"), (Join-Path $portalRoot "platform.html"), (Join-Path $portalRoot "workflow.html"), (Join-Path $portalRoot "contact.html"), (Join-Path $portalRoot "iot-system.html"), (Join-Path $portalRoot "apk-system.html"), (Join-Path $portalRoot "system.html"), (Join-Path $portalRoot "styles.css"), (Join-Path $portalRoot "script.js"), (Join-Path $portalRoot "workspace.js"), (Join-Path $portalRoot "config.js"), (Join-Path $portalRoot "favicon.ico") $portalPreview -Force

$env:CELERY_TASK_ALWAYS_EAGER = "1"
$env:ALLOW_TUNNEL_ORIGINS = "1"
$apiProcess = Start-Process -FilePath $pythonExe -ArgumentList '-m','uvicorn','apps.api.main:app','--host','127.0.0.1','--port','8100' -WorkingDirectory (Join-Path $platformRoot 'apk-platform') -WindowStyle Hidden -PassThru

$bundle = Get-ChildItem (Join-Path $frontendPreview 'assets\index-*.js') | Select-Object -First 1
$frontendProcess = Start-Process -FilePath $pythonExe -ArgumentList $staticServer,'--directory',$frontendPreview,'--port','5100','--bind','127.0.0.1','--proxy-api','http://127.0.0.1:8100' -WorkingDirectory $frontendPreview -WindowStyle Hidden -PassThru
$frontendLog = Join-Path $previewRoot "frontend-tunnel.log"
$frontendTunnelResult = Start-QuickTunnel 'http://127.0.0.1:5100' $frontendLog
$frontendTunnel = $frontendTunnelResult.Process
$frontendUrl = $frontendTunnelResult.Url

$bundleText = [System.IO.File]::ReadAllText($bundle.FullName)
$bundleText = $bundleText.Replace('http://127.0.0.1:8000', $frontendUrl)
if (-not $bundleText.Contains($frontendUrl)) {
    throw "The frontend API URL could not be configured."
}
[System.IO.File]::WriteAllText($bundle.FullName, $bundleText, [System.Text.UTF8Encoding]::new($false))

$jwtBytes = New-Object byte[] 48
$jwtGenerator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$jwtGenerator.GetBytes($jwtBytes)
$jwtGenerator.Dispose()
$env:JWT_SECRET = [Convert]::ToBase64String($jwtBytes)
$iotApiProcess = Start-Process -FilePath $iotPythonExe -ArgumentList '-m','uvicorn','main:app','--app-dir','backend','--host','127.0.0.1','--port','8200' -WorkingDirectory $iotRoot -WindowStyle Hidden -PassThru
$iotFrontendProcess = Start-Process -FilePath $iotPythonExe -ArgumentList $staticServer,'--directory',$iotFrontendPreview,'--port','5200','--bind','127.0.0.1','--proxy-api','http://127.0.0.1:8200','--proxy-prefix','/backend/','--proxy-strip-prefix' -WorkingDirectory $iotFrontendPreview -WindowStyle Hidden -PassThru
$iotFrontendLog = Join-Path $previewRoot "iot-frontend-tunnel.log"
$iotFrontendTunnelResult = Start-QuickTunnel 'http://127.0.0.1:5200' $iotFrontendLog
$iotFrontendTunnel = $iotFrontendTunnelResult.Process
$iotFrontendUrl = $iotFrontendTunnelResult.Url

$configPath = Join-Path $portalPreview 'config.js'
$configText = [System.IO.File]::ReadAllText($configPath)
$configText = [regex]::Replace($configText, 'apkFrontendUrl:\s*"[^"]+"', "apkFrontendUrl: `"$frontendUrl`"")
$configText = [regex]::Replace($configText, 'iotSystemUrl:\s*"[^"]+"', "iotSystemUrl: `"$iotFrontendUrl`"")
[System.IO.File]::WriteAllText($configPath, $configText, [System.Text.UTF8Encoding]::new($false))

$portalProcess = Start-Process -FilePath $pythonExe -ArgumentList $staticServer,'--directory',$portalPreview,'--port','8101','--bind','127.0.0.1' -WorkingDirectory $portalPreview -WindowStyle Hidden -PassThru
$portalLog = Join-Path $previewRoot "portal-tunnel.log"
$portalTunnelResult = Start-QuickTunnel 'http://127.0.0.1:8101' $portalLog
$portalTunnel = $portalTunnelResult.Process
$portalUrl = $portalTunnelResult.Url

$bundleText = [System.IO.File]::ReadAllText($bundle.FullName)
$bundleText = $bundleText.Replace('http://127.0.0.1:8080', $portalUrl)
if (-not $bundleText.Contains($portalUrl)) {
    throw "The frontend portal navigation URL could not be configured."
}
[System.IO.File]::WriteAllText($bundle.FullName, $bundleText, [System.Text.UTF8Encoding]::new($false))

$processIds = @($apiProcess.Id, $frontendProcess.Id, $frontendTunnel.Id, $iotApiProcess.Id, $iotFrontendProcess.Id, $iotFrontendTunnel.Id, $portalProcess.Id, $portalTunnel.Id)
$processIds | ConvertTo-Json | Set-Content (Join-Path $previewRoot 'pids.json') -Encoding UTF8

Write-Host ""
Write-Host "Public test site is ready:" -ForegroundColor Green
Write-Host $portalUrl -ForegroundColor Cyan
Write-Host ""
Write-Host "Keep this window and your computer running while teammates test."
Write-Host "Run stop-public.bat when testing is finished."
