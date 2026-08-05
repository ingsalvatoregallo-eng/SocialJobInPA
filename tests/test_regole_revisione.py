"""Regole del Quality & Risk Agent "addestrate" dai revisori umani (vedi
web.conferma_alert_reviewer/rifiuta_alert_reviewer in test_revisione_ux.py,
e la pagina /social/regole qui): un dubbio del giudizio AI confermato
diventa un vincolo applicato SEMPRE, uno rifiutato un'esenzione — scope
GLOBALE (segnalato dall'utente: vuole una lista sola semplice da gestire,
non per categoria/contenuto)."""

import re

import auth
import pytest

from social import agents, db_social, llm, models


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


def _csrf(client, url="/social/regole"):
    pagina = client.get(url).text
    return re.search(r'name="csrf" value="([0-9a-f]+)"', pagina).group(1)


# --- db_social: CRUD ----------------------------------------------------

def test_crea_regola_revisione_tipo_non_valido_solleva(conn):
    with pytest.raises(ValueError):
        db_social.crea_regola_revisione(conn, "testo", "boh")


def test_crea_regola_revisione_salva_attiva_di_default(conn):
    regola_id = db_social.crea_regola_revisione(conn, "Non citare prezzi senza fonte", "vincolo")
    regole = db_social.lista_regole_revisione(conn)
    assert len(regole) == 1
    assert regole[0]["id"] == regola_id
    assert regole[0]["stato"] == "attiva"
    assert regole[0]["origine_content_id"] is None


def test_regole_revisione_attive_filtra_per_tipo_e_stato(conn):
    v = db_social.crea_regola_revisione(conn, "Vincolo attivo", "vincolo")
    db_social.crea_regola_revisione(conn, "Esenzione attiva", "esenzione")
    disattivato = db_social.crea_regola_revisione(conn, "Vincolo disattivato", "vincolo")
    db_social.aggiorna_regola_revisione(conn, disattivato, stato="disattivata")

    solo_vincoli = db_social.regole_revisione_attive(conn, tipo="vincolo")
    assert [r["id"] for r in solo_vincoli] == [v]

    tutte_attive = db_social.regole_revisione_attive(conn)
    assert len(tutte_attive) == 2


def test_aggiorna_regola_revisione_cambia_testo(conn):
    regola_id = db_social.crea_regola_revisione(conn, "Testo originale", "vincolo")
    db_social.aggiorna_regola_revisione(conn, regola_id, testo="Testo corretto")
    regole = db_social.lista_regole_revisione(conn)
    assert regole[0]["testo"] == "Testo corretto"


def test_elimina_regola_revisione(conn):
    regola_id = db_social.crea_regola_revisione(conn, "Da eliminare", "esenzione")
    db_social.elimina_regola_revisione(conn, regola_id)
    assert db_social.lista_regole_revisione(conn) == []


# --- agents.quality_risk: le regole attive entrano nel prompt -----------

def _content_con_variante(conn, titolo="Tema"):
    content_id = db_social.crea_content(conn, titolo, canali=["instagram"])
    db_social.salva_variante(conn, content_id, "instagram", "Testo del post di prova")
    return content_id


def _risultato():
    return models.RisultatoRicerca(
        fatti=[models.FattoVerificato(fatto="fatto di prova", confidenza=0.9)], sintesi="Sintesi.")


def test_quality_risk_inietta_i_vincoli_attivi(conn):
    db_social.crea_regola_revisione(conn, "Non citare mai un prezzo senza fonte", "vincolo")
    content_id = _content_con_variante(conn)
    provider = llm.MockLLMProvider(conn)
    agents.quality_risk(conn, content_id, _risultato(), provider=provider)
    _, user_prompt, schema = next(c for c in provider.chiamate if c[2] is models.ValutazioneRischio)
    assert "Non citare mai un prezzo senza fonte" in user_prompt
    assert "confermati da un revisore umano" in user_prompt


