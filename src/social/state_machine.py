"""
state_machine.py — ciclo di vita di un contenuto social (sez. 15 del prompt).

Le transizioni valide sono esplicite: qualunque altra viene rifiutata con
TransizioneNonValida. Ogni transizione riuscita finisce nell'audit log.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from social import db_social  # noqa: E402

STATI = (
    "IDEA", "RESEARCHING", "RESEARCH_FAILED", "DRAFTING", "DRAFT_READY",
    "GENERATING_VISUAL", "QUALITY_CHECK", "BLOCKED", "AWAITING_APPROVAL",
    "CHANGES_REQUESTED", "APPROVED", "SCHEDULED", "PUBLISHING", "PUBLISHED",
    "PARTIALLY_PUBLISHED", "PUBLISH_FAILED", "CANCELLED", "ARCHIVED",
)

TRANSIZIONI = {
    "IDEA": {"RESEARCHING", "CANCELLED"},
    "RESEARCHING": {"DRAFTING", "RESEARCH_FAILED", "CANCELLED"},
    "RESEARCH_FAILED": {"RESEARCHING", "CANCELLED", "ARCHIVED"},
    "DRAFTING": {"DRAFT_READY", "RESEARCH_FAILED", "CANCELLED"},
    "DRAFT_READY": {"GENERATING_VISUAL", "QUALITY_CHECK", "CANCELLED"},
    "GENERATING_VISUAL": {"QUALITY_CHECK", "DRAFT_READY", "CANCELLED"},
    "QUALITY_CHECK": {"BLOCKED", "AWAITING_APPROVAL", "APPROVED", "CANCELLED"},
    "BLOCKED": {"DRAFTING", "CANCELLED", "ARCHIVED"},
    "AWAITING_APPROVAL": {"APPROVED", "CHANGES_REQUESTED", "CANCELLED"},
    # RESEARCHING: senza, esegui_pipeline() non puo' mai ripartire da qui
    # (STATI_PIPELINE_AVVIABILE lo elenca come punto di ripartenza valido,
    # ma la prima transizione della pipeline e' sempre -> RESEARCHING —
    # bug pre-esistente, mai innescato perche' nessun test rilanciava
    # davvero la pipeline da CHANGES_REQUESTED prima d'ora).
    "CHANGES_REQUESTED": {"RESEARCHING", "DRAFTING", "AWAITING_APPROVAL", "CANCELLED"},
    "APPROVED": {"SCHEDULED", "CANCELLED"},
    "SCHEDULED": {"PUBLISHING", "CANCELLED"},
    "PUBLISHING": {"PUBLISHED", "PARTIALLY_PUBLISHED", "PUBLISH_FAILED"},
    "PUBLISHED": {"ARCHIVED"},
    "PARTIALLY_PUBLISHED": {"PUBLISHING", "PUBLISHED", "PUBLISH_FAILED", "ARCHIVED"},
    "PUBLISH_FAILED": {"SCHEDULED", "PUBLISHING", "CANCELLED", "ARCHIVED"},
    # IDEA: un contenuto annullato (es. "nessun bando pertinente" dalla
    # ricerca semantica) puo' avere una causa rimediabile — un brief da
    # correggere, o un bando specifico da indicare adesso disponibile
    # (vedi web.riporta_in_bozza) — invece di poter solo essere eliminato
    # definitivamente (segnalato dall'utente).
    "CANCELLED": {"ARCHIVED", "IDEA"},
    "ARCHIVED": set(),
}


class TransizioneNonValida(ValueError):
    pass


def transizione_valida(da, a):
    return a in TRANSIZIONI.get(da, set())


def transisci(conn, content_id, nuovo_stato, *, utente_id=None, agente=None, motivo=None):
    """Applica la transizione se valida, registra in audit, ritorna la riga
    aggiornata. Solleva TransizioneNonValida altrimenti."""
    if nuovo_stato not in STATI:
        raise TransizioneNonValida(f"stato sconosciuto: {nuovo_stato}")
    contenuto = db_social.get_content(conn, content_id)
    if contenuto is None:
        raise TransizioneNonValida(f"contenuto inesistente: {content_id}")
    attuale = contenuto["stato"]
    if not transizione_valida(attuale, nuovo_stato):
        raise TransizioneNonValida(f"transizione non consentita: {attuale} -> {nuovo_stato}")
    db_social.aggiorna_content(conn, content_id, stato=nuovo_stato)
    db_social.audit(conn, "transizione_stato", utente_id=utente_id, agente=agente,
                    oggetto_tipo="content", oggetto_id=content_id,
                    stato_prima=attuale, stato_dopo=nuovo_stato, motivo=motivo)
    return db_social.get_content(conn, content_id)
