import asyncio

import pytest
from PIL import Image

from social import images


@pytest.mark.parametrize("template", images.TEMPLATE_VALIDI)
def test_tutti_i_template_renderizzano(tmp_path, template):
    provider = images.TemplateImageProvider(output_dir=tmp_path)
    asset = provider.genera_sync(images.ImageGenerationRequest(
        template=template, formato="instagram_feed",
        titolo="Concorso pubblico di prova con un titolo piuttosto lungo",
        sottotitolo="Ente di prova", dati_chiave=["10 posti", "Scadenza 31/12/2026"]))
    assert asset.percorso.exists()
    with Image.open(asset.percorso) as img:
        assert img.size == (1080, 1350)
        assert img.format == "PNG"


def _hex_a_rgb(colore_hex):
    colore_hex = colore_hex.lstrip("#")
    return tuple(int(colore_hex[i:i + 2], 16) for i in (0, 2, 4))


def test_titolo_lungo_non_sconfina_nel_footer(tmp_path):
    """Regressione: con titolo/sottotitolo/dati_chiave molto lunghi il testo
    finiva disegnato dietro la barra blu del footer invece di fermarsi
    prima (bug osservato in produzione: 'Funzionari amministrativi: nessun
    bando disponibile questa' tagliato a meta' sopra il footer)."""
    provider = images.TemplateImageProvider(output_dir=tmp_path)
    titolo_lunghissimo = (
        "Funzionari amministrativi per enti pubblici centrali e periferici: "
        "nessun bando disponibile questa settimana per il profilo richiesto "
        "dal brief con più di dieci posti disponibili in totale")
    asset = provider.genera_sync(images.ImageGenerationRequest(
        template="opportunita_settimana", formato="instagram_feed", titolo=titolo_lunghissimo,
        sottotitolo="Un sottotitolo altrettanto lungo per mettere sotto stress il layout",
        dati_chiave=["Dato molto lungo numero uno", "Dato molto lungo numero due",
                    "Dato molto lungo numero tre", "Dato quattro", "Dato cinque"]))
    with Image.open(asset.percorso) as img:
        larghezza, altezza = img.size
        scala = larghezza / 1080
        altezza_footer = int(110 * scala)
        fascia_footer = img.crop((0, altezza - altezza_footer, larghezza, altezza))
        colori_nella_fascia = set(fascia_footer.getdata())
        # Il testo del titolo/sottotitolo (colore "testo") e i riquadri dei
        # dati chiave (bordo "accento") non devono MAI comparire nella
        # fascia del footer: se ci sono, hanno sconfinato.
        assert _hex_a_rgb(images.PALETTE_DEFAULT["testo"]) not in colori_nella_fascia
        assert _hex_a_rgb(images.PALETTE_DEFAULT["accento"]) not in colori_nella_fascia


@pytest.mark.parametrize("formato,atteso", [
    ("instagram_feed", (1080, 1350)), ("instagram_square", (1080, 1080)),
    ("instagram_story", (1080, 1920)), ("linkedin", (1200, 627))])
def test_formati_richiesti(tmp_path, formato, atteso):
    provider = images.TemplateImageProvider(output_dir=tmp_path)
    asset = provider.genera_sync(images.ImageGenerationRequest(
        template="presentazione", formato=formato, titolo="JobInPA"))
    with Image.open(asset.percorso) as img:
        assert img.size == atteso


def test_template_sconosciuto_rifiutato(tmp_path):
    provider = images.TemplateImageProvider(output_dir=tmp_path)
    with pytest.raises(ValueError):
        provider.genera_sync(images.ImageGenerationRequest(
            template="inventato", formato="linkedin", titolo="x"))


def test_formato_sconosciuto_rifiutato(tmp_path):
    provider = images.TemplateImageProvider(output_dir=tmp_path)
    with pytest.raises(ValueError):
        provider.genera_sync(images.ImageGenerationRequest(
            template="faq", formato="4k", titolo="x"))


def test_mock_provider(tmp_path):
    provider = images.MockImageProvider(output_dir=tmp_path)
    asset = asyncio.run(provider.generate(images.ImageGenerationRequest(
        template="faq", formato="linkedin", titolo="x")))
    assert asset.percorso.exists()
    assert provider.richieste[0].template == "faq"


def test_factory_in_mock_mode(conn):
    provider = images.provider_immagini(conn, mode="mock")
    assert isinstance(provider, images.MockImageProvider)


def test_factory_senza_openai_usa_template(conn, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ENABLE_AI_IMAGES", "true")
    provider = images.provider_immagini(conn, mode="sandbox")
    assert isinstance(provider, images.TemplateImageProvider)