def test_quality_risk_inietta_le_esenzioni_attive(conn):
    db_social.crea_regola_revisione(conn, "Menzionare una scadenza non e' un claim commerciale",
                                    "esenzione")
    content_id = _content_con_variante(conn)
    provider = llm.MockLLMProvider(conn)
    agents.quality_risk(conn, content_id, _risultato(), provider=provider)
    _, user_prompt, _ = provider.chiamate[0]
    assert "Menzionare una scadenza non e' un claim commerciale" in user_prompt
    assert "NON problematici" in user_prompt


def test_quality_risk_ignora_le_regole_disattivate(conn):
    regola_id = db_social.crea_regola_revisione(conn, "Regola spenta", "vincolo")
    db_social.aggiorna_regola_revisione(conn, regola_id, stato="disattivata")
    content_id = _content_con_variante(conn)
    provider = llm.MockLLMProvider(conn)
    agents.quality_risk(conn, content_id, _risultato(), provider=provider)
    _, user_prompt, _ = provider.chiamate[0]
    assert "Regola spenta" not in user_prompt


def test_quality_risk_senza_regole_non_aggiunge_sezioni_vuote(conn):
    content_id = _content_con_variante(conn)
    provider = llm.MockLLMProvider(conn)
    agents.quality_risk(conn, content_id, _risultato(), provider=provider)
    _, user_prompt, _ = provider.chiamate[0]
    assert "confermati da un revisore umano" not in user_prompt
    assert "NON problematici" not in user_prompt


# --- pagina /social/regole ------------------------------------------------

def test_pagina_regole_richiede_admin(conn, client):
    db_social.crea_utente(conn, "editor-regole-pagina@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-regole-pagina@test.local")
    r = client.get("/social/regole")
    assert r.status_code == 403


def test_pagina_regole_elenca_le_regole_esistenti(conn, client):
    db_social.crea_regola_revisione(conn, "Regola visibile in pagina", "vincolo")
    db_social.crea_utente(conn, "admin-regole1@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-regole1@test.local")

    pagina = client.get("/social/regole").text
    assert "Regola visibile in pagina" in pagina


def test_crea_regola_da_pagina(conn, client):
    db_social.crea_utente(conn, "admin-regole2@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-regole2@test.local")
    csrf = _csrf(client)

    r = client.post("/social/regole", data={
        "testo": "Non usare mai il superlativo 'il migliore'", "tipo": "vincolo", "csrf": csrf,
    }, follow_redirects=False)

    assert r.status_code == 303
    regole = db_social.lista_regole_revisione(conn)
    assert regole[0]["testo"] == "Non usare mai il superlativo 'il migliore'"
    assert regole[0]["origine_content_id"] is None


def test_crea_regola_da_pagina_tipo_non_valido_400(conn, client):
    db_social.crea_utente(conn, "admin-regole3@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-regole3@test.local")
    csrf = _csrf(client)

    r = client.post("/social/regole", data={
        "testo": "Qualsiasi", "tipo": "boh", "csrf": csrf,
    }, follow_redirects=False)

    assert r.status_code == 400
    assert db_social.lista_regole_revisione(conn) == []


def test_toggle_regola_disattiva_e_riattiva(conn, client):
    regola_id = db_social.crea_regola_revisione(conn, "Regola da mettere in pausa", "vincolo")
    db_social.crea_utente(conn, "admin-regole4@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-regole4@test.local")
    csrf = _csrf(client)

    client.post(f"/social/regole/{regola_id}/toggle", data={"csrf": csrf}, follow_redirects=False)
    assert db_social.lista_regole_revisione(conn)[0]["stato"] == "disattivata"

    csrf = _csrf(client)
    client.post(f"/social/regole/{regola_id}/toggle", data={"csrf": csrf}, follow_redirects=False)
    assert db_social.lista_regole_revisione(conn)[0]["stato"] == "attiva"


def test_elimina_regola_da_pagina(conn, client):
    regola_id = db_social.crea_regola_revisione(conn, "Regola da eliminare", "esenzione")
    db_social.crea_utente(conn, "admin-regole5@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-regole5@test.local")
    csrf = _csrf(client)

    client.post(f"/social/regole/{regola_id}/elimina", data={"csrf": csrf}, follow_redirects=False)
    assert db_social.lista_regole_revisione(conn) == []
