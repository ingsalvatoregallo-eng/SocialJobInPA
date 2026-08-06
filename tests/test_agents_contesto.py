"""_contesto_jobinpa legge i bandi SOLO tramite jobinpa_client (API private
del portale, mai un DB condiviso: i due progetti sono separati). Qui si
verifica con un client finto, iniettato via il parametro `client=`."""

from social import agents, db_social, jobinpa_client, llm, models
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

    def bandi(self, *, stato="OPEN", limit=5, solo_concorsi=None, **_):
        self.chiamate.append(("bandi", stato, limit, solo_concorsi))
        return self._bandi

    def bando(self, concorso_id):
        self.chiamate.append(("bando", concorso_id))
        return self._bando_singolo

    def bandi_semantici(self, query, *, limit=5, **filtri):
        # Come il vero client: un filtro valorizzato a None equivale a
        # "non passato" (vedi JobInPAClient.bandi_semantici, opzionali
        # scartati se None prima di costruire i params HTTP) --
        # _contesto_jobinpa passa sempre scadenza_da/scadenza_a come kwargs
        # espliciti, anche quando None.
        filtri_validi = {k: v for k, v in filtri.items() if v is not None}
        self.chiamate.append(("bandi_semantici", query, limit, filtri_validi))
        if filtri_validi:
            return self._bandi_semantici_con_filtri or []
        return self._bandi_semantici_senza_filtri or []


def test_contesto_jobinpa_con_bandi_dalla_api(conn):
    client = _ClientFinto(bandi=[_BANDO_ESEMPIO])
    contesto, righe = agents._contesto_jobinpa(None, limite=3, client=client)
    assert "Concorso di prova per 10 posti" in contesto
    assert "Comune Demo" in contesto
    assert "Laurea in Informatica" in contesto
    assert righe == [_BANDO_ESEMPIO]
    assert client.chiamate == [("bandi", "OPEN", 3, None)]


def test_contesto_jobinpa_propaga_solo_concorsi_ai_bandi_semplici(conn):
    client = _ClientFinto(bandi=[_BANDO_ESEMPIO])
    agents._contesto_jobinpa(None, limite=3, client=client, solo_concorsi=True)
    assert client.chiamate == [("bandi", "OPEN", 3, True)]


def test_contesto_jobinpa_propaga_solo_concorsi_alla_ricerca_semantica(conn):
    client = _ClientFinto(bandi_semantici_con_filtri=[_BANDO_ESEMPIO])
    agents._contesto_jobinpa(
        None, client=client, filtri={"regione": "Lombardia"},
        query_semantica="concorsi qualsiasi", solo_concorsi=True)
    chiamata = [c for c in client.chiamate if c[0] == "bandi_semantici"][0]
    assert chiamata[3]["solo_concorsi"] is True


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


def test_contesto_jobinpa_fallback_mantiene_scadenza_scarta_solo_i_filtri_soft(conn):
    """Bug segnalato dall'utente: un brief con vincolo di scadenza ("in
    scadenza nei prossimi 7 giorni") + un filtro da vocabolario (es.
    inquadramento, potenzialmente ambiguo) che insieme non trovano nulla
    faceva scattare il fallback SENZA ALCUN filtro, scadenza inclusa —
    ripescando bandi con scadenze lontanissime, perche' senza quel filtro
    il reranking Claude non ha modo di saperlo (non vede le date). La
    scadenza non e' un'ipotesi ambigua dell'AI come inquadramento: e' un
    vincolo esplicito dell'utente e deve restare applicata anche nel
    fallback."""
    client = _ClientFinto(bandi_semantici_con_filtri=[], bandi_semantici_senza_filtri=[_BANDO_ESEMPIO])
    contesto, righe = agents._contesto_jobinpa(
        None, client=client,
        filtri={"inquadramento": "Personale sanitario",
                "scadenza_da": "2026-08-03", "scadenza_a": "2026-08-10"},
        query_semantica="dirigenti medici in scadenza nei prossimi 7 giorni")
    chiamate_semantiche = [c for c in client.chiamate if c[0] == "bandi_semantici"]
    assert len(chiamate_semantiche) == 2
    primo_filtri, secondo_filtri = chiamate_semantiche[0][3], chiamate_semantiche[1][3]
    assert primo_filtri["inquadramento"] == "Personale sanitario"
    assert primo_filtri["scadenza_da"] == "2026-08-03"
    assert primo_filtri["scadenza_a"] == "2026-08-10"
    # secondo tentativo (fallback): inquadramento scartato, scadenza NO
    assert "inquadramento" not in secondo_filtri
    assert secondo_filtri["scadenza_da"] == "2026-08-03"
    assert secondo_filtri["scadenza_a"] == "2026-08-10"


def test_contesto_jobinpa_filtri_manuali_non_hanno_fallback(conn):
    """filtri_da_ai=False (ricerca avanzata impostata a mano dall'utente,
    vedi web.py): zero risultati coi filtri espliciti NON deve far scattare
    il fallback senza filtri -- un valore scelto dall'utente non e' un
    ipotesi ambigua dell'AI da poter scartare in automatico (segnalato
    dall'utente: vuole un risultato prevedibile, mai una reinterpretazione
    silenziosa al posto della sua scelta esplicita)."""
    client = _ClientFinto(bandi_semantici_con_filtri=[], bandi_semantici_senza_filtri=[_BANDO_ESEMPIO])
    contesto, righe = agents._contesto_jobinpa(
        None, client=client, filtri={"regione": "Lombardia"},
        query_semantica="concorsi qualsiasi", filtri_da_ai=False)
    assert righe == []
    chiamate_semantiche = [c for c in client.chiamate if c[0] == "bandi_semantici"]
    assert len(chiamate_semantiche) == 1  # nessun secondo tentativo senza filtri


