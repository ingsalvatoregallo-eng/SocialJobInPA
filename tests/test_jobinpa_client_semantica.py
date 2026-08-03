"""JobInPAClient.bandi_semantici(): chiama il nuovo endpoint interno di
ricerca semantica di JobInPA, con lo stesso stile difensivo di bandi()
(mai un'eccezione che rompe la pipeline: [] se non configurato o in caso
di errore di rete)."""

from unittest import mock

from social.jobinpa_client import JobInPAClient


def _client_configurato():
    return JobInPAClient(base_url="https://jobinpa.it", api_key="chiave-di-test")


def test_bandi_semantici_non_configurato_ritorna_lista_vuota(monkeypatch):
    """base_url="" da solo non basta a forzare 'non configurato': il
    costruttore fa `base_url or config.jobinpa_api_url()`, quindi una
    stringa vuota (falsy) ricade sul valore REALMENTE configurato in questo
    ambiente (rischio concreto: una chiamata di rete vera contro
    jobinpa.it durante i test, osservato qui). Va forzato anche il default
    di config, stesso principio gia' applicato in
    test_agents_contesto.py::test_contesto_jobinpa_client_non_configurato."""
    monkeypatch.setattr("social.config.jobinpa_api_url", lambda: "")
    monkeypatch.setattr("social.config.jobinpa_api_key", lambda: "")
    client = JobInPAClient(base_url="", api_key="")
    assert not client.configurato
    assert client.bandi_semantici("concorsi informatici") == []


def test_bandi_semantici_chiama_l_endpoint_corretto_con_la_chiave():
    client = _client_configurato()
    risposta_finta = mock.Mock()
    risposta_finta.json.return_value = {"bandi": [{"id": "CONC-1"}]}
    risposta_finta.raise_for_status.return_value = None
    with mock.patch("social.jobinpa_client.requests.get", return_value=risposta_finta) as finto:
        risultato = client.bandi_semantici("concorsi informatici a Milano")
    assert risultato == [{"id": "CONC-1"}]
    url_chiamato, kwargs = finto.call_args
    assert url_chiamato[0] == "https://jobinpa.it/api/internal/bandi/semantica"
    assert kwargs["headers"] == {"X-Internal-Api-Key": "chiave-di-test"}
    assert kwargs["params"]["query"] == "concorsi informatici a Milano"
    assert kwargs["params"]["stato"] == "OPEN"
    assert kwargs["params"]["limit"] == 5


def test_bandi_semantici_passa_solo_i_filtri_valorizzati():
    client = _client_configurato()
    risposta_finta = mock.Mock()
    risposta_finta.json.return_value = {"bandi": []}
    risposta_finta.raise_for_status.return_value = None
    with mock.patch("social.jobinpa_client.requests.get", return_value=risposta_finta) as finto:
        client.bandi_semantici("test", regione="Lombardia", titolo_studio=None, ambito="Informatica")
    _, kwargs = finto.call_args
    assert kwargs["params"]["regione"] == "Lombardia"
    assert kwargs["params"]["ambito"] == "Informatica"
    assert "titolo_studio" not in kwargs["params"]


def test_bandi_semantici_passa_scadenza_da_e_scadenza_a():
    """scadenza_da/scadenza_a sono l'unico modo affidabile di applicare un
    vincolo temporale ('in scadenza nei prossimi 7 giorni'): un filtro DURO
    lato JobInPA (applicato prima del reranking AI), non testo libero che
    il reranking non puo' verificare (non vede le date dei bandi)."""
    client = _client_configurato()
    risposta_finta = mock.Mock()
    risposta_finta.json.return_value = {"bandi": []}
    risposta_finta.raise_for_status.return_value = None
    with mock.patch("social.jobinpa_client.requests.get", return_value=risposta_finta) as finto:
        client.bandi_semantici("test", scadenza_da="2026-08-03", scadenza_a="2026-08-10")
    _, kwargs = finto.call_args
    assert kwargs["params"]["scadenza_da"] == "2026-08-03"
    assert kwargs["params"]["scadenza_a"] == "2026-08-10"


def test_bandi_semantici_errore_di_rete_ritorna_lista_vuota():
    import requests
    client = _client_configurato()
    with mock.patch("social.jobinpa_client.requests.get",
                    side_effect=requests.RequestException("boom")):
        assert client.bandi_semantici("test") == []
