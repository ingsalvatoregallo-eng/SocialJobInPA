"""
web.py — dashboard del modulo social: FastAPI + Jinja2, form HTML standard
(HTMX facoltativo: le pagine funzionano anche senza JS).

Autenticazione: login con le credenziali proprie (auth.py), sessione in
cookie HttpOnly/SameSite=Lax col token firmato; CSRF su ogni POST (token
derivato dalla sessione, vedi security.py); autorizzazione via permessi
social.* (db_social.ha_permesso) — admin/editor/reviewer/viewer.
"""

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import auth  # noqa: E402
from deps import ottieni_conn  # noqa: E402
from social import (  # noqa: E402
    agents, approvals, config, db_social, llm, publishing, security, state_machine,
)
from social.integrations.instagram import InstagramAdapter  # noqa: E402
from social.integrations.linkedin import LinkedInAdapter  # noqa: E402

router = APIRouter(prefix="/social", tags=["social-dashboard"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
log = logging.getLogger(__name__)

# --- Fasi semplificate -------------------------------------------------------
# I 16 stati tecnici della state machine (vedi state_machine.py) si
# raggruppano in poche fasi comprensibili per chi usa la dashboard (vedi
# docs/ux-redesign-brief.md, sez. 3): la UI mostra sempre la fase, mai lo
# stato tecnico grezzo come informazione primaria.
FASE_PER_STATO = {
    "IDEA": "idea",
    "RESEARCHING": "elaborazione", "DRAFTING": "elaborazione",
    "GENERATING_VISUAL": "elaborazione", "QUALITY_CHECK": "elaborazione",
    "RESEARCH_FAILED": "non_riuscita",
    "CANCELLED": "annullata",
    "BLOCKED": "bloccata",
    "AWAITING_APPROVAL": "revisione", "CHANGES_REQUESTED": "revisione",
    "APPROVED": "programmata", "SCHEDULED": "programmata",
    "PUBLISHING": "pubblicazione",
    "PUBLISHED": "pubblicata", "PARTIALLY_PUBLISHED": "pubblicata",
    "PUBLISH_FAILED": "fallita",
    "ARCHIVED": "archiviata",
}
FASE_LABEL = {
    "idea": "Idea", "elaborazione": "In elaborazione", "non_riuscita": "Non riuscita",
    "annullata": "Annullata", "bloccata": "Bloccata", "revisione": "Da rivedere",
    "programmata": "Programmata", "pubblicazione": "In pubblicazione",
    "pubblicata": "Pubblicata", "fallita": "Fallita", "archiviata": "Archiviata",
}
FASE_COLORE = {
    "idea": "grigio", "elaborazione": "viola", "non_riuscita": "arancio",
    "annullata": "grigio", "bloccata": "rosso", "revisione": "arancio",
    "programmata": "blu", "pubblicazione": "viola", "pubblicata": "verde",
    "fallita": "rosso", "archiviata": "grigio",
}


def fase_di(stato):
    return FASE_PER_STATO.get(stato, stato.lower())


templates.env.filters["fase"] = lambda stato: FASE_LABEL.get(fase_di(stato), stato)
templates.env.filters["fase_colore"] = lambda stato: FASE_COLORE.get(fase_di(stato), "grigio")
templates.env.filters["data_breve"] = lambda iso: (iso or "")[:16].replace("T", " ")
templates.env.globals["FASE_COLORE"] = FASE_COLORE

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


_MESI = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
        "agosto", "settembre", "ottobre", "novembre", "dicembre")
_GIORNI_NOME = ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")


def _ctx(request, sessione, conn, **extra):
    utente = sessione["utente"]
    permessi = db_social.permessi_di_ruolo(conn, utente["ruolo"])
    oggi = datetime.now(ZoneInfo(config.default_timezone()))
    ora_locale = f"{_GIORNI_NOME[oggi.weekday()].capitalize()} {oggi.day} {_MESI[oggi.month - 1]} {oggi.year}"
    return {
        "request": request, "utente": dict(utente),
        "permessi": permessi,
        "csrf": security.csrf_token(sessione["token"]),
        "kill_switch": db_social.kill_switch_attivo(conn),
        "modalita": publishing.modalita_effettiva(conn),
        "ora_locale": ora_locale,
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

def _parse_iso(valore):
    if not valore:
        return None
    try:
        return datetime.fromisoformat(valore.replace("Z", "+00:00"))
    except ValueError:
        return None


@router.get("/", response_class=HTMLResponse)
def home(request: Request, sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    tutti = db_social.lista_content(conn, limit=500)
    per_fase = {}
    for c in tutti:
        per_fase.setdefault(fase_di(c["stato"]), []).append(c)

    adesso = datetime.now(timezone.utc)
    programmati_7gg = [c for c in per_fase.get("programmata", [])
                      if _parse_iso(c["programmato_at"]) and _parse_iso(c["programmato_at"]) <= adesso + timedelta(days=7)]
    pubblicati_7gg = [c for c in per_fase.get("pubblicata", [])
                     if _parse_iso(c["aggiornato_at"]) and _parse_iso(c["aggiornato_at"]) >= adesso - timedelta(days=7)]
    problemi = per_fase.get("bloccata", []) + per_fase.get("fallita", [])

    approvazioni = db_social.approvals_in_attesa(conn)
    pubblicazioni_fallite = db_social.lista_publications(conn, stato="fallito")
    instagram = InstagramAdapter(conn).health_check()
    linkedin = LinkedInAdapter(conn).health_check()

    azioni_richieste = []
    if approvazioni:
        azioni_richieste.append({
            "testo": f"{len(approvazioni)} contenut{'o' if len(approvazioni) == 1 else 'i'} da approvare",
            "dettaglio": "In attesa di revisione umana.", "link": "/social/approvazioni",
            "cta": "Vai alla revisione", "colore": "arancio"})
    if pubblicazioni_fallite:
        prima = pubblicazioni_fallite[0]
        azioni_richieste.append({
            "testo": f"{len(pubblicazioni_fallite)} pubblicazion{'e' if len(pubblicazioni_fallite) == 1 else 'i'} fallit{'a' if len(pubblicazioni_fallite) == 1 else 'e'}",
            "dettaglio": f"Errore su {prima['piattaforma']}: {prima['errore'] or 'non specificato'}.",
            "link": f"/social/contenuti/{prima['content_id']}",
            "cta": "Gestisci errore", "colore": "rosso"})
    if not instagram["pronto"] or not linkedin["pronto"]:
        mancante = "Instagram" if not instagram["pronto"] else "LinkedIn"
        azioni_richieste.append({
            "testo": f"Integrazione {mancante} da completare",
            "dettaglio": "Serve per poter pubblicare davvero su questo canale.",
            "link": "/social/impostazioni", "cta": "Completa integrazione", "colore": "blu"})

    aggiornamenti = []
    for pub in db_social.lista_publications(conn, limit=6):
        if pub["stato"] == "pubblicato":
            aggiornamenti.append({"colore": "verde", "testo": f"Pubblicato su {pub['piattaforma']}",
                                  "dettaglio": pub["titolo"], "quando": pub["pubblicato_at"] or pub["creato_at"]})
        elif pub["stato"] == "fallito":
            aggiornamenti.append({"colore": "rosso", "testo": f"Fallita pubblicazione su {pub['piattaforma']}",
                                  "dettaglio": pub["titolo"], "quando": pub["creato_at"]})
    for app_ in approvazioni[:4]:
        aggiornamenti.append({"colore": "arancio", "testo": "In attesa di approvazione",
                              "dettaglio": app_["titolo"], "quando": app_["richiesto_at"]})
    aggiornamenti.sort(key=lambda a: a["quando"] or "", reverse=True)

    return templates.TemplateResponse(request, "home.html", _ctx(
        request, sessione, conn, pagina_attiva="panoramica",
        per_fase=per_fase,
        conteggio_elaborazione=len(per_fase.get("elaborazione", [])),
        conteggio_revisione=len(per_fase.get("revisione", [])),
        conteggio_programmati=len(programmati_7gg),
        conteggio_pubblicati=len(pubblicati_7gg),
        conteggio_problemi=len(problemi),
        azioni_richieste=azioni_richieste,
        ultimi_aggiornamenti=aggiornamenti[:6],
        instagram=instagram, linkedin=linkedin,
        publishing_env=config.publishing_enabled_env(),
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


_NOMI_GIORNI = ("Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom")


@router.get("/calendario", response_class=HTMLResponse)
def calendario(request: Request, settimana: Optional[str] = None,
               sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    if settimana:
        inizio = datetime.strptime(settimana, "%Y-%m-%d").date()
    else:
        inizio = _lunedi()
    settimana_iso = inizio.isoformat()
    voci = db_social.plan_settimana(conn, settimana_iso)

    giorni = []
    for offset, nome in enumerate(_NOMI_GIORNI):
        data = inizio + timedelta(days=offset)
        contenuti_giorno = []
        for v in voci:
            if v["giorno"] != data.isoformat() or v["content_id"] is None:
                continue
            content = db_social.get_content(conn, v["content_id"])
            if content:
                contenuti_giorno.append(content)
        giorni.append({"data": data.isoformat(), "nome": nome, "contenuti": contenuti_giorno})

    suggerimenti = [v for v in voci if v["stato"] == "suggerito"]

    return templates.TemplateResponse(request, "calendario.html", _ctx(
        request, sessione, conn, giorni=giorni, suggerimenti=suggerimenti,
        pillars=db_social.pillars(conn),
        corrente=settimana_iso,
        precedente=(inizio - timedelta(weeks=1)).isoformat(),
        successiva=(inizio + timedelta(weeks=1)).isoformat()))


@router.post("/calendario/genera")
def genera_piano(request: Request, settimana: str = Form(...), csrf: str = Form(None),
                 sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.edit")
    _verifica_csrf(sessione, csrf)
    db_social.crea_job(conn, "generate_week_plan", {"settimana": settimana})
    return RedirectResponse(f"/social/calendario?settimana={settimana}", status_code=303)


@router.post("/calendario/{entry_id}/accetta")
def accetta_suggerimento(request: Request, entry_id: str, tema: str = Form(...),
                         pillar: str = Form(None), obiettivo: str = Form(None),
                         giorno: str = Form(None), csrf: str = Form(None),
                         sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    """Accetta (eventualmente modificato: i campi arrivano dal form della
    card, editabile) un suggerimento: crea il contenuto vero e avvia subito
    la pipeline — accettare un tema equivale a dire "procedi"."""
    _richiedi(conn, sessione, "social.edit")
    _verifica_csrf(sessione, csrf)
    settimana = request.query_params.get("settimana") or _lunedi().isoformat()
    content_id = db_social.accetta_plan_entry(
        conn, entry_id, tema=tema.strip(), pillar_chiave=pillar or None,
        obiettivo=obiettivo or None, giorno=giorno or None,
        creato_da=sessione["utente"]["id"])
    if content_id is None:
        raise HTTPException(status_code=404, detail="Suggerimento non trovato o gia' accettato")
    db_social.audit(conn, "suggerimento_accettato", utente_id=sessione["utente"]["id"],
                    oggetto_tipo="content", oggetto_id=content_id)
    return RedirectResponse(f"/social/calendario?settimana={settimana}", status_code=303)


@router.post("/calendario/{entry_id}/scarta")
def scarta_suggerimento(request: Request, entry_id: str, csrf: str = Form(None),
                        sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.edit")
    _verifica_csrf(sessione, csrf)
    settimana = request.query_params.get("settimana") or _lunedi().isoformat()
    db_social.audit(conn, "suggerimento_scartato", utente_id=sessione["utente"]["id"],
                    oggetto_tipo="plan_entry", oggetto_id=entry_id)
    db_social.elimina_plan_entry(conn, entry_id)
    return RedirectResponse(f"/social/calendario?settimana={settimana}", status_code=303)


@router.post("/calendario/giorno/aggiungi")
def aggiungi_contenuto_giorno(request: Request, giorno: str = Form(...),
                              titolo: str = Form(...), pillar: str = Form(None),
                              csrf: str = Form(None),
                              sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    """Idea creata a mano e assegnata subito a un giorno preciso (a
    differenza dei suggerimenti del Supervisor, e' gia' una decisione
    dell'utente: nessun passaggio Accetta/Scarta)."""
    _richiedi(conn, sessione, "social.edit")
    _verifica_csrf(sessione, csrf)
    settimana = _lunedi(datetime.strptime(giorno, "%Y-%m-%d").date()).isoformat()
    content_id = db_social.crea_content(conn, titolo.strip(), pillar_chiave=pillar or None,
                                        creato_da=sessione["utente"]["id"])
    db_social.crea_plan_entry(conn, settimana, titolo.strip(), pillar_chiave=pillar or None,
                             content_id=content_id, giorno=giorno)
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


@router.get("/contenuti/nuovo", response_class=HTMLResponse)
def nuovo_contenuto_form(request: Request, sessione=Depends(utente_web),
                         conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.edit")
    return templates.TemplateResponse(request, "nuovo_contenuto.html", _ctx(
        request, sessione, conn, pillars=db_social.pillars(conn)))


@router.post("/contenuti/analizza-brief")
def analizza_brief(request: Request, brief: str = Form(...), csrf: str = Form(None),
                   sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    """Endpoint AJAX (JSON): interpreta il brief SUBITO, prima di creare il
    contenuto — mostra all'utente i filtri che l'AI ha capito (regione,
    competenze, posti minimi...) cosi' puo' correggere il testo se non
    riflettono l'intenzione, invece di scoprirlo solo a fine pipeline.
    Chiamata esplicita (bottone "Analizza brief"), non automatica ad ogni
    tasto: ogni chiamata e' comunque una richiesta AI reale, con relativo
    costo tracciato come le altre (vedi agents.interpreta_brief)."""
    _richiedi(conn, sessione, "social.edit")
    _verifica_csrf(sessione, csrf)
    brief = (brief or "").strip()
    if not brief:
        return {"nessun_criterio_specifico": True, "filtri": {}}
    try:
        criteri = agents.interpreta_brief(conn, brief)
    except llm.BudgetEsaurito as errore:
        log.warning("analisi brief fallita: %s", errore)
        periodo = "giornaliero" if "giornaliero" in str(errore) else "mensile"
        return {"errore": f"Budget AI {periodo} esaurito: l'analisi automatica del brief non è "
                           "disponibile fino al reset del budget. Puoi comunque salvare l'idea o "
                           "avviare la pipeline (potrebbe incontrare lo stesso limite).",
                "nessun_criterio_specifico": True, "filtri": {}}
    except llm.CircuitAperto as errore:
        log.warning("analisi brief fallita: %s", errore)
        return {"errore": "Il provider AI ha risposto con troppi errori di fila ed è stato "
                           "temporaneamente disattivato: riprova tra qualche minuto.",
                "nessun_criterio_specifico": True, "filtri": {}}
    except Exception as errore:
        log.warning("analisi brief fallita: %s", errore)
        return {"errore": "Analisi non disponibile al momento: riprova, o procedi comunque.",
                "nessun_criterio_specifico": True, "filtri": {}}
    return {"nessun_criterio_specifico": criteri.nessun_criterio_specifico,
            "filtri": agents._filtri_da_criteri(criteri)}


@router.post("/contenuti")
def crea_contenuto(request: Request, titolo: str = Form(...),
                   pillar: str = Form(None), obiettivo: str = Form(None),
                   brief: str = Form(None), azione: str = Form("salva_idea"),
                   csrf: str = Form(None),
                   sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.edit")
    _verifica_csrf(sessione, csrf)
    content_id = db_social.crea_content(conn, titolo.strip(), pillar_chiave=pillar or None,
                                        obiettivo=obiettivo or None,
                                        brief=(brief or "").strip() or None,
                                        creato_da=sessione["utente"]["id"])
    db_social.audit(conn, "contenuto_creato", utente_id=sessione["utente"]["id"],
                    oggetto_tipo="content", oggetto_id=content_id)
    if azione == "avvia":
        db_social.crea_job(conn, "pipeline", {"content_id": content_id})
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


@router.post("/contenuti/{content_id}/elimina")
def elimina_contenuto(request: Request, content_id: str, csrf: str = Form(None),
                      sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    """Cancellazione permanente: richiede social.admin (soglia piu' alta di
    social.edit, e' un'azione distruttiva) + conferma lato client (vedi
    template, onsubmit con confirm())."""
    _richiedi(conn, sessione, "social.admin")
    _verifica_csrf(sessione, csrf)
    if not db_social.elimina_content(conn, content_id, utente_id=sessione["utente"]["id"]):
        raise HTTPException(status_code=404, detail="Contenuto non trovato")
    return RedirectResponse("/social/contenuti", status_code=303)


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
def approvazioni(request: Request, content_id: str = None,
                 sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    coda = db_social.approvals_in_attesa(conn)
    selezionata = None
    if coda:
        selezionata = next((a for a in coda if a["content_id"] == content_id), None)
        if selezionata is None:
            selezionata = coda[0]
    dettaglio = None
    if selezionata:
        content = db_social.get_content(conn, selezionata["content_id"])
        dettaglio = {
            "content": content,
            "punteggi": json.loads(content["punteggi_rischio"]) if content["punteggi_rischio"] else None,
            "varianti": {v["piattaforma"]: v for v in db_social.varianti_di(conn, selezionata["content_id"])},
            "fatti": db_social.fatti_di(conn, selezionata["content_id"]),
        }
    return templates.TemplateResponse(request, "approvazioni.html", _ctx(
        request, sessione, conn, approvazioni=coda, selezionata=selezionata, dettaglio=dettaglio))


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
        publishing_env=config.publishing_enabled_env(),
        costo_anthropic=db_social.costo_periodo(conn, "anthropic"),
        budget_anthropic=config.anthropic_monthly_budget_eur(),
        costo_openai=db_social.costo_periodo(conn, "openai_images"),
        budget_openai=config.openai_image_monthly_budget_eur()))


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
