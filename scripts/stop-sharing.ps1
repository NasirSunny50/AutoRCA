# ============================================================
#  Take the AutoRCA share links offline and stop the local apps.
#  Stops: cloudflared tunnels + the monitor, portal, and health checker.
# ============================================================
Write-Host "Closing Cloudflare tunnels..."
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

Write-Host "Stopping AutoRCA monitor, portal, and health checker..."
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'main\.py|webapp\.py|app\.py' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Write-Host "Done. The public links are now offline."
