# monthly_password_reset.ps1 — the 2nd-of-the-month password rotation.
#
# WHAT IT DOES
#   Requires every team member to choose a NEW password of their own the next
#   time they open the validator. It does NOT pick passwords for them, does not
#   email anything, and nobody (not even whoever runs this) sees their password.
#
# WHAT IT NEVER TOUCHES
#   Products, validations, validation history, the input sheet, product specs.
#   It writes to the users / password_history / job_runs tables and nothing else.
#   It also does not sign anyone out, so unsynced work in a browser stays put.
#
# HOW TO USE
#   Automatic:  set it up once with  .\setup_monthly_reset_task.ps1
#               after that Windows runs it on the 2nd of every month.
#   By hand:    .\monthly_password_reset.ps1            (do it for real)
#               .\monthly_password_reset.ps1 -DryRun    (show who, change nothing)
#               .\monthly_password_reset.ps1 -Status    (who still owes a password)
#
# Running it twice in the same month is harmless: the job records the month it
# ran for and does nothing the second time, so a catch-up run can't re-arm
# someone who has already chosen their new password. -Force overrides that.

param(
    [switch]$DryRun,
    [switch]$Force,
    [switch]$Status,
    [string]$Period = ""
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$logDir = Join-Path $PSScriptRoot "output"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$logFile = Join-Path $logDir "monthly_password_reset.log"

function Write-Log([string]$msg, [string]$colour = "Gray") {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Write-Host $line -ForegroundColor $colour
    Add-Content -Path $logFile -Value $line
}

Write-Log "=== monthly password reset: starting ===" "Cyan"

# The SHARED database, never a local file. The connection string is a secret and
# is never hardcoded: it comes from $env:DATABASE_URL or the git-ignored
# deploy.secrets.ps1 sitting next to this script (same as deploy.ps1 uses).
if (-not $env:DATABASE_URL) {
    $secretsFile = Join-Path $PSScriptRoot "deploy.secrets.ps1"
    if (Test-Path $secretsFile) { . $secretsFile }
}

# Hard stop rather than a fallback. Without this the script would happily run
# against the local SQLite file, print a cheerful success, and leave the team on
# Neon completely unrotated — a silent failure that only shows up as "why was I
# never asked to change my password?" months later.
if (-not $env:DATABASE_URL) {
    Write-Log "ERROR: DATABASE_URL is not set — refusing to run." "Red"
    Write-Log "       Set `$env:DATABASE_URL or create deploy.secrets.ps1 next to this script." "Yellow"
    exit 1
}
if ($env:DATABASE_URL -notmatch '^postgres') {
    Write-Log "ERROR: DATABASE_URL is not a Postgres/Neon URL — refusing to run." "Red"
    exit 1
}

$masked = $env:DATABASE_URL -replace '://[^@]+@', '://***@'
Write-Log "database: $masked"

try {
    if ($Status) {
        $out = if ($Period) { python hygiene_db.py password-reset-status $Period }
               else         { python hygiene_db.py password-reset-status }
    }
    else {
        $cmd = @("hygiene_db.py", "monthly-password-reset")
        if ($DryRun) { $cmd += "--dry-run" }
        if ($Force)  { $cmd += "--force" }
        if ($Period) { $cmd += @("--period", $Period) }
        $out = python @cmd
    }
    $code = $LASTEXITCODE
}
catch {
    Write-Log "FAILED: $($_.Exception.Message)" "Red"
    exit 1
}

foreach ($line in $out) { Write-Log "  $line" }

if ($code -ne 0) {
    Write-Log "FAILED: python exited with code $code" "Red"
    exit $code
}

if (-not $DryRun -and -not $Status) {
    Write-Log "Done. Tell the team: next time you open the validator you'll be asked" "Green"
    Write-Log "to set a new password. Sign in with your CURRENT one, then choose a new" "Green"
    Write-Log "one (8+ characters, and not one of your last 6)." "Green"
}
Write-Log "=== finished ===" "Cyan"
