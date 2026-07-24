"""
seed_demo.py — dati dimostrativi del modulo social (sez. 25 del prompt).

Tutto e' chiaramente marcato DEMO (is_demo=1, titoli con prefisso [DEMO],
email @demo.jobinpa.local) e non tocca dati reali: nessuna riga viene
inserita nelle tabelle esistenti dei bandi. Idempotente: rieseguirlo non
duplica nulla.

Uso:  python -m social.seed_demo   (da src/, o via scripts/setup.ps1)
Password degli utenti demo: variabile DEMO_PASSWORD (default JobInPA-demo1,
SOLO per sviluppo locale).
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import auth  # noqa: E402
from social import agents, db_social, llm, publishing  # noqa: E402

UTENTI_DEMO = (
    ("admin@demo.jobinpa.local", "Ada", "Admin", "admin"),
    ("editor@demo.jobinpa.local", "Edo", "Editor", "editor"),
    ("reviewer@demo.jobinpa.local", "Rita", "Reviewer", "reviewer"),
)


def semina(conn, *, verbose=True):
    def stampa(msg):
        if verbose:
            print(msg)

    password = os.environ.get("DEMO_PASSWORD", "JobInPA-demo1")
    for email, nome, cognome, ruolo in UTENTI_DEMO:
        if db_social.utente_per_email(conn, email) is None:
            db_social.crea_utente(conn, email, auth.hash_password(password),
                                  nome=nome, cognome=cognome, ruolo=ruolo)
            stampa(f"utente demo creato: {email} (ruolo {ruolo})")

    # Brand demo (palette JobInPA).
    esiste = conn.execute("SELECT 1 FROM social_brands WHERE is_demo = 1").fetchone()
    if not esiste:
        db_social._insert(conn, "social_brands", {
            "id": db_social._nuovo_id(), "nome": "JobInPA",
            "payoff": "Your PA, powered by AI",
            "palette": '{"primario": "#0B3D91", "accento": "#1FA774"}',
            "is_demo": 1, "creato_at": db_social._adesso()})
        conn.commit()
        stampa("brand demo creato")

    db_social.aggiungi_source_domain(conn, "demo.jobinpa.local", "Fonte ufficiale DEMO")

    # Contenuto demo con pipeline mock completa (ricerca -> copy -> visual ->
    # rischio) + richiesta approvazione + pubblicazione mock + metriche mock.
    gia_seminato = conn.execute(
        "SELECT 1 FROM social_content WHERE is_demo = 1").fetchone()
    if gia_seminato:
        stampa("contenuti demo gia' presenti: niente da fare")
        return

    provider = llm.MockLLMProvider(conn)
    from social.images import MockImageProvider
    image_provider = MockImageProvider()

    # 1) contenuto che finisce in AWAITING_APPROVAL (per provare il flusso).
    in_approvazione = db_social.crea_content(
        conn, "[DEMO] Aggiornamento normativo sui concorsi", pillar_chiave="scadenza",
        brief="Contenuto demo per il flusso di approvazione", is_demo=True)
    from social import models
    provider.imposta(models.ValutazioneRischio, models.ValutazioneRischio(
        classe="giallo", punteggio_accuratezza=0.8, punteggio_brand=0.9,
        punteggio_conformita=0.85, motivi=["[DEMO] tema normativo: revisione umana"]))
    agents.esegui_pipeline(conn, in_approvazione, provider=provider,
                           image_provider=image_provider)
    stampa(f"contenuto demo in approvazione: {in_approvazione}")

    # 2) contenuto verde pubblicato in mock, con metriche e commento demo.
    provider_verde = llm.MockLLMProvider(conn)
    pubblicato = db_social.crea_content(
        conn, "[DEMO] Nuovo concorso: 10 posti al Comune Demo",
        pillar_chiave="opportunita", brief="Contenuto demo pubblicato (mock)",
        is_demo=True)
    agents.esegui_pipeline(conn, pubblicato, provider=provider_verde,
                           image_provider=image_provider)
    publishing.pubblica_contenuto(conn, pubblicato)
    agents.analytics_raccogli(conn)
    agents.community_importa_commenti(conn)
    n_proposte = 0
    for commento in db_social.commenti(conn, stato="nuovo"):
        esiste = conn.execute("SELECT 1 FROM social_reply_drafts WHERE comment_id = ?",
                              (commento["id"],)).fetchone()
        if not esiste:
            db_social.salva_reply_draft(
                conn, commento["id"],
                "[DEMO] Grazie! Trovi il link al bando ufficiale nel post.")
            n_proposte += 1
    stampa(f"contenuto demo pubblicato (mock): {pubblicato}, risposte proposte: {n_proposte}")

    # Piano editoriale demo per la settimana corrente.
    from datetime import datetime, timedelta, timezone
    oggi = datetime.now(timezone.utc).date()
    lunedi = (oggi - timedelta(days=oggi.weekday())).isoformat()
    if not db_social.plan_settimana(conn, lunedi):
        for tema, pillar, fascia in (
                ("[DEMO] Opportunita' della settimana", "opportunita", "12:00-14:00"),
                ("[DEMO] Guida: come leggere un bando", "guida", "08:00-10:00"),
                ("[DEMO] Concorsi in scadenza", "scadenza", "17:00-19:00")):
            db_social.crea_plan_entry(conn, lunedi, tema, pillar_chiave=pillar,
                                      fascia_oraria=fascia, is_demo=True)
        stampa(f"piano editoriale demo creato per la settimana {lunedi}")


if __name__ == "__main__":
    conn = db_social.connect()
    db_social.init_social_db(conn)
    semina(conn)
    print("Seed demo completato.")
