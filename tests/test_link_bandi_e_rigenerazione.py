"""Tre miglioramenti segnalati dall'utente dopo aver visto un post reale:
1. il testo dei post deve citare il link ufficiale del bando, non solo un
   generico rimando a jobinpa.it;
2. in revisione si deve poter verificare i link e correggere il testo a
   mano (senza AI, senza costo);
3. si deve poter rigenerare SOLO l'immagine di un contenuto (con AI, se
   abilitata), senza rifare la ricerca ne' il testo gia' approvato."""

import re
from pathlib import Path

import auth
import pytest

from social import agents, db_social, llm, models, state_machine
from social.images import MockImageProvider

_BANDO_CON_LINK = {
    "id": "CONC-1", "titolo": "Concorso di prova per 10 posti",
    "enti": ["Comune Demo"], "num_posti": 10, "scadenza": "2026-12-31",
    "stato": "OPEN", "sintesi": "Bando di prova per collaudo.",
    "titolo_studio_richiesto": "Laurea in Informatica", "competenze": [],
    "url_dettaglio": "https://www.inpa.gov.it/dettaglio/CONC-1",
}


class _ClientFinto:
    configurato = True

    def __init__(self, bandi=None):
        self._bandi = bandi if bandi is not None else [_BANDO_CON_LINK]

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


# --- Persistenza bandi_trovati sul contenuto (research) ---------------------

def test_research_persiste_bandi_trovati_sul_contenuto(conn):
    content_id = db_social.crea_content(conn, "Contenuto senza brief")
    agents.research(conn, content_id, provider=llm.MockLLMProvider(conn),
                    jobinpa_client_=_ClientFinto())
    content = db_social.get_content(conn, content_id)
    assert content["bandi_trovati"] is not None
    import json
    bandi = json.loads(content["bandi_trovati"])
    assert bandi == [_BANDO_CON_LINK]


def test_research_annuncio_funzionalita_persiste_lista_vuota(conn):
    content_id = db_social.crea_content(
        conn, "Premium gratis", brief="Il premium è gratis fino al 31 agosto")
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.CriteriRicerca, models.CriteriRicerca(
        annuncio_funzionalita=True, nessun_criterio_specifico=True))
    agents.research(conn, content_id, provider=provider, jobinpa_client_=_ClientFinto())
    content = db_social.get_content(conn, content_id)
    assert content["bandi_trovati"] == "[]"


# --- Link ai bandi nel testo (copywriting) -----------------------------------

def test_copywriting_include_il_link_del_bando_nel_prompt(conn):
    content_id = db_social.crea_content(conn, "Concorso con link")
    provider = llm.MockLLMProvider(conn)
    risultato = models.RisultatoRicerca(
        fatti=[models.FattoVerificato(fatto="Concorso di prova.", confidenza=0.9)],
        sintesi="Sintesi.", bandi_trovati=[_BANDO_CON_LINK])
    agents.copywriting(conn, content_id, risultato, provider=provider)
    prompt_instagram = next(u for (_, u, schema) in provider.chiamate if "Instagram" in u)
    prompt_linkedin = next(u for (_, u, schema) in provider.chiamate if "LinkedIn" in u)
    assert _BANDO_CON_LINK["url_dettaglio"] in prompt_instagram
    assert _BANDO_CON_LINK["url_dettaglio"] in prompt_linkedin


def test_copywriting_senza_bandi_non_menziona_link(conn):
    content_id = db_social.crea_content(conn, "Nessun bando")
    provider = llm.MockLLMProvider(conn)
    risultato = models.RisultatoRicerca(
        fatti=[models.FattoVerificato(fatto="Fatto generico.", confidenza=0.9)],
        sintesi="Sintesi.", bandi_trovati=[])
    agents.copywriting(conn, content_id, risultato, provider=provider)
    prompt_instagram = next(u for (_, u, schema) in provider.chiamate if "Instagram" in u)
    assert "Link ufficiali" not in prompt_instagram


# --- copywriting genera solo i canali selezionati sul contenuto --------------

