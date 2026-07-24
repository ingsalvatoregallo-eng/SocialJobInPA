# restore.ps1 - ripristina un backup creato da backup.ps1, dopo verifica
# dei checksum. Il DB corrente viene messo da parte (mai cancellato).
#
#   .\scripts\restore.ps1 -BackupPath backups\social-backup-20260724-120000

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

# 2) Ferma lo stack se attivo (ignora errori se Docker non gira).
try { docker compose down 2>$null } catch {}

# 3) Metti da parte il DB corrente, poi ripristina.
if (Test-Path "data/inpa.db") {
    $salvataggio = "data/inpa.db.pre-restore-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Move-Item "data/inpa.db" $salvataggio
    Write-Host "DB corrente conservato in $salvataggio" -ForegroundColor Yellow
}
New-Item -ItemType Directory -Force "data" | Out-Null
Copy-Item (Join-Path $BackupPath "inpa.db") "data/inpa.db"

# 4) Asset.
$assetBackup = Join-Path $BackupPath "assets"
if (Test-Path $assetBackup) {
    Copy-Item "$assetBackup\*" "assets\" -Recurse -Force
}

Write-Host "Restore completato da $BackupPath" -ForegroundColor Green
Write-Host "Riavvia con: .\scripts\start.ps1"
