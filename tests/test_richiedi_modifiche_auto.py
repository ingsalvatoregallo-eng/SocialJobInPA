""""Richiedi modifiche" deve poter ripartire da solo (senza dover ricliccare
"Avvia pipeline" a mano) e il brief deve essere modificabile prima del
nuovo giro — due lacune segnalate dall'utente dopo aver usato la revisione
su un contenuto reale."""

import re

import pytest

import auth
from social import agents, db_social, llm, models
from social.images import MockImageProvider


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


def _contenuto_in_revisione(conn):
    """Pipeline eseguita fino a AWAITING_APPROVAL (classe giallo)."""
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.ValutazioneRischio, models.ValutazioneRischio(
        classe="giallo", punteggio_accuratezza=0.7, punteggio_brand=0.7,
        punteggio_conformita=0.7, motivi=[]))
    content_id = db_social.crea_content(conn, "Contenuto da rivedere")
    agents.esegui_pipeline(conn, content_id, provider=provider,
                           image_provider=MockImageProvider())
    approval = db_social.approval_aperta_di(conn, content_id)
    return content_id, approval["id"]


def test_richiedi_modifiche_rimette_in_coda_la_pipeline(conn, client):
    content_id, approval_id = _contenuto_in_revisione(conn)
    db_social.crea_utente(conn, "revisore@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "revisore@test.local")
    csrf = _csrf(client)

    r = client.post(f"/social/approvazioni/{approval_id}",
                    data={"azione": "modifiche", "motivo": "Aggiungi il link al bando",
                          "csrf": csrf},
                    follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == f"/social/contenuti/{content_id}?avviata=1"
    assert db_social.get_content(conn, content_id)["stato"] == "CHANGES_REQUESTED"
    assert db_social.job_in_corso(conn, "pipeline", content_id)


def test_richiedi_modifiche_senza_motivo_non_mette_in_coda(conn, client):
    """Il motivo resta obbligatorio (comportamento gia' esistente): senza,
    niente stato CHANGES_REQUESTED e niente job in coda."""
    content_id, approval_id = _contenuto_in_revisione(conn)
    db_social.crea_utente(conn, "revisore2@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "revisore2@test.local")
    csrf = _csrf(client)

    r = client.post(f"/social/approvazioni/{approval_id}",
                    data={"azione": "modifiche", "motivo": "  ", "csrf": csrf},
                    follow_redirects=False)

    assert r.status_code == 422
    assert not db_social.job_in_corso(conn, "pipeline", content_id)


def test_route_modifica_brief_aggiorna_titolo_e_brief(conn, client):
    content_id = db_social.crea_content(conn, "Titolo originale", brief="Brief originale")
    db_social.crea_utente(conn, "editor-brief@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-brief@test.local")
    csrf = _csrf(client)

    r = client.post(f"/social/contenuti/{content_id}/brief",
                    data={"titolo": "Titolo corretto", "brief": "Brief corretto",
                          "csrf": csrf}, follow_redirects=False)

    assert r.status_code == 303
    content = db_social.get_content(conn, content_id)
    assert content["titolo"] == "Titolo corretto"
    assert content["brief"] == "Brief corretto"


def test_route_modifica_brief_rifiutata_fuori_dagli_stati_avviabili(conn, client):
    content_id, _ = _contenuto_in_revisione(conn)  # finisce in AWAITING_APPROVAL
    db_social.crea_utente(conn, "editor-brief2@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-brief2@test.local")
    csrf = _csrf(client)

    r = client.post(f"/social/contenuti/{content_id}/brief",
                    data={"titolo": "Tentativo fuori stato", "csrf": csrf},
                    follow_redirects=False)

    assert r.status_code == 409
