"""Carosello Instagram: quando il Research Agent trova piu' di un bando, il
Visual Agent genera un'immagine per bando (invece di sceglierne uno solo,
comportamento segnalato dall'utente) e l'Instagram Adapter pubblica un vero
post carosello — LinkedIn resta a immagine singola (nessun equivalente
richiesto per questo repo)."""

import pytest

from social import agents, db_social, llm, models
from social.images import MASSIMO_IMMAGINI_CAROSELLO, MockImageProvider
from social.integrations.base import MockAdapter, asset_a_lista
from social.integrations.instagram import InstagramAdapter

_BANDI_ESEMPIO = [
    {"id": "CONC-1", "titolo": "Funzionario amministrativo Ente 1", "enti": ["Ente 1"],
     "num_posti": 5, "scadenza": "2026-12-01", "sintesi": "Concorso 1.",
     "titolo_studio_richiesto": "Laurea", "competenze": []},
    {"id": "CONC-2", "titolo": "Funzionario amministrativo Ente 2", "enti": ["Ente 2"],
     "num_posti": 3, "scadenza": "2026-12-15", "sintesi": "Concorso 2.",
     "titolo_studio_richiesto": "Diploma", "competenze": []},
    {"id": "CONC-3", "titolo": "Funzionario amministrativo Ente 3", "enti": ["Ente 3"],
     "num_posti": 1, "scadenza": "2026-12-20", "sintesi": "Concorso 3.",
     "titolo_studio_richiesto": None, "competenze": []},
]


def _risultato_con_bandi(n):
    return models.RisultatoRicerca(
        fatti=[models.FattoVerificato(fatto="fatto di prova", confidenza=0.9)],
        sintesi="Sintesi di prova.", bandi_trovati=_BANDI_ESEMPIO[:n])


@pytest.mark.parametrize("enti,atteso", [
    (["Agenzia Italiana del Farmaco - AIFA"], "Agenzia Italiana del Farmaco - AIFA"),
    (["Ente A", "Ente B"], "Ente A, Ente B"),
    ([], "n/d"),
    (None, "n/d"),
    ("Ente singolo come stringa", "Ente singolo come stringa"),
])
def test_formatta_ente(enti, atteso):
    assert agents._formatta_ente(enti) == atteso


@pytest.mark.parametrize("scadenza,atteso", [
    ("2026-07-25T21:59:00Z", "25 luglio 2026"),
    ("2026-12-01", "1 dicembre 2026"),
    (None, None),
    ("", None),
    ("testo non valido", "testo non valido"),  # non nasconde il dato se non riconosciuto
])
def test_formatta_scadenza(scadenza, atteso):
    assert agents._formatta_scadenza(scadenza) == atteso


def test_richiesta_immagine_da_bando_non_mostra_la_lista_python_grezza():
    """Regressione: 'Ente: [...]' con parentesi quadre e apici era la repr
    Python grezza della lista, mostrata cosi' com'e' in un'immagine social
    reale (bando AIFA, segnalato dall'utente)."""
    bando = {
        "id": "CONC-AIFA", "titolo": "Concorso pubblico AIFA",
        "enti": ["Agenzia Italiana del Farmaco - AIFA"], "num_posti": 5,
        "scadenza": "2026-07-25T21:59:00Z", "sintesi": "Concorso AIFA.",
        "titolo_studio_richiesto": None,
    }
    richiesta = agents._richiesta_immagine_da_bando(bando, "instagram_feed", "content-1")
    assert "Ente: Agenzia Italiana del Farmaco - AIFA" in richiesta.dati_chiave
    assert "Scadenza: 25 luglio 2026" in richiesta.dati_chiave
    assert not any("[" in dato for dato in richiesta.dati_chiave)


