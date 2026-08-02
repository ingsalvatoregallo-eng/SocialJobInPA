import json
import sqlite3

import pytest

from social import db_social, security


def test_settings_roundtrip(conn):
    assert db_social.get_setting(conn, "kill_switch") is False
    db_social.set_setting(conn, "kill_switch", True)
    assert db_social.kill_switch_attivo(conn) is True


def test_init_idempotente(conn):
    db_social.init_social_db(conn)
    db_social.init_social_db(conn)
    domini = db_social.source_domains(conn)
    assert len({d["dominio"] for d in domini}) == len(domini)


def test_matrice_permessi_per_ruolo(conn):
    assert set(db_social.RUOLI) == {"admin", "editor", "reviewer", "viewer"}
    assert "social.approve" in db_social.permessi_di_ruolo(conn, "reviewer")
    assert "social.view" in db_social.permessi_di_ruolo(conn, "viewer")
    assert "social.admin" in db_social.permessi_di_ruolo(conn, "admin")
    assert "social.approve" not in db_social.permessi_di_ruolo(conn, "editor")
    assert db_social.permessi_di_ruolo(conn, "sconosciuto") == ()


def test_crea_e_recupera_utente(conn):
    utente_id = db_social.crea_utente(conn, "Prova@Esempio.it", "hash-fittizio",
                                      nome="Ada", ruolo="editor")
    assert db_social.utente_per_id(conn, utente_id)["email"] == "prova@esempio.it"
    assert db_social.utente_per_email(conn, "PROVA@esempio.it")["ruolo"] == "editor"
    assert db_social.utente_per_email(conn, "assente@esempio.it") is None


def test_crea_utente_ruolo_non_valido(conn):
    import pytest
    with pytest.raises(ValueError):
        db_social.crea_utente(conn, "x@esempio.it", "hash", ruolo="superadmin")


def test_aggiorna_ruolo_utente(conn):
    utente_id = db_social.crea_utente(conn, "x@esempio.it", "hash", ruolo="viewer")
    db_social.aggiorna_ruolo_utente(conn, utente_id, "admin")
    assert db_social.utente_per_id(conn, utente_id)["ruolo"] == "admin"


def test_whitelist_domini_include_sottodomini(conn):
    assert db_social.source_domain_allowed(conn, "www.inpa.gov.it")
    assert db_social.source_domain_allowed(conn, "jobinpa.it")
    assert db_social.source_domain_allowed(conn, "blog.jobinpa.it")
    assert not db_social.source_domain_allowed(conn, "evil-jobinpa.it")
    assert not db_social.source_domain_allowed(conn, "aggregatore-random.com")


def test_publication_unica_per_contenuto_e_piattaforma(conn):
    content_id = db_social.crea_content(conn, "Test")
    pub1 = db_social.apri_publication(conn, content_id, "linkedin", "mock")
    assert pub1 is not None
    # secondo tentativo mentre e' in corso: rifiutato (idempotenza)
    assert db_social.apri_publication(conn, content_id, "linkedin", "mock") is None
    db_social.chiudi_publication(conn, pub1, esito="ok", remote_id="x")
    # gia' pubblicata: ancora rifiutato
    assert db_social.apri_publication(conn, content_id, "linkedin", "mock") is None


def test_publication_fallita_e_ritentabile(conn):
    content_id = db_social.crea_content(conn, "Test")
    pub1 = db_social.apri_publication(conn, content_id, "instagram", "mock")
    db_social.chiudi_publication(conn, pub1, esito="errore", errore="rete")
    pub2 = db_social.apri_publication(conn, content_id, "instagram", "mock")
    assert pub2 == pub1  # stessa riga riaperta, mai una seconda pubblicazione


