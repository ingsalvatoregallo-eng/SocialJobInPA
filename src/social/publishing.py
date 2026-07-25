"""
publishing.py — Publishing Agent: idempotente e con la catena completa di
controlli di sicurezza (sez. 19 del prompt master).

can_publish() verifica NELL'ORDINE, fermandosi al primo no:
    1. environment  (GLOBAL_PUBLISHING_ENABLED)
    2. database     (kill switch in system_settings)
    3. account      (stato verificato + publishing_enabled per account)
    4. approvazione (approvato, o verde con decisione auto_publish)
    5. classe di rischio (mai rosso/blocked)
In caso di dubbio (stato mancante, classe assente) NON si pubblica.

In modalita' mock/sandbox il publisher e' sempre MockAdapter: nessuna
chiamata alle piattaforme reali, qualunque cosa dicano i controlli.

Idempotenza: db_social.apri_publication ha un vincolo UNIQUE
(content_id, piattaforma) — lo stesso contenuto non puo' mai essere
pubblicato due volte sulla stessa piattaforma, nemmeno da due worker
concorrenti.
"""

import json
import logging

from social import config, db_social, state_machine
from social.integrations.base import MockAdapter
from social.integrations.instagram import InstagramAdapter
from social.integrations.linkedin import LinkedInAdapter

log = logging.getLogger(__name__)


def modalita_effettiva(conn):
    return db_social.get_setting(conn, "mode_override") or config.mode()


def adapter_per(conn, piattaforma, *, forza_mock=False):
    if forza_mock or modalita_effettiva(conn) != "production":
        return MockAdapter(piattaforma)
    if piattaforma == "instagram":
        return InstagramAdapter(conn)
    if piattaforma == "linkedin":
        return LinkedInAdapter(conn)
    raise ValueError(f"piattaforma sconosciuta: {piattaforma}")


def can_publish(conn, content, piattaforma):
    """(consentito, motivo). Il motivo del primo blocco e' sempre esplicito."""
    if not config.publishing_enabled_env():
        return False, "GLOBAL_PUBLISHING_ENABLED=false (kill switch environment)"
    if db_social.kill_switch_attivo(conn):
        return False, "kill switch attivo nelle impostazioni di sistema"
    account = db_social.account_per_piattaforma(conn, piattaforma)
    if account is None or account["stato"] != "verificato":
        return False, f"account {piattaforma} non verificato"
    if not account["publishing_enabled"]:
        return False, f"pubblicazione disabilitata sull'account {piattaforma}"
    classe = content["classe_rischio"]
    decisione = content["decisione_rischio"]
    if classe == "rosso" or decisione == "blocked":
        return False, "contenuto bloccato dalla classe di rischio"
    if content["stato"] not in {"APPROVED", "SCHEDULED", "PUBLISHING", "PARTIALLY_PUBLISHED"}:
        return False, f"stato non pubblicabile: {content['stato']}"
    if decisione == "auto_publish" and classe == "verde":
        return True, "ok (verde, auto_publish)"
    # Tutto il resto richiede un'approvazione umana registrata.
    approvazione = conn.execute(
        "SELECT 1 FROM social_approvals WHERE content_id = ? AND stato = 'approvato'",
        (content["id"],)).fetchone()
    if approvazione is None:
        return False, "manca l'approvazione umana (classe non verde)"
    return True, "ok (approvato da revisore)"


