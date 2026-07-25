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


@pytest.mark.parametrize("y_iniziale,passo,y_limite,atteso", [
    (0, 100, 350, 3),      # 3 interi (300<=350), il 4* (400) non ci sta
    (0, 100, 300, 3),      # esattamente al limite: ci sta
    (0, 100, 299, 2),
    (500, 50, 400, 0),     # gia' oltre il limite: zero elementi
    (100, 0, 500, 0),      # passo non valido: zero elementi, mai un loop infinito
])
def test_numero_di_elementi_che_entrano(y_iniziale, passo, y_limite, atteso):
    """La stessa aritmetica usata per fermare titolo/sottotitolo/righe della
    card prima del footer, isolata dal rendering: e' quella che garantisce
    che un testo lungo si fermi invece di sconfinare, verificabile senza
    generare immagini."""
    assert images._numero_di_elementi_che_entrano(y_iniziale, passo, y_limite) == atteso


def test_titolo_lunghissimo_non_sconfina_nel_footer(tmp_path):
    """Regressione: con titolo/sottotitolo/dati_chiave molto lunghi il testo
    finiva disegnato dietro il footer invece di fermarsi prima (bug
    osservato in produzione: 'Funzionari amministrativi: nessun bando
    disponibile questa' tagliato a meta' sopra il footer). La card bianca
    dei dati_chiave non deve mai comparire nella fascia riservata al
    footer: se ci fosse, vorrebbe dire che ha sconfinato."""
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
        altezza_footer = int(120 * scala)
        fascia_footer = img.crop((0, altezza - altezza_footer, larghezza, altezza))
        colori_nella_fascia = set(fascia_footer.getdata())
        assert _hex_a_rgb(images.PALETTE_DEFAULT["card"]) not in colori_nella_fascia


def test_dato_chiave_lungo_non_sconfina_a_destra(tmp_path):
    """Regressione: un dato_chiave lungo (es. 'Tipo contratto: tempo
    indeterminato, tempo pieno') veniva disegnato su una riga sola senza
    alcun limite di larghezza e finiva tagliato fuori dal riquadro e dal
    bordo destro dell'immagine (osservato in produzione sulle card
    'nuovo_concorso'). Verifica che nessun pixel di colore 'testo' compaia
    nella fascia di margine destro riservata al bordo sicuro."""
    provider = images.TemplateImageProvider(output_dir=tmp_path)
    asset = provider.genera_sync(images.ImageGenerationRequest(
        template="nuovo_concorso", formato="instagram_feed", titolo="AIFA: concorso per 5 posti",
        sottotitolo="Area dei Funzionari - profilo amministrativo gestionale",
        dati_chiave=[
            "Ente: Agenzia Italiana del Farmaco (AIFA)", "Posti: 5",
            "Tipo contratto: tempo indeterminato, tempo pieno",
            "Area: Funzionari - Famiglia professionale amministrativo gestionale",
            "Modalità selezione: concorso pubblico per esami"]))
    with Image.open(asset.percorso) as img:
        larghezza, altezza = img.size
        scala = larghezza / 1080
        margine = int(larghezza * images.MARGINE_SICURO)
        altezza_footer = int(120 * scala)
        fascia_destra = img.crop((larghezza - margine, 0, larghezza, altezza - altezza_footer))
        colori_nella_fascia = set(fascia_destra.getdata())
        assert _hex_a_rgb(images.PALETTE_DEFAULT["testo"]) not in colori_nella_fascia


def test_titolo_non_tronca_mai_va_a_capo_o_rimpicciolisce(tmp_path):
    """Regressione: una parola composta lunga (es.
    'amministrativo-gestionali') e' indivisibile per _a_capo (nessuno
    spazio) e poteva sconfinare oltre il margine destro. Ora deve stare
    dentro i margini SENZA MAI perdere contenuto (niente ellissi): a capo,
    rimpicciolita, o come ultima rete di sicurezza spezzata a meta' parola."""
    provider = images.TemplateImageProvider(output_dir=tmp_path)
    parola_mostruosa = "Amministrativogestionaledirigenzialecoordinamentotecnicooperativo" * 2
    asset = provider.genera_sync(images.ImageGenerationRequest(
        template="faq", formato="instagram_feed", titolo=parola_mostruosa,
        dati_chiave=[parola_mostruosa]))
    with Image.open(asset.percorso) as img:
        larghezza, altezza = img.size
        margine = int(larghezza * images.MARGINE_SICURO)
        fascia_destra = img.crop((larghezza - margine, 0, larghezza, altezza))
        colori_nella_fascia = set(fascia_destra.getdata())
        assert _hex_a_rgb(images.PALETTE_DEFAULT["testo"]) not in colori_nella_fascia


def test_logo_completo_disegnato_in_alto_a_destra(tmp_path, monkeypatch):
    """Il logo completo (icona + wordmark + payoff) va in alto a destra,
    non piu' in basso a sinistra come la vecchia icona: se presente, deve
    comparire nell'immagine generata (a differenza di quando l'asset manca,
    caso in cui l'immagine resta invariata)."""
    provider = images.TemplateImageProvider(output_dir=tmp_path)
    richiesta = images.ImageGenerationRequest(
        template="presentazione", formato="instagram_feed", titolo="JobInPA")
    zona_logo = (700, 70, 1000, 170)  # angolo in alto a destra

    monkeypatch.setattr(provider, "_logo_completo", lambda altezza_max: None)
    asset_senza_logo = provider.genera_sync(richiesta)
    with Image.open(asset_senza_logo.percorso) as img:
        pixel_senza_logo = list(img.crop(zona_logo).getdata())

    logo_finto = Image.new("RGBA", (280, 70), (255, 0, 0, 255))
    monkeypatch.setattr(provider, "_logo_completo", lambda altezza_max: logo_finto)
    asset_con_logo = provider.genera_sync(richiesta)
    with Image.open(asset_con_logo.percorso) as img:
        pixel_con_logo = list(img.crop(zona_logo).getdata())

    assert pixel_senza_logo != pixel_con_logo
    assert (255, 0, 0) in {p[:3] for p in pixel_con_logo}


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
