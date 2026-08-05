"""Redesign della pagina di revisione (richiesto dall'utente): si deve
capire cosa ha verificato il Quality & Risk Agent (regole automatiche vs
giudizio AI, non piu' un'unica lista indistinta) e poter agire su ogni
dubbio dell'AI (citarlo nella richiesta di modifiche). In piu', per i
bandi/promozioni citati devono comparire sia il link JobInPA (fonte
primaria) sia quello ufficiale esterno (InPA/inpa.gov.it, gia' esposto
come url_dettaglio), con un badge che segnala che i dati sono stati letti
in diretta dall'API e non inseriti a mano."""

import json
import re

import auth
import pytest

from social import approvals, db_social, state_machine


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


def _content_in_attesa_approvazione(conn, titolo, **kwargs):
    content_id = db_social.crea_content(conn, titolo, **kwargs)
    for stato in ("RESEARCHING", "DRAFTING", "DRAFT_READY", "GENERATING_VISUAL",
                  "QUALITY_CHECK", "AWAITING_APPROVAL"):
        state_machine.transisci(conn, content_id, stato)
    approvals.richiedi_approvazione(conn, content_id)
    return content_id


def test_bandi_citati_mostrano_link_jobinpa_e_inpa_con_badge(conn, client):
    bando = {"id": "CONC-1", "titolo": "Concorso di prova", "url_jobinpa": "https://jobinpa.it/bandi/CONC-1",
             "url_dettaglio": "https://www.inpa.gov.it/dettaglio/CONC-1"}
    content_id = _content_in_attesa_approvazione(conn, "Concorso di prova")
    db_social.aggiorna_content(conn, content_id, bandi_trovati=json.dumps([bando]))
    db_social.crea_utente(conn, "revisore-ux1@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "revisore-ux1@test.local")

    pagina = client.get(f"/social/approvazioni?content_id={content_id}").text

    assert "https://jobinpa.it/bandi/CONC-1" in pagina
    assert "https://www.inpa.gov.it/dettaglio/CONC-1" in pagina
    assert "Verificato via API" in pagina


def test_bando_senza_url_jobinpa_usa_solo_url_dettaglio(conn, client):
    """Compatibilita' con bandi_trovati persistiti prima del campo
    url_jobinpa: non deve rompersi, mostra solo il link disponibile."""
    bando = {"id": "CONC-2", "titolo": "Concorso vecchio",
             "url_dettaglio": "https://www.inpa.gov.it/dettaglio/CONC-2"}
    content_id = _content_in_attesa_approvazione(conn, "Concorso vecchio")
    db_social.aggiorna_content(conn, content_id, bandi_trovati=json.dumps([bando]))
    db_social.crea_utente(conn, "revisore-ux2@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "revisore-ux2@test.local")

    pagina = client.get(f"/social/approvazioni?content_id={content_id}").text
    assert "https://www.inpa.gov.it/dettaglio/CONC-2" in pagina


def test_promozione_con_promo_dati_mostra_card_verificata(conn, client):
    promo = {"tipo": "piano", "chiave": "premium-promo", "nome": "Premium promo",
             "descrizione": "Accesso completo", "prezzo_eur": 9.99,
             "prezzo_promozionale_eur": 0.0, "scadenza": "2026-08-31",
             "url_jobinpa": "https://jobinpa.it/premium"}
    promozioni_id = next(c["id"] for c in db_social.lista_categorie(conn) if c["nome"] == "Promozioni")
    content_id = _content_in_attesa_approvazione(conn, "Premium promo", categoria_id=promozioni_id,
                                                 promo_dati=promo)
    db_social.crea_utente(conn, "revisore-ux3@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "revisore-ux3@test.local")

    pagina = client.get(f"/social/approvazioni?content_id={content_id}").text

    assert "Promozione citata" in pagina
    assert "Accesso completo" in pagina
    assert "https://jobinpa.it/premium" in pagina
    assert "Verificata via API JobInPA" in pagina


def test_promozione_senza_promo_dati_avvisa_di_verificare_a_mano(conn, client):
    promozioni_id = next(c["id"] for c in db_social.lista_categorie(conn) if c["nome"] == "Promozioni")
    content_id = _content_in_attesa_approvazione(conn, "Promo inserita a mano",
                                                 categoria_id=promozioni_id)
    db_social.crea_utente(conn, "revisore-ux4@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "revisore-ux4@test.local")

    pagina = client.get(f"/social/approvazioni?content_id={content_id}").text
    assert "creato senza lettura automatica da JobInPA" in pagina


def test_motivi_regole_e_ai_mostrati_separatamente(conn, client):
    content_id = _content_in_attesa_approvazione(conn, "Da rivedere")
    db_social.aggiorna_content(conn, content_id, punteggi_rischio=json.dumps({
        "classe_regole": "giallo", "motivi_regole": ["Contiene una parola sensibile"],
        "classe_ai": "rosso", "motivi_ai": ["Il tono sembra promettere un risultato certo"],
        "accuratezza": 0.7, "brand": 0.8, "conformita": 0.6}))
    db_social.crea_utente(conn, "revisore-ux5@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "revisore-ux5@test.local")

    pagina = client.get(f"/social/approvazioni?content_id={content_id}").text

    assert "Regole automatiche" in pagina
    assert "Contiene una parola sensibile" in pagina
    assert "Giudizio AI" in pagina or "🤖" in pagina
    assert "Il tono sembra promettere un risultato certo" in pagina
    # Solo i dubbi AI hanno il pulsante "cita" (le regole sono oggettive,
    # non ha senso "citarle" come se fossero un'opinione da discutere)
    assert "citaDubbio" in pagina


def test_fatto_in_conflitto_ha_pulsante_cita(conn, client):
    content_id = _content_in_attesa_approvazione(conn, "Con fatto in conflitto")
    db_social.salva_fatto(conn, "Le fonti non concordano sul numero di posti",
                          content_id=content_id, confidenza=0.4, conflitto=True)
    db_social.crea_utente(conn, "revisore-ux6@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "revisore-ux6@test.local")

    pagina = client.get(f"/social/approvazioni?content_id={content_id}").text
    assert "Le fonti non concordano sul numero di posti" in pagina
    assert "citaDubbio(" in pagina


def test_pagina_approvazioni_senza_permesso_approve_non_mostra_pulsante_cita(conn, client):
    content_id = _content_in_attesa_approvazione(conn, "Solo lettura")
    db_social.aggiorna_content(conn, content_id, punteggi_rischio=json.dumps({
        "classe_regole": "verde", "motivi_regole": [],
        "classe_ai": "giallo", "motivi_ai": ["dubbio AI"],
        "accuratezza": 0.9, "brand": 0.9, "conformita": 0.9}))
    db_social.crea_utente(conn, "viewer-ux@test.local",
                          auth.hash_password("Password123!"), ruolo="viewer")
    _login(client, "viewer-ux@test.local")

    pagina = client.get(f"/social/approvazioni?content_id={content_id}").text
    assert "dubbio AI" in pagina
    assert "onclick=\"citaDubbio(" not in pagina


# --- Addestrare il reviewer: confermare/rifiutare un dubbio AI diventa ------
# una regola globale (vedi db_social.social_review_rules, agents.
# quality_risk): segnalato dall'utente, vuole poter approvare/rifiutare il
# singolo alert cosi' che continui/smetta di bloccare in futuro.

def _content_con_dubbio_ai(conn, titolo, motivo="Il tono sembra promettere un risultato certo"):
    content_id = _content_in_attesa_approvazione(conn, titolo)
    db_social.aggiorna_content(conn, content_id, punteggi_rischio=json.dumps({
        "classe_regole": "verde", "motivi_regole": [],
        "classe_ai": "giallo", "motivi_ai": [motivo],
        "accuratezza": 0.8, "brand": 0.8, "conformita": 0.8}))
    return content_id


def test_dubbio_ai_ha_pulsanti_conferma_e_rifiuta(conn, client):
    content_id = _content_con_dubbio_ai(conn, "Con dubbio da addestrare")
    db_social.crea_utente(conn, "revisore-regole1@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "revisore-regole1@test.local")

    pagina = client.get(f"/social/approvazioni?content_id={content_id}").text
    assert f"/social/approvazioni/{content_id}/dubbio/conferma" in pagina
    assert f"/social/approvazioni/{content_id}/dubbio/rifiuta" in pagina


def test_pagina_approvazioni_senza_permesso_approve_non_mostra_conferma_rifiuta(conn, client):
    content_id = _content_con_dubbio_ai(conn, "Solo lettura 2")
    db_social.crea_utente(conn, "viewer-regole@test.local",
                          auth.hash_password("Password123!"), ruolo="viewer")
    _login(client, "viewer-regole@test.local")

    pagina = client.get(f"/social/approvazioni?content_id={content_id}").text
    assert "/dubbio/conferma" not in pagina
    assert "/dubbio/rifiuta" not in pagina


def test_route_conferma_alert_reviewer_crea_regola_vincolo(conn, client):
    content_id = _content_con_dubbio_ai(conn, "Da confermare")
    db_social.crea_utente(conn, "revisore-regole2@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "revisore-regole2@test.local")
    csrf = re.search(r'name="csrf" value="([0-9a-f]+)"',
                     client.get(f"/social/approvazioni?content_id={content_id}").text).group(1)

    r = client.post(f"/social/approvazioni/{content_id}/dubbio/conferma",
                    data={"testo": "Il tono sembra promettere un risultato certo", "csrf": csrf},
                    follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == f"/social/approvazioni?content_id={content_id}"
    regole = db_social.lista_regole_revisione(conn)
    assert len(regole) == 1
    assert regole[0]["tipo"] == "vincolo"
    assert regole[0]["stato"] == "attiva"
    assert regole[0]["testo"] == "Il tono sembra promettere un risultato certo"
    assert regole[0]["origine_content_id"] == content_id


def test_route_rifiuta_alert_reviewer_crea_regola_esenzione(conn, client):
    content_id = _content_con_dubbio_ai(conn, "Da rifiutare")
    db_social.crea_utente(conn, "revisore-regole3@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "revisore-regole3@test.local")
    csrf = re.search(r'name="csrf" value="([0-9a-f]+)"',
                     client.get(f"/social/approvazioni?content_id={content_id}").text).group(1)

    r = client.post(f"/social/approvazioni/{content_id}/dubbio/rifiuta",
                    data={"testo": "Falso positivo", "csrf": csrf}, follow_redirects=False)

    assert r.status_code == 303
    regole = db_social.lista_regole_revisione(conn)
    assert regole[0]["tipo"] == "esenzione"


def test_route_conferma_alert_reviewer_da_blocked_torna_a_pagina_contenuto(conn, client):
    content_id = db_social.crea_content(conn, "Bloccato con dubbio")
    for stato in ("RESEARCHING", "DRAFTING", "DRAFT_READY", "GENERATING_VISUAL",
                  "QUALITY_CHECK", "BLOCKED"):
        state_machine.transisci(conn, content_id, stato)
    db_social.aggiorna_content(conn, content_id, punteggi_rischio=json.dumps({
        "classe_regole": "verde", "motivi_regole": [], "classe_ai": "rosso",
        "motivi_ai": ["Motivo bloccante"], "accuratezza": 0.5, "brand": 0.5, "conformita": 0.5}))
    db_social.crea_utente(conn, "revisore-regole4@test.local",
                          auth.hash_password("Password123!"), ruolo="admin")
    _login(client, "revisore-regole4@test.local")
    csrf = re.search(r'name="csrf" value="([0-9a-f]+)"',
                     client.get("/social/contenuti/nuovo").text).group(1)

    r = client.post(f"/social/approvazioni/{content_id}/dubbio/conferma",
                    data={"testo": "Motivo bloccante", "csrf": csrf}, follow_redirects=False)

    assert r.headers["location"] == f"/social/contenuti/{content_id}"


def test_route_conferma_alert_reviewer_richiede_permesso_approve(conn, client):
    content_id = _content_con_dubbio_ai(conn, "Senza permesso")
    db_social.crea_utente(conn, "editor-regole@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-regole@test.local")
    csrf = re.search(r'name="csrf" value="([0-9a-f]+)"',
                     client.get("/social/contenuti/nuovo").text).group(1)

    r = client.post(f"/social/approvazioni/{content_id}/dubbio/conferma",
                    data={"testo": "qualsiasi", "csrf": csrf}, follow_redirects=False)

    assert r.status_code == 403
    assert db_social.lista_regole_revisione(conn) == []
