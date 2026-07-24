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
    def __init__(self, bandi=None, bando_singolo=None):
        self._bandi = bandi or []
        self._bando_singolo = bando_singolo
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


def test_research_agent_non_esplode_con_bandi_dalla_api(conn, monkeypatch):
    client = _ClientFinto(bandi=[_BANDO_ESEMPIO])
    monkeypatch.setattr("social.jobinpa_client.client", lambda: client)
    content_id = db_social.crea_content(conn, "Idea legata a un bando reale")
    risultato = agents.esegui_pipeline(conn, content_id,
                                       provider=llm.MockLLMProvider(conn),
                                       image_provider=MockImageProvider())
    assert risultato in {"APPROVED", "AWAITING_APPROVAL", "BLOCKED"}
