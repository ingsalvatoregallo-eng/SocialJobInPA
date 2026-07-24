"""OAuth account social (Instagram/LinkedIn): avvio, callback, stato
anti-CSRF, persistenza cifrata del token. Le chiamate HTTP reali ai
provider sono sostituite (monkeypatch su completa_oauth): qui si verifica
solo l'orchestrazione locale (stato firmato, salvataggio, audit, stato
account), non l'integrazione di rete verso Meta/LinkedIn."""

import re

import pytest

import auth
from social import db_social, security
from social.integrations.instagram import InstagramAdapter
from social.integrations.linkedin import LinkedInAdapter


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


@pytest.fixture
def admin(conn):
    return db_social.crea_utente(conn, "admin-oauth@test.local",
                                 auth.hash_password("Password123!"), ruolo="admin")


def _login(client, email="admin-oauth@test.local", password="Password123!"):
    client.post("/social/login", data={"email": email, "password": password})


def test_start_richiede_permesso_admin(client, conn, admin):
    db_social.crea_utente(conn, "editor-oauth@test.local",
                          auth.hash_password("Password123!"), ruolo="editor")
    _login(client, "editor-oauth@test.local", "Password123!")
    r = client.get("/social/oauth/linkedin/start", follow_redirects=False)
    assert r.status_code == 403


def test_start_provider_sconosciuto(client, admin):
    _login(client)
    r = client.get("/social/oauth/tiktok/start", follow_redirects=False)
    assert r.status_code == 404


def test_start_redirige_al_provider(client, conn, admin, monkeypatch):
    monkeypatch.setenv("LINKEDIN_CLIENT_ID", "client-id-test")
    monkeypatch.setenv("LINKEDIN_REDIRECT_URI", "http://localhost:8000/social/oauth/linkedin/callback")
    _login(client)
    r = client.get("/social/oauth/linkedin/start", follow_redirects=False)
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("https://www.linkedin.com/oauth/v2/authorization")
    assert "client_id=client-id-test" in location
    stato = re.search(r"state=([^&]+)", location).group(1)
    payload = auth.verifica_token(stato)
    assert payload["scopo"] == "social_link"
    assert payload["provider"] == "linkedin"


def test_callback_senza_code_o_state(client, conn):
    r = client.get("/social/oauth/linkedin/callback")
    assert r.status_code == 400


def test_callback_con_errore_dal_provider(client, conn):
    r = client.get("/social/oauth/instagram/callback", params={"error": "access_denied"})
    assert r.status_code == 400


def test_callback_stato_invalido(client, conn):
    r = client.get("/social/oauth/instagram/callback",
                   params={"code": "abc", "state": "non-e-un-token-valido"})
    assert r.status_code == 400


def test_callback_stato_provider_diverso_rifiutato(client, conn):
    stato = auth.crea_token({"scopo": "social_link", "provider": "linkedin", "utente_id": 1})
    r = client.get("/social/oauth/instagram/callback", params={"code": "abc", "state": stato})
    assert r.status_code == 400


def test_callback_linkedin_salva_token_cifrato_e_verifica(client, conn, admin, monkeypatch):
    monkeypatch.setattr(LinkedInAdapter, "completa_oauth", lambda self, code: ("tok-fake-123", 3600))
    monkeypatch.setattr(LinkedInAdapter, "verifica_privilegi_admin", lambda self: True)
    monkeypatch.setattr(LinkedInAdapter, "health_check",
                        lambda self: {"pronto": True, "checklist": [], "messaggio": "ok"})
    stato = auth.crea_token({"scopo": "social_link", "provider": "linkedin",
                             "utente_id": admin})
    r = client.get("/social/oauth/linkedin/callback",
                   params={"code": "codice-vero", "state": stato}, follow_redirects=False)
    assert r.status_code == 303
    account = db_social.account_per_piattaforma(conn, "linkedin")
    assert account["stato"] == "verificato"
    riga = db_social.oauth_token_attivo(conn, account["id"])
    assert riga["token_cifrato"] != "tok-fake-123"
    assert security.decrypt_token(riga["token_cifrato"]) == "tok-fake-123"


def test_callback_linkedin_non_admin_non_verifica(client, conn, admin, monkeypatch):
    monkeypatch.setattr(LinkedInAdapter, "completa_oauth", lambda self, code: ("tok", 3600))
    monkeypatch.setattr(LinkedInAdapter, "verifica_privilegi_admin", lambda self: False)
    monkeypatch.setattr(LinkedInAdapter, "health_check",
                        lambda self: {"pronto": True, "checklist": [], "messaggio": "ok"})
    stato = auth.crea_token({"scopo": "social_link", "provider": "linkedin", "utente_id": admin})
    client.get("/social/oauth/linkedin/callback", params={"code": "c", "state": stato})
    account = db_social.account_per_piattaforma(conn, "linkedin")
    assert account["stato"] == "in_configurazione"
    assert any(i["tipo"] == "publishing" for i in db_social.incidenti_aperti(conn))


def test_callback_instagram_mai_verificato_in_locale(client, conn, admin, monkeypatch):
    monkeypatch.setattr(InstagramAdapter, "completa_oauth", lambda self, code: "page-token-fake")
    stato = auth.crea_token({"scopo": "social_link", "provider": "instagram", "utente_id": admin})
    client.get("/social/oauth/instagram/callback", params={"code": "c", "state": stato})
    account = db_social.account_per_piattaforma(conn, "instagram")
    # mai "verificato" in locale: manca sempre l'URL pubblico per le immagini
    assert account["stato"] == "in_configurazione"
    riga = db_social.oauth_token_attivo(conn, account["id"])
    assert security.decrypt_token(riga["token_cifrato"]) == "page-token-fake"


def test_callback_errore_provider_registra_incidente_e_502(client, conn, admin, monkeypatch):
    def fallisce(self, code):
        raise RuntimeError("scambio code->token fallito")
    monkeypatch.setattr(LinkedInAdapter, "completa_oauth", fallisce)
    stato = auth.crea_token({"scopo": "social_link", "provider": "linkedin", "utente_id": admin})
    r = client.get("/social/oauth/linkedin/callback", params={"code": "c", "state": stato})
    assert r.status_code == 502
    assert any(i["tipo"] == "publishing" for i in db_social.incidenti_aperti(conn))
