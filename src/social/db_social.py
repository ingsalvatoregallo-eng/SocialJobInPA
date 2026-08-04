"""
db_social.py — schema e accesso dati di SocialJobInPA, su un database
SQLite PROPRIO (data/social.db): separato per costruzione dal DB del
portale JobInPA, che questo progetto legge solo via API private
(vedi jobinpa_client.py).

Convenzioni ereditate da JobInPA: tutto l'SQL vive qui, connessione WAL
con FK e timeout 30s, init idempotente con executescript + migrazione
additiva. Gli utenti sono pochi (lo staff che usa la dashboard) e vivono
nella tabella `utenti` di questo DB; i permessi derivano dal ruolo tramite
la mappa statica RUOLI_PERMESSI (nessuna tabella: cambiare la matrice e'
una modifica di codice, passa dalla review).

I token OAuth sono cifrati a livello applicativo (social/security.py) PRIMA
di entrare qui: queste funzioni accettano/restituiscono solo il testo cifrato.
"""

import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "social.db"

RUOLI = ("admin", "editor", "reviewer", "viewer")

RUOLI_PERMESSI = {
    "admin": ("social.view", "social.edit", "social.approve", "social.publish", "social.admin"),
    "editor": ("social.view", "social.edit"),
    "reviewer": ("social.view", "social.approve"),
    "viewer": ("social.view",),
}


def permessi_di_ruolo(conn, ruolo):
    """Permessi derivati dal ruolo. `conn` e' accettata (e ignorata) per
    simmetria con la funzione omonima di JobInPA: le route la passano gia'."""
    return RUOLI_PERMESSI.get(ruolo, ())


def ha_permesso(conn, utente, permesso):
    return permesso in permessi_di_ruolo(conn, utente["ruolo"])