def pubblica_contenuto(conn, content_id, *, utente_id=None):
    """Pubblica il contenuto su tutti i canali previsti. Ritorna il riepilogo
    {piattaforma: esito}. Aggiorna lo stato: PUBLISHED se tutti ok,
    PARTIALLY_PUBLISHED se misto, PUBLISH_FAILED se tutti falliti."""
    content = db_social.get_content(conn, content_id)
    if content is None:
        raise ValueError(f"contenuto inesistente: {content_id}")
    if content["stato"] == "PUBLISHED":
        return {}  # idempotenza a livello contenuto: gia' tutto pubblicato
    if content["stato"] in {"APPROVED", "SCHEDULED"}:
        if content["stato"] == "APPROVED":
            state_machine.transisci(conn, content_id, "SCHEDULED",
                                    utente_id=utente_id, agente="publishing")
        state_machine.transisci(conn, content_id, "PUBLISHING",
                                utente_id=utente_id, agente="publishing")
    elif content["stato"] not in {"PUBLISHING", "PARTIALLY_PUBLISHED", "PUBLISH_FAILED"}:
        raise state_machine.TransizioneNonValida(
            f"contenuto non pubblicabile dallo stato {content['stato']}")
    if content["stato"] == "PUBLISH_FAILED":
        state_machine.transisci(conn, content_id, "PUBLISHING",
                                utente_id=utente_id, agente="publishing")
    content = db_social.get_content(conn, content_id)

    modalita = modalita_effettiva(conn)
    canali = json.loads(content["canali"] or "[]")
    varianti = {v["piattaforma"]: v for v in db_social.varianti_di(conn, content_id)}
    # Lista, non un solo asset per piattaforma: un contenuto con un
    # carosello Instagram ha piu' immagini per lo stesso canale (vedi
    # agents.visual), e un dict semplice ne terrebbe visibile solo l'ultima.
    asset_per_piattaforma = {}
    for a in db_social.asset_di(conn, content_id):
        asset_per_piattaforma.setdefault(a["piattaforma"], []).append(a["percorso"])
    esiti = {}
    for piattaforma in canali:
        # In produzione i controlli sono vincolanti; in mock/sandbox si
        # simula comunque una pubblicazione mock (serve a testare il flusso).
        if modalita == "production":
            consentito, motivo = can_publish(conn, content, piattaforma)
            if not consentito:
                esiti[piattaforma] = f"bloccato: {motivo}"
                db_social.audit(conn, "pubblicazione_bloccata", agente="publishing",
                                oggetto_tipo="content", oggetto_id=content_id,
                                motivo=motivo)
                continue
        variante = varianti.get(piattaforma)
        if variante is None:
            esiti[piattaforma] = "bloccato: variante mancante"
            continue
        pub_id = db_social.apri_publication(
            conn, content_id, piattaforma,
            "reale" if modalita == "production" else "mock")
        if pub_id is None:
            esiti[piattaforma] = "saltato: gia' pubblicato"
            continue
        adapter = adapter_per(conn, piattaforma)
        try:
            percorsi_asset = asset_per_piattaforma.get(piattaforma) or None
            risultato = adapter.publish(variante["testo"], percorsi_asset)
            db_social.chiudi_publication(conn, pub_id, esito="ok",
                                         remote_id=risultato.remote_id,
                                         remote_url=risultato.remote_url)
            esiti[piattaforma] = "pubblicato"
            db_social.audit(conn, "pubblicazione", utente_id=utente_id,
                            agente="publishing", oggetto_tipo="publication",
                            oggetto_id=pub_id,
                            dettagli={"piattaforma": piattaforma, "modalita": modalita,
                                      "remote_id": risultato.remote_id})
        except Exception as errore:
            log.exception("pubblicazione %s fallita per %s", piattaforma, content_id)
            db_social.chiudi_publication(conn, pub_id, esito="errore", errore=str(errore))
            db_social.registra_incidente(conn, "publishing",
                                         f"{piattaforma}/{content_id}: {errore}")
            esiti[piattaforma] = f"fallito: {errore}"

    pubblicati = sum(1 for e in esiti.values() if e in {"pubblicato", "saltato: gia' pubblicato"})
    if pubblicati == len(canali) and canali:
        state_machine.transisci(conn, content_id, "PUBLISHED", agente="publishing")
    elif pubblicati > 0:
        state_machine.transisci(conn, content_id, "PARTIALLY_PUBLISHED", agente="publishing")
    else:
        state_machine.transisci(conn, content_id, "PUBLISH_FAILED", agente="publishing")
    return esiti