def test_contesto_jobinpa_soglia_confidenza_scarta_match_deboli(conn):
    bando_forte = dict(_BANDO_ESEMPIO, coerenza_semantica=92)
    bando_debole = dict(_BANDO_ESEMPIO, id="CONC-2", coerenza_semantica=55)
    client = _ClientFinto(bandi_semantici_con_filtri=[bando_forte, bando_debole])
    contesto, righe = agents._contesto_jobinpa(
        None, client=client, filtri={"regione": "Lombardia"},
        query_semantica="concorsi qualsiasi", soglia_confidenza=80)
    assert righe == [bando_forte]


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


def test_research_forza_solo_concorsi_per_la_strategia_bandi_jobinpa(conn, monkeypatch):
    """Un contenuto senza categoria (default bandi_jobinpa, vedi
    _strategia_fatti_per_content) deve chiedere solo concorsi veri a
    JobInPA — segnalato dall'utente: 'Genera 3 idee' (e la creazione
    manuale) non devono mai mischiare concorsi con mobilita'/distacchi/
    incarichi per collaboratori esterni."""
    client = _ClientFinto(bandi=[_BANDO_ESEMPIO])
    monkeypatch.setattr("social.jobinpa_client.client", lambda: client)
    content_id = db_social.crea_content(conn, "Idea legata a un bando reale")
    agents.research(conn, content_id, provider=llm.MockLLMProvider(conn))
    chiamata_bandi = [c for c in client.chiamate if c[0] == "bandi"][0]
    assert chiamata_bandi[3] is True


def test_research_fatti_vuoti_con_bando_trovato_usa_fallback_dai_dati_reali(conn, monkeypatch):
    """Bug reale riprodotto in produzione: il Research Agent a volte non
    popola 'fatti' anche con una fonte JobInPA completa (bando trovato con
    sintesi/link reali) -- risultato: il Quality & Risk Agent vedeva zero
    fatti verificati e bloccava un contenuto la cui fonte era davvero
    verificata (badge "Verificato via API" corretto, ma nessun 'fatto' a
    supporto), confondendo l'utente. Il fallback deterministico dal bando
    trovato (mai testo libero del modello) deve garantire almeno un fatto
    quando un bando c'e' davvero."""
    client = _ClientFinto(bandi=[_BANDO_ESEMPIO])
    monkeypatch.setattr("social.jobinpa_client.client", lambda: client)
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.RisultatoRicerca, models.RisultatoRicerca(fatti=[], sintesi="Sintesi vuota"))
    content_id = db_social.crea_content(conn, "Idea legata a un bando reale")
    risultato = agents.research(conn, content_id, provider=provider)
    assert len(risultato.fatti) == 1
    assert risultato.fatti[0].fatto == _BANDO_ESEMPIO["sintesi"]
    assert risultato.fatti[0].fonte_url == _BANDO_ESEMPIO["url_dettaglio"]
    assert risultato.fatti[0].confidenza == 1.0
    fatti_salvati = db_social.fatti_di(conn, content_id)
    assert len(fatti_salvati) == 1


def test_research_fatti_gia_presenti_non_vengono_sovrascritti(conn, monkeypatch):
    """Se il modello popola davvero 'fatti' (comportamento normale), il
    fallback non deve intervenire -- si fida del giudizio del Research
    Agent quando c'e'."""
    client = _ClientFinto(bandi=[_BANDO_ESEMPIO])
    monkeypatch.setattr("social.jobinpa_client.client", lambda: client)
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.RisultatoRicerca, models.RisultatoRicerca(
        fatti=[models.FattoVerificato(fatto="Fatto scritto dal modello", confidenza=0.8)],
        sintesi="Sintesi."))
    content_id = db_social.crea_content(conn, "Idea legata a un bando reale")
    risultato = agents.research(conn, content_id, provider=provider)
    assert len(risultato.fatti) == 1
    assert risultato.fatti[0].fatto == "Fatto scritto dal modello"


def test_supervisor_pianifica_settimana_forza_solo_concorsi_nei_bandi_di_riferimento(conn, monkeypatch):
    """I bandi di riferimento passati al prompt del Supervisor ('Genera 3
    idee') non devono includere mobilita'/distacchi/incarichi esterni:
    "Concorsi" (bandi_jobinpa) e' seminata di default (vedi db_social.
    _migra), quindi la chiamata a _contesto_jobinpa avviene sempre qui."""
    client = _ClientFinto(bandi=[_BANDO_ESEMPIO])
    monkeypatch.setattr("social.jobinpa_client.client", lambda: client)
    agents.supervisor_pianifica_settimana(conn, "2026-08-03", provider=llm.MockLLMProvider(conn))
    chiamata_bandi = [c for c in client.chiamate if c[0] == "bandi"][0]
    assert chiamata_bandi[3] is True
