"""Integrazione API/dashboard: RBAC, CSRF, kill switch, accesso non autorizzato."""

import pytest

import auth
from social import db_social


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
    # niente context manager: il lifespan userebbe il DB di default
    with TestClient(fastapi_app) as client:
        yield client
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def utenti(conn):
    creati = {}
    for ruolo in db_social.RUOLI:
        utente_id = db_social.crea_utente(conn, f"{ruolo}@test.local",
                                          auth.hash_password("Password123!"), ruolo=ruolo)
        creati[ruolo] = utente_id
    return creati


def _bearer(utente_id):
    token = auth.crea_token({"utente_id": utente_id, "scope": "session"})
    return {"Authorization": f"Bearer {token}"}


def test_api_senza_token_401(client):
    assert client.get("/api/v1/social/content").status_code == 401


def test_token_manomesso_401(client, utenti):
    token = auth.crea_token({"utente_id": utenti["admin"], "scope": "session"})
    r = client.get("/api/v1/social/content",
                   headers={"Authorization": f"Bearer {token}x"})
    assert r.status_code == 401


def test_viewer_legge_ma_non_scrive(client, utenti):
    ok = client.get("/api/v1/social/content", headers=_bearer(utenti["viewer"]))
    assert ok.status_code == 200
    negato = client.post("/api/v1/social/content", headers=_bearer(utenti["viewer"]),
                         json={"titolo": "Nuova idea social"})
    assert negato.status_code == 403


def test_editor_crea_contenuto_e_avvia_pipeline(client, utenti):
    r = client.post("/api/v1/social/content", headers=_bearer(utenti["editor"]),
                    json={"titolo": "Idea creata via API", "pillar": "guida"})
    assert r.status_code == 201
    content_id = r.json()["id"]
    dettaglio = client.get(f"/api/v1/social/content/{content_id}",
                           headers=_bearer(utenti["viewer"]))
    assert dettaglio.status_code == 200
    assert dettaglio.json()["content"]["stato"] == "IDEA"
    pipeline = client.post(f"/api/v1/social/content/{content_id}/pipeline",
                           headers=_bearer(utenti["editor"]))
    assert pipeline.status_code == 200 and pipeline.json()["job_id"]


def test_canale_non_valido_422(client, utenti):
    r = client.post("/api/v1/social/content", headers=_bearer(utenti["editor"]),
                    json={"titolo": "Idea", "canali": ["tiktok"]})
    assert r.status_code == 422


def test_editor_non_puo_eliminare_contenuto(client, utenti, conn):
    content_id = db_social.crea_content(conn, "Da non eliminare")
    r = client.delete(f"/api/v1/social/content/{content_id}",
                      headers=_bearer(utenti["editor"]))
    assert r.status_code == 403
    assert db_social.get_content(conn, content_id) is not None


def test_admin_elimina_contenuto(client, utenti, conn):
    content_id = db_social.crea_content(conn, "Da eliminare")
    r = client.delete(f"/api/v1/social/content/{content_id}",
                      headers=_bearer(utenti["admin"]))
    assert r.status_code == 204
    assert db_social.get_content(conn, content_id) is None


def test_elimina_contenuto_inesistente_404(client, utenti):
    r = client.delete("/api/v1/social/content/id-inesistente",
                      headers=_bearer(utenti["admin"]))
    assert r.status_code == 404


def test_kill_switch_richiede_social_publish(client, utenti):
    negato = client.post("/api/v1/social/system/kill-switch",
                         headers=_bearer(utenti["editor"]), json={"attivo": True})
    assert negato.status_code == 403
    ok = client.post("/api/v1/social/system/kill-switch",
                     headers=_bearer(utenti["admin"]), json={"attivo": True})
    assert ok.status_code == 200 and ok.json()["kill_switch"] is True
    stato = client.get("/api/v1/social/system/status", headers=_bearer(utenti["viewer"]))
    assert stato.json()["kill_switch"] is True


def test_status_espone_checklist(client, utenti):
    stato = client.get("/api/v1/social/system/status",
                       headers=_bearer(utenti["viewer"])).json()
    assert stato["instagram"]["pronto"] is False
    assert any("App Instagram" in v["voce"] for v in stato["instagram"]["checklist"])
    assert stato["modalita"] == "mock"


def test_settings_solo_admin_e_chiavi_note(client, utenti):
    negato = client.get("/api/v1/social/settings", headers=_bearer(utenti["editor"]))
    assert negato.status_code == 403
    ok = client.get("/api/v1/social/settings", headers=_bearer(utenti["admin"]))
    assert ok.status_code == 200 and "kill_switch" in ok.json()
    invalida = client.post("/api/v1/social/settings", headers=_bearer(utenti["admin"]),
                           json={"chiave": "chiave_inventata", "valore": 1})
    assert invalida.status_code == 422


def test_fonti_whitelist_via_api(client, utenti):
    r = client.post("/api/v1/social/sources", headers=_bearer(utenti["admin"]),
                    json={"dominio": "www.miur.gov.it", "nome": "MIUR"})
    assert r.status_code == 201
    fonti = client.get("/api/v1/social/sources", headers=_bearer(utenti["viewer"])).json()
    assert any(f["dominio"] == "www.miur.gov.it" for f in fonti)


# --- Dashboard ---------------------------------------------------------------

def test_dashboard_redirige_al_login(client):
    r = client.get("/social/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/social/login"


def test_login_dashboard_e_sessione(client, utenti):
    r = client.post("/social/login",
                    data={"email": "admin@test.local", "password": "Password123!"},
                    follow_redirects=False)
    assert r.status_code == 303
    home = client.get("/social/")
    assert home.status_code == 200
    assert "Kill switch" in home.text


def test_login_credenziali_errate(client, utenti):
    r = client.post("/social/login",
                    data={"email": "admin@test.local", "password": "sbagliata"})
    assert r.status_code == 401


def test_login_email_sconosciuta(client, utenti):
    r = client.post("/social/login",
                    data={"email": "nessuno@test.local", "password": "Password123!"})
    assert r.status_code == 401


def test_post_dashboard_senza_csrf_rifiutato(client, utenti):
    client.post("/social/login",
                data={"email": "admin@test.local", "password": "Password123!"})
    r = client.post("/social/kill-switch", data={"attivo": "1"},
                    follow_redirects=False)
    assert r.status_code == 403
    # col token giusto invece passa
    import re as re_mod
    home = client.get("/social/").text
    csrf = re_mod.search(r'name="csrf" value="([0-9a-f]+)"', home).group(1)
    ok = client.post("/social/kill-switch", data={"attivo": "1", "csrf": csrf},
                     follow_redirects=False)
    assert ok.status_code == 303


def test_viewer_non_vede_impostazioni(client, utenti):
    client.post("/social/login",
                data={"email": "viewer@test.local", "password": "Password123!"})
    r = client.get("/social/impostazioni")
    assert r.status_code == 403
