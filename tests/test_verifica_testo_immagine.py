"""Verifica automatica del testo disegnato dall'AI dentro un'immagine
(badge/titolo/dati/CTA, vedi images._prompt_grafica_intera): segnalato
dall'utente dopo refusi/accenti storpiati ricorrenti nelle grafiche generate
anche col prompt gia' esplicito su ortografia — chiedere "piu' educatamente"
nel prompt di generazione non basta, serve un giudizio indipendente
sull'immagine finita e un ritentativo automatico quando sbaglia."""

import tempfile
from pathlib import Path

from PIL import Image

from social import agents, images, llm, models


class _ImageProviderFinto:
    """Come MockImageProvider, ma con nome configurabile (per testare che
    la verifica scatti SOLO per provider "openai_images") e che registra
    ogni ImageGenerationRequest ricevuta."""

    def __init__(self, nome="openai_images"):
        self.nome = nome
        self.richieste = []

    async def generate(self, request):
        self.richieste.append(request)
        percorso = Path(tempfile.mkdtemp()) / f"finta_{len(self.richieste)}.png"
        Image.new("RGB", (2, 2), "#FFFFFF").save(percorso, "PNG")
        return images.GeneratedAsset(percorso=percorso, provider=self.nome,
                                     template=request.template, formato=request.formato)


def _richiesta(template="nuovo_concorso"):
    return images.ImageGenerationRequest(
        template=template, formato="instagram_feed", titolo="Concorso pubblico di prova",
        dati_chiave=["Posti: 5"])


def test_verifica_testo_immagine_manda_l_immagine_al_provider(conn):
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.VerificaTestoImmagine,
                     models.VerificaTestoImmagine(testo_corretto=True, problemi=[]))
    ok, problemi = agents._verifica_testo_immagine(conn, b"finti byte png", _richiesta(),
                                                    provider=provider)
    assert ok is True
    assert problemi == []
    assert provider.immagini_ricevute == [b"finti byte png"]


def test_verifica_testo_immagine_include_le_stringhe_esatte_attese(conn):
    provider = llm.MockLLMProvider(conn)
    provider.imposta(models.VerificaTestoImmagine,
                     models.VerificaTestoImmagine(testo_corretto=True, problemi=[]))
    richiesta = images.ImageGenerationRequest(
        template="nuovo_concorso", formato="instagram_feed", titolo="Concorso pubblico AIFA",
        sottotitolo="Sottotitolo di prova", dati_chiave=["Posti: 3", "Scadenza: 10/09/2026"])
    agents._verifica_testo_immagine(conn, b"finti byte png", richiesta, provider=provider)
    _, user_prompt, _ = provider.chiamate[0]
    assert '"Concorso pubblico AIFA"' in user_prompt
    assert '"Sottotitolo di prova"' in user_prompt
    assert '"Posti: 3"' in user_prompt
    assert '"Scadenza: 10/09/2026"' in user_prompt


def test_genera_con_verifica_testo_provider_mock_non_verifica(conn):
    """MockImageProvider (usato nei test/demo) non disegna testo AI: niente
    da verificare, una sola generate() -- niente chiamate LLM sprecate."""
    finto = _ImageProviderFinto(nome="mock")
    llm_provider = llm.MockLLMProvider(conn)
    agents._genera_con_verifica_testo(finto, _richiesta(), conn, llm_provider=llm_provider)
    assert len(finto.richieste) == 1
    assert llm_provider.chiamate == []


def test_genera_con_verifica_testo_template_non_grafica_intera_non_verifica(conn):
    """Un template fuori da images._TEMPLATE_GRAFICA_INTERA (es. "scadenza",
    sfondo + overlay Pillow) non ha testo disegnato dall'AI da verificare,
    anche con un provider "openai_images"."""
    finto = _ImageProviderFinto(nome="openai_images")
    llm_provider = llm.MockLLMProvider(conn)
    agents._genera_con_verifica_testo(finto, _richiesta(template="scadenza"), conn,
                                      llm_provider=llm_provider)
    assert len(finto.richieste) == 1
    assert llm_provider.chiamate == []


def test_genera_con_verifica_testo_ok_non_ritenta(conn):
    finto = _ImageProviderFinto()
    llm_provider = llm.MockLLMProvider(conn)
    llm_provider.imposta(models.VerificaTestoImmagine,
                         models.VerificaTestoImmagine(testo_corretto=True, problemi=[]))
    agents._genera_con_verifica_testo(finto, _richiesta(), conn, llm_provider=llm_provider)
    assert len(finto.richieste) == 1


def test_genera_con_verifica_testo_con_refusi_ritenta_una_volta_con_la_nota(conn):
    """Se la verifica trova refusi, ritenta UNA volta (mai piu' di
    _TENTATIVI_VERIFICA_TESTO_IMMAGINE generazioni totali, per non far
    esplodere costo/latenza), passando i problemi trovati come
    nota_correzione al secondo tentativo."""
    finto = _ImageProviderFinto()
    llm_provider = llm.MockLLMProvider(conn)
    llm_provider.imposta(models.VerificaTestoImmagine, models.VerificaTestoImmagine(
        testo_corretto=False, problemi=['il badge dice "NUVO" invece di "NUOVO"']))
    agents._genera_con_verifica_testo(finto, _richiesta(), conn, llm_provider=llm_provider)
    assert len(finto.richieste) == agents._TENTATIVI_VERIFICA_TESTO_IMMAGINE
    assert finto.richieste[0].nota_correzione is None
    assert finto.richieste[1].nota_correzione == 'il badge dice "NUVO" invece di "NUOVO"'


def test_genera_con_verifica_testo_errore_nella_verifica_usa_comunque_l_immagine(conn):
    """Un errore nella verifica stessa (es. rete) non deve mai bloccare la
    pipeline: si usa l'immagine gia' generata senza verifica, non si
    solleva l'eccezione."""
    finto = _ImageProviderFinto()

    class _ProviderCheEsplode:
        async def generate_structured(self, *a, **k):
            raise RuntimeError("errore di rete finto")

    generato = agents._genera_con_verifica_testo(finto, _richiesta(), conn,
                                                  llm_provider=_ProviderCheEsplode())
    assert generato is not None
    assert len(finto.richieste) == 1
