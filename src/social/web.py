"""
web.py — dashboard del modulo social: FastAPI + Jinja2, form HTML standard
(HTMX facoltativo: le pagine funzionano anche senza JS).

Autenticazione: login con le credenziali proprie (auth.py), sessione in
cookie HttpOnly/SameSite=Lax col token firmato; CSRF su ogni POST (token
derivato dalla sessione, vedi security.py); autorizzazione via permessi
social.* (db_social.ha_permesso) — admin/editor/reviewer/viewer.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import auth  # noqa: E402
from deps import ottieni_conn  # noqa: E402
from social import (  # noqa: E402
    agents, approvals, config, db_social, publishing, security, state_machine,
)
from social.integrations.instagram import InstagramAdapter  # noqa: E402
from social.integrations.linkedin import LinkedInAdapter  # noqa: E402

router = APIRouter(prefix="/social", tags=["social-dashboard"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_COOKIE = "social_session"


def _sessione_web(request: Request, conn):
    token = request.cookies.get(_COOKIE)
    if not token:
        return None
    payload = auth.verifica_token(token)
    if payload is None or not auth.payload_valido_per_sessione(payload):
        return None
    utente = db_social.utente_per_id(conn, payload.get("utente_id"))
    if utente is None or utente["stato"] != "attivo":
        return None
    return {"utente": utente, "token": token}


def utente_web(request: Request, conn=Depends(ottieni_conn)):
    sessione = _sessione_web(request, conn)
    if sessione is None:
        raise HTTPException(status_code=303, headers={"Location": "/social/login"})
    if not db_social.ha_permesso(conn, sessione["utente"], "social.view"):
        raise HTTPException(status_code=403, detail="Nessun accesso alla dashboard social")
    return sessione


def _richiedi(conn, sessione, permesso):
    if not db_social.ha_permesso(conn, sessione["utente"], permesso):
        raise HTTPException(status_code=403, detail=f"Permesso mancante: {permesso}")


def _verifica_csrf(sessione, csrf):
    if not security.csrf_valido(sessione["token"], csrf or ""):
        raise HTTPException(status_code=403, detail="Token CSRF non valido")


def _ctx(request, sessione, conn, **extra):
    utente = sessione["utente"]
    permessi = db_social.permessi_di_ruolo(conn, utente["ruolo"])
    return {
        "request": request, "utente": dict(utente),
        "permessi": permessi,
        "csrf": security.csrf_token(sessione["token"]),
        "kill_switch": db_social.kill_switch_attivo(conn),
        "modalita": publishing.modalita_effettiva(conn),
        **extra,
    }


# --- Login / logout ----------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"request": request, "errore": None})


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...),
          conn=Depends(ottieni_conn)):
    utente = db_social.utente_per_email(conn, email.strip().lower())
    if (utente is None or utente["stato"] != "attivo"
            or not auth.verifica_password(password, utente["password_hash"] or "")):
        return templates.TemplateResponse(
            request, "login.html", {"errore": "Credenziali non valide"},
            status_code=401)
    if not db_social.ha_permesso(conn, utente, "social.view"):
        return templates.TemplateResponse(
            request, "login.html",
            {"errore": "Il tuo ruolo non ha accesso alla dashboard social"},
            status_code=403)
    token = auth.crea_token({"utente_id": utente["id"], "scope": "session"})
    risposta = RedirectResponse("/social/", status_code=303)
    risposta.set_cookie(_COOKIE, token, httponly=True, samesite="lax",
                        secure=config.base_url().startswith("https"),
                        max_age=12 * 3600, path="/social")
    db_social.audit(conn, "login_dashboard", utente_id=utente["id"])
    return risposta


@router.get("/logout")
def logout():
    risposta = RedirectResponse("/social/login", status_code=303)
    risposta.delete_cookie(_COOKIE, path="/social")
    return risposta


# --- Dashboard (home / stato sistema / checklist) ----------------------------

@router.get("/", response_class=HTMLResponse)
def home(request: Request, sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    contenuti = db_social.lista_content(conn, limit=8)
    return templates.TemplateResponse(request, "home.html", _ctx(
        request, sessione, conn,
        instagram=InstagramAdapter(conn).health_check(),
        linkedin=LinkedInAdapter(conn).health_check(),
        publishing_env=config.publishing_enabled_env(),
        incidenti=db_social.incidenti_aperti(conn),
        approvazioni=db_social.approvals_in_attesa(conn),
        contenuti=contenuti,
        job_stati={s: len(db_social.lista_jobs(conn, stati=[s]))
                   for s in ("pending", "running", "dead")},
        costo_anthropic=db_social.costo_periodo(conn, "anthropic"),
        budget_anthropic=config.anthropic_monthly_budget_eur(),
        costo_openai=db_social.costo_periodo(conn, "openai_images"),
        budget_openai=config.openai_image_monthly_budget_eur(),
    ))


@router.post("/kill-switch")
def kill_switch(request: Request, attivo: str = Form(...), csrf: str = Form(None),
                sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.publish")
    _verifica_csrf(sessione, csrf)
    valore = attivo == "1"
    db_social.set_setting(conn, "kill_switch", valore)
    db_social.audit(conn, "kill_switch", utente_id=sessione["utente"]["id"],
                    stato_dopo="attivo" if valore else "spento")
    return RedirectResponse("/social/", status_code=303)


# --- Calendario --------------------------------------------------------------

def _lunedi(data=None):
    data = data or datetime.now(timezone.utc).date()
    return data - timedelta(days=data.weekday())


@router.get("/calendario", response_class=HTMLResponse)
def calendario(request: Request, settimana: Optional[str] = None,
               sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    if settimana:
        inizio = datetime.strptime(settimana, "%Y-%m-%d").date()
    else:
        inizio = _lunedi()
    settimane = [(inizio + timedelta(weeks=delta)).isoformat() for delta in range(-1, 4)]
    voci = {s: db_social.plan_settimana(conn, s) for s in settimane}
    return templates.TemplateResponse(request, "calendario.html", _ctx(
        request, sessione, conn, settimane=settimane, voci=voci,
        corrente=inizio.isoformat(),
        precedente=(inizio - timedelta(weeks=1)).isoformat(),
        successiva=(inizio + timedelta(weeks=1)).isoformat()))


@router.post("/calendario/genera")
def genera_piano(request: Request, settimana: str = Form(...), csrf: str = Form(None),
                 sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.edit")
    _verifica_csrf(sessione, csrf)
    db_social.crea_job(conn, "generate_week_plan", {"settimana": settimana})
    return RedirectResponse(f"/social/calendario?settimana={settimana}", status_code=303)


# --- Contenuti ---------------------------------------------------------------

_GRUPPI_STATO = {
    "idee": ["IDEA", "RESEARCHING", "RESEARCH_FAILED"],
    "bozze": ["DRAFTING", "DRAFT_READY", "GENERATING_VISUAL", "QUALITY_CHECK",
              "CHANGES_REQUESTED"],
    "approvazioni": ["AWAITING_APPROVAL"],
    "programmati": ["APPROVED", "SCHEDULED", "PUBLISHING"],
    "pubblicati": ["PUBLISHED", "PARTIALLY_PUBLISHED"],
    "errori": ["PUBLISH_FAILED", "BLOCKED"],
    "archivio": ["CANCELLED", "ARCHIVED"],
}


@router.get("/contenuti", response_class=HTMLResponse)
def contenuti(request: Request, gruppo: str = "idee",
              sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    stati = _GRUPPI_STATO.get(gruppo, None)
    return templates.TemplateResponse(request, "contenuti.html", _ctx(
        request, sessione, conn, gruppo=gruppo, gruppi=list(_GRUPPI_STATO),
        contenuti=db_social.lista_content(conn, stati=stati),
        pillars=db_social.pillars(conn)))


@router.post("/contenuti")
def crea_contenuto(request: Request, titolo: str = Form(...),
                   pillar: str = Form(None), brief: str = Form(None),
                   csrf: str = Form(None),
                   sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.edit")
    _verifica_csrf(sessione, csrf)
    content_id = db_social.crea_content(conn, titolo.strip(), pillar_chiave=pillar or None,
                                        brief=(brief or "").strip() or None,
                                        creato_da=sessione["utente"]["id"])
    db_social.audit(conn, "contenuto_creato", utente_id=sessione["utente"]["id"],
                    oggetto_tipo="content", oggetto_id=content_id)
    return RedirectResponse(f"/social/contenuti/{content_id}", status_code=303)


@router.get("/contenuti/{content_id}", response_class=HTMLResponse)
def contenuto(request: Request, content_id: str,
              sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    content = db_social.get_content(conn, content_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Contenuto non trovato")
    punteggi = json.loads(content["punteggi_rischio"]) if content["punteggi_rischio"] else None
    return templates.TemplateResponse(request, "contenuto.html", _ctx(
        request, sessione, conn, c=content, punteggi=punteggi,
        varianti={v["piattaforma"]: v for v in db_social.varianti_di(conn, content_id)},
        assets=db_social.asset_di(conn, content_id),
        fatti=db_social.fatti_di(conn, content_id),
        pubblicazioni=db_social.publications_di(conn, content_id),
        approvazione=db_social.approval_aperta_di(conn, content_id)))


@router.post("/contenuti/{content_id}/pipeline")
def avvia_pipeline(request: Request, content_id: str, csrf: str = Form(None),
                   sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.edit")
    _verifica_csrf(sessione, csrf)
    db_social.crea_job(conn, "pipeline", {"content_id": content_id})
    return RedirectResponse(f"/social/contenuti/{content_id}", status_code=303)


@router.post("/contenuti/{content_id}/pubblica")
def pubblica_ora(request: Request, content_id: str, csrf: str = Form(None),
                 sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.publish")
    _verifica_csrf(sessione, csrf)
    try:
        publishing.pubblica_contenuto(conn, content_id, utente_id=sessione["utente"]["id"])
    except state_machine.TransizioneNonValida as errore:
        raise HTTPException(status_code=409, detail=str(errore))
    return RedirectResponse(f"/social/contenuti/{content_id}", status_code=303)


@router.get("/asset/{asset_id}")
def anteprima_asset(asset_id: str, sessione=Depends(utente_web),
                    conn=Depends(ottieni_conn)):
    riga = conn.execute("SELECT * FROM social_media_assets WHERE id = ?",
                        (asset_id,)).fetchone()
    if riga is None:
        raise HTTPException(status_code=404)
    percorso = Path(riga["percorso"]).resolve()
    radice = config.asset_storage_path().resolve()
    if radice not in percorso.parents and percorso != radice:
        raise HTTPException(status_code=403, detail="percorso fuori dallo storage asset")
    if not percorso.exists():
        raise HTTPException(status_code=404)
    return FileResponse(percorso, media_type="image/png")


# --- Approvazioni ------------------------------------------------------------

@router.get("/approvazioni", response_class=HTMLResponse)
def approvazioni(request: Request, sessione=Depends(utente_web),
                 conn=Depends(ottieni_conn)):
    return templates.TemplateResponse(request, "approvazioni.html", _ctx(
        request, sessione, conn, approvazioni=db_social.approvals_in_attesa(conn)))


@router.post("/approvazioni/{approval_id}")
def decidi_approvazione(request: Request, approval_id: str,
                        azione: str = Form(...), motivo: str = Form(None),
                        csrf: str = Form(None),
                        sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.approve")
    _verifica_csrf(sessione, csrf)
    utente_id = sessione["utente"]["id"]
    try:
        if azione == "approva":
            approvals.approva(conn, approval_id, utente_id, motivo)
        elif azione == "rifiuta":
            approvals.rifiuta(conn, approval_id, utente_id, motivo)
        elif azione == "modifiche":
            if not (motivo or "").strip():
                raise HTTPException(status_code=422,
                                    detail="La richiesta di modifiche richiede un motivo")
            approvals.richiedi_modifiche(conn, approval_id, utente_id, motivo)
        else:
            raise HTTPException(status_code=422, detail="azione sconosciuta")
    except ValueError as errore:
        raise HTTPException(status_code=404, detail=str(errore))
    return RedirectResponse("/social/approvazioni", status_code=303)


# --- Pubblicazioni / commenti / analytics / log ------------------------------

@router.get("/pubblicazioni", response_class=HTMLResponse)
def pubblicazioni(request: Request, sessione=Depends(utente_web),
                  conn=Depends(ottieni_conn)):
    return templates.TemplateResponse(request, "pubblicazioni.html", _ctx(
        request, sessione, conn,
        programmati=db_social.lista_content(conn, stati=["APPROVED", "SCHEDULED"]),
        in_corso=db_social.lista_publications(conn, stato="in_corso"),
        pubblicate=db_social.lista_publications(conn, stato="pubblicato"),
        fallite=db_social.lista_publications(conn, stato="fallito")))


@router.get("/commenti", response_class=HTMLResponse)
def commenti(request: Request, sessione=Depends(utente_web),
             conn=Depends(ottieni_conn)):
    return templates.TemplateResponse(request, "commenti.html", _ctx(
        request, sessione, conn, commenti=db_social.commenti(conn),
        risposte=db_social.reply_drafts(conn)))


@router.post("/commenti/risposte/{reply_id}")
def decidi_risposta(request: Request, reply_id: str, azione: str = Form(...),
                    csrf: str = Form(None),
                    sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.approve")
    _verifica_csrf(sessione, csrf)
    stato = "approvata" if azione == "approva" else "rifiutata"
    db_social.decidi_reply(conn, reply_id, stato, sessione["utente"]["id"])
    db_social.audit(conn, f"risposta_{stato}", utente_id=sessione["utente"]["id"],
                    oggetto_tipo="reply", oggetto_id=reply_id)
    return RedirectResponse("/social/commenti", status_code=303)


@router.get("/analytics", response_class=HTMLResponse)
def analytics(request: Request, sessione=Depends(utente_web),
              conn=Depends(ottieni_conn)):
    snapshot = conn.execute(
        "SELECT pub.piattaforma, c.titolo, m.metriche, m.rilevato_at "
        "FROM social_metric_snapshots m "
        "JOIN social_publications pub ON pub.id = m.publication_id "
        "JOIN social_content c ON c.id = pub.content_id "
        "ORDER BY m.rilevato_at DESC LIMIT 50").fetchall()
    return templates.TemplateResponse(request, "analytics.html", _ctx(
        request, sessione, conn,
        metriche=[dict(r, metriche=json.loads(r["metriche"])) for r in snapshot],
        pubblicate=len(db_social.lista_publications(conn, stato="pubblicato")),
        fallite=len(db_social.lista_publications(conn, stato="fallito")),
        bloccati=len(db_social.lista_content(conn, stati=["BLOCKED"])),
        costo_anthropic=db_social.costo_periodo(conn, "anthropic"),
        budget_anthropic=config.anthropic_monthly_budget_eur(),
        costo_openai=db_social.costo_periodo(conn, "openai_images"),
        budget_openai=config.openai_image_monthly_budget_eur(),
        costi=db_social.report_costi(conn, limit=100)))


@router.get("/log", response_class=HTMLResponse)
def log_pagina(request: Request, sessione=Depends(utente_web),
               conn=Depends(ottieni_conn)):
    return templates.TemplateResponse(request, "log.html", _ctx(
        request, sessione, conn,
        agent_runs=db_social.agent_runs_recenti(conn, limit=50),
        audit=db_social.audit_recenti(conn, limit=50),
        email=db_social.email_recenti(conn, limit=30),
        jobs=db_social.lista_jobs(conn, limit=50)))


# --- OAuth account social (Instagram/LinkedIn) -------------------------------
# Stessa convenzione del login social esistente (src/oauth.py, src/api.py
# /api/auth/{provider}/login|callback): state = token firmato con "scopo"
# dedicato, verificato al ritorno dal provider prima di fidarsi del code.

DURATA_STATO_OAUTH_SOCIAL_SECONDI = 600  # 10 minuti: il tempo di completare il consenso


@router.get("/oauth/{provider}/start")
def oauth_start(request: Request, provider: str, sessione=Depends(utente_web),
                conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.admin")
    if provider not in db_social.PIATTAFORME:
        raise HTTPException(status_code=404, detail=f"provider sconosciuto: {provider}")
    stato = auth.crea_token(
        {"scopo": "social_link", "provider": provider, "utente_id": sessione["utente"]["id"]},
        durata_secondi=DURATA_STATO_OAUTH_SOCIAL_SECONDI)
    adapter = InstagramAdapter(conn) if provider == "instagram" else LinkedInAdapter(conn)
    return RedirectResponse(adapter.oauth_authorize_url(stato), status_code=303)


@router.get("/oauth/{provider}/callback")
def oauth_callback(request: Request, provider: str, code: Optional[str] = None,
                   state: Optional[str] = None, error: Optional[str] = None,
                   conn=Depends(ottieni_conn)):
    if provider not in db_social.PIATTAFORME:
        raise HTTPException(status_code=404, detail=f"provider sconosciuto: {provider}")
    if error:
        raise HTTPException(status_code=400,
                            detail=f"Autorizzazione rifiutata dal provider: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="callback OAuth incompleto")
    payload = auth.verifica_token(state)
    if (payload is None or payload.get("scopo") != "social_link"
            or payload.get("provider") != provider):
        raise HTTPException(status_code=400,
                            detail="stato OAuth non valido o scaduto: ripeti l'autorizzazione")
    utente_id = payload.get("utente_id")
    account = db_social.account_per_piattaforma(conn, provider)
    if account is None:
        raise HTTPException(status_code=404, detail="account non configurato")

    try:
        if provider == "instagram":
            adapter = InstagramAdapter(conn)
            token = adapter.completa_oauth(code)
            db_social.salva_oauth_token(conn, account["id"], "access",
                                        security.encrypt_token(token))
            # L'ultimo requisito della checklist (immagini via URL pubblico)
            # non puo' mai essere vero in locale: l'account resta
            # "in_configurazione" finche' non sara' esposto pubblicamente
            # (vedi docs/deployment-future.md), il token e' comunque salvato.
            nuovo_stato = "in_configurazione"
        else:
            adapter = LinkedInAdapter(conn)
            token, expires_in = adapter.completa_oauth(code)
            scadenza = ((datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
                       if expires_in else None)
            db_social.salva_oauth_token(conn, account["id"], "access",
                                        security.encrypt_token(token), scadenza_at=scadenza)
            amministratore = adapter.verifica_privilegi_admin()
            salute = adapter.health_check()
            if salute["pronto"] and not amministratore:
                db_social.registra_incidente(
                    conn, "publishing",
                    "LinkedIn: l'account autorizzato non e' amministratore "
                    "della Pagina configurata (LINKEDIN_ORGANIZATION_URN)")
                nuovo_stato = "in_configurazione"
            else:
                nuovo_stato = "verificato" if salute["pronto"] else "in_configurazione"
        db_social.aggiorna_account(conn, account["id"], stato=nuovo_stato)
        db_social.audit(conn, "oauth_completato", utente_id=utente_id,
                        oggetto_tipo="account", oggetto_id=account["id"],
                        stato_dopo=nuovo_stato, dettagli={"provider": provider})
    except Exception as errore:
        db_social.registra_incidente(conn, "publishing", f"OAuth {provider} fallito: {errore}")
        db_social.audit(conn, "oauth_fallito", utente_id=utente_id,
                        oggetto_tipo="account", oggetto_id=account["id"],
                        dettagli={"provider": provider, "errore": str(errore)})
        raise HTTPException(status_code=502,
                            detail=f"Autorizzazione {provider} fallita: {errore}")
    return RedirectResponse("/social/impostazioni", status_code=303)


# --- Impostazioni ------------------------------------------------------------

@router.get("/impostazioni", response_class=HTMLResponse)
def impostazioni(request: Request, sessione=Depends(utente_web),
                 conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.admin")
    prompt_versioni = conn.execute(
        "SELECT nome, versione, hash, creato_at FROM social_prompt_versions "
        "ORDER BY nome, creato_at DESC").fetchall()
    utenti_social = conn.execute(
        "SELECT id, email, ruolo, stato FROM utenti "
        "WHERE ruolo IN ('admin', 'editor', 'reviewer', 'viewer') ORDER BY email").fetchall()
    settings = {r["chiave"]: r["valore"] for r in conn.execute(
        "SELECT chiave, valore FROM social_system_settings").fetchall()}
    return templates.TemplateResponse(request, "impostazioni.html", _ctx(
        request, sessione, conn,
        accounts=db_social.lista_accounts(conn),
        instagram=InstagramAdapter(conn).health_check(),
        linkedin=LinkedInAdapter(conn).health_check(),
        fonti=db_social.source_domains(conn, solo_attivi=False),
        settings=settings, prompt_versioni=prompt_versioni,
        utenti_social=utenti_social,
        publishing_env=config.publishing_enabled_env()))


@router.post("/impostazioni/fonti")
def aggiungi_fonte(request: Request, dominio: str = Form(...), nome: str = Form(None),
                   csrf: str = Form(None),
                   sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.admin")
    _verifica_csrf(sessione, csrf)
    db_social.aggiungi_source_domain(conn, dominio.strip(), (nome or "").strip() or None)
    db_social.audit(conn, "fonte_aggiunta", utente_id=sessione["utente"]["id"],
                    oggetto_tipo="source_domain", oggetto_id=dominio)
    return RedirectResponse("/social/impostazioni", status_code=303)


@router.post("/impostazioni/fonti/{dominio}/toggle")
def toggle_fonte(request: Request, dominio: str, csrf: str = Form(None),
                 sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.admin")
    _verifica_csrf(sessione, csrf)
    riga = conn.execute("SELECT attivo FROM social_source_domains WHERE dominio = ?",
                        (dominio,)).fetchone()
    if riga is None:
        raise HTTPException(status_code=404)
    db_social.imposta_source_domain(conn, dominio, not riga["attivo"])
    return RedirectResponse("/social/impostazioni", status_code=303)


@router.post("/impostazioni/revisori")
def imposta_revisori(request: Request, emails: str = Form(""), csrf: str = Form(None),
                     sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.admin")
    _verifica_csrf(sessione, csrf)
    lista = [e.strip() for e in emails.replace(";", ",").split(",") if e.strip()]
    db_social.set_setting(conn, "revisori_email", lista)
    db_social.audit(conn, "revisori_aggiornati", utente_id=sessione["utente"]["id"],
                    dettagli={"quanti": len(lista)})
    return RedirectResponse("/social/impostazioni", status_code=303)


@router.post("/impostazioni/account/{account_id}/publishing")
def toggle_account_publishing(request: Request, account_id: str, csrf: str = Form(None),
                              sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.publish")
    _verifica_csrf(sessione, csrf)
    riga = conn.execute("SELECT publishing_enabled FROM social_accounts WHERE id = ?",
                        (account_id,)).fetchone()
    if riga is None:
        raise HTTPException(status_code=404)
    db_social.aggiorna_account(conn, account_id,
                               publishing_enabled=0 if riga["publishing_enabled"] else 1)
    db_social.audit(conn, "account_publishing_toggle",
                    utente_id=sessione["utente"]["id"],
                    oggetto_tipo="account", oggetto_id=account_id)
    return RedirectResponse("/social/impostazioni", status_code=303)
