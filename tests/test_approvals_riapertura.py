"""richiedi_approvazione() deve sempre lasciare l'approvazione visibile
nella coda "in_attesa" — anche quando riusa una riga gia' decisa in
precedenza (es. dopo "Richiedi modifiche" + rigenerazione). Bug reale:
la riga restava con lo stato vecchio ('modifiche_richieste'), invisibile
in approvals_in_attesa() pur essendo trovata da approval_aperta_di()
(usata dalla scheda del contenuto per il link "Richiesta aperta")."""

from social import approvals, db_social, state_machine


def _content_in_attesa_approvazione(conn, titolo):
    """Percorso di stato reale fino ad AWAITING_APPROVAL: richiedi_modifiche
    fa una transizione di stato vera (AWAITING_APPROVAL -> CHANGES_REQUESTED),
    non valida partendo da IDEA."""
    content_id = db_social.crea_content(conn, titolo)
    for stato in ("RESEARCHING", "DRAFTING", "DRAFT_READY", "GENERATING_VISUAL",
                  "QUALITY_CHECK", "AWAITING_APPROVAL"):
        state_machine.transisci(conn, content_id, stato)
    return content_id


def test_richiedi_approvazione_prima_volta_crea_riga_in_attesa(conn):
    content_id = _content_in_attesa_approvazione(conn, "Prova")
    approvals.richiedi_approvazione(conn, content_id)
    approval = db_social.approval_aperta_di(conn, content_id)
    assert approval["stato"] == "in_attesa"
    assert any(a["id"] == approval["id"] for a in db_social.approvals_in_attesa(conn))


def test_richiedi_approvazione_dopo_modifiche_riappare_in_coda(conn):
    content_id = _content_in_attesa_approvazione(conn, "Prova")
    approvals.richiedi_approvazione(conn, content_id)
    approval = db_social.approval_aperta_di(conn, content_id)
    approvals.richiedi_modifiche(conn, approval["id"], utente_id=1, motivo="Correggi X")
    assert db_social.approval_aperta_di(conn, content_id)["stato"] == "modifiche_richieste"
    assert not any(a["id"] == approval["id"] for a in db_social.approvals_in_attesa(conn))

    # Il contenuto e' stato rigenerato e torna in revisione (stessa
    # transizione reale che fa esegui_pipeline): la STESSA riga (nessun
    # duplicato) deve ridiventare visibile in coda.
    state_machine.transisci(conn, content_id, "RESEARCHING")
    for stato in ("DRAFTING", "DRAFT_READY", "GENERATING_VISUAL",
                  "QUALITY_CHECK", "AWAITING_APPROVAL"):
        state_machine.transisci(conn, content_id, stato)
    approvals.richiedi_approvazione(conn, content_id)

    approval_dopo = db_social.approval_aperta_di(conn, content_id)
    assert approval_dopo["id"] == approval["id"]
    assert approval_dopo["stato"] == "in_attesa"
    assert approval_dopo["motivo"] is None
    assert any(a["id"] == approval["id"] for a in db_social.approvals_in_attesa(conn))


def test_riapri_approval_registra_evento(conn):
    content_id = db_social.crea_content(conn, "Prova")
    approval_id = db_social.crea_approval(conn, content_id)
    db_social.decidi_approval(conn, approval_id, "modifiche_richieste", 1, "motivo vecchio")

    db_social.riapri_approval(conn, approval_id)

    riga = next(a for a in db_social.approvals_in_attesa(conn) if a["id"] == approval_id)
    assert riga["stato"] == "in_attesa"
    assert riga["motivo"] is None
