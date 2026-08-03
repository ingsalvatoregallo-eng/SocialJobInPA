"""
scheduler.py — esecuzione dei job persistenti (sez. 16 del prompt master).

Due entrypoint separati (vedi worker_main.py e scheduler_main.py):
- il worker consuma social_scheduled_jobs (lock atomico, retry con backoff,
  dead-letter, recovery dei lock scaduti dopo un riavvio);
- lo scheduler semina i job ricorrenti (piano settimanale, raccolta metriche,
  import commenti) in modo idempotente.

Niente Redis/Celery: SQLite e' gia' il punto di coordinamento (WAL), e un
solo worker alla volta per job e' garantito dal lock in prendi_job.
"""

import json
import logging
import os
import socket
import time
from datetime import datetime, timedelta, timezone

from social import agents, db_social, publishing

log = logging.getLogger(__name__)

OWNER = f"{socket.gethostname()}:{os.getpid()}"


def esegui_job(conn, job):
    """Dispatch per tipo. Le eccezioni risalgono al chiamante che fa
    retry/backoff via chiudi_job."""
    payload = json.loads(job["payload"] or "{}")
    tipo = job["tipo"]
    if tipo == "publish":
        return publishing.pubblica_contenuto(conn, payload["content_id"])
    if tipo == "pipeline":
        return agents.esegui_pipeline(conn, payload["content_id"],
                                      urls_extra=payload.get("urls_extra"))
    if tipo == "rigenera_visual":
        return agents.rigenera_visual(conn, payload["content_id"])
    if tipo == "rigenera_asset_singolo":
        return agents.rigenera_immagine_singola(conn, payload["content_id"], payload["asset_id"])
    if tipo == "rigenera_copy":
        return agents.rigenera_copy(conn, payload["content_id"],
                                    note_revisore=payload.get("note_revisore"))
    if tipo == "generate_week_plan":
        return agents.supervisor_pianifica_settimana(conn, payload["settimana"])
    if tipo == "collect_metrics":
        raccolte = agents.analytics_raccogli(conn)
        agents.community_importa_commenti(conn)
        agents.community_proponi_risposte(conn)
        return raccolte
    raise ValueError(f"tipo di job sconosciuto: {tipo}")


def ciclo_worker(conn, *, una_volta=False, pausa_secondi=10):
    """Loop del worker: reclama ed esegue un job alla volta."""
    log.info("worker social avviato (%s)", OWNER)
    while True:
        job = db_social.prendi_job(conn, OWNER)
        if job is None:
            if una_volta:
                return
            time.sleep(pausa_secondi)
            continue
        log.info("job %s (%s), tentativo %s", job["id"], job["tipo"], job["tentativi"])
        try:
            esegui_job(conn, job)
            db_social.chiudi_job(conn, job["id"], "ok")
        except Exception as errore:
            log.exception("job %s fallito", job["id"])
            db_social.chiudi_job(conn, job["id"], "errore", errore=str(errore))
        if una_volta:
            return


def _lunedi_prossimo():
    oggi = datetime.now(timezone.utc).date()
    return (oggi + timedelta(days=(7 - oggi.weekday()) % 7 or 7)).isoformat()


def semina_job_ricorrenti(conn):
    """Idempotente: crea i job ricorrenti solo se non gia' in coda.
    - piano editoriale: ogni venerdi' per la settimana successiva;
    - metriche+commenti: ogni 6 ore."""
    settimana = _lunedi_prossimo()
    esiste_piano = any(
        j["tipo"] == "generate_week_plan" and settimana in (j["payload"] or "")
        and j["stato"] in {"pending", "running", "done"}
        for j in db_social.lista_jobs(conn, limit=500))
    if not esiste_piano:
        db_social.crea_job(conn, "generate_week_plan", {"settimana": settimana})
        log.info("job piano settimanale creato per %s", settimana)
    metriche_in_coda = any(
        j["tipo"] == "collect_metrics" and j["stato"] in {"pending", "running"}
        for j in db_social.lista_jobs(conn, stati=["pending", "running"], limit=500))
    if not metriche_in_coda:
        prossimo = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
        db_social.crea_job(conn, "collect_metrics", {}, esegui_at=prossimo)
        log.info("job raccolta metriche creato per %s", prossimo)


def ciclo_scheduler(conn, *, una_volta=False, pausa_secondi=300):
    log.info("scheduler social avviato")
    while True:
        try:
            semina_job_ricorrenti(conn)
        except Exception:
            log.exception("semina job ricorrenti fallita")
        if una_volta:
            return
        time.sleep(pausa_secondi)
