""""Interrompi ora" durante la pipeline: segnalato dall'utente dopo aver
visto un post scritto che non gli piaceva ma dover comunque aspettare che
finissero anche le immagini (le piu' costose) prima di poter modificare
l'idea e risottometterla. Il testo arriva prima delle immagini
(RESEARCHING -> DRAFTING -> DRAFT_READY -> GENERATING_VISUAL): un flag
controllato a ogni immagine del carosello, non solo all'inizio, cosi'
fermarsi a meta' risparmia davvero il costo delle immagini rimanenti."""

import tempfile
from pathlib import Path

import auth
import pytest
from PIL import Image

from social import agents, db_social, images, llm, models, state_machine
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

    def bandi(self, *, stato="OPEN", limit=10, **_):
        return _BANDI

    def bandi_semantici(self, query, *, limit=10, **_):
        return _BANDI

    def bando(self, concorso_id):
        return None

    def filtri_disponibili(self):
        return {"regioni": [], "categorie": [], "settori": [], "enti": [],
                "inquadramenti": [], "titoli_studio": [], "tipi_contratto": [],
                "competenze": [], "ambiti": []}


def _contenuto_con_carosello_non_generato(conn):
    """Come _contenuto_con_carosello di test_carosello_gestione.py, ma si
    ferma dopo copywriting: visual() lo chiama il test stesso, con un
    image_provider diverso a seconda dello scenario."""
    content_id = db_social.crea_content(conn, "Tre concorsi", canali=["instagram"])
    provider = llm.MockLLMProvider(conn)
    risultato = agents.research(conn, content_id, provider=provider, jobinpa_client_=_ClientFinto())
    agents.copywriting(conn, content_id, risultato, provider=provider)
    return content_id, risultato


class _ImageProviderCheInterrompeDopoLaPrima:
    """Simula l'utente che clicca "Interrompi ora" MENTRE la prima
    immagine sta ancora generando: il flag si accende come side-effect
    del primo generate() completato, cosi' il controllo prima della
    SECONDA immagine lo trova gia' attivo."""

    def __init__(self, conn, content_id):
        self.conn = conn
        self.content_id = content_id
        self.richieste = []

    async def generate(self, request):
        self.richieste.append(request)
        if len(self.richieste) == 1:
            db_social.richiedi_interruzione(self.conn, self.content_id)
        percorso = Path(tempfile.mkdtemp()) / f"finta_{len(self.richieste)}.png"
        Image.new("RGB", (2, 2), "#FFFFFF").save(percorso, "PNG")
        return images.GeneratedAsset(percorso=percorso, provider="mock",
                                     template=request.template, formato=request.formato)


# --- db_social: il flag -----------------------------------------------------

def test_interruzione_richiesta_di_default_e_falsa(conn):
    content_id = db_social.crea_content(conn, "Prova")
    assert db_social.interruzione_richiesta(conn, content_id) is False


def test_richiedi_interruzione_la_attiva(conn):
    content_id = db_social.crea_content(conn, "Prova")
    db_social.richiedi_interruzione(conn, content_id)
    assert db_social.interruzione_richiesta(conn, content_id) is True


# --- visual(): controllo a ogni immagine del carosello ----------------------

def test_visual_si_ferma_a_meta_carosello_se_interrotto(conn):
    content_id, risultato = _contenuto_con_carosello_non_generato(conn)
    provider_immagini = _ImageProviderCheInterrompeDopoLaPrima(conn, content_id)

    with pytest.raises(agents.GenerazioneInterrotta):
        agents.visual(conn, content_id, risultato, provider=llm.MockLLMProvider(conn),
                      image_provider=provider_immagini)

    # Solo la prima immagine generata: le altre due (piu' costose, non
    # ancora fatte quando l'interruzione e' stata richiesta) risparmiate.
    assert len(provider_immagini.richieste) == 1


def test_visual_senza_interruzione_genera_tutto_il_carosello(conn):
    content_id, risultato = _contenuto_con_carosello_non_generato(conn)
    agents.visual(conn, content_id, risultato, provider=llm.MockLLMProvider(conn),
                  image_provider=MockImageProvider())
    assert len(db_social.asset_di(conn, content_id)) == 3


# --- esegui_pipeline(): l'interruzione riporta a RESEARCH_FAILED, non a un
# errore ritentabile (altrimenti il prossimo tentativo automatico
# rispenderebbe esattamente il costo che l'interruzione doveva evitare) ----

def test_esegui_pipeline_interrotta_torna_a_research_failed(conn, monkeypatch):
    content_id = db_social.crea_content(conn, "Tre concorsi", canali=["instagram"])
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.ValutazioneRischio, models.ValutazioneRischio(
        classe="verde", punteggio_accuratezza=1.0, punteggio_brand=1.0, punteggio_conformita=1.0))
    monkeypatch.setattr("social.jobinpa_client.client", lambda: _ClientFinto())

    provider_immagini = _ImageProviderCheInterrompeDopoLaPrima(conn, content_id)
    stato = agents.esegui_pipeline(conn, content_id, provider=provider,
                                   image_provider=provider_immagini)

    assert stato == "RESEARCH_FAILED"
    content = db_social.get_content(conn, content_id)
    assert content["stato"] == "RESEARCH_FAILED"
    assert "interrott" in content["errore"].lower()
    # Il brief resta modificabile: RESEARCH_FAILED e' in STATI_PIPELINE_AVVIABILE.
    assert content["stato"] in agents.STATI_PIPELINE_AVVIABILE


