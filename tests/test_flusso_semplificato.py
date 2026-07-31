"""Semplificazioni al flusso idea -> pubblicazione, segnalate dall'utente
dopo aver usato la dashboard end-to-end:
1. "Nuovo contenuto" ha un solo pulsante, che crea E avvia sempre la pipeline;
2. il bottone "Avvia pipeline" non deve comparire insieme al banner "in coda";
3. i contatori nel menu (Revisione/Pubblicazioni) mostrano cosa c'e' da fare;
4. una pubblicazione programmata e' riprogrammabile;
5. il Calendario mostra anche i contenuti programmati/pubblicati creati
   fuori dal piano editoriale AI, non solo i suggerimenti."""

import json
import re
from datetime import datetime, timedelta, timezone

import auth
import pytest

from social import agents, db_social, llm, models, state_machine
from social.images import MockImageProvider


def _content_approvato(conn, titolo, canali=None):
    """Contenuto portato fino ad APPROVED (percorso obbligato prima di
    SCHEDULED, vedi state_machine.TRANSIZIONI): programma_pubblicazione()
    parte sempre da li'."""
    content_id = db_social.crea_content(conn, titolo, canali=canali)
    for stato in ("RESEARCHING", "DRAFTING", "DRAFT_READY", "GENERATING_VISUAL",
                  "QUALITY_CHECK", "APPROVED"):
        state_machine.transisci(conn, content_id, stato)
    return content_id


@pytest.fixture
def client(conn, tmp_db_path):
    from fastapi.testclient import TestClient
    from app import app as fastapi_app
    from deps import ottieni_conn

    def conn_test():
        connessione = db_social.connect(tmp_db_path)
        try:
            yield connessione
        finally:
            connessione.close()

    fastapi_app.dependency_overrides[ottieni_conn] = conn_test
    with TestClient(fastapi_app) as client:
        yield client
    fastapi_app.dependency_overrides.clear()


def _login(client, email, password="Password123!"):
    client.post("/social/login", data={"email": email, "password": password})


def _csrf(client, url="/social/contenuti/nuovo"):
    pagina = client.get(url).text
    return re.search(r'name="csrf" value="([0-9a-f]+)"', pagina).group(1)


# --- 1. Un solo pulsante: crea e avvia sempre --------------------------------

def test_crea_contenuto_avvia_sempre_la_pipeline(conn, client):
    db_social.crea_utente(conn, "editor-flusso@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-flusso@test.local")
    csrf = _csrf(client)
    categoria_id = next(c["id"] for c in db_social.lista_categorie(conn) if c["nome"] == "Concorsi")

    r = client.post("/social/contenuti", data={
        "titolo": "Tema di prova", "brief": "Brief di prova", "categoria_id": categoria_id,
        "csrf": csrf,
    }, follow_redirects=False)

    assert r.status_code == 303
    location = r.headers["location"]
    assert location.endswith("?avviata=1")
    content_id = location.split("/")[-1].split("?")[0]
    assert db_social.job_in_corso(conn, "pipeline", content_id)


# --- Canali: niente spreco AI per un canale non abilitato in Impostazioni ----

