import json

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
