"""_contesto_jobinpa legge i bandi SOLO tramite jobinpa_client (API private
del portale, mai un DB condiviso: i due progetti sono separati). Qui si
verifica con un client finto, iniettato via il parametro `client=`."""

from social import agents, db_social, jobinpa_client, llm
from social.images import MockImageProvider

_BANDO_ESEMPIO = {
    "id": "CONC-1", "titolo": "Concorso di prova per 10 posti",
    "enti": ["Comune Demo"], "num_posti": 10, "scadenza": "2026-12-31",
    "stato": "OPEN", "sintesi": "Bando di prova per collaudo.",
    "titolo_studio_richiesto": "Laurea in Informatica",
    "competenze": ["informatica"],
    "url_dettaglio": "https://www.inpa.gov.it/dettaglio",
}


class _ClientFinto:
    def __init__(self, bandi=None, bando_singolo=None,
                bandi_semantici_con_filtri=None, bandi_semantici_senza_filtri=None):
        self._bandi = bandi or []
        self._bando_singolo = bando_singolo
        self._bandi_semantici_con_filtri = bandi_semantici_con_filtri
        self._bandi_semantici_senza_filtri = bandi_semantici_senza_filtri
        self.chiamate = []

    @property
    def configurato(self):
        return True

    def bandi(self, *, stato="OPEN", limit=5, **_):
        self.chiamate.append(("bandi", stato, limit))
        return self._bandi

    def bando(self, concorso_id):
        self.chiamate.append(("bando", concorso_id))
        return self._bando_singolo

    def bandi_semantici(self, query, *, limit=5, **filtri):
        self.chiamate.append(("bandi_semantici", query, limit, dict(filtri)))
        if filtri:
            return self._bandi_semantici_con_filtri or []
        return self._bandi_semantici_senza_filtri or []


def test_contesto_jobinpa_con_bandi_dalla_api(conn):
    client = _ClientFinto(bandi=[_BANDO_ESEMPIO])
    contesto, righe = agents._contesto_jobinpa(None, limite=3, client=client)
    assert "Concorso di prova per 10 posti" in contesto
    assert "Comune Demo" in contesto
    assert "Laurea in Informatica" in contesto
    assert righe == [_BANDO_ESEMPIO]
    assert client.chiamate == [("bandi", "OPEN", 3)]


def test_contesto_jobinpa_per_concorso_specifico(conn):
    client = _ClientFinto(bando_singolo=_BANDO_ESEMPIO)
    contesto, righe = agents._contesto_jobinpa("CONC-1", client=client)
    assert "Concorso di prova per 10 posti" in contesto
    assert righe == [_BANDO_ESEMPIO]
    assert client.chiamate == [("bando", "CONC-1")]


def test_contesto_jobinpa_client_non_configurato(conn, monkeypatch):
    """Senza JOBINPA_API_URL/KEY il client reale ritorna liste vuote: la
    pipeline non deve fallire, solo lavorare con meno contesto.

    JobInPAClient(base_url=None/"") ricade sempre su config.jobinpa_api_url()
    (pattern "or": None e "" sono equivalenti, entrambi "usa il default"),
    quindi va forzato il default stesso — altrimenti su una macchina con
    JOBINPA_API_URL/KEY reali gia' in .env (es. questa di sviluppo) il
    client risulterebbe comunque "configurato" e il test chiamerebbe
    davvero JobInPA invece di restare isolato."""
    monkeypatch.setattr("social.config.jobinpa_api_url", lambda: "")
    monkeypatch.setattr("social.config.jobinpa_api_key", lambda: "")
    client_non_configurato = jobinpa_client.JobInPAClient()
    assert not client_non_configurato.configurato
    contesto, righe = agents._contesto_jobinpa(None, limite=3, client=client_non_configurato)
    assert contesto == ""
    assert righe == []


# --- Fallback: filtri estratti dal brief ambigui -> riprova senza --------
# Bug riprodotto: interpreta_brief puo' scegliere in modo non deterministico
# fra due valori del vocabolario chiuso genuinamente ambigui per lo stesso
# brief (es. inquadramento "Dirigente" vs "Personale sanitario" per un
# medico dirigente) — un valore "sbagliato" (comunque valido) escludeva
# bandi realmente pertinenti PRIMA che la ricerca semantica li giudicasse,
# annullando un contenuto che aveva fonti reali.

def test_contesto_jobinpa_ripete_senza_filtri_se_filtrati_non_trovano_nulla(conn):
    client = _ClientFinto(bandi_semantici_con_filtri=[], bandi_semantici_senza_filtri=[_BANDO_ESEMPIO])
    contesto, righe = agents._contesto_jobinpa(
        None, client=client, filtri={"inquadramento": "Personale sanitario"},
        query_semantica="dirigenti medici")
    assert righe == [_BANDO_ESEMPIO]
    chiamate_semantiche = [c for c in client.chiamate if c[0] == "bandi_semantici"]
    assert len(chiamate_semantiche) == 2
    assert chiamate_semantiche[0][3] == {"inquadramento": "Personale sanitario"}  # primo tentativo, coi filtri
    assert chiamate_semantiche[1][3] == {}  # secondo tentativo, senza


def test_contesto_jobinpa_non_ripete_se_i_filtri_trovano_gia_qualcosa(conn):
    """Nessuna chiamata sprecata quando il primo tentativo (coi filtri) ha
    gia' successo: il fallback scatta solo quando serve davvero."""
    client = _ClientFinto(bandi_semantici_con_filtri=[_BANDO_ESEMPIO])
    contesto, righe = agents._contesto_jobinpa(
        None, client=client, filtri={"inquadramento": "Dirigente"},
        query_semantica="dirigenti medici")
    assert righe == [_BANDO_ESEMPIO]
    chiamate_semantiche = [c for c in client.chiamate if c[0] == "bandi_semantici"]
    assert len(chiamate_semantiche) == 1


def test_contesto_jobinpa_senza_filtri_non_ripete_una_seconda_volta_a_vuoto(conn):
    """Senza filtri da togliere (brief senza criteri specifici, o gia'
    nessun filtro), un risultato vuoto resta vuoto — niente chiamata
    duplicata e identica alla prima."""
    client = _ClientFinto(bandi_semantici_senza_filtri=[])
    contesto, righe = agents._contesto_jobinpa(
        None, client=client, filtri=None, query_semantica="query qualsiasi")
    assert righe == []
    chiamate_semantiche = [c for c in client.chiamate if c[0] == "bandi_semantici"]
    assert len(chiamate_semantiche) == 1


def test_research_agent_non_esplode_con_bandi_dalla_api(conn, monkeypatch):
    client = _ClientFinto(bandi=[_BANDO_ESEMPIO])
    monkeypatch.setattr("social.jobinpa_client.client", lambda: client)
    content_id = db_social.crea_content(conn, "Idea legata a un bando reale")
    risultato = agents.esegui_pipeline(conn, content_id,
                                       provider=llm.MockLLMProvider(conn),
                                       image_provider=MockImageProvider())
    assert risultato in {"APPROVED", "AWAITING_APPROVAL", "BLOCKED"}
