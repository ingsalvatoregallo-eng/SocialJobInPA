"""
images.py — generazione degli asset grafici (sez. 7 del prompt master).

Modalita' predefinita: template deterministici renderizzati server-side con
Pillow — testi sempre corretti (nessun testo "inventato" da un modello),
palette e logo del brand, formati e margini sicuri per piattaforma.

OpenAI Images e' opzionale (ENABLE_AI_IMAGES): anche quando genera lo sfondo,
i dati essenziali (scadenze, posti, enti) vengono SEMPRE sovrapposti con
rendering deterministico, mai lasciati al modello.
"""

import logging
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from social import config, db_social  # noqa: E402

log = logging.getLogger(__name__)

# Formati richiesti (sez. 26).
FORMATI = {
    "instagram_feed": (1080, 1350),
    "instagram_square": (1080, 1080),
    "instagram_story": (1080, 1920),
    "linkedin": (1200, 627),
}
FORMATO_PER_PIATTAFORMA = {"instagram": "instagram_feed", "linkedin": "linkedin"}

# Margine sicuro: nessun testo oltre questo bordo (crop delle anteprime).
MARGINE_SICURO = 0.08

TEMPLATE_VALIDI = (
    "presentazione", "nuovo_concorso", "scadenza", "opportunita_settimana",
    "guida", "funzionalita", "errore_da_evitare", "faq",
)

# Etichetta mostrata in alto per ogni template (il "tipo" di card).
_ETICHETTA_TEMPLATE = {
    "presentazione": "JOBINPA",
    "nuovo_concorso": "NUOVO CONCORSO",
    "scadenza": "IN SCADENZA",
    "opportunita_settimana": "OPPORTUNITÀ DELLA SETTIMANA",
    "guida": "GUIDA PRATICA",
    "funzionalita": "COSA FA JOBINPA",
    "errore_da_evitare": "ERRORE DA EVITARE",
    "faq": "DOMANDA FREQUENTE",
}

PALETTE_DEFAULT = {
    "primario": "#0B3D91",     # blu istituzionale
    "accento": "#1FA774",      # verde JobInPA
    "sfondo": "#F5F7FB",
    "testo": "#15213B",
    "testo_su_primario": "#FFFFFF",
}


@dataclass
class ImageGenerationRequest:
    template: str
    formato: str                     # chiave di FORMATI
    titolo: str
    sottotitolo: Optional[str] = None
    dati_chiave: list = field(default_factory=list)
    prompt_ai: Optional[str] = None  # usato solo da OpenAIImageProvider
    palette: dict = field(default_factory=dict)
    content_id: Optional[str] = None


@dataclass
class GeneratedAsset:
    percorso: Path
    provider: str
    template: str
    formato: str


class ImageProvider(Protocol):
    async def generate(self, request: ImageGenerationRequest) -> GeneratedAsset:
        ...


