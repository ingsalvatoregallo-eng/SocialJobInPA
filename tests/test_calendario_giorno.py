"""Calendario con giorno specifico e suggerimenti AI separati dai contenuti
(accetta/modifica/scarta prima di creare un contenuto vero e spendere
budget AI sulla pipeline)."""

import json

import pytest

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


# --- canali_abilitati: mai tutte le piattaforme per default -----------------
# Segnalato dall'utente: accettare un suggerimento del Supervisor generava
# sempre anche la variante LinkedIn, pure con l'account disabilitato — sia
# accetta_plan_entry sia aggiungi_contenuto_giorno bypassavano il calcolo
# dei canali davvero abilitati (gia' usato correttamente da
# web.nuovo_contenuto_form) e ricadevano sul default di crea_content
# (tutte le piattaforme).

def test_canali_abilitati_esclude_le_piattaforme_disabilitate(conn):
    account_instagram = db_social.account_per_piattaforma(conn, "instagram")
    db_social.aggiorna_account(conn, account_instagram["id"], publishing_enabled=1)
    assert db_social.canali_abilitati(conn) == ["instagram"]


def test_canali_abilitati_vuoto_se_nessun_account_abilitato(conn):
    assert db_social.canali_abilitati(conn) == []


def test_accetta_plan_entry_rispetta_i_canali_abilitati(conn):
    account_instagram = db_social.account_per_piattaforma(conn, "instagram")
    db_social.aggiorna_account(conn, account_instagram["id"], publishing_enabled=1)
    # LinkedIn resta disabilitato (default di seed).
    entry_id = db_social.crea_plan_entry(conn, "2026-08-03", "Tema da accettare",
                                         giorno="2026-08-03")
    content_id = db_social.accetta_plan_entry(conn, entry_id, avvia_pipeline=False)
    content = db_social.get_content(conn, content_id)
    assert json.loads(content["canali"]) == ["instagram"]


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


# --- categoria_id: il tema accettato riusa la categoria del Supervisor -----
# Segnalato dall'utente: un tema del piano settimanale diventava un
# contenuto "generico" che ignorava prompt/stile/struttura della categoria
# censita nel backoffice, anche quando il tema era chiaramente a tema
# concorsi — perche' categoria_id non veniva mai propagato al contenuto vero.

def test_crea_plan_entry_memorizza_la_categoria(conn):
    categoria_id = db_social.crea_categoria(conn, "Bandi test", strategia_fatti="bandi_jobinpa")
    entry_id = db_social.crea_plan_entry(conn, "2026-08-03", "Tema", giorno="2026-08-03",
                                         categoria_id=categoria_id)
    voce = db_social.plan_entry(conn, entry_id)
    assert voce["categoria_id"] == categoria_id


def test_accetta_plan_entry_riusa_la_categoria_del_suggerimento(conn):
    categoria_id = db_social.crea_categoria(conn, "Bandi test", strategia_fatti="bandi_jobinpa")
    entry_id = db_social.crea_plan_entry(conn, "2026-08-03", "Tema", giorno="2026-08-03",
                                         categoria_id=categoria_id)
    content_id = db_social.accetta_plan_entry(conn, entry_id, avvia_pipeline=False)
    content = db_social.get_content(conn, content_id)
    assert content["categoria_id"] == categoria_id


def test_accetta_plan_entry_permette_di_cambiare_la_categoria(conn):
    """La card del suggerimento in Calendario ha un menu a tendina per
    cambiare la categoria prima di accettare (segnalato dall'utente): un
    categoria_id esplicito sovrascrive quello scelto dal Supervisor."""
    categoria_supervisor = db_social.crea_categoria(conn, "Bandi test", strategia_fatti="bandi_jobinpa")
    categoria_scelta_utente = db_social.crea_categoria(conn, "Guide", strategia_fatti="libera")
    entry_id = db_social.crea_plan_entry(conn, "2026-08-03", "Tema", giorno="2026-08-03",
                                         categoria_id=categoria_supervisor)
    content_id = db_social.accetta_plan_entry(
        conn, entry_id, categoria_id=categoria_scelta_utente, avvia_pipeline=False)
    content = db_social.get_content(conn, content_id)
    assert content["categoria_id"] == categoria_scelta_utente


def test_accetta_plan_entry_senza_categoria_resta_senza(conn):
    entry_id = db_social.crea_plan_entry(conn, "2026-08-03", "Tema", giorno="2026-08-03")
    content_id = db_social.accetta_plan_entry(conn, entry_id, avvia_pipeline=False)
    content = db_social.get_content(conn, content_id)
    assert content["categoria_id"] is None


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
    # Nessuna categoria da creare: "Concorsi" (bandi_jobinpa) e' gia'
    # seminata di default su ogni DB (vedi db_social._migra), quindi c'e'
    # sempre almeno una categoria idonea qui.
    provider = llm.MockLLMProvider(conn)
    entry_ids = agents.supervisor_pianifica_settimana(conn, "2026-08-03", provider=provider)
    assert len(entry_ids) == 3
    voci = db_social.plan_settimana(conn, "2026-08-03")
    assert all(v["stato"] == "suggerito" for v in voci)
    assert all(v["content_id"] is None for v in voci)
    # nessun contenuto vero creato finche' non si accetta
    assert db_social.lista_content(conn) == []
    assert all(v["giorno"] is not None for v in voci)


