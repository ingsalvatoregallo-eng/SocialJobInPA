"""Visibilita' dei contenuti in errore: segnalato dall'utente, un contenuto
BLOCKED mostrava solo "Fase: Bloccata" + "Rischio: rosso" nella lista
Contenuti, senza dire il perche' (bisognava aprire il dettaglio), e la
sidebar non dava nessun segnale della sua esistenza finche' non si
cliccava per caso sulla tab "Errori"."""

import json
import re

import pytest

import auth
from social import db_social


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


def _blocca_per_rischio(conn, content_id, motivo_ai):
    punteggi = {
        "classe_regole": "verde", "motivi_regole": [],
        "classe_ai": "rosso", "accuratezza": 0.2, "brand": 0.6, "conformita": 0.4,
        "motivi_ai": [motivo_ai],
    }
    db_social.aggiorna_content(conn, content_id, stato="BLOCKED",
                               classe_rischio="rosso", decisione_rischio="review",
                               punteggi_rischio=json.dumps(punteggi))


def test_lista_contenuti_mostra_il_motivo_del_blocco(conn, client):
    db_social.crea_utente(conn, "revisore@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    content_id = db_social.crea_content(conn, "Concorsi per medici in scadenza")
    _blocca_per_rischio(conn, content_id,
                        "Errore fattuale grave: le scadenze non coincidono con quanto scritto")
    _login(client, "revisore@test.local")
    pagina = client.get("/social/contenuti?gruppo=errori").text
    assert "Errore fattuale grave" in pagina


def test_lista_contenuti_altri_gruppi_non_hanno_colonna_motivo(conn, client):
    """La colonna Motivo ha senso solo nel gruppo errori: altrove sarebbe
    sempre vuota (nessun contenuto li' e' in BLOCKED/PUBLISH_FAILED)."""
    db_social.crea_utente(conn, "revisore@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    db_social.crea_content(conn, "Idea qualsiasi")
    _login(client, "revisore@test.local")
    pagina = client.get("/social/contenuti?gruppo=idee").text
    assert "<th>Motivo</th>" not in pagina


def test_sidebar_mostra_il_conteggio_errori_su_qualsiasi_pagina(conn, client):
    db_social.crea_utente(conn, "revisore@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    content_id = db_social.crea_content(conn, "Concorsi per medici in scadenza")
    _blocca_per_rischio(conn, content_id, "motivo qualsiasi")
    _login(client, "revisore@test.local")
    # Non solo sulla pagina Contenuti: il segnale deve comparire ovunque,
    # es. in Panoramica -- e' quello il punto (prima serviva entrare per
    # caso nella tab giusta per scoprire che c'era qualcosa da rivedere).
    pagina = client.get("/social/").text
    assert re.search(r'Contenuti\s*<span class="badge rosso"[^>]*>1</span>', pagina)


def test_sidebar_senza_errori_non_mostra_badge(conn, client):
    db_social.crea_utente(conn, "revisore@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    db_social.crea_content(conn, "Idea qualsiasi")
    _login(client, "revisore@test.local")
    pagina = client.get("/social/").text
    assert not re.search(r'Contenuti\s*<span class="badge rosso"', pagina)


# --- Rigenerare (con le note del reviewer) o correggere a mano un BLOCKED ---
# Segnalato dall'utente: un contenuto bloccato dal Quality & Risk Agent non
# offriva ne' un modo di rigenerare il testo tenendo conto del motivo del
# blocco, ne' di correggerlo a mano dalla stessa pagina.

def test_pagina_contenuto_blocked_precompila_le_note_col_motivo_del_blocco(conn, client):
    db_social.crea_utente(conn, "revisore@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    content_id = db_social.crea_content(conn, "Concorsi per medici in scadenza")
    # Il pannello "Rigenera testo" (e le note) e' nello stesso blocco del
    # pulsante "Rigenera immagine", condizionato ad avere gia' almeno un
    # asset -- coerente con l'uso reale (BLOCKED e' raggiungibile solo dopo
    # QUALITY_CHECK, che segue sempre GENERATING_VISUAL).
    db_social.salva_asset(conn, content_id, "/finto.png", piattaforma="instagram")
    _blocca_per_rischio(conn, content_id, "Le scadenze citate non coincidono con i fatti verificati")
    _login(client, "revisore@test.local")
    pagina = client.get(f"/social/contenuti/{content_id}").text
    assert "Le scadenze citate non coincidono con i fatti verificati" in pagina
    assert 'name="note_revisore"' in pagina


def test_pagina_contenuto_non_blocked_non_mostra_note_revisore(conn, client):
    db_social.crea_utente(conn, "revisore@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    content_id = db_social.crea_content(conn, "Idea qualsiasi")
    _login(client, "revisore@test.local")
    pagina = client.get(f"/social/contenuti/{content_id}").text
    assert 'name="note_revisore"' not in pagina


def test_pagina_contenuto_blocked_mostra_form_di_modifica_manuale(conn, client):
    db_social.crea_utente(conn, "revisore@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    content_id = db_social.crea_content(conn, "Concorsi per medici in scadenza")
    db_social.salva_variante(conn, content_id, "instagram", "testo originale generato")
    _blocca_per_rischio(conn, content_id, "motivo qualsiasi")
    _login(client, "revisore@test.local")
    pagina = client.get(f"/social/contenuti/{content_id}").text
    assert f'action="/social/approvazioni/{content_id}/variante/instagram"' in pagina
    assert "testo originale generato" in pagina


def test_pagina_contenuto_blocked_senza_permesso_approve_non_mostra_modifica_manuale(conn, client):
    """editor ha social.edit (rigenerazione con note) ma non social.approve
    (modifica manuale, riservata a chi puo' approvare/revisionare)."""
    db_social.crea_utente(conn, "editor@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    content_id = db_social.crea_content(conn, "Concorsi per medici in scadenza")
    db_social.salva_variante(conn, content_id, "instagram", "testo originale generato")
    _blocca_per_rischio(conn, content_id, "motivo qualsiasi")
    _login(client, "editor@test.local")
    pagina = client.get(f"/social/contenuti/{content_id}").text
    assert f'action="/social/approvazioni/{content_id}/variante/instagram"' not in pagina