def test_nuovo_contenuto_precompila_solo_i_canali_abilitati(conn, client):
    """LinkedIn non ancora abilitato in Impostazioni (kill switch per
    account, vedi social_accounts.publishing_enabled): il checkbox parte
    deselezionato, cosi' un nuovo contenuto non genera per sbaglio testo/
    immagine (e relativo costo AI) per un canale su cui non si puo'
    comunque pubblicare (segnalato dall'utente per LinkedIn)."""
    db_social.crea_utente(conn, "editor-canali@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    account_instagram = db_social.account_per_piattaforma(conn, "instagram")
    db_social.aggiorna_account(conn, account_instagram["id"], publishing_enabled=1)
    _login(client, "editor-canali@test.local")

    pagina = client.get("/social/contenuti/nuovo").text

    checkbox_instagram = re.search(
        r'<input type="checkbox" name="canali" value="instagram"[^>]*>', pagina).group(0)
    checkbox_linkedin = re.search(
        r'<input type="checkbox" name="canali" value="linkedin"[^>]*>', pagina).group(0)
    assert "checked" in checkbox_instagram
    assert "checked" not in checkbox_linkedin


def test_crea_contenuto_con_solo_instagram_selezionato(conn, client):
    db_social.crea_utente(conn, "editor-canali2@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-canali2@test.local")
    csrf = _csrf(client)
    categoria_id = next(c["id"] for c in db_social.lista_categorie(conn) if c["nome"] == "Concorsi")

    r = client.post("/social/contenuti", data={
        "titolo": "Solo Instagram", "categoria_id": categoria_id,
        "canali": ["instagram"], "csrf": csrf,
    }, follow_redirects=False)

    content_id = r.headers["location"].split("/")[-1].split("?")[0]
    content = db_social.get_content(conn, content_id)
    assert json.loads(content["canali"]) == ["instagram"]


def test_crea_contenuto_senza_campo_canali_usa_il_default_di_sempre(conn, client):
    """Retrocompatibilita': se il form non invia affatto il campo "canali"
    (chiamata diretta, o form non aggiornato), il comportamento resta
    quello di sempre (entrambe le piattaforme) — nessuna rottura per chi
    non passa mai da questo campo."""
    db_social.crea_utente(conn, "editor-canali3@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-canali3@test.local")
    csrf = _csrf(client)
    categoria_id = next(c["id"] for c in db_social.lista_categorie(conn) if c["nome"] == "Concorsi")

    r = client.post("/social/contenuti", data={
        "titolo": "Nessun campo canali", "categoria_id": categoria_id, "csrf": csrf,
    }, follow_redirects=False)

    content_id = r.headers["location"].split("/")[-1].split("?")[0]
    content = db_social.get_content(conn, content_id)
    assert set(json.loads(content["canali"])) == {"instagram", "linkedin"}


# --- 2. "Avvia pipeline" non compare col banner "in coda" -------------------

def test_pagina_contenuto_non_mostra_bottone_avvia_se_appena_in_coda(conn, client):
    db_social.crea_utente(conn, "editor-flusso2@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-flusso2@test.local")
    csrf = _csrf(client)
    categoria_id = next(c["id"] for c in db_social.lista_categorie(conn) if c["nome"] == "Concorsi")
    r = client.post("/social/contenuti", data={
        "titolo": "Tema due", "categoria_id": categoria_id, "csrf": csrf,
    }, follow_redirects=False)
    location = r.headers["location"]

    pagina = client.get(location).text

    assert "Pipeline in coda" in pagina
    assert "Avvia pipeline agenti" not in pagina


# --- 3. Contatori nel menu ----------------------------------------------------

def test_badge_revisione_mostra_le_approvazioni_in_attesa(conn, client):
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.ValutazioneRischio, models.ValutazioneRischio(
        classe="giallo", punteggio_accuratezza=0.7, punteggio_brand=0.7,
        punteggio_conformita=0.7, motivi=[]))
    content_id = db_social.crea_content(conn, "Da rivedere")
    agents.esegui_pipeline(conn, content_id, provider=provider,
                           image_provider=MockImageProvider())

    db_social.crea_utente(conn, "viewer-badge@test.local",
                          auth.hash_password("Password123!"), ruolo="viewer")
    _login(client, "viewer-badge@test.local")

    pagina = client.get("/social/").text
    assert re.search(r'Revisione\s*<span class="badge rosso"[^>]*>1</span>', pagina)


def test_badge_pubblicazioni_conta_approvati_e_falliti(conn, client):
    _content_approvato(conn, "Pronto da pubblicare")

    db_social.crea_utente(conn, "viewer-badge2@test.local",
                          auth.hash_password("Password123!"), ruolo="viewer")
    _login(client, "viewer-badge2@test.local")

    pagina = client.get("/social/").text
    assert re.search(r'Pubblicazioni\s*<span class="badge rosso"[^>]*>1</span>', pagina)


def test_badge_pubblicazioni_conta_anche_i_programmati(conn, client):
    """Un contenuto SCHEDULED (programmato, non ancora pubblicato) deve
    contare nel badge: prima veniva contato solo APPROVED, un post
    programmato per il futuro spariva dal contatore appena schedulato
    (segnalato dall'utente)."""
    content_id = _content_approvato(conn, "Programmato per il futuro", canali=["instagram"])
    agents.programma_pubblicazione(conn, content_id,
                                   quando=datetime.now(timezone.utc) + timedelta(days=1))

    db_social.crea_utente(conn, "viewer-badge3@test.local",
                          auth.hash_password("Password123!"), ruolo="viewer")
    _login(client, "viewer-badge3@test.local")

    pagina = client.get("/social/").text
    assert re.search(r'Pubblicazioni\s*<span class="badge rosso"[^>]*>1</span>', pagina)


