# setup_monthly_reset_task.ps1 — run ONCE. Teaches Windows to run the password
# rotation on the 2nd of every month, by itself, forever.
#
#   .\setup_monthly_reset_task.ps1              install (2nd of the month, 09:00)
#   .\setup_monthly_reset_task.ps1 -Time 10:30  install at a different time
#   .\setup_monthly_reset_task.ps1 -Show        what is scheduled / when it next runs
#   .\setup_monthly_reset_task.ps1 -Remove      unschedule it
#
# The task runs as YOU, only while you are logged on, so Windows never has to
# store your account password. If the PC is off on the 2nd, the task is marked
# "start when available" and catches up the next time you log on — and because
# the job records which month it ran for, a late catch-up still counts as that
# month's rotation and won't double-fire.

param(
    [string]$Time = "09:00",
    [string]$TaskName = "Hygiene Monthly Password Reset",
    [switch]$Show,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$script = Join-Path $PSScriptRoot "monthly_password_reset.ps1"

function Show-Task {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $t) {
        Write-Host "Not scheduled. Install it with:  .\setup_monthly_reset_task.ps1" -ForegroundColor Yellow
        return
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "Task:        $TaskName" -ForegroundColor Cyan
    Write-Host "State:       $($t.State)"
    Write-Host "Next run:    $($info.NextRunTime)"
    Write-Host "Last run:    $($info.LastRunTime)  (result $($info.LastTaskResult))"
    Write-Host "Runs:        $script"
    Write-Host ""
    Write-Host "Log:         $(Join-Path $PSScriptRoot 'output\monthly_password_reset.log')"
}

if ($Show)   { Show-Task; exit 0 }

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed '$TaskName'. The monthly reset will no longer run on its own." -ForegroundColor Yellow
    } else {
        Write-Host "'$TaskName' was not scheduled — nothing to remove." -ForegroundColor Yellow
    }
    exit 0
}

if (-not (Test-Path $script)) {
    Write-Host "ERROR: monthly_password_reset.ps1 not found next to this script." -ForegroundColor Red
    exit 1
}
if ($Time -notmatch '^\d{2}:\d{2}$') {
    Write-Host "ERROR: -Time must look like 09:00 (24-hour)." -ForegroundColor Red
    exit 1
}

# schtasks.exe rather than New-ScheduledTaskTrigger: the PowerShell cmdlets have
# no monthly-by-day-of-month trigger, so this is the direct route to "day 2 of
# every month". The settings that the command line can't express are applied
# straight after via the scheduler objects.
$action = '"powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $script

Write-Host "Scheduling '$TaskName' for day 2 of every month at $Time ..." -ForegroundColor Cyan
$out = schtasks.exe /Create /TN "$TaskName" /TR "$action" /SC MONTHLY /D 2 /ST $Time /F 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: could not create the task." -ForegroundColor Red
    $out | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    exit 1
}

# Catch up after a month where the PC happened to be off on the 2nd, and don't
# let a stuck run sit there forever.
#
# Passing a whole task object back with -InputObject fails with "The parameter is
# incorrect" — the object round-trip carries fields Set-ScheduledTask won't take.
# A freshly built settings object applied by name is the route that works.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
                                         -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
                                         -MultipleInstances IgnoreNew
try {
    Set-ScheduledTask -TaskName $TaskName -Settings $settings -ErrorAction Stop | Out-Null
    $applied = (Get-ScheduledTask -TaskName $TaskName).Settings.StartWhenAvailable
    if (-not $applied) { throw "StartWhenAvailable did not stick" }
}
catch {
    # The schedule itself is live, so this is a warning and not a failure — but
    # say so plainly, because the silent version of this is a month that gets
    # skipped entirely whenever the PC is off on the 2nd.
    Write-Host "WARNING: the task is scheduled, but 'run as soon as possible after a" -ForegroundColor Yellow
    Write-Host "         missed start' could not be set ($($_.Exception.Message))." -ForegroundColor Yellow
    Write-Host "         If the PC is off on the 2nd, run .\monthly_password_reset.ps1 by hand." -ForegroundColor Yellow
}

Write-Host "Installed." -ForegroundColor Green
Write-Host ""
Show-Task
Write-Host ""
Write-Host "Test it now without changing anything:" -ForegroundColor Cyan
Write-Host "    .\monthly_password_reset.ps1 -DryRun"
