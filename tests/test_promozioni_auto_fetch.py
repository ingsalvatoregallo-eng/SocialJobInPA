"""Le promozioni devono essere prese direttamente dal sito JobInPA
(richiesta esplicita dell'utente): niente inserimento manuale di
titolo/prezzo/scadenza, il form 'Nuovo contenuto' mostra le promozioni
davvero attive lette in diretta da /api/internal/promozioni, e alla
creazione il server rilegge quella scelta (mai i dati passati dal
browser) per evitare uno snapshot scaduto o manomesso."""

import re
from unittest import mock

import auth
import pytest
import requests

from social import db_social, jobinpa_client


# --- jobinpa_client.promozioni() --------------------------------------------

def test_promozioni_non_configurato_ritorna_lista_vuota(monkeypatch):
    """JobInPAClient(base_url=None) ricade sempre su config.jobinpa_api_url()
    (pattern "or": None e' equivalente a "usa il default"): su una macchina
    con JOBINPA_API_URL/KEY reali gia' in .env (come questa di sviluppo,
    puntata su jobinpa.it) il client risulterebbe comunque "configurato" e
    la chiamata colpirebbe davvero la produzione invece di restare isolata
    (stesso gotcha di test_agents_contesto.test_contesto_jobinpa_client_non_configurato)."""
    monkeypatch.setattr("social.config.jobinpa_api_url", lambda: "")
    monkeypatch.setattr("social.config.jobinpa_api_key", lambda: "")
    client = jobinpa_client.JobInPAClient(base_url=None, api_key=None)
    assert client.promozioni() == []


def test_promozioni_chiama_endpoint_e_ritorna_la_lista():
    client = jobinpa_client.JobInPAClient(base_url="https://jobinpa.it", api_key="chiave")
    risposta_finta = mock.Mock()
    risposta_finta.json.return_value = {"promozioni": [{"tipo": "piano", "chiave": "premium",
                                                         "nome": "Premium promo"}]}
    risposta_finta.raise_for_status = mock.Mock()
    with mock.patch("social.jobinpa_client.requests.get", return_value=risposta_finta) as finto:
        risultato = client.promozioni()
    assert risultato == [{"tipo": "piano", "chiave": "premium", "nome": "Premium promo"}]
    assert finto.call_args.args[0] == "https://jobinpa.it/api/internal/promozioni"
    assert finto.call_args.kwargs["headers"]["X-Internal-Api-Key"] == "chiave"


def test_promozioni_errore_di_rete_ritorna_lista_vuota():
    client = jobinpa_client.JobInPAClient(base_url="https://jobinpa.it", api_key="chiave")
    with mock.patch("social.jobinpa_client.requests.get",
                    side_effect=requests.ConnectionError("giu'")):
        assert client.promozioni() == []


# --- web: form + creazione ----------------------------------------------------

_PROMO_ESEMPIO = {"tipo": "piano", "chiave": "premium-promo", "nome": "Premium promo",
                  "descrizione": "Accesso completo", "prezzo_eur": 9.99,
                  "prezzo_promozionale_eur": 0.0, "scadenza": "2026-08-31",
                  "url_jobinpa": "https://jobinpa.it/premium"}


class _ClientFinto:
    def __init__(self, promozioni=None):
        self._promozioni = promozioni if promozioni is not None else [_PROMO_ESEMPIO]

    def promozioni(self):
        return self._promozioni


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


def test_form_nuovo_contenuto_elenca_le_promozioni_attive(conn, client, monkeypatch):
    monkeypatch.setattr("social.web.jobinpa_client.client", lambda: _ClientFinto())
    db_social.crea_utente(conn, "editor-autofetch@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-autofetch@test.local")

    pagina = client.get("/social/contenuti/nuovo").text
    assert "Premium promo" in pagina
    assert "piano|premium-promo" in pagina


def test_form_nuovo_contenuto_senza_promo_attive_mostra_avviso(conn, client, monkeypatch):
    monkeypatch.setattr("social.web.jobinpa_client.client", lambda: _ClientFinto(promozioni=[]))
    db_social.crea_utente(conn, "editor-autofetch2@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-autofetch2@test.local")

    pagina = client.get("/social/contenuti/nuovo").text
    assert "Nessuna promozione attiva" in pagina


def _categoria_id(conn, nome):
    return next(c["id"] for c in db_social.lista_categorie(conn) if c["nome"] == nome)


def test_crea_contenuto_promozione_rilegge_i_dati_da_jobinpa(conn, client, monkeypatch):
    monkeypatch.setattr("social.web.jobinpa_client.client", lambda: _ClientFinto())
    db_social.crea_utente(conn, "editor-autofetch3@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-autofetch3@test.local")
    csrf = _csrf(client)

    r = client.post("/social/contenuti", data={
        "categoria_id": _categoria_id(conn, "Promozioni"),
        "promo_selezionata": "piano|premium-promo", "csrf": csrf,
    }, follow_redirects=False)

    assert r.status_code == 303
    content_id = r.headers["location"].split("/")[-1].split("?")[0]
    content = db_social.get_content(conn, content_id)
    assert content["titolo"] == "Premium promo"
    assert content["scadenza_promo"] == "2026-08-31"
    import json
    assert json.loads(content["promo_dati"]) == _PROMO_ESEMPIO


def test_crea_contenuto_promozione_senza_selezione_400(conn, client, monkeypatch):
    monkeypatch.setattr("social.web.jobinpa_client.client", lambda: _ClientFinto())
    db_social.crea_utente(conn, "editor-autofetch4@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-autofetch4@test.local")
    csrf = _csrf(client)

    r = client.post("/social/contenuti", data={
        "categoria_id": _categoria_id(conn, "Promozioni"), "csrf": csrf,
    }, follow_redirects=False)
    assert r.status_code == 400


def test_crea_contenuto_promozione_non_piu_attiva_400(conn, client, monkeypatch):
    """La promo scelta nel form potrebbe nel frattempo essere scaduta: il
    server la rilegge sempre da JobInPA invece di fidarsi del valore
    inviato dal browser (mai un post su un'offerta non più reale)."""
    monkeypatch.setattr("social.web.jobinpa_client.client", lambda: _ClientFinto(promozioni=[]))
    db_social.crea_utente(conn, "editor-autofetch5@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-autofetch5@test.local")
    csrf = _csrf(client)

    r = client.post("/social/contenuti", data={
        "categoria_id": _categoria_id(conn, "Promozioni"),
        "promo_selezionata": "piano|premium-promo", "csrf": csrf,
    }, follow_redirects=False)
    assert r.status_code == 400


def test_crea_contenuto_concorso_senza_titolo_400(conn, client, monkeypatch):
    monkeypatch.setattr("social.web.jobinpa_client.client", lambda: _ClientFinto())
    db_social.crea_utente(conn, "editor-autofetch6@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-autofetch6@test.local")
    csrf = _csrf(client)

    r = client.post("/social/contenuti", data={
        "categoria_id": _categoria_id(conn, "Concorsi"), "csrf": csrf,
    }, follow_redirects=False)
    assert r.status_code == 400
