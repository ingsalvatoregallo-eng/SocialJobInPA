# Backup e restore

## Backup

```powershell
.\scripts\backup.ps1
.\scripts\backup.ps1 -Destinazione D:\backup\jobinpa -RetentionGiorni 60
```

Contenuto di `backups/social-backup-<timestamp>/`:

- `inpa.db` — copia **coerente** del database (API di backup SQLite, sicura
  anche con l'app in esecuzione grazie al WAL: mai una copia file nuda);
- `assets/` — asset di brand e generati;
- `.env.example`, `docker-compose*.yml` — configurazioni non segrete;
- `CHECKSUMS.sha256` — hash di ogni file per la verifica d'integrita'.

Retention: default 30 giorni (configurabile in
`social_system_settings.retention_backup_giorni` o col parametro); i backup
piu' vecchi vengono eliminati a fine esecuzione.

**Attenzione**: il `.env` (con `ENCRYPTION_KEY`, `INPA_AUTH_SECRET`, chiavi
API) NON e' nel backup, di proposito. Custodiscilo in un password manager:
senza `ENCRYPTION_KEY` i token OAuth nel DB ripristinato non sono
decifrabili (si risolve riautorizzando gli account).

## Restore

```powershell
.\scripts\restore.ps1 -BackupPath backups\social-backup-20260724-120000
```

1. verifica tutti i checksum (si ferma al primo file mancante/corrotto);
2. ferma lo stack Docker se attivo;
3. **conserva** il DB corrente come `data/inpa.db.pre-restore-<timestamp>`
   (mai cancellato);
4. ripristina DB e asset;
5. riavvia con `.\scripts\start.ps1`.
