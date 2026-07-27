"""Gestione del carosello dopo la generazione, segnalata dall'utente dopo
aver modificato un brief e rilanciato la pipeline:
1. rigenerare le immagini (pipeline completa o "Rigenera immagine") deve
   SOVRASCRIVERE quelle vecchie, mai aggiungersi (bug reale: le vecchie
   restavano insieme alle nuove);
2. si deve poter togliere una singola immagine dal carosello;
3. dopo aver tolto immagini, si deve poter rigenerare SOLO il testo,
   coerente col carosello ridotto (mai piu' bandi di quante immagini)."""

import re
from pathlib import Path

import auth
import pytest

from social import agents, db_social, llm, models
from social.images import MockImageProvider

_BANDI = [
    {"id": f"CONC-{i}", "titolo": f"Concorso di prova {i}", "enti": ["Comune Demo"],
     "num_posti": 5, "scadenza": "2026-12-31", "stato": "OPEN",
     "sintesi": "Bando di prova.", "titolo_studio_richiesto": "Diploma", "competenze": [],
     "url_dettaglio": f"https://www.inpa.gov.it/dettaglio/CONC-{i}"}
    for i in range(3)
]


class _ClientFinto:
    configurato = True

    def __init__(self, bandi=None):
        self._bandi = bandi if bandi is not None else _BANDI

    def bandi(self, *, stato="OPEN", limit=10, **_):
        return self._bandi

    def bandi_semantici(self, query, *, limit=10, **_):
        return self._bandi

    def bando(self, concorso_id):
        return None

    def filtri_disponibili(self):
        return {"regioni": [], "categorie": [], "settori": [], "enti": [],
                "inquadramenti": [], "titoli_studio": [], "tipi_contratto": [],
                "competenze": [], "ambiti": []}


def _contenuto_con_carosello(conn):
    content_id = db_social.crea_content(conn, "Tre concorsi", canali=["instagram"])
    provider = llm.MockLLMProvider(conn)
    risultato = agents.research(conn, content_id, provider=provider,
                                jobinpa_client_=_ClientFinto())
    agents.copywriting(conn, content_id, risultato, provider=provider)
    agents.visual(conn, content_id, risultato, provider=provider,
                  image_provider=MockImageProvider())
    return content_id


# --- visual() sovrascrive, non aggiunge -------------------------------------

def test_visual_rigenerato_sovrascrive_le_immagini_vecchie(conn):
    content_id = _contenuto_con_carosello(conn)
    assert len(db_social.asset_di(conn, content_id)) == 3
    percorsi_vecchi = [a["percorso"] for a in db_social.asset_di(conn, content_id)]

    provider = llm.MockLLMProvider(conn)
    risultato = models.RisultatoRicerca(fatti=[], sintesi="", bandi_trovati=_BANDI[:2])
    agents.visual(conn, content_id, risultato, provider=provider,
                  image_provider=MockImageProvider())

    nuovi_asset = db_social.asset_di(conn, content_id)
    assert len(nuovi_asset) == 2  # non 3 vecchi + 2 nuovi = 5
    assert all(not Path(p).exists() for p in percorsi_vecchi)


def test_visual_carosello_salva_il_bando_id_per_immagine(conn):
    content_id = _contenuto_con_carosello(conn)
    asset = db_social.asset_di(conn, content_id)
    assert {a["bando_id"] for a in asset} == {"CONC-0", "CONC-1", "CONC-2"}


# --- Eliminazione di una singola immagine dal carosello ---------------------

def test_elimina_asset_toglie_anche_il_bando_da_bandi_trovati(conn):
    content_id = _contenuto_con_carosello(conn)
    asset = db_social.asset_di(conn, content_id)
    da_togliere = next(a for a in asset if a["bando_id"] == "CONC-1")

    assert db_social.elimina_asset(conn, content_id, da_togliere["id"]) is True

    assert len(db_social.asset_di(conn, content_id)) == 2
    assert not Path(da_togliere["percorso"]).exists()
    content = db_social.get_content(conn, content_id)
    import json
    bandi_rimasti = json.loads(content["bandi_trovati"])
    assert {b["id"] for b in bandi_rimasti} == {"CONC-0", "CONC-2"}


def test_elimina_asset_inesistente_ritorna_false(conn):
    content_id = db_social.crea_content(conn, "Senza asset")
    assert db_social.elimina_asset(conn, content_id, "non-esiste") is False


def test_elimina_asset_di_altro_contenuto_ritorna_false(conn):
    """Un asset_id valido ma di un ALTRO contenuto non deve essere toccato
    (isolamento fra contenuti, stessa logica di elimina_content)."""
    content_a = _contenuto_con_carosello(conn)
    content_b = db_social.crea_content(conn, "Altro contenuto")
    asset_di_a = db_social.asset_di(conn, content_a)[0]
    assert db_social.elimina_asset(conn, content_b, asset_di_a["id"]) is False
    assert len(db_social.asset_di(conn, content_a)) == 3


# --- Rigenera solo il testo, coerente col carosello ridotto -----------------

def test_rigenera_copy_non_tocca_le_immagini(conn):
    content_id = _contenuto_con_carosello(conn)
    percorsi_prima = {a["id"]: a["percorso"] for a in db_social.asset_di(conn, content_id)}

    agents.rigenera_copy(conn, content_id, provider=llm.MockLLMProvider(conn))

    percorsi_dopo = {a["id"]: a["percorso"] for a in db_social.asset_di(conn, content_id)}
    assert percorsi_prima == percorsi_dopo


def test_rigenera_copy_riflette_il_carosello_ridotto(conn):
    content_id = _contenuto_con_carosello(conn)
    asset_da_togliere = db_social.asset_di(conn, content_id)[0]
    db_social.elimina_asset(conn, content_id, asset_da_togliere["id"])

    provider = llm.MockLLMProvider(conn)
    agents.rigenera_copy(conn, content_id, provider=provider)

    prompt_instagram = next(u for (_, u, _) in provider.chiamate if "Instagram" in u)
    assert "carosello di 2 immagini" in prompt_instagram


# --- Rotte web ----------------------------------------------------------------

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


def test_route_elimina_asset(conn, client):
    content_id = _contenuto_con_carosello(conn)
    asset_id = db_social.asset_di(conn, content_id)[0]["id"]
    db_social.crea_utente(conn, "editor-carosello@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-carosello@test.local")
    csrf = _csrf(client)

    r = client.post(f"/social/contenuti/{content_id}/asset/{asset_id}/elimina",
                    data={"csrf": csrf}, follow_redirects=False)

    assert r.status_code == 303
    assert len(db_social.asset_di(conn, content_id)) == 2


def test_route_elimina_asset_inesistente_404(conn, client):
    content_id = _contenuto_con_carosello(conn)
    db_social.crea_utente(conn, "editor-carosello2@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-carosello2@test.local")
    csrf = _csrf(client)

    r = client.post(f"/social/contenuti/{content_id}/asset/non-esiste/elimina",
                    data={"csrf": csrf}, follow_redirects=False)

    assert r.status_code == 404


def test_route_rigenera_testo_mette_in_coda_il_job(conn, client):
    content_id = _contenuto_con_carosello(conn)
    db_social.crea_utente(conn, "editor-testo@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-testo@test.local")
    csrf = _csrf(client)

    r = client.post(f"/social/contenuti/{content_id}/rigenera-testo",
                    data={"csrf": csrf}, follow_redirects=False)

    assert r.status_code == 303
    assert db_social.job_in_corso(conn, "rigenera_copy", content_id)
