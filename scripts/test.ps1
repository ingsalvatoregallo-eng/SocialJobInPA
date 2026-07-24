# test.ps1 - esegue la suite di test (tests/).
#   .\scripts\test.ps1             nel container Docker
#   .\scripts\test.ps1 -Local      col Python locale invece che in Docker

param(
    [switch]$Local
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if ($Local) {
    python -m pytest tests -q
} else {
    docker compose run --rm -e SOCIAL_AUTH_SECRET=test-secret app python -m pytest tests -q
}
if (-not $?) { throw "test falliti" }
Write-Host "Test superati." -ForegroundColor Green