# --- 4. Riprogrammazione -------------------------------------------------------

def test_riprogramma_pubblicazione_aggiorna_content_e_job(conn):
    content_id = _content_approvato(conn, "Da riprogrammare", canali=["instagram"])
    quando = datetime.now(timezone.utc) + timedelta(days=1)
    agents.programma_pubblicazione(conn, content_id, quando=quando)
    nuovo_orario = (quando + timedelta(days=2)).isoformat()

    db_social.riprogramma_pubblicazione(conn, content_id, nuovo_orario)

    content = db_social.get_content(conn, content_id)
    assert content["programmato_at"] == nuovo_orario
    job = next(j for j in db_social.lista_jobs(conn, limit=50)
              if j["tipo"] == "publish" and content_id in j["payload"])
    assert job["esegui_at"] == nuovo_orario


def test_route_riprogramma_aggiorna_la_data(conn, client):
    content_id = _content_approvato(conn, "Da riprogrammare via web", canali=["instagram"])
    quando = datetime.now(timezone.utc) + timedelta(days=1)
    agents.programma_pubblicazione(conn, content_id, quando=quando)

    db_social.crea_utente(conn, "publisher-flusso@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "publisher-flusso@test.local")
    csrf = _csrf(client)

    r = client.post(f"/social/contenuti/{content_id}/riprogramma",
                    data={"quando": "2026-09-01T10:00", "csrf": csrf},
                    follow_redirects=False)

    assert r.status_code == 303
    content = db_social.get_content(conn, content_id)
    assert content["programmato_at"] is not None
    assert content["programmato_at"] != quando.isoformat()


def test_route_riprogramma_rifiutata_se_non_schedulato(conn, client):
    content_id = db_social.crea_content(conn, "Ancora idea")
    db_social.crea_utente(conn, "publisher-flusso2@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "publisher-flusso2@test.local")
    csrf = _csrf(client)

    r = client.post(f"/social/contenuti/{content_id}/riprogramma",
                    data={"quando": "2026-09-01T10:00", "csrf": csrf},
                    follow_redirects=False)

    assert r.status_code == 409


def test_pagina_pubblicazioni_mostra_form_riprogramma_per_schedulati(conn, client):
    content_id = _content_approvato(conn, "Da mostrare in Pubblicazioni", canali=["instagram"])
    agents.programma_pubblicazione(conn, content_id,
                                   quando=datetime.now(timezone.utc) + timedelta(days=1))
    db_social.crea_utente(conn, "publisher-flusso3@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "publisher-flusso3@test.local")

    pagina = client.get("/social/pubblicazioni").text

    assert f"/social/contenuti/{content_id}/riprogramma" in pagina
    assert 'type="datetime-local"' in pagina


# --- 5. Calendario mostra anche i contenuti fuori dal piano AI ---------------

def test_content_con_programmato_at_esclude_i_non_programmati(conn):
    db_social.crea_content(conn, "Senza data")
    content_id = _content_approvato(conn, "Con data", canali=["instagram"])
    agents.programma_pubblicazione(conn, content_id,
                                   quando=datetime.now(timezone.utc) + timedelta(days=1))
    risultato = db_social.content_con_programmato_at(conn)
    assert [c["id"] for c in risultato] == [content_id]


def test_calendario_mostra_contenuto_programmato_non_legato_al_piano(conn, client):
    """Un contenuto creato da 'Nuovo contenuto' (nessuna voce di
    social_editorial_plans) deve comunque comparire nel Calendario una
    volta programmato — prima non compariva mai."""
    lunedi = _prossimo_lunedi()
    content_id = _content_approvato(conn, "Fuori dal piano AI", canali=["instagram"])
    quando_locale = datetime.combine(lunedi + timedelta(days=2), datetime.min.time(),
                                     tzinfo=timezone.utc) + timedelta(hours=10)
    agents.programma_pubblicazione(conn, content_id, quando=quando_locale)

    db_social.crea_utente(conn, "viewer-calendario@test.local",
                          auth.hash_password("Password123!"), ruolo="viewer")
    _login(client, "viewer-calendario@test.local")

    pagina = client.get(f"/social/calendario?settimana={lunedi.isoformat()}").text
    assert "Fuori dal piano AI" in pagina


def _prossimo_lunedi():
    oggi = datetime.now(timezone.utc).date()
    return oggi - timedelta(days=oggi.weekday()) + timedelta(weeks=1)