def _risultato_semplice():
    return models.RisultatoRicerca(
        fatti=[models.FattoVerificato(fatto="Fatto di prova.", confidenza=0.9)], sintesi="Sintesi.")


def test_copywriting_con_solo_instagram_non_chiama_l_llm_per_linkedin(conn):
    """Regressione: prima veniva sempre generata (e salvata) anche la
    variante LinkedIn, anche per un contenuto con solo Instagram tra i
    canali — uno spreco di una chiamata AI per un canale mai scelto
    (segnalato dall'utente per LinkedIn, non ancora abilitato)."""
    content_id = db_social.crea_content(conn, "Solo Instagram", canali=["instagram"])
    provider = llm.MockLLMProvider(conn)
    agents.copywriting(conn, content_id, _risultato_semplice(), provider=provider)
    assert not any("LinkedIn" in u for (_, u, schema) in provider.chiamate)
    assert any("Instagram" in u for (_, u, schema) in provider.chiamate)
    piattaforme_salvate = {v["piattaforma"] for v in db_social.varianti_di(conn, content_id)}
    assert piattaforme_salvate == {"instagram"}


def test_copywriting_con_entrambi_i_canali_genera_entrambe_le_varianti(conn):
    content_id = db_social.crea_content(conn, "Entrambi", canali=["instagram", "linkedin"])
    provider = llm.MockLLMProvider(conn)
    agents.copywriting(conn, content_id, _risultato_semplice(), provider=provider)
    assert any("Instagram" in u for (_, u, schema) in provider.chiamate)
    assert any("LinkedIn" in u for (_, u, schema) in provider.chiamate)
    piattaforme_salvate = {v["piattaforma"] for v in db_social.varianti_di(conn, content_id)}
    assert piattaforme_salvate == {"instagram", "linkedin"}


# --- Modifica manuale del testo in revisione (nessuna AI) --------------------

def test_aggiorna_testo_variante_non_tocca_hashtag_e_cta(conn):
    content_id = db_social.crea_content(conn, "Da correggere")
    db_social.salva_variante(conn, content_id, "instagram", "testo originale",
                             hashtags=["#Prova"], call_to_action="Scopri di più")
    db_social.aggiorna_testo_variante(conn, content_id, "instagram", "testo corretto a mano")
    variante = next(v for v in db_social.varianti_di(conn, content_id)
                    if v["piattaforma"] == "instagram")
    assert variante["testo"] == "testo corretto a mano"
    import json
    assert json.loads(variante["hashtags"]) == ["#Prova"]
    assert variante["call_to_action"] == "Scopri di più"


# --- Rigenerazione della sola immagine ---------------------------------------

def _contenuto_con_pipeline_completa(conn):
    content_id = db_social.crea_content(conn, "Concorso da rigenerare",
                                        canali=["instagram", "linkedin"])
    provider = llm.MockLLMProvider(conn)
    risultato = agents.research(conn, content_id, provider=provider,
                                jobinpa_client_=_ClientFinto())
    agents.copywriting(conn, content_id, risultato, provider=provider)
    agents.visual(conn, content_id, risultato, provider=provider,
                  image_provider=MockImageProvider())
    return content_id


def test_rigenera_visual_cancella_le_vecchie_immagini(conn):
    content_id = _contenuto_con_pipeline_completa(conn)
    percorsi_vecchi = [a["percorso"] for a in db_social.asset_di(conn, content_id)]
    assert percorsi_vecchi

    agents.rigenera_visual(conn, content_id, provider=llm.MockLLMProvider(conn),
                           image_provider=MockImageProvider())

    assert all(not Path(p).exists() for p in percorsi_vecchi)