def test_oauth_token_mai_in_chiaro_e_revoca(conn):
    account = db_social.account_per_piattaforma(conn, "linkedin")
    cifrato = security.encrypt_token("token-in-chiaro")
    db_social.salva_oauth_token(conn, account["id"], "access", cifrato)
    riga = db_social.oauth_token_attivo(conn, account["id"])
    assert riga["token_cifrato"] != "token-in-chiaro"
    assert security.decrypt_token(riga["token_cifrato"]) == "token-in-chiaro"
    # un nuovo token revoca il precedente
    db_social.salva_oauth_token(conn, account["id"], "access",
                                security.encrypt_token("token-2"))
    attivi = conn.execute(
        "SELECT COUNT(*) FROM social_oauth_tokens WHERE account_id = ? "
        "AND revocato_at IS NULL", (account["id"],)).fetchone()[0]
    assert attivi == 1
    db_social.revoca_oauth_tokens(conn, account["id"])
    assert db_social.oauth_token_attivo(conn, account["id"]) is None


def test_audit_scarta_chiavi_sensibili(conn):
    db_social.audit(conn, "test", dettagli={
        "campo_ok": "valore", "password": "segretissima",
        "access_token": "tok", "api_key_meta": "chiave"})
    riga = db_social.audit_recenti(conn, limit=1)[0]
    dettagli = json.loads(riga["dettagli"])
    assert dettagli == {"campo_ok": "valore"}


def test_job_lock_e_backoff(conn):
    job_id = db_social.crea_job(conn, "publish", {"content_id": "x"}, max_tentativi=3)
    job = db_social.prendi_job(conn, "worker-a")
    assert job["id"] == job_id and job["stato"] == "running"
    # un secondo worker non puo' prenderlo finche' il lock e' fresco
    assert db_social.prendi_job(conn, "worker-b") is None
    # errore -> torna pending con esecuzione futura (backoff)
    db_social.chiudi_job(conn, job_id, "errore", errore="boom")
    riga = conn.execute("SELECT * FROM social_scheduled_jobs WHERE id = ?",
                        (job_id,)).fetchone()
    assert riga["stato"] == "pending"
    assert riga["esegui_at"] > db_social._adesso()
    assert riga["ultimo_errore"] == "boom"


def test_job_dead_letter_dopo_max_tentativi(conn):
    job_id = db_social.crea_job(conn, "publish", {}, max_tentativi=1)
    job = db_social.prendi_job(conn, "worker-a")
    db_social.chiudi_job(conn, job_id, "errore", errore="fatale")
    riga = conn.execute("SELECT stato FROM social_scheduled_jobs WHERE id = ?",
                        (job_id,)).fetchone()
    assert riga["stato"] == "dead"


def test_job_recovery_lock_scaduto(conn):
    job_id = db_social.crea_job(conn, "publish", {})
    db_social.prendi_job(conn, "worker-morto")
    # simula un worker morto: lock piu' vecchio del timeout
    conn.execute("UPDATE social_scheduled_jobs SET lock_at = '2000-01-01T00:00:00' "
                 "WHERE id = ?", (job_id,))
    conn.commit()
    recuperato = db_social.prendi_job(conn, "worker-nuovo")
    assert recuperato is not None and recuperato["id"] == job_id


def test_costo_periodo(conn):
    db_social.registra_costo(conn, "anthropic", 1.5, modello="m")
    db_social.registra_costo(conn, "anthropic", 0.5, modello="m")
    db_social.registra_costo(conn, "openai_images", 0.04)
    assert db_social.costo_periodo(conn, "anthropic") == 2.0
    assert db_social.costo_periodo(conn, "openai_images") == 0.04


def test_get_asset_e_aggiorna_asset(conn):
    content_id = db_social.crea_content(conn, "Test asset")
    asset_id = db_social.salva_asset(conn, content_id, "/vecchio.png", piattaforma="instagram",
                                     template="nuovo_concorso", formato="instagram_feed",
                                     provider="mock", bando_id="CONC-1")
    db_social.aggiorna_asset(conn, asset_id, percorso="/nuovo.png", provider="openai_images",
                             url_pubblico="https://cdn.example/nuovo.png")
    riga = db_social.get_asset(conn, content_id, asset_id)
    assert riga["percorso"] == "/nuovo.png"
    assert riga["provider"] == "openai_images"
    assert riga["url_pubblico"] == "https://cdn.example/nuovo.png"
    # Campi non passati restano invariati (stesso principio di aggiorna_content).
    assert riga["template"] == "nuovo_concorso"
    assert riga["bando_id"] == "CONC-1"


