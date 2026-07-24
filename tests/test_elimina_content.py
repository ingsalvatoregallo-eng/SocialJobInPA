"""Eliminazione di un contenuto: deve ripulire tutto cio' che ne dipende,
comprese le tabelle senza FK verso social_content (fatti, agent_runs,
collegamento dal calendario) e i job in coda che lo referenziano."""

from pathlib import Path

from social import agents, db_social, llm
from social.images import MockImageProvider


def _contenuto_completo(conn):
    """Contenuto con pipeline eseguita: varianti, asset, fatti, agent_runs
    e un job (publish) gia' presenti, per verificare la pulizia completa."""
    content_id = db_social.crea_content(conn, "Contenuto da eliminare",
                                        pillar_chiave="opportunita")
    agents.esegui_pipeline(conn, content_id, provider=llm.MockLLMProvider(conn),
                           image_provider=MockImageProvider())
    return content_id


def test_elimina_content_rimuove_tutto(conn):
    content_id = _contenuto_completo(conn)
    assert db_social.varianti_di(conn, content_id)
    assert db_social.asset_di(conn, content_id)
    assert db_social.fatti_di(conn, content_id)
    jobs_prima = db_social.lista_jobs(conn, limit=100)
    assert any(content_id in j["payload"] for j in jobs_prima)

    assert db_social.elimina_content(conn, content_id) is True

    assert db_social.get_content(conn, content_id) is None
    assert db_social.varianti_di(conn, content_id) == []
    assert db_social.asset_di(conn, content_id) == []
    assert db_social.fatti_di(conn, content_id) == []
    esecuzioni = db_social.agent_runs_recenti(conn, limit=200)
    assert not any(r["content_id"] == content_id for r in esecuzioni)
    jobs_dopo = db_social.lista_jobs(conn, limit=100)
    assert not any(content_id in j["payload"] for j in jobs_dopo)


def test_elimina_content_inesistente_ritorna_false(conn):
    assert db_social.elimina_content(conn, "id-che-non-esiste") is False


def test_elimina_content_scollega_dal_calendario(conn):
    content_id = db_social.crea_content(conn, "Tema pianificato")
    entry_id = db_social.crea_plan_entry(conn, "2026-08-03", "Tema pianificato",
                                         pillar_chiave="guida", content_id=content_id)
    db_social.elimina_content(conn, content_id)
    voce = conn.execute("SELECT content_id FROM social_editorial_plans WHERE id = ?",
                        (entry_id,)).fetchone()
    assert voce is not None  # la voce di calendario resta
    assert voce["content_id"] is None  # ma scollegata dal contenuto cancellato


def test_elimina_content_cancella_file_asset_su_disco(conn):
    content_id = _contenuto_completo(conn)
    percorsi = [a["percorso"] for a in db_social.asset_di(conn, content_id)]
    assert percorsi and all(Path(p).exists() for p in percorsi)
    db_social.elimina_content(conn, content_id)
    assert all(not Path(p).exists() for p in percorsi)


def test_elimina_content_registra_audit(conn):
    content_id = db_social.crea_content(conn, "Da tracciare prima di eliminare")
    db_social.elimina_content(conn, content_id, utente_id=42)
    audit = db_social.audit_recenti(conn, limit=10)
    voce = next(a for a in audit if a["azione"] == "content_eliminato")
    assert voce["utente_id"] == 42
    assert voce["oggetto_id"] == content_id