def test_richiesta_immagine_da_bando_mostra_solo_ente_posti_scadenza():
    """Segnalato dall'utente: voleva solo titolo/sottotitolo/Ente/Posti/
    Scadenza nella card dati, niente altro (es. Titolo di studio) anche
    quando il bando lo riporta."""
    bando = {
        "id": "CONC-1", "titolo": "Concorso pubblico", "enti": ["Ente"],
        "num_posti": 5, "scadenza": "2026-07-25T21:59:00Z", "sintesi": "Sintesi.",
        "titolo_studio_richiesto": "Laurea magistrale",
    }
    richiesta = agents._richiesta_immagine_da_bando(bando, "instagram_feed", "content-1")
    assert len(richiesta.dati_chiave) == 3
    assert not any("studio" in dato.lower() for dato in richiesta.dati_chiave)


@pytest.mark.parametrize("testo,atteso", [
    (None, None),
    ("", None),
    ("Una sola frase senza punto finale", "Una sola frase senza punto finale"),
    ("Prima frase. Seconda frase molto piu' lunga che non dovrebbe comparire.",
     "Prima frase."),
    ("Frase con punto esclamativo! E poi altro testo.", "Frase con punto esclamativo!"),
    ("Frase con punto interrogativo? E poi altro testo.", "Frase con punto interrogativo?"),
])
def test_sottotitolo_sintetico_prima_frase(testo, atteso):
    assert agents._sottotitolo_sintetico(testo) == atteso


def test_sottotitolo_sintetico_tronca_se_nessun_punto_entro_il_limite():
    testo = "Testo lunghissimo senza punteggiatura " + "parola " * 30
    risultato = agents._sottotitolo_sintetico(testo, lunghezza_massima=50)
    assert risultato.endswith("…")
    assert len(risultato) <= 51


def test_richiesta_immagine_da_bando_sottotitolo_e_la_prima_frase_della_sintesi():
    bando = {"id": "CONC-1", "titolo": "Concorso",
             "sintesi": "Riservata al personale interno. Requisiti aggiuntivi molto dettagliati qui."}
    richiesta = agents._richiesta_immagine_da_bando(bando, "instagram_feed", "content-1")
    assert richiesta.sottotitolo == "Riservata al personale interno."


def test_richiesta_immagine_da_bando_applica_il_prompt_della_categoria_per_bando():
    """Regressione: una categoria "Concorsi" personalizzata (prompt/
    immagini/stile) restava completamente ignorata nel carosello, che
    passava sempre per lo stile fisso di default indipendentemente dalla
    configurazione (segnalato dall'utente: la grafica non cambiava mai
    nonostante la categoria fosse stata modificata). {TITOLO}/{SCADENZA}
    vanno sostituiti coi dati del bando specifico, non del content, perche'
    ogni slide del carosello ha il proprio bando."""
    bando = {"id": "CONC-1", "titolo": "Concorso pubblico Comune di Trieste",
             "enti": ["Comune di Trieste"], "num_posti": 5,
             "scadenza": "2026-08-07T12:00:00Z", "sintesi": "Sintesi."}
    categoria = {"prompt_ai": "Mockup per {TITOLO}, scadenza {SCADENZA}.",
                "immagini_riferimento": ["/a.png", "/b.png"]}
    richiesta = agents._richiesta_immagine_da_bando(
        bando, "instagram_feed", "content-1", categoria=categoria,
        immagini_riferimento=categoria["immagini_riferimento"], stile_immagine="Stile x")
    assert richiesta.prompt_ai == "Mockup per Concorso pubblico Comune di Trieste, scadenza 7 agosto 2026."
    assert richiesta.immagini_riferimento == ["/a.png", "/b.png"]
    assert richiesta.stile_ai == "Stile x"


def test_richiesta_immagine_da_bando_senza_categoria_non_passa_nulla():
    bando = {"id": "CONC-1", "titolo": "Concorso"}
    richiesta = agents._richiesta_immagine_da_bando(bando, "instagram_feed", "content-1")
    assert richiesta.prompt_ai is None
    assert richiesta.immagini_riferimento == []
    assert richiesta.stile_ai is None


