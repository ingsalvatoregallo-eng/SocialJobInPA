"""Categorie (menu Categorie): unico punto in cui si decide sia come
procurare/verificare i fatti (strategia_fatti: bandi_jobinpa |
promozioni_jobinpa | libera) sia come generare il post — struttura del
testo (facoltativa, guida il Copywriter) e soggetto/immagini di
riferimento per l'illustrazione AI (facoltativi: /v1/images/edits
accetta piu' immagini nella stessa richiesta). Sostituisce la vecchia
tipologia fissa (concorso | promozione | generico): tre categorie
("Concorsi", "Promozioni", "Funzionalità") sono seminate di default
(vedi db_social._migra) e i contenuti creati in precedenza vengono
collegati automaticamente a quella corrispondente."""

import re

import auth
import pytest
from PIL import Image

from social import agents, db_social, llm, models
from social.images import MockImageProvider

_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415408d763f8cfc0c00000030001a1b7b6210000000049454e44ae426082")


# --- db_social: CRUD -----------------------------------------------------

def test_categorie_di_default_seminate(conn):
    """init_social_db (chiamato dalla fixture conn) gira _migra: le tre
    categorie di base devono esistere sempre, senza doverle creare a
    mano — sostituiscono la vecchia tipologia fissa."""
    categorie = {c["nome"]: c for c in db_social.lista_categorie(conn)}
    assert categorie["Concorsi"]["strategia_fatti"] == "bandi_jobinpa"
    assert categorie["Promozioni"]["strategia_fatti"] == "promozioni_jobinpa"
    assert categorie["Funzionalità"]["strategia_fatti"] == "funzionalita_jobinpa"


def test_categoria_promozioni_seminata_con_stile_immagine_di_default(conn):
    """Lo stile fisso globale (_STILE_OPENAI_IMAGES) e' pensato per bandi/
    concorsi (istituzionale, piatto): "Promozioni" ha bisogno di uno stile
    diverso (glossy/gradient) e deve averlo gia' pronto senza doverlo
    configurare a mano (segnalato dall'utente dopo un risultato 'pessimo')."""
    promozioni = next(c for c in db_social.lista_categorie(conn) if c["nome"] == "Promozioni")
    assert promozioni["stile_immagine"]
    assert "Flat vector" not in promozioni["stile_immagine"]


def test_categoria_promozioni_stile_di_default_non_vieta_piu_il_testo(conn):
    """Il template "promozione" fa comporre all'AI l'intera grafica, testo
    incluso (vedi images._prompt_promozione_completa): un'istruzione "no
    text" nello stile di default (residuo del vecchio layout disegnato a
    mano) lo contraddirebbe direttamente."""
    promozioni = next(c for c in db_social.lista_categorie(conn) if c["nome"] == "Promozioni")
    assert "no text" not in promozioni["stile_immagine"].lower()


def test_crea_categoria_e_recupera(conn):
    categoria_id = db_social.crea_categoria(conn, "Guida rapida", "Illustrazione di {TITOLO}")
    categoria = db_social.get_categoria(conn, categoria_id)
    assert categoria["nome"] == "Guida rapida"
    assert categoria["prompt_ai"] == "Illustrazione di {TITOLO}"
    assert categoria["immagini_riferimento"] == []


def test_crea_categoria_con_piu_immagini_di_riferimento(conn):
    categoria_id = db_social.crea_categoria(
        conn, "Con piu' immagini", "prompt", immagini_riferimento=["/a.png", "/b.png"])
    assert db_social.get_categoria(conn, categoria_id)["immagini_riferimento"] == ["/a.png", "/b.png"]


def test_lista_categorie_ritorna_liste_parsate(conn):
    db_social.crea_categoria(conn, "Con immagini", "prompt", immagini_riferimento=["/a.png"])
    categoria = next(c for c in db_social.lista_categorie(conn) if c["nome"] == "Con immagini")
    assert categoria["immagini_riferimento"] == ["/a.png"]


def test_crea_categoria_nome_duplicato_solleva_errore(conn):
    db_social.crea_categoria(conn, "Guida rapida", "prompt 1")
    with pytest.raises(Exception):  # sqlite3.IntegrityError (UNIQUE su nome)
        db_social.crea_categoria(conn, "Guida rapida", "prompt 2")


def test_elimina_categoria(conn):
    categoria_id = db_social.crea_categoria(conn, "Da eliminare", "prompt")
    assert db_social.elimina_categoria(conn, categoria_id) is True
    assert db_social.get_categoria(conn, categoria_id) is None


def test_elimina_categoria_inesistente_ritorna_false(conn):
    assert db_social.elimina_categoria(conn, "non-esiste") is False


def test_aggiorna_categoria_prompt(conn):
    categoria_id = db_social.crea_categoria(conn, "Guida rapida", "vecchio prompt")
    db_social.aggiorna_categoria(conn, categoria_id, prompt_ai="nuovo prompt")
    assert db_social.get_categoria(conn, categoria_id)["prompt_ai"] == "nuovo prompt"


def test_aggiorna_categoria_immagini_none_non_le_tocca(conn):
    categoria_id = db_social.crea_categoria(
        conn, "Con immagini", "prompt", immagini_riferimento=["/a.png"])
    db_social.aggiorna_categoria(conn, categoria_id, prompt_ai="nuovo prompt")
    assert db_social.get_categoria(conn, categoria_id)["immagini_riferimento"] == ["/a.png"]


