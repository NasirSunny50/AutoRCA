# ============================================================
#  AutoRCA — share the portal + health checker over the internet
#  Starts the apps, opens two Cloudflare quick tunnels, and wires the
#  in-portal "Health Checker" button to the health checker's public URL.
#
#  NOTE: there is NO authentication — anyone with the link has full access
#  (including creating Jira issues). Only share with people you trust.
# ============================================================
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$hcDir = Join-Path $root 'Health-checker-with-component-names'

# ---- locate python + cloudflared ----
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
if (-not (Test-Path $py)) { $py = (Get-Command python).Source }
$cf = (Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter cloudflared.exe -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
if (-not $cf) { $cf = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source }
if (-not $cf) { Write-Host "[ERROR] cloudflared not found. Install: winget install Cloudflare.cloudflared"; exit 1 }

function Start-Tunnel([int]$port, [string]$name) {
  $log = Join-Path $env:TEMP "cf_$name.log"
  Remove-Item "$log*" -Force -ErrorAction SilentlyContinue
  Start-Process -FilePath $cf -ArgumentList 'tunnel','--url',"http://localhost:$port" `
    -RedirectStandardOutput $log -RedirectStandardError "$log.err" -WindowStyle Hidden | Out-Null
  for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 800
    $txt = ''
    if (Test-Path $log) { $txt += Get-Content $log -Raw -ErrorAction SilentlyContinue }
    if (Test-Path "$log.err") { $txt += Get-Content "$log.err" -Raw -ErrorAction SilentlyContinue }
    $m = [regex]::Match($txt, 'https://[a-z0-9-]+\.trycloudflare\.com')
    if ($m.Success) { return $m.Value }
  }
  return $null
}

Write-Host "Stopping any running instances..."
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'main\.py|webapp\.py|app\.py' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

Write-Host "Starting AutoRCA monitor + health checker..."
Start-Process -FilePath $py -ArgumentList 'main.py' -WorkingDirectory $root -WindowStyle Hidden | Out-Null
Start-Process -FilePath $py -ArgumentList 'app.py'  -WorkingDirectory $hcDir -WindowStyle Hidden | Out-Null
Start-Sleep -Seconds 4

Write-Host "Opening tunnel for the Health Checker (5001)..."
$hcUrl = Start-Tunnel 5001 'hc'
if (-not $hcUrl) { Write-Host "[ERROR] Could not get health-checker tunnel URL."; exit 1 }

# Start the portal with the health-checker button pointing at its public URL.
Write-Host "Starting AutoRCA portal (5000)..."
$env:HEALTH_CHECKER_URL = $hcUrl
Start-Process -FilePath $py -ArgumentList 'webapp.py' -WorkingDirectory $root -WindowStyle Hidden | Out-Null
Start-Sleep -Seconds 4

Write-Host "Opening tunnel for the AutoRCA portal (5000)..."
$mainUrl = Start-Tunnel 5000 'main'
if (-not $mainUrl) { Write-Host "[ERROR] Could not get portal tunnel URL."; exit 1 }

Write-Host ""
Write-Host "============================================================"
Write-Host "  AutoRCA is now shared online (no password - trusted only)"
Write-Host ""
Write-Host "  >> SHARE THIS (AutoRCA portal):"
Write-Host "     $mainUrl"
Write-Host ""
Write-Host "  Health Checker (also linked from the dashboard button):"
Write-Host "     $hcUrl"
Write-Host "============================================================"
Write-Host ""
Write-Host "Keep this window open. Close it (or run Stop) to take the links offline."