def test_richiesta_immagine_da_bando_categoria_con_prompt_vuoto_non_lo_sostituisce():
    bando = {"id": "CONC-1", "titolo": "Concorso"}
    categoria = {"prompt_ai": "", "immagini_riferimento": []}
    richiesta = agents._richiesta_immagine_da_bando(bando, "instagram_feed", "content-1", categoria=categoria)
    assert richiesta.prompt_ai is None


def test_visual_carosello_applica_prompt_e_immagini_della_categoria_a_ogni_bando(conn):
    """End-to-end: agents.visual() deve propagare prompt_ai/immagini_
    riferimento/stile_immagine della categoria a CIASCUNA immagine del
    carosello, non solo al caso a immagine singola."""
    from social.images import MockImageProvider

    categoria_id = db_social.crea_categoria(
        conn, "Concorsi personalizzati", "Mockup per {TITOLO}.",
        immagini_riferimento=["/campanella.png"], strategia_fatti="bandi_jobinpa",
        stile_immagine="Stile schede concorso")
    content_id = db_social.crea_content(conn, "Tre concorsi amministrativi",
                                        canali=["instagram"], categoria_id=categoria_id)
    provider = llm.MockLLMProvider(conn)
    spia = MockImageProvider()
    agents.visual(conn, content_id, _risultato_con_bandi(3), provider=provider, image_provider=spia)
    assert len(spia.richieste) == 3
    for richiesta, bando in zip(spia.richieste, _BANDI_ESEMPIO):
        assert richiesta.prompt_ai == f"Mockup per {bando['titolo']}."
        assert richiesta.immagini_riferimento == ["/campanella.png"]
        assert richiesta.stile_ai == "Stile schede concorso"


def test_visual_genera_un_immagine_per_bando_su_instagram(conn):
    content_id = db_social.crea_content(conn, "Tre concorsi amministrativi",
                                        canali=["instagram", "linkedin"])
    provider = llm.MockLLMProvider(conn)
    agents.visual(conn, content_id, _risultato_con_bandi(3),
                  provider=provider, image_provider=MockImageProvider())
    asset = db_social.asset_di(conn, content_id)
    per_piattaforma = {}
    for a in asset:
        per_piattaforma.setdefault(a["piattaforma"], []).append(a)
    assert len(per_piattaforma["instagram"]) == 3
    assert len(per_piattaforma["linkedin"]) == 1


def test_visual_con_un_solo_bando_resta_immagine_singola(conn):
    """Con 0 o 1 bando il comportamento e' quello di sempre: una sola
    immagine per piattaforma, scelta dal Visual Agent (visual_brief)."""
    content_id = db_social.crea_content(conn, "Un solo concorso",
                                        canali=["instagram", "linkedin"])
    provider = llm.MockLLMProvider(conn)
    agents.visual(conn, content_id, _risultato_con_bandi(1),
                  provider=provider, image_provider=MockImageProvider())
    asset = db_social.asset_di(conn, content_id)
    per_piattaforma = {}
    for a in asset:
        per_piattaforma.setdefault(a["piattaforma"], []).append(a)
    assert len(per_piattaforma["instagram"]) == 1
    assert len(per_piattaforma["linkedin"]) == 1