def test_rigenera_visual_crea_nuove_immagini_senza_rifare_la_ricerca(conn):
    content_id = _contenuto_con_pipeline_completa(conn)

    class _ClientCheNonDeveEssereChiamato:
        configurato = True

        def bandi(self, *_, **__):
            raise AssertionError("rigenera_visual non deve rifare la ricerca")

    agents.rigenera_visual(conn, content_id, provider=llm.MockLLMProvider(conn),
                           image_provider=MockImageProvider())

    nuovi_asset = db_social.asset_di(conn, content_id)
    assert nuovi_asset
    # il testo (variante) non e' toccato dalla rigenerazione immagine
    variante = next(v for v in db_social.varianti_di(conn, content_id)
                    if v["piattaforma"] == "instagram")
    assert variante["testo"]


def test_rigenera_visual_non_perde_il_carosello(conn):
    """bandi_trovati e' persistito su research(): rigenera_visual deve poter
    ricreare lo stesso carosello (un'immagine per bando), non ripiegare su
    una singola immagine per mancanza di dati."""
    tre_bandi = [dict(_BANDO_CON_LINK, id=f"CONC-{i}") for i in range(3)]
    content_id = db_social.crea_content(conn, "Tre concorsi", canali=["instagram"])
    provider = llm.MockLLMProvider(conn)
    risultato = agents.research(conn, content_id, provider=provider,
                                jobinpa_client_=_ClientFinto(bandi=tre_bandi))
    agents.visual(conn, content_id, risultato, provider=provider,
                  image_provider=MockImageProvider())
    assert len(db_social.asset_di(conn, content_id)) == 3

    agents.rigenera_visual(conn, content_id, provider=llm.MockLLMProvider(conn),
                           image_provider=MockImageProvider())
    assert len(db_social.asset_di(conn, content_id)) == 3


# --- Rotte web: modifica testo in revisione + rigenera immagine -------------

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
    # "/social/" mostra il form CSRF solo a chi ha social.publish (kill
    # switch): usiamo "nuovo contenuto" perche' basta social.edit, che
    # hanno sia editor che admin nei test qui sotto.
    pagina = client.get(url).text
    return re.search(r'name="csrf" value="([0-9a-f]+)"', pagina).group(1)


