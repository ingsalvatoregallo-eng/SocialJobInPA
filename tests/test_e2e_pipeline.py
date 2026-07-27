"""E2E (tutto mock): idea -> ricerca -> copy -> visual -> rischio ->
approvazione -> programmazione -> pubblicazione -> metriche -> audit."""

import json

from social import agents, approvals, db_social, llm, models, publishing, scheduler
from social.images import MockImageProvider


class _ClienteJobInPAFinto:
    """Bandi fissi, mai una vera chiamata di rete: gli e2e test devono
    restare deterministici (numero di asset generati per il carosello
    Instagram) indipendentemente da quanti bandi esistano davvero su
    JobInPA in questo momento."""

    def __init__(self, n_bandi=3):
        self._bandi = [
            {"id": f"CONC-{i}", "titolo": f"Concorso demo {i}", "enti": ["Ente Demo"],
             "num_posti": 5, "scadenza": "2026-12-31", "stato": "OPEN",
             "sintesi": "Concorso di prova.", "titolo_studio_richiesto": "Diploma",
             "competenze": [], "url_dettaglio": "https://example.invalid"}
            for i in range(n_bandi)]

    @property
    def configurato(self):
        return True

    def bandi(self, *, limit=10, **filtri):
        return self._bandi[:limit]

    def bando(self, concorso_id):
        return None

    def filtri_disponibili(self):
        return {}


def test_e2e_percorso_verde_automatico(conn, monkeypatch):
    monkeypatch.setattr(agents.jobinpa_client, "client", lambda: _ClienteJobInPAFinto(3))
    content_id = db_social.crea_content(conn, "Nuovo concorso Comune Demo",
                                        pillar_chiave="opportunita")
    stato = agents.esegui_pipeline(conn, content_id,
                                   provider=llm.MockLLMProvider(conn),
                                   image_provider=MockImageProvider())
    assert stato == "APPROVED"
    content = db_social.get_content(conn, content_id)
    assert content["stato"] == "SCHEDULED"
    assert content["classe_rischio"] == "verde"
    assert content["programmato_at"] is not None
    # varianti per entrambe le piattaforme + asset + fatti
    assert {v["piattaforma"] for v in db_social.varianti_di(conn, content_id)} \
        == {"instagram", "linkedin"}
    # 3 bandi trovati -> carosello Instagram (1 immagine per bando) + 1 LinkedIn
    assert len(db_social.asset_di(conn, content_id)) == 4
    assert db_social.fatti_di(conn, content_id)
    # job di pubblicazione in coda
    jobs = db_social.lista_jobs(conn, stati=["pending"])
    assert any(j["tipo"] == "publish" and content_id in j["payload"] for j in jobs)
    # il worker lo esegue (data futura: forziamo esegui_at a ora)
    conn.execute("UPDATE social_scheduled_jobs SET esegui_at = ? WHERE tipo = 'publish'",
                 (db_social._adesso(),))
    conn.commit()
    scheduler.ciclo_worker(conn, una_volta=True)
    assert db_social.get_content(conn, content_id)["stato"] == "PUBLISHED"
    # metriche e commenti mock
    assert agents.analytics_raccogli(conn) == 2
    assert agents.community_importa_commenti(conn) == 2
    proposte = agents.community_proponi_risposte(conn, )
    assert proposte == 2
    # le risposte NON sono mai inviate: restano proposte
    assert all(r["stato"] == "proposta" for r in db_social.reply_drafts(conn))
    # audit completo
    azioni = {a["azione"] for a in db_social.audit_recenti(conn, limit=200)}
    assert {"transizione_stato", "pubblicazione"} <= azioni


def test_pipeline_duplicata_e_no_op(conn, monkeypatch):
    """Un secondo job 'pipeline' sullo stesso contenuto (doppio click, o due
    job accodati) non deve sollevare TransizioneNonValida: il contenuto e'
    gia' avanzato oltre gli stati di partenza, quindi la seconda chiamata
    esce subito senza toccare nulla (regressione: prima falliva con
    "SCHEDULED -> RESEARCHING" e finiva in dead-letter dopo 5 tentativi)."""
    monkeypatch.setattr(agents.jobinpa_client, "client", lambda: _ClienteJobInPAFinto(3))
    content_id = db_social.crea_content(conn, "Contenuto con doppio avvio")
    provider = llm.MockLLMProvider(conn)
    image_provider = MockImageProvider()
    primo = agents.esegui_pipeline(conn, content_id, provider=provider,
                                   image_provider=image_provider)
    assert primo == "APPROVED"
    stato_dopo_primo = db_social.get_content(conn, content_id)["stato"]
    assert stato_dopo_primo == "SCHEDULED"

    secondo = agents.esegui_pipeline(conn, content_id, provider=provider,
                                     image_provider=image_provider)
    assert secondo == "SCHEDULED"  # ritorna lo stato attuale, nessuna eccezione
    assert db_social.get_content(conn, content_id)["stato"] == "SCHEDULED"
    # varianti/asset non duplicati: la seconda chiamata non ha rifatto nulla
    assert len(db_social.varianti_di(conn, content_id)) == 2
    assert len(db_social.asset_di(conn, content_id)) == 4


