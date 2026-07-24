"""
api.py (social) — API REST versionate di SocialJobInPA: /api/v1/social/*.

Autenticazione: Bearer token propri (deps.utente_corrente) + permessi
social.* via db_social.ha_permesso (mappa statica ruolo->permessi).
Montato da src/app.py.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from deps import ottieni_conn, utente_corrente  # noqa: E402
from social import (  # noqa: E402
    agents, approvals, config, db_social, publishing, state_machine,
)

router = APIRouter(prefix="/api/v1/social", tags=["social"])


def richiede(permesso):
    def dipendenza(utente=Depends(utente_corrente), conn=Depends(ottieni_conn)):
        if not db_social.ha_permesso(conn, utente, permesso):
            raise HTTPException(status_code=403, detail=f"Permesso mancante: {permesso}")
        return utente
    return dipendenza


def _riga(riga):
    return dict(riga) if riga is not None else None


# --- Contenuti ---------------------------------------------------------------

class NuovoContenuto(BaseModel):
    titolo: str = Field(min_length=3, max_length=300)
    pillar: Optional[str] = None
    brief: Optional[str] = Field(default=None, max_length=4000)
    canali: list[str] = Field(default_factory=lambda: ["instagram", "linkedin"])
    concorso_id: Optional[str] = None


@router.get("/content")
def elenco_contenuti(stato: Optional[str] = None,
                     utente=Depends(richiede("social.view")),
                     conn=Depends(ottieni_conn)):
    stati = [stato] if stato else None
    return [_riga(r) for r in db_social.lista_content(conn, stati=stati)]


@router.post("/content", status_code=201)
def crea_contenuto(corpo: NuovoContenuto,
                   utente=Depends(richiede("social.edit")),
                   conn=Depends(ottieni_conn)):
    for canale in corpo.canali:
        if canale not in db_social.PIATTAFORME:
            raise HTTPException(status_code=422, detail=f"canale non valido: {canale}")
    content_id = db_social.crea_content(
        conn, corpo.titolo, pillar_chiave=corpo.pillar, brief=corpo.brief,
        canali=corpo.canali, concorso_id=corpo.concorso_id, creato_da=utente["id"])
    db_social.audit(conn, "contenuto_creato", utente_id=utente["id"],
                    oggetto_tipo="content", oggetto_id=content_id)
    return {"id": content_id}


@router.get("/content/{content_id}")
def dettaglio_contenuto(content_id: str, utente=Depends(richiede("social.view")),
                        conn=Depends(ottieni_conn)):
    content = db_social.get_content(conn, content_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Contenuto non trovato")
    return {
        "content": _riga(content),
        "varianti": [_riga(v) for v in db_social.varianti_di(conn, content_id)],
        "asset": [_riga(a) for a in db_social.asset_di(conn, content_id)],
        "fatti": [_riga(f) for f in db_social.fatti_di(conn, content_id)],
        "pubblicazioni": [_riga(p) for p in db_social.publications_di(conn, content_id)],
    }


@router.post("/content/{content_id}/pipeline")
def avvia_pipeline(content_id: str, utente=Depends(richiede("social.edit")),
                   conn=Depends(ottieni_conn)):
    """Accoda la pipeline al worker (asincrona: la risposta e' immediata)."""
    if db_social.get_content(conn, content_id) is None:
        raise HTTPException(status_code=404, detail="Contenuto non trovato")
    job_id = db_social.crea_job(conn, "pipeline", {"content_id": content_id})
    return {"job_id": job_id}


@router.post("/content/{content_id}/publish")
def pubblica(content_id: str, utente=Depends(richiede("social.publish")),
             conn=Depends(ottieni_conn)):
    try:
        esiti = publishing.pubblica_contenuto(conn, content_id, utente_id=utente["id"])
    except state_machine.TransizioneNonValida as errore:
        raise HTTPException(status_code=409, detail=str(errore))
    return {"esiti": esiti}


# --- Approvazioni ------------------------------------------------------------

class Decisione(BaseModel):
    motivo: Optional[str] = Field(default=None, max_length=2000)


@router.get("/approvals")
def elenco_approvazioni(utente=Depends(richiede("social.view")),
                        conn=Depends(ottieni_conn)):
    return [_riga(a) for a in db_social.approvals_in_attesa(conn)]


@router.post("/approvals/{approval_id}/approve")
def approva(approval_id: str, corpo: Decisione,
            utente=Depends(richiede("social.approve")), conn=Depends(ottieni_conn)):
    try:
        approvals.approva(conn, approval_id, utente["id"], corpo.motivo)
    except ValueError as errore:
        raise HTTPException(status_code=404, detail=str(errore))
    return {"stato": "approvato"}


@router.post("/approvals/{approval_id}/reject")
def rifiuta(approval_id: str, corpo: Decisione,
            utente=Depends(richiede("social.approve")), conn=Depends(ottieni_conn)):
    try:
        approvals.rifiuta(conn, approval_id, utente["id"], corpo.motivo)
    except ValueError as errore:
        raise HTTPException(status_code=404, detail=str(errore))
    return {"stato": "rifiutato"}


@router.post("/approvals/{approval_id}/request-changes")
def modifiche(approval_id: str, corpo: Decisione,
              utente=Depends(richiede("social.approve")), conn=Depends(ottieni_conn)):
    if not corpo.motivo:
        raise HTTPException(status_code=422, detail="La richiesta di modifiche richiede un motivo")
    try:
        approvals.richiedi_modifiche(conn, approval_id, utente["id"], corpo.motivo)
    except ValueError as errore:
        raise HTTPException(status_code=404, detail=str(errore))
    return {"stato": "modifiche_richieste"}


# --- Pubblicazioni / analytics / costi ---------------------------------------

@router.get("/publications")
def elenco_pubblicazioni(stato: Optional[str] = None,
                         utente=Depends(richiede("social.view")),
                         conn=Depends(ottieni_conn)):
    return [_riga(p) for p in db_social.lista_publications(conn, stato=stato)]


@router.get("/analytics")
def analytics(utente=Depends(richiede("social.view")), conn=Depends(ottieni_conn)):
    pubblicate = db_social.lista_publications(conn, stato="pubblicato")
    fallite = db_social.lista_publications(conn, stato="fallito")
    bloccati = db_social.lista_content(conn, stati=["BLOCKED"])
    snapshot = conn.execute(
        "SELECT pub.piattaforma, m.metriche, m.rilevato_at FROM social_metric_snapshots m "
        "JOIN social_publications pub ON pub.id = m.publication_id "
        "ORDER BY m.rilevato_at DESC LIMIT 50").fetchall()
    return {
        "kpi": {
            "pubblicazioni_ok": len(pubblicate),
            "pubblicazioni_fallite": len(fallite),
            "contenuti_bloccati": len(bloccati),
            "costo_anthropic_mese_eur": db_social.costo_periodo(conn, "anthropic"),
            "costo_openai_images_mese_eur": db_social.costo_periodo(conn, "openai_images"),
        },
        "metriche": [dict(r, metriche=json.loads(r["metriche"])) for r in snapshot],
    }


@router.get("/costs")
def costi(utente=Depends(richiede("social.view")), conn=Depends(ottieni_conn)):
    return {
        "mese_anthropic_eur": db_social.costo_periodo(conn, "anthropic"),
        "mese_openai_images_eur": db_social.costo_periodo(conn, "openai_images"),
        "budget_anthropic_eur": config.anthropic_monthly_budget_eur(),
        "budget_openai_images_eur": config.openai_image_monthly_budget_eur(),
        "voci": [_riga(r) for r in db_social.report_costi(conn, limit=200)],
    }


# --- Commenti ----------------------------------------------------------------

@router.get("/comments")
def elenco_commenti(utente=Depends(richiede("social.view")), conn=Depends(ottieni_conn)):
    return [_riga(c) for c in db_social.commenti(conn)]


@router.get("/replies")
def risposte_proposte(utente=Depends(richiede("social.view")), conn=Depends(ottieni_conn)):
    return [_riga(r) for r in db_social.reply_drafts(conn)]


@router.post("/replies/{reply_id}/decide")
def decidi_risposta(reply_id: str, stato: str,
                    utente=Depends(richiede("social.approve")),
                    conn=Depends(ottieni_conn)):
    if stato not in {"approvata", "rifiutata"}:
        raise HTTPException(status_code=422, detail="stato deve essere approvata|rifiutata")
    db_social.decidi_reply(conn, reply_id, stato, utente["id"])
    db_social.audit(conn, f"risposta_{stato}", utente_id=utente["id"],
                    oggetto_tipo="reply", oggetto_id=reply_id)
    return {"stato": stato}


# --- Calendario --------------------------------------------------------------

class NuovoPiano(BaseModel):
    settimana: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


@router.get("/calendar/{settimana}")
def calendario(settimana: str, utente=Depends(richiede("social.view")),
               conn=Depends(ottieni_conn)):
    return [_riga(v) for v in db_social.plan_settimana(conn, settimana)]


@router.post("/calendar/generate")
def genera_piano(corpo: NuovoPiano, utente=Depends(richiede("social.edit")),
                 conn=Depends(ottieni_conn)):
    job_id = db_social.crea_job(conn, "generate_week_plan",
                                {"settimana": corpo.settimana})
    return {"job_id": job_id}


# --- Sistema -----------------------------------------------------------------

@router.get("/system/status")
def stato_sistema(utente=Depends(richiede("social.view")), conn=Depends(ottieni_conn)):
    from social.integrations.instagram import InstagramAdapter
    from social.integrations.linkedin import LinkedInAdapter
    return {
        "modalita": publishing.modalita_effettiva(conn),
        "kill_switch": db_social.kill_switch_attivo(conn),
        "publishing_env": config.publishing_enabled_env(),
        "instagram": InstagramAdapter(conn).health_check(),
        "linkedin": LinkedInAdapter(conn).health_check(),
        "incidenti_aperti": [_riga(i) for i in db_social.incidenti_aperti(conn)],
        "job": {stato: len(db_social.lista_jobs(conn, stati=[stato]))
                for stato in ("pending", "running", "failed", "dead")},
        "ora_server": datetime.now(timezone.utc).isoformat(),
    }


class KillSwitch(BaseModel):
    attivo: bool


@router.post("/system/kill-switch")
def imposta_kill_switch(corpo: KillSwitch,
                        utente=Depends(richiede("social.publish")),
                        conn=Depends(ottieni_conn)):
    db_social.set_setting(conn, "kill_switch", corpo.attivo)
    db_social.audit(conn, "kill_switch", utente_id=utente["id"],
                    stato_dopo="attivo" if corpo.attivo else "spento")
    return {"kill_switch": corpo.attivo}


# --- Fonti / audit / configurazione ------------------------------------------

class NuovaFonte(BaseModel):
    dominio: str = Field(min_length=3, max_length=255)
    nome: Optional[str] = None


@router.get("/sources")
def fonti(utente=Depends(richiede("social.view")), conn=Depends(ottieni_conn)):
    return [_riga(d) for d in db_social.source_domains(conn, solo_attivi=False)]


@router.post("/sources", status_code=201)
def aggiungi_fonte(corpo: NuovaFonte, utente=Depends(richiede("social.admin")),
                   conn=Depends(ottieni_conn)):
    db_social.aggiungi_source_domain(conn, corpo.dominio, corpo.nome)
    db_social.audit(conn, "fonte_aggiunta", utente_id=utente["id"],
                    oggetto_tipo="source_domain", oggetto_id=corpo.dominio)
    return {"dominio": corpo.dominio}


@router.get("/audit")
def audit_log(utente=Depends(richiede("social.view")), conn=Depends(ottieni_conn)):
    return [_riga(r) for r in db_social.audit_recenti(conn)]


@router.get("/agent-runs")
def esecuzioni_agenti(utente=Depends(richiede("social.view")), conn=Depends(ottieni_conn)):
    return [_riga(r) for r in db_social.agent_runs_recenti(conn)]


class Impostazione(BaseModel):
    chiave: str
    valore: object = None


@router.get("/settings")
def impostazioni(utente=Depends(richiede("social.admin")), conn=Depends(ottieni_conn)):
    righe = conn.execute("SELECT chiave, valore FROM social_system_settings").fetchall()
    return {r["chiave"]: json.loads(r["valore"]) for r in righe}


@router.post("/settings")
def imposta(corpo: Impostazione, utente=Depends(richiede("social.admin")),
            conn=Depends(ottieni_conn)):
    if corpo.chiave not in db_social.SETTINGS_DEFAULT:
        raise HTTPException(status_code=422, detail=f"chiave sconosciuta: {corpo.chiave}")
    db_social.set_setting(conn, corpo.chiave, corpo.valore)
    db_social.audit(conn, "impostazione_modificata", utente_id=utente["id"],
                    oggetto_tipo="setting", oggetto_id=corpo.chiave)
    return {corpo.chiave: corpo.valore}
