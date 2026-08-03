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

    monkeypatch.setattr(images, "_logo_completo", lambda altezza_max: None)
    asset_senza_logo = provider.genera_sync(richiesta)
    with Image.open(asset_senza_logo.percorso) as img:
        pixel_senza_logo = list(img.crop(zona_logo).getdata())

    logo_finto = Image.new("RGBA", (280, 70), (255, 0, 0, 255))
    monkeypatch.setattr(images, "_logo_completo", lambda altezza_max: logo_finto)
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


class _RispostaFinta:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        import base64
        return {"data": [{"b64_json": base64.b64encode(_PNG_1X1_OPENAI).decode()}]}


_PNG_1X1_OPENAI = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415408d763f8cfc0c00000030001a1b7b6210000000049454e44ae426082")


def test_ridimensiona_a_contenimento_non_taglia_mai_i_bordi():
    """A differenza di _ridimensiona_a_copertura (crop), _ridimensiona_a_
    contenimento non deve mai perdere contenuto vicino ai bordi: usata per
    il template "promozione" dove l'AI compone badge/logo in alto e
    bottone CTA in fondo (bug segnalato dall'utente: con un crop "a
    copertura" finivano tagliati fuori)."""
    sorgente = Image.new("RGB", (100, 200), "white")
    for x in range(20):
        for y in range(20):
            sorgente.putpixel((x, y), (255, 0, 0))
            sorgente.putpixel((99 - x, 199 - y), (0, 0, 255))
    risultato = images._ridimensiona_a_contenimento(sorgente, 300, 150, "#FFFFFF")
    assert risultato.size == (300, 150)
    pixel = list(risultato.getdata())
    assert any(r > g + 50 and r > b + 50 for r, g, b in pixel)  # angolo rosso sopravvissuto
    assert any(b > r + 50 and b > g + 50 for r, g, b in pixel)  # angolo blu sopravvissuto


def test_ridimensiona_a_contenimento_riempie_il_bordo_col_colore_indicato():
    sorgente = Image.new("RGB", (10, 10), "black")
    risultato = images._ridimensiona_a_contenimento(sorgente, 100, 50, "#F5F7FB")
    assert risultato.getpixel((0, 0)) == _hex_a_rgb("#F5F7FB")


