# restore.ps1 - ripristina un backup creato da backup.ps1, dopo verifica
# dei checksum. Il DB corrente viene messo da parte su host (mai cancellato).
#
#   .\scripts\restore.ps1 -BackupPath backups\social-backup-20260724-120000
#
# Il DB vive in un volume Docker nativo (non una cartella host): il
# ripristino passa da un container "app" avviato apposta, con "docker
# compose cp" per portare i file dentro/fuori dal volume.

param(
    [Parameter(Mandatory = $true)][string]$BackupPath
)

$ErrorActionPreference = "Stop"
$radice = Split-Path -Parent $PSScriptRoot
Set-Location $radice

if (-not (Test-Path $BackupPath)) { throw "backup non trovato: $BackupPath" }

# 1) Verifica integrita' con i checksum salvati.
$fileChecksum = Join-Path $BackupPath "CHECKSUMS.sha256"
if (-not (Test-Path $fileChecksum)) { throw "CHECKSUMS.sha256 mancante: backup non verificabile" }
$errori = 0
Get-Content $fileChecksum | ForEach-Object {
    if ($_ -match "^([0-9A-Fa-f]{64})\s+(.+)$") {
        $atteso = $Matches[1]; $relativo = $Matches[2]
        $percorso = Join-Path $BackupPath $relativo
        if (-not (Test-Path $percorso)) {
            Write-Host "MANCANTE: $relativo" -ForegroundColor Red; $script:errori++
        } elseif ((Get-FileHash $percorso -Algorithm SHA256).Hash -ne $atteso) {
            Write-Host "CORROTTO: $relativo" -ForegroundColor Red; $script:errori++
        }
    }
}
if ($errori -gt 0) { throw "verifica integrita' fallita ($errori file): restore annullato" }
Write-Host "Integrita' verificata." -ForegroundColor Green

# 2) Ferma lo stack, poi riavvia solo "app" (serve un container attaccato
# al volume per le operazioni di copia).
docker compose down
docker compose up -d app
Start-Sleep -Seconds 2

# 3) Metti da parte su host il DB corrente (dal volume), poi sovrascrivi.
New-Item -ItemType Directory -Force "data" | Out-Null
$salvataggio = "data/social.db.pre-restore-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
docker compose cp app:/app/data/social.db $salvataggio
Write-Host "DB corrente conservato in $salvataggio" -ForegroundColor Yellow

docker compose cp (Join-Path $BackupPath "social.db") app:/app/data/social.db

# 4) Asset (bind mount, copia diretta su host).
$assetBackup = Join-Path $BackupPath "assets"
if (Test-Path $assetBackup) {
    Copy-Item "$assetBackup\*" "assets\" -Recurse -Force
}

# 5) Riavvia tutto lo stack cosi' ogni container riapre una connessione
# fresca sul file appena ripristinato.
docker compose up -d

Write-Host "Restore completato da $BackupPath" -ForegroundColor Green