def test_aggiorna_categoria_immagini_lista_vuota_le_svuota(conn):
    categoria_id = db_social.crea_categoria(
        conn, "Con immagini", "prompt", immagini_riferimento=["/a.png"])
    db_social.aggiorna_categoria(conn, categoria_id, immagini_riferimento=[])
    assert db_social.get_categoria(conn, categoria_id)["immagini_riferimento"] == []


def test_crea_categoria_senza_stile_immagine_e_none(conn):
    """Vuoto = usa lo stile fisso globale (default, comportamento invariato
    per tutte le categorie che non lo personalizzano, es. Concorsi)."""
    categoria_id = db_social.crea_categoria(conn, "Senza stile", "prompt")
    assert db_social.get_categoria(conn, categoria_id)["stile_immagine"] is None


def test_crea_categoria_con_stile_immagine(conn):
    """Salvato con .strip() (stessa convenzione di prompt_ai): eventuali
    spazi finali non sono significativi qui, images._chiama_api aggiunge
    esplicitamente lo spazio di separazione prima del soggetto."""
    categoria_id = db_social.crea_categoria(
        conn, "Con stile", "prompt", stile_immagine="Stile glossy 3D. Subject: ")
    assert db_social.get_categoria(conn, categoria_id)["stile_immagine"] == "Stile glossy 3D. Subject:"


def test_aggiorna_categoria_stile_immagine_none_non_lo_tocca(conn):
    categoria_id = db_social.crea_categoria(
        conn, "Con stile 2", "prompt", stile_immagine="Stile originale")
    db_social.aggiorna_categoria(conn, categoria_id, prompt_ai="nuovo prompt")
    assert db_social.get_categoria(conn, categoria_id)["stile_immagine"] == "Stile originale"


def test_aggiorna_categoria_stile_immagine_stringa_vuota_lo_cancella(conn):
    categoria_id = db_social.crea_categoria(
        conn, "Con stile 3", "prompt", stile_immagine="Stile originale")
    db_social.aggiorna_categoria(conn, categoria_id, stile_immagine="")
    assert db_social.get_categoria(conn, categoria_id)["stile_immagine"] is None


# --- "base fissa" (esperimento): illustrazione unica per categoria, riusata
# per ogni post invece di richiamare l'AI a ogni volta (vedi images.
# TemplateImageProvider.genera_sync_su_base/BaseFissaImageProvider) --------

def test_crea_categoria_base_fissa_disattivata_di_default(conn):
    """Comportamento invariato per ogni categoria esistente/nuova finche'
    non la si attiva esplicitamente."""
    categoria_id = db_social.crea_categoria(conn, "Senza base fissa", "prompt")
    categoria = db_social.get_categoria(conn, categoria_id)
    assert categoria["usa_base_fissa"] is False
    assert categoria["base_fissa_path"] is None


def test_aggiorna_categoria_base_fissa(conn):
    categoria_id = db_social.crea_categoria(conn, "Con base fissa", "prompt")
    db_social.aggiorna_categoria(conn, categoria_id, base_fissa_path="/tmp/base.png",
                                 usa_base_fissa=True)
    categoria = db_social.get_categoria(conn, categoria_id)
    assert categoria["base_fissa_path"] == "/tmp/base.png"
    assert categoria["usa_base_fissa"] is True


def test_aggiorna_categoria_usa_base_fissa_none_non_lo_tocca(conn):
    categoria_id = db_social.crea_categoria(conn, "Toggle base fissa", "prompt")
    db_social.aggiorna_categoria(conn, categoria_id, base_fissa_path="/tmp/base.png",
                                 usa_base_fissa=True)
    db_social.aggiorna_categoria(conn, categoria_id, prompt_ai="nuovo prompt")
    assert db_social.get_categoria(conn, categoria_id)["usa_base_fissa"] is True


def test_visual_usa_base_fissa_quando_categoria_abilitata(conn, tmp_path):
    """Con usa_base_fissa attivo e un'immagine di base gia' pronta,
    visual() non deve passare dal provider AI/template normale: l'asset
    generato porta la firma del provider dedicato (vedi images.
    BaseFissaImageProvider), zero chiamate AI per questo post."""
    base_path = tmp_path / "base.png"
    Image.new("RGB", (1024, 1536), "#0B3D91").save(base_path, "PNG")
    categoria_id = db_social.crea_categoria(conn, "Concorsi base fissa", strategia_fatti="bandi_jobinpa")
    db_social.aggiorna_categoria(conn, categoria_id, base_fissa_path=str(base_path), usa_base_fissa=True)
    content_id = db_social.crea_content(
        conn, "Bando aperto", canali=["instagram"], categoria_id=categoria_id)
    provider = llm.MockLLMProvider(conn)
    risultato = models.RisultatoRicerca(
        fatti=[models.FattoVerificato(fatto="fatto di prova", confidenza=0.9)], sintesi="Sintesi.")

    agents.visual(conn, content_id, risultato, provider=provider)

    asset = db_social.asset_di(conn, content_id)[0]
    assert asset["provider"] == "template_base_fissa"


