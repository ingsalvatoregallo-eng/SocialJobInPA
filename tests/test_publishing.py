import json

import pytest

from social import agents, db_social, llm, publishing
from social.images import MockImageProvider
from social.integrations.base import MockAdapter


def _contenuto_pronto(conn):
    """Pipeline mock completa: torna un contenuto APPROVED/SCHEDULED verde."""
    content_id = db_social.crea_content(conn, "Contenuto di prova")
    agents.esegui_pipeline(conn, content_id, provider=llm.MockLLMProvider(conn),
                           image_provider=MockImageProvider())
    return content_id


def test_flusso_mock_pubblica_su_entrambe_le_piattaforme(conn):
    content_id = _contenuto_pronto(conn)
    esiti = publishing.pubblica_contenuto(conn, content_id)
    assert esiti == {"instagram": "pubblicato", "linkedin": "pubblicato"}
    content = db_social.get_content(conn, content_id)
    assert content["stato"] == "PUBLISHED"
    pubs = db_social.publications_di(conn, content_id)
    assert all(p["modalita"] == "mock" and p["remote_id"].startswith("mock-")
               for p in pubs)


def test_pubblica_contenuto_passa_tutti_gli_asset_del_carosello(conn, monkeypatch):
    """Regressione: asset_per_piattaforma era costruito come dict
    {piattaforma: ultimo asset}, quindi un contenuto con piu' immagini
    Instagram (carosello) ne perdeva tutte tranne l'ultima al momento di
    passarle all'adapter per la pubblicazione."""
    content_id = _contenuto_pronto(conn)
    percorsi_aggiunti = [f"/tmp/carosello-{i}.png" for i in range(3)]
    for percorso in percorsi_aggiunti:
        db_social.salva_asset(conn, content_id, percorso, piattaforma="instagram",
                              template="nuovo_concorso", formato="instagram_feed")

    adapter_instagram = MockAdapter("instagram")
    originale = publishing.adapter_per

    def adapter_per_finto(conn, piattaforma, forza_mock=False):
        if piattaforma == "instagram":
            return adapter_instagram
        return originale(conn, piattaforma, forza_mock=forza_mock)

    monkeypatch.setattr(publishing, "adapter_per", adapter_per_finto)
    publishing.pubblica_contenuto(conn, content_id)
    asset_passati = adapter_instagram.pubblicati[0]["asset"]
    assert all(p in asset_passati for p in percorsi_aggiunti)


def test_pubblica_usa_url_pubblico_per_instagram_e_percorso_per_linkedin(conn, monkeypatch):
    """Instagram accetta solo un image_url raggiungibile da Internet (mai i
    byte diretti come LinkedIn): quando l'asset ha un url_pubblico (caricato
    su R2, vedi asset_storage.py) va usato quello, non il percorso locale.
    LinkedIn invece deve continuare a ricevere il percorso locale (legge i
    byte da disco), anche se per qualche motivo avesse anche lui un
    url_pubblico valorizzato."""
    content_id = _contenuto_pronto(conn)
    db_social.elimina_asset_di(conn, content_id)
    db_social.salva_asset(conn, content_id, "/tmp/instagram-locale.png",
                          piattaforma="instagram", url_pubblico="https://cdn.test/ig.png")
    db_social.salva_asset(conn, content_id, "/tmp/linkedin-locale.png",
                          piattaforma="linkedin", url_pubblico="https://cdn.test/li.png")

    adapter_instagram = MockAdapter("instagram")
    adapter_linkedin = MockAdapter("linkedin")
    originale = publishing.adapter_per

    def adapter_per_finto(conn, piattaforma, forza_mock=False):
        if piattaforma == "instagram":
            return adapter_instagram
        if piattaforma == "linkedin":
            return adapter_linkedin
        return originale(conn, piattaforma, forza_mock=forza_mock)

    monkeypatch.setattr(publishing, "adapter_per", adapter_per_finto)
    publishing.pubblica_contenuto(conn, content_id)

    assert adapter_instagram.pubblicati[0]["asset"] == ["https://cdn.test/ig.png"]
    assert adapter_linkedin.pubblicati[0]["asset"] == ["/tmp/linkedin-locale.png"]


def test_doppia_pubblicazione_impossibile(conn):
    content_id = _contenuto_pronto(conn)
    publishing.pubblica_contenuto(conn, content_id)
    # seconda chiamata: nessuna nuova pubblicazione
    esiti = publishing.pubblica_contenuto(conn, content_id)
    assert esiti == {}
    pubs = db_social.publications_di(conn, content_id)
    assert len(pubs) == 2  # una per piattaforma, mai duplicate