def test_get_asset_di_altro_contenuto_ritorna_none(conn):
    content_a = db_social.crea_content(conn, "A")
    content_b = db_social.crea_content(conn, "B")
    asset_id = db_social.salva_asset(conn, content_a, "/x.png")
    assert db_social.get_asset(conn, content_b, asset_id) is None


# --- Retry su "database is locked" -------------------------------------------
# Segnalato dall'utente: salvare una categoria dava 500 Internal Server
# Error con "database is locked" nei log — sotto scritture concorrenti da
# piu' processi (app/worker/scheduler sullo stesso file SQLite) il timeout
# passato a sqlite3.connect() non basta sempre ad assorbire la contesa.

class _ConnessioneFinta:
    """Simula N fallimenti con "database is locked" prima di riuscire (o
    esaurire i tentativi): non serve un vero secondo scrittore concorrente
    per testare la logica di retry, solo l'eccezione che produce."""

    def __init__(self, fallimenti):
        self.fallimenti = fallimenti
        self.chiamate = 0
        self.commit_chiamato = False

    def execute(self, sql, parametri):
        self.chiamate += 1
        if self.chiamate <= self.fallimenti:
            raise sqlite3.OperationalError("database is locked")

    def commit(self):
        self.commit_chiamato = True


def test_esegui_scrittura_con_retry_riesce_dopo_alcuni_fallimenti(monkeypatch):
    monkeypatch.setattr(db_social.time, "sleep", lambda s: None)
    finta = _ConnessioneFinta(fallimenti=2)
    db_social._esegui_scrittura_con_retry(finta, "UPDATE x SET y = ?", (1,))
    assert finta.chiamate == 3  # 2 falliti + 1 riuscito
    assert finta.commit_chiamato


def test_esegui_scrittura_con_retry_rilancia_dopo_troppi_fallimenti(monkeypatch):
    monkeypatch.setattr(db_social.time, "sleep", lambda s: None)
    finta = _ConnessioneFinta(fallimenti=99)
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        db_social._esegui_scrittura_con_retry(finta, "UPDATE x SET y = ?", (1,), tentativi=3)
    assert finta.chiamate == 3
    assert not finta.commit_chiamato


def test_esegui_scrittura_con_retry_non_riprova_altri_errori(monkeypatch):
    """Solo "database is locked" viene riprovato: un altro OperationalError
    (es. una colonna sbagliata) deve fallire subito, non nascondersi
    dietro 5 tentativi identici inutili."""
    class _ConnessioneAltroErrore:
        def execute(self, sql, parametri):
            raise sqlite3.OperationalError("no such column: x")

        def commit(self):
            pass

    with pytest.raises(sqlite3.OperationalError, match="no such column"):
        db_social._esegui_scrittura_con_retry(_ConnessioneAltroErrore(), "UPDATE x SET y = ?", (1,))


def test_aggiorna_categoria_passa_dal_percorso_con_retry(conn, monkeypatch):
    """Verifica che aggiorna_categoria deleghi davvero a
    _esegui_scrittura_con_retry (non un conn.execute/commit diretto che
    aggirerebbe il retry) — il comportamento di recupero da "database is
    locked" e' gia' testato a fondo sopra, isolato dalla vera connessione
    sqlite3 (un tipo C immutabile, non intercettabile a livello di classe
    dai test)."""
    categoria_id = db_social.crea_categoria(conn, "Categoria di prova", "prompt")
    chiamate = []
    originale = db_social._esegui_scrittura_con_retry

    def spia(connessione, sql, parametri, **kwargs):
        chiamate.append(sql)
        return originale(connessione, sql, parametri, **kwargs)

    monkeypatch.setattr(db_social, "_esegui_scrittura_con_retry", spia)
    db_social.aggiorna_categoria(conn, categoria_id, prompt_ai="prompt aggiornato")
    assert any("UPDATE social_content_categories" in sql for sql in chiamate)
    assert db_social.get_categoria(conn, categoria_id)["prompt_ai"] == "prompt aggiornato"