def test_visual_con_un_solo_bando_usa_i_dati_reali_non_la_scelta_del_visual_agent(conn):
    """Segnalato dall'utente: con un solo bando (niente carosello, es.
    LinkedIn o un bando specifico su Instagram) l'immagine mostrava dati
    scelti liberamente dal Visual Agent (es. 'Ripartizione', 'Riferimento
    CCNL') invece dei soli Ente/Posti/Scadenza reali del bando — stesso
    principio gia' applicato al carosello, qui esteso all'immagine
    singola quando un bando vero e' disponibile."""
    content_id = db_social.crea_content(conn, "Un solo concorso",
                                        canali=["instagram", "linkedin"])
    provider = llm.MockLLMProvider(conn)
    agents.visual(conn, content_id, _risultato_con_bandi(1),
                  provider=provider, image_provider=MockImageProvider())
    asset = db_social.asset_di(conn, content_id)
    bando = _BANDI_ESEMPIO[0]
    for a in asset:
        assert a["titolo"] == bando["titolo"]
        dati = a["dati_chiave"]
        assert dati is not None
        import json
        dati = json.loads(dati) if isinstance(dati, str) else dati
        assert f"Ente: {bando['enti'][0]}" in dati
        assert f"Posti: {bando['num_posti']}" in dati
        assert len(dati) == 3
        # Non il placeholder demo del Visual Agent (vedi llm._risposta_demo).
        assert a["titolo"] != "[DEMO] Nuovo concorso"


def test_visual_carosello_rispetta_il_limite_di_dieci(conn):
    """Anche se il Research Agent trovasse piu' di 10 bandi (limite
    superato in futuro), il carosello non deve mai superare il vero
    limite di Instagram."""
    tanti_bandi = [dict(b, id=f"CONC-{i}") for i, b in
                  enumerate((_BANDI_ESEMPIO * 5)[:15])]
    content_id = db_social.crea_content(conn, "Quindici concorsi",
                                        canali=["instagram"])
    provider = llm.MockLLMProvider(conn)
    risultato = models.RisultatoRicerca(fatti=[], sintesi="", bandi_trovati=tanti_bandi)
    agents.visual(conn, content_id, risultato,
                  provider=provider, image_provider=MockImageProvider())
    asset = db_social.asset_di(conn, content_id)
    assert len(asset) == MASSIMO_IMMAGINI_CAROSELLO


def test_copywriting_invita_a_scorrere_se_carosello(conn):
    content_id = db_social.crea_content(conn, "Tre concorsi amministrativi")
    provider = llm.MockLLMProvider(conn)
    agents.copywriting(conn, content_id, _risultato_con_bandi(3), provider=provider)
    prompt_instagram = next(u for (_, u, schema) in provider.chiamate
                            if "Instagram" in u)
    assert "carosello" in prompt_instagram.lower()


def test_copywriting_non_menziona_carosello_con_un_bando(conn):
    content_id = db_social.crea_content(conn, "Un concorso")
    provider = llm.MockLLMProvider(conn)
    agents.copywriting(conn, content_id, _risultato_con_bandi(1), provider=provider)
    prompt_instagram = next(u for (_, u, schema) in provider.chiamate
                            if "Instagram" in u)
    assert "carosello" not in prompt_instagram.lower()


def test_research_popola_bandi_trovati(conn):
    class _ClientFinto:
        configurato = True

        def bandi(self, *, stato="OPEN", limit=10, **_):
            return _BANDI_ESEMPIO

        def bando(self, concorso_id):
            return None

    content_id = db_social.crea_content(conn, "Contenuto senza brief")
    risultato = agents.research(conn, content_id, provider=llm.MockLLMProvider(conn),
                                jobinpa_client_=_ClientFinto())
    assert len(risultato.bandi_trovati) == 3
    assert risultato.bandi_trovati[0]["id"] == "CONC-1"


# --- asset_a_lista / MockAdapter --------------------------------------------

@pytest.mark.parametrize("valore,atteso", [
    (None, []), ("a.png", ["a.png"]), (["a.png", "b.png"], ["a.png", "b.png"]),
    ((), []), (["a.png", None, "b.png"], ["a.png", "b.png"]),
])
def test_asset_a_lista(valore, atteso):
    assert asset_a_lista(valore) == atteso


def test_mock_adapter_registra_lista_di_asset():
    adapter = MockAdapter("instagram")
    adapter.publish("caption", ["img1.png", "img2.png", "img3.png"])
    assert adapter.pubblicati[0]["asset"] == ["img1.png", "img2.png", "img3.png"]


