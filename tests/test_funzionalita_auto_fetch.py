"""Le funzionalità di JobInPA (menu Categorie -> "Funzionalità", strategia
'funzionalita_jobinpa') si prendono direttamente dal catalogo JobInPA
(/api/internal/funzionalita, aggiunta dall'utente): niente inserimento
manuale di nome/descrizione/link, il form 'Nuovo contenuto' mostra il
catalogo letto in diretta (una o piu' funzionalita' insieme, segnalato
dall'utente), e alla creazione il server rilegge quella scelta (mai i
dati passati dal browser) insieme alle statistiche d'uso aggregate reali
— stesso principio già usato per le Promozioni."""

import json
import re
from unittest import mock

import auth
import pytest
import requests

from social import agents, db_social, jobinpa_client, llm


# --- jobinpa_client.funzionalita() ------------------------------------------

def test_funzionalita_non_configurato_ritorna_dict_vuoto(monkeypatch):
    monkeypatch.setattr("social.config.jobinpa_api_url", lambda: "")
    monkeypatch.setattr("social.config.jobinpa_api_key", lambda: "")
    client = jobinpa_client.JobInPAClient(base_url=None, api_key=None)
    assert client.funzionalita() == {}


def test_funzionalita_chiama_endpoint_e_ritorna_il_catalogo():
    client = jobinpa_client.JobInPAClient(base_url="https://jobinpa.it", api_key="chiave")
    risposta_finta = mock.Mock()
    risposta_finta.json.return_value = {
        "funzionalita": [{"chiave": "ricerca_intelligente", "nome": "Ricerca intelligente"}],
        "statistiche": {"ricerche_intelligenti_questo_mese": 42}}
    risposta_finta.raise_for_status = mock.Mock()
    with mock.patch("social.jobinpa_client.requests.get", return_value=risposta_finta) as finto:
        risultato = client.funzionalita()
    assert risultato["statistiche"]["ricerche_intelligenti_questo_mese"] == 42
    assert finto.call_args.args[0] == "https://jobinpa.it/api/internal/funzionalita"


def test_funzionalita_errore_di_rete_ritorna_dict_vuoto():
    client = jobinpa_client.JobInPAClient(base_url="https://jobinpa.it", api_key="chiave")
    with mock.patch("social.jobinpa_client.requests.get",
                    side_effect=requests.ConnectionError("giu'")):
        assert client.funzionalita() == {}


# --- agents.research: usa funzionalita_dati (una o piu' funzionalita') -----

_RICERCA_INTELLIGENTE = {"chiave": "ricerca_intelligente", "nome": "Ricerca intelligente con AI",
                         "descrizione_estesa": "Descrivi cosa cerchi con parole tue.",
                         "categoria": "premium",
                         "url_jobinpa": "https://jobinpa.it/scopri/ricerca-semantica-ai"}
_ANALISI_CV = {"chiave": "analisi_cv", "nome": "Analisi CV con AI",
               "descrizione_estesa": "Carica il tuo CV e l'AI estrae il profilo.",
               "categoria": "premium", "url_jobinpa": "https://jobinpa.it/carica-cv"}
_STATISTICHE_ESEMPIO = {"ricerche_intelligenti_questo_mese": 42, "analisi_cv_questo_mese": 18}


def _funzionalita_dati(*funzionalita):
    return {"funzionalita": list(funzionalita), "statistiche": _STATISTICHE_ESEMPIO}


def _categoria_id(conn, nome):
    return next(c["id"] for c in db_social.lista_categorie(conn) if c["nome"] == nome)


class _ClientJobinpaVietato:
    def bandi(self, *a, **k):
        raise AssertionError("jobinpa_client interrogato per una funzionalità")

    def bandi_semantici(self, *a, **k):
        raise AssertionError("jobinpa_client interrogato per una funzionalità")

    def bando(self, *a, **k):
        raise AssertionError("jobinpa_client interrogato per una funzionalità")

    def filtri_disponibili(self):
        raise AssertionError("jobinpa_client interrogato per una funzionalità")


def test_research_funzionalita_usa_i_dati_reali_e_la_statistica_pertinente(conn):
    content_id = db_social.crea_content(
        conn, "Ricerca intelligente con AI", categoria_id=_categoria_id(conn, "Funzionalità"),
        funzionalita_dati=_funzionalita_dati(_RICERCA_INTELLIGENTE))
    risultato = agents.research(conn, content_id, provider=llm.MockLLMProvider(conn),
                                jobinpa_client_=_ClientJobinpaVietato())
    fatto = risultato.fatti[0]
    assert "Ricerca intelligente con AI" in fatto.fatto
    assert "Descrivi cosa cerchi con parole tue." in fatto.fatto
    assert "42 utilizzi" in fatto.fatto
    assert fatto.fonte_url == "https://jobinpa.it/scopri/ricerca-semantica-ai"
    assert risultato.annuncio_funzionalita is True
    assert risultato.richiede_revisione is True


