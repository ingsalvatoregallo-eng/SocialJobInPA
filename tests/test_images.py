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
