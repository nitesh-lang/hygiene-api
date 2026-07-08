# deploy.ps1 — push the latest crawl + input sheet to the shared Render database.
#
# HOW TO USE:
#   1. Save the latest input sheet as:  output\All Brands Hygiene Input file - 2026.xlsx
#      (same name, overwrite the old one) and CLOSE it in Excel.
#   2. In PowerShell, from this folder, run:   .\deploy.ps1
#   3. Check the output: 'uploaded_at' = now  ->  new sheet is live.
#                        'crawl_runs' went up ->  new crawl is live.
#   4. Tell the team to open https://hygiene-validator.onrender.com/ and hard-refresh (Ctrl+Shift+R).

$ErrorActionPreference = "Stop"

# Always run from the folder this script lives in.
Set-Location -Path $PSScriptRoot

# Point at the SHARED Render database (not a local file).
# The connection string is a SECRET — it is NEVER hardcoded here.
# Provide it in ONE of two ways (both kept OUT of git):
#   1. Set an environment variable before running:
#        $env:DATABASE_URL = "postgresql://USER:PASSWORD@HOST/DBNAME"
#   2. OR create a local, git-ignored file next to this script named
#      "deploy.secrets.ps1" containing that single line above. It will be
#      loaded automatically. (deploy.secrets.ps1 is in .gitignore.)
if (-not $env:DATABASE_URL) {
    $secretsFile = Join-Path $PSScriptRoot "deploy.secrets.ps1"
    if (Test-Path $secretsFile) { . $secretsFile }
}
if (-not $env:DATABASE_URL) {
    Write-Host "ERROR: DATABASE_URL is not set." -ForegroundColor Red
    Write-Host "Set `$env:DATABASE_URL, or create a local deploy.secrets.ps1 (see comments above)." -ForegroundColor Yellow
    exit 1
}

$csv   = "output\amazon_products_full.csv"
$input = "output\All Brands Hygiene Input file - 2026.xlsx"

# Friendly checks before we start.
if (-not (Test-Path $csv))   { Write-Host "ERROR: crawl CSV not found at $csv" -ForegroundColor Red; exit 1 }
if (-not (Test-Path $input)) { Write-Host "ERROR: input sheet not found at $input" -ForegroundColor Red; exit 1 }
if (Test-Path "~`$All Brands Hygiene Input file - 2026.xlsx") {
    Write-Host "WARNING: the input sheet looks OPEN in Excel (~`$ lock file present). Close it first, then re-run." -ForegroundColor Yellow
}

Write-Host "== Pushing latest crawl data ==" -ForegroundColor Cyan
python hygiene_db.py import-csv $csv

Write-Host "== Pushing latest input sheet (tab: Format) ==" -ForegroundColor Cyan
python hygiene_db.py import-input $input Format

Write-Host "== Verify ==" -ForegroundColor Cyan
python hygiene_db.py show-input
python hygiene_db.py stats

Write-Host ""
Write-Host "DONE. Tell the team to open https://hygiene-validator.onrender.com/ and hard-refresh (Ctrl+Shift+R)." -ForegroundColor Green
