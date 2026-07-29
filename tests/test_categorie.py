"""Categorie personalizzate (menu Categorie, richiesto dall'utente): un
prompt immagine — e opzionalmente un'immagine di riferimento che guida
davvero OpenAI (/v1/images/edits) — configurabile per QUALSIASI tipologia
di contenuto, non solo 'promozione' come nella prima versione. La
categoria 'Promozione' e' seminata di default (vedi db_social._migra) e
scelta automaticamente per la tipologia 'promozione' quando non se ne
sceglie esplicitamente un'altra."""

import re

import auth
import pytest

from social import agents, db_social, llm, models
from social.images import MockImageProvider


# --- db_social: CRUD -----------------------------------------------------

def test_categoria_promozione_seminata_di_default(conn):
    """init_social_db (chiamato dalla fixture conn) gira _migra: la
    categoria 'Promozione' deve esistere sempre, senza doverla creare a
    mano — prima viveva nel setting prompt_templates_immagine."""
    nomi = [c["nome"] for c in db_social.lista_categorie(conn)]
    assert "Promozione" in nomi


def test_crea_categoria_e_recupera(conn):
    categoria_id = db_social.crea_categoria(conn, "Guida rapida", "Illustrazione di {TITOLO}")
    categoria = db_social.get_categoria(conn, categoria_id)
    assert categoria["nome"] == "Guida rapida"
    assert categoria["prompt_ai"] == "Illustrazione di {TITOLO}"
    assert categoria["immagine_riferimento_path"] is None


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


def test_visual_promozione_senza_categoria_esplicita_usa_promozione_di_default(conn):
    db_social.aggiorna_categoria(
        conn, next(c["id"] for c in db_social.lista_categorie(conn) if c["nome"] == "Promozione"),
        prompt_ai="Regalo per {TITOLO}.")
    catturate = _content_con_richiesta_catturata(
        conn, titolo="Premium gratis", tipologia="promozione")
    assert catturate[0].prompt_ai == "Regalo per Premium gratis."


def test_visual_promozione_senza_categoria_promozione_non_sovrascrive_nulla(conn):
    """Se la categoria 'Promozione' non esiste (env ripulito a mano), il
    prompt_ai resta quello del Visual Agent: mai un errore, mai una stringa
    vuota al posto del giudizio dell'AI."""
    promo_id = next(c["id"] for c in db_social.lista_categorie(conn) if c["nome"] == "Promozione")
    db_social.elimina_categoria(conn, promo_id)
    catturate = _content_con_richiesta_catturata(
        conn, titolo="Premium gratis", tipologia="promozione")
    assert catturate[0].prompt_ai is None  # quello (assente) del MockLLMProvider demo


def test_visual_concorso_senza_categoria_non_viene_toccato(conn):
    catturate = _content_con_richiesta_catturata(conn, titolo="Concorso normale")
    assert catturate[0].prompt_ai is None


def test_visual_passa_immagine_di_riferimento_alla_richiesta(conn):
    categoria_id = db_social.crea_categoria(
        conn, "Con riferimento", "prompt qualsiasi",
        immagine_riferimento_path="/percorso/finto/esempio.png")
    catturate = _content_con_richiesta_catturata(
        conn, titolo="Tema", tipologia="generico", categoria_id=categoria_id)
    assert catturate[0].immagine_riferimento == "/percorso/finto/esempio.png"


def test_visual_senza_categoria_non_passa_immagine_di_riferimento(conn):
    catturate = _content_con_richiesta_catturata(conn, titolo="Tema")
    assert catturate[0].immagine_riferimento is None


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
        "nome": "Categoria via web", "prompt_ai": "Illustrazione di {TITOLO}", "csrf": csrf,
    }, follow_redirects=False)

    assert r.status_code == 303
    nomi = [c["nome"] for c in db_social.lista_categorie(conn)]
    assert "Categoria via web" in nomi


def test_crea_categoria_con_immagine_di_riferimento_via_web(conn, client):
    """L'immagine caricata deve essere salvata su disco e collegata alla
    categoria, poi servita da /social/categorie/{id}/immagine (stesso
    pattern di autenticazione di /social/asset/{id})."""
    db_social.crea_utente(conn, "admin-cat5@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-cat5@test.local")
    csrf = _csrf(client)
    png_1x1 = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
        "53de0000000c4944415408d763f8cfc0c00000030001a1b7b6210000000049454e44ae426082")

    r = client.post("/social/categorie",
                    data={"nome": "Con immagine", "prompt_ai": "x", "csrf": csrf},
                    files={"immagine": ("riferimento.png", png_1x1, "image/png")},
                    follow_redirects=False)

    assert r.status_code == 303
    categoria = next(c for c in db_social.lista_categorie(conn) if c["nome"] == "Con immagine")
    assert categoria["immagine_riferimento_path"]

    risposta_immagine = client.get(f"/social/categorie/{categoria['id']}/immagine")
    assert risposta_immagine.status_code == 200
    assert risposta_immagine.content == png_1x1


def test_crea_categoria_nome_duplicato_via_web_400(conn, client):
    db_social.crea_utente(conn, "admin-cat2@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-cat2@test.local")
    csrf = _csrf(client)
    client.post("/social/categorie",
                data={"nome": "Doppione", "prompt_ai": "x", "csrf": csrf})

    r = client.post("/social/categorie",
                    data={"nome": "Doppione", "prompt_ai": "y", "csrf": csrf})
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


def test_immagine_categoria_404_se_assente(conn, client):
    db_social.crea_utente(conn, "admin-cat4@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "admin-cat4@test.local")
    categoria_id = db_social.crea_categoria(conn, "Senza immagine", "x")

    r = client.get(f"/social/categorie/{categoria_id}/immagine")
    assert r.status_code == 404


def test_nuovo_contenuto_elenca_le_categorie(conn, client):
    db_social.crea_utente(conn, "editor-cat2@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-cat2@test.local")
    db_social.crea_categoria(conn, "Categoria visibile nel form", "x")

    pagina = client.get("/social/contenuti/nuovo").text
    assert "Categoria visibile nel form" in pagina
