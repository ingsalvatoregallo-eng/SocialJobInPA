# backup.ps1 - backup coerente di DB SQLite, asset e configurazioni non
# segrete, con checksum e retention.
#
#   .\scripts\backup.ps1
#   .\scripts\backup.ps1 -Destinazione D:\backup\socialjobinpa -RetentionGiorni 60
#
# Il DB vive in un volume Docker nativo (non una cartella host: vedi
# docker-compose.yml, "social-data"), quindi il backup passa dal container
# app in esecuzione: copia coerente via API di backup SQLite dentro il
# container, poi "docker compose cp" la porta fuori sull'host. Il file .env
# NON viene incluso: contiene segreti (la ENCRYPTION_KEY va custodita a
# parte, vedi docs/backup-restore.md).

param(
    [string]$Destinazione = "backups",
    [int]$RetentionGiorni = 0    # 0 = usa il valore in social_system_settings (default 30)
)

$ErrorActionPreference = "Stop"
$radice = Split-Path -Parent $PSScriptRoot
Set-Location $radice

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$cartella = Join-Path $Destinazione "social-backup-$timestamp"
New-Item -ItemType Directory -Force $cartella | Out-Null

# 1) Database: backup coerente (API SQLite) eseguito DENTRO il container,
# poi copiato fuori sull'host.
docker compose exec -T app python -c "import sqlite3; src = sqlite3.connect('/app/data/social.db'); dst = sqlite3.connect('/tmp/social-backup.db'); src.backup(dst); dst.close(); src.close()"
if (-not $?) { throw "backup del database fallito (il container 'app' e' in esecuzione?)" }
docker compose cp app:/tmp/social-backup.db "$cartella/social.db"
docker compose exec -T app rm -f /tmp/social-backup.db

# 2) Asset generati e di brand, config non segrete.
if (Test-Path "assets") { Copy-Item "assets" -Destination $cartella -Recurse }
Copy-Item ".env.example" -Destination $cartella
Copy-Item "docker-compose.yml" -Destination $cartella
if (Test-Path "docker-compose.dev.yml") { Copy-Item "docker-compose.dev.yml" -Destination $cartella }

# 3) Checksum SHA256 di ogni file per la verifica in restore.
$righe = Get-ChildItem $cartella -Recurse -File | ForEach-Object {
    $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash
    $relativo = $_.FullName.Substring((Resolve-Path $cartella).Path.Length + 1)
    "$hash  $relativo"
}
Set-Content (Join-Path $cartella "CHECKSUMS.sha256") $righe -Encoding utf8

# 4) Retention: elimina i backup piu' vecchi di N giorni.
if ($RetentionGiorni -le 0) {
    $RetentionGiorni = 30
    try {
        $valore = docker compose exec -T app python -c "import sys; sys.path.insert(0, 'src'); from social import db_social; conn = db_social.connect(); print(db_social.get_setting(conn, 'retention_backup_giorni', 30))"
        if ($valore) { $RetentionGiorni = [int]$valore }
    } catch {}
}
$limite = (Get-Date).AddDays(-$RetentionGiorni)
Get-ChildItem $Destinazione -Directory -Filter "social-backup-*" |
    Where-Object { $_.CreationTime -lt $limite } |
    ForEach-Object {
        Write-Host "retention: elimino $($_.Name)" -ForegroundColor Yellow
        Remove-Item $_.FullName -Recurse -Force -Confirm:$false
    }

Write-Host "Backup completato: $cartella" -ForegroundColor Green
Write-Host "NB: la ENCRYPTION_KEY e i segreti (.env) NON sono nel backup: custodiscili a parte."