def _font(dimensione, bold=False):
    """Prova i font di sistema piu' comuni (Windows e Linux/Docker), con
    fallback al font bitmap di Pillow: il rendering non deve mai fallire."""
    candidati = (
        ["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        if bold else
        ["segoeui.ttf", "arial.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"])
    for nome in candidati:
        try:
            return ImageFont.truetype(nome, dimensione)
        except OSError:
            continue
    return ImageFont.load_default(size=dimensione)


def _a_capo(draw, testo, font, larghezza_max):
    """Spezza il testo in righe che stanno in larghezza_max pixel."""
    parole, righe, riga = testo.split(), [], ""
    for parola in parole:
        tentativo = f"{riga} {parola}".strip()
        if draw.textlength(tentativo, font=font) <= larghezza_max:
            riga = tentativo
        else:
            if riga:
                righe.append(riga)
            riga = parola
    if riga:
        righe.append(riga)
    return righe


class TemplateImageProvider:
    """Rendering deterministico: layout a card con etichetta del template,
    titolo, sottotitolo, riquadri per i dati chiave e footer di brand."""

    nome = "template"

    def __init__(self, output_dir=None):
        self.output_dir = Path(output_dir or config.asset_storage_path())

    async def generate(self, request: ImageGenerationRequest) -> GeneratedAsset:
        return self.genera_sync(request)

    def genera_sync(self, request: ImageGenerationRequest) -> GeneratedAsset:
        if request.template not in TEMPLATE_VALIDI:
            raise ValueError(f"template sconosciuto: {request.template}")
        if request.formato not in FORMATI:
            raise ValueError(f"formato sconosciuto: {request.formato}")
        larghezza, altezza = FORMATI[request.formato]
        palette = {**PALETTE_DEFAULT, **(request.palette or {})}
        immagine = Image.new("RGB", (larghezza, altezza), palette["sfondo"])
        draw = ImageDraw.Draw(immagine)
        margine = int(larghezza * MARGINE_SICURO)
        interno = larghezza - 2 * margine
        scala = larghezza / 1080  # i font scalano col formato

        # Barra superiore col colore primario + etichetta template.
        altezza_barra = int(90 * scala)
        draw.rectangle([0, 0, larghezza, altezza_barra], fill=palette["primario"])
        font_etichetta = _font(int(34 * scala), bold=True)
        draw.text((margine, altezza_barra // 2), _ETICHETTA_TEMPLATE[request.template],
                  font=font_etichetta, fill=palette["testo_su_primario"], anchor="lm")

        y = altezza_barra + int(70 * scala)

        # Calcolato PRIMA di disegnare: senza un limite noto, con testi
        # lunghi titolo/sottotitolo/dati_chiave finivano dietro il footer
        # invece di fermarsi prima — bug reale osservato in produzione.
        # Ogni blocco sotto controlla lo spazio residuo e si ferma (mai
        # tronca a meta' riga: semplicemente non disegna la riga/il
        # riquadro successivo se non ci sta).
        altezza_footer = int(110 * scala)
        y_limite = altezza - altezza_footer - int(20 * scala)

        # Titolo (a capo automatico dentro l'area sicura).
        font_titolo = _font(int(72 * scala), bold=True)
        altezza_riga_titolo = int(86 * scala)
        for riga in _a_capo(draw, request.titolo, font_titolo, interno)[:4]:
            if y + altezza_riga_titolo > y_limite:
                break
            draw.text((margine, y), riga, font=font_titolo, fill=palette["testo"])
            y += altezza_riga_titolo
        y += int(20 * scala)

        if request.sottotitolo:
            font_sotto = _font(int(44 * scala))
            altezza_riga_sotto = int(56 * scala)
            for riga in _a_capo(draw, request.sottotitolo, font_sotto, interno)[:3]:
                if y + altezza_riga_sotto > y_limite:
                    break
                draw.text((margine, y), riga, font=font_sotto, fill=palette["primario"])
                y += altezza_riga_sotto
            y += int(24 * scala)

        # Dati chiave: riquadri con bordo accento — SEMPRE deterministici.
        font_dato = _font(int(40 * scala), bold=True)
        altezza_box = int(76 * scala)
        for dato in request.dati_chiave[:5]:
            if y + altezza_box > y_limite:
                break
            testo_dato = str(dato)
            draw.rounded_rectangle(
                [margine, y, larghezza - margine, y + altezza_box],
                radius=int(14 * scala), outline=palette["accento"], width=max(2, int(4 * scala)))
            draw.text((margine + int(28 * scala), y + altezza_box // 2), testo_dato,
                      font=font_dato, fill=palette["testo"], anchor="lm")
            y += altezza_box + int(22 * scala)

        # Footer di brand: logo se presente in assets/brand, altrimenti wordmark.
        draw.rectangle([0, altezza - altezza_footer, larghezza, altezza],
                       fill=palette["primario"])
        logo = self._logo(int(altezza_footer * 0.6))
        x_testo = margine
        if logo is not None:
            immagine.paste(logo, (margine, altezza - altezza_footer
                                  + (altezza_footer - logo.height) // 2),
                           logo if logo.mode == "RGBA" else None)
            x_testo = margine + logo.width + int(24 * scala)
        font_brand = _font(int(38 * scala), bold=True)
        draw.text((x_testo, altezza - altezza_footer // 2),
                  "JobInPA — Your PA, powered by AI",
                  font=font_brand, fill=palette["testo_su_primario"], anchor="lm")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        percorso = self.output_dir / f"{request.template}_{request.formato}_{uuid.uuid4().hex[:10]}.png"
        # PNG sRGB: Pillow salva RGB senza profilo, che i social interpretano sRGB.
        immagine.save(percorso, "PNG")
        return GeneratedAsset(percorso=percorso, provider=self.nome,
                              template=request.template, formato=request.formato)

    def _logo(self, altezza_max):
        for nome in ("logo.png", "icona.png", "LogoInsta.png"):
            percorso = config.brand_asset_path() / nome
            if percorso.exists():
                try:
                    logo = Image.open(percorso).convert("RGBA")
                    rapporto = altezza_max / logo.height
                    return logo.resize((max(1, int(logo.width * rapporto)), altezza_max))
                except OSError:
                    log.warning("logo %s non leggibile, footer senza logo", percorso)
        return None


class MockImageProvider:
    """Per i test: file PNG 1x1, nessun rendering."""

    nome = "mock"

    def __init__(self, output_dir=None):
        self.output_dir = Path(output_dir or config.asset_storage_path())
        self.richieste = []

    async def generate(self, request: ImageGenerationRequest) -> GeneratedAsset:
        self.richieste.append(request)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        percorso = self.output_dir / f"mock_{uuid.uuid4().hex[:10]}.png"
        Image.new("RGB", (1, 1), "#FFFFFF").save(percorso, "PNG")
        return GeneratedAsset(percorso=percorso, provider=self.nome,
                              template=request.template, formato=request.formato)


class OpenAIImageProvider:
    """Sfondo generato da OpenAI Images + overlay deterministico dei dati
    essenziali (riusa TemplateImageProvider per l'overlay dei dati_chiave).
    Attivo solo con ENABLE_AI_IMAGES=true, chiave presente e budget residuo."""

    nome = "openai_images"

    def __init__(self, conn, output_dir=None):
        self.conn = conn
        self.output_dir = Path(output_dir or config.asset_storage_path())
        self._template_provider = TemplateImageProvider(output_dir)
        if not config.openai_api_key():
            raise RuntimeError("OPENAI_API_KEY non impostata")

    def _verifica_budget(self):
        budget = config.openai_image_monthly_budget_eur()
        speso = db_social.costo_periodo(self.conn, "openai_images")
        prezzo = db_social.get_setting(self.conn, "prezzo_immagine_ai_eur", 0.04)
        if speso + prezzo >= budget:
            from social.llm import BudgetEsaurito
            raise BudgetEsaurito("budget mensile OpenAI Images esaurito: uso template")
        return prezzo

    async def generate(self, request: ImageGenerationRequest) -> GeneratedAsset:
        import asyncio
        prezzo = self._verifica_budget()
        larghezza, altezza = FORMATI[request.formato]
        sfondo_bytes = await asyncio.to_thread(
            self._chiama_api, request.prompt_ai or request.titolo)
        db_social.registra_costo(self.conn, "openai_images", prezzo,
                                 modello=config.openai_image_model(),
                                 content_id=request.content_id)
        import io
        sfondo = Image.open(io.BytesIO(sfondo_bytes)).convert("RGB")
        sfondo = sfondo.resize((larghezza, altezza))
        # Overlay deterministico: fascia scura + titolo + dati chiave.
        draw = ImageDraw.Draw(sfondo, "RGBA")
        margine = int(larghezza * MARGINE_SICURO)
        scala = larghezza / 1080
        fascia_y = int(altezza * 0.55)
        draw.rectangle([0, fascia_y, larghezza, altezza], fill=(11, 61, 145, 235))
        font_titolo = _font(int(56 * scala), bold=True)
        y = fascia_y + int(40 * scala)
        for riga in _a_capo(draw, request.titolo, font_titolo, larghezza - 2 * margine)[:2]:
            draw.text((margine, y), riga, font=font_titolo, fill="#FFFFFF")
            y += int(68 * scala)
        font_dato = _font(int(38 * scala), bold=True)
        for dato in request.dati_chiave[:3]:
            draw.text((margine, y), f"• {dato}", font=font_dato, fill="#FFFFFF")
            y += int(52 * scala)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        percorso = self.output_dir / f"ai_{request.formato}_{uuid.uuid4().hex[:10]}.png"
        sfondo.save(percorso, "PNG")
        return GeneratedAsset(percorso=percorso, provider=self.nome,
                              template=request.template, formato=request.formato)

    def _chiama_api(self, prompt):
        import base64
        import requests
        risposta = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {config.openai_api_key()}"},
            json={"model": config.openai_image_model(), "prompt": prompt,
                  "n": 1, "size": "1024x1024"},
            timeout=120)
        risposta.raise_for_status()
        dato = risposta.json()["data"][0]
        if "b64_json" in dato:
            return base64.b64decode(dato["b64_json"])
        url = dato["url"]
        return requests.get(url, timeout=60).content


def provider_immagini(conn, mode=None):
    """Factory con fallback (sez. 30): OpenAI solo se abilitato, con chiave e
    budget; in ogni altro caso template deterministici; mock in modalita' mock."""
    mode = mode or db_social.get_setting(conn, "mode_override") or config.mode()
    if mode == "mock":
        return MockImageProvider()
    if config.ai_images_enabled() and config.openai_api_key():
        try:
            provider = OpenAIImageProvider(conn)
            provider._verifica_budget()
            return provider
        except Exception as errore:
            log.info("OpenAI Images non disponibile (%s): uso template", errore)
    return TemplateImageProvider()