def test_research_funzionalita_multiple_produce_un_fatto_per_ciascuna(conn):
    """Piu' funzionalita' scelte insieme (richiesta esplicita dell'utente:
    'potrei voler fare un post su più funzionalità') devono diventare piu'
    fatti verificati distinti, non uno solo che le riassume male."""
    content_id = db_social.crea_content(
        conn, "Il pacchetto AI di JobInPA", categoria_id=_categoria_id(conn, "Funzionalità"),
        funzionalita_dati=_funzionalita_dati(_RICERCA_INTELLIGENTE, _ANALISI_CV))
    risultato = agents.research(conn, content_id, provider=llm.MockLLMProvider(conn),
                                jobinpa_client_=_ClientJobinpaVietato())
    assert len(risultato.fatti) == 2
    testi = [f.fatto for f in risultato.fatti]
    assert any("Ricerca intelligente con AI" in t and "42 utilizzi" in t for t in testi)
    assert any("Analisi CV con AI" in t and "18 utilizzi" in t for t in testi)
    fonti = {f.fonte_url for f in risultato.fatti}
    assert fonti == {"https://jobinpa.it/scopri/ricerca-semantica-ai", "https://jobinpa.it/carica-cv"}


def test_research_funzionalita_senza_statistica_pertinente_non_la_cita(conn):
    funz = {**_RICERCA_INTELLIGENTE, "chiave": "bandi_salvati", "nome": "Bandi salvati"}
    content_id = db_social.crea_content(
        conn, "Bandi salvati", categoria_id=_categoria_id(conn, "Funzionalità"),
        funzionalita_dati=_funzionalita_dati(funz))
    risultato = agents.research(conn, content_id, provider=llm.MockLLMProvider(conn),
                                jobinpa_client_=_ClientJobinpaVietato())
    assert "utilizzi" not in risultato.fatti[0].fatto


def test_research_funzionalita_senza_dati_usa_il_fallback(conn):
    content_id = db_social.crea_content(conn, "Annuncio generico",
                                        categoria_id=_categoria_id(conn, "Funzionalità"),
                                        brief="Presto una nuova funzione")
    risultato = agents.research(conn, content_id, provider=llm.MockLLMProvider(conn),
                                jobinpa_client_=_ClientJobinpaVietato())
    assert "Presto una nuova funzione" in risultato.fatti[0].fatto
    assert len(risultato.fatti) == 1


def test_copywriting_include_i_link_delle_funzionalita(conn):
    from social import models
    content_id = db_social.crea_content(
        conn, "Il pacchetto AI di JobInPA", categoria_id=_categoria_id(conn, "Funzionalità"),
        funzionalita_dati=_funzionalita_dati(_RICERCA_INTELLIGENTE, _ANALISI_CV))
    provider = llm.MockLLMProvider(conn)
    risultato = models.RisultatoRicerca(
        fatti=[models.FattoVerificato(fatto="fatto di prova", confidenza=0.9)],
        sintesi="Sintesi.")
    agents.copywriting(conn, content_id, risultato, provider=provider)
    prompt_instagram = next(u for (_, u, schema) in provider.chiamate if "Instagram" in u)
    assert "https://jobinpa.it/scopri/ricerca-semantica-ai" in prompt_instagram
    assert "https://jobinpa.it/carica-cv" in prompt_instagram


# --- web: form + creazione ---------------------------------------------------

class _ClientFinto:
    def __init__(self, funzionalita=None, statistiche=None):
        self._funzionalita = (funzionalita if funzionalita is not None
                              else [_RICERCA_INTELLIGENTE, _ANALISI_CV])
        self._statistiche = statistiche if statistiche is not None else _STATISTICHE_ESEMPIO

    def promozioni(self):
        return []

    def funzionalita(self):
        return {"funzionalita": self._funzionalita, "statistiche": self._statistiche}

    def filtri_disponibili(self):
        return {"regioni": [], "categorie": [], "settori": [], "enti": [],
                "inquadramenti": [], "titoli_studio": [], "tipi_contratto": [],
                "competenze": [], "ambiti": []}


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


def test_form_nuovo_contenuto_elenca_le_funzionalita(conn, client, monkeypatch):
    monkeypatch.setattr("social.web.jobinpa_client.client", lambda: _ClientFinto())
    db_social.crea_utente(conn, "editor-funz1@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-funz1@test.local")

    pagina = client.get("/social/contenuti/nuovo").text
    assert "Ricerca intelligente con AI" in pagina
    assert "ricerca_intelligente" in pagina
    assert "multiple" in pagina  # selezione multipla abilitata


