# setup.ps1 - prima configurazione di SocialJobInPA (Windows + Docker
# Desktop + WSL2). Idempotente: rieseguirlo non rovina nulla.
#
#   .\scripts\setup.ps1            setup con Docker (build + seed demo)
#   .\scripts\setup.ps1 -NoDocker  solo .env e seed demo col Python locale

param(
    [switch]$NoDocker
)

$ErrorActionPreference = "Stop"
$radice = Split-Path -Parent $PSScriptRoot
Set-Location $radice

# 1) .env dal template, se manca (mai sovrascritto).
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ".env creato da .env.example - compila le variabili necessarie." -ForegroundColor Yellow
} else {
    Write-Host ".env gia' presente: non toccato." -ForegroundColor Green
}

# 2) Genera i segreti mancanti direttamente nel .env (solo se placeholder/vuoti).
$contenuto = Get-Content ".env" -Raw
$rigenerato = $false

if ($contenuto -match "(?m)^SOCIAL_AUTH_SECRET=\s*$") {
    $segreto = python -c "import secrets; print(secrets.token_urlsafe(48))"
    $contenuto = $contenuto -replace "(?m)^SOCIAL_AUTH_SECRET=\s*$", "SOCIAL_AUTH_SECRET=$segreto"
    $rigenerato = $true
    Write-Host "SOCIAL_AUTH_SECRET generata." -ForegroundColor Green
}
if ($contenuto -match "(?m)^ENCRYPTION_KEY=\s*$") {
    $chiave = python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    $contenuto = $contenuto -replace "(?m)^ENCRYPTION_KEY=\s*$", "ENCRYPTION_KEY=$chiave"
    $rigenerato = $true
    Write-Host "ENCRYPTION_KEY (Fernet) generata." -ForegroundColor Green
}
if ($rigenerato) { Set-Content ".env" $contenuto -Encoding utf8 -NoNewline }

if ($NoDocker) {
    # 3a) Setup locale senza Docker: schema + dati demo col Python di sistema.
    Write-Host "Inizializzo schema e dati demo (Python locale)..." -ForegroundColor Cyan
    python src/social/seed_demo.py
    if (-not $?) { throw "seed demo fallito" }
    Write-Host "`nSetup completato (senza Docker)." -ForegroundColor Green
    Write-Host "Avvio manuale: python -m uvicorn src.app:app --app-dir src --port 8100"
    Write-Host "Dashboard:     http://localhost:8100/social"
} else {
    # 3b) Build immagini e seed demo dentro il container.
    Write-Host "Build delle immagini Docker (puo' richiedere qualche minuto)..." -ForegroundColor Cyan
    docker compose build
    if (-not $?) { throw "docker compose build fallito" }
    Write-Host "Inizializzo schema e dati demo nel container..." -ForegroundColor Cyan
    docker compose run --rm app python src/social/seed_demo.py
    if (-not $?) { throw "seed demo fallito" }
    Write-Host "`nSetup completato." -ForegroundColor Green
    Write-Host "Avvia con: .\scripts\start.ps1"
    Write-Host "Dashboard: http://localhost:8100/social - Mailpit: http://localhost:8026"
}
Write-Host "Utenti demo: admin@demo.jobinpa.local / editor@... / reviewer@... (password: JobInPA-demo1)"
Write-Host "Ricorda: JOBINPA_API_URL/JOBINPA_API_KEY in .env per leggere bandi e classificazioni reali."