def test_mock_adapter_singolo_asset_resta_lista_di_uno():
    adapter = MockAdapter("linkedin")
    adapter.publish("caption", "img1.png")
    assert adapter.pubblicati[0]["asset"] == ["img1.png"]


# --- InstagramAdapter: post singolo vs carosello -----------------------------

class _RispostaFinta:
    def __init__(self, dati):
        self._dati = dati

    def raise_for_status(self):
        pass

    def json(self):
        return self._dati


def _adapter_instagram_pronto(conn, monkeypatch):
    adapter = InstagramAdapter(conn)
    monkeypatch.setattr(adapter, "health_check", lambda: {"pronto": True})
    monkeypatch.setattr(adapter, "_token", lambda: "token-finto")
    adapter.cfg = {**adapter.cfg, "graph_api_version": "v21.0"}
    adapter.account = {"identificativo": "IG123"}
    # Il container e' subito FINISHED: i test sul flusso di pubblicazione
    # non riguardano l'attesa (vedi test_attendi_container_pronto_*).
    monkeypatch.setattr("social.integrations.instagram.requests.get",
                        lambda url, params=None, timeout=None: _RispostaFinta(
                            {"status_code": "FINISHED"}))
    return adapter


def test_publish_singola_immagine_non_usa_il_flusso_carosello(conn, monkeypatch):
    adapter = _adapter_instagram_pronto(conn, monkeypatch)
    chiamate = []

    def post_finto(url, data=None, timeout=None):
        chiamate.append((url, data))
        if url.endswith("/media"):
            return _RispostaFinta({"id": "container-1"})
        return _RispostaFinta({"id": "media-1"})

    monkeypatch.setattr("social.integrations.instagram.requests.post", post_finto)
    risultato = adapter.publish("caption di prova", "https://example.invalid/img.png")
    assert risultato.remote_id == "media-1"
    # un solo container creato (no is_carousel_item, no media_type CAROUSEL)
    assert len(chiamate) == 2
    assert "is_carousel_item" not in chiamate[0][1]
    assert chiamate[0][1]["image_url"] == "https://example.invalid/img.png"


def test_publish_carosello_crea_un_container_per_immagine_piu_il_padre(conn, monkeypatch):
    adapter = _adapter_instagram_pronto(conn, monkeypatch)
    chiamate = []
    contatore = {"n": 0}

    def post_finto(url, data=None, timeout=None):
        chiamate.append((url, data))
        if url.endswith("/media") and data.get("is_carousel_item"):
            contatore["n"] += 1
            return _RispostaFinta({"id": f"figlio-{contatore['n']}"})
        if url.endswith("/media") and data.get("media_type") == "CAROUSEL":
            return _RispostaFinta({"id": "container-padre"})
        return _RispostaFinta({"id": "media-carosello"})

    monkeypatch.setattr("social.integrations.instagram.requests.post", post_finto)
    immagini = ["https://example.invalid/1.png", "https://example.invalid/2.png",
                "https://example.invalid/3.png"]
    risultato = adapter.publish("caption carosello", immagini)
    assert risultato.remote_id == "media-carosello"
    # 3 container figli + 1 container padre + 1 publish = 5 chiamate
    assert len(chiamate) == 5
    chiamata_padre = next(c for c in chiamate if c[1].get("media_type") == "CAROUSEL")
    assert chiamata_padre[1]["children"] == "figlio-1,figlio-2,figlio-3"
    assert chiamata_padre[1]["caption"] == "caption carosello"


# --- Attesa che il container sia pronto (FINISHED) prima del publish -------
# Bug reale: pubblicare subito dopo la creazione del container puo'
# fallire con 400 in modo intermittente se Meta non ha ancora finito di
# scaricare/validare l'immagine (status_code resta IN_PROGRESS per
# qualche secondo). Riprodotto una volta su un contenuto vero: il
# publish falliva, la stessa identica chiamata rieseguita subito dopo
# a mano riusciva perche' nel frattempo il container era FINISHED.