def test_visual_ignora_base_fissa_se_image_provider_esplicito(conn, tmp_path):
    """Un image_provider passato esplicitamente (es. i test, o una
    rigenerazione con provider specifico) non viene mai sostituito
    silenziosamente da quello di base fissa."""
    base_path = tmp_path / "base.png"
    Image.new("RGB", (1024, 1536), "#0B3D91").save(base_path, "PNG")
    categoria_id = db_social.crea_categoria(conn, "Concorsi base fissa 2", strategia_fatti="bandi_jobinpa")
    db_social.aggiorna_categoria(conn, categoria_id, base_fissa_path=str(base_path), usa_base_fissa=True)
    content_id = db_social.crea_content(
        conn, "Bando aperto", canali=["instagram"], categoria_id=categoria_id)
    provider = llm.MockLLMProvider(conn)
    risultato = models.RisultatoRicerca(
        fatti=[models.FattoVerificato(fatto="fatto di prova", confidenza=0.9)], sintesi="Sintesi.")

    agents.visual(conn, content_id, risultato, provider=provider, image_provider=MockImageProvider())

    asset = db_social.asset_di(conn, content_id)[0]
    assert asset["provider"] == "mock"


class _RispostaFintaBaseFissa:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        import base64
        return {"data": [{"b64_json": base64.b64encode(_PNG_1X1).decode()}]}


