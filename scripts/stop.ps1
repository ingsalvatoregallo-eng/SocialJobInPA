# stop.ps1 - ferma lo stack social. I dati (data/, assets/) restano sul disco.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
docker compose down
Write-Host "Stack fermato." -ForegroundColor Green