def test_form_nuovo_contenuto_senza_funzionalita_mostra_avviso(conn, client, monkeypatch):
    monkeypatch.setattr("social.web.jobinpa_client.client", lambda: _ClientFinto(funzionalita=[]))
    db_social.crea_utente(conn, "editor-funz2@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-funz2@test.local")

    pagina = client.get("/social/contenuti/nuovo").text
    assert "Nessuna funzionalità trovata" in pagina


def test_crea_contenuto_funzionalita_singola_rilegge_i_dati_da_jobinpa(conn, client, monkeypatch):
    monkeypatch.setattr("social.web.jobinpa_client.client", lambda: _ClientFinto())
    db_social.crea_utente(conn, "editor-funz3@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-funz3@test.local")
    csrf = _csrf(client)

    r = client.post("/social/contenuti", data={
        "categoria_id": _categoria_id(conn, "Funzionalità"), "titolo": "Post sulla ricerca AI",
        "funzionalita_selezionata": "ricerca_intelligente", "pillar": "opportunita", "csrf": csrf,
    }, follow_redirects=False)

    assert r.status_code == 303
    content_id = r.headers["location"].split("/")[-1].split("?")[0]
    content = db_social.get_content(conn, content_id)
    assert content["titolo"] == "Post sulla ricerca AI"
    dati = json.loads(content["funzionalita_dati"])
    assert [f["chiave"] for f in dati["funzionalita"]] == ["ricerca_intelligente"]
    assert dati["statistiche"] == _STATISTICHE_ESEMPIO


def test_crea_contenuto_funzionalita_multiple_rilegge_tutti_i_dati(conn, client, monkeypatch):
    """Piu' valori per lo stesso campo (select multiple lato browser):
    devono diventare piu' voci in funzionalita_dati, tutte rilette dal
    catalogo reale, non solo la prima."""
    monkeypatch.setattr("social.web.jobinpa_client.client", lambda: _ClientFinto())
    db_social.crea_utente(conn, "editor-funz3b@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-funz3b@test.local")
    csrf = _csrf(client)

    r = client.post("/social/contenuti", data={
        "categoria_id": _categoria_id(conn, "Funzionalità"),
        "titolo": "Il pacchetto AI di JobInPA",
        "funzionalita_selezionata": ["ricerca_intelligente", "analisi_cv"],
        "pillar": "opportunita", "csrf": csrf,
    }, follow_redirects=False)

    assert r.status_code == 303
    content_id = r.headers["location"].split("/")[-1].split("?")[0]
    content = db_social.get_content(conn, content_id)
    dati = json.loads(content["funzionalita_dati"])
    assert {f["chiave"] for f in dati["funzionalita"]} == {"ricerca_intelligente", "analisi_cv"}


def test_crea_contenuto_funzionalita_senza_selezione_400(conn, client, monkeypatch):
    monkeypatch.setattr("social.web.jobinpa_client.client", lambda: _ClientFinto())
    db_social.crea_utente(conn, "editor-funz4@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-funz4@test.local")
    csrf = _csrf(client)

    r = client.post("/social/contenuti", data={
        "categoria_id": _categoria_id(conn, "Funzionalità"),
        "titolo": "Qualcosa", "pillar": "opportunita", "csrf": csrf,
    }, follow_redirects=False)
    assert r.status_code == 400


def test_crea_contenuto_funzionalita_senza_titolo_400(conn, client, monkeypatch):
    monkeypatch.setattr("social.web.jobinpa_client.client", lambda: _ClientFinto())
    db_social.crea_utente(conn, "editor-funz4b@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-funz4b@test.local")
    csrf = _csrf(client)

    r = client.post("/social/contenuti", data={
        "categoria_id": _categoria_id(conn, "Funzionalità"),
        "funzionalita_selezionata": "ricerca_intelligente", "pillar": "opportunita", "csrf": csrf,
    }, follow_redirects=False)
    assert r.status_code == 400


def test_crea_contenuto_funzionalita_non_piu_nel_catalogo_400(conn, client, monkeypatch):
    """Una delle funzionalità scelte nel form potrebbe nel frattempo essere
    stata rimossa dal catalogo: il server lo rilegge sempre invece di
    fidarsi del valore inviato dal browser."""
    monkeypatch.setattr("social.web.jobinpa_client.client", lambda: _ClientFinto(funzionalita=[]))
    db_social.crea_utente(conn, "editor-funz5@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-funz5@test.local")
    csrf = _csrf(client)

    r = client.post("/social/contenuti", data={
        "categoria_id": _categoria_id(conn, "Funzionalità"), "titolo": "Qualcosa",
        "funzionalita_selezionata": "ricerca_intelligente", "pillar": "opportunita", "csrf": csrf,
    }, follow_redirects=False)
    assert r.status_code == 400
