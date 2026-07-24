# Database — schema del modulo Social AI

Database SQLite proprio (`data/social.db`, WAL) — nessuna tabella
condivisa col portale JobInPA, che si legge solo via API (vedi
`jobinpa_client.py`). Tutte le tabelle vivono in `src/social/db_social.py`
(`init_social_db` = `executescript` + migrazione additiva idempotente).

## Mapping col prompt master (sez. 14)

users → tabella `utenti` propria (email, password_hash, ruolo, stato) ·
roles/user_roles → colonna `utenti.ruolo` (admin/editor/reviewer/viewer),
i permessi derivano da una mappa statica in codice
(`db_social.RUOLI_PERMESSI`), non da tabelle ruoli/permessi separate.
Tutte le altre entita' sono tabelle `social_*`:

| Entita' richiesta      | Tabella                         |
|------------------------|---------------------------------|
| brands                 | `social_brands`                 |
| social_accounts        | `social_accounts`               |
| oauth_tokens           | `social_oauth_tokens` (cifrati) |
| source_domains/items   | `social_source_domains` / `social_source_items` |
| verified_facts         | `social_verified_facts`         |
| editorial_pillars/plans| `social_editorial_pillars` / `social_editorial_plans` |
| content_ideas + drafts | `social_content` (unica riga per l'intero ciclo di vita, stato nella colonna `stato`) |
| post_variants          | `social_post_variants` (UNIQUE content+piattaforma) |
| media_assets           | `social_media_assets`           |
| approvals/events       | `social_approvals` / `social_approval_events` |
| publications/attempts  | `social_publications` (UNIQUE content+piattaforma) / `social_publication_attempts` |
| metric_snapshots       | `social_metric_snapshots`       |
| comments/reply_drafts  | `social_comments` / `social_reply_drafts` |
| agent_runs             | `social_agent_runs`             |
| prompt_versions        | `social_prompt_versions`        |
| cost_entries           | `social_cost_entries`           |
| incidents / policies   | `social_incidents` / `social_policies` |
| audit_logs             | `social_audit_logs`             |
| system_settings        | `social_system_settings` (valori JSON) |
| scheduled_jobs         | `social_scheduled_jobs`         |
| email_notifications    | `social_email_notifications`    |

## Vincoli chiave

- **Idempotenza pubblicazione**: `UNIQUE (content_id, piattaforma)` su
  `social_publications` — impossibile pubblicare due volte lo stesso
  contenuto sulla stessa piattaforma, anche con worker concorrenti.
- **Token OAuth**: colonna `token_cifrato` (Fernet, `ENCRYPTION_KEY`); il
  chiaro non tocca mai il DB, i log o l'audit.
- **Job**: lock con owner+timestamp; un lock piu' vecchio di 15 minuti e'
  considerato di un processo morto e viene reclamato (recovery riavvio).

## Accesso

**Tutto l'SQL del progetto sta in `db_social.py`** — nessuna query nelle
route o negli agenti (le poche SELECT di sola visualizzazione nella
dashboard sono l'eccezione documentata). Connessioni via
`db_social.connect()` (WAL, FK ON, timeout 30s).
