"""Tipologia 'promozione' (richiesta dall'utente): niente ricerca bandi,
l'immagine usa un template di prompt configurabile in Impostazioni (solo il
soggetto/illustrazione, mai testo/numeri — quelli restano disegnati dal
sistema come per 'concorso') invece del brief libero scritto dal Visual
Agent. La promozione forza sempre la revisione umana (stesso motivo di
annuncio_funzionalita: un claim commerciale non ha una fonte esterna
verificabile come un bando su JobInPA)."""

import re

import auth
import pytest

from social import agents, db_social, llm, models
from social.images import MockImageProvider


def test_crea_content_tipologia_default_concorso(conn):
    content_id = db_social.crea_content(conn, "Tema qualsiasi")
    content = db_social.get_content(conn, content_id)
    assert content["tipologia"] == "concorso"
    assert content["scadenza_promo"] is None


def test_crea_content_tipologia_non_valida_solleva_errore(conn):
    with pytest.raises(ValueError):
        db_social.crea_content(conn, "Tema", tipologia="non-esiste")


class _ClientJobinpaVietato:
    """Se research() lo interroga per una 'promozione' e' un bug: niente
    bando da cercare, la chiamata non deve avvenire."""

    def bandi(self, *a, **k):
        raise AssertionError("jobinpa_client interrogato per una promozione")

    def bandi_semantici(self, *a, **k):
        raise AssertionError("jobinpa_client interrogato per una promozione")

    def bando(self, *a, **k):
        raise AssertionError("jobinpa_client interrogato per una promozione")

    def filtri_disponibili(self):
        raise AssertionError("jobinpa_client interrogato per una promozione")


def test_research_promozione_non_interroga_jobinpa(conn):
    content_id = db_social.crea_content(
        conn, "Premium gratis fino al 31 agosto", tipologia="promozione",
        scadenza_promo="2026-08-31", brief="Piano Premium senza costi")
    risultato = agents.research(conn, content_id, provider=llm.MockLLMProvider(conn),
                                jobinpa_client_=_ClientJobinpaVietato())
    assert risultato.annuncio_funzionalita is True
    assert risultato.richiede_revisione is True
    assert risultato.bandi_trovati == []
    assert "Premium gratis fino al 31 agosto" in risultato.fatti[0].fatto
    assert "31 agosto 2026" in risultato.fatti[0].fatto
    assert "Piano Premium senza costi" in risultato.fatti[0].fatto
    content = db_social.get_content(conn, content_id)
    assert content["bandi_trovati"] == "[]"


def test_research_promozione_senza_scadenza_non_rompe(conn):
    content_id = db_social.crea_content(conn, "Promo senza data", tipologia="promozione")
    risultato = agents.research(conn, content_id, provider=llm.MockLLMProvider(conn),
                                jobinpa_client_=_ClientJobinpaVietato())
    assert "Promo senza data" in risultato.fatti[0].fatto


def test_visual_promozione_usa_il_template_configurato(conn):
    db_social.set_setting(conn, "prompt_templates_immagine", {
        "promozione": "Illustrazione regalo per {NOME_PROMO}, scade il {DATA_SCADENZA}."})
    content_id = db_social.crea_content(
        conn, "Premium gratis", tipologia="promozione", scadenza_promo="2026-08-31",
        canali=["instagram"])
    provider = llm.MockLLMProvider(conn)
    risultato = agents.research(conn, content_id, provider=provider,
                                jobinpa_client_=_ClientJobinpaVietato())

    catturate = []

    class _ImageProviderSpia(MockImageProvider):
        async def generate(self, request):
            catturate.append(request)
            return await super().generate(request)

    agents.visual(conn, content_id, risultato, provider=provider,
                  image_provider=_ImageProviderSpia())
    assert len(catturate) == 1
    assert catturate[0].prompt_ai == "Illustrazione regalo per Premium gratis, scade il 31 agosto 2026."


def test_visual_promozione_senza_template_lascia_il_prompt_ai_del_llm(conn):
    db_social.set_setting(conn, "prompt_templates_immagine", {})
    content_id = db_social.crea_content(conn, "Promo senza template", tipologia="promozione",
                                        canali=["instagram"])
    provider = llm.MockLLMProvider(conn)
    risultato = agents.research(conn, content_id, provider=provider,
                                jobinpa_client_=_ClientJobinpaVietato())
    brief = agents.visual(conn, content_id, risultato, provider=provider,
                          image_provider=MockImageProvider())
    # Nessun template configurato: prompt_ai resta quello (eventualmente
    # None) prodotto dal visual_brief LLM, nessuna sostituzione forzata.
    assert brief.prompt_ai is None


