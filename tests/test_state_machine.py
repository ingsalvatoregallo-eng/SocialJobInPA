import pytest

from social import db_social, state_machine


def test_transizione_valida_registra_audit(conn):
    content_id = db_social.crea_content(conn, "Test")
    riga = state_machine.transisci(conn, content_id, "RESEARCHING", agente="test")
    assert riga["stato"] == "RESEARCHING"
    audit = db_social.audit_recenti(conn, limit=5)
    assert any(a["azione"] == "transizione_stato" and a["stato_prima"] == "IDEA"
               and a["stato_dopo"] == "RESEARCHING" for a in audit)


def test_transizione_arbitraria_bloccata(conn):
    content_id = db_social.crea_content(conn, "Test")
    with pytest.raises(state_machine.TransizioneNonValida):
        state_machine.transisci(conn, content_id, "PUBLISHED")
    assert db_social.get_content(conn, content_id)["stato"] == "IDEA"


def test_stato_sconosciuto_rifiutato(conn):
    content_id = db_social.crea_content(conn, "Test")
    with pytest.raises(state_machine.TransizioneNonValida):
        state_machine.transisci(conn, content_id, "INVENTATO")


def test_contenuto_inesistente(conn):
    with pytest.raises(state_machine.TransizioneNonValida):
        state_machine.transisci(conn, "id-che-non-esiste", "RESEARCHING")


def test_percorso_completo_fino_a_published(conn):
    content_id = db_social.crea_content(conn, "Test")
    for stato in ("RESEARCHING", "DRAFTING", "DRAFT_READY", "GENERATING_VISUAL",
                  "QUALITY_CHECK", "APPROVED", "SCHEDULED", "PUBLISHING", "PUBLISHED",
                  "ARCHIVED"):
        state_machine.transisci(conn, content_id, stato)
    assert db_social.get_content(conn, content_id)["stato"] == "ARCHIVED"


def test_archived_e_terminale(conn):
    content_id = db_social.crea_content(conn, "Test")
    state_machine.transisci(conn, content_id, "CANCELLED")
    state_machine.transisci(conn, content_id, "ARCHIVED")
    with pytest.raises(state_machine.TransizioneNonValida):
        state_machine.transisci(conn, content_id, "IDEA")


def test_tutte_le_transizioni_puntano_a_stati_noti():
    for da, destinazioni in state_machine.TRANSIZIONI.items():
        assert da in state_machine.STATI
        for a in destinazioni:
            assert a in state_machine.STATI
