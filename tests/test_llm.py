import asyncio

import pytest

from social import db_social, llm, models


def test_mock_provider_rispetta_lo_schema(conn):
    provider = llm.MockLLMProvider(conn)
    risultato = asyncio.run(provider.generate_structured(
        "system", "user", models.RisultatoRicerca))
    assert isinstance(risultato, models.RisultatoRicerca)
    assert risultato.fatti[0].confidenza <= 1


def test_mock_provider_risposte_personalizzate(conn):
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.ValutazioneRischio, models.ValutazioneRischio(
        classe="rosso", punteggio_accuratezza=0, punteggio_brand=0,
        punteggio_conformita=0, motivi=["test"]))
    risultato = asyncio.run(provider.generate_structured(
        "s", "u", models.ValutazioneRischio))
    assert risultato.classe == "rosso"


def test_factory_ritorna_mock_senza_chiave(conn, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = llm.provider_llm(conn, mode="sandbox")
    assert isinstance(provider, llm.MockLLMProvider)


def test_budget_blocca_al_100_percento(conn):
    budget = llm.BudgetManager(conn, "anthropic", budget_mensile_eur=1.0)
    db_social.registra_costo(conn, "anthropic", 0.99)
    budget.verifica(costo_stimato_eur=0.0)  # sotto il limite: ok
    db_social.registra_costo(conn, "anthropic", 0.02)
    with pytest.raises(llm.BudgetEsaurito):
        budget.verifica()
    assert any(i["tipo"] == "budget" for i in db_social.incidenti_aperti(conn))


def test_budget_incidente_alla_soglia_80(conn):
    budget = llm.BudgetManager(conn, "anthropic", budget_mensile_eur=10.0)
    db_social.registra_costo(conn, "anthropic", 8.5)
    budget.verifica()  # sopra l'80% ma sotto il 100%: passa ma segnala
    incidenti = db_social.incidenti_aperti(conn)
    assert any("soglia 80%" in (i["dettaglio"] or "") for i in incidenti)
    # non duplica l'incidente alla verifica successiva
    budget.verifica()
    incidenti_dopo = [i for i in db_social.incidenti_aperti(conn)
                      if "soglia 80%" in (i["dettaglio"] or "")]
    assert len(incidenti_dopo) == 1


def test_budget_giornaliero(conn):
    budget = llm.BudgetManager(conn, "anthropic", budget_mensile_eur=100.0,
                               budget_giornaliero_eur=0.5)
    db_social.registra_costo(conn, "anthropic", 0.6)
    with pytest.raises(llm.BudgetEsaurito):
        budget.verifica()


def test_circuit_breaker_apre_e_richiude():
    breaker = llm.CircuitBreaker(soglia_errori=2, cooldown_secondi=0.05)
    breaker.verifica()
    breaker.errore()
    breaker.verifica()  # 1 errore: ancora chiuso
    breaker.errore()
    with pytest.raises(llm.CircuitAperto):
        breaker.verifica()
    import time
    time.sleep(0.06)
    breaker.verifica()  # cooldown passato
    breaker.successo()
    assert breaker.errori_consecutivi == 0


def test_anthropic_richiede_chiave(conn, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(llm.ErroreProvider):
        llm.AnthropicProvider(conn)


class _RispostaAnthropicFinta:
    def __init__(self, input_schema):
        self.usage = type("Uso", (), {"input_tokens": 10, "output_tokens": 5})()
        blocco = type("Blocco", (), {"type": "tool_use", "input": input_schema})()
        self.content = [blocco]


def test_anthropic_generate_structured_allega_l_immagine_quando_presente(conn, monkeypatch):
    """immagine_bytes (vedi agents._verifica_testo_immagine) deve arrivare
    ad Anthropic come blocco immagine nel messaggio, non solo come testo:
    senza, un modello con visione non avrebbe nulla da guardare."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-finta")
    provider = llm.AnthropicProvider(conn)
    catturato = {}

    async def _create_finto(**kwargs):
        catturato.update(kwargs)
        return _RispostaAnthropicFinta({"testo_corretto": True, "problemi": []})

    provider._client.messages.create = _create_finto
    asyncio.run(provider.generate_structured(
        "system", "controlla questa immagine", models.VerificaTestoImmagine,
        immagine_bytes=b"png finto"))

    contenuto = catturato["messages"][0]["content"]
    assert isinstance(contenuto, list)
    assert contenuto[0]["type"] == "image"
    assert contenuto[0]["source"]["media_type"] == "image/png"
    assert contenuto[1] == {"type": "text", "text": "controlla questa immagine"}


def test_anthropic_generate_structured_senza_immagine_manda_solo_testo(conn, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-finta")
    provider = llm.AnthropicProvider(conn)
    catturato = {}

    async def _create_finto(**kwargs):
        catturato.update(kwargs)
        return _RispostaAnthropicFinta({})

    provider._client.messages.create = _create_finto
    asyncio.run(provider.generate_structured("system", "testo libero", models.RisultatoRicerca))

    assert catturato["messages"][0]["content"] == "testo libero"


# --- Recupero da un campo lista/dict tornato come stringa JSON -------------
# Bug reale osservato in produzione (generate_week_plan, PianoSettimanale):
# Anthropic a volte restituisce un campo list[Model] come stringa JSON
# invece del valore nativo, a volte perfino "ri-avvolta" (la stringa
# contiene di nuovo la stessa chiave). model_validate() falliva sempre con
# "Input should be a valid list", 3 tentativi identici inutili (non e' un
# problema di rete) e il job restava a lungo 'pending' con backoff —
# segnalato dall'utente: "Genera 3 temi" restava bloccato su "in corso"
# per ore, anche ricaricando la pagina a mano.

def test_decodifica_campi_json_annidati_stringa_semplice(conn):
    dato = {"voci": '[{"tema": "Prova"}]'}
    corretto = llm._decodifica_campi_json_annidati(dato, models.PianoSettimanale)
    assert corretto["voci"] == [{"tema": "Prova"}]


def test_decodifica_campi_json_annidati_stringa_ri_avvolta(conn):
    """Caso reale osservato: la stringa contiene di nuovo la stessa chiave
    ({"voci": "{\"voci\": [...]}"} invece di {"voci": [...]})."""
    dato = {"voci": '{"voci": [{"tema": "Prova"}]}'}
    corretto = llm._decodifica_campi_json_annidati(dato, models.PianoSettimanale)
    assert corretto["voci"] == [{"tema": "Prova"}]


def test_decodifica_campi_json_annidati_lascia_invariati_i_campi_gia_corretti(conn):
    dato = {"voci": [{"tema": "Prova"}]}
    corretto = llm._decodifica_campi_json_annidati(dato, models.PianoSettimanale)
    assert corretto["voci"] == [{"tema": "Prova"}]


def test_decodifica_campi_json_annidati_stringa_non_json_resta_invariata(conn):
    """Se la stringa non e' nemmeno JSON valido, non deve esplodere: la
    validazione originale fallira' comunque a valle, coi propri dettagli
    sull'errore reale."""
    dato = {"voci": "non e' json"}
    corretto = llm._decodifica_campi_json_annidati(dato, models.PianoSettimanale)
    assert corretto["voci"] == "non e' json"


def test_anthropic_generate_structured_recupera_da_lista_ri_avvolta(conn, monkeypatch):
    """Riproduce esattamente il bug osservato in produzione: il tool_use
    restituisce {"voci": "{\"voci\": [...]}"} invece di {"voci": [...]} --
    generate_structured deve comunque produrre un PianoSettimanale valido
    al primo tentativo, senza scaricare l'errore sul chiamante/sul job."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-finta")
    provider = llm.AnthropicProvider(conn)
    voce = {"tema": "Concorso della settimana", "pillar": "opportunita", "obiettivo": "traffico",
           "fascia_oraria": "12:00-14:00", "giorno_settimana": "martedi",
           "categoria_nome": "Concorsi"}
    import json as json_
    input_malformato = {"voci": json_.dumps({"voci": [voce]})}

    async def _create_finto(**kwargs):
        return _RispostaAnthropicFinta(input_malformato)

    provider._client.messages.create = _create_finto
    risultato = asyncio.run(provider.generate_structured("system", "user", models.PianoSettimanale))

    assert len(risultato.voci) == 1
    assert risultato.voci[0].tema == "Concorso della settimana"


def test_anthropic_generate_structured_rilancia_se_ancora_non_valido(conn, monkeypatch):
    """Se anche dopo il tentativo di recupero i dati restano non validi
    (non e' il bug noto, e' un errore diverso), l'eccezione originale deve
    comunque risalire -- niente fallimento silenzioso."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-finta")
    provider = llm.AnthropicProvider(conn, max_retry=1)

    async def _create_finto(**kwargs):
        return _RispostaAnthropicFinta({"voci": "questo non e' json ne' una lista"})

    provider._client.messages.create = _create_finto
    with pytest.raises(llm.ErroreProvider):
        asyncio.run(provider.generate_structured("system", "user", models.PianoSettimanale))
