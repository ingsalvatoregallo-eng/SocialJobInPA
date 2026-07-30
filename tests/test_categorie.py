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