def test_genera_base_fissa_categoria_salva_il_percorso(conn, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-finta")
    monkeypatch.setattr("requests.post", lambda url, **kwargs: _RispostaFintaBaseFissa())
    categoria_id = db_social.crea_categoria(conn, "Da generare", "un soggetto qualsiasi")

    agents.genera_base_fissa_categoria(conn, categoria_id)

    categoria = db_social.get_categoria(conn, categoria_id)
    assert categoria["base_fissa_path"]
    from pathlib import Path
    assert Path(categoria["base_fissa_path"]).exists()


def test_genera_base_fissa_categoria_rigenerando_cancella_il_file_vecchio(conn, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-finta")
    monkeypatch.setattr("requests.post", lambda url, **kwargs: _RispostaFintaBaseFissa())
    categoria_id = db_social.crea_categoria(conn, "Da rigenerare", "un soggetto")
    agents.genera_base_fissa_categoria(conn, categoria_id)
    from pathlib import Path
    vecchio_percorso = Path(db_social.get_categoria(conn, categoria_id)["base_fissa_path"])
    assert vecchio_percorso.exists()

    agents.genera_base_fissa_categoria(conn, categoria_id)

    assert not vecchio_percorso.exists()
    nuovo_percorso = Path(db_social.get_categoria(conn, categoria_id)["base_fissa_path"])
    assert nuovo_percorso.exists()
    assert nuovo_percorso != vecchio_percorso


def test_genera_base_fissa_categoria_inesistente_solleva_errore(conn):
    with pytest.raises(ValueError):
        agents.genera_base_fissa_categoria(conn, "non-esiste")


# --- agents.visual: integrazione categoria -------------------------------

def _content_con_richiesta_catturata(conn, **kwargs):
    content_id = db_social.crea_content(conn, kwargs.pop("titolo", "Tema"),
                                        canali=["instagram"], **kwargs)
    provider = llm.MockLLMProvider(conn)
    risultato = models.RisultatoRicerca(
        fatti=[models.FattoVerificato(fatto="fatto di prova", confidenza=0.9)],
        sintesi="Sintesi.")
    catturate = []

    class _Spia(MockImageProvider):
        async def generate(self, request):
            catturate.append(request)
            return await super().generate(request)

    agents.visual(conn, content_id, risultato, provider=provider, image_provider=_Spia())
    return catturate


def test_visual_usa_la_categoria_esplicita_per_qualsiasi_tipologia(conn):
    """Non solo 'promozione': una categoria scelta esplicitamente si
    applica anche a un contenuto 'generico' (richiesta esplicita
    dell'utente: 'il template deve poter essere configurabile anche per
    gli altri casi non solo promozioni')."""
    categoria_id = db_social.crea_categoria(
        conn, "Novità prodotto", "Illustrazione di {TITOLO}, entro il {SCADENZA}.")
    catturate = _content_con_richiesta_catturata(
        conn, titolo="Nuova funzione AI", tipologia="generico",
        scadenza_promo="2026-09-01", categoria_id=categoria_id)
    assert catturate[0].prompt_ai == "Illustrazione di Nuova funzione AI, entro il 1 settembre 2026."


# --- agents.visual/copywriting: Intento (pillar) guida template e tono ------
# Segnalato dall'utente: la scelta fatta in creazione (Intento) deve
# ritrovarsi davvero nell'immagine (badge) e nel testo (tono), non solo
# restare un'etichetta organizzativa senza effetto (vecchio comportamento
# di pillar/obiettivo, mai riletti da nessun agente).

def _categoria_concorsi(conn):
    return next(c for c in db_social.lista_categorie(conn) if c["nome"] == "Concorsi")


def test_visual_bandi_jobinpa_usa_nuovo_concorso_per_opportunita(conn):
    catturate = _content_con_richiesta_catturata(
        conn, titolo="Bando aperto", categoria_id=_categoria_concorsi(conn)["id"],
        pillar_chiave="opportunita")
    assert catturate[0].template == "nuovo_concorso"


def test_visual_bandi_jobinpa_usa_nuovo_concorso_per_guida_o_senza_intento(conn):
    """"guida" (spiegazione generica) e l'assenza di un Intento (contenuti
    creati prima di questa modifica) non hanno un badge dedicato: restano
    sul default "nuovo_concorso", mai il vecchio template deterministico
    difettoso (vedi images._TEMPLATE_GRAFICA_INTERA)."""
    categoria_id = _categoria_concorsi(conn)["id"]
    catturate = _content_con_richiesta_catturata(
        conn, titolo="Come funziona un concorso", categoria_id=categoria_id, pillar_chiave="guida")
    assert catturate[0].template == "nuovo_concorso"
    catturate = _content_con_richiesta_catturata(
        conn, titolo="Senza intento", categoria_id=categoria_id)
    assert catturate[0].template == "nuovo_concorso"


def test_visual_bandi_jobinpa_usa_scadenza_per_promemoria(conn):
    catturate = _content_con_richiesta_catturata(
        conn, titolo="Ultimi giorni", categoria_id=_categoria_concorsi(conn)["id"],
        pillar_chiave="scadenza")
    assert catturate[0].template == "scadenza"


def test_copywriting_intento_scadenza_cambia_il_tono_del_prompt(conn):
    content_id = db_social.crea_content(
        conn, "Bando in chiusura", categoria_id=_categoria_concorsi(conn)["id"],
        pillar_chiave="scadenza", canali=["instagram"])
    provider = llm.MockLLMProvider(conn)
    risultato = models.RisultatoRicerca(
        fatti=[models.FattoVerificato(fatto="fatto di prova", confidenza=0.9)],
        sintesi="Sintesi.")
    agents.copywriting(conn, content_id, risultato, provider=provider)
    prompt_variante = next(u for _, u, schema in provider.chiamate
                           if schema is models.VarianteCopy)
    assert "promemoria di scadenza" in prompt_variante


def test_copywriting_senza_intento_non_aggiunge_indicazioni_di_tono(conn):
    content_id = db_social.crea_content(
        conn, "Tema qualsiasi", categoria_id=_categoria_concorsi(conn)["id"], canali=["instagram"])
    provider = llm.MockLLMProvider(conn)
    risultato = models.RisultatoRicerca(
        fatti=[models.FattoVerificato(fatto="fatto di prova", confidenza=0.9)],
        sintesi="Sintesi.")
    agents.copywriting(conn, content_id, risultato, provider=provider)
    prompt_variante = next(u for _, u, schema in provider.chiamate
                           if schema is models.VarianteCopy)
    assert "Il tono e'" not in prompt_variante


def test_visual_categoria_promozioni_applica_il_suo_prompt(conn):
    promozioni_id = next(c["id"] for c in db_social.lista_categorie(conn) if c["nome"] == "Promozioni")
    db_social.aggiorna_categoria(conn, promozioni_id, prompt_ai="Regalo per {TITOLO}.")
    catturate = _content_con_richiesta_catturata(
        conn, titolo="Premium gratis", categoria_id=promozioni_id)
    assert catturate[0].prompt_ai == "Regalo per Premium gratis."


def test_visual_categoria_concorsi_ha_prompt_vuoto_non_sovrascrive_nulla(conn):
    """"Concorsi" e' seminata con prompt_ai vuoto apposta: il soggetto
    dell'illustrazione varia da bando a bando, deve restare il giudizio
    del Visual Agent (mai un errore, mai una stringa vuota al posto)."""
    concorsi_id = next(c["id"] for c in db_social.lista_categorie(conn) if c["nome"] == "Concorsi")
    catturate = _content_con_richiesta_catturata(
        conn, titolo="Premium gratis", categoria_id=concorsi_id)
    assert catturate[0].prompt_ai is None  # quello (assente) del MockLLMProvider demo


def test_visual_senza_categoria_esplicita_non_viene_toccato(conn):
    catturate = _content_con_richiesta_catturata(conn, titolo="Concorso normale")
    assert catturate[0].prompt_ai is None


def test_visual_passa_piu_immagini_di_riferimento_alla_richiesta(conn):
    categoria_id = db_social.crea_categoria(
        conn, "Con riferimento", "prompt qualsiasi",
        immagini_riferimento=["/percorso/finto/uno.png", "/percorso/finto/due.png"])
    catturate = _content_con_richiesta_catturata(
        conn, titolo="Tema", tipologia="generico", categoria_id=categoria_id)
    assert catturate[0].immagini_riferimento == ["/percorso/finto/uno.png", "/percorso/finto/due.png"]


def test_visual_senza_categoria_non_passa_immagini_di_riferimento(conn):
    catturate = _content_con_richiesta_catturata(conn, titolo="Tema")
    assert catturate[0].immagini_riferimento == []


def test_visual_categoria_con_stile_immagine_lo_passa_alla_richiesta(conn):
    """Lo stile personalizzato della categoria (menu Categorie) deve
    arrivare fino a ImageGenerationRequest.stile_ai, cosi' da sostituire
    lo stile fisso globale (vedi images.OpenAIImageProvider._chiama_api)."""
    categoria_id = db_social.crea_categoria(
        conn, "Con stile personalizzato", "prompt qualsiasi",
        stile_immagine="Stile glossy 3D, sfondo sfumato viola/blu. Subject: ")
    catturate = _content_con_richiesta_catturata(
        conn, titolo="Tema", categoria_id=categoria_id)
    assert catturate[0].stile_ai == "Stile glossy 3D, sfondo sfumato viola/blu. Subject:"


def test_visual_categoria_senza_stile_immagine_non_passa_nulla(conn):
    categoria_id = db_social.crea_categoria(conn, "Senza stile personalizzato", "prompt qualsiasi")
    catturate = _content_con_richiesta_catturata(
        conn, titolo="Tema", categoria_id=categoria_id)
    assert catturate[0].stile_ai is None


def test_visual_senza_categoria_non_passa_stile_immagine(conn):
    catturate = _content_con_richiesta_catturata(conn, titolo="Tema")
    assert catturate[0].stile_ai is None


def test_visual_categoria_promozioni_forza_il_template_promozione(conn):
    """Il layout a card (badge/logo/illustrazione laterale/CTA, vedi
    images.OpenAIImageProvider._genera_promozione) non puo' dipendere dalla
    scelta libera del Visual Agent, che sceglierebbe uno dei template "a
    sfondo intero" pensati per bandi/concorsi (segnalato dall'utente: il
    risultato restava troppo lontano dal mockup atteso anche con lo stile
    immagine corretto)."""
    promozioni_id = next(c["id"] for c in db_social.lista_categorie(conn) if c["nome"] == "Promozioni")
    catturate = _content_con_richiesta_catturata(
        conn, titolo="Premium gratis", categoria_id=promozioni_id)
    assert catturate[0].template == "promozione"


def test_visual_categoria_concorsi_non_forza_il_template(conn):
    concorsi_id = next(c["id"] for c in db_social.lista_categorie(conn) if c["nome"] == "Concorsi")
    catturate = _content_con_richiesta_catturata(
        conn, titolo="Premium gratis", categoria_id=concorsi_id)
    assert catturate[0].template != "promozione"


# --- agents.copywriting: struttura del post per categoria -----------------

def test_copywriting_inietta_la_struttura_della_categoria(conn):
    categoria_id = db_social.crea_categoria(
        conn, "Con struttura", "", struttura_post="Un titolo e una CTA.")
    content_id = db_social.crea_content(conn, "Tema", categoria_id=categoria_id)
    provider = llm.MockLLMProvider(conn)
    risultato = models.RisultatoRicerca(
        fatti=[models.FattoVerificato(fatto="fatto di prova", confidenza=0.9)],
        sintesi="Sintesi.")
    agents.copywriting(conn, content_id, risultato, provider=provider)
    prompt_instagram = next(u for (_, u, schema) in provider.chiamate if "Instagram" in u)
    assert "Un titolo e una CTA." in prompt_instagram
    assert "Struttura richiesta per questo post" in prompt_instagram


def test_copywriting_categoria_senza_struttura_non_aggiunge_nulla(conn):
    categoria_id = db_social.crea_categoria(conn, "Senza struttura", "")
    content_id = db_social.crea_content(conn, "Tema", categoria_id=categoria_id)
    provider = llm.MockLLMProvider(conn)
    risultato = models.RisultatoRicerca(
        fatti=[models.FattoVerificato(fatto="fatto di prova", confidenza=0.9)],
        sintesi="Sintesi.")
    agents.copywriting(conn, content_id, risultato, provider=provider)
    prompt_instagram = next(u for (_, u, schema) in provider.chiamate if "Instagram" in u)
    assert "Struttura richiesta per questo post" not in prompt_instagram


def test_copywriting_categoria_promozioni_usa_la_struttura_di_default(conn):
    """La categoria "Promozioni" seminata di default ha gia' una
    struttura (etichetta/titolo/punti/CTA, ispirata al mockup fornito
    dall'utente): deve essere usata senza doverla configurare a mano."""
    promozioni = db_social.get_categoria(conn, _categoria_id_helper(conn, "Promozioni"))
    assert promozioni["struttura_post"]
    content_id = db_social.crea_content(conn, "Premium promo", categoria_id=promozioni["id"])
    provider = llm.MockLLMProvider(conn)
    risultato = models.RisultatoRicerca(
        fatti=[models.FattoVerificato(fatto="fatto di prova", confidenza=0.9)],
        sintesi="Sintesi.")
    agents.copywriting(conn, content_id, risultato, provider=provider)
    prompt_instagram = next(u for (_, u, schema) in provider.chiamate if "Instagram" in u)
    assert promozioni["struttura_post"] in prompt_instagram


def _categoria_id_helper(conn, nome):
    return next(c["id"] for c in db_social.lista_categorie(conn) if c["nome"] == nome)


# --- web: rotte CRUD -------------------------------------------------------

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


def _csrf(client, url="/social/categorie"):
    pagina = client.get(url).text
    return re.search(r'name="csrf" value="([0-9a-f]+)"', pagina).group(1)


def test_pagina_categorie_richiede_admin(conn, client):
    db_social.crea_utente(conn, "editor-cat@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-cat@test.local")
    r = client.get("/social/categorie")
    assert r.status_code == 403


def test_crea_categoria_via_web(conn, client):
    db_social.crea_utente(conn, "admin-cat@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-cat@test.local")
    csrf = _csrf(client)

    r = client.post("/social/categorie", data={
        "nome": "Categoria via web", "prompt_ai": "Illustrazione di {TITOLO}",
        "strategia_fatti": "libera", "csrf": csrf,
    }, follow_redirects=False)

    assert r.status_code == 303
    nomi = [c["nome"] for c in db_social.lista_categorie(conn)]
    assert "Categoria via web" in nomi


def test_crea_categoria_con_piu_immagini_di_riferimento_via_web(conn, client):
    """Piu' file caricati insieme (stesso campo "immagini", multiple):
    devono essere salvati tutti e serviti singolarmente per indice."""
    db_social.crea_utente(conn, "admin-cat5@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-cat5@test.local")
    csrf = _csrf(client)

    r = client.post("/social/categorie",
                    data={"nome": "Con immagini", "prompt_ai": "x",
                          "strategia_fatti": "libera", "csrf": csrf},
                    files=[("immagini", ("uno.png", _PNG_1X1, "image/png")),
                          ("immagini", ("due.png", _PNG_1X1 + b"\x00extra", "image/png"))],
                    follow_redirects=False)

    assert r.status_code == 303
    categoria = next(c for c in db_social.lista_categorie(conn) if c["nome"] == "Con immagini")
    assert len(categoria["immagini_riferimento"]) == 2

    risposta_0 = client.get(f"/social/categorie/{categoria['id']}/immagine/0")
    risposta_1 = client.get(f"/social/categorie/{categoria['id']}/immagine/1")
    assert risposta_0.status_code == 200
    assert risposta_1.status_code == 200
    assert risposta_0.content == _PNG_1X1
    assert risposta_1.content == _PNG_1X1 + b"\x00extra"


def test_crea_categoria_con_stile_immagine_via_web(conn, client):
    db_social.crea_utente(conn, "admin-cat11@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-cat11@test.local")
    csrf = _csrf(client)

    r = client.post("/social/categorie", data={
        "nome": "Con stile via web", "prompt_ai": "x", "strategia_fatti": "libera",
        "stile_immagine": "Stile glossy 3D personalizzato", "csrf": csrf,
    }, follow_redirects=False)

    assert r.status_code == 303
    categoria = next(c for c in db_social.lista_categorie(conn) if c["nome"] == "Con stile via web")
    assert categoria["stile_immagine"] == "Stile glossy 3D personalizzato"


def test_aggiorna_categoria_via_web_cambia_lo_stile_immagine(conn, client):
    db_social.crea_utente(conn, "admin-cat12@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-cat12@test.local")
    csrf = _csrf(client)
    categoria_id = db_social.crea_categoria(conn, "Da modificare stile", "x")

    r = client.post(f"/social/categorie/{categoria_id}", data={
        "prompt_ai": "x", "strategia_fatti": "libera",
        "stile_immagine": "Nuovo stile", "csrf": csrf,
    }, follow_redirects=False)

    assert r.status_code == 303
    assert db_social.get_categoria(conn, categoria_id)["stile_immagine"] == "Nuovo stile"


def test_aggiorna_categoria_via_web_cambia_il_prompt(conn, client):
    db_social.crea_utente(conn, "admin-cat6@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-cat6@test.local")
    csrf = _csrf(client)
    categoria_id = db_social.crea_categoria(conn, "Da modificare", "vecchio prompt")

    r = client.post(f"/social/categorie/{categoria_id}", data={
        "prompt_ai": "nuovo prompt per {TITOLO}", "strategia_fatti": "libera", "csrf": csrf,
    }, follow_redirects=False)

    assert r.status_code == 303
    assert db_social.get_categoria(conn, categoria_id)["prompt_ai"] == "nuovo prompt per {TITOLO}"


def test_aggiorna_categoria_aggiunge_nuove_immagini_mantenendo_le_vecchie(conn, client):
    db_social.crea_utente(conn, "admin-cat7@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-cat7@test.local")
    csrf = _csrf(client)
    categoria_id = db_social.crea_categoria(conn, "Con vecchia immagine", "x")
    client.post(f"/social/categorie/{categoria_id}", data={"prompt_ai": "x", "strategia_fatti": "libera", "csrf": csrf},
                files={"immagini_nuove": ("prima.png", _PNG_1X1, "image/png")})

    r = client.post(f"/social/categorie/{categoria_id}", data={"prompt_ai": "x", "strategia_fatti": "libera", "csrf": csrf},
                    files={"immagini_nuove": ("seconda.png", _PNG_1X1 + b"\x00extra", "image/png")},
                    follow_redirects=False)

    assert r.status_code == 303
    assert len(db_social.get_categoria(conn, categoria_id)["immagini_riferimento"]) == 2


def test_aggiorna_categoria_rimuove_una_singola_immagine(conn, client):
    """Puo' rimuovere solo una delle immagini, mantenendo le altre —
    l'utente deve poter comporre/scomporre gli elementi grafici separati."""
    db_social.crea_utente(conn, "admin-cat8@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-cat8@test.local")
    csrf = _csrf(client)
    categoria_id = db_social.crea_categoria(conn, "Da spogliare", "x")
    client.post(f"/social/categorie/{categoria_id}", data={"prompt_ai": "x", "strategia_fatti": "libera", "csrf": csrf},
                files=[("immagini_nuove", ("prima.png", _PNG_1X1, "image/png")),
                      ("immagini_nuove", ("seconda.png", _PNG_1X1 + b"\x00extra", "image/png"))])
    percorso_da_rimuovere = db_social.get_categoria(conn, categoria_id)["immagini_riferimento"][0]

    r = client.post(f"/social/categorie/{categoria_id}",
                    data={"prompt_ai": "x", "strategia_fatti": "libera",
                          "rimuovi_immagini": percorso_da_rimuovere, "csrf": csrf},
                    follow_redirects=False)

    assert r.status_code == 303
    from pathlib import Path
    assert not Path(percorso_da_rimuovere).exists()
    assert len(db_social.get_categoria(conn, categoria_id)["immagini_riferimento"]) == 1


def test_aggiorna_categoria_inesistente_404(conn, client):
    db_social.crea_utente(conn, "admin-cat9@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-cat9@test.local")
    csrf = _csrf(client)

    r = client.post("/social/categorie/non-esiste",
                    data={"prompt_ai": "x", "strategia_fatti": "libera", "csrf": csrf}, follow_redirects=False)
    assert r.status_code == 404


def test_aggiorna_categoria_richiede_admin(conn, client):
    db_social.crea_utente(conn, "editor-cat3@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    categoria_id = db_social.crea_categoria(conn, "Protetta", "x")
    _login(client, "editor-cat3@test.local")
    csrf = _csrf(client, "/social/contenuti/nuovo")

    r = client.post(f"/social/categorie/{categoria_id}",
                    data={"prompt_ai": "y", "strategia_fatti": "libera", "csrf": csrf},
                    follow_redirects=False)
    assert r.status_code == 403


def test_pagina_categorie_mostra_il_prompt_completo_non_troncato(conn, client):
    db_social.crea_utente(conn, "admin-cat10@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-cat10@test.local")
    prompt_lungo = "Illustrazione molto dettagliata " * 10  # oltre 140 caratteri
    db_social.crea_categoria(conn, "Prompt lungo", prompt_lungo)

    pagina = client.get("/social/categorie").text
    assert prompt_lungo.strip() in pagina  # crea_categoria fa .strip() sul prompt salvato


def test_crea_categoria_nome_duplicato_via_web_400(conn, client):
    db_social.crea_utente(conn, "admin-cat2@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-cat2@test.local")
    csrf = _csrf(client)
    client.post("/social/categorie",
                data={"nome": "Doppione", "prompt_ai": "x", "strategia_fatti": "libera", "csrf": csrf})

    r = client.post("/social/categorie",
                    data={"nome": "Doppione", "prompt_ai": "y",
                          "strategia_fatti": "libera", "csrf": csrf})
    assert r.status_code == 400


# --- web: "base fissa" ------------------------------------------------------

def test_aggiorna_categoria_via_web_attiva_usa_base_fissa(conn, client):
    db_social.crea_utente(conn, "admin-cat13@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-cat13@test.local")
    csrf = _csrf(client)
    categoria_id = db_social.crea_categoria(conn, "Con toggle base fissa", "x")
    db_social.aggiorna_categoria(conn, categoria_id, base_fissa_path="/tmp/base.png")

    r = client.post(f"/social/categorie/{categoria_id}", data={
        "prompt_ai": "x", "strategia_fatti": "libera", "usa_base_fissa": "on", "csrf": csrf,
    }, follow_redirects=False)

    assert r.status_code == 303
    assert db_social.get_categoria(conn, categoria_id)["usa_base_fissa"] is True


def test_aggiorna_categoria_via_web_senza_checkbox_disattiva_usa_base_fissa(conn, client):
    """Una checkbox HTML non spunta non viene inviata affatto (non un
    valore "false"): l'assenza del campo deve comunque disattivare
    l'opzione, non lasciarla invariata."""
    db_social.crea_utente(conn, "admin-cat14@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-cat14@test.local")
    csrf = _csrf(client)
    categoria_id = db_social.crea_categoria(conn, "Con toggle da spegnere", "x")
    db_social.aggiorna_categoria(conn, categoria_id, base_fissa_path="/tmp/base.png",
                                 usa_base_fissa=True)

    r = client.post(f"/social/categorie/{categoria_id}", data={
        "prompt_ai": "x", "strategia_fatti": "libera", "csrf": csrf,
    }, follow_redirects=False)

    assert r.status_code == 303
    assert db_social.get_categoria(conn, categoria_id)["usa_base_fissa"] is False


def test_genera_base_categoria_mette_in_coda_un_job(conn, client):
    db_social.crea_utente(conn, "admin-cat15@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-cat15@test.local")
    csrf = _csrf(client)
    categoria_id = db_social.crea_categoria(conn, "Da generare", "x")

    r = client.post(f"/social/categorie/{categoria_id}/genera-base",
                    data={"csrf": csrf}, follow_redirects=False)

    assert r.status_code == 303
    assert db_social.job_in_corso(conn, "genera_base_fissa", categoria_id)


def test_genera_base_categoria_inesistente_404(conn, client):
    db_social.crea_utente(conn, "admin-cat16@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-cat16@test.local")
    csrf = _csrf(client)

    r = client.post("/social/categorie/non-esiste/genera-base",
                    data={"csrf": csrf}, follow_redirects=False)
    assert r.status_code == 404


def test_genera_base_categoria_richiede_admin(conn, client):
    db_social.crea_utente(conn, "editor-cat4@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    categoria_id = db_social.crea_categoria(conn, "Protetta 2", "x")
    _login(client, "editor-cat4@test.local")
    csrf = _csrf(client, "/social/contenuti/nuovo")

    r = client.post(f"/social/categorie/{categoria_id}/genera-base",
                    data={"csrf": csrf}, follow_redirects=False)
    assert r.status_code == 403


def test_anteprima_base_fissa_senza_immagine_404(conn, client):
    db_social.crea_utente(conn, "admin-cat17@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-cat17@test.local")
    categoria_id = db_social.crea_categoria(conn, "Senza base ancora", "x")

    r = client.get(f"/social/categorie/{categoria_id}/base-fissa")
    assert r.status_code == 404


def test_anteprima_base_fissa_serve_il_file(conn, client, tmp_path):
    db_social.crea_utente(conn, "admin-cat18@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-cat18@test.local")
    categoria_id = db_social.crea_categoria(conn, "Con base pronta", "x")
    from social import config
    cartella = config.asset_storage_path() / "basi_fisse"
    cartella.mkdir(parents=True, exist_ok=True)
    percorso = cartella / "base_test.png"
    percorso.write_bytes(_PNG_1X1)
    db_social.aggiorna_categoria(conn, categoria_id, base_fissa_path=str(percorso))

    r = client.get(f"/social/categorie/{categoria_id}/base-fissa")
    assert r.status_code == 200
    percorso.unlink(missing_ok=True)


def test_elimina_categoria_via_web(conn, client):
    db_social.crea_utente(conn, "admin-cat3@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-cat3@test.local")
    csrf = _csrf(client)
    categoria_id = db_social.crea_categoria(conn, "Da cancellare via web", "x")

    r = client.post(f"/social/categorie/{categoria_id}/elimina",
                    data={"csrf": csrf}, follow_redirects=False)

    assert r.status_code == 303
    assert db_social.get_categoria(conn, categoria_id) is None


def test_immagine_categoria_404_se_indice_fuori_range(conn, client):
    db_social.crea_utente(conn, "admin-cat4@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-cat4@test.local")
    categoria_id = db_social.crea_categoria(conn, "Senza immagine", "x")

    r = client.get(f"/social/categorie/{categoria_id}/immagine/0")
    assert r.status_code == 404


def test_nuovo_contenuto_elenca_le_categorie(conn, client):
    db_social.crea_utente(conn, "editor-cat2@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-cat2@test.local")
    db_social.crea_categoria(conn, "Categoria visibile nel form", "x")

    pagina = client.get("/social/contenuti/nuovo").text
    assert "Categoria visibile nel form" in pagina


def test_nuovo_contenuto_categorie_sono_javascript_valido_non_html_escaped(conn, client):
    """Bug reale riprodotto: l'autoescape di Jinja2 (attivo di default sui
    .html) trasformava le virgolette del JSON prodotto dal filtro tojson
    in '&#34;', rendendo il JavaScript dello stepper (CATEGORIE/PILLARS,
    vedi nuovo_contenuto.html) sintatticamente invalido — l'intero script
    si bloccava e la pagina del percorso guidato restava vuota (segnalato
    dall'utente). Deve restare vera virgoletta JS, mai l'entita' HTML."""
    db_social.crea_utente(conn, "editor-cat3@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-cat3@test.local")

    pagina = client.get("/social/contenuti/nuovo").text
    inizio = pagina.index('var CATEGORIE = [')
    fine = pagina.index('];', inizio)
    blocco_categorie = pagina[inizio:fine]
    assert '&#34;' not in blocco_categorie
    assert '{id: "' in blocco_categorie


def test_nuovo_contenuto_scelta_modalita_aggiorna_i_campi_visibili(conn, client):
    """Bug reale segnalato dall'utente: Concorsi -> Un concorso specifico ->
    Intento arrivava a "Dettagli" senza il campo "Bando specifico" (mostrava
    invece Titolo/Brief, come per la ricerca) -- il radio "modalita_ricerca"
    faceva avanzare lo step ma non richiamava aggiornaCategoria(), che
    decide quali campi mostrare in base alla modalita' scelta. Deve
    richiamarla ogni volta che cambia."""
    db_social.crea_utente(conn, "editor-cat4@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-cat4@test.local")

    pagina = client.get("/social/contenuti/nuovo").text
    assert 'value="specifico" style="width:auto" onchange="aggiornaCategoria(); alPassoSuccessivo()"' in pagina
    assert 'value="ricerca" style="width:auto" checked onchange="aggiornaCategoria(); alPassoSuccessivo()"' in pagina


def test_nuovo_contenuto_ha_un_tasto_avanti_persistente(conn, client):
    """Segnalato dall'utente: tornando indietro con "Indietro" non c'era
    modo di riprocedere senza cambiare la scelta gia' fatta (le carte
    avanzano solo al cambio di selezione). Un tasto "Continua" sempre
    presente risolve, a prescindere dallo step."""
    db_social.crea_utente(conn, "editor-cat5@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-cat5@test.local")

    pagina = client.get("/social/contenuti/nuovo").text
    assert 'id="btn-avanti"' in pagina
    assert 'onclick="alPassoSuccessivo()"' in pagina