@pytest.mark.parametrize("template", sorted(images._TEMPLATE_GRAFICA_INTERA))
@pytest.mark.parametrize("formato", list(images.FORMATI))
def test_genera_grafica_intera_renderizza_in_tutti_i_formati(conn, monkeypatch, formato, template):
    """"promozione" e "nuovo_concorso" (carosello concorsi) fanno comporre
    all'AI l'intera grafica (vedi OpenAIImageProvider._genera_grafica_
    intera): l'immagine ricevuta viene solo ridimensionata "a contenimento"
    (mai un crop) sul formato target, mai un crash."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-finta")
    monkeypatch.setattr("requests.post", lambda url, **kwargs: _RispostaFinta())
    provider = images.OpenAIImageProvider(conn)
    richiesta = images.ImageGenerationRequest(
        template=template, formato=formato,
        titolo="Piano Base: prova l'AI di JobInPA senza impegno",
        sottotitolo="Promozione a tempo limitato",
        dati_chiave=["Promozione: Base", "Prezzo promozionale: 0,00 EUR (invece di 3,00 EUR)",
                    "Valida fino al 31 agosto 2026"],
        prompt_ai="Un pacchetto regalo")
    asset = asyncio.run(provider.generate(richiesta))
    assert asset.percorso.exists()
    with Image.open(asset.percorso) as img:
        assert img.size == images.FORMATI[formato]
        assert img.format == "PNG"


def test_prompt_grafica_intera_include_testo_esatto_tra_virgolette(conn):
    """Il prompt deve contenere titolo/dati_chiave/CTA come stringhe esatte
    tra virgolette (vedi images._prompt_grafica_intera): a differenza di
    _STILE_OPENAI_IMAGES, qui il testo nell'immagine e' voluto, non
    vietato — deve arrivare al modello parola per parola, non parafrasato."""
    richiesta = images.ImageGenerationRequest(
        template="promozione", formato="instagram_feed",
        titolo="Piano Base: prova l'AI di JobInPA senza impegno",
        sottotitolo="Promozione a tempo limitato",
        dati_chiave=["Promozione: Base", "Valida fino al 31 agosto 2026"])
    prompt = images._prompt_grafica_intera(richiesta)
    assert '"Piano Base: prova l\'AI di JobInPA senza impegno"' in prompt
    assert '"Promozione a tempo limitato"' in prompt
    assert '"Promozione: Base"' in prompt
    assert '"Valida fino al 31 agosto 2026"' in prompt
    assert '"PROMOZIONE JOBINPA"' in prompt
    assert '"Scopri di più su JobInPA"' in prompt


def test_prompt_grafica_intera_usa_badge_e_cta_del_template_nuovo_concorso(conn):
    """Badge e CTA non sono un testo fisso unico: "nuovo_concorso" (usato
    dal carosello dei concorsi) deve avere i propri, diversi da quelli di
    "promozione"."""
    richiesta = images.ImageGenerationRequest(
        template="nuovo_concorso", formato="instagram_feed",
        titolo="Concorso pubblico Comune di Trieste", dati_chiave=["Posti: 5"])
    prompt = images._prompt_grafica_intera(richiesta)
    assert '"NUOVO CONCORSO"' in prompt
    assert '"Scopri il concorso su JobInPA"' in prompt
    assert "PROMOZIONE JOBINPA" not in prompt


def test_prompt_grafica_intera_senza_sottotitolo_non_lo_menziona(conn):
    richiesta = images.ImageGenerationRequest(
        template="promozione", formato="instagram_feed", titolo="Tema", dati_chiave=[])
    prompt = images._prompt_grafica_intera(richiesta)
    assert "subtitle" not in prompt.lower()


def test_prompt_grafica_intera_senza_soggetto_usa_il_titolo_non_il_pacchetto_regalo(conn):
    """"Concorsi" e' seminata con prompt_ai vuoto apposta (il soggetto
    varia da bando a bando): il fallback non deve essere un pacchetto
    regalo (pensato per le promozioni) per qualsiasi template."""
    richiesta = images.ImageGenerationRequest(
        template="nuovo_concorso", formato="instagram_feed",
        titolo="Concorso pubblico AIFA", dati_chiave=[])
    prompt = images._prompt_grafica_intera(richiesta)
    assert "gift box" not in prompt.lower()
    assert "Concorso pubblico AIFA" in prompt


def test_prompt_grafica_intera_con_nota_correzione_la_include(conn):
    """Un'istruzione ad-hoc per un tentativo di rigenerazione (es. refusi/
    accenti storpiati, segnalato dall'utente) entra nel prompt come
    correzione per QUESTO tentativo, senza sostituire il testo esatto da
    riprodurre (titolo/dati_chiave restano quelli quotati)."""
    richiesta = images.ImageGenerationRequest(
        template="nuovo_concorso", formato="instagram_feed",
        titolo="Concorso pubblico Comune di Trieste", dati_chiave=["Posti: 5"],
        nota_correzione="sistema gli accenti nel titolo, sono usciti storpiati")
    prompt = images._prompt_grafica_intera(richiesta)
    assert "sistema gli accenti nel titolo, sono usciti storpiati" in prompt
    assert '"Concorso pubblico Comune di Trieste"' in prompt  # il testo esatto resta invariato


def test_prompt_grafica_intera_senza_nota_correzione_non_la_menziona(conn):
    richiesta = images.ImageGenerationRequest(
        template="nuovo_concorso", formato="instagram_feed",
        titolo="Concorso pubblico Comune di Trieste", dati_chiave=[])
    prompt = images._prompt_grafica_intera(richiesta)
    assert "correction" not in prompt.lower()


def test_genera_grafica_intera_invia_il_prompt_completo_a_openai(conn, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-finta")
    catturato = {}

    def _post_finto(url, **kwargs):
        catturato["prompt"] = kwargs["json"]["prompt"]
        return _RispostaFinta()

    monkeypatch.setattr("requests.post", _post_finto)
    provider = images.OpenAIImageProvider(conn)
    richiesta = images.ImageGenerationRequest(
        template="promozione", formato="instagram_feed",
        titolo="Piano Base gratis", dati_chiave=["Valida fino al 31 agosto 2026"])
    asyncio.run(provider.generate(richiesta))
    assert '"Piano Base gratis"' in catturato["prompt"]
    assert '"Valida fino al 31 agosto 2026"' in catturato["prompt"]


def test_genera_grafica_intera_usato_solo_per_i_template_dedicati(conn, monkeypatch):
    """Qualsiasi altro template continua a passare per il vecchio layout a
    sfondo intero (invariato): nessuna regressione per Funzionalità/
    generico, che non hanno chiesto il nuovo layout a card."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-finta")
    monkeypatch.setattr("requests.post", lambda url, **kwargs: _RispostaFinta())
    provider = images.OpenAIImageProvider(conn)
    richiesta = images.ImageGenerationRequest(
        template="presentazione", formato="instagram_feed", titolo="Concorso pubblico")
    asset = asyncio.run(provider.generate(richiesta))
    assert asset.percorso.name.startswith("ai_") and not asset.percorso.name.startswith("ai_grafica_")


def test_chiama_api_usa_lo_stile_fisso_se_nessuno_stile_categoria(conn, monkeypatch):
    """Senza uno stile_ai (categoria senza stile_immagine, o nessuna
    categoria): il comportamento storico non cambia, resta lo stile fisso
    _STILE_OPENAI_IMAGES prepended al prompt del soggetto."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-finta")
    provider = images.OpenAIImageProvider(conn)
    catturato = {}

    def _post_finto(url, **kwargs):
        catturato["prompt"] = kwargs["json"]["prompt"]
        return _RispostaFinta()

    monkeypatch.setattr("requests.post", _post_finto)
    provider._chiama_api("Un pacchetto regalo", "1024x1024")
    assert catturato["prompt"] == images._STILE_OPENAI_IMAGES + "Un pacchetto regalo"


def test_chiama_api_stile_categoria_sostituisce_del_tutto_lo_stile_fisso(conn, monkeypatch):
    """Con uno stile_ai valorizzato (categoria.stile_immagine, es.
    "Promozioni"): sostituisce COMPLETAMENTE _STILE_OPENAI_IMAGES, non si
    somma ad esso — altrimenti resterebbe comunque piatto/istituzionale
    (bug segnalato dall'utente: risultato 'pessimo' con edificio classico
    anche quando il prompt/immagine di riferimento non lo richiedevano)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-finta")
    provider = images.OpenAIImageProvider(conn)
    catturato = {}

    def _post_finto(url, **kwargs):
        catturato["prompt"] = kwargs["json"]["prompt"]
        return _RispostaFinta()

    monkeypatch.setattr("requests.post", _post_finto)
    stile_personalizzato = "Glossy 3D illustration, purple/blue gradient. Subject: "
    provider._chiama_api("Un pacchetto regalo", "1024x1024", stile_ai=stile_personalizzato)
    assert catturato["prompt"] == stile_personalizzato + "Un pacchetto regalo"
    assert "Flat vector" not in catturato["prompt"]
