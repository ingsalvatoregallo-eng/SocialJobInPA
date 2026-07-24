# Docker — stack del modulo Social AI

## Servizi (docker-compose.yml)

| Servizio  | Ruolo                                  | Porta (solo localhost) |
|-----------|----------------------------------------|------------------------|
| app       | FastAPI: portale + API + dashboard     | 8000                   |
| worker    | consuma i job (pipeline, publish, ...) | —                      |
| scheduler | semina i job ricorrenti                | —                      |
| mailpit   | SMTP di sviluppo + UI email            | 8025 (UI), 1025 (SMTP) |

`app`, `worker` e `scheduler` usano la **stessa immagine** (Dockerfile),
cambia solo il comando. Il DB SQLite (`./data`) e gli asset (`./assets`)
sono volumi bind: sopravvivono a rebuild e `docker compose down`.

## Comandi

```powershell
.\scripts\setup.ps1              # build + .env + seed demo
.\scripts\start.ps1              # up -d
.\scripts\start.ps1 -Dev         # con override dev (hot reload, Mailpit)
.\scripts\stop.ps1               # down
docker compose logs -f worker    # log di un servizio
docker compose run --rm app python src/social/seed_demo.py   # ri-seed demo
```

## Immagine

- Base `python:3.12-slim` + `fonts-dejavu-core` (font per i template
  immagine deterministici).
- Dipendenze da `requirements-docker.txt`: come `requirements.txt` ma senza
  `sentence-transformers`/`transformers` (torch pesa gigabyte e serve solo
  alla ricerca semantica premium, non usata dai container social; l'import
  e' lazy quindi il resto dell'app funziona regolarmente).
- `.dockerignore` esclude `.env`, dati, CV di test e chiavi: mai segreti o
  dati personali nell'immagine.

## Email in sviluppo

`docker-compose.dev.yml` punta `SMTP_HOST=mailpit`: ogni email di
approvazione finisce nella UI di Mailpit, niente invii reali.
