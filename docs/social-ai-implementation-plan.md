# Piano di implementazione — Modulo Social AI di JobInPA

> **Superato dalla riorganizzazione in due progetti separati** (2026-07-24,
> stesso giorno): questo documento descrive la PRIMA versione, con il modulo
> dentro il repository di JobInPA e DB/auth/RBAC condivisi. Quella scelta e'
> stata poi rivista: SocialJobInPA e' diventato un repository a se stante,
> con database, autenticazione e permessi propri, collegato a JobInPA solo
> via API HTTP private (`jobinpa_client.py`). Per l'architettura attuale
> vedi `docs/architecture.md` e `docs/database.md`. Questo file resta come
> registro storico delle decisioni originali, non come stato attuale.

Data: 2026-07-24. Fonte dei requisiti: `docs/jobinpa_social_ai_prompt_master.md`.

## Esito dell'ispezione del repository

- **Stack**: FastAPI + Python 3.14 + SQLite (`data/inpa.db`), frontend React separato in `frontend/`.
- **Accesso dati**: tutto in `src/db.py` (~6250 righe): `connect()` (WAL, FK on, timeout 30s),
  `init_db()` = `executescript(_SCHEMA)` + `_migra_schema()` idempotente con `PRAGMA table_info`.
- **Auth esistente**: `src/auth.py` (token firmati, Bearer), `src/deps.py` (`ottieni_conn`,
  `utente_corrente`), `src/rbac.py` (permessi per ruolo da tabelle `ruoli`/`permessi`/`ruoli_permessi`),
  tabella `utenti` con colonna `ruolo`.
- **Email**: `src/notifiche.py` — smtplib con STARTTLS/SSL, config via env `INPA_SMTP_*`,
  caricamento `.env` locale senza sovrascrivere l'ambiente.
- **Anthropic**: già in `requirements.txt` (usato da `ai_classifier.py`); Pillow già installato.
- **Test**: `tests/` con pytest; baseline al 2026-07-24: 106 pass, 5 failure preesistenti
  (`test_cv_parser`, `test_cv_matching`, `test_db_cv_profilo`, `test_semantic_search_quota`) non
  correlati al modulo social.
- **DB test**: env `INPA_DB_PATH` reindirizza il DB (vedi `deps.py`) — riusato dai test social.

## Decisioni architetturali

1. **Package `src/social/`** autonomo ma coerente con le convenzioni del repo; nessuna modifica
   invasiva a `db.py`. L'unica modifica al codice esistente è il montaggio dei router in `src/api.py`
   (try/except: se il modulo social manca o fallisce, l'app esistente continua a funzionare).
2. **Tabelle prefissate `social_`** nello stesso SQLite, create da `social/db_social.py` con lo
   stesso pattern `executescript` + migrazione idempotente. Gli utenti/ruoli riusano le tabelle
   esistenti `utenti`/`ruoli`: la migrazione social aggiunge i ruoli `reviewer` e `viewer`
   (INSERT OR IGNORE) e i permessi `social.*`. Mapping richiesto dal prompt: users→`utenti`,
   roles→`ruoli`, user_roles→colonna `utenti.ruolo` (convenzione esistente del repo, un ruolo per utente).
3. **Modalità operative**: `SOCIAL_MODE` = `mock` | `sandbox` (default) | `production`.
   In mock nessuna chiamata esterna; in sandbox AI reale ma publisher sempre mock;
   in production publisher reali solo se: config completa + account verificato +
   `GLOBAL_PUBLISHING_ENABLED=true` + kill switch spento (env→DB→account) + contenuto
   approvato o verde + budget disponibile.
4. **Provider AI**: `LLMProvider` protocol → `AnthropicProvider` (tool-use per output strutturato
   Pydantic, retry con backoff, timeout, circuit breaker) e `MockLLMProvider` deterministico.
   Costi registrati in `social_cost_entries` con budget giornaliero/mensile e blocco al 100%.
5. **Immagini**: `TemplateImageProvider` deterministico (Pillow, 8 template, formati 1080×1350,
   1080×1080, 1080×1920, 1200×627, safe area, PNG sRGB) come default; `OpenAIImageProvider`
   opzionale dietro `ENABLE_AI_IMAGES` (testi essenziali mai affidati all'AI: overlay deterministico);
   `MockImageProvider` per i test.
6. **Agenti** = servizi applicativi in-process coordinati da un orchestratore (`agents.py`),
   non processi separati. Ogni esecuzione tracciata in `social_agent_runs` con prompt version,
   modello, costo, esito.
7. **Scheduler/worker**: processo separato `python -m social.scheduler_main` (pattern
   `alert_worker.py`): job persistenti in `social_scheduled_jobs`, lock, retry con backoff,
   dead-letter, recovery al riavvio. Niente Redis/Celery.
8. **Dashboard**: FastAPI + Jinja2 + HTMX sotto `/social`, sessione via cookie HttpOnly firmato
   (riusa `auth.py`), CSRF token per i POST, RBAC admin/editor/reviewer/viewer. Le pagine
   richieste sono raggruppate in viste coerenti (vedi sotto).
9. **API**: router versionato `/api/v1/social/*` con Bearer auth esistente.
10. **Sicurezza**: token OAuth cifrati con Fernet (`cryptography`, già dipendenza) e chiave
    `ENCRYPTION_KEY`; mai token in chiaro in DB/log/audit; whitelist domini fonti; SSRF guard
    (solo https, no IP privati/metadata, no schemi non http); sanitizzazione HTML e limiti
    dimensionali sul contenuto delle fonti; separazione istruzioni/contenuti nei prompt.

## Ordine di lavoro

1. `db_social.py` (schema + accesso dati + settings + audit + job) — base di tutto.
2. `config.py`, `security.py` (cifratura, CSRF, SSRF guard, sanitizzazione).
3. `state_machine.py`, `risk.py`.
4. `llm.py`, `prompts.py` (prompt versionati con hash), `images.py`.
5. `integrations/instagram.py`, `integrations/linkedin.py`, `publishing.py` (idempotente).
6. `agents.py` (8 agenti + orchestratore).
7. `approvals.py` + email; `scheduler.py` + `scheduler_main.py` + `worker_main.py`.
8. `api.py` (REST) + `web.py` + `templates/` (dashboard) + montaggio in `src/api.py`.
9. `seed_demo.py`; test in `tests/social/`.
10. Docker (`Dockerfile`, `docker-compose.yml`, `docker-compose.dev.yml`, mailpit),
    `scripts/*.ps1`, `.env.example`, documentazione in `docs/`.
11. Run completo dei test, fix, report finale.

## Assunzioni documentate (default sicuri, tutti configurabili)

- `SOCIAL_MODE=sandbox` e `GLOBAL_PUBLISHING_ENABLED=false` di default: nessuna pubblicazione
  reale possibile durante lo sviluppo.
- Budget default prudenti: 20 €/mese Anthropic, 5 €/mese OpenAI Images, alert all'80%.
- Prezzi token per il calcolo costi presi da tabella configurabile in `system_settings`
  (default: Sonnet), aggiornabili da dashboard senza deploy.
- La checklist Meta segnala Pagina Facebook e Developer App mancanti; Instagram resta
  "non pronto per la pubblicazione API" finché la config non è completa e verificata.
- SMTP: si riusano le convenzioni `notifiche.py` ma con variabili dedicate `SMTP_*` come da
  prompt (fallback alle `INPA_SMTP_*` esistenti se non impostate). In sviluppo Docker si usa
  Mailpit (nessuna email reale).
- Fuso orario editoriale: `Europe/Rome`.
