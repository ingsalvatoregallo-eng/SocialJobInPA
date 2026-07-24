# start.ps1 - avvia lo stack social (app + worker + scheduler + mailpit).
#   .\scripts\start.ps1        modalita' normale
#   .\scripts\start.ps1 -Dev   con hot reload e email verso Mailpit

param([switch]$Dev)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if ($Dev) {
    docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
} else {
    docker compose up -d
}
if (-not $?) { throw "avvio fallito" }

Write-Host "`nStack avviato." -ForegroundColor Green
Write-Host "Dashboard social:  http://localhost:8100/social"
Write-Host "API:               http://localhost:8100/api/v1/social/system/status"
Write-Host "Mailpit (email):   http://localhost:8026"
Write-Host "`nLog:  docker compose logs -f app worker scheduler"
