"""Interprete brief in linguaggio naturale -> filtri reali di JobInPA
(modello CriteriRicerca), e annullamento automatico della pipeline quando
i criteri derivati non trovano nessun bando corrispondente."""

import pytest

from social import agents, db_social, llm, models
from social.images import MockImageProvider


class _ClientFinto:
    """Espone bandi()/filtri_disponibili() come il vero JobInPAClient, ma
    con risposte prefissate — mai una vera chiamata di rete nei test."""

    def __init__(self, bandi=None, filtri=None):
        self._bandi = bandi if bandi is not None else []
        self._filtri = filtri or {
            "regioni": ["Lombardia", "Lazio"],
            "categorie": ["Amministrativo"],
            "settori": [],
            "enti": ["Comune Demo"],
            "inquadramenti": list(["Funzionario / Elevata Qualificazione"]),
            "titoli_studio": ["Laurea triennale"],
            "tipi_contratto": ["tempo indeterminato"],
            "competenze": ["informatica", "diritto_amministrativo"],
            "ambiti": ["Informatica e tecnologia"],
        }
        self.chiamate_bandi = []

    @property
    def configurato(self):
        return True

    def bandi(self, *, limit=5, **filtri):
        self.chiamate_bandi.append(dict(limit=limit, **filtri))
        return self._bandi

    def bando(self, concorso_id):
        return None

    def filtri_disponibili(self):
        return self._filtri


_BANDO_INFORMATICO = {
    "id": "CONC-IT", "titolo": "Funzionario informatico", "enti": ["Comune Demo"],
    "num_posti": 15, "scadenza": "2026-12-31", "stato": "OPEN",
    "sintesi": "Concorso per funzionari con competenze informatiche.",
    "titolo_studio_richiesto": "Laurea in Informatica", "competenze": ["informatica"],
    "url_dettaglio": "https://www.inpa.gov.it/dettaglio",
}


def test_interpreta_brief_usa_solo_valori_dai_vocabolari(conn):
    client = _ClientFinto()
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.CriteriRicerca, models.CriteriRicerca(
        competenza="informatica", posti_minimi=10, nessun_criterio_specifico=False))
    criteri = agents.interpreta_brief(
        conn, "Concorsi informatici con più di 10 posti", provider=provider, client=client)
    assert criteri.competenza == "informatica"
    assert criteri.posti_minimi == 10
    assert not criteri.nessun_criterio_specifico


def test_filtri_da_criteri_esclude_i_campi_vuoti():
    criteri = models.CriteriRicerca(competenza="informatica", posti_minimi=10)
    filtri = agents._filtri_da_criteri(criteri)
    assert filtri == {"competenza": "informatica", "posti_minimi": 10}


def test_filtri_da_criteri_rinomina_query_testuale():
    criteri = models.CriteriRicerca(query_testuale="cybersecurity")
    filtri = agents._filtri_da_criteri(criteri)
    assert filtri == {"query": "cybersecurity"}


def test_research_con_criteri_specifici_e_bandi_trovati(conn):
    client = _ClientFinto(bandi=[_BANDO_INFORMATICO])
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.CriteriRicerca, models.CriteriRicerca(
        competenza="informatica", posti_minimi=10, nessun_criterio_specifico=False))
    content_id = db_social.crea_content(
        conn, "Concorsi IT", brief="Concorsi informatici con più di 10 posti")
    risultato = agents.research(conn, content_id, provider=provider, jobinpa_client_=client)
    assert client.chiamate_bandi == [{"limit": 10, "competenza": "informatica", "posti_minimi": 10}]
    assert risultato.fatti or risultato.sintesi is not None  # non solleva, prosegue normale


def test_research_con_criteri_specifici_e_zero_bandi_annulla(conn):
    client = _ClientFinto(bandi=[])  # nessun bando soddisfa i criteri
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.CriteriRicerca, models.CriteriRicerca(
        competenza="cybersecurity", posti_minimi=50, nessun_criterio_specifico=False))
    content_id = db_social.crea_content(
        conn, "Concorsi cyber", brief="Concorsi cybersecurity con più di 50 posti")
    with pytest.raises(agents.NessunBandoCorrispondente):
        agents.research(conn, content_id, provider=provider, jobinpa_client_=client)


def test_research_senza_criteri_specifici_non_annulla_anche_se_vuoto(conn):
    """Un brief generico (es. 'novita della settimana') non deve mai
    annullare la pipeline anche se JobInPA non ha nulla da mostrare in quel
    momento — e' il comportamento di sempre (ricerca generica), non un
    criterio da soddisfare."""
    client = _ClientFinto(bandi=[])
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.CriteriRicerca, models.CriteriRicerca(
        nessun_criterio_specifico=True))
    content_id = db_social.crea_content(
        conn, "Novità della settimana", brief="Raccontiamo le novità di questa settimana")
    risultato = agents.research(conn, content_id, provider=provider, jobinpa_client_=client)
    assert client.chiamate_bandi == [{"limit": 10, "stato": "OPEN"}]  # ricerca generica, non filtrata
    assert risultato is not None


def test_research_senza_brief_non_interpreta_niente(conn):
    """Nessun brief -> nessuna chiamata a interpreta_brief (risparmia un
    giro LLM inutile): comportamento invariato, ricerca generica."""
    client = _ClientFinto(bandi=[_BANDO_INFORMATICO])
    provider = llm.MockLLMProvider(conn)
    content_id = db_social.crea_content(conn, "Contenuto senza brief")
    agents.research(conn, content_id, provider=provider, jobinpa_client_=client)
    assert client.chiamate_bandi == [{"limit": 10, "stato": "OPEN"}]
    # nessuna chiamata al prompt interpreta_brief tra le chiamate LLM registrate
    esecuzioni = db_social.agent_runs_recenti(conn)
    assert not any(r["prompt_nome"] == "interpreta_brief" for r in esecuzioni)


def test_pipeline_annullata_per_zero_corrispondenze(conn, monkeypatch):
    client = _ClientFinto(bandi=[])
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.CriteriRicerca, models.CriteriRicerca(
        competenza="cybersecurity", posti_minimi=50, nessun_criterio_specifico=False))
    content_id = db_social.crea_content(
        conn, "Concorsi cyber", brief="Concorsi cybersecurity con più di 50 posti")

    # esegui_pipeline() non accetta un client esplicito: usa sempre
    # jobinpa_client.client() (stesso pattern di test_agents_contesto.py).
    monkeypatch.setattr("social.jobinpa_client.client", lambda: client)
    stato = agents.esegui_pipeline(conn, content_id, provider=provider,
                                   image_provider=MockImageProvider())

    assert stato == "CANCELLED"
    content = db_social.get_content(conn, content_id)
    assert content["stato"] == "CANCELLED"
    assert "cybersecurity" in content["errore"] or "criteri" in content["errore"]
    # nessuna variante/asset creati: la pipeline si e' fermata subito dopo la ricerca
    assert db_social.varianti_di(conn, content_id) == []
    assert db_social.asset_di(conn, content_id) == []