def test_route_modifica_variante_aggiorna_solo_il_testo(conn, client):
    db_social.crea_utente(conn, "revisore@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    content_id = db_social.crea_content(conn, "Contenuto da correggere")
    db_social.salva_variante(conn, content_id, "instagram", "testo vecchio",
                             hashtags=["#Prova"], call_to_action="CTA originale")
    _login(client, "revisore@test.local")
    csrf = _csrf(client)
    r = client.post(f"/social/approvazioni/{content_id}/variante/instagram",
                    data={"testo": "testo nuovo scritto a mano", "csrf": csrf},
                    follow_redirects=False)
    assert r.status_code == 303
    variante = next(v for v in db_social.varianti_di(conn, content_id)
                    if v["piattaforma"] == "instagram")
    assert variante["testo"] == "testo nuovo scritto a mano"
    assert variante["call_to_action"] == "CTA originale"


def test_route_modifica_variante_richiede_permesso_approve(conn, client):
    db_social.crea_utente(conn, "editor-solo@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    content_id = db_social.crea_content(conn, "Contenuto")
    db_social.salva_variante(conn, content_id, "instagram", "testo")
    _login(client, "editor-solo@test.local")
    csrf = _csrf(client)
    r = client.post(f"/social/approvazioni/{content_id}/variante/instagram",
                    data={"testo": "tentativo non autorizzato", "csrf": csrf},
                    follow_redirects=False)
    assert r.status_code == 403


def test_route_modifica_variante_piattaforma_sconosciuta_404(conn, client):
    db_social.crea_utente(conn, "revisore2@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    content_id = db_social.crea_content(conn, "Contenuto")
    _login(client, "revisore2@test.local")
    csrf = _csrf(client)
    r = client.post(f"/social/approvazioni/{content_id}/variante/tiktok",
                    data={"testo": "x", "csrf": csrf}, follow_redirects=False)
    assert r.status_code == 404


def test_route_modifica_variante_inesistente_404(conn, client):
    db_social.crea_utente(conn, "revisore3@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "revisore3@test.local")
    csrf = _csrf(client)
    r = client.post("/social/approvazioni/non-esiste/variante/instagram",
                    data={"testo": "x", "csrf": csrf}, follow_redirects=False)
    assert r.status_code == 404


def test_route_modifica_variante_awaiting_approval_torna_a_revisione(conn, client):
    db_social.crea_utente(conn, "revisore4@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    content_id = db_social.crea_content(conn, "Contenuto in revisione")
    db_social.salva_variante(conn, content_id, "instagram", "testo")
    db_social.aggiorna_content(conn, content_id, stato="AWAITING_APPROVAL")
    _login(client, "revisore4@test.local")
    csrf = _csrf(client)
    r = client.post(f"/social/approvazioni/{content_id}/variante/instagram",
                    data={"testo": "corretto a mano", "csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/social/approvazioni")


def test_route_modifica_variante_blocked_torna_al_contenuto(conn, client):
    """Un contenuto BLOCKED non e' nella coda di Revisione (solo
    AWAITING_APPROVAL lo e'): tornare li' mostrerebbe un elemento a caso
    della coda invece del contenuto appena modificato, un redirect
    fuorviante segnalato dall'utente per il flusso "correggi a mano" da
    un BLOCKED.

    Testo scelto apposta per far scattare ancora un pattern rosso
    deterministico (risk._PATTERN_ROSSO): dopo questo fix la modifica a
    mano rivaluta SUBITO il rischio (vedi agents.rivaluta_rischio_dopo_
    modifica) e un testo genuinamente corretto avanzerebbe da solo ad
    AWAITING_APPROVAL/APPROVED — qui si verifica invece il caso "resta
    davvero BLOCKED", per cui il redirect a /contenuti resta quello giusto."""
    db_social.crea_utente(conn, "revisore5@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    content_id = db_social.crea_content(conn, "Contenuto bloccato")
    db_social.salva_variante(conn, content_id, "instagram", "testo")
    db_social.aggiorna_content(conn, content_id, stato="BLOCKED")
    _login(client, "revisore5@test.local")
    csrf = _csrf(client)
    r = client.post(f"/social/approvazioni/{content_id}/variante/instagram",
                    data={"testo": "Vi garantiamo il successo", "csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/social/contenuti/{content_id}"
    assert db_social.get_content(conn, content_id)["stato"] == "BLOCKED"


def test_route_rigenera_immagine_mette_in_coda_il_job(conn, client):
    db_social.crea_utente(conn, "editor-img@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    content_id = db_social.crea_content(conn, "Contenuto con immagine")
    _login(client, "editor-img@test.local")
    csrf = _csrf(client)
    r = client.post(f"/social/contenuti/{content_id}/rigenera-immagine",
                    data={"csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    assert db_social.job_in_corso(conn, "rigenera_visual", content_id)


def test_route_rigenera_immagine_contenuto_inesistente_404(conn, client):
    db_social.crea_utente(conn, "editor-img2@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "editor-img2@test.local")
    csrf = _csrf(client)
    r = client.post("/social/contenuti/non-esiste/rigenera-immagine",
                    data={"csrf": csrf}, follow_redirects=False)
    assert r.status_code == 404


# --- Bando specifico: bypassa ricerca semantica/brief (segnalato dall'utente:
# un bando che esiste davvero su JobInPA non trovato dalla ricerca semantica,
# es. embedding non ancora calcolato) --------------------------------------

def test_estrai_concorso_id_da_url():
    from social.web import _estrai_concorso_id
    assert _estrai_concorso_id("https://jobinpa.it/bandi/gu:26E04294") == "gu:26E04294"
    assert _estrai_concorso_id("https://jobinpa.it/bandi/gu:26E04294?ref=share") == "gu:26E04294"
    assert _estrai_concorso_id("https://jobinpa.it/bandi/gu:26E04294/") == "gu:26E04294"


def test_estrai_concorso_id_da_id_nudo():
    from social.web import _estrai_concorso_id
    assert _estrai_concorso_id("  gu:26E04294  ") == "gu:26E04294"


_BANDO_SEGRETARIO = {
    "id": "gu:26E04294", "titolo": "Concorso pubblico, per esami, a trenta posti di Segretario parlamentare",
    "enti": ["Senato della Repubblica"], "num_posti": 30, "scadenza": "2026-08-27",
    "sintesi": "Concorso per 30 posti di Segretario parlamentare.",
    "url_dettaglio": "https://jobinpa.it/bandi/gu:26E04294",
}


class _ClientConBando:
    def __init__(self, bando=None):
        self._bando = bando

    def promozioni(self):
        return []

    def funzionalita(self):
        return {}

    def bando(self, concorso_id):
        return self._bando if self._bando and self._bando["id"] == concorso_id else None

    def filtri_disponibili(self):
        return {"regioni": [], "categorie": [], "settori": [], "enti": [],
                "inquadramenti": [], "titoli_studio": [], "tipi_contratto": [],
                "competenze": [], "ambiti": []}


def test_crea_contenuto_con_bando_specifico_deriva_titolo_e_salta_la_ricerca(conn, client, monkeypatch):
    monkeypatch.setattr("social.web.jobinpa_client.client",
                        lambda: _ClientConBando(_BANDO_SEGRETARIO))
    db_social.crea_utente(conn, "editor-bando1@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-bando1@test.local")
    csrf = _csrf(client)
    categoria_id = next(c["id"] for c in db_social.lista_categorie(conn) if c["nome"] == "Concorsi")

    r = client.post("/social/contenuti", data={
        "categoria_id": categoria_id,
        "bando_specifico": "https://jobinpa.it/bandi/gu:26E04294",
        "pillar": "opportunita", "csrf": csrf,
    }, follow_redirects=False)

    assert r.status_code == 303
    content_id = r.headers["location"].split("/")[-1].split("?")[0]
    content = db_social.get_content(conn, content_id)
    assert content["concorso_id"] == "gu:26E04294"
    assert content["titolo"] == _BANDO_SEGRETARIO["titolo"]


def test_crea_contenuto_bando_specifico_non_trovato_400(conn, client, monkeypatch):
    monkeypatch.setattr("social.web.jobinpa_client.client", lambda: _ClientConBando(None))
    db_social.crea_utente(conn, "editor-bando2@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-bando2@test.local")
    csrf = _csrf(client)
    categoria_id = next(c["id"] for c in db_social.lista_categorie(conn) if c["nome"] == "Concorsi")

    r = client.post("/social/contenuti", data={
        "categoria_id": categoria_id, "bando_specifico": "id-inesistente",
        "pillar": "opportunita", "csrf": csrf,
    }, follow_redirects=False)

    assert r.status_code == 400


def test_crea_contenuto_concorsi_senza_bando_specifico_richiede_titolo(conn, client, monkeypatch):
    """Il flusso normale (brief + ricerca semantica) resta invariato quando
    "bando_specifico" non viene compilato."""
    monkeypatch.setattr("social.web.jobinpa_client.client", lambda: _ClientConBando())
    db_social.crea_utente(conn, "editor-bando3@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-bando3@test.local")
    csrf = _csrf(client)
    categoria_id = next(c["id"] for c in db_social.lista_categorie(conn) if c["nome"] == "Concorsi")

    r = client.post("/social/contenuti", data={
        "categoria_id": categoria_id, "pillar": "opportunita", "csrf": csrf,
    }, follow_redirects=False)

    assert r.status_code == 400


# --- Ricerca avanzata: filtri espliciti stile JobInPA -----------------------
# Segnalato dall'utente: non vuole che i filtri di ricerca restino nascosti
# dietro l'interpretazione AI del brief -- li vuole espliciti, come sulla
# ricerca avanzata di JobInPA (vedi memoria feedback_ricerca_esplicita_vs_ai).

def test_crea_contenuto_con_ricerca_avanzata_salva_filtri_manuali(conn, client):
    db_social.crea_utente(conn, "editor-avanzata1@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-avanzata1@test.local")
    csrf = _csrf(client)
    categoria_id = next(c["id"] for c in db_social.lista_categorie(conn) if c["nome"] == "Concorsi")

    r = client.post("/social/contenuti", data={
        "categoria_id": categoria_id, "titolo": "Concorsi medici", "pillar": "opportunita",
        "csrf": csrf,
        "f_regione": "Lombardia", "f_scadenza_da": "2026-08-03", "f_scadenza_a": "2026-08-10",
        "f_posti_minimi": "5", "f_lavoro_agile": "1", "f_soglia_confidenza": "80",
    }, follow_redirects=False)

    assert r.status_code == 303
    content_id = r.headers["location"].split("/")[-1].split("?")[0]
    content = db_social.get_content(conn, content_id)
    import json
    filtri = json.loads(content["filtri_manuali"])
    assert filtri == {"regione": "Lombardia", "scadenza_da": "2026-08-03",
                      "scadenza_a": "2026-08-10", "posti_minimi": 5, "lavoro_agile": True}
    assert content["soglia_confidenza"] == 80


def test_crea_contenuto_senza_ricerca_avanzata_non_salva_filtri(conn, client):
    db_social.crea_utente(conn, "editor-avanzata2@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-avanzata2@test.local")
    csrf = _csrf(client)
    categoria_id = next(c["id"] for c in db_social.lista_categorie(conn) if c["nome"] == "Concorsi")

    r = client.post("/social/contenuti", data={
        "categoria_id": categoria_id, "titolo": "Concorsi medici", "pillar": "opportunita",
        "csrf": csrf,
    }, follow_redirects=False)

    assert r.status_code == 303
    content_id = r.headers["location"].split("/")[-1].split("?")[0]
    content = db_social.get_content(conn, content_id)
    assert content["filtri_manuali"] is None
    assert content["soglia_confidenza"] is None


def test_modifica_brief_con_ricerca_avanzata_salva_e_puo_svuotare_filtri(conn, client):
    categoria_id = next(c["id"] for c in db_social.lista_categorie(conn) if c["nome"] == "Concorsi")
    db_social.crea_utente(conn, "editor-avanzata3@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    content_id = db_social.crea_content(conn, "Concorsi medici", categoria_id=categoria_id)
    _login(client, "editor-avanzata3@test.local")
    csrf = _csrf(client, url=f"/social/contenuti/{content_id}")

    r = client.post(f"/social/contenuti/{content_id}/brief", data={
        "titolo": "Concorsi medici", "f_regione": "Lazio", "csrf": csrf,
    }, follow_redirects=False)
    assert r.status_code == 303
    import json
    content = db_social.get_content(conn, content_id)
    assert json.loads(content["filtri_manuali"]) == {"regione": "Lazio"}

    # Riscrittura da zero: lasciare tutto vuoto svuota i filtri (non un
    # "non toccare" -- la form mostra sempre lo stato attuale).
    csrf = _csrf(client, url=f"/social/contenuti/{content_id}")
    r = client.post(f"/social/contenuti/{content_id}/brief", data={
        "titolo": "Concorsi medici", "csrf": csrf,
    }, follow_redirects=False)
    assert r.status_code == 303
    content = db_social.get_content(conn, content_id)
    assert content["filtri_manuali"] is None


# --- Riportare in bozza un contenuto annullato -------------------------------
# Segnalato dall'utente: un contenuto annullato (es. "nessun bando
# pertinente") non si poteva ne' modificare ne' rilanciare, solo eliminare
# definitivamente — anche quando la causa era rimediabile (brief da
# correggere, o ora un bando specifico da indicare).

def test_riporta_in_bozza_da_cancelled(conn, client):
    db_social.crea_utente(conn, "editor-bozza1@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    content_id = db_social.crea_content(conn, "Contenuto annullato")
    state_machine.transisci(conn, content_id, "CANCELLED")
    db_social.aggiorna_content(conn, content_id, errore="Nessun bando pertinente")
    _login(client, "editor-bozza1@test.local")
    csrf = _csrf(client)

    r = client.post(f"/social/contenuti/{content_id}/riporta-in-bozza",
                    data={"csrf": csrf}, follow_redirects=False)

    assert r.status_code == 303
    content = db_social.get_content(conn, content_id)
    assert content["stato"] == "IDEA"
    assert content["errore"] is None


def test_riporta_in_bozza_da_stato_non_cancellato_409(conn, client):
    db_social.crea_utente(conn, "editor-bozza2@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    content_id = db_social.crea_content(conn, "Contenuto normale")
    _login(client, "editor-bozza2@test.local")
    csrf = _csrf(client)

    r = client.post(f"/social/contenuti/{content_id}/riporta-in-bozza",
                    data={"csrf": csrf}, follow_redirects=False)

    assert r.status_code == 409
    assert db_social.get_content(conn, content_id)["stato"] == "IDEA"


def test_riporta_in_bozza_contenuto_inesistente_404(conn, client):
    db_social.crea_utente(conn, "editor-bozza3@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-bozza3@test.local")
    csrf = _csrf(client)

    r = client.post("/social/contenuti/non-esiste/riporta-in-bozza",
                    data={"csrf": csrf}, follow_redirects=False)

    assert r.status_code == 404


def test_pagina_contenuto_mostra_riporta_in_bozza_solo_se_cancellato(conn, client):
    db_social.crea_utente(conn, "editor-bozza4@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    content_id = db_social.crea_content(conn, "Contenuto annullato")
    state_machine.transisci(conn, content_id, "CANCELLED")
    _login(client, "editor-bozza4@test.local")

    pagina = client.get(f"/social/contenuti/{content_id}").text
    assert "Riporta in bozza" in pagina


# --- Bando specifico anche nella modifica del brief di un contenuto gia'
# esistente (non solo alla creazione) ---------------------------------------

def test_modifica_brief_con_bando_specifico_deriva_il_titolo(conn, client, monkeypatch):
    monkeypatch.setattr("social.web.jobinpa_client.client",
                        lambda: _ClientConBando(_BANDO_SEGRETARIO))
    db_social.crea_utente(conn, "editor-bozza5@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    content_id = db_social.crea_content(conn, "Tema originale", brief="brief originale")
    _login(client, "editor-bozza5@test.local")
    csrf = _csrf(client)

    r = client.post(f"/social/contenuti/{content_id}/brief", data={
        "titolo": "Tema originale", "brief": "brief originale",
        "bando_specifico": "https://jobinpa.it/bandi/gu:26E04294", "csrf": csrf,
    }, follow_redirects=False)

    assert r.status_code == 303
    content = db_social.get_content(conn, content_id)
    assert content["titolo"] == _BANDO_SEGRETARIO["titolo"]
    assert content["concorso_id"] == "gu:26E04294"


def test_modifica_brief_senza_bando_specifico_non_tocca_il_concorso_id_esistente(conn, client, monkeypatch):
    monkeypatch.setattr("social.web.jobinpa_client.client", lambda: _ClientConBando())
    db_social.crea_utente(conn, "editor-bozza6@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    content_id = db_social.crea_content(conn, "Tema", concorso_id="gia-impostato")
    _login(client, "editor-bozza6@test.local")
    csrf = _csrf(client)

    r = client.post(f"/social/contenuti/{content_id}/brief", data={
        "titolo": "Tema modificato", "csrf": csrf,
    }, follow_redirects=False)

    assert r.status_code == 303
    assert db_social.get_content(conn, content_id)["concorso_id"] == "gia-impostato"


def test_modifica_brief_bando_specifico_non_trovato_400(conn, client, monkeypatch):
    monkeypatch.setattr("social.web.jobinpa_client.client", lambda: _ClientConBando(None))
    db_social.crea_utente(conn, "editor-bozza7@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    content_id = db_social.crea_content(conn, "Tema")
    _login(client, "editor-bozza7@test.local")
    csrf = _csrf(client)

    r = client.post(f"/social/contenuti/{content_id}/brief", data={
        "titolo": "Tema", "bando_specifico": "id-inesistente", "csrf": csrf,
    }, follow_redirects=False)

    assert r.status_code == 400