from social.integrations.instagram import _attendi_container_pronto


def test_attendi_container_pronto_ritorna_subito_se_gia_finished(monkeypatch):
    chiamate = []
    monkeypatch.setattr("social.integrations.instagram.requests.get",
                        lambda *a, **k: chiamate.append(1) or _RispostaFinta(
                            {"status_code": "FINISHED"}))
    monkeypatch.setattr("social.integrations.instagram.time.sleep", lambda s: None)
    _attendi_container_pronto("container-1", "token", "v21.0")
    assert len(chiamate) == 1


def test_attendi_container_pronto_ripete_finche_non_e_finished(monkeypatch):
    stati = iter(["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
    attese = []
    monkeypatch.setattr(
        "social.integrations.instagram.requests.get",
        lambda *a, **k: _RispostaFinta({"status_code": next(stati)}))
    monkeypatch.setattr("social.integrations.instagram.time.sleep",
                        lambda s: attese.append(s))
    _attendi_container_pronto("container-1", "token", "v21.0")
    assert attese == [2, 2]


def test_attendi_container_pronto_solleva_errore_se_stato_error(monkeypatch):
    monkeypatch.setattr("social.integrations.instagram.requests.get",
                        lambda *a, **k: _RispostaFinta({"status_code": "ERROR"}))
    with pytest.raises(RuntimeError, match="elaborazione immagine"):
        _attendi_container_pronto("container-1", "token", "v21.0")


def test_attendi_container_pronto_solleva_timeout(monkeypatch):
    orologio = iter([0, 5, 15, 25, 35])
    monkeypatch.setattr("social.integrations.instagram.time.monotonic",
                        lambda: next(orologio))
    monkeypatch.setattr("social.integrations.instagram.time.sleep", lambda s: None)
    monkeypatch.setattr("social.integrations.instagram.requests.get",
                        lambda *a, **k: _RispostaFinta({"status_code": "IN_PROGRESS"}))
    with pytest.raises(RuntimeError, match="timeout"):
        _attendi_container_pronto("container-1", "token", "v21.0")


def test_publish_singola_immagine_attende_il_container_prima_di_pubblicare(conn, monkeypatch):
    adapter = _adapter_instagram_pronto(conn, monkeypatch)
    ordine = []
    monkeypatch.setattr(
        "social.integrations.instagram.requests.get",
        lambda *a, **k: ordine.append("get") or _RispostaFinta({"status_code": "FINISHED"}))

    def post_finto(url, data=None, timeout=None):
        ordine.append("publish" if url.endswith("media_publish") else "post")
        if url.endswith("/media"):
            return _RispostaFinta({"id": "container-1"})
        return _RispostaFinta({"id": "media-1"})

    monkeypatch.setattr("social.integrations.instagram.requests.post", post_finto)
    adapter.publish("caption", "https://example.invalid/img.png")
    assert ordine == ["post", "get", "publish"]


def test_publish_carosello_rispetta_il_limite_di_dieci_immagini(conn, monkeypatch):
    adapter = _adapter_instagram_pronto(conn, monkeypatch)
    chiamate_figli = []

    def post_finto(url, data=None, timeout=None):
        if data.get("is_carousel_item"):
            chiamate_figli.append(data)
            return _RispostaFinta({"id": f"figlio-{len(chiamate_figli)}"})
        if data.get("media_type") == "CAROUSEL":
            return _RispostaFinta({"id": "container-padre"})
        return _RispostaFinta({"id": "media-carosello"})

    monkeypatch.setattr("social.integrations.instagram.requests.post", post_finto)
    quindici_immagini = [f"https://example.invalid/{i}.png" for i in range(15)]
    adapter.publish("caption", quindici_immagini)
    assert len(chiamate_figli) == MASSIMO_IMMAGINI_CAROSELLO
