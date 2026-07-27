"""Il filtro data_breve mostrava l'ISO grezzo troncato (quasi sempre UTC,
vedi programmato_at/creato_at/richiesto_at) senza mai convertirlo nel
fuso locale: un orario futuro corretto (es. le 18:00 di Roma, salvato
come 16:00 UTC in estate) appariva "gia' passato" a chi confrontava con
l'orologio di casa — bug reale, segnalato dall'utente su una
programmazione vera."""

import os

import pytest

os.environ.setdefault("DEFAULT_TIMEZONE", "Europe/Rome")

from social.web import _data_breve  # noqa: E402


@pytest.mark.parametrize("iso,atteso", [
    ("2026-07-27T16:00:00+00:00", "2026-07-27 18:00"),  # CEST, UTC+2 in estate
    ("2026-01-15T10:00:00+00:00", "2026-01-15 11:00"),  # CET, UTC+1 in inverno
    ("2026-07-27T16:00:00", "2026-07-27 18:00"),  # naive: trattato come UTC
    (None, ""),
    ("", ""),
])
def test_data_breve_converte_da_utc_a_fuso_locale(iso, atteso):
    assert _data_breve(iso) == atteso


def test_data_breve_valore_non_iso_torna_troncato_senza_errore():
    assert _data_breve("testo non valido lungo") == "testo non valido"
