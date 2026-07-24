"""
approvals.py — workflow di approvazione umana (sez. 12 del prompt master).

Quando un contenuto richiede revisione: si crea la richiesta, si manda una
email ai revisori (SMTP configurabile; in sviluppo Docker: Mailpit) con
titolo, piattaforme, classe di rischio e link alla dashboard locale — MAI
token o link firmati: l'approvazione avviene autenticati in dashboard.
Ogni invio e ogni decisione restano registrati (approval_events + audit).
"""

import json
import logging
import smtplib
import ssl
from email.message import EmailMessage

from social import config, db_social, state_machine

log = logging.getLogger(__name__)


def _invia_email(destinatari, oggetto, corpo):
    """(esito, dettaglio). 'saltata' se SMTP non configurato: mai un errore
    che blocchi la pipeline (stesso principio di notifiche.py)."""
    cfg = config.smtp_config()
    if not cfg["host"]:
        return "saltata", "SMTP non configurato"
    messaggio = EmailMessage()
    messaggio["Subject"] = oggetto
    messaggio["From"] = f"{cfg['from_name']} <{cfg['from_email']}>"
    messaggio["To"] = ", ".join(destinatari)
    messaggio.set_content(corpo)
    try:
        if cfg["use_ssl"]:
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"],
                                  context=ssl.create_default_context(), timeout=30) as smtp:
                if cfg["username"]:
                    smtp.login(cfg["username"], cfg["password"])
                smtp.send_message(messaggio)
        else:
            with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as smtp:
                if cfg["use_tls"]:
                    smtp.starttls(context=ssl.create_default_context())
                if cfg["username"]:
                    smtp.login(cfg["username"], cfg["password"])
                smtp.send_message(messaggio)
        return "inviata", None
    except (smtplib.SMTPException, OSError) as errore:
        log.warning("invio email approvazione fallito: %s", errore)
        return "fallita", str(errore)


def richiedi_approvazione(conn, content_id):
    """Crea (o riusa) la richiesta aperta e notifica i revisori."""
    approval = db_social.approval_aperta_di(conn, content_id)
    if approval is None:
        approval_id = db_social.crea_approval(conn, content_id)
    else:
        approval_id = approval["id"]
    content = db_social.get_content(conn, content_id)
    destinatari = db_social.get_setting(conn, "revisori_email", []) or []
    if destinatari:
        canali = ", ".join(json.loads(content["canali"] or "[]"))
        link = f"{config.base_url()}/social/approvazioni"
        corpo = (
            "È in attesa di approvazione un contenuto social JobInPA.\n\n"
            f"Titolo: {content['titolo']}\n"
            f"Piattaforme: {canali}\n"
            f"Classe di rischio: {content['classe_rischio'] or 'n/d'}\n\n"
            f"Rivedi e decidi dalla dashboard: {link}\n"
            "(accesso con le tue credenziali; questo link non contiene token)")
        esito, dettaglio = _invia_email(destinatari,
                                        f"[JobInPA Social] Approvazione richiesta: {content['titolo']}",
                                        corpo)
        db_social.registra_email(conn, destinatari,
                                 f"Approvazione richiesta: {content['titolo']}",
                                 corpo, riferimento=f"approval:{approval_id}",
                                 esito=esito, dettaglio=dettaglio)
        db_social.registra_approval_event(conn, approval_id, "email_inviata",
                                          motivo=esito)
    db_social.audit(conn, "approvazione_richiesta", agente="supervisor",
                    oggetto_tipo="approval", oggetto_id=approval_id)
    return approval_id


def approva(conn, approval_id, utente_id, motivo=None):
    approval = conn.execute("SELECT * FROM social_approvals WHERE id = ?",
                            (approval_id,)).fetchone()
    if approval is None:
        raise ValueError("approvazione inesistente")
    db_social.decidi_approval(conn, approval_id, "approvato", utente_id, motivo)
    state_machine.transisci(conn, approval["content_id"], "APPROVED",
                            utente_id=utente_id, motivo=motivo or "approvato dal revisore")
    from social import agents
    agents.programma_pubblicazione(conn, approval["content_id"])
    db_social.audit(conn, "approvazione_concessa", utente_id=utente_id,
                    oggetto_tipo="approval", oggetto_id=approval_id, motivo=motivo)


def rifiuta(conn, approval_id, utente_id, motivo=None):
    approval = conn.execute("SELECT * FROM social_approvals WHERE id = ?",
                            (approval_id,)).fetchone()
    if approval is None:
        raise ValueError("approvazione inesistente")
    db_social.decidi_approval(conn, approval_id, "rifiutato", utente_id, motivo)
    state_machine.transisci(conn, approval["content_id"], "CANCELLED",
                            utente_id=utente_id, motivo=motivo or "rifiutato dal revisore")
    db_social.audit(conn, "approvazione_rifiutata", utente_id=utente_id,
                    oggetto_tipo="approval", oggetto_id=approval_id, motivo=motivo)


def richiedi_modifiche(conn, approval_id, utente_id, motivo):
    approval = conn.execute("SELECT * FROM social_approvals WHERE id = ?",
                            (approval_id,)).fetchone()
    if approval is None:
        raise ValueError("approvazione inesistente")
    db_social.decidi_approval(conn, approval_id, "modifiche_richieste", utente_id, motivo)
    state_machine.transisci(conn, approval["content_id"], "CHANGES_REQUESTED",
                            utente_id=utente_id, motivo=motivo)
    db_social.audit(conn, "modifiche_richieste", utente_id=utente_id,
                    oggetto_tipo="approval", oggetto_id=approval_id, motivo=motivo)