def connect(db_path=None):
    """Apre la connessione (creando data/ se manca). FK + timeout 30,
    check_same_thread=False: stesse ragioni documentate in JobInPA (FastAPI
    esegue le dependency su un pool di thread; la connessione resta comunque
    una per richiesta, mai condivisa fra richieste concorrenti).

    SOCIAL_DB_PATH permette di puntare a un DB alternativo (es. nei test),
    ma DEVE essere un percorso ASSOLUTO se impostata: app/worker/scheduler
    girano in container separati con working_dir diversi (/app e /app/src),
    quindi un percorso relativo si risolverebbe in posti diversi a seconda
    del servizio — bug reale gia' incontrato (il worker apriva senza
    accorgersene un DB vuoto in /app/src/data/, mentre app scriveva nel DB
    vero in /app/data/: nessun errore, solo dati mai visti dal worker).
    Una SOCIAL_DB_PATH vuota o assente usa sempre il default assoluto
    (`or` invece di get(..., default): una stringa vuota nell'ambiente non
    deve "vincere" sul default, altrimenti Path("") risolverebbe alla
    working directory corrente, stesso bug in un'altra forma).

    Journal DELETE (non WAL): scelta difensiva dato che il DB e' letto e
    scritto da PIU' CONTAINER separati — DELETE non richiede coordinamento
    fra processi via memoria condivisa (il file -shm di WAL), a fronte di
    un costo in concorrenza trascurabile per il volume di scritture di
    questa app (job e contenuti aggiornati occasionalmente, non un sistema
    ad alto throughput); timeout=30 assorbe comunque le brevi contese."""
    db_path = Path(db_path or os.environ.get("SOCIAL_DB_PATH") or DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

# Whitelist iniziale delle fonti ufficiali (sez. 9 del prompt master).
SOURCE_DOMAINS_SEED = (
    ("www.inpa.gov.it", "Portale inPA"),
    ("www.gazzettaufficiale.it", "Gazzetta Ufficiale"),
    ("www.normattiva.it", "Normattiva"),
    ("www.agid.gov.it", "AgID"),
    ("www.formez.it", "Formez PA"),
    ("www.funzionepubblica.gov.it", "Dipartimento della Funzione Pubblica"),
    ("jobinpa.it", "JobInPA"),
)

PILLARS_SEED = (
    ("opportunita", "Opportunità", "Nuovi concorsi e opportunità della settimana"),
    ("guida", "Guida pratica", "Come funzionano i concorsi, errori da evitare, FAQ"),
    ("scadenza", "Scadenze e aggiornamenti", "Concorsi in scadenza e novità"),
)

PIATTAFORME = ("instagram", "linkedin")
# Storico, sostituito dalla categoria scelta alla creazione (vedi
# STRATEGIE_FATTI/social_content_categories): la colonna resta in schema
# per compatibilita' con contenuti gia' creati, ma non guida piu' alcuna
# logica — agents.research/visual leggono solo content.categoria_id.
TIPOLOGIE_CONTENUTO = ("concorso", "promozione", "generico")

# Come una categoria (menu Categorie) procura/verifica i fatti di un
# contenuto (vedi agents.research): bandi_jobinpa cerca/filtra bandi
# come sempre fatto per "Concorsi"; promozioni_jobinpa legge le
# promozioni attive da JobInPA (mai a mano); funzionalita_jobinpa legge
# il catalogo funzionalita' + statistiche d'uso reali da JobInPA; libera
# lascia scrivere tutto all'AI dal solo brief, forzando comunque la
# revisione umana.
STRATEGIE_FATTI = ("bandi_jobinpa", "promozioni_jobinpa", "funzionalita_jobinpa", "libera")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS utenti (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    nome          TEXT,
    cognome       TEXT,
    ruolo         TEXT NOT NULL DEFAULT 'viewer',  -- admin | editor | reviewer | viewer
    stato         TEXT NOT NULL DEFAULT 'attivo',  -- attivo | disattivato
    creato_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_brands (
    id            TEXT PRIMARY KEY,
    nome          TEXT NOT NULL,
    payoff        TEXT,
    palette       TEXT,               -- JSON {primario, secondario, testo, sfondo, accento}
    logo_path     TEXT,
    is_demo       INTEGER NOT NULL DEFAULT 0,
    creato_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_accounts (
    id             TEXT PRIMARY KEY,
    piattaforma    TEXT NOT NULL,     -- instagram | linkedin
    nome           TEXT NOT NULL,
    identificativo TEXT,              -- IG account id / organization URN
    stato          TEXT NOT NULL DEFAULT 'non_configurato',
                                      -- non_configurato | in_configurazione | verificato | errore
    publishing_enabled INTEGER NOT NULL DEFAULT 0,   -- kill switch per account
    dettagli       TEXT,              -- JSON diagnostica/checklist
    is_demo        INTEGER NOT NULL DEFAULT 0,
    creato_at      TEXT NOT NULL,
    aggiornato_at  TEXT
);

CREATE TABLE IF NOT EXISTS social_oauth_tokens (
    id             TEXT PRIMARY KEY,
    account_id     TEXT NOT NULL REFERENCES social_accounts(id) ON DELETE CASCADE,
    tipo           TEXT NOT NULL,     -- access | refresh
    token_cifrato  TEXT NOT NULL,     -- Fernet, mai in chiaro
    scadenza_at    TEXT,
    scopes         TEXT,
    creato_at      TEXT NOT NULL,
    revocato_at    TEXT
);

CREATE TABLE IF NOT EXISTS social_source_domains (
    id         TEXT PRIMARY KEY,
    dominio    TEXT NOT NULL UNIQUE,
    nome       TEXT,
    attivo     INTEGER NOT NULL DEFAULT 1,
    creato_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_source_items (
    id          TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    dominio     TEXT NOT NULL,
    titolo      TEXT,
    testo       TEXT,                 -- gia' sanitizzato (security.sanitizza_html)
    tipo        TEXT NOT NULL DEFAULT 'web',   -- web | jobinpa_db | jobinpa_api
    content_id  TEXT,                 -- contenuto per cui e' stata raccolta
    creato_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_verified_facts (
    id          TEXT PRIMARY KEY,
    content_id  TEXT,
    fatto       TEXT NOT NULL,
    fonte_url   TEXT,
    confidenza  REAL NOT NULL DEFAULT 0,
    conflitto   INTEGER NOT NULL DEFAULT 0,
    richiede_revisione INTEGER NOT NULL DEFAULT 0,
    creato_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_editorial_pillars (
    id          TEXT PRIMARY KEY,
    chiave      TEXT NOT NULL UNIQUE,
    nome        TEXT NOT NULL,
    descrizione TEXT,
    attivo      INTEGER NOT NULL DEFAULT 1,
    creato_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_editorial_plans (
    id          TEXT PRIMARY KEY,
    settimana   TEXT NOT NULL,        -- lunedi' ISO della settimana (YYYY-MM-DD)
    giorno      TEXT,                 -- giorno specifico ISO (YYYY-MM-DD), dentro la settimana
    pillar_id   TEXT REFERENCES social_editorial_pillars(id),
    tema        TEXT NOT NULL,
    obiettivo   TEXT,
    canali      TEXT,                 -- JSON ["instagram","linkedin"]
    fascia_oraria TEXT,               -- es. "12:00-14:00"
    priorita    INTEGER NOT NULL DEFAULT 0,
    stato       TEXT NOT NULL DEFAULT 'pianificato',
                                      -- suggerito (proposta AI, content_id NULL) | pianificato
                                      -- (accettata/creata a mano, content_id valorizzato) |
                                      -- in_lavorazione | completato | annullato
    content_id  TEXT,
    is_demo     INTEGER NOT NULL DEFAULT 0,
    creato_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_content (
    id            TEXT PRIMARY KEY,
    titolo        TEXT NOT NULL,
    pillar_id     TEXT REFERENCES social_editorial_pillars(id),
    obiettivo     TEXT,               -- es. "traffico" | "notorieta" | "conversione"
    brief         TEXT,               -- richiesta iniziale / idea
    stato         TEXT NOT NULL DEFAULT 'IDEA',
    classe_rischio TEXT,              -- verde | giallo | rosso
    decisione_rischio TEXT,           -- auto_publish | human_approval | blocked
    punteggi_rischio TEXT,            -- JSON dal Quality&Risk agent
    canali        TEXT NOT NULL DEFAULT '["instagram","linkedin"]',
    programmato_at TEXT,              -- ISO UTC di pubblicazione programmata
    concorso_id   TEXT,               -- riferimento facoltativo a bandi.id
    errore        TEXT,
    tipologia     TEXT NOT NULL DEFAULT 'concorso',  -- concorso | promozione | generico
    scadenza_promo TEXT,              -- data (YYYY-MM-DD), solo tipologia 'promozione'
    promo_dati    TEXT,               -- JSON: dati reali della promo letti da JobInPA
    funzionalita_dati TEXT,           -- JSON: dati reali della funzionalita' (+ statistiche) da JobInPA
    categoria_id  TEXT,               -- riferimento facoltativo a social_content_categories(id)
    filtri_manuali TEXT,              -- JSON: filtri espliciti "ricerca avanzata" (vedi web.py),
                                      -- se valorizzato SOSTITUISCE interpreta_brief
    soglia_confidenza INTEGER,        -- coerenza_semantica minima accettata (0-100), None = nessuna
    is_demo       INTEGER NOT NULL DEFAULT 0,
    creato_da     INTEGER,            -- utenti.id
    creato_at     TEXT NOT NULL,
    aggiornato_at TEXT
);

CREATE TABLE IF NOT EXISTS social_post_variants (
    id          TEXT PRIMARY KEY,
    content_id  TEXT NOT NULL REFERENCES social_content(id) ON DELETE CASCADE,
    piattaforma TEXT NOT NULL,
    testo       TEXT NOT NULL,
    hashtags    TEXT,                 -- JSON lista
    call_to_action TEXT,
    creato_at   TEXT NOT NULL,
    UNIQUE (content_id, piattaforma)
);

CREATE TABLE IF NOT EXISTS social_media_assets (
    id          TEXT PRIMARY KEY,
    content_id  TEXT REFERENCES social_content(id) ON DELETE CASCADE,
    piattaforma TEXT,
    template    TEXT,                 -- chiave template deterministico o 'openai'
    formato     TEXT,                 -- es. 1080x1350
    percorso    TEXT NOT NULL,
    provider    TEXT NOT NULL DEFAULT 'template',
    bando_id    TEXT,                 -- bando del carosello che questa immagine rappresenta (NULL fuori carosello)
    url_pubblico TEXT,                -- URL su storage pubblico (R2), NULL se non caricato/non configurato
    creato_at   TEXT NOT NULL
);

-- Categorie (menu "Categorie"): unico punto in cui si decide sia come
-- procurare/verificare i fatti (strategia_fatti) sia come generare il
-- post — struttura del testo (struttura_post, guida per il Copywriter
-- Agent, non un testo fisso: l'AI scrive comunque le parole sui fatti
-- veri) e soggetto dell'illustrazione (prompt_ai, facoltativo: vuoto =
-- l'AI sceglie liberamente, utile per "Concorsi" dove il soggetto varia
-- da bando a bando). Una o piu' immagini di riferimento guidano davvero
-- OpenAI (endpoint /v1/images/edits, che accetta piu' immagini nella
-- stessa richiesta, invece di /v1/images/generations).
CREATE TABLE IF NOT EXISTS social_content_categories (
    id                      TEXT PRIMARY KEY,
    nome                    TEXT NOT NULL UNIQUE,
    prompt_ai               TEXT NOT NULL DEFAULT '',
    stile_immagine          TEXT,  -- sostituisce lo stile fisso (images._STILE_OPENAI_IMAGES) se valorizzato
    immagini_riferimento    TEXT,  -- JSON lista di percorsi locali, [] o NULL se nessuna
    strategia_fatti         TEXT NOT NULL DEFAULT 'libera',
    struttura_post          TEXT,  -- guida di struttura per il Copywriter Agent, facoltativa
    creato_at               TEXT NOT NULL,
    aggiornato_at           TEXT
);

CREATE TABLE IF NOT EXISTS social_approvals (
    id          TEXT PRIMARY KEY,
    content_id  TEXT NOT NULL REFERENCES social_content(id) ON DELETE CASCADE,
    stato       TEXT NOT NULL DEFAULT 'in_attesa',
                                      -- in_attesa | approvato | rifiutato | modifiche_richieste
    motivo      TEXT,
    richiesto_at TEXT NOT NULL,
    deciso_da   INTEGER,              -- utenti.id
    deciso_at   TEXT
);

CREATE TABLE IF NOT EXISTS social_approval_events (
    id          TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL REFERENCES social_approvals(id) ON DELETE CASCADE,
    azione      TEXT NOT NULL,        -- richiesta | approvato | rifiutato | modifiche_richieste | email_inviata
    utente_id   INTEGER,
    motivo      TEXT,
    creato_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_publications (
    id           TEXT PRIMARY KEY,
    content_id   TEXT NOT NULL REFERENCES social_content(id) ON DELETE CASCADE,
    piattaforma  TEXT NOT NULL,
    stato        TEXT NOT NULL DEFAULT 'in_corso',  -- in_corso | pubblicato | fallito
    remote_id    TEXT,                -- id del post sulla piattaforma
    remote_url   TEXT,
    modalita     TEXT NOT NULL,       -- mock | reale
    pubblicato_at TEXT,
    errore       TEXT,
    creato_at    TEXT NOT NULL,
    UNIQUE (content_id, piattaforma)  -- idempotenza: mai due pubblicazioni dello stesso contenuto
);

CREATE TABLE IF NOT EXISTS social_publication_attempts (
    id              TEXT PRIMARY KEY,
    publication_id  TEXT NOT NULL REFERENCES social_publications(id) ON DELETE CASCADE,
    esito           TEXT NOT NULL,    -- ok | errore
    dettaglio       TEXT,
    creato_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_metric_snapshots (
    id              TEXT PRIMARY KEY,
    publication_id  TEXT NOT NULL REFERENCES social_publications(id) ON DELETE CASCADE,
    metriche        TEXT NOT NULL,    -- JSON: solo metriche realmente disponibili
    rilevato_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_comments (
    id              TEXT PRIMARY KEY,
    publication_id  TEXT NOT NULL REFERENCES social_publications(id) ON DELETE CASCADE,
    remote_id       TEXT,
    autore          TEXT,
    testo           TEXT NOT NULL,
    stato           TEXT NOT NULL DEFAULT 'nuovo',  -- nuovo | risposto | ignorato
    creato_at       TEXT NOT NULL,
    UNIQUE (publication_id, remote_id)
);

CREATE TABLE IF NOT EXISTS social_reply_drafts (
    id          TEXT PRIMARY KEY,
    comment_id  TEXT NOT NULL REFERENCES social_comments(id) ON DELETE CASCADE,
    testo       TEXT NOT NULL,
    stato       TEXT NOT NULL DEFAULT 'proposta',  -- proposta | approvata | rifiutata | inviata
    deciso_da   INTEGER,
    creato_at   TEXT NOT NULL,
    aggiornato_at TEXT
);

CREATE TABLE IF NOT EXISTS social_agent_runs (
    id          TEXT PRIMARY KEY,
    agente      TEXT NOT NULL,
    content_id  TEXT,
    esito       TEXT NOT NULL DEFAULT 'in_corso',  -- in_corso | ok | errore
    dettaglio   TEXT,
    prompt_nome TEXT,
    prompt_versione TEXT,
    prompt_hash TEXT,
    provider    TEXT,
    modello     TEXT,
    token_input  INTEGER,
    token_output INTEGER,
    costo_eur   REAL,
    iniziato_at TEXT NOT NULL,
    finito_at   TEXT
);

CREATE TABLE IF NOT EXISTS social_prompt_versions (
    id        TEXT PRIMARY KEY,
    nome      TEXT NOT NULL,
    versione  TEXT NOT NULL,
    hash      TEXT NOT NULL,
    testo     TEXT NOT NULL,
    creato_at TEXT NOT NULL,
    UNIQUE (nome, versione)
);

CREATE TABLE IF NOT EXISTS social_cost_entries (
    id          TEXT PRIMARY KEY,
    provider    TEXT NOT NULL,        -- anthropic | openai_images
    modello     TEXT,
    content_id  TEXT,
    agente      TEXT,
    token_input  INTEGER,
    token_output INTEGER,
    costo_eur   REAL NOT NULL,
    stimato     INTEGER NOT NULL DEFAULT 0,
    creato_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_incidents (
    id         TEXT PRIMARY KEY,
    tipo       TEXT NOT NULL,         -- budget | publishing | injection_sospetta | provider | altro
    dettaglio  TEXT,
    risolto    INTEGER NOT NULL DEFAULT 0,
    creato_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_policies (
    id         TEXT PRIMARY KEY,
    chiave     TEXT NOT NULL UNIQUE,
    valore     TEXT NOT NULL,         -- JSON
    creato_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_audit_logs (
    id          TEXT PRIMARY KEY,
    utente_id   INTEGER,
    agente      TEXT,
    azione      TEXT NOT NULL,
    oggetto_tipo TEXT,
    oggetto_id  TEXT,
    stato_prima TEXT,
    stato_dopo  TEXT,
    motivo      TEXT,
    dettagli    TEXT,                 -- JSON (mai segreti: vedi audit())
    ip          TEXT,
    creato_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_system_settings (
    chiave        TEXT PRIMARY KEY,
    valore        TEXT NOT NULL,      -- JSON
    aggiornato_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_scheduled_jobs (
    id           TEXT PRIMARY KEY,
    tipo         TEXT NOT NULL,       -- publish | collect_metrics | generate_week_plan | pipeline
    payload      TEXT,                -- JSON
    stato        TEXT NOT NULL DEFAULT 'pending',
                                      -- pending | running | done | failed | dead
    esegui_at    TEXT NOT NULL,       -- ISO UTC
    tentativi    INTEGER NOT NULL DEFAULT 0,
    max_tentativi INTEGER NOT NULL DEFAULT 5,
    lock_owner   TEXT,
    lock_at      TEXT,
    ultimo_errore TEXT,
    creato_at    TEXT NOT NULL,
    aggiornato_at TEXT
);

CREATE TABLE IF NOT EXISTS social_email_notifications (
    id          TEXT PRIMARY KEY,
    destinatari TEXT NOT NULL,        -- JSON lista email
    oggetto     TEXT NOT NULL,
    corpo       TEXT NOT NULL,
    riferimento TEXT,                 -- es. approval:<id>
    esito       TEXT NOT NULL,        -- inviata | fallita | saltata
    dettaglio   TEXT,
    creato_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_social_content_stato ON social_content(stato);
CREATE INDEX IF NOT EXISTS idx_social_jobs_pending ON social_scheduled_jobs(stato, esegui_at);
CREATE INDEX IF NOT EXISTS idx_social_audit_oggetto ON social_audit_logs(oggetto_tipo, oggetto_id);
CREATE INDEX IF NOT EXISTS idx_social_costs_data ON social_cost_entries(provider, creato_at);
"""

SETTINGS_DEFAULT = {
    "kill_switch": False,             # True = pubblicazione bloccata ovunque
    "mode_override": None,            # se impostato, prevale su SOCIAL_MODE
    "posting_windows": None,          # None = usa config.DEFAULT_POSTING_WINDOWS
    "argomenti_settimanali": 3,
    "alert_budget_percent": 80,
    "revisori_email": [],             # destinatari delle richieste di approvazione
    "prezzi_token_eur": {              # per milione di token; configurabile da dashboard
        "input": 2.7, "output": 13.5,
    },
    "prezzo_immagine_ai_eur": 0.04,
    "retention_backup_giorni": 30,
}

# Categoria seminata di default (menu "Categorie", vedi crea_categoria).
# Per "Promozioni" questo testo descrive SOLO l'illustrazione laterale:
# viene inserito (in inglese, come frammento di frase) dentro il prompt
# completo del template "promozione" (vedi images._prompt_promozione_
# completa), che a differenza di images._STILE_OPENAI_IMAGES fa comporre
# all'AI l'intera grafica testo incluso — non solo un'illustrazione senza
# scritte. Placeholder disponibili: {TITOLO}, {SCADENZA} (vedi agents.visual).
CATEGORIA_PROMOZIONI_DEFAULT_PROMPT = (
    "a glossy 3D gift box in the brand colors with a ribbon, next to a "
    "stylized desk calendar highlighting the expiry date, small sparkles "
    "and soft decorative shapes around them, warm and promotional "
    "composition. Promotion theme: {TITOLO}, expiring {SCADENZA}."
)

# Struttura suggerita al Copywriter Agent per le promozioni (l'AI scrive
# comunque le parole vere sui fatti letti da JobInPA, questa e' solo la
# forma): rispecchia il layout tipico di un post promozionale JobInPA.
CATEGORIA_PROMOZIONI_DEFAULT_STRUTTURA = (
    "Un'etichetta breve in cima (es. 'Cosa fa JobInPA' o simile), un "
    "titolo principale che comunica chiaramente l'offerta, un "
    "sottotitolo con la scadenza, un elenco di 2-3 punti/vantaggi "
    "concreti (es. cosa include, per chi e' valida, fino a quando), e "
    "una call to action diretta (es. 'Registrati gratis')."
)

# Stile immagine di default per le promozioni: lo stile fisso di sempre
# (images._STILE_OPENAI_IMAGES) e' pensato per contenuti istituzionali
# (bandi/concorsi) — piatto, navy/verde, motivo architettonico — e
# confligge con l'estetica commerciale/SaaS che una promozione richiede
# (sfondo sfumato, look 3D lucido). Sostituisce del tutto lo stile fisso
# per questa categoria (vedi social_content_categories.stile_immagine).
# A differenza dello stile fisso, qui NON si vieta il testo: il template
# "promozione" fa comporre all'AI l'intera grafica, testo incluso (vedi
# images._prompt_promozione_completa) — le istruzioni di testo esatto
# stanno in quel prompt, qui solo l'estetica generale.
CATEGORIA_PROMOZIONI_DEFAULT_STILE = (
    "Modern glossy 3D SaaS marketing graphic, premium and polished, soft "
    "rounded shapes with gentle shadows and highlights. Background: smooth "
    "gradient from white to light lavender/blue. Accent colors: purple "
    "#7C3AED and institutional blue #0B3D91. Small decorative sparkles. "
    "Clean, elegant, not flat vector, not photorealistic, no real people."
)


def _adesso():
    return datetime.now(timezone.utc).isoformat()


def _nuovo_id():
    return uuid.uuid4().hex


def _insert(conn, tabella, dati):
    colonne = ", ".join(dati)
    segnaposto = ", ".join("?" for _ in dati)
    conn.execute(f"INSERT INTO {tabella} ({colonne}) VALUES ({segnaposto})",
                 tuple(dati.values()))


def _esegui_scrittura_con_retry(conn, sql, parametri, *, tentativi=5, attesa=0.2):
    """Riprova una scrittura se SQLite risponde "database is locked": sotto
    scritture concorrenti da piu' processi (app/worker/scheduler, stesso
    file — vedi connect(), journal DELETE non WAL per compatibilita' fra
    container separati) il timeout=30 passato a sqlite3.connect() assorbe
    di norma le contese brevi, ma non sempre sotto carico reale (segnalato
    dall'utente: 500 Internal Server Error su un semplice salvataggio di
    una categoria, con "database is locked" nei log). Backoff esponenziale
    breve invece di alzare ulteriormente il timeout globale — non risolve
    la contesa alla radice, ma il tentativo successivo quasi sempre passa."""
    for tentativo in range(tentativi):
        try:
            conn.execute(sql, parametri)
            conn.commit()
            return
        except sqlite3.OperationalError as errore:
            if "database is locked" not in str(errore) or tentativo == tentativi - 1:
                raise
            time.sleep(attesa * (2 ** tentativo))


def init_social_db(conn):
    """Idempotente, sicura ad ogni avvio (stesso pattern di db.init_db)."""
    conn.executescript(_SCHEMA)
    conn.commit()
    _migra(conn)


def _migra(conn):
    adesso = _adesso()
    # Colonna aggiunta dopo il primo rilascio: DB creati prima non ce l'hanno,
    # CREATE TABLE IF NOT EXISTS non la aggiunge da sola a una tabella gia'
    # esistente (stesso pattern di JobInPA/db.py, PRAGMA table_info).
    colonne_plans = {r["name"] for r in conn.execute("PRAGMA table_info(social_editorial_plans)")}
    if "giorno" not in colonne_plans:
        conn.execute("ALTER TABLE social_editorial_plans ADD COLUMN giorno TEXT")
        conn.commit()
    if "categoria_id" not in colonne_plans:
        # Categoria scelta dal Supervisor (vocabolario chiuso, vedi
        # agents.supervisor_pianifica_settimana) o dall'utente prima di
        # accettare: senza, un suggerimento accettato diventava un
        # contenuto "generico" che ignorava prompt/stile/struttura della
        # categoria censita nel backoffice (segnalato dall'utente: "Genera
        # 3 temi" doveva riusare le Categorie, non inventare temi liberi).
        conn.execute("ALTER TABLE social_editorial_plans ADD COLUMN categoria_id TEXT")
        conn.commit()
    colonne_content = {r["name"] for r in conn.execute("PRAGMA table_info(social_content)")}
    if "obiettivo" not in colonne_content:
        conn.execute("ALTER TABLE social_content ADD COLUMN obiettivo TEXT")
        conn.commit()
    if "bandi_trovati" not in colonne_content:
        # Record grezzi dei bandi trovati da research() (JSON): senza
        # persisterli qui, un "rigenera immagine" isolato (senza rifare tutta
        # la ricerca) perderebbe i dati per il carosello Instagram.
        conn.execute("ALTER TABLE social_content ADD COLUMN bandi_trovati TEXT")
        conn.commit()
    colonne_assets = {r["name"] for r in conn.execute("PRAGMA table_info(social_media_assets)")}
    if "bando_id" not in colonne_assets:
        # Collega ogni immagine del carosello al bando che rappresenta:
        # senza, eliminare una singola immagine non potrebbe togliere anche
        # il bando corrispondente da bandi_trovati (vedi elimina_asset).
        conn.execute("ALTER TABLE social_media_assets ADD COLUMN bando_id TEXT")
        conn.commit()
    if "url_pubblico" not in colonne_assets:
        # URL su storage pubblico (R2): Instagram richiede un image_url
        # raggiungibile da Internet, mai i byte diretti come LinkedIn.
        conn.execute("ALTER TABLE social_media_assets ADD COLUMN url_pubblico TEXT")
        conn.commit()
    if "aggiornato_at" not in colonne_assets:
        # Separata da creato_at apposta: creato_at decide l'ordine delle
        # slide nel carosello (vedi asset_di, ORDER BY creato_at) e non
        # deve mai cambiare quando si rigenera SOLO un'immagine, altrimenti
        # quella slide salterebbe in fondo alla sequenza. aggiornato_at
        # serve solo a sapere QUANDO e' stata rigenerata l'ultima volta —
        # mostrato in pagina e usato per invalidare la cache del browser
        # sull'URL dell'immagine (stabile, /social/asset/{id}): senza,
        # un'immagine appena rigenerata poteva sembrare "identica a prima"
        # perche' il browser riusava la versione in cache dello stesso URL
        # (segnalato dall'utente dopo aver rigenerato una singola immagine
        # con delle note di correzione).
        conn.execute("ALTER TABLE social_media_assets ADD COLUMN aggiornato_at TEXT")
        conn.commit()
    if "tipologia" not in colonne_content:
        conn.execute(
            "ALTER TABLE social_content ADD COLUMN tipologia TEXT NOT NULL DEFAULT 'concorso'")
        conn.commit()
    if "scadenza_promo" not in colonne_content:
        conn.execute("ALTER TABLE social_content ADD COLUMN scadenza_promo TEXT")
        conn.commit()
    if "promo_dati" not in colonne_content:
        conn.execute("ALTER TABLE social_content ADD COLUMN promo_dati TEXT")
        conn.commit()
    if "funzionalita_dati" not in colonne_content:
        conn.execute("ALTER TABLE social_content ADD COLUMN funzionalita_dati TEXT")
        conn.commit()
    if "categoria_id" not in colonne_content:
        conn.execute("ALTER TABLE social_content ADD COLUMN categoria_id TEXT")
        conn.commit()
    if "filtri_manuali" not in colonne_content:
        # Filtri espliciti "ricerca avanzata" (stessi campi della ricerca
        # avanzata di JobInPA): se valorizzati, sostituiscono interpreta_brief
        # invece di lasciare che l'AI li deduca dal brief (segnalato
        # dall'utente: vuole gli stessi filtri espliciti di JobInPA, non
        # un'interpretazione nascosta -- vedi memoria
        # feedback_ricerca_esplicita_vs_ai).
        conn.execute("ALTER TABLE social_content ADD COLUMN filtri_manuali TEXT")
        conn.commit()
    if "soglia_confidenza" not in colonne_content:
        conn.execute("ALTER TABLE social_content ADD COLUMN soglia_confidenza INTEGER")
        conn.commit()
    colonne_categorie = {r["name"] for r in conn.execute("PRAGMA table_info(social_content_categories)")}
    if "immagini_riferimento" not in colonne_categorie:
        # Prima si supportava una sola immagine di riferimento
        # (immagine_riferimento_path): l'endpoint /v1/images/edits accetta
        # piu' immagini nella stessa richiesta, quindi si passa a una
        # lista JSON — le categorie gia' create con una singola immagine
        # la mantengono come lista di un elemento.
        conn.execute("ALTER TABLE social_content_categories ADD COLUMN immagini_riferimento TEXT")
        conn.commit()
        if "immagine_riferimento_path" in colonne_categorie:
            for riga in conn.execute(
                    "SELECT id, immagine_riferimento_path FROM social_content_categories "
                    "WHERE immagine_riferimento_path IS NOT NULL"):
                conn.execute(
                    "UPDATE social_content_categories SET immagini_riferimento = ? WHERE id = ?",
                    (json.dumps([riga["immagine_riferimento_path"]]), riga["id"]))
            conn.commit()
    if "strategia_fatti" not in colonne_categorie:
        conn.execute(
            "ALTER TABLE social_content_categories ADD COLUMN strategia_fatti "
            "TEXT NOT NULL DEFAULT 'libera'")
        conn.commit()
    if "struttura_post" not in colonne_categorie:
        conn.execute("ALTER TABLE social_content_categories ADD COLUMN struttura_post TEXT")
        conn.commit()
    if "stile_immagine" not in colonne_categorie:
        conn.execute("ALTER TABLE social_content_categories ADD COLUMN stile_immagine TEXT")
        conn.commit()
    for dominio, nome in SOURCE_DOMAINS_SEED:
        conn.execute(
            "INSERT OR IGNORE INTO social_source_domains (id, dominio, nome, attivo, creato_at) "
            "VALUES (?, ?, ?, 1, ?)", (_nuovo_id(), dominio, nome, adesso))
    for chiave, nome, descrizione in PILLARS_SEED:
        conn.execute(
            "INSERT OR IGNORE INTO social_editorial_pillars (id, chiave, nome, descrizione, creato_at) "
            "VALUES (?, ?, ?, ?, ?)", (_nuovo_id(), chiave, nome, descrizione, adesso))
    for chiave, valore in SETTINGS_DEFAULT.items():
        conn.execute(
            "INSERT OR IGNORE INTO social_system_settings (chiave, valore, aggiornato_at) "
            "VALUES (?, ?, ?)", (chiave, json.dumps(valore), adesso))
    # Tre categorie sempre presenti (seed idempotente, come i pillar/le
    # fonti sopra): unificano la vecchia tipologia fissa (concorso |
    # promozione | generico) in un menu aperto che l'utente puo' estendere
    # da "Categorie" — ogni categoria decide da sola come procurare i
    # fatti (strategia_fatti) e con che struttura scrivere il post.
    riga_promo_vecchia = conn.execute(
        "SELECT id FROM social_content_categories WHERE nome IN ('Promozione', 'Promozioni')"
    ).fetchone()
    if riga_promo_vecchia:
        # Rinomina la categoria "Promozione" (singolare, versione
        # precedente) mantenendo lo stesso id — i contenuti che la
        # referenziano restano collegati. Applica anche lo stile immagine
        # di default, ma solo se non e' gia' stato personalizzato a mano.
        conn.execute(
            "UPDATE social_content_categories SET nome = 'Promozioni', "
            "strategia_fatti = 'promozioni_jobinpa' WHERE id = ?", (riga_promo_vecchia["id"],))
        conn.execute(
            "UPDATE social_content_categories SET stile_immagine = ? "
            "WHERE id = ? AND (stile_immagine IS NULL OR stile_immagine = '')",
            (CATEGORIA_PROMOZIONI_DEFAULT_STILE, riga_promo_vecchia["id"]))
        # Il vecchio stile di default (prima del template "promozione" a
        # grafica intera, vedi images._prompt_promozione_completa) vietava
        # esplicitamente il testo nell'immagine: se e' rimasto esattamente
        # quel valore (mai personalizzato a mano), passa al nuovo default,
        # che non lo vieta piu' — non tocca chi lo ha gia' modificato.
        vecchio_stile_promozioni = (
            "Modern glossy 3D/semi-3D illustration, premium SaaS/tech aesthetic, "
            "soft rounded shapes with gentle shadows and highlights. Background: "
            "smooth gradient from white to light lavender/blue. Accent colors: "
            "purple #7C3AED and institutional blue #0B3D91 on the main "
            "illustrated object, plus small decorative sparkles. Clean and "
            "elegant, not flat vector, not photorealistic, no real people. No "
            "text, no letters, no numbers, no words anywhere in the image. Leave "
            "the bottom half of the composition visually calm and uncluttered, "
            "suitable for overlaid text. Subject: "
        )
        conn.execute(
            "UPDATE social_content_categories SET stile_immagine = ? "
            "WHERE id = ? AND stile_immagine = ?",
            (CATEGORIA_PROMOZIONI_DEFAULT_STILE, riga_promo_vecchia["id"], vecchio_stile_promozioni))
    else:
        crea_categoria(conn, "Promozioni", CATEGORIA_PROMOZIONI_DEFAULT_PROMPT,
                       strategia_fatti="promozioni_jobinpa",
                       struttura_post=CATEGORIA_PROMOZIONI_DEFAULT_STRUTTURA,
                       stile_immagine=CATEGORIA_PROMOZIONI_DEFAULT_STILE)
    if not conn.execute(
            "SELECT 1 FROM social_content_categories WHERE nome = ?", ("Concorsi",)).fetchone():
        crea_categoria(conn, "Concorsi", "", strategia_fatti="bandi_jobinpa")
    if not conn.execute(
            "SELECT 1 FROM social_content_categories WHERE nome = ?", ("Funzionalità",)).fetchone():
        crea_categoria(conn, "Funzionalità", "", strategia_fatti="funzionalita_jobinpa")
    else:
        # Prima dell'API dedicata "Funzionalità" era seminata 'libera':
        # passa alla strategia vera solo se non e' gia' stata personalizzata
        # a mano (mai sovrascrivere una scelta esplicita dell'utente).
        conn.execute(
            "UPDATE social_content_categories SET strategia_fatti = 'funzionalita_jobinpa' "
            "WHERE nome = 'Funzionalità' AND strategia_fatti = 'libera'")
    # Contenuti creati prima di questa migrazione: collegati alla
    # categoria corrispondente alla vecchia tipologia, cosi' continuano a
    # comportarsi esattamente come prima (vedi agents.research/visual,
    # che ora leggono solo categoria_id).
    if conn.execute(
            "SELECT 1 FROM social_content WHERE categoria_id IS NULL LIMIT 1").fetchone():
        mappa_categorie = {r["nome"]: r["id"] for r in conn.execute(
            "SELECT id, nome FROM social_content_categories "
            "WHERE nome IN ('Concorsi', 'Promozioni', 'Funzionalità')")}
        mappa_tipologia = {"concorso": mappa_categorie.get("Concorsi"),
                          "promozione": mappa_categorie.get("Promozioni"),
                          "generico": mappa_categorie.get("Funzionalità")}
        for tipologia, categoria_id in mappa_tipologia.items():
            if categoria_id:
                conn.execute(
                    "UPDATE social_content SET categoria_id = ? "
                    "WHERE tipologia = ? AND categoria_id IS NULL", (categoria_id, tipologia))
    # I due account gestiti (uno per piattaforma): creati subito in stato
    # non_configurato, la checklist in dashboard guida il completamento.
    for piattaforma, nome in (("instagram", "JobInPA (Instagram)"), ("linkedin", "JobInPA (LinkedIn)")):
        esiste = conn.execute(
            "SELECT 1 FROM social_accounts WHERE piattaforma = ? AND is_demo = 0",
            (piattaforma,)).fetchone()
        if not esiste:
            _insert(conn, "social_accounts", {
                "id": _nuovo_id(), "piattaforma": piattaforma, "nome": nome,
                "stato": "non_configurato", "publishing_enabled": 0,
                "creato_at": adesso})
    conn.commit()


# --- Utenti (staff con accesso alla dashboard) -------------------------------

def crea_utente(conn, email, password_hash, *, nome=None, cognome=None, ruolo="viewer"):
    """Solleva sqlite3.IntegrityError se l'email e' gia' registrata."""
    if ruolo not in RUOLI:
        raise ValueError(f"ruolo non valido: {ruolo}")
    cursore = conn.execute(
        "INSERT INTO utenti (email, password_hash, nome, cognome, ruolo, creato_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (email.strip().lower(), password_hash, nome, cognome, ruolo, _adesso()))
    conn.commit()
    return cursore.lastrowid


def utente_per_email(conn, email):
    riga = conn.execute("SELECT * FROM utenti WHERE email = ?",
                        (email.strip().lower(),)).fetchone()
    return dict(riga) if riga is not None else None


def utente_per_id(conn, utente_id):
    riga = conn.execute("SELECT * FROM utenti WHERE id = ?", (utente_id,)).fetchone()
    return dict(riga) if riga is not None else None


def aggiorna_ruolo_utente(conn, utente_id, ruolo):
    if ruolo not in RUOLI:
        raise ValueError(f"ruolo non valido: {ruolo}")
    conn.execute("UPDATE utenti SET ruolo = ? WHERE id = ?", (ruolo, utente_id))
    conn.commit()


def lista_utenti(conn):
    return conn.execute("SELECT id, email, nome, cognome, ruolo, stato, creato_at "
                        "FROM utenti ORDER BY email").fetchall()


# --- Settings ----------------------------------------------------------------

def get_setting(conn, chiave, default=None):
    riga = conn.execute(
        "SELECT valore FROM social_system_settings WHERE chiave = ?", (chiave,)).fetchone()
    if riga is None:
        return default
    return json.loads(riga["valore"])


def set_setting(conn, chiave, valore):
    conn.execute(
        "INSERT INTO social_system_settings (chiave, valore, aggiornato_at) VALUES (?, ?, ?) "
        "ON CONFLICT(chiave) DO UPDATE SET valore = excluded.valore, "
        "aggiornato_at = excluded.aggiornato_at",
        (chiave, json.dumps(valore), _adesso()))
    conn.commit()


def kill_switch_attivo(conn):
    return bool(get_setting(conn, "kill_switch", False))


# --- Audit -------------------------------------------------------------------

_CHIAVI_VIETATE_AUDIT = ("password", "token", "secret", "api_key", "apikey", "authorization")


def audit(conn, azione, *, utente_id=None, agente=None, oggetto_tipo=None,
          oggetto_id=None, stato_prima=None, stato_dopo=None, motivo=None,
          dettagli=None, ip=None):
    """Mai segreti nell'audit: le chiavi sensibili nei dettagli vengono scartate."""
    if dettagli:
        dettagli = {k: v for k, v in dettagli.items()
                    if not any(s in k.lower() for s in _CHIAVI_VIETATE_AUDIT)}
    _insert(conn, "social_audit_logs", {
        "id": _nuovo_id(), "utente_id": utente_id, "agente": agente,
        "azione": azione, "oggetto_tipo": oggetto_tipo, "oggetto_id": oggetto_id,
        "stato_prima": stato_prima, "stato_dopo": stato_dopo, "motivo": motivo,
        "dettagli": json.dumps(dettagli, ensure_ascii=False) if dettagli else None,
        "ip": ip, "creato_at": _adesso()})
    conn.commit()


def audit_recenti(conn, limit=100):
    return conn.execute(
        "SELECT * FROM social_audit_logs ORDER BY creato_at DESC LIMIT ?", (limit,)).fetchall()


# --- Contenuti ---------------------------------------------------------------

def crea_content(conn, titolo, *, pillar_chiave=None, obiettivo=None, brief=None, canali=None,
                 concorso_id=None, creato_da=None, is_demo=False, tipologia="concorso",
                 scadenza_promo=None, promo_dati=None, funzionalita_dati=None, categoria_id=None,
                 filtri_manuali=None, soglia_confidenza=None):
    if tipologia not in TIPOLOGIE_CONTENUTO:
        raise ValueError(f"tipologia non valida: {tipologia}")
    pillar_id = None
    if pillar_chiave:
        riga = conn.execute(
            "SELECT id FROM social_editorial_pillars WHERE chiave = ?", (pillar_chiave,)).fetchone()
        pillar_id = riga["id"] if riga else None
    content_id = _nuovo_id()
    _insert(conn, "social_content", {
        "id": content_id, "titolo": titolo, "pillar_id": pillar_id, "obiettivo": obiettivo,
        "brief": brief, "stato": "IDEA", "canali": json.dumps(canali or list(PIATTAFORME)),
        "concorso_id": concorso_id, "creato_da": creato_da, "tipologia": tipologia,
        "scadenza_promo": scadenza_promo,
        "promo_dati": json.dumps(promo_dati, ensure_ascii=False) if promo_dati else None,
        "funzionalita_dati": json.dumps(funzionalita_dati, ensure_ascii=False)
                             if funzionalita_dati else None,
        "categoria_id": categoria_id,
        "filtri_manuali": json.dumps(filtri_manuali, ensure_ascii=False) if filtri_manuali else None,
        "soglia_confidenza": soglia_confidenza,
        "is_demo": 1 if is_demo else 0, "creato_at": _adesso()})
    conn.commit()
    return content_id


def get_content(conn, content_id):
    return conn.execute(
        "SELECT c.*, p.chiave AS pillar_chiave, p.nome AS pillar_nome "
        "FROM social_content c LEFT JOIN social_editorial_pillars p ON p.id = c.pillar_id "
        "WHERE c.id = ?", (content_id,)).fetchone()


def lista_content(conn, stati=None, limit=200):
    sql = ("SELECT c.*, p.nome AS pillar_nome FROM social_content c "
           "LEFT JOIN social_editorial_pillars p ON p.id = c.pillar_id ")
    parametri = []
    if stati:
        sql += "WHERE c.stato IN (%s) " % ", ".join("?" for _ in stati)
        parametri.extend(stati)
    sql += "ORDER BY c.creato_at DESC LIMIT ?"
    parametri.append(limit)
    return conn.execute(sql, parametri).fetchall()


def aggiorna_content(conn, content_id, **campi):
    consentiti = {"titolo", "obiettivo", "brief", "stato", "classe_rischio", "decisione_rischio",
                  "punteggi_rischio", "canali", "programmato_at", "errore", "bandi_trovati",
                  "concorso_id", "filtri_manuali", "soglia_confidenza"}
    campi = {k: v for k, v in campi.items() if k in consentiti}
    if not campi:
        return
    campi["aggiornato_at"] = _adesso()
    assegnazioni = ", ".join(f"{k} = ?" for k in campi)
    # Funzione di scrittura piu' chiamata di tutto il modulo (ogni fase
    # della pipeline la usa, oltre a ogni azione dell'utente dalla
    # dashboard): stessa contesa possibile di aggiorna_categoria, vedi
    # _esegui_scrittura_con_retry.
    _esegui_scrittura_con_retry(
        conn, f"UPDATE social_content SET {assegnazioni} WHERE id = ?",
        (*campi.values(), content_id))


def elimina_content(conn, content_id, *, utente_id=None):
    """Elimina un contenuto e tutto cio' che ne dipende. Ritorna False se il
    contenuto non esiste (nessun errore, il chiamante decide se e' un 404).

    Con FK ON, la CASCADE dello schema copre da sola varianti/asset(riga)/
    approvazioni(+eventi)/pubblicazioni(+tentativi/metriche/commenti/
    risposte). Vanno ripulite a mano SOLO le tabelle senza FK verso
    social_content (fatti verificati, esecuzioni agente, il collegamento
    dal calendario editoriale) e i job ancora in coda che referenziano
    questo content_id nel payload JSON (non e' una vera FK, e' testo).
    I file immagine generati su disco vengono cancellati anche loro
    (best-effort: un file gia' mancante non blocca l'operazione)."""
    content = get_content(conn, content_id)
    if content is None:
        return False

    audit(conn, "content_eliminato", utente_id=utente_id, oggetto_tipo="content",
          oggetto_id=content_id, stato_prima=content["stato"],
          dettagli={"titolo": content["titolo"], "is_demo": bool(content["is_demo"])})

    for asset in asset_di(conn, content_id):
        try:
            Path(asset["percorso"]).unlink(missing_ok=True)
        except OSError:
            pass  # percorso non valido/non raggiungibile: non blocca la cancellazione

    for job in lista_jobs(conn, limit=1000):
        try:
            payload = json.loads(job["payload"] or "{}")
        except json.JSONDecodeError:
            continue
        if payload.get("content_id") == content_id:
            conn.execute("DELETE FROM social_scheduled_jobs WHERE id = ?", (job["id"],))

    conn.execute("DELETE FROM social_verified_facts WHERE content_id = ?", (content_id,))
    conn.execute("DELETE FROM social_agent_runs WHERE content_id = ?", (content_id,))
    conn.execute("UPDATE social_editorial_plans SET content_id = NULL WHERE content_id = ?",
                 (content_id,))
    conn.execute("DELETE FROM social_content WHERE id = ?", (content_id,))
    conn.commit()
    return True


# --- Categorie personalizzate (prompt + immagini di riferimento) ------------

def _parse_categoria(riga):
    d = dict(riga)
    d["immagini_riferimento"] = json.loads(d["immagini_riferimento"]) if d.get("immagini_riferimento") else []
    return d


def crea_categoria(conn, nome, prompt_ai="", *, immagini_riferimento=None,
                   strategia_fatti="libera", struttura_post=None, stile_immagine=None):
    """Solleva sqlite3.IntegrityError se il nome e' gia' in uso (UNIQUE),
    ValueError se strategia_fatti non e' valida.
    prompt_ai: facoltativo, vuoto = l'AI sceglie liberamente il soggetto
    dell'illustrazione (utile per categorie come "Concorsi" dove varia da
    bando a bando). immagini_riferimento: lista di percorsi locali (0 o
    piu' immagini), passate insieme a OpenAI /v1/images/edits (vedi
    images.py). struttura_post: guida di struttura per il Copywriter
    Agent (vedi agents.copywriting), non un testo fisso. stile_immagine:
    facoltativo, vuoto = usa lo stile fisso di sempre
    (images._STILE_OPENAI_IMAGES, condiviso da tutte le categorie senza
    uno stile proprio); se valorizzato lo SOSTITUISCE del tutto per
    questa categoria (es. per "Promozioni", che ha bisogno di un
    linguaggio visivo diverso da quello istituzionale)."""
    if strategia_fatti not in STRATEGIE_FATTI:
        raise ValueError(f"strategia_fatti non valida: {strategia_fatti}")
    categoria_id = _nuovo_id()
    _insert(conn, "social_content_categories", {
        "id": categoria_id, "nome": nome.strip(), "prompt_ai": prompt_ai.strip(),
        "immagini_riferimento": json.dumps(immagini_riferimento) if immagini_riferimento else None,
        "strategia_fatti": strategia_fatti,
        "struttura_post": struttura_post.strip() if struttura_post else None,
        "stile_immagine": stile_immagine.strip() if stile_immagine else None,
        "creato_at": _adesso()})
    conn.commit()
    return categoria_id


def lista_categorie(conn):
    righe = conn.execute("SELECT * FROM social_content_categories ORDER BY nome").fetchall()
    return [_parse_categoria(r) for r in righe]


def get_categoria(conn, categoria_id):
    riga = conn.execute(
        "SELECT * FROM social_content_categories WHERE id = ?", (categoria_id,)).fetchone()
    return _parse_categoria(riga) if riga is not None else None


def aggiorna_categoria(conn, categoria_id, *, prompt_ai=None, immagini_riferimento=None,
                       strategia_fatti=None, struttura_post=None, stile_immagine=None):
    """Ogni parametro non passato (None) lascia il valore esistente
    invariato. Per svuotare davvero un campo facoltativo si passa una
    stringa vuota "" (struttura_post, stile_immagine) o una lista vuota []
    (immagini_riferimento) — non None, che significa "non toccare"."""
    if strategia_fatti is not None and strategia_fatti not in STRATEGIE_FATTI:
        raise ValueError(f"strategia_fatti non valida: {strategia_fatti}")
    campi = {}
    if prompt_ai is not None:
        campi["prompt_ai"] = prompt_ai.strip()
    if immagini_riferimento is not None:
        campi["immagini_riferimento"] = json.dumps(immagini_riferimento) if immagini_riferimento else None
    if strategia_fatti is not None:
        campi["strategia_fatti"] = strategia_fatti
    if struttura_post is not None:
        campi["struttura_post"] = struttura_post.strip() or None
    if stile_immagine is not None:
        campi["stile_immagine"] = stile_immagine.strip() or None
    if not campi:
        return
    campi["aggiornato_at"] = _adesso()
    assegnazioni = ", ".join(f"{k} = ?" for k in campi)
    _esegui_scrittura_con_retry(
        conn, f"UPDATE social_content_categories SET {assegnazioni} WHERE id = ?",
        (*campi.values(), categoria_id))


def elimina_categoria(conn, categoria_id):
    """Ritorna False se la categoria non esiste. I contenuti che la
    referenziano restano com'erano (categoria_id non e' una FK con CASCADE
    qui: un contenuto gia' generato non deve rompersi se la categoria usata
    viene rimossa in seguito)."""
    riga = get_categoria(conn, categoria_id)
    if riga is None:
        return False
    conn.execute("DELETE FROM social_content_categories WHERE id = ?", (categoria_id,))
    conn.commit()
    return True


def salva_variante(conn, content_id, piattaforma, testo, hashtags=None, call_to_action=None):
    conn.execute(
        "INSERT INTO social_post_variants (id, content_id, piattaforma, testo, hashtags, "
        "call_to_action, creato_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(content_id, piattaforma) DO UPDATE SET testo = excluded.testo, "
        "hashtags = excluded.hashtags, call_to_action = excluded.call_to_action",
        (_nuovo_id(), content_id, piattaforma, testo,
         json.dumps(hashtags or [], ensure_ascii=False), call_to_action, _adesso()))
    conn.commit()


def varianti_di(conn, content_id):
    return conn.execute(
        "SELECT * FROM social_post_variants WHERE content_id = ? ORDER BY piattaforma",
        (content_id,)).fetchall()


def aggiorna_testo_variante(conn, content_id, piattaforma, testo):
    """Modifica manuale del reviewer in fase di approvazione: aggiorna SOLO
    il testo, lascia hashtags/call_to_action invariati (a differenza di
    salva_variante, che e' un upsert pensato per l'output completo del
    Copywriter Agent e li sovrascriverebbe a vuoto se non ripassati)."""
    conn.execute(
        "UPDATE social_post_variants SET testo = ? WHERE content_id = ? AND piattaforma = ?",
        (testo, content_id, piattaforma))
    conn.commit()


def salva_asset(conn, content_id, percorso, *, piattaforma=None, template=None,
                formato=None, provider="template", bando_id=None, url_pubblico=None):
    asset_id = _nuovo_id()
    adesso = _adesso()
    _insert(conn, "social_media_assets", {
        "id": asset_id, "content_id": content_id, "piattaforma": piattaforma,
        "template": template, "formato": formato, "percorso": str(percorso),
        "provider": provider, "bando_id": bando_id, "url_pubblico": url_pubblico,
        "creato_at": adesso, "aggiornato_at": adesso})
    conn.commit()
    return asset_id


def elimina_asset_di(conn, content_id):
    """Cancella tutti gli asset immagine di un contenuto (righe + file su
    disco, best-effort): usato prima di rigenerare le immagini, per non
    accumulare vecchie versioni insieme alle nuove (vedi agents.rigenera_visual)."""
    for asset in asset_di(conn, content_id):
        try:
            Path(asset["percorso"]).unlink(missing_ok=True)
        except OSError:
            pass
    conn.execute("DELETE FROM social_media_assets WHERE content_id = ?", (content_id,))
    conn.commit()


def elimina_asset(conn, content_id, asset_id):
    """Cancella UNA singola immagine del carosello (riga + file su disco,
    best-effort). Se l'immagine era collegata a un bando (bando_id), lo
    toglie anche da content.bandi_trovati: senza, una successiva
    rigenerazione del testo (rigenera_copy) continuerebbe a citare/contare
    un bando di cui l'immagine non esiste piu'. Ritorna False se l'asset
    non esiste o non appartiene a questo contenuto."""
    asset = conn.execute(
        "SELECT * FROM social_media_assets WHERE id = ? AND content_id = ?",
        (asset_id, content_id)).fetchone()
    if asset is None:
        return False
    try:
        Path(asset["percorso"]).unlink(missing_ok=True)
    except OSError:
        pass
    conn.execute("DELETE FROM social_media_assets WHERE id = ?", (asset_id,))
    if asset["bando_id"]:
        content = get_content(conn, content_id)
        bandi = json.loads(content["bandi_trovati"] or "[]")
        bandi = [b for b in bandi if b.get("id") != asset["bando_id"]]
        conn.execute("UPDATE social_content SET bandi_trovati = ? WHERE id = ?",
                    (json.dumps(bandi, ensure_ascii=False), content_id))
    conn.commit()
    return True


def asset_di(conn, content_id):
    return conn.execute(
        "SELECT * FROM social_media_assets WHERE content_id = ? ORDER BY creato_at",
        (content_id,)).fetchall()


def get_asset(conn, content_id, asset_id):
    return conn.execute(
        "SELECT * FROM social_media_assets WHERE id = ? AND content_id = ?",
        (asset_id, content_id)).fetchone()


def aggiorna_asset(conn, asset_id, **campi):
    """Sostituisce i campi di UN asset esistente (stessa riga, nuovo file):
    a differenza di elimina_asset_di + salva_asset, non cambia l'id ne' la
    posizione nell'ordine del carosello (vedi agents.rigenera_immagine_
    singola — rigenerare una sola immagine del carosello senza toccare le
    altre, ne' la loro sequenza). aggiornato_at si aggiorna SEMPRE da solo
    (mai passato dal chiamante, come aggiorna_content): usato in pagina per
    mostrare quando l'immagine e' stata rigenerata l'ultima volta e per
    invalidare la cache del browser sull'URL dell'asset (stabile, /social/
    asset/{id}) — senza, un'immagine rigenerata poteva sembrare invariata
    perche' il browser riusava la versione in cache dello stesso URL."""
    consentiti = {"percorso", "template", "formato", "provider", "url_pubblico"}
    campi = {k: v for k, v in campi.items() if k in consentiti}
    if not campi:
        return
    if "percorso" in campi:
        campi["percorso"] = str(campi["percorso"])
    campi["aggiornato_at"] = _adesso()
    assegnazioni = ", ".join(f"{k} = ?" for k in campi)
    conn.execute(f"UPDATE social_media_assets SET {assegnazioni} WHERE id = ?",
                 (*campi.values(), asset_id))
    conn.commit()


# --- Fonti e fatti ------------------------------------------------------------

def source_domains(conn, solo_attivi=True):
    sql = "SELECT * FROM social_source_domains"
    if solo_attivi:
        sql += " WHERE attivo = 1"
    return conn.execute(sql + " ORDER BY dominio").fetchall()


def source_domain_allowed(conn, host):
    """Un host e' consentito se coincide con un dominio in whitelist o ne e'
    un sottodominio (www.inpa.gov.it copre inpa.gov.it e viceversa no)."""
    host = (host or "").lower().strip(".")
    for riga in source_domains(conn):
        dominio = riga["dominio"].lower()
        if host == dominio or host.endswith("." + dominio):
            return True
    return False


def aggiungi_source_domain(conn, dominio, nome=None):
    conn.execute(
        "INSERT OR IGNORE INTO social_source_domains (id, dominio, nome, attivo, creato_at) "
        "VALUES (?, ?, ?, 1, ?)", (_nuovo_id(), dominio.lower(), nome, _adesso()))
    conn.commit()


def imposta_source_domain(conn, dominio, attivo):
    conn.execute("UPDATE social_source_domains SET attivo = ? WHERE dominio = ?",
                 (1 if attivo else 0, dominio.lower()))
    conn.commit()


def salva_source_item(conn, url, dominio, *, titolo=None, testo=None, tipo="web",
                      content_id=None):
    item_id = _nuovo_id()
    _insert(conn, "social_source_items", {
        "id": item_id, "url": url, "dominio": dominio, "titolo": titolo,
        "testo": testo, "tipo": tipo, "content_id": content_id, "creato_at": _adesso()})
    conn.commit()
    return item_id


def salva_fatto(conn, fatto, *, content_id=None, fonte_url=None, confidenza=0.0,
                conflitto=False, richiede_revisione=False):
    fatto_id = _nuovo_id()
    _insert(conn, "social_verified_facts", {
        "id": fatto_id, "content_id": content_id, "fatto": fatto,
        "fonte_url": fonte_url, "confidenza": confidenza,
        "conflitto": 1 if conflitto else 0,
        "richiede_revisione": 1 if richiede_revisione else 0, "creato_at": _adesso()})
    conn.commit()
    return fatto_id


def fatti_di(conn, content_id):
    return conn.execute(
        "SELECT * FROM social_verified_facts WHERE content_id = ? ORDER BY creato_at",
        (content_id,)).fetchall()


# --- Approvazioni -------------------------------------------------------------

def crea_approval(conn, content_id):
    approval_id = _nuovo_id()
    _insert(conn, "social_approvals", {
        "id": approval_id, "content_id": content_id, "stato": "in_attesa",
        "richiesto_at": _adesso()})
    _insert(conn, "social_approval_events", {
        "id": _nuovo_id(), "approval_id": approval_id, "azione": "richiesta",
        "creato_at": _adesso()})
    conn.commit()
    return approval_id


def approval_aperta_di(conn, content_id):
    return conn.execute(
        "SELECT * FROM social_approvals WHERE content_id = ? AND stato IN "
        "('in_attesa', 'modifiche_richieste') ORDER BY richiesto_at DESC LIMIT 1",
        (content_id,)).fetchone()


def riapri_approval(conn, approval_id):
    """Riporta un'approvazione gia' decisa (tipicamente 'modifiche_richieste')
    di nuovo 'in_attesa': usato quando la pipeline rigenera il contenuto
    dopo una richiesta di modifiche e serve una nuova decisione umana.
    Senza questo, richiedi_approvazione() riusa la riga esistente (trovata
    da approval_aperta_di, che include anche 'modifiche_richieste') senza
    mai resettarne lo stato — il contenuto torna in AWAITING_APPROVAL ma
    l'approvazione resta invisibile nella coda "in_attesa" (bug reale,
    la richiesta risultava aperta sulla scheda del contenuto ma introvabile
    in Revisione)."""
    conn.execute(
        "UPDATE social_approvals SET stato = 'in_attesa', motivo = NULL, "
        "deciso_da = NULL, deciso_at = NULL WHERE id = ?", (approval_id,))
    _insert(conn, "social_approval_events", {
        "id": _nuovo_id(), "approval_id": approval_id, "azione": "riaperta",
        "creato_at": _adesso()})
    conn.commit()


def approvals_in_attesa(conn):
    return conn.execute(
        "SELECT a.*, c.titolo, c.classe_rischio, c.stato AS content_stato "
        "FROM social_approvals a JOIN social_content c ON c.id = a.content_id "
        "WHERE a.stato = 'in_attesa' ORDER BY a.richiesto_at").fetchall()


def decidi_approval(conn, approval_id, stato, utente_id, motivo=None):
    if stato not in {"approvato", "rifiutato", "modifiche_richieste"}:
        raise ValueError(f"stato approvazione non valido: {stato}")
    conn.execute(
        "UPDATE social_approvals SET stato = ?, motivo = ?, deciso_da = ?, deciso_at = ? "
        "WHERE id = ?", (stato, motivo, utente_id, _adesso(), approval_id))
    _insert(conn, "social_approval_events", {
        "id": _nuovo_id(), "approval_id": approval_id, "azione": stato,
        "utente_id": utente_id, "motivo": motivo, "creato_at": _adesso()})
    conn.commit()


def registra_approval_event(conn, approval_id, azione, *, utente_id=None, motivo=None):
    _insert(conn, "social_approval_events", {
        "id": _nuovo_id(), "approval_id": approval_id, "azione": azione,
        "utente_id": utente_id, "motivo": motivo, "creato_at": _adesso()})
    conn.commit()


# --- Pubblicazioni ------------------------------------------------------------

def apri_publication(conn, content_id, piattaforma, modalita):
    """None se esiste gia' una pubblicazione per (contenuto, piattaforma) non
    fallita: e' il lucchetto di idempotenza del Publishing Agent."""
    esistente = conn.execute(
        "SELECT * FROM social_publications WHERE content_id = ? AND piattaforma = ?",
        (content_id, piattaforma)).fetchone()
    if esistente is not None:
        if esistente["stato"] == "fallito":
            conn.execute(
                "UPDATE social_publications SET stato = 'in_corso', errore = NULL, "
                "modalita = ? WHERE id = ?", (modalita, esistente["id"]))
            conn.commit()
            return esistente["id"]
        return None
    pub_id = _nuovo_id()
    _insert(conn, "social_publications", {
        "id": pub_id, "content_id": content_id, "piattaforma": piattaforma,
        "stato": "in_corso", "modalita": modalita, "creato_at": _adesso()})
    conn.commit()
    return pub_id


def chiudi_publication(conn, pub_id, *, esito, remote_id=None, remote_url=None, errore=None):
    stato = "pubblicato" if esito == "ok" else "fallito"
    conn.execute(
        "UPDATE social_publications SET stato = ?, remote_id = ?, remote_url = ?, "
        "errore = ?, pubblicato_at = ? WHERE id = ?",
        (stato, remote_id, remote_url, errore,
         _adesso() if esito == "ok" else None, pub_id))
    _insert(conn, "social_publication_attempts", {
        "id": _nuovo_id(), "publication_id": pub_id,
        "esito": "ok" if esito == "ok" else "errore",
        "dettaglio": errore, "creato_at": _adesso()})
    conn.commit()


def publications_di(conn, content_id):
    return conn.execute(
        "SELECT * FROM social_publications WHERE content_id = ? ORDER BY piattaforma",
        (content_id,)).fetchall()


def lista_publications(conn, stato=None, limit=200):
    sql = ("SELECT pub.*, c.titolo FROM social_publications pub "
           "JOIN social_content c ON c.id = pub.content_id ")
    parametri = []
    if stato:
        sql += "WHERE pub.stato = ? "
        parametri.append(stato)
    sql += "ORDER BY pub.creato_at DESC LIMIT ?"
    parametri.append(limit)
    return conn.execute(sql, parametri).fetchall()


# --- Metriche e commenti ------------------------------------------------------

def salva_metriche(conn, publication_id, metriche):
    _insert(conn, "social_metric_snapshots", {
        "id": _nuovo_id(), "publication_id": publication_id,
        "metriche": json.dumps(metriche, ensure_ascii=False), "rilevato_at": _adesso()})
    conn.commit()


def metriche_di(conn, publication_id):
    return conn.execute(
        "SELECT * FROM social_metric_snapshots WHERE publication_id = ? "
        "ORDER BY rilevato_at DESC", (publication_id,)).fetchall()


def salva_commento(conn, publication_id, testo, *, remote_id=None, autore=None):
    try:
        _insert(conn, "social_comments", {
            "id": _nuovo_id(), "publication_id": publication_id, "remote_id": remote_id,
            "autore": autore, "testo": testo, "creato_at": _adesso()})
        conn.commit()
    except sqlite3.IntegrityError:
        pass  # commento gia' importato (stesso remote_id): niente duplicati


def commenti(conn, stato=None, limit=200):
    sql = ("SELECT com.*, c.titolo, pub.piattaforma FROM social_comments com "
           "JOIN social_publications pub ON pub.id = com.publication_id "
           "JOIN social_content c ON c.id = pub.content_id ")
    parametri = []
    if stato:
        sql += "WHERE com.stato = ? "
        parametri.append(stato)
    sql += "ORDER BY com.creato_at DESC LIMIT ?"
    parametri.append(limit)
    return conn.execute(sql, parametri).fetchall()


def salva_reply_draft(conn, comment_id, testo):
    reply_id = _nuovo_id()
    _insert(conn, "social_reply_drafts", {
        "id": reply_id, "comment_id": comment_id, "testo": testo, "creato_at": _adesso()})
    conn.commit()
    return reply_id


def decidi_reply(conn, reply_id, stato, utente_id):
    if stato not in {"approvata", "rifiutata", "inviata"}:
        raise ValueError(f"stato risposta non valido: {stato}")
    conn.execute(
        "UPDATE social_reply_drafts SET stato = ?, deciso_da = ?, aggiornato_at = ? WHERE id = ?",
        (stato, utente_id, _adesso(), reply_id))
    conn.commit()


def reply_drafts(conn, stato="proposta", limit=100):
    return conn.execute(
        "SELECT r.*, com.testo AS commento_testo, com.autore FROM social_reply_drafts r "
        "JOIN social_comments com ON com.id = r.comment_id WHERE r.stato = ? "
        "ORDER BY r.creato_at DESC LIMIT ?", (stato, limit)).fetchall()


# --- Agent runs / prompt / costi ---------------------------------------------

def apri_agent_run(conn, agente, *, content_id=None, prompt_nome=None,
                   prompt_versione=None, prompt_hash=None, provider=None, modello=None):
    run_id = _nuovo_id()
    _insert(conn, "social_agent_runs", {
        "id": run_id, "agente": agente, "content_id": content_id,
        "prompt_nome": prompt_nome, "prompt_versione": prompt_versione,
        "prompt_hash": prompt_hash, "provider": provider, "modello": modello,
        "iniziato_at": _adesso()})
    conn.commit()
    return run_id


def chiudi_agent_run(conn, run_id, esito, *, dettaglio=None, token_input=None,
                     token_output=None, costo_eur=None):
    conn.execute(
        "UPDATE social_agent_runs SET esito = ?, dettaglio = ?, token_input = ?, "
        "token_output = ?, costo_eur = ?, finito_at = ? WHERE id = ?",
        (esito, dettaglio, token_input, token_output, costo_eur, _adesso(), run_id))
    conn.commit()


def agent_runs_recenti(conn, limit=100):
    return conn.execute(
        "SELECT * FROM social_agent_runs ORDER BY iniziato_at DESC LIMIT ?", (limit,)).fetchall()


def registra_prompt_version(conn, nome, versione, hash_, testo):
    conn.execute(
        "INSERT OR IGNORE INTO social_prompt_versions (id, nome, versione, hash, testo, creato_at) "
        "VALUES (?, ?, ?, ?, ?, ?)", (_nuovo_id(), nome, versione, hash_, testo, _adesso()))
    conn.commit()


def registra_costo(conn, provider, costo_eur, *, modello=None, content_id=None,
                   agente=None, token_input=None, token_output=None, stimato=False):
    _insert(conn, "social_cost_entries", {
        "id": _nuovo_id(), "provider": provider, "modello": modello,
        "content_id": content_id, "agente": agente, "token_input": token_input,
        "token_output": token_output, "costo_eur": costo_eur,
        "stimato": 1 if stimato else 0, "creato_at": _adesso()})
    conn.commit()


def costo_periodo(conn, provider, *, giorni=None):
    """Somma dei costi (EUR) del provider: mese corrente, o ultimi N giorni."""
    if giorni is not None:
        da = (datetime.now(timezone.utc) - timedelta(days=giorni)).isoformat()
    else:
        da = datetime.now(timezone.utc).strftime("%Y-%m-01")
    riga = conn.execute(
        "SELECT COALESCE(SUM(costo_eur), 0) AS totale FROM social_cost_entries "
        "WHERE provider = ? AND creato_at >= ?", (provider, da)).fetchone()
    return riga["totale"]


def riepilogo_costi_per_agente(conn, giorni=30):
    """Costi aggregati per provider+agente sugli ultimi N giorni: quante
    chiamate e quanto costano in totale. Un job schedulato che gira ogni
    pochi minuti puo' produrre decine di righe quasi identiche nel log
    grezzo (report_costi): questo riepilogo da' il quadro d'insieme senza
    doverle scorrere una per una."""
    soglia = (datetime.now(timezone.utc) - timedelta(days=giorni)).isoformat()
    return conn.execute(
        "SELECT provider, COALESCE(agente, '(n/d)') AS agente, COUNT(*) AS chiamate, "
        "SUM(costo_eur) AS costo_totale FROM social_cost_entries WHERE creato_at >= ? "
        "GROUP BY provider, agente ORDER BY costo_totale DESC", (soglia,)).fetchall()


def report_costi(conn, limit=500):
    return conn.execute(
        "SELECT provider, modello, agente, content_id, costo_eur, token_input, "
        "token_output, stimato, creato_at FROM social_cost_entries "
        "ORDER BY creato_at DESC LIMIT ?", (limit,)).fetchall()


# --- Incidenti ---------------------------------------------------------------

def registra_incidente(conn, tipo, dettaglio):
    _insert(conn, "social_incidents", {
        "id": _nuovo_id(), "tipo": tipo, "dettaglio": dettaglio, "creato_at": _adesso()})
    conn.commit()


def incidenti_aperti(conn):
    return conn.execute(
        "SELECT * FROM social_incidents WHERE risolto = 0 ORDER BY creato_at DESC").fetchall()


# --- Account e token ---------------------------------------------------------

def account_per_piattaforma(conn, piattaforma):
    return conn.execute(
        "SELECT * FROM social_accounts WHERE piattaforma = ? AND is_demo = 0",
        (piattaforma,)).fetchone()


def canali_abilitati(conn):
    """Piattaforme con publishing_enabled=True (vedi impostazioni
    Integrazioni): stessa logica gia' usata da web.nuovo_contenuto_form per
    precompilare le checkbox canali, qui condivisa con accetta_plan_entry —
    un contenuto creato accettando un suggerimento del Supervisor deve
    rispettare gli stessi canali abilitati di uno creato a mano, non tutte
    le piattaforme per default (bug segnalato dall'utente: veniva generata
    anche la variante LinkedIn mentre l'account e' disabilitato)."""
    return [p for p in PIATTAFORME
           if (account := account_per_piattaforma(conn, p)) and account["publishing_enabled"]]


def lista_accounts(conn):
    return conn.execute("SELECT * FROM social_accounts ORDER BY piattaforma").fetchall()


def aggiorna_account(conn, account_id, **campi):
    consentiti = {"nome", "identificativo", "stato", "publishing_enabled", "dettagli"}
    campi = {k: v for k, v in campi.items() if k in consentiti}
    if not campi:
        return
    campi["aggiornato_at"] = _adesso()
    assegnazioni = ", ".join(f"{k} = ?" for k in campi)
    conn.execute(f"UPDATE social_accounts SET {assegnazioni} WHERE id = ?",
                 (*campi.values(), account_id))
    conn.commit()


def salva_oauth_token(conn, account_id, tipo, token_cifrato, *, scadenza_at=None, scopes=None):
    """Revoca i token precedenti dello stesso tipo: uno attivo alla volta."""
    conn.execute(
        "UPDATE social_oauth_tokens SET revocato_at = ? WHERE account_id = ? "
        "AND tipo = ? AND revocato_at IS NULL", (_adesso(), account_id, tipo))
    _insert(conn, "social_oauth_tokens", {
        "id": _nuovo_id(), "account_id": account_id, "tipo": tipo,
        "token_cifrato": token_cifrato, "scadenza_at": scadenza_at,
        "scopes": scopes, "creato_at": _adesso()})
    conn.commit()


def oauth_token_attivo(conn, account_id, tipo="access"):
    return conn.execute(
        "SELECT * FROM social_oauth_tokens WHERE account_id = ? AND tipo = ? "
        "AND revocato_at IS NULL ORDER BY creato_at DESC LIMIT 1",
        (account_id, tipo)).fetchone()


def revoca_oauth_tokens(conn, account_id):
    conn.execute(
        "UPDATE social_oauth_tokens SET revocato_at = ? WHERE account_id = ? "
        "AND revocato_at IS NULL", (_adesso(), account_id))
    conn.commit()


# --- Calendario editoriale ----------------------------------------------------

def crea_plan_entry(conn, settimana, tema, *, pillar_chiave=None, obiettivo=None,
                    canali=None, fascia_oraria=None, priorita=0, content_id=None,
                    giorno=None, stato=None, is_demo=False, categoria_id=None):
    """stato di default: 'suggerito' se non c'e' ancora un content_id (proposta
    del Supervisor, in attesa di Accetta/Modifica/Scarta), 'pianificato' se
    content_id e' gia' presente (aggiunta manuale diretta a un giorno).
    categoria_id: la categoria scelta dal Supervisor per questo tema (vedi
    agents.supervisor_pianifica_settimana) — trasferita al contenuto vero
    solo quando il suggerimento viene accettato (vedi accetta_plan_entry)."""
    pillar_id = None
    if pillar_chiave:
        riga = conn.execute("SELECT id FROM social_editorial_pillars WHERE chiave = ?",
                            (pillar_chiave,)).fetchone()
        pillar_id = riga["id"] if riga else None
    if stato is None:
        stato = "pianificato" if content_id else "suggerito"
    entry_id = _nuovo_id()
    _insert(conn, "social_editorial_plans", {
        "id": entry_id, "settimana": settimana, "giorno": giorno, "pillar_id": pillar_id,
        "tema": tema, "obiettivo": obiettivo, "canali": json.dumps(canali or list(PIATTAFORME)),
        "fascia_oraria": fascia_oraria, "priorita": priorita, "content_id": content_id,
        "stato": stato, "is_demo": 1 if is_demo else 0, "creato_at": _adesso(),
        "categoria_id": categoria_id})
    conn.commit()
    return entry_id


def plan_entry(conn, entry_id):
    return conn.execute(
        "SELECT pl.*, p.nome AS pillar_nome, p.chiave AS pillar_chiave "
        "FROM social_editorial_plans pl "
        "LEFT JOIN social_editorial_pillars p ON p.id = pl.pillar_id "
        "WHERE pl.id = ?", (entry_id,)).fetchone()


def accetta_plan_entry(conn, entry_id, *, tema=None, obiettivo=None, brief=None,
                       pillar_chiave=None, giorno=None, creato_da=None, avvia_pipeline=True,
                       categoria_id=None):
    """Trasforma un suggerimento ('suggerito', content_id NULL) in un
    contenuto vero: crea social_content e collega la voce di calendario, con
    eventuali modifiche (tema/obiettivo/brief/pillar/giorno/categoria,
    tipicamente dalla card del suggerimento in Calendario) applicate prima
    di creare il contenuto. Il brief e' facoltativo: il Supervisor propone
    solo tema+obiettivo, non un brief in linguaggio naturale — senza, la
    ricerca lavora sui bandi piu' recenti invece che su criteri specifici
    (comportamento invariato, nessun annullamento automatico).

    categoria_id: se non passato esplicitamente, usa quella scelta dal
    Supervisor (voce.categoria_id) — SENZA categoria, il contenuto perdeva
    tutte le personalizzazioni configurate nel backoffice (prompt
    illustrazione, stile, struttura del post: segnalato dall'utente, un
    "tema concorsi" generato dal piano settimanale non seguiva affatto lo
    schema della categoria "Concorsi").

    I canali sono SEMPRE quelli davvero abilitati (vedi canali_abilitati),
    mai tutte le piattaforme per default: creare il contenuto qui usava
    prima il default di crea_content (tutte), generando anche la variante
    LinkedIn pur con l'account disabilitato (bug segnalato dall'utente).

    Ritorna il content_id creato, o None se la voce non esiste o non e'
    piu' un suggerimento in attesa."""
    voce = plan_entry(conn, entry_id)
    if voce is None or voce["content_id"] is not None:
        return None
    tema = tema or voce["tema"]
    obiettivo = obiettivo or voce["obiettivo"]
    pillar_chiave = pillar_chiave or voce["pillar_chiave"]
    categoria_id = categoria_id if categoria_id is not None else voce["categoria_id"]
    content_id = crea_content(conn, tema, pillar_chiave=pillar_chiave, obiettivo=obiettivo,
                              brief=brief, creato_da=creato_da, categoria_id=categoria_id,
                              canali=canali_abilitati(conn))
    aggiorna_plan_entry(conn, entry_id, tema=tema, obiettivo=obiettivo, content_id=content_id,
                       stato="pianificato", giorno=giorno or voce["giorno"],
                       pillar_chiave=pillar_chiave, categoria_id=categoria_id)
    if avvia_pipeline:
        crea_job(conn, "pipeline", {"content_id": content_id})
    return content_id


def elimina_plan_entry(conn, entry_id):
    """Scarta una voce di calendario (suggerimento AI non voluto, o voce
    manuale). Se era gia' collegata a un contenuto vero, il contenuto NON
    viene toccato: solo scollegato dal calendario (usa elimina_content per
    cancellare anche il contenuto)."""
    righe = conn.execute("DELETE FROM social_editorial_plans WHERE id = ?",
                         (entry_id,)).rowcount
    conn.commit()
    return righe > 0


def plan_settimana(conn, settimana):
    return conn.execute(
        "SELECT pl.*, p.nome AS pillar_nome, p.chiave AS pillar_chiave "
        "FROM social_editorial_plans pl "
        "LEFT JOIN social_editorial_pillars p ON p.id = pl.pillar_id "
        "WHERE pl.settimana = ? ORDER BY pl.priorita DESC, pl.creato_at",
        (settimana,)).fetchall()


def conteggio_suggerimenti_per_settimana(conn):
    """Numero di suggerimenti in attesa ('suggerito') per ciascuna
    settimana: usato in Calendario per segnalare quando la settimana
    visualizzata e' vuota ma il Supervisor Agent ne ha proposti altrove
    (es. la settimana successiva), altrimenti invisibili finche' non si
    clicca avanti/indietro per caso."""
    righe = conn.execute(
        "SELECT settimana, COUNT(*) AS n FROM social_editorial_plans "
        "WHERE stato = 'suggerito' GROUP BY settimana ORDER BY settimana").fetchall()
    return {r["settimana"]: r["n"] for r in righe}


def plan_mese(conn, prefisso_mese):
    """prefisso_mese = 'YYYY-MM': tutte le settimane che iniziano in quel mese."""
    return conn.execute(
        "SELECT pl.*, p.nome AS pillar_nome FROM social_editorial_plans pl "
        "LEFT JOIN social_editorial_pillars p ON p.id = pl.pillar_id "
        "WHERE pl.settimana LIKE ? ORDER BY pl.settimana, pl.priorita DESC",
        (prefisso_mese + "%",)).fetchall()


def aggiorna_plan_entry(conn, entry_id, *, pillar_chiave=None, **campi):
    consentiti = {"tema", "obiettivo", "fascia_oraria", "priorita", "stato", "content_id", "giorno",
                 "categoria_id"}
    campi = {k: v for k, v in campi.items() if k in consentiti}
    if pillar_chiave is not None:
        riga = conn.execute("SELECT id FROM social_editorial_pillars WHERE chiave = ?",
                            (pillar_chiave,)).fetchone()
        campi["pillar_id"] = riga["id"] if riga else None
    if not campi:
        return
    assegnazioni = ", ".join(f"{k} = ?" for k in campi)
    conn.execute(f"UPDATE social_editorial_plans SET {assegnazioni} WHERE id = ?",
                 (*campi.values(), entry_id))
    conn.commit()


def pillars(conn):
    return conn.execute(
        "SELECT * FROM social_editorial_pillars WHERE attivo = 1 ORDER BY chiave").fetchall()


# --- Job persistenti ----------------------------------------------------------

def crea_job(conn, tipo, payload=None, *, esegui_at=None, max_tentativi=5):
    job_id = _nuovo_id()
    _insert(conn, "social_scheduled_jobs", {
        "id": job_id, "tipo": tipo,
        "payload": json.dumps(payload or {}, ensure_ascii=False),
        "esegui_at": esegui_at or _adesso(), "max_tentativi": max_tentativi,
        "creato_at": _adesso()})
    conn.commit()
    return job_id


def content_con_programmato_at(conn):
    """Contenuti con programmato_at valorizzato (SCHEDULED e stati
    successivi: PUBLISHING/PUBLISHED/PARTIALLY_PUBLISHED/PUBLISH_FAILED) —
    per il Calendario, che altrimenti mostra SOLO i suggerimenti del piano
    editoriale (plan_settimana): un contenuto creato direttamente da
    'Nuovo contenuto' (non da un suggerimento accettato) una volta
    programmato o pubblicato non compariva mai nel calendario."""
    return conn.execute(
        "SELECT * FROM social_content WHERE programmato_at IS NOT NULL "
        "ORDER BY programmato_at").fetchall()


def riprogramma_pubblicazione(conn, content_id, nuovo_orario_iso):
    """Sposta la pubblicazione programmata a un nuovo orario: aggiorna sia
    content.programmato_at sia l'esegui_at del job 'publish' pending gia'
    in coda (creato da agents.programma_pubblicazione) — senza toccare il
    job, il contenuto mostrerebbe il nuovo orario ma verrebbe comunque
    pubblicato a quello vecchio."""
    conn.execute(
        "UPDATE social_scheduled_jobs SET esegui_at = ?, aggiornato_at = ? "
        "WHERE tipo = 'publish' AND stato = 'pending' AND payload LIKE ?",
        (nuovo_orario_iso, _adesso(), f"%{content_id}%"))
    conn.execute("UPDATE social_content SET programmato_at = ?, aggiornato_at = ? WHERE id = ?",
                (nuovo_orario_iso, _adesso(), content_id))
    conn.commit()


def prendi_job(conn, owner, *, lock_timeout_minuti=15):
    """Reclama atomicamente il prossimo job eseguibile (pending scaduto, o
    running con lock piu' vecchio del timeout = processo morto, recovery)."""
    adesso = _adesso()
    lock_scaduto = (datetime.now(timezone.utc)
                    - timedelta(minutes=lock_timeout_minuti)).isoformat()
    riga = conn.execute(
        "SELECT id FROM social_scheduled_jobs WHERE "
        "(stato = 'pending' AND esegui_at <= ?) OR (stato = 'running' AND lock_at < ?) "
        "ORDER BY esegui_at LIMIT 1", (adesso, lock_scaduto)).fetchone()
    if riga is None:
        return None
    aggiornate = conn.execute(
        "UPDATE social_scheduled_jobs SET stato = 'running', lock_owner = ?, lock_at = ?, "
        "tentativi = tentativi + 1, aggiornato_at = ? "
        "WHERE id = ? AND ((stato = 'pending' AND esegui_at <= ?) OR "
        "(stato = 'running' AND lock_at < ?))",
        (owner, adesso, adesso, riga["id"], adesso, lock_scaduto)).rowcount
    conn.commit()
    if not aggiornate:
        return None  # un altro worker l'ha preso fra SELECT e UPDATE
    return conn.execute("SELECT * FROM social_scheduled_jobs WHERE id = ?",
                        (riga["id"],)).fetchone()


def chiudi_job(conn, job_id, esito, *, errore=None, backoff_base_minuti=5):
    """done, oppure retry con backoff esponenziale finche' restano tentativi,
    poi dead (dead-letter state: visibile in dashboard, mai ritentato da solo)."""
    job = conn.execute("SELECT * FROM social_scheduled_jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        return
    if esito == "ok":
        conn.execute(
            "UPDATE social_scheduled_jobs SET stato = 'done', lock_owner = NULL, "
            "ultimo_errore = NULL, aggiornato_at = ? WHERE id = ?", (_adesso(), job_id))
    elif job["tentativi"] >= job["max_tentativi"]:
        conn.execute(
            "UPDATE social_scheduled_jobs SET stato = 'dead', lock_owner = NULL, "
            "ultimo_errore = ?, aggiornato_at = ? WHERE id = ?",
            (errore, _adesso(), job_id))
    else:
        ritardo = backoff_base_minuti * (2 ** max(0, job["tentativi"] - 1))
        prossimo = (datetime.now(timezone.utc) + timedelta(minutes=ritardo)).isoformat()
        conn.execute(
            "UPDATE social_scheduled_jobs SET stato = 'pending', lock_owner = NULL, "
            "esegui_at = ?, ultimo_errore = ?, aggiornato_at = ? WHERE id = ?",
            (prossimo, errore, _adesso(), job_id))
    conn.commit()


def job_in_corso(conn, tipo, payload_contiene=None):
    """True se esiste un job pending o running di questo tipo (e, se
    indicato, il cui payload JSON contiene questa sottostringa — usato per
    il banner 'generazione in corso' con auto-refresh nel Calendario)."""
    query = "SELECT 1 FROM social_scheduled_jobs WHERE tipo = ? AND stato IN ('pending', 'running')"
    parametri = [tipo]
    if payload_contiene:
        query += " AND payload LIKE ?"
        parametri.append(f"%{payload_contiene}%")
    return conn.execute(query, parametri).fetchone() is not None


def lista_jobs(conn, stati=None, limit=200):
    sql = "SELECT * FROM social_scheduled_jobs "
    parametri = []
    if stati:
        sql += "WHERE stato IN (%s) " % ", ".join("?" for _ in stati)
        parametri.extend(stati)
    sql += "ORDER BY esegui_at DESC LIMIT ?"
    parametri.append(limit)
    return conn.execute(sql, parametri).fetchall()


# --- Email log ---------------------------------------------------------------

def registra_email(conn, destinatari, oggetto, corpo, *, riferimento=None,
                   esito="inviata", dettaglio=None):
    _insert(conn, "social_email_notifications", {
        "id": _nuovo_id(), "destinatari": json.dumps(destinatari),
        "oggetto": oggetto, "corpo": corpo, "riferimento": riferimento,
        "esito": esito, "dettaglio": dettaglio, "creato_at": _adesso()})
    conn.commit()


def email_recenti(conn, limit=100):
    return conn.execute(
        "SELECT * FROM social_email_notifications ORDER BY creato_at DESC LIMIT ?",
        (limit,)).fetchall()