def test_e2e_percorso_giallo_con_approvazione_umana(conn):
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.ValutazioneRischio, models.ValutazioneRischio(
        classe="giallo", punteggio_accuratezza=0.8, punteggio_brand=0.9,
        punteggio_conformita=0.8, motivi=["tema normativo"]))
    content_id = db_social.crea_content(conn, "Aggiornamento requisiti")
    stato = agents.esegui_pipeline(conn, content_id, provider=provider,
                                   image_provider=MockImageProvider())
    assert stato == "AWAITING_APPROVAL"
    approval = db_social.approval_aperta_di(conn, content_id)
    assert approval is not None
    # email: senza SMTP configurato niente invio, ma nessun errore
    # il revisore approva -> APPROVED -> SCHEDULED + job
    approvals.approva(conn, approval["id"], utente_id=42, motivo="ok")
    content = db_social.get_content(conn, content_id)
    assert content["stato"] == "SCHEDULED"
    eventi = conn.execute(
        "SELECT azione FROM social_approval_events WHERE approval_id = ?",
        (approval["id"],)).fetchall()
    assert {"richiesta", "approvato"} <= {e["azione"] for e in eventi}


def test_e2e_percorso_rosso_bloccato(conn):
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.ValutazioneRischio, models.ValutazioneRischio(
        classe="rosso", punteggio_accuratezza=0.2, punteggio_brand=0.5,
        punteggio_conformita=0.1, motivi=["accuse verso enti"]))
    content_id = db_social.crea_content(conn, "Contenuto rischioso")
    stato = agents.esegui_pipeline(conn, content_id, provider=provider,
                                   image_provider=MockImageProvider())
    assert stato == "BLOCKED"
    assert db_social.get_content(conn, content_id)["stato"] == "BLOCKED"
    # nessuna pubblicazione possibile
    assert db_social.publications_di(conn, content_id) == []


def test_e2e_regole_deterministiche_prevalgono_sul_giudizio_ai(conn):
    """Il mock dice verde, ma il testo contiene una promessa di successo:
    le regole forzano rosso e il giudizio AI non puo' declassarlo."""
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.VarianteCopy, models.VarianteCopy(
        testo="Con JobInPA vincerai il concorso: garantiamo il successo!"))
    content_id = db_social.crea_content(conn, "Promesse")
    stato = agents.esegui_pipeline(conn, content_id, provider=provider,
                                   image_provider=MockImageProvider())
    assert stato == "BLOCKED"
    punteggi = json.loads(db_social.get_content(conn, content_id)["punteggi_rischio"])
    assert punteggi["classe_regole"] == "rosso"
    assert punteggi["classe_ai"] == "verde"


def test_e2e_richiesta_modifiche_e_nuovo_giro(conn):
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.ValutazioneRischio, models.ValutazioneRischio(
        classe="giallo", punteggio_accuratezza=0.7, punteggio_brand=0.7,
        punteggio_conformita=0.7, motivi=[]))
    content_id = db_social.crea_content(conn, "Statistiche concorsi")
    agents.esegui_pipeline(conn, content_id, provider=provider,
                           image_provider=MockImageProvider())
    approval = db_social.approval_aperta_di(conn, content_id)
    approvals.richiedi_modifiche(conn, approval["id"], utente_id=7,
                                 motivo="Togliere la percentuale")
    assert db_social.get_content(conn, content_id)["stato"] == "CHANGES_REQUESTED"

    # Riavviando la pipeline, la nota del revisore deve arrivare da sola ai
    # prompt di research/copywriting (mai piu' bisogno di ritrascriverla).
    agents.esegui_pipeline(conn, content_id, provider=provider,
                           image_provider=MockImageProvider())
    prompt_con_nota = [u for (_, u, _) in provider.chiamate if "Togliere la percentuale" in u]
    assert len(prompt_con_nota) >= 2  # almeno research + copywriting (instagram/linkedin)


def test_e2e_supervisor_genera_piano_settimanale(conn):
    """Il Supervisor crea SUGGERIMENTI (non contenuti): serve un Accetta
    esplicito prima che diventino contenuti veri (vedi test_calendario_giorno.py)."""
    creati = agents.supervisor_pianifica_settimana(
        conn, "2026-07-27", provider=llm.MockLLMProvider(conn))
    assert len(creati) == 3
    voci = db_social.plan_settimana(conn, "2026-07-27")
    assert {v["pillar_chiave"] for v in voci} == {"opportunita", "guida", "scadenza"}
    assert all(v["stato"] == "suggerito" for v in voci)
    assert all(v["content_id"] is None for v in voci)


def test_seed_demo_idempotente(conn):
    from social import seed_demo
    seed_demo.semina(conn, verbose=False)
    contenuti_prima = len(db_social.lista_content(conn))
    seed_demo.semina(conn, verbose=False)
    assert len(db_social.lista_content(conn)) == contenuti_prima
    # tutto marcato demo
    assert all(c["is_demo"] for c in db_social.lista_content(conn)
               if c["titolo"].startswith("[DEMO]"))