def test_visual_concorso_non_tocca_prompt_ai(conn):
    """Tipologia di default: comportamento invariato, nessuna sostituzione."""
    db_social.set_setting(conn, "prompt_templates_immagine", {
        "promozione": "Non deve mai comparire per un concorso."})
    content_id = db_social.crea_content(conn, "Concorso normale", canali=["instagram"])
    provider = llm.MockLLMProvider(conn)
    risultato = models.RisultatoRicerca(
        fatti=[models.FattoVerificato(fatto="fatto di prova", confidenza=0.9)],
        sintesi="Sintesi.")
    brief = agents.visual(conn, content_id, risultato, provider=provider,
                          image_provider=MockImageProvider())
    assert brief.prompt_ai != "Non deve mai comparire per un concorso."


def test_esegui_pipeline_promozione_forza_approvazione_anche_a_classe_verde(conn):
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.ValutazioneRischio, models.ValutazioneRischio(
        classe="verde", punteggio_accuratezza=0.95, punteggio_brand=0.95,
        punteggio_conformita=0.95, motivi=[]))
    content_id = db_social.crea_content(
        conn, "Premium gratis fino al 31 agosto", tipologia="promozione",
        scadenza_promo="2026-08-31", canali=["instagram"])
    stato_finale = agents.esegui_pipeline(conn, content_id, provider=provider,
                                          image_provider=MockImageProvider())
    assert stato_finale == "AWAITING_APPROVAL"


# --- Livello web: form di creazione e template in Impostazioni --------------

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


def test_crea_contenuto_promozione_salva_tipologia_e_scadenza(conn, client):
    db_social.crea_utente(conn, "editor-promo@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-promo@test.local")
    csrf = _csrf(client)

    r = client.post("/social/contenuti", data={
        "titolo": "Premium gratis", "tipologia": "promozione",
        "scadenza_promo": "2026-08-31", "csrf": csrf,
    }, follow_redirects=False)

    assert r.status_code == 303
    content_id = r.headers["location"].split("/")[-1].split("?")[0]
    content = db_social.get_content(conn, content_id)
    assert content["tipologia"] == "promozione"
    assert content["scadenza_promo"] == "2026-08-31"
    assert db_social.job_in_corso(conn, "pipeline", content_id)


def test_crea_contenuto_tipologia_non_valida_rifiutata(conn, client):
    db_social.crea_utente(conn, "editor-promo2@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-promo2@test.local")
    csrf = _csrf(client)

    r = client.post("/social/contenuti", data={
        "titolo": "Prova", "tipologia": "inesistente", "csrf": csrf,
    }, follow_redirects=False)

    assert r.status_code == 400


def test_salva_prompt_template_richiede_admin(conn, client):
    db_social.crea_utente(conn, "editor-promo3@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-promo3@test.local")
    csrf = _csrf(client, "/social/contenuti/nuovo")

    r = client.post("/social/impostazioni/prompt-template",
                    data={"tipologia": "promozione", "prompt": "x", "csrf": csrf},
                    follow_redirects=False)
    assert r.status_code == 403


def test_salva_prompt_template_admin_aggiorna_il_setting(conn, client):
    db_social.crea_utente(conn, "admin-promo@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-promo@test.local")
    csrf = _csrf(client, "/social/contenuti/nuovo")

    r = client.post("/social/impostazioni/prompt-template",
                    data={"tipologia": "promozione", "prompt": "Nuovo template {NOME_PROMO}",
                          "csrf": csrf},
                    follow_redirects=False)
    assert r.status_code == 303
    assert db_social.get_setting(conn, "prompt_templates_immagine")["promozione"] == (
        "Nuovo template {NOME_PROMO}")


def test_pagina_impostazioni_mostra_il_template_promozione(conn, client):
    db_social.set_setting(conn, "prompt_templates_immagine", {"promozione": "Template attuale"})
    db_social.crea_utente(conn, "admin-promo2@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-promo2@test.local")

    pagina = client.get("/social/impostazioni").text
    assert "Template attuale" in pagina
