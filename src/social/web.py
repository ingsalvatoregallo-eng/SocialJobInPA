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
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import auth  # noqa: E402
from deps import ottieni_conn  # noqa: E402
from social import (  # noqa: E402
    agents, approvals, config, db_social, jobinpa_client, publishing, security, state_machine,
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


# Stato tecnico degli account social (social_accounts.stato): mostrato
# finora come stringa grezza del DB ("non_configurato") invece di
# un'etichetta leggibile, unico posto della dashboard rimasto cosi' dopo
# il resto del restyling.
STATO_ACCOUNT_LABEL = {
    "non_configurato": "Da configurare", "in_configurazione": "In configurazione",
    "verificato": "Verificato", "errore": "Errore",
}
STATO_ACCOUNT_COLORE = {
    "non_configurato": "grigio", "in_configurazione": "arancio",
    "verificato": "verde", "errore": "rosso",
}

templates.env.filters["fase"] = lambda stato: FASE_LABEL.get(fase_di(stato), stato)
templates.env.filters["fase_colore"] = lambda stato: FASE_COLORE.get(fase_di(stato), "grigio")


def _tojson(valore):
    """Non e' Flask: Jinja2Templates di FastAPI non registra 'tojson' da
    solo. Serve per incorporare dati del server (es. nomi di categorie
    scelti dall'utente in Categorie, possono contenere apici/caratteri
    speciali) in un array JS dentro <script>, senza spezzare la sintassi
    ne' aprire a injection (vedi nuovo_contenuto.html, percorso guidato a
    step: CATEGORIE/PILLARS costruiti cosi'). Escape di <, >, & per non
    rischiare di chiudere il tag <script> con un nome tipo '</script>'."""
    return (json.dumps(valore, ensure_ascii=False)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


templates.env.filters["tojson"] = _tojson


def _data_breve(iso):
    """Mostrava finora l'ISO grezzo troncato — quasi sempre UTC (vedi
    programmato_at/creato_at/richiesto_at ecc.) senza mai convertirlo nel
    fuso locale: un orario futuro corretto (es. le 18:00 di Roma, salvato
    come 16:00 UTC) appariva "gia' passato" a chi confrontava con
    l'orologio di casa (bug segnalato dall'utente su una programmazione
    reale)."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso[:16].replace("T", " ")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(config.default_timezone())).strftime("%Y-%m-%d %H:%M")


templates.env.filters["data_breve"] = _data_breve
templates.env.filters["stato_account"] = lambda stato: STATO_ACCOUNT_LABEL.get(stato, stato)
templates.env.filters["stato_account_colore"] = lambda stato: STATO_ACCOUNT_COLORE.get(stato, "grigio")
templates.env.globals["FASE_COLORE"] = FASE_COLORE

MODALITA_SPIEGAZIONE = {
    "mock": "Nessuna chiamata esterna: ne' l'AI ne' la pubblicazione sono reali. Solo per test offline.",
    "sandbox": "L'AI genera contenuti veri, ma la pubblicazione sui social e' SEMPRE simulata "
              "(nessun post reale, il link \"apri\" non porta a nulla di vero).",
    "production": "Pubblicazione reale sui social configurati (richiede account verificati, "
                  "GLOBAL_PUBLISHING_ENABLED=true e kill switch spento).",
}
templates.env.filters["modalita_spiegazione"] = lambda m: MODALITA_SPIEGAZIONE.get(m, "")


def _iso_a_locale_input(iso):
    """ISO UTC (come salvato in programmato_at) -> 'YYYY-MM-DDTHH:MM' nel
    fuso locale, il formato richiesto da <input type="datetime-local">."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(config.default_timezone())).strftime("%Y-%m-%dT%H:%M")


templates.env.filters["iso_a_locale_input"] = _iso_a_locale_input


def _hashtags_testo(valore):
    """hashtags e' salvato come JSON TEXT (vedi db_social.salva_variante):
    senza questo filtro il template mostrerebbe la stringa JSON grezza
    invece di un elenco leggibile (bug visibile in produzione, segnalato
    dall'utente)."""
    if isinstance(valore, str):
        try:
            valore = json.loads(valore)
        except json.JSONDecodeError:
            return valore
    if isinstance(valore, (list, tuple)):
        return " ".join(str(v) for v in valore if v)
    return valore or ""


templates.env.filters["hashtags_testo"] = _hashtags_testo

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
        # Contatori nel menu laterale (base.html): senza, l'unico modo di
        # scoprire che c'e' qualcosa da revisionare/pubblicare era entrare
        # per caso nella pagina giusta (segnalato dall'utente).
        "revisione_in_attesa": len(db_social.approvals_in_attesa(conn)),
        # APPROVED/SCHEDULED = non ancora pubblicato (stessa lista della
        # tabella "Programmati" in Pubblicazioni): un pubblicato non deve
        # piu' contare, un fallito si' (richiede attenzione).
        "pubblicazioni_da_gestire": (
            len(db_social.lista_content(conn, stati=["APPROVED", "SCHEDULED"]))
            + len(db_social.lista_publications(conn, stato="fallito"))),
        # Stesso stato del gruppo "errori" in contenuti() (_GRUPPI_STATO):
        # un contenuto con pubblicazione fallita era invisibile finche' non
        # si cliccava per caso sulla tab giusta in Contenuti (segnalato
        # dall'utente: vuole un segnale nel menu laterale, non solo un
        # contatore per i pubblicati). BLOCKED non e' qui: e' un giudizio
        # dell'AI da rivedere, gia' contato in "revisione_in_attesa" sopra
        # (segnalato dall'utente: un bollino rosso non e' un errore tecnico).
        "contenuti_in_errore": len(db_social.lista_content(conn, stati=["PUBLISH_FAILED"])),
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
def calendario(request: Request, settimana: Optional[str] = None, errore: Optional[str] = None,
               sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    if settimana:
        inizio = datetime.strptime(settimana, "%Y-%m-%d").date()
    else:
        inizio = _lunedi()
    settimana_iso = inizio.isoformat()
    voci = db_social.plan_settimana(conn, settimana_iso)

    # Contenuti VERI con una data di pubblicazione (programmata o gia'
    # avvenuta), raggruppati per giorno LOCALE: senza questo, un contenuto
    # creato da "Nuovo contenuto" (non da un suggerimento del piano AI
    # accettato) non compariva mai nel calendario una volta programmato o
    # pubblicato, anche se e' l'informazione piu' importante da vedere qui.
    fuso = ZoneInfo(config.default_timezone())
    programmati_per_giorno = {}
    for content in db_social.content_con_programmato_at(conn):
        data_locale = datetime.fromisoformat(content["programmato_at"]).astimezone(fuso).date()
        programmati_per_giorno.setdefault(data_locale.isoformat(), []).append(content)

    giorni = []
    for offset, nome in enumerate(_NOMI_GIORNI):
        data = inizio + timedelta(days=offset)
        contenuti_giorno = []
        id_visti = set()
        for v in voci:
            if v["giorno"] != data.isoformat() or v["content_id"] is None:
                continue
            content = db_social.get_content(conn, v["content_id"])
            if content and content["id"] not in id_visti:
                contenuti_giorno.append(content)
                id_visti.add(content["id"])
        for content in programmati_per_giorno.get(data.isoformat(), []):
            if content["id"] not in id_visti:
                contenuti_giorno.append(content)
                id_visti.add(content["id"])
        giorni.append({"data": data.isoformat(), "nome": nome, "contenuti": contenuti_giorno})

    suggerimenti = [v for v in voci if v["stato"] == "suggerito"]

    conteggio_settimane = db_social.conteggio_suggerimenti_per_settimana(conn)
    altre_settimane_suggerimenti = sorted(
        (s, n) for s, n in conteggio_settimane.items() if s != settimana_iso)

    generazione_in_corso = db_social.job_in_corso(conn, "generate_week_plan", settimana_iso)
    categorie = db_social.lista_categorie(conn)

    return templates.TemplateResponse(request, "calendario.html", _ctx(
        request, sessione, conn, giorni=giorni, suggerimenti=suggerimenti,
        pillars=db_social.pillars(conn),
        categorie=categorie,
        altre_settimane_suggerimenti=altre_settimane_suggerimenti,
        corrente=settimana_iso,
        generazione_in_corso=generazione_in_corso,
        nessuna_categoria_idonea=(errore == "nessuna_categoria"),
        precedente=(inizio - timedelta(weeks=1)).isoformat(),
        successiva=(inizio + timedelta(weeks=1)).isoformat()))


@router.post("/calendario/genera")
def genera_piano(request: Request, settimana: str = Form(...), csrf: str = Form(None),
                 sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.edit")
    _verifica_csrf(sessione, csrf)
    # Verificato SUBITO (non nel job in background): senza almeno una
    # categoria idonea (vedi agents.categorie_idonee_supervisor) non c'e'
    # nulla di sensato da generare, e un job in coda destinato a fallire
    # 5 volte con backoff (prima di finire "morto") sarebbe un errore
    # silenzioso, mai visibile qui come i job falliti in passato
    # (segnalato dall'utente: "Genera 3 temi" deve riusare le Categorie,
    # non inventare temi liberi).
    if not agents.categorie_idonee_supervisor(conn):
        return RedirectResponse(
            f"/social/calendario?settimana={settimana}&errore=nessuna_categoria", status_code=303)
    db_social.crea_job(conn, "generate_week_plan", {"settimana": settimana})
    return RedirectResponse(f"/social/calendario?settimana={settimana}", status_code=303)


@router.post("/calendario/{entry_id}/accetta")
def accetta_suggerimento(request: Request, entry_id: str, tema: str = Form(...),
                         pillar: str = Form(None), obiettivo: str = Form(None),
                         giorno: str = Form(None), categoria_id: str = Form(None),
                         csrf: str = Form(None),
                         sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    """Accetta (eventualmente modificato: i campi arrivano dal form della
    card, editabile) un suggerimento: crea il contenuto vero in IDEA, SENZA
    avviare la pipeline. Prima la avviava subito ("accettare un tema
    equivale a dire procedi"): la pipeline arrivava a GENERATING_VISUAL in
    meno di un minuto, cosi' in fretta che l'utente non faceva in tempo a
    leggere il tema/brief precompilato ne' a correggerlo prima che partisse
    la ricerca/generazione immagini — il form di modifica appariva per
    pochi secondi per poi sparire dietro al refresh automatico della
    pagina "in corso" (bug segnalato dall'utente). Ora l'utente atterra
    sulla scheda del contenuto in IDEA, rivede/modifica tema, brief e
    filtri con calma, e clicca lui "Avvia pipeline agenti" quando e'
    pronto — stesso identico passaggio in piu' richiesto per un contenuto
    creato da "Nuovo contenuto" manuale. categoria_id puo' sovrascrivere
    quella scelta dal Supervisor (menu a tendina nella card, vedi
    calendario.html) prima di trasformarla in contenuto vero."""
    _richiedi(conn, sessione, "social.edit")
    _verifica_csrf(sessione, csrf)
    content_id = db_social.accetta_plan_entry(
        conn, entry_id, tema=tema.strip(), pillar_chiave=pillar or None,
        obiettivo=obiettivo or None, giorno=giorno or None,
        categoria_id=categoria_id or None,
        creato_da=sessione["utente"]["id"], avvia_pipeline=False)
    if content_id is None:
        raise HTTPException(status_code=404, detail="Suggerimento non trovato o gia' accettato")
    db_social.audit(conn, "suggerimento_accettato", utente_id=sessione["utente"]["id"],
                    oggetto_tipo="content", oggetto_id=content_id)
    return RedirectResponse(f"/social/contenuti/{content_id}", status_code=303)


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
    # canali=canali_abilitati(conn): stesso fix di accetta_plan_entry, mai
    # il default "tutte le piattaforme" di crea_content (segnalato
    # dall'utente per LinkedIn, stesso bug su questo secondo percorso di
    # creazione che lo bypassava allo stesso modo).
    content_id = db_social.crea_content(conn, titolo.strip(), pillar_chiave=pillar or None,
                                        creato_da=sessione["utente"]["id"],
                                        canali=db_social.canali_abilitati(conn))
    db_social.crea_plan_entry(conn, settimana, titolo.strip(), pillar_chiave=pillar or None,
                             content_id=content_id, giorno=giorno)
    return RedirectResponse(f"/social/calendario?settimana={settimana}", status_code=303)


# --- Contenuti ---------------------------------------------------------------

_GRUPPI_STATO = {
    "idee": ["IDEA", "RESEARCHING", "RESEARCH_FAILED"],
    "bozze": ["DRAFTING", "DRAFT_READY", "GENERATING_VISUAL", "QUALITY_CHECK",
              "CHANGES_REQUESTED"],
    # BLOCKED (bollino rosso del Quality & Risk Agent) sta con le
    # approvazioni, non con gli errori: e' un giudizio dell'AI da rivedere
    # (stessa coda di /social/approvazioni, badge rosso invece di giallo,
    # vedi approvals.richiedi_approvazione in esegui_pipeline), non un
    # guasto tecnico — "errori" resta per fallimenti reali (rete, provider,
    # eccezioni non gestite) (segnalato dall'utente).
    "approvazioni": ["AWAITING_APPROVAL", "BLOCKED"],
    "programmati": ["APPROVED", "SCHEDULED", "PUBLISHING"],
    "pubblicati": ["PUBLISHED", "PARTIALLY_PUBLISHED"],
    "errori": ["PUBLISH_FAILED"],
    "archivio": ["CANCELLED", "ARCHIVED"],
}


@router.get("/contenuti", response_class=HTMLResponse)
def contenuti(request: Request, gruppo: str = "idee",
              sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    # Un contenuto in "approvazioni" o "errori" era invisibile finche' non
    # si cliccava per caso su quella tab: senza un conteggio per gruppo,
    # una tab vuota ("Idee") non dava nessun indizio che ci fosse altro da
    # vedere altrove. Un'unica query su tutti i contenuti invece di una
    # per gruppo (7 query) evita di moltiplicare gli accessi al DB.
    tutti = db_social.lista_content(conn, stati=None, limit=1000)
    conteggi = {g: 0 for g in _GRUPPI_STATO}
    for c in tutti:
        for g, stati_gruppo in _GRUPPI_STATO.items():
            if c["stato"] in stati_gruppo:
                conteggi[g] += 1
                break
    stati = _GRUPPI_STATO.get(gruppo, None)
    contenuti_gruppo = [c for c in tutti if stati is None or c["stato"] in stati]
    # Gruppi che richiedono attenzione (approvazioni in attesa, errori di
    # pubblicazione): se non e' quello aperto ora, segnalato con un avviso
    # invece di restare invisibile finche' non si clicca per caso li'.
    altrove = [(g, conteggi[g]) for g in ("approvazioni", "errori")
               if g != gruppo and conteggi.get(g)]
    return templates.TemplateResponse(request, "contenuti.html", _ctx(
        request, sessione, conn, gruppo=gruppo, gruppi=list(_GRUPPI_STATO), conteggi=conteggi,
        altrove=altrove, contenuti=contenuti_gruppo, pillars=db_social.pillars(conn)))


@router.get("/contenuti/nuovo", response_class=HTMLResponse)
def nuovo_contenuto_form(request: Request, sessione=Depends(utente_web),
                         conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.edit")
    # Precompilato in base a cosa e' davvero abilitato in Impostazioni (non
    # sempre entrambi): un canale il cui account non e' ancora abilitato
    # resta deselezionato di default, cosi' non si spreca una generazione
    # (testo + immagine) per un canale su cui non si puo' comunque
    # pubblicare (segnalato dall'utente per LinkedIn, in attesa di
    # approvazione Community Management API).
    canali_abilitati = db_social.canali_abilitati(conn)
    return templates.TemplateResponse(request, "nuovo_contenuto.html", _ctx(
        request, sessione, conn, pillars=db_social.pillars(conn),
        categorie=db_social.lista_categorie(conn),
        canali_abilitati=canali_abilitati,
        # Promozioni/funzionalita' davvero attive/reali lette in diretta da
        # JobInPA (mai inserite a mano, vedi crea_contenuto sotto): [] se
        # l'API non e' configurata o irraggiungibile, il form lo segnala.
        promozioni_disponibili=jobinpa_client.client().promozioni(),
        funzionalita_disponibili=jobinpa_client.client().funzionalita().get("funzionalita", []),
        # Vocabolari chiusi per il pannello "Ricerca avanzata" (Concorsi):
        # stessi campi/valori della ricerca avanzata reale su jobinpa.it,
        # compilabili a mano dall'utente invece di farli dedurre da un'AI
        # al brief (segnalato dall'utente: vuole i filtri espliciti, non
        # nascosti dietro un'interpretazione automatica — vedi memoria
        # feedback_ricerca_esplicita_vs_ai). {} se l'API non e' raggiungibile.
        filtri_disponibili=jobinpa_client.client().filtri_disponibili()))


def _filtri_manuali_da_form(*, regione=None, categoria=None, settore=None, ente=None,
                            competenza=None, ambito=None, inquadramento=None,
                            titolo_studio=None, tipo_contratto=None, posti_minimi=None,
                            lavoro_agile=None, scadenza_da=None, scadenza_a=None):
    """Campi del pannello "Ricerca avanzata" (Concorsi, vedi nuovo_contenuto.html)
    -> dict di filtri per agents.research()/jobinpa_client, stessa forma di
    _filtri_da_criteri in agents.py. Solo i campi valorizzati: un checkbox
    "lavoro_agile" non spuntato arriva come None (assente dal form), non
    "false" -- niente filtro, non un filtro "lavoro_agile=False" implicito
    che escluderebbe bandi non da remoto."""
    grezzi = {
        "regione": regione, "categoria": categoria, "settore": settore, "ente": ente,
        "competenza": competenza, "ambito": ambito, "inquadramento": inquadramento,
        "titolo_studio": titolo_studio, "tipo_contratto": tipo_contratto,
        "scadenza_da": scadenza_da, "scadenza_a": scadenza_a,
    }
    filtri = {k: v for k, v in grezzi.items() if v and v.strip()}
    if posti_minimi and posti_minimi.strip():
        filtri["posti_minimi"] = int(posti_minimi)
    if lavoro_agile:
        filtri["lavoro_agile"] = True
    return filtri


def _estrai_concorso_id(testo):
    """Accetta sia l'URL della scheda bando su JobInPA (.../bandi/<id>) sia
    l'id nudo incollato direttamente: l'utente non deve sapere qual e' il
    formato interno, incolla quello che ha sott'occhio nel browser."""
    testo = testo.strip()
    if "/bandi/" in testo:
        return testo.rsplit("/bandi/", 1)[1].split("?")[0].split("#")[0].strip("/")
    return testo


@router.post("/contenuti")
def crea_contenuto(request: Request, titolo: str = Form(None),
                   pillar: str = Form(...),
                   brief: str = Form(None), categoria_id: str = Form(...),
                   promo_selezionata: str = Form(None),
                   funzionalita_selezionata: Optional[list[str]] = Form(None),
                   bando_specifico: str = Form(None),
                   canali: Optional[list[str]] = Form(None),
                   f_regione: str = Form(None), f_categoria: str = Form(None),
                   f_settore: str = Form(None), f_ente: str = Form(None),
                   f_competenza: str = Form(None), f_ambito: str = Form(None),
                   f_inquadramento: str = Form(None), f_titolo_studio: str = Form(None),
                   f_tipo_contratto: str = Form(None), f_posti_minimi: str = Form(None),
                   f_lavoro_agile: str = Form(None), f_scadenza_da: str = Form(None),
                   f_scadenza_a: str = Form(None), f_soglia_confidenza: str = Form(None),
                   csrf: str = Form(None),
                   sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    """Crea il contenuto e avvia SEMPRE la pipeline: la vecchia scelta fra
    "salva come idea" e "avvia elaborazione AI" era un passaggio in piu'
    senza un vero bisogno dietro (segnalato dall'utente) — chi vuole
    davvero solo una bozza puo' comunque modificare/rilanciare in un
    secondo momento dalla scheda del contenuto.

    `pillar` (etichettato "Intento" nel form, sempre obbligatorio: vedi
    nuovo_contenuto.html) non e' piu' solo organizzativo — per i Concorsi
    sceglie fra il badge "NUOVO CONCORSO" e "IN SCADENZA" nell'immagine
    (vedi agents.visual) e guida il tono del testo (vedi agents.
    copywriting), cosi' la scelta fatta qui si ritrova davvero nel
    contenuto generato (segnalato dall'utente). Sostituisce il vecchio
    campo "obiettivo" (testo libero mai riletto da nessun agente, solo
    vetrina): resta disponibile su crea_content per i temi proposti dal
    Supervisor (vedi accetta_plan_entry), non piu' su questo form manuale.

    La categoria (menu Categorie) e' l'altro selettore chiave: decide se
    serve scegliere una promozione, cercare bandi dal brief, o scrivere
    liberamente — non piu' una tipologia fissa separata."""
    _richiedi(conn, sessione, "social.edit")
    _verifica_csrf(sessione, csrf)
    categoria = db_social.get_categoria(conn, categoria_id)
    if categoria is None:
        raise HTTPException(status_code=400, detail="Categoria non valida")
    scadenza_promo = None
    promo_dati = None
    funzionalita_dati = None
    concorso_id = None
    if (categoria["strategia_fatti"] == "bandi_jobinpa" and bando_specifico
            and bando_specifico.strip()):
        # Bypassa del tutto interpretazione del brief + ricerca semantica
        # (vedi agents.research: content.concorso_id, se impostato, fa
        # recuperare esattamente questo bando): utile quando la ricerca
        # semantica non trova un bando che esiste davvero su JobInPA, per
        # esempio perche' l'embedding non e' ancora stato calcolato (gira
        # con un ritardo orario rispetto alla classificazione). Titolo
        # derivato dal bando stesso, mai scritto a mano (stesso principio
        # di promo_selezionata/funzionalita_selezionata sopra).
        concorso_id = _estrai_concorso_id(bando_specifico)
        bando = jobinpa_client.client().bando(concorso_id)
        if bando is None:
            raise HTTPException(
                status_code=400,
                detail="Bando non trovato su JobInPA: controlla l'id o l'URL incollato")
        titolo = bando["titolo"]
    elif categoria["strategia_fatti"] == "promozioni_jobinpa":
        # Titolo/prezzo/scadenza NON arrivano dal form (mai un claim
        # commerciale scritto a mano): si rilegge la promo in diretta da
        # JobInPA, cosi' e' sempre quella davvero attiva in questo momento,
        # non uno snapshot potenzialmente scaduto passato dal browser.
        if not promo_selezionata or "|" not in promo_selezionata:
            raise HTTPException(status_code=400, detail="Seleziona una promozione")
        tipo_sel, chiave_sel = promo_selezionata.split("|", 1)
        promo = next((p for p in jobinpa_client.client().promozioni()
                     if p["tipo"] == tipo_sel and p["chiave"] == chiave_sel), None)
        if promo is None:
            raise HTTPException(
                status_code=400, detail="Promozione non più attiva su JobInPA: ricarica la pagina")
        titolo = promo["nome"]
        scadenza_promo = promo.get("scadenza")
        promo_dati = promo
    elif categoria["strategia_fatti"] == "funzionalita_jobinpa":
        # Nome/descrizione/URL NON arrivano dal form (mai un claim inventato
        # su cosa fa JobInPA): si rilegge il catalogo in diretta, cosi' i
        # dati (comprese le statistiche d'uso) sono sempre aggiornati. Una
        # o piu' funzionalita' insieme (es. un post che ne combina piu' di
        # una): il titolo/tema del post resta libero, a differenza delle
        # promozioni non c'e' un singolo "nome" ovvio da cui derivarlo.
        chiavi_selezionate = [c for c in (funzionalita_selezionata or []) if c]
        if not chiavi_selezionate:
            raise HTTPException(status_code=400, detail="Seleziona almeno una funzionalità")
        risposta = jobinpa_client.client().funzionalita()
        catalogo = {f["chiave"]: f for f in risposta.get("funzionalita", [])}
        funzionalita_scelte = []
        for chiave in chiavi_selezionate:
            funz = catalogo.get(chiave)
            if funz is None:
                raise HTTPException(
                    status_code=400,
                    detail="Funzionalità non più disponibile: ricarica la pagina")
            funzionalita_scelte.append(funz)
        funzionalita_dati = {"funzionalita": funzionalita_scelte,
                             "statistiche": risposta.get("statistiche", {})}
        if not titolo or not titolo.strip():
            raise HTTPException(status_code=400, detail="Titolo obbligatorio")
    elif not titolo or not titolo.strip():
        raise HTTPException(status_code=400, detail="Titolo obbligatorio")
    # Ricerca avanzata (Concorsi): irrilevante se un bando specifico bypassa
    # gia' del tutto la ricerca (concorso_id impostato sopra).
    filtri_manuali = None
    soglia_confidenza = None
    if categoria["strategia_fatti"] == "bandi_jobinpa" and not concorso_id:
        filtri_manuali = _filtri_manuali_da_form(
            regione=f_regione, categoria=f_categoria, settore=f_settore, ente=f_ente,
            competenza=f_competenza, ambito=f_ambito, inquadramento=f_inquadramento,
            titolo_studio=f_titolo_studio, tipo_contratto=f_tipo_contratto,
            posti_minimi=f_posti_minimi, lavoro_agile=f_lavoro_agile,
            scadenza_da=f_scadenza_da, scadenza_a=f_scadenza_a) or None
        if f_soglia_confidenza and f_soglia_confidenza.strip():
            soglia_confidenza = int(f_soglia_confidenza)
    # None (nessun campo "canali" inviato, es. form non aggiornato o
    # chiamata diretta) lascia il default di crea_content invariato
    # (entrambe le piattaforme) — solo se il campo e' presente ma svuotato
    # dall'utente il filtro puo' risultare in lista vuota, gestita a valle
    # dalla pipeline (nessuna variante/immagine generata per nessun canale).
    canali_scelti = [c for c in canali if c in db_social.PIATTAFORME] if canali is not None else None
    content_id = db_social.crea_content(conn, titolo.strip(), pillar_chiave=pillar,
                                        brief=(brief or "").strip() or None,
                                        scadenza_promo=scadenza_promo,
                                        promo_dati=promo_dati, funzionalita_dati=funzionalita_dati,
                                        categoria_id=categoria_id, canali=canali_scelti,
                                        concorso_id=concorso_id,
                                        filtri_manuali=filtri_manuali,
                                        soglia_confidenza=soglia_confidenza,
                                        creato_da=sessione["utente"]["id"])
    db_social.audit(conn, "contenuto_creato", utente_id=sessione["utente"]["id"],
                    oggetto_tipo="content", oggetto_id=content_id)
    db_social.crea_job(conn, "pipeline", {"content_id": content_id})
    return RedirectResponse(f"/social/contenuti/{content_id}?avviata=1", status_code=303)


_STATI_PIPELINE_IN_CORSO = {"RESEARCHING", "DRAFTING", "DRAFT_READY", "GENERATING_VISUAL", "QUALITY_CHECK"}


def _errore_leggibile(errore):
    """Traduce le eccezioni tecniche salvate in social_content.errore in un
    messaggio comprensibile: senza questo, l'utente vede solo la stringa
    grezza dell'eccezione Python (es. 'budget giornaliero anthropic
    esaurito') senza capire cosa fare — stesso problema gia' risolto per
    l'anteprima del brief, qui applicato alla pagina del contenuto."""
    if not errore:
        return None
    testo = str(errore)
    if "budget giornaliero" in testo:
        return ("Pipeline interrotta: budget AI giornaliero esaurito. Riprova più tardi "
                "(si resetta a mezzanotte) o alza ANTHROPIC_DAILY_BUDGET_EUR in .env.")
    if "budget mensile" in testo:
        return "Pipeline interrotta: budget AI mensile esaurito."
    if "circuito aperto" in testo.lower() or "CircuitAperto" in testo:
        return ("Pipeline interrotta: il provider AI ha risposto con troppi errori di fila "
                "ed è stato temporaneamente disattivato. Riprova tra qualche minuto.")
    return testo


def _motivo_breve(content):
    """Motivo sintetico per un contenuto nel gruppo 'errori' (PUBLISH_FAILED)
    o 'approvazioni' (include anche BLOCKED, vedi _GRUPPI_STATO): senza
    questo la lista Contenuti mostrava solo 'Fase: Bloccata' + 'Rischio:
    rosso', senza dire il perche' — bisognava aprire il dettaglio per
    scoprirlo (segnalato dall'utente).
    Un errore di pipeline (eccezione, vedi _errore_leggibile) ha sempre la
    precedenza; per un BLOCKED senza eccezione (il caso normale: il Quality
    & Risk Agent ha semplicemente giudicato rosso) il motivo e' il primo
    rilievo AI o, in mancanza, la prima regola deterministica scattata."""
    leggibile = _errore_leggibile(content["errore"])
    if leggibile:
        return leggibile
    if content["stato"] == "BLOCKED" and content["punteggi_rischio"]:
        punteggi = json.loads(content["punteggi_rischio"])
        motivi = (punteggi.get("motivi_ai") or []) + (punteggi.get("motivi_regole") or [])
        if motivi:
            return motivi[0]
    return None


templates.env.filters["motivo_breve"] = _motivo_breve


@router.get("/contenuti/{content_id}", response_class=HTMLResponse)
def contenuto(request: Request, content_id: str, avviata: bool = False,
              sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    content = db_social.get_content(conn, content_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Contenuto non trovato")
    punteggi = json.loads(content["punteggi_rischio"]) if content["punteggi_rischio"] else None
    in_corso = content["stato"] in _STATI_PIPELINE_IN_CORSO
    categoria = db_social.get_categoria(conn, content["categoria_id"]) if content["categoria_id"] else None
    return templates.TemplateResponse(request, "contenuto.html", _ctx(
        request, sessione, conn, c=content, punteggi=punteggi,
        errore_leggibile=_errore_leggibile(content["errore"]),
        in_corso=in_corso,
        # Pannello "Ricerca avanzata" nel form di modifica brief: solo per
        # Concorsi, pre-compilato con i filtri manuali gia' salvati (vedi
        # modifica_brief), stessi vocabolari chiusi del form di creazione.
        categoria_bandi_jobinpa=bool(categoria and categoria["strategia_fatti"] == "bandi_jobinpa"),
        filtri_disponibili=(jobinpa_client.client().filtri_disponibili()
                            if categoria and categoria["strategia_fatti"] == "bandi_jobinpa" else {}),
        filtri_manuali_attuali=(json.loads(content["filtri_manuali"])
                                if content["filtri_manuali"] else {}),
        appena_in_coda=(avviata and not in_corso and not content["errore"]
                        and content["stato"] in agents.STATI_PIPELINE_AVVIABILE),
        rigenerazione_in_corso=db_social.job_in_corso(conn, "rigenera_visual", content_id),
        rigenerazione_asset_in_corso=db_social.job_in_corso(conn, "rigenera_asset_singolo", content_id),
        rigenerazione_testo_in_corso=db_social.job_in_corso(conn, "rigenera_copy", content_id),
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
    db_social.aggiorna_content(conn, content_id, errore=None)
    db_social.crea_job(conn, "pipeline", {"content_id": content_id})
    return RedirectResponse(f"/social/contenuti/{content_id}?avviata=1", status_code=303)


@router.post("/contenuti/{content_id}/interrompi")
def interrompi_generazione(request: Request, content_id: str, csrf: str = Form(None),
                           sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    """Segnala al worker di fermarsi al prossimo checkpoint (vedi agents.
    GenerazioneInterrotta) invece di aspettare che finisca da sola: non
    immediato (non puo' interrompere una chiamata AI gia' in volo), ma
    risparmia le immagini del carosello ancora da generare — le piu'
    costose, e quelle che arrivano per ultime (segnalato dall'utente: vuole
    poter fermarsi appena legge un testo che non gli piace, senza aspettare
    che finiscano anche le immagini, per modificare l'idea e risottometterla)."""
    _richiedi(conn, sessione, "social.edit")
    _verifica_csrf(sessione, csrf)
    content = db_social.get_content(conn, content_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Contenuto non trovato")
    if content["stato"] not in _STATI_PIPELINE_IN_CORSO:
        raise HTTPException(status_code=409, detail="Nessuna generazione in corso da interrompere")
    db_social.richiedi_interruzione(conn, content_id)
    db_social.audit(conn, "generazione_interrotta", utente_id=sessione["utente"]["id"],
                    oggetto_tipo="content", oggetto_id=content_id)
    return RedirectResponse(f"/social/contenuti/{content_id}", status_code=303)


@router.post("/contenuti/{content_id}/riporta-in-bozza")
def riporta_in_bozza(request: Request, content_id: str, csrf: str = Form(None),
                     sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    """Un contenuto annullato (es. "nessun bando pertinente" dalla ricerca
    semantica) puo' avere una causa rimediabile — un brief da correggere,
    o adesso un bando specifico da indicare — invece di poter solo essere
    eliminato definitivamente (segnalato dall'utente). Torna a IDEA,
    l'unico altro stato raggiungibile da CANCELLED oltre ad ARCHIVED (vedi
    state_machine.TRANSIZIONI): da li' si modifica il brief e si rilancia
    la pipeline come per qualsiasi altro contenuto in bozza."""
    _richiedi(conn, sessione, "social.edit")
    _verifica_csrf(sessione, csrf)
    content = db_social.get_content(conn, content_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Contenuto non trovato")
    if content["stato"] != "CANCELLED":
        raise HTTPException(status_code=409, detail="Il contenuto non è annullato")
    db_social.aggiorna_content(conn, content_id, errore=None)
    state_machine.transisci(conn, content_id, "IDEA", utente_id=sessione["utente"]["id"])
    db_social.audit(conn, "contenuto_riportato_in_bozza", utente_id=sessione["utente"]["id"],
                    oggetto_tipo="content", oggetto_id=content_id)
    return RedirectResponse(f"/social/contenuti/{content_id}", status_code=303)


@router.post("/contenuti/{content_id}/brief")
def modifica_brief(request: Request, content_id: str, titolo: str = Form(...),
                   brief: str = Form(None), bando_specifico: str = Form(None),
                   f_regione: str = Form(None), f_categoria: str = Form(None),
                   f_settore: str = Form(None), f_ente: str = Form(None),
                   f_competenza: str = Form(None), f_ambito: str = Form(None),
                   f_inquadramento: str = Form(None), f_titolo_studio: str = Form(None),
                   f_tipo_contratto: str = Form(None), f_posti_minimi: str = Form(None),
                   f_lavoro_agile: str = Form(None), f_scadenza_da: str = Form(None),
                   f_scadenza_a: str = Form(None), f_soglia_confidenza: str = Form(None),
                   csrf: str = Form(None),
                   sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    """Modifica il tema/brief PRIMA di rilanciare la pipeline (es. dopo una
    richiesta di modifiche, o dopo aver riportato in bozza un contenuto
    annullato): salva subito, nessuna chiamata AI. Non riavvia da sola la
    pipeline — il revisore decide quando farlo (bottone a parte).

    bando_specifico (opzionale): stesso meccanismo di crea_contenuto —
    se compilato, deriva il titolo dal bando vero (mai scritto a mano) e
    imposta content.concorso_id, che fa saltare la ricerca semantica in
    agents.research(). Se vuoto, il concorso_id gia' eventualmente
    presente NON viene toccato (nessun modo di svuotarlo da questo form:
    solo di impostarlo).

    f_* (Ricerca avanzata, solo Concorsi): stesso meccanismo di
    crea_contenuto — se ANCHE UNO e' valorizzato, filtri_manuali viene
    RISCRITTO da zero con solo i campi valorizzati qui (mai un merge coi
    filtri precedenti: la form mostra sempre lo stato attuale, quindi
    "vuoto" qui significa davvero "nessun filtro", non "non toccare")."""
    _richiedi(conn, sessione, "social.edit")
    _verifica_csrf(sessione, csrf)
    content = db_social.get_content(conn, content_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Contenuto non trovato")
    if content["stato"] not in agents.STATI_PIPELINE_AVVIABILE:
        raise HTTPException(status_code=409,
                            detail="Il brief si modifica solo prima di (ri)lanciare la pipeline")
    campi_concorso = {}
    if bando_specifico and bando_specifico.strip():
        concorso_id = _estrai_concorso_id(bando_specifico)
        bando = jobinpa_client.client().bando(concorso_id)
        if bando is None:
            raise HTTPException(
                status_code=400,
                detail="Bando non trovato su JobInPA: controlla l'id o l'URL incollato")
        titolo = bando["titolo"]
        campi_concorso["concorso_id"] = concorso_id
    categoria = db_social.get_categoria(conn, content["categoria_id"]) if content["categoria_id"] else None
    if categoria and categoria["strategia_fatti"] == "bandi_jobinpa":
        filtri_manuali = _filtri_manuali_da_form(
            regione=f_regione, categoria=f_categoria, settore=f_settore, ente=f_ente,
            competenza=f_competenza, ambito=f_ambito, inquadramento=f_inquadramento,
            titolo_studio=f_titolo_studio, tipo_contratto=f_tipo_contratto,
            posti_minimi=f_posti_minimi, lavoro_agile=f_lavoro_agile,
            scadenza_da=f_scadenza_da, scadenza_a=f_scadenza_a)
        campi_concorso["filtri_manuali"] = json.dumps(filtri_manuali) if filtri_manuali else None
        campi_concorso["soglia_confidenza"] = (
            int(f_soglia_confidenza) if f_soglia_confidenza and f_soglia_confidenza.strip() else None)
    db_social.aggiorna_content(conn, content_id, titolo=titolo.strip(),
                               brief=(brief or "").strip() or None, **campi_concorso)
    return RedirectResponse(f"/social/contenuti/{content_id}", status_code=303)


@router.post("/contenuti/{content_id}/rigenera-immagine")
def rigenera_immagine(request: Request, content_id: str, csrf: str = Form(None),
                      sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    """Rigenera solo le immagini (non il testo gia' approvato/in revisione),
    in coda come gli altri agenti: puo' costare una vera chiamata AI se le
    immagini generate da OpenAI sono abilitate (vedi images.provider_immagini)."""
    _richiedi(conn, sessione, "social.edit")
    _verifica_csrf(sessione, csrf)
    if db_social.get_content(conn, content_id) is None:
        raise HTTPException(status_code=404, detail="Contenuto non trovato")
    db_social.crea_job(conn, "rigenera_visual", {"content_id": content_id})
    return RedirectResponse(f"/social/contenuti/{content_id}?avviata=1", status_code=303)


@router.post("/contenuti/{content_id}/rigenera-testo")
def rigenera_testo(request: Request, content_id: str, note_revisore: str = Form(None),
                   csrf: str = Form(None),
                   sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    """Rigenera solo il testo (non le immagini): tipicamente dopo aver
    tolto una o piu' immagini dal carosello, per allineare la caption
    (es. il conteggio 'scorri le N immagini') al carosello effettivo, oppure
    per un contenuto BLOCKED dal Quality & Risk Agent -- note_revisore
    (opzionale, precompilato in contenuto.html coi motivi del blocco) entra
    nel prompt del copywriter esattamente come la nota di "richiedi
    modifiche" di un revisore umano (vedi copywriting/esegui_pipeline),
    cosi' la nuova versione tiene conto del perche' e' stata bloccata
    invece di riprodurre lo stesso problema."""
    _richiedi(conn, sessione, "social.edit")
    _verifica_csrf(sessione, csrf)
    if db_social.get_content(conn, content_id) is None:
        raise HTTPException(status_code=404, detail="Contenuto non trovato")
    payload = {"content_id": content_id}
    if note_revisore and note_revisore.strip():
        payload["note_revisore"] = note_revisore.strip()
    db_social.crea_job(conn, "rigenera_copy", payload)
    return RedirectResponse(f"/social/contenuti/{content_id}?avviata=1", status_code=303)


@router.post("/contenuti/{content_id}/asset/{asset_id}/elimina")
def elimina_asset(request: Request, content_id: str, asset_id: str,
                  csrf: str = Form(None),
                  sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    """Toglie UNA immagine dal carosello (mai l'intera rigenerazione): se
    era collegata a un bando, lo rimuove anche da bandi_trovati (vedi
    db_social.elimina_asset), cosi' un successivo 'Rigenera testo' non lo
    cita/conta piu'."""
    _richiedi(conn, sessione, "social.edit")
    _verifica_csrf(sessione, csrf)
    if db_social.get_content(conn, content_id) is None:
        raise HTTPException(status_code=404, detail="Contenuto non trovato")
    if not db_social.elimina_asset(conn, content_id, asset_id):
        raise HTTPException(status_code=404, detail="Immagine non trovata")
    return RedirectResponse(f"/social/contenuti/{content_id}", status_code=303)


@router.post("/contenuti/{content_id}/asset/{asset_id}/rigenera")
def rigenera_asset_singolo(request: Request, content_id: str, asset_id: str,
                           nota: str = Form(None), csrf: str = Form(None),
                           sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    """Rigenera SOLO questa immagine (di un carosello o l'unica di una
    piattaforma), in coda come gli altri agenti (stesso possibile costo AI
    di 'Rigenera immagine'): le altre immagini non vengono toccate
    (segnalato dall'utente).

    `nota` (opzionale, vedi contenuto.html): istruzione ad-hoc per questo
    tentativo (es. refusi/accenti storpiati nel testo generato dall'AI),
    passata solo a questa rigenerazione — vedi agents.rigenera_immagine_
    singola/images._prompt_grafica_intera."""
    _richiedi(conn, sessione, "social.edit")
    _verifica_csrf(sessione, csrf)
    asset = db_social.get_asset(conn, content_id, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Immagine non trovata")
    if not asset["titolo"]:
        raise HTTPException(
            status_code=400,
            detail="Questa immagine e' stata generata prima che la rigenerazione guidata da "
                   "nota fosse disponibile per immagini singole: usa 'Rigenera immagine'")
    payload = {"content_id": content_id, "asset_id": asset_id}
    if nota and nota.strip():
        payload["nota"] = nota.strip()
    db_social.crea_job(conn, "rigenera_asset_singolo", payload)
    return RedirectResponse(f"/social/contenuti/{content_id}?avviata=1", status_code=303)


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


@router.post("/contenuti/{content_id}/riprogramma")
def riprogramma(request: Request, content_id: str, quando: str = Form(...),
                csrf: str = Form(None),
                sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    """Cambia data/ora di una pubblicazione gia' programmata (stato
    SCHEDULED): oggi non c'era alcun modo di farlo se non annullare e
    rifare tutto da capo (segnalato dall'utente)."""
    _richiedi(conn, sessione, "social.publish")
    _verifica_csrf(sessione, csrf)
    content = db_social.get_content(conn, content_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Contenuto non trovato")
    if content["stato"] != "SCHEDULED":
        raise HTTPException(status_code=409,
                            detail="Si puo' riprogrammare solo un contenuto gia' programmato")
    try:
        locale = datetime.fromisoformat(quando).replace(tzinfo=ZoneInfo(config.default_timezone()))
    except ValueError:
        raise HTTPException(status_code=422, detail="Data/ora non valida")
    nuovo_orario = locale.astimezone(timezone.utc)
    db_social.riprogramma_pubblicazione(conn, content_id, nuovo_orario.isoformat())
    db_social.audit(conn, "pubblicazione_riprogrammata", utente_id=sessione["utente"]["id"],
                    oggetto_tipo="content", oggetto_id=content_id,
                    dettagli={"nuovo_orario": nuovo_orario.isoformat()})
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
            "categoria": db_social.get_categoria(conn, content["categoria_id"])
                        if content["categoria_id"] else None,
            "punteggi": json.loads(content["punteggi_rischio"]) if content["punteggi_rischio"] else None,
            "varianti": {v["piattaforma"]: v for v in db_social.varianti_di(conn, selezionata["content_id"])},
            "fatti": db_social.fatti_di(conn, selezionata["content_id"]),
            "bandi_trovati": json.loads(content["bandi_trovati"] or "[]"),
            "promo_dati": json.loads(content["promo_dati"]) if content["promo_dati"] else None,
            "funzionalita_dati": json.loads(content["funzionalita_dati"])
                                if content["funzionalita_dati"] else None,
        }
    return templates.TemplateResponse(request, "approvazioni.html", _ctx(
        request, sessione, conn, approvazioni=coda, selezionata=selezionata, dettaglio=dettaglio))


@router.post("/approvazioni/{content_id}/variante/{piattaforma}")
def modifica_variante(request: Request, content_id: str, piattaforma: str,
                      testo: str = Form(...), csrf: str = Form(None),
                      sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    """Modifica manuale del testo (es. per correggere o aggiungere un link,
    o per riscrivere a mano un post BLOCKED dal Quality & Risk Agent invece
    di farlo rigenerare dall'AI): salva subito, nessuna chiamata AI, nessun
    costo. Nessun vincolo sullo stato del contenuto -- lo stesso form serve
    sia dalla coda di Revisione (AWAITING_APPROVAL) sia dalla pagina
    Contenuto (BLOCKED)."""
    _richiedi(conn, sessione, "social.approve")
    _verifica_csrf(sessione, csrf)
    content = db_social.get_content(conn, content_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Contenuto non trovato")
    if piattaforma not in db_social.PIATTAFORME:
        raise HTTPException(status_code=404, detail=f"piattaforma sconosciuta: {piattaforma}")
    db_social.aggiorna_testo_variante(conn, content_id, piattaforma, testo)
    db_social.audit(conn, "variante_modificata", utente_id=sessione["utente"]["id"],
                    oggetto_tipo="content", oggetto_id=content_id,
                    dettagli={"piattaforma": piattaforma})
    # Tornare a Revisione ha senso solo se il contenuto e' davvero li'
    # (AWAITING_APPROVAL): altrimenti approvazioni() ripiegherebbe sul primo
    # elemento della coda (o su una pagina vuota), un redirect fuorviante
    # per chi arriva dalla pagina Contenuto di un BLOCKED.
    if content["stato"] == "AWAITING_APPROVAL":
        return RedirectResponse(f"/social/approvazioni?content_id={content_id}", status_code=303)
    return RedirectResponse(f"/social/contenuti/{content_id}", status_code=303)


def _redirect_dopo_regola(content):
    """Stesso motivo di modifica_variante: torna a Revisione solo se il
    contenuto e' davvero li' (AWAITING_APPROVAL), altrimenti alla pagina
    Contenuto (BLOCKED, arrivato dalla scheda invece che dalla coda)."""
    if content["stato"] == "AWAITING_APPROVAL":
        return RedirectResponse(f"/social/approvazioni?content_id={content['id']}", status_code=303)
    return RedirectResponse(f"/social/contenuti/{content['id']}", status_code=303)


@router.post("/approvazioni/{content_id}/dubbio/conferma")
def conferma_alert_reviewer(request: Request, content_id: str, testo: str = Form(...),
                            csrf: str = Form(None),
                            sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    """Il dubbio del giudizio AI (content.punteggi_rischio.motivi_ai, MAI le
    regole deterministiche di risk.classifica_regole: quelle sono codice
    fisso, non un'opinione da confermare/rifiutare) era fondato: diventa un
    "vincolo" applicato SEMPRE nei prossimi giudizi (vedi agents.
    quality_risk), non solo per questo contenuto — cosi' il reviewer AI
    "impara" dalle correzioni umane invece di ripetere sempre lo stesso
    lavoro di revisione (segnalato dall'utente)."""
    _richiedi(conn, sessione, "social.approve")
    _verifica_csrf(sessione, csrf)
    content = db_social.get_content(conn, content_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Contenuto non trovato")
    db_social.crea_regola_revisione(conn, testo, "vincolo", origine_content_id=content_id,
                                    creato_da=sessione["utente"]["id"])
    db_social.audit(conn, "regola_revisione_confermata", utente_id=sessione["utente"]["id"],
                    oggetto_tipo="content", oggetto_id=content_id, dettagli={"testo": testo})
    return _redirect_dopo_regola(content)


@router.post("/approvazioni/{content_id}/dubbio/rifiuta")
def rifiuta_alert_reviewer(request: Request, content_id: str, testo: str = Form(...),
                           csrf: str = Form(None),
                           sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    """Il dubbio del giudizio AI non era fondato (falso positivo): diventa
    un'"esenzione" che dice esplicitamente all'AI di non segnalarlo piu',
    non solo per questo contenuto (vedi conferma_alert_reviewer sopra,
    stesso principio, verso opposto)."""
    _richiedi(conn, sessione, "social.approve")
    _verifica_csrf(sessione, csrf)
    content = db_social.get_content(conn, content_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Contenuto non trovato")
    db_social.crea_regola_revisione(conn, testo, "esenzione", origine_content_id=content_id,
                                    creato_da=sessione["utente"]["id"])
    db_social.audit(conn, "regola_revisione_rifiutata", utente_id=sessione["utente"]["id"],
                    oggetto_tipo="content", oggetto_id=content_id, dettagli={"testo": testo})
    return _redirect_dopo_regola(content)


@router.post("/approvazioni/{approval_id}")
def decidi_approvazione(request: Request, approval_id: str,
                        azione: str = Form(...), motivo: str = Form(None),
                        csrf: str = Form(None),
                        sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.approve")
    _verifica_csrf(sessione, csrf)
    utente_id = sessione["utente"]["id"]
    approval = conn.execute("SELECT * FROM social_approvals WHERE id = ?",
                            (approval_id,)).fetchone()
    if approval is None:
        raise HTTPException(status_code=404, detail="approvazione inesistente")
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
            # Riparte da sola: la nota appena registrata viene recuperata
            # automaticamente da esegui_pipeline (vedi agents.py), niente
            # bisogno di ricliccare "Avvia pipeline" a mano.
            db_social.crea_job(conn, "pipeline", {"content_id": approval["content_id"]})
        else:
            raise HTTPException(status_code=422, detail="azione sconosciuta")
    except ValueError as errore:
        raise HTTPException(status_code=404, detail=str(errore))
    if azione == "modifiche":
        return RedirectResponse(f"/social/contenuti/{approval['content_id']}?avviata=1",
                                status_code=303)
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
        riepilogo_costi=db_social.riepilogo_costi_per_agente(conn),
        costi=db_social.report_costi(conn, limit=100)))


def _raggruppa_esecuzioni_ripetute(righe):
    """Raggruppa esecuzioni fallite consecutive e identiche (stesso agente e
    prompt) in un'unica riga con un contatore: un job dello scheduler che
    ritenta ogni pochi minuti puo' altrimenti riempire il log di decine di
    righe indistinguibili, nascondendo le esecuzioni realmente diverse.
    Le esecuzioni riuscite non vengono mai raggruppate: ognuna e' un evento
    reale con il proprio costo e i propri token."""
    raggruppate = []
    for r in righe:
        precedente = raggruppate[-1] if raggruppate else None
        if (r["esito"] == "errore" and precedente is not None
                and precedente["esito"] == "errore"
                and precedente["agente"] == r["agente"]
                and precedente["prompt_nome"] == r["prompt_nome"]):
            precedente["_conteggio"] += 1
            precedente["_da"] = r["iniziato_at"]
        else:
            riga = dict(r)
            riga["_conteggio"] = 1
            riga["_da"] = r["iniziato_at"]
            raggruppate.append(riga)
    return raggruppate


@router.get("/log", response_class=HTMLResponse)
def log_pagina(request: Request, sessione=Depends(utente_web),
               conn=Depends(ottieni_conn)):
    return templates.TemplateResponse(request, "log.html", _ctx(
        request, sessione, conn,
        agent_runs=_raggruppa_esecuzioni_ripetute(db_social.agent_runs_recenti(conn, limit=50)),
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
            # Con lo storage pubblico R2 configurato (vedi asset_storage.py),
            # "pronto" e' finalmente raggiungibile anche in locale: il token
            # e' comunque salvato anche se qualche altro requisito manca.
            nuovo_stato = "verificato" if adapter.health_check()["pronto"] else "in_configurazione"
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


@router.post("/oauth/instagram/token-manuale")
def instagram_token_manuale(request: Request, token: str = Form(...), csrf: str = Form(None),
                            sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    """Via alternativa al redirect OAuth completo: incolla qui un token
    generato altrove (es. il bottone "Genera token" della dashboard Meta
    per un account tester) — utile quando il redirect OAuth non funziona
    (problema noto lato Meta in certi periodi, non del nostro codice)."""
    _richiedi(conn, sessione, "social.admin")
    _verifica_csrf(sessione, csrf)
    account = db_social.account_per_piattaforma(conn, "instagram")
    if account is None:
        raise HTTPException(status_code=404, detail="account non configurato")
    utente_id = sessione["utente"]["id"]
    try:
        adapter = InstagramAdapter(conn)
        token_finale = adapter.completa_con_token_manuale(token.strip())
        db_social.salva_oauth_token(conn, account["id"], "access",
                                    security.encrypt_token(token_finale))
        nuovo_stato = "verificato" if adapter.health_check()["pronto"] else "in_configurazione"
        db_social.aggiorna_account(conn, account["id"], stato=nuovo_stato)
        db_social.audit(conn, "oauth_completato", utente_id=utente_id,
                        oggetto_tipo="account", oggetto_id=account["id"],
                        stato_dopo=nuovo_stato,
                        dettagli={"provider": "instagram", "metodo": "token_manuale"})
    except Exception as errore:
        db_social.registra_incidente(conn, "publishing",
                                     f"Token manuale Instagram fallito: {errore}")
        db_social.audit(conn, "oauth_fallito", utente_id=utente_id,
                        oggetto_tipo="account", oggetto_id=account["id"],
                        dettagli={"provider": "instagram", "metodo": "token_manuale",
                                  "errore": str(errore)})
        raise HTTPException(status_code=502, detail=f"Token non valido: {errore}")
    return RedirectResponse("/social/impostazioni", status_code=303)


# --- Categorie (prompt + immagini di riferimento) ---------------------------

def _cartella_categorie():
    cartella = config.asset_storage_path() / "categorie"
    cartella.mkdir(parents=True, exist_ok=True)
    return cartella


@router.get("/categorie", response_class=HTMLResponse)
def categorie(request: Request, sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.admin")
    return templates.TemplateResponse(request, "categorie.html", _ctx(
        request, sessione, conn, pagina_attiva="categorie",
        categorie=db_social.lista_categorie(conn)))


async def _salva_immagine_categoria(file: UploadFile) -> str:
    estensione = Path(file.filename).suffix or ".png"
    percorso = _cartella_categorie() / f"{uuid.uuid4().hex}{estensione}"
    percorso.write_bytes(await file.read())
    return str(percorso)


@router.post("/categorie")
async def crea_categoria(request: Request, nome: str = Form(...),
                         strategia_fatti: str = Form(...), prompt_ai: str = Form(""),
                         struttura_post: str = Form(""), stile_immagine: str = Form(""),
                         immagini: Optional[list[UploadFile]] = File(None), csrf: str = Form(None),
                         sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.admin")
    _verifica_csrf(sessione, csrf)
    if strategia_fatti not in db_social.STRATEGIE_FATTI:
        raise HTTPException(status_code=400, detail="strategia_fatti non valida")
    percorsi = [await _salva_immagine_categoria(f) for f in (immagini or []) if f and f.filename]
    try:
        categoria_id = db_social.crea_categoria(
            conn, nome.strip(), prompt_ai, immagini_riferimento=percorsi,
            strategia_fatti=strategia_fatti, struttura_post=struttura_post or None,
            stile_immagine=stile_immagine or None)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Esiste già una categoria con questo nome")
    db_social.audit(conn, "categoria_creata", utente_id=sessione["utente"]["id"],
                    oggetto_tipo="categoria", oggetto_id=categoria_id)
    return RedirectResponse("/social/categorie", status_code=303)


@router.post("/categorie/{categoria_id}")
async def aggiorna_categoria(request: Request, categoria_id: str,
                             strategia_fatti: str = Form(...), prompt_ai: str = Form(""),
                             struttura_post: str = Form(""), stile_immagine: str = Form(""),
                             immagini_nuove: Optional[list[UploadFile]] = File(None),
                             rimuovi_immagini: Optional[list[str]] = Form(None),
                             csrf: str = Form(None),
                             sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.admin")
    _verifica_csrf(sessione, csrf)
    if strategia_fatti not in db_social.STRATEGIE_FATTI:
        raise HTTPException(status_code=400, detail="strategia_fatti non valida")
    categoria = db_social.get_categoria(conn, categoria_id)
    if categoria is None:
        raise HTTPException(status_code=404)
    percorsi = list(categoria["immagini_riferimento"])
    for percorso_da_rimuovere in (rimuovi_immagini or []):
        if percorso_da_rimuovere in percorsi:
            Path(percorso_da_rimuovere).unlink(missing_ok=True)
            percorsi.remove(percorso_da_rimuovere)
    for file in (immagini_nuove or []):
        if file and file.filename:
            percorsi.append(await _salva_immagine_categoria(file))
    db_social.aggiorna_categoria(conn, categoria_id, prompt_ai=prompt_ai,
                                 immagini_riferimento=percorsi, strategia_fatti=strategia_fatti,
                                 struttura_post=struttura_post or "",
                                 stile_immagine=stile_immagine or "")
    db_social.audit(conn, "categoria_modificata", utente_id=sessione["utente"]["id"],
                    oggetto_tipo="categoria", oggetto_id=categoria_id)
    return RedirectResponse("/social/categorie", status_code=303)


@router.post("/categorie/{categoria_id}/elimina")
def elimina_categoria(request: Request, categoria_id: str, csrf: str = Form(None),
                      sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.admin")
    _verifica_csrf(sessione, csrf)
    riga = db_social.get_categoria(conn, categoria_id)
    if riga is None:
        raise HTTPException(status_code=404)
    for percorso in riga["immagini_riferimento"]:
        Path(percorso).unlink(missing_ok=True)
    db_social.elimina_categoria(conn, categoria_id)
    db_social.audit(conn, "categoria_eliminata", utente_id=sessione["utente"]["id"],
                    oggetto_tipo="categoria", oggetto_id=categoria_id)
    return RedirectResponse("/social/categorie", status_code=303)


@router.get("/categorie/{categoria_id}/immagine/{indice}")
def anteprima_immagine_categoria(categoria_id: str, indice: int, sessione=Depends(utente_web),
                                 conn=Depends(ottieni_conn)):
    riga = db_social.get_categoria(conn, categoria_id)
    if riga is None or indice < 0 or indice >= len(riga["immagini_riferimento"]):
        raise HTTPException(status_code=404)
    percorso = Path(riga["immagini_riferimento"][indice]).resolve()
    radice = config.asset_storage_path().resolve()
    if radice not in percorso.parents and percorso != radice:
        raise HTTPException(status_code=403, detail="percorso fuori dallo storage asset")
    if not percorso.exists():
        raise HTTPException(status_code=404)
    return FileResponse(percorso)


# --- Regole del Quality & Risk Agent ------------------------------------------
# Nascono di solito confermando/rifiutando un dubbio in Revisione (vedi
# conferma_alert_reviewer/rifiuta_alert_reviewer sopra); questa pagina le
# elenca tutte e permette di crearne/modificarne/disattivarne a mano, senza
# dover passare per un contenuto specifico (segnalato dall'utente: vuole
# una pagina di gestione delle regole passate al reviewer).

@router.get("/regole", response_class=HTMLResponse)
def regole(request: Request, sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.admin")
    return templates.TemplateResponse(request, "regole.html", _ctx(
        request, sessione, conn, pagina_attiva="regole",
        regole=db_social.lista_regole_revisione(conn)))


@router.post("/regole")
def crea_regola(request: Request, testo: str = Form(...), tipo: str = Form(...),
                csrf: str = Form(None),
                sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.admin")
    _verifica_csrf(sessione, csrf)
    if tipo not in ("vincolo", "esenzione"):
        raise HTTPException(status_code=400, detail="tipo non valido")
    regola_id = db_social.crea_regola_revisione(conn, testo, tipo,
                                                creato_da=sessione["utente"]["id"])
    db_social.audit(conn, "regola_revisione_creata", utente_id=sessione["utente"]["id"],
                    oggetto_tipo="regola_revisione", oggetto_id=regola_id)
    return RedirectResponse("/social/regole", status_code=303)


@router.post("/regole/{regola_id}")
def aggiorna_regola(request: Request, regola_id: str, testo: str = Form(...),
                    csrf: str = Form(None),
                    sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.admin")
    _verifica_csrf(sessione, csrf)
    db_social.aggiorna_regola_revisione(conn, regola_id, testo=testo)
    db_social.audit(conn, "regola_revisione_modificata", utente_id=sessione["utente"]["id"],
                    oggetto_tipo="regola_revisione", oggetto_id=regola_id)
    return RedirectResponse("/social/regole", status_code=303)


@router.post("/regole/{regola_id}/toggle")
def toggle_regola(request: Request, regola_id: str, csrf: str = Form(None),
                  sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    """Disattivare (mai eliminare) e' la via reversibile per smettere di
    applicare una regola senza perderne lo storico — riattivabile in un
    secondo momento con lo stesso bottone."""
    _richiedi(conn, sessione, "social.admin")
    _verifica_csrf(sessione, csrf)
    riga = conn.execute("SELECT stato FROM social_review_rules WHERE id = ?",
                        (regola_id,)).fetchone()
    if riga is None:
        raise HTTPException(status_code=404)
    nuovo_stato = "disattivata" if riga["stato"] == "attiva" else "attiva"
    db_social.aggiorna_regola_revisione(conn, regola_id, stato=nuovo_stato)
    db_social.audit(conn, "regola_revisione_stato_cambiato", utente_id=sessione["utente"]["id"],
                    oggetto_tipo="regola_revisione", oggetto_id=regola_id,
                    dettagli={"nuovo_stato": nuovo_stato})
    return RedirectResponse("/social/regole", status_code=303)


@router.post("/regole/{regola_id}/elimina")
def elimina_regola(request: Request, regola_id: str, csrf: str = Form(None),
                   sessione=Depends(utente_web), conn=Depends(ottieni_conn)):
    _richiedi(conn, sessione, "social.admin")
    _verifica_csrf(sessione, csrf)
    db_social.elimina_regola_revisione(conn, regola_id)
    db_social.audit(conn, "regola_revisione_eliminata", utente_id=sessione["utente"]["id"],
                    oggetto_tipo="regola_revisione", oggetto_id=regola_id)
    return RedirectResponse("/social/regole", status_code=303)


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
