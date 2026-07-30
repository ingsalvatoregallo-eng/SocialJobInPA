"""Strategia fatti 'promozioni_jobinpa' (categoria "Promozioni", seminata
di default — vedi test_categorie.py): niente ricerca bandi. La promozione
forza sempre la revisione umana (stesso motivo di annuncio_funzionalita:
un dato commerciale, anche se letto in diretta da JobInPA, va sempre
controllato da un umano prima di pubblicare).

Il "cosa disegna l'AI" (categorie, prompt, immagine di riferimento) e il
"da dove vengono i dati della promo" (fetch automatico da JobInPA) sono
coperti rispettivamente da test_categorie.py e test_promozioni_auto_fetch.py."""

import pytest

from social import agents, db_social, llm, models
from social.images import MockImageProvider


def _categoria_id(conn, nome):
    return next(c["id"] for c in db_social.lista_categorie(conn) if c["nome"] == nome)


def test_crea_content_tipologia_default_concorso(conn):
    content_id = db_social.crea_content(conn, "Tema qualsiasi")
    content = db_social.get_content(conn, content_id)
    assert content["tipologia"] == "concorso"
    assert content["scadenza_promo"] is None
    assert content["promo_dati"] is None


def test_crea_content_tipologia_non_valida_solleva_errore(conn):
    with pytest.raises(ValueError):
        db_social.crea_content(conn, "Tema", tipologia="non-esiste")


class _ClientJobinpaVietato:
    """Se research() lo interroga per una 'promozione' e' un bug: niente
    bando da cercare, la chiamata non deve avvenire."""

    def bandi(self, *a, **k):
        raise AssertionError("jobinpa_client interrogato per una promozione")

    def bandi_semantici(self, *a, **k):
        raise AssertionError("jobinpa_client interrogato per una promozione")

    def bando(self, *a, **k):
        raise AssertionError("jobinpa_client interrogato per una promozione")

    def filtri_disponibili(self):
        raise AssertionError("jobinpa_client interrogato per una promozione")


def test_research_promozione_non_interroga_jobinpa(conn):
    """Percorso di fallback (senza promo_dati, es. contenuto creato prima
    del fetch automatico): usa comunque solo titolo/scadenza/brief gia'
    salvati, mai una nuova ricerca su JobInPA."""
    content_id = db_social.crea_content(
        conn, "Premium gratis fino al 31 agosto", categoria_id=_categoria_id(conn, "Promozioni"),
        scadenza_promo="2026-08-31", brief="Piano Premium senza costi")
    risultato = agents.research(conn, content_id, provider=llm.MockLLMProvider(conn),
                                jobinpa_client_=_ClientJobinpaVietato())
    assert risultato.annuncio_funzionalita is True
    assert risultato.richiede_revisione is True
    assert risultato.bandi_trovati == []
    assert "Premium gratis fino al 31 agosto" in risultato.fatti[0].fatto
    assert "31 agosto 2026" in risultato.fatti[0].fatto
    assert "Piano Premium senza costi" in risultato.fatti[0].fatto
    content = db_social.get_content(conn, content_id)
    assert content["bandi_trovati"] == "[]"


def test_research_promozione_senza_scadenza_non_rompe(conn):
    content_id = db_social.crea_content(conn, "Promo senza data",
                                        categoria_id=_categoria_id(conn, "Promozioni"))
    risultato = agents.research(conn, content_id, provider=llm.MockLLMProvider(conn),
                                jobinpa_client_=_ClientJobinpaVietato())
    assert "Promo senza data" in risultato.fatti[0].fatto


def test_research_promozione_usa_promo_dati_quando_presenti(conn):
    """Con promo_dati (popolato alla creazione da jobinpa_client.promozioni,
    vedi test_promozioni_auto_fetch.py) il fatto riporta i dati reali letti
    da JobInPA — descrizione, prezzo/prezzo promozionale, scadenza — con
    fonte_url = il link JobInPA, non solo titolo/scadenza."""
    promo = {"tipo": "piano", "chiave": "premium-promo", "nome": "Premium promo",
             "descrizione": "Accesso completo per un mese", "prezzo_eur": 9.99,
             "prezzo_promozionale_eur": 0.0, "scadenza": "2026-08-31",
             "url_jobinpa": "https://jobinpa.it/premium"}
    content_id = db_social.crea_content(conn, "Premium promo",
                                        categoria_id=_categoria_id(conn, "Promozioni"),
                                        promo_dati=promo)
    risultato = agents.research(conn, content_id, provider=llm.MockLLMProvider(conn),
                                jobinpa_client_=_ClientJobinpaVietato())
    fatto = risultato.fatti[0]
    assert "Premium promo" in fatto.fatto
    assert "Accesso completo per un mese" in fatto.fatto
    assert "0.00 EUR" in fatto.fatto
    assert "9.99 EUR" in fatto.fatto
    assert "31 agosto 2026" in fatto.fatto
    assert fatto.fonte_url == "https://jobinpa.it/premium"


def test_esegui_pipeline_promozione_forza_approvazione_anche_a_classe_verde(conn):
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.ValutazioneRischio, models.ValutazioneRischio(
        classe="verde", punteggio_accuratezza=0.95, punteggio_brand=0.95,
        punteggio_conformita=0.95, motivi=[]))
    content_id = db_social.crea_content(
        conn, "Premium gratis fino al 31 agosto", categoria_id=_categoria_id(conn, "Promozioni"),
        scadenza_promo="2026-08-31", canali=["instagram"])
    stato_finale = agents.esegui_pipeline(conn, content_id, provider=provider,
                                          image_provider=MockImageProvider())
    assert stato_finale == "AWAITING_APPROVAL"


def test_esegui_pipeline_categoria_libera_forza_approvazione_anche_a_classe_verde(conn):
    """Stessa garanzia per la strategia 'libera' (categoria "Funzionalità",
    seminata di default): mai pubblicazione automatica su un annuncio
    senza fonte esterna verificabile, anche a classe verde."""
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.ValutazioneRischio, models.ValutazioneRischio(
        classe="verde", punteggio_accuratezza=0.95, punteggio_brand=0.95,
        punteggio_conformita=0.95, motivi=[]))
    content_id = db_social.crea_content(
        conn, "Nuova funzionalità: bandi consigliati per te",
        categoria_id=_categoria_id(conn, "Funzionalità"),
        brief="Il CV viene analizzato per suggerire i bandi più adatti", canali=["instagram"])
    stato_finale = agents.esegui_pipeline(conn, content_id, provider=provider,
                                          image_provider=MockImageProvider())
    assert stato_finale == "AWAITING_APPROVAL"
