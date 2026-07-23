# ============================================================
#  Keep the system awake while sharing is active.
#  Uses SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) so the PC
#  will NOT sleep even when locked. The monitor/display may still turn off —
#  that does not affect the running apps or tunnels. Releasing is automatic
#  when this process exits (stop-sharing kills it).
# ============================================================
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class PowerKeepAwake {
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern uint SetThreadExecutionState(uint esFlags);
}
"@

$ES_CONTINUOUS      = [uint32]0x80000000
$ES_SYSTEM_REQUIRED = [uint32]0x00000001
$flags = $ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED

Write-Host "Keep-awake active: the PC will not sleep while this runs (lock is fine)."
while ($true) {
    # Re-assert every minute as a safety net.
    [PowerKeepAwake]::SetThreadExecutionState($flags) | Out-Null
    Start-Sleep -Seconds 60
}