# --- Il Supervisor riusa le Categorie censite nel backoffice ---------------
# Segnalato dall'utente: "Genera 3 temi" non sapeva nulla delle Categorie e
# generava temi "generici" che sembravano a tema concorsi solo perche'
# 'bandi_jobinpa' e' il default senza categoria — mai le personalizzazioni
# davvero configurate (prompt illustrazione, stile, struttura del post).

def test_categorie_idonee_supervisor_esclude_promozioni_e_funzionalita(conn):
    """'promozioni_jobinpa'/'funzionalita_jobinpa' richiedono di scegliere
    a mano una promozione/funzionalita' reale e attiva da JobInPA (vedi
    web.crea_contenuto): il piano settimanale automatico non prevede quel
    passaggio, quindi quelle categorie non sono proponibili qui.
    "Concorsi"/"Promozioni"/"Funzionalità" sono gia' seminate di default
    (vedi db_social._migra): qui si aggiunge solo "Guide" (idonea)."""
    db_social.crea_categoria(conn, "Guide", strategia_fatti="libera")
    idonee = {c["nome"] for c in agents.categorie_idonee_supervisor(conn)}
    assert idonee == {"Concorsi", "Guide"}


def test_supervisor_pianifica_settimana_senza_categorie_idonee_solleva(conn):
    # "Concorsi" (bandi_jobinpa) e' seminata di default: va rimossa per
    # simulare davvero "nessuna categoria idonea" (vedi db_social._migra).
    concorsi = next(c for c in db_social.lista_categorie(conn) if c["nome"] == "Concorsi")
    db_social.elimina_categoria(conn, concorsi["id"])
    with pytest.raises(agents.NessunaCategoriaIdonea):
        agents.supervisor_pianifica_settimana(conn, "2026-08-03", provider=llm.MockLLMProvider(conn))
    # nessun suggerimento parziale lasciato a meta'
    assert db_social.plan_settimana(conn, "2026-08-03") == []


def test_supervisor_pianifica_settimana_include_solo_categorie_idonee_nel_prompt(conn):
    # "Concorsi" (bandi_jobinpa, idonea) e "Promozioni" (promozioni_jobinpa,
    # non idonea) sono gia' seminate di default (vedi db_social._migra).
    provider = llm.MockLLMProvider(conn)
    agents.supervisor_pianifica_settimana(conn, "2026-08-03", provider=provider)
    _, user_prompt, _ = provider.chiamate[0]
    assert '"Concorsi"' in user_prompt
    assert '"Promozioni"' not in user_prompt


def test_supervisor_pianifica_settimana_risolve_la_categoria_scelta(conn):
    # "Concorsi" (bandi_jobinpa) e' gia' seminata di default.
    categoria_id = next(c for c in db_social.lista_categorie(conn) if c["nome"] == "Concorsi")["id"]
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.PianoSettimanale, models.PianoSettimanale(voci=[
        models.VoceCalendario(tema="Concorso della settimana", pillar="opportunita",
                              obiettivo="traffico", fascia_oraria="12:00-14:00",
                              giorno_settimana="martedi", categoria_nome="Concorsi")]))
    agents.supervisor_pianifica_settimana(conn, "2026-08-03", provider=provider)
    voci = db_social.plan_settimana(conn, "2026-08-03")
    assert voci[0]["categoria_id"] == categoria_id


def test_supervisor_pianifica_settimana_nome_categoria_inventato_resta_senza(conn):
    """Un nome fuori dal vocabolario fornito (il modello ha sbagliato) non
    deve mai inventare/indovinare una categoria: categoria_id resta None,
    stesso principio dei vocabolari chiusi altrove (es. interpreta_brief).
    "Concorsi" e' gia' seminata di default (vedi db_social._migra)."""
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.PianoSettimanale, models.PianoSettimanale(voci=[
        models.VoceCalendario(tema="Tema qualsiasi", pillar="guida", obiettivo="traffico",
                              fascia_oraria="12:00-14:00", giorno_settimana="martedi",
                              categoria_nome="Categoria che non esiste")]))
    agents.supervisor_pianifica_settimana(conn, "2026-08-03", provider=provider)
    voci = db_social.plan_settimana(conn, "2026-08-03")
    assert voci[0]["categoria_id"] is None