def test_can_publish_catena_di_controlli(conn, monkeypatch):
    content_id = _contenuto_pronto(conn)
    content = db_social.get_content(conn, content_id)

    # 1) environment
    monkeypatch.delenv("GLOBAL_PUBLISHING_ENABLED", raising=False)
    ok, motivo = publishing.can_publish(conn, content, "linkedin")
    assert not ok and "GLOBAL_PUBLISHING_ENABLED" in motivo

    # 2) kill switch DB
    monkeypatch.setenv("GLOBAL_PUBLISHING_ENABLED", "true")
    db_social.set_setting(conn, "kill_switch", True)
    ok, motivo = publishing.can_publish(conn, content, "linkedin")
    assert not ok and "kill switch" in motivo

    # 3) account non verificato
    db_social.set_setting(conn, "kill_switch", False)
    ok, motivo = publishing.can_publish(conn, content, "linkedin")
    assert not ok and "non verificato" in motivo

    # 4) account verificato ma publishing disabilitato
    account = db_social.account_per_piattaforma(conn, "linkedin")
    db_social.aggiorna_account(conn, account["id"], stato="verificato")
    ok, motivo = publishing.can_publish(conn, content, "linkedin")
    assert not ok and "disabilitata" in motivo

    # 5) tutto abilitato + classe verde -> ok
    db_social.aggiorna_account(conn, account["id"], publishing_enabled=1)
    ok, motivo = publishing.can_publish(conn, content, "linkedin")
    assert ok, motivo


def test_can_publish_blocca_rosso_e_richiede_approvazione(conn, monkeypatch):
    monkeypatch.setenv("GLOBAL_PUBLISHING_ENABLED", "true")
    account = db_social.account_per_piattaforma(conn, "linkedin")
    db_social.aggiorna_account(conn, account["id"], stato="verificato",
                               publishing_enabled=1)
    content_id = _contenuto_pronto(conn)
    # declassa a rosso: mai pubblicabile
    db_social.aggiorna_content(conn, content_id, classe_rischio="rosso",
                               decisione_rischio="blocked")
    ok, motivo = publishing.can_publish(conn, db_social.get_content(conn, content_id),
                                        "linkedin")
    assert not ok and "rischio" in motivo
    # giallo senza approvazione umana: no
    db_social.aggiorna_content(conn, content_id, classe_rischio="giallo",
                               decisione_rischio="human_approval")
    ok, motivo = publishing.can_publish(conn, db_social.get_content(conn, content_id),
                                        "linkedin")
    assert not ok and "approvazione" in motivo
    # con approvazione registrata: si
    db_social.crea_approval(conn, content_id)
    approval = db_social.approval_aperta_di(conn, content_id)
    db_social.decidi_approval(conn, approval["id"], "approvato", utente_id=1)
    ok, motivo = publishing.can_publish(conn, db_social.get_content(conn, content_id),
                                        "linkedin")
    assert ok, motivo


def test_stato_non_pubblicabile(conn, monkeypatch):
    monkeypatch.setenv("GLOBAL_PUBLISHING_ENABLED", "true")
    account = db_social.account_per_piattaforma(conn, "linkedin")
    db_social.aggiorna_account(conn, account["id"], stato="verificato",
                               publishing_enabled=1)
    content_id = db_social.crea_content(conn, "Solo un'idea")
    content = db_social.get_content(conn, content_id)
    ok, motivo = publishing.can_publish(conn, content, "linkedin")
    assert not ok and "stato" in motivo


def test_adapter_factory(conn):
    assert isinstance(publishing.adapter_per(conn, "instagram"), MockAdapter)
    db_social.set_setting(conn, "mode_override", "production")
    from social.integrations.linkedin import LinkedInAdapter
    assert isinstance(publishing.adapter_per(conn, "linkedin"), LinkedInAdapter)
    assert isinstance(publishing.adapter_per(conn, "linkedin", forza_mock=True),
                      MockAdapter)
    db_social.set_setting(conn, "mode_override", None)


def test_adapter_reali_non_pronti_senza_config(conn):
    from social.integrations.instagram import InstagramAdapter
    from social.integrations.linkedin import LinkedInAdapter
    instagram = InstagramAdapter(conn).health_check()
    assert not instagram["pronto"]
    assert "non pronto" in instagram["messaggio"]
    linkedin = LinkedInAdapter(conn).health_check()
    assert not linkedin["pronto"]
    # e publish() rifiuta esplicitamente
    with pytest.raises(RuntimeError):
        InstagramAdapter(conn).publish("test")


def test_checklist_immagini_pubbliche_segue_configurazione_r2(conn, monkeypatch):
    """Il requisito "immagini via URL pubblico" e' legato alla configurazione
    di Cloudflare R2 (storage pubblico per le immagini, vedi asset_storage.py)
    — prima era un placeholder sempre False, non sarebbe mai diventato verde
    nemmeno dopo un vero deploy (bug segnalato indirettamente dall'utente)."""
    from social.integrations.instagram import InstagramAdapter
    voce = "Storage pubblico immagini (Cloudflare R2) configurato"

    for chiave in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
                  "R2_BUCKET_NAME", "R2_PUBLIC_BASE_URL"):
        monkeypatch.delenv(chiave, raising=False)
    checklist = InstagramAdapter(conn).health_check()["checklist"]
    assert not next(v for v in checklist if v["voce"] == voce)["ok"]

    monkeypatch.setenv("R2_ACCOUNT_ID", "account-test")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "chiave-test")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "segreto-test")
    monkeypatch.setenv("R2_BUCKET_NAME", "bucket-test")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://immagini.jobinpa.it")
    checklist = InstagramAdapter(conn).health_check()["checklist"]
    assert next(v for v in checklist if v["voce"] == voce)["ok"]
