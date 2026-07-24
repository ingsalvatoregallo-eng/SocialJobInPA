# backup.ps1 - backup coerente di DB SQLite, asset, template e configurazioni
# non segrete, con checksum e retention.
#
#   .\scripts\backup.ps1
#   .\scripts\backup.ps1 -Destinazione D:\backup\jobinpa -RetentionGiorni 60
#
# Il DB viene copiato con l'API di backup di SQLite (coerente anche con
# l'applicazione in esecuzione, grazie al WAL), MAI con una copia file nuda.
# Il file .env NON viene incluso: contiene segreti (la ENCRYPTION_KEY va
# custodita a parte, vedi docs/backup-restore.md).

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

# 1) Database (backup coerente via API SQLite).
python -c "import sqlite3, sys; src = sqlite3.connect('data/inpa.db'); dst = sqlite3.connect(sys.argv[1]); src.backup(dst); dst.close(); src.close()" "$cartella/inpa.db"
if (-not $?) { throw "backup del database fallito" }

# 2) Asset generati e di brand, template email/prompt (nel codice), config non segrete.
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
        $valore = python -c "import sys; sys.path.insert(0, 'src'); import db; from social import db_social; conn = db.connect('data/inpa.db'); print(db_social.get_setting(conn, 'retention_backup_giorni', 30))"
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
