# SocialJobInPA

Gestione automatizzata dei contenuti social (Instagram + LinkedIn) di
JobInPA: agenti AI, approvazioni umane, kill switch e dashboard dedicata.

Progetto **separato** dal portale JobInPA (repository `InPASearch.ai`, che
gira sulla VM Aruba): non condivide database, utenti né segreti. L'unico
collegamento e' via API HTTP private e autenticate esposte da JobInPA
(`GET /api/internal/bandi*`), da cui questo progetto legge bandi e
classificazione AI — vedi `src/social/jobinpa_client.py`.

Requisiti originali: `docs/jobinpa_social_ai_prompt_master.md`.
Piano e decisioni architetturali: `docs/social-ai-implementation-plan.md`.

## Avvio rapido (Windows + Docker Desktop + WSL2)

```powershell
.\scripts\setup.ps1     # .env, segreti, build, dati demo
.\scripts\start.ps1     # app + worker + scheduler + mailpit
.\scripts\test.ps1      # suite di test
```

- Dashboard: <http://localhost:8100/social>
  (demo: `admin@demo.jobinpa.local` / `JobInPA-demo1`)
- Email di sviluppo (Mailpit): <http://localhost:8026>
- API: `GET /api/v1/social/system/status`

Senza Docker:

```powershell
python -m pip install -r requirements.txt
.\scripts\setup.ps1 -NoDocker
python -m uvicorn src.app:app --app-dir src --port 8100
```

Modalita' iniziale: **sandbox** — AI reale se configurata, pubblicazioni
social sempre simulate. `GLOBAL_PUBLISHING_ENABLED=false` di default.

## Collegamento con JobInPA

Compila in `.env`:

```
JOBINPA_API_URL=http://localhost:8000   # o https://jobinpa.it in produzione
JOBINPA_API_KEY=...                     # stessa INTERNAL_API_KEY del .env di JobInPA
```

Senza questa configurazione il Research Agent lavora solo col brief
inserito a mano (utile in mock/demo, mai un errore bloccante).

## Documentazione

| Documento | Contenuto |
|---|---|
| `docs/social-ai-implementation-plan.md` | piano e decisioni architetturali |
| `docs/architecture.md` | architettura del modulo |
| `docs/local-setup-windows.md` | setup locale passo-passo |
| `docs/docker.md` | stack Docker |
| `docs/database.md` | schema del proprio DB |
| `docs/anthropic.md` / `docs/openai-images.md` | provider AI |
| `docs/meta-instagram-setup.md` / `docs/linkedin-setup.md` | configurazione canali |
| `docs/smtp-setup.md` | email di approvazione |
| `docs/security.md` | RBAC, cifratura, SSRF, prompt injection |
| `docs/backup-restore.md` | backup/restore con checksum |
| `docs/testing.md` | suite di test |
| `docs/operations.md` | operativita' e passaggio a produzione |
| `docs/troubleshooting.md` | problemi comuni |
| `docs/deployment-future.md` | esposizione futura su social.jobinpa.it |
