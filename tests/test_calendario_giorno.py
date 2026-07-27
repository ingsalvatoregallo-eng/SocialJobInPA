"""Calendario con giorno specifico e suggerimenti AI separati dai contenuti
(accetta/modifica/scarta prima di creare un contenuto vero e spendere
budget AI sulla pipeline)."""

from social import agents, db_social, llm, models


def test_job_in_corso_vero_se_pending(conn):
    db_social.crea_job(conn, "generate_week_plan", {"settimana": "2026-08-03"})
    assert db_social.job_in_corso(conn, "generate_week_plan", "2026-08-03")


def test_job_in_corso_falso_se_nessun_job(conn):
    assert not db_social.job_in_corso(conn, "generate_week_plan", "2026-08-03")


def test_job_in_corso_filtra_per_settimana_nel_payload(conn):
    db_social.crea_job(conn, "generate_week_plan", {"settimana": "2026-08-10"})
    assert not db_social.job_in_corso(conn, "generate_week_plan", "2026-08-03")


def test_job_in_corso_falso_se_job_gia_concluso(conn):
    job_id = db_social.crea_job(conn, "generate_week_plan", {"settimana": "2026-08-03"})
    job = db_social.prendi_job(conn, "worker-test")
    assert job["id"] == job_id
    db_social.chiudi_job(conn, job_id, "ok")
    assert not db_social.job_in_corso(conn, "generate_week_plan", "2026-08-03")


def test_crea_plan_entry_senza_content_id_e_suggerito(conn):
    entry_id = db_social.crea_plan_entry(conn, "2026-08-03", "Tema proposto",
                                         pillar_chiave="guida", giorno="2026-08-05")
    voce = db_social.plan_entry(conn, entry_id)
    assert voce["stato"] == "suggerito"
    assert voce["content_id"] is None
    assert voce["giorno"] == "2026-08-05"


def test_crea_plan_entry_con_content_id_e_pianificato(conn):
    content_id = db_social.crea_content(conn, "Contenuto vero")
    entry_id = db_social.crea_plan_entry(conn, "2026-08-03", "Tema",
                                         content_id=content_id, giorno="2026-08-04")
    voce = db_social.plan_entry(conn, entry_id)
    assert voce["stato"] == "pianificato"
    assert voce["content_id"] == content_id


def test_accetta_plan_entry_crea_contenuto_e_avvia_pipeline(conn):
    entry_id = db_social.crea_plan_entry(conn, "2026-08-03", "Tema da accettare",
                                         pillar_chiave="opportunita", obiettivo="traffico",
                                         giorno="2026-08-03")
    content_id = db_social.accetta_plan_entry(conn, entry_id, creato_da=1)
    assert content_id is not None
    content = db_social.get_content(conn, content_id)
    assert content["titolo"] == "Tema da accettare"
    assert content["stato"] == "IDEA"
    voce = db_social.plan_entry(conn, entry_id)
    assert voce["stato"] == "pianificato"
    assert voce["content_id"] == content_id
    jobs = db_social.lista_jobs(conn, stati=["pending"])
    assert any(content_id in j["payload"] for j in jobs)


def test_accetta_plan_entry_con_modifiche(conn):
    entry_id = db_social.crea_plan_entry(conn, "2026-08-03", "Tema originale",
                                         pillar_chiave="guida", giorno="2026-08-03")
    content_id = db_social.accetta_plan_entry(
        conn, entry_id, tema="Tema modificato", pillar_chiave="scadenza",
        giorno="2026-08-06", avvia_pipeline=False)
    content = db_social.get_content(conn, content_id)
    assert content["titolo"] == "Tema modificato"
    voce = db_social.plan_entry(conn, entry_id)
    assert voce["tema"] == "Tema modificato"
    assert voce["pillar_chiave"] == "scadenza"
    assert voce["giorno"] == "2026-08-06"
    # avvia_pipeline=False: nessun job creato
    assert db_social.lista_jobs(conn, stati=["pending"]) == []


def test_accetta_plan_entry_gia_accettata_ritorna_none(conn):
    entry_id = db_social.crea_plan_entry(conn, "2026-08-03", "Tema",
                                         pillar_chiave="guida", giorno="2026-08-03")
    db_social.accetta_plan_entry(conn, entry_id, avvia_pipeline=False)
    assert db_social.accetta_plan_entry(conn, entry_id, avvia_pipeline=False) is None


def test_accetta_plan_entry_inesistente_ritorna_none(conn):
    assert db_social.accetta_plan_entry(conn, "id-inesistente") is None


def test_elimina_plan_entry_scarta_suggerimento(conn):
    entry_id = db_social.crea_plan_entry(conn, "2026-08-03", "Da scartare",
                                         pillar_chiave="guida", giorno="2026-08-03")
    assert db_social.elimina_plan_entry(conn, entry_id) is True
    assert db_social.plan_entry(conn, entry_id) is None


def test_elimina_plan_entry_non_tocca_il_contenuto_collegato(conn):
    content_id = db_social.crea_content(conn, "Contenuto da non toccare")
    entry_id = db_social.crea_plan_entry(conn, "2026-08-03", "Tema",
                                         content_id=content_id, giorno="2026-08-03")
    db_social.elimina_plan_entry(conn, entry_id)
    assert db_social.get_content(conn, content_id) is not None


def test_giorno_da_settimana_calcola_data_corretta():
    # 2026-08-03 e' un lunedi'
    assert agents._giorno_da_settimana("2026-08-03", "lunedi") == "2026-08-03"
    assert agents._giorno_da_settimana("2026-08-03", "Mercoledi") == "2026-08-05"
    assert agents._giorno_da_settimana("2026-08-03", "domenica") == "2026-08-09"


def test_giorno_da_settimana_nome_sconosciuto_ritorna_none():
    # "lunedì" con accento non e' fra i valori attesi (solo ASCII, vedi
    # _GIORNI_SETTIMANA): meglio nessun giorno che uno indovinato male.
    assert agents._giorno_da_settimana("2026-08-03", "lunedì") is None
    assert agents._giorno_da_settimana("2026-08-03", "notte fonda") is None


def test_supervisor_crea_solo_suggerimenti_non_contenuti(conn):
    provider = llm.MockLLMProvider(conn)
    entry_ids = agents.supervisor_pianifica_settimana(conn, "2026-08-03", provider=provider)
    assert len(entry_ids) == 3
    voci = db_social.plan_settimana(conn, "2026-08-03")
    assert all(v["stato"] == "suggerito" for v in voci)
    assert all(v["content_id"] is None for v in voci)
    # nessun contenuto vero creato finche' non si accetta
    assert db_social.lista_content(conn) == []
    assert all(v["giorno"] is not None for v in voci)
