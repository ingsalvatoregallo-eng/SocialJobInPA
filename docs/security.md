# Sicurezza — Modulo Social AI

## Autenticazione e autorizzazione

- **API**: Bearer token firmati HMAC esistenti (`src/auth.py`); password
  hashate con PBKDF2 (vedi `auth.hash_password` del repo — Argon2 e' una
  possibile evoluzione, la scelta segue lo standard gia' in uso nel repo).
- **Dashboard**: cookie `social_session` HttpOnly + SameSite=Lax (+ Secure
  se `APP_BASE_URL` e' https), scadenza 12 h, limitato al path `/social`.
- **RBAC**: permessi `social.view/edit/approve/publish/admin` derivati dal
  ruolo (`rbac.ha_permesso`); ruoli: admin, editor, reviewer, viewer.
- **CSRF**: token HMAC derivato dalla sessione, obbligatorio su ogni POST
  della dashboard (`security.csrf_valido`).

## Segreti e cifratura

- Token OAuth cifrati con **Fernet** (`ENCRYPTION_KEY`) prima di toccare il
  DB; nei log/audit compare solo la maschera (`mask_secret`).
- L'audit **scarta** qualunque chiave che contenga password/token/secret/
  api_key (`db_social.audit`).
- Nessun segreto nel repository; `.env` e' in `.gitignore` e in
  `.dockerignore`.
- **Rotazione ENCRYPTION_KEY**: genera la nuova chiave, riautorizza gli
  account social dalla dashboard (i token con la vecchia chiave risultano
  non decifrabili con un errore esplicito, mai silenzioso), revoca i vecchi
  con `db_social.revoca_oauth_tokens`. **Logout globale**: cambia
  `INPA_AUTH_SECRET` (invalida ogni token e cookie firmato).

## SSRF e prompt injection (Research Agent)

- Whitelist di domini (`social_source_domains`, gestibile da dashboard):
  tutto il resto e' rifiutato a prescindere;
- guard `security.url_fetch_consentito`: solo http/https, no credenziali
  in URL, blocco localhost/`.local`/metadata endpoint e di OGNI indirizzo
  risolto privato/loopback/link-local (anti DNS-rebinding);
- sanitizzazione HTML: rimozione script/style/iframe/commenti e degli
  elementi **nascosti** (veicolo classico di injection), testo piano con
  limite 60k caratteri;
- separazione istruzioni/contenuti: il testo delle fonti sta SOLO in
  blocchi `<fonte>` del prompt utente, mai nel system prompt;
- il Research Agent non ha accesso a credenziali social ne' a tool
  privilegiati; gli URL rifiutati generano un incidente
  (`injection_sospetta`) visibile in dashboard;
- output sempre validato Pydantic; le anomalie finiscono in
  `social_incidents`.

## Difese applicative

- SQL: solo query parametrizzate (nessuna concatenazione di input);
- XSS: autoescape Jinja2 attivo (default per i template `.html`);
- path traversal: l'endpoint `/social/asset/{id}` serve solo file dentro
  `ASSET_STORAGE_PATH` (verifica sul percorso risolto);
- upload: il modulo non accetta upload utente nella prima versione (gli
  asset sono generati server-side) — superficie assente;
- rate limiting sul login e security headers: demandati al reverse proxy
  in produzione (vedi docs/deployment-future.md), coerentemente col
  deployment esistente dietro nginx.

## Kill switch e pubblicazione

Cinque controlli in serie prima di ogni pubblicazione (environment → DB →
account → approvazione → classe di rischio), default tutti chiusi; nel
dubbio non si pubblica. Vedi docs/architecture.md.

## Audit

`social_audit_logs`: utente/agente, azione, oggetto, stato prima/dopo,
motivo, dettagli filtrati, IP, timestamp — piu' `social_agent_runs` con
prompt version/hash, modello, token e costi. Mai password, chiavi, token.
