# Setup locale su Windows (Docker Desktop + WSL2)

Prerequisiti: Windows 10/11, Docker Desktop con backend WSL2 attivo,
PowerShell, Python 3.12+ nel PATH (serve agli script per generare i segreti).

## Passi

```powershell
# 1. prima configurazione: crea .env, genera i segreti, build, dati demo
.\scripts\setup.ps1

# 2. avvio (app + worker + scheduler + mailpit)
.\scripts\start.ps1

# 3. test
.\scripts\test.ps1
```

Poi apri:

- Dashboard: <http://localhost:8000/social> — login con gli utenti demo
  (`admin@demo.jobinpa.local`, password `JobInPA-demo1`);
- Mailpit (tutte le email di sviluppo): <http://localhost:8025>.

## Senza Docker (Python locale)

```powershell
pip install -r requirements.txt Pillow pytest httpx
.\scripts\setup.ps1 -NoDocker
python -m uvicorn src.api:app --port 8000        # web
cd src; python -m social.worker_main             # worker (altro terminale)
cd src; python -m social.scheduler_main          # scheduler (altro terminale)
```

## Variabili minime in .env

- `INPA_AUTH_SECRET` — firmata dal setup se placeholder;
- `ENCRYPTION_KEY` — generata dal setup se vuota (cifra i token OAuth);
- `ANTHROPIC_API_KEY` — senza, il modulo funziona in mock (nessuna AI reale);
- `SOCIAL_MODE=sandbox` e `GLOBAL_PUBLISHING_ENABLED=false` — default sicuri.

## Note WSL2

- I volumi (`./data`, `./assets`) montati da NTFS funzionano ma sono piu'
  lenti: per uso intensivo si puo' clonare il repo dentro WSL.
- Le porte sono esposte solo su `127.0.0.1`: nessun accesso dalla rete.
  Per l'accesso da altri dispositivi della LAN cambia il binding in
  `docker-compose.yml` (es. `"8000:8000"`), consapevolmente.
