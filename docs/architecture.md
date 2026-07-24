# Architettura — Modulo Social AI

SocialJobInPA e' un progetto **separato** dal portale JobInPA (repository
`InPASearch.ai`, sulla VM Aruba): processo proprio, database proprio,
utenti/autenticazione propri. L'unico collegamento e' via API HTTP private
autenticate esposte da JobInPA (`GET /api/internal/bandi*`), lette da
`social/jobinpa_client.py` — mai un accesso diretto al database del portale.

## Componenti

```
                    ┌────────────────────────────────────────────┐
                    │  FastAPI (src/app.py, processo web)        │
                    │   ├── /api/v1/social/*   (social/api.py)   │
                    │   └── /social            (social/web.py)   │
                    └───────────────┬────────────────────────────┘
                                    │  SQLite (data/social.db, WAL)
      ┌────────────────┬────────────┴─────────────┬───────────────┐
      │ worker          │ scheduler               │ mailpit (dev)  │
      │ social/         │ social/                 │ SMTP+UI email  │
      │ worker_main.py  │ scheduler_main.py       │                │
      └────────────────┴──────────────────────────┴───────────────┘
                                    │  HTTP (API key)
                                    ▼
                    JobInPA — /api/internal/bandi* (VM Aruba)
```

- **Web**: dashboard Jinja2 (form standard, cookie di sessione firmato,
  CSRF) + API REST versionate con Bearer token propri (`src/auth.py`,
  `src/deps.py`).
- **Worker**: consuma `social_scheduled_jobs` (lock atomico, retry con
  backoff esponenziale, dead-letter, recovery dei lock orfani).
- **Scheduler**: semina i job ricorrenti (piano settimanale, raccolta
  metriche/commenti) in modo idempotente.

## Agenti (social/agents.py)

Servizi in-process coordinati dall'orchestratore `esegui_pipeline`:

1. **Supervisor** — piano editoriale settimanale (3 argomenti/pillar);
2. **Research** — fatti verificati dalle API private di JobInPA (bandi +
   classificazione AI) + fonti in whitelist
   (guard SSRF, sanitizzazione, contenuti sempre in blocchi `<fonte>`);
3. **Copywriting** — varianti distinte Instagram e LinkedIn;
4. **Visual** — brief + rendering deterministico (Pillow) o OpenAI Images;
5. **Quality & Risk** — classe finale = la peggiore fra regole
   deterministiche (`risk.py`) e giudizio AI; decide
   `auto_publish` / `human_approval` / `blocked`;
6. **Publishing** — idempotente (UNIQUE su content+piattaforma), catena di
   controlli a 5 livelli prima di ogni pubblicazione;
7. **Analytics** — importa solo metriche realmente disponibili;
8. **Community Assistant** — propone risposte ai commenti, MAI inviate in
   automatico.

Ogni esecuzione e' tracciata in `social_agent_runs` (prompt nome/versione/
hash, provider, modello, token, costo).

## State machine (social/state_machine.py)

18 stati da `IDEA` ad `ARCHIVED`; le transizioni valide sono un dizionario
esplicito, tutto il resto solleva `TransizioneNonValida`. Ogni transizione
finisce nell'audit log con stato prima/dopo, attore e motivo.

## Modalita' operative

`SOCIAL_MODE` (override runtime in `social_system_settings.mode_override`):

| Modalita'  | LLM               | Publisher social |
|------------|-------------------|------------------|
| mock       | MockLLMProvider   | MockAdapter      |
| sandbox    | Anthropic (se chiave) | MockAdapter  |
| production | Anthropic         | adapter reali, previa catena kill switch |

## Catena di sicurezza pre-pubblicazione (publishing.can_publish)

1. `GLOBAL_PUBLISHING_ENABLED` (environment)
2. kill switch in `social_system_settings`
3. account verificato + publishing abilitato per account
4. approvazione umana registrata (o classe verde con `auto_publish`)
5. classe di rischio mai rossa

Nel dubbio (stato mancante, classe assente) **non si pubblica**.

## Dati

Database SQLite proprio (`data/social.db`, vedi docs/database.md): tabelle
`social_*` + una tabella `utenti` minima per lo staff (admin/editor/
reviewer/viewer), i permessi derivano dal ruolo tramite una mappa statica
in codice (`db_social.RUOLI_PERMESSI`), non da tabelle a parte.