def test_esegui_pipeline_azzera_il_flag_a_ogni_nuovo_tentativo(conn, monkeypatch):
    """Un flag rimasto acceso da un'interruzione precedente non deve far
    fallire subito anche il tentativo appena risottomesso dall'utente."""
    content_id = db_social.crea_content(conn, "Tre concorsi", canali=["instagram"])
    db_social.richiedi_interruzione(conn, content_id)
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.ValutazioneRischio, models.ValutazioneRischio(
        classe="verde", punteggio_accuratezza=1.0, punteggio_brand=1.0, punteggio_conformita=1.0))
    monkeypatch.setattr("social.jobinpa_client.client", lambda: _ClientFinto())

    stato = agents.esegui_pipeline(conn, content_id, provider=provider,
                                   image_provider=MockImageProvider())

    assert stato != "RESEARCH_FAILED"
    assert db_social.interruzione_richiesta(conn, content_id) is False


# --- rotta web ----------------------------------------------------------------

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
    import re
    pagina = client.get(url).text
    return re.search(r'name="csrf" value="([0-9a-f]+)"', pagina).group(1)


def test_route_interrompi_richiede_pipeline_in_corso(conn, client):
    content_id = db_social.crea_content(conn, "Idea qualsiasi")  # stato IDEA
    db_social.crea_utente(conn, "editor-stop1@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-stop1@test.local")
    csrf = _csrf(client)

    r = client.post(f"/social/contenuti/{content_id}/interrompi",
                    data={"csrf": csrf}, follow_redirects=False)

    assert r.status_code == 409
    assert db_social.interruzione_richiesta(conn, content_id) is False


def test_route_interrompi_durante_generazione_attiva_il_flag(conn, client):
    content_id = db_social.crea_content(conn, "Tre concorsi")
    db_social.aggiorna_content(conn, content_id, stato="GENERATING_VISUAL")
    db_social.crea_utente(conn, "editor-stop2@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-stop2@test.local")
    csrf = _csrf(client)

    r = client.post(f"/social/contenuti/{content_id}/interrompi",
                    data={"csrf": csrf}, follow_redirects=False)

    assert r.status_code == 303
    assert db_social.interruzione_richiesta(conn, content_id) is True


def test_route_interrompi_inesistente_404(conn, client):
    db_social.crea_utente(conn, "editor-stop3@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-stop3@test.local")
    csrf = _csrf(client)

    r = client.post("/social/contenuti/non-esiste/interrompi",
                    data={"csrf": csrf}, follow_redirects=False)

    assert r.status_code == 404


def test_pagina_contenuto_mostra_interrompi_ora_durante_la_generazione(conn, client):
    content_id = db_social.crea_content(conn, "Tre concorsi")
    db_social.aggiorna_content(conn, content_id, stato="GENERATING_VISUAL")
    db_social.crea_utente(conn, "editor-stop4@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-stop4@test.local")
    pagina = client.get(f"/social/contenuti/{content_id}").text
    assert f'action="/social/contenuti/{content_id}/interrompi"' in pagina


def test_pagina_contenuto_non_mostra_interrompi_ora_senza_generazione_in_corso(conn, client):
    content_id = db_social.crea_content(conn, "Idea qualsiasi")  # stato IDEA
    db_social.crea_utente(conn, "editor-stop5@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-stop5@test.local")
    pagina = client.get(f"/social/contenuti/{content_id}").text
    assert f'action="/social/contenuti/{content_id}/interrompi"' not in pagina


def test_pagina_contenuto_mostra_banner_distinto_dopo_interruzione_richiesta(conn, client):
    """Segnalato dall'utente: dopo aver cliccato "Interrompi ora" il banner
    restava identico a "Pipeline in corso" (stesso testo, stesso bottone),
    senza dare conferma che il click fosse stato registrato -- non si
    capiva se stesse davvero lavorando all'interruzione. Ora il banner
    cambia (giallo, testo diverso, niente bottone duplicato) finche' il
    flag e' attivo."""
    content_id = db_social.crea_content(conn, "Tre concorsi")
    db_social.aggiorna_content(conn, content_id, stato="GENERATING_VISUAL")
    db_social.richiedi_interruzione(conn, content_id)
    db_social.crea_utente(conn, "editor-stop6@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-stop6@test.local")

    pagina = client.get(f"/social/contenuti/{content_id}").text

    assert "Interruzione richiesta" in pagina
    assert f'action="/social/contenuti/{content_id}/interrompi"' not in pagina
