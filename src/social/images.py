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
import math
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol

from PIL import Image, ImageDraw, ImageFilter, ImageFont

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

# Limite reale dei post carosello di Instagram (Content Publishing API):
# condiviso fra generazione (agents.visual) e pubblicazione
# (integrations.instagram) per evitare che i due si disallineino.
MASSIMO_IMMAGINI_CAROSELLO = 10

# Margine sicuro: nessun testo oltre questo bordo (crop delle anteprime).
MARGINE_SICURO = 0.08

TEMPLATE_VALIDI = (
    "presentazione", "nuovo_concorso", "scadenza", "opportunita_settimana",
    "guida", "funzionalita", "errore_da_evitare", "faq", "promozione",
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
    "promozione": "PROMOZIONE JOBINPA",
}

PALETTE_DEFAULT = {
    "primario": "#0B3D91",     # blu istituzionale
    "accento": "#1FA774",      # verde JobInPA
    "sfondo": "#F5F7FB",
    "testo": "#15213B",
    "testo_su_primario": "#FFFFFF",
    "testo_muto": "#64748B",
    "viola": "#7C3AED",        # badge/decorazioni, come nei mockup di riferimento
    "card": "#FFFFFF",
    "bordo_card": "#E2E8F0",
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
    immagini_riferimento: list = field(default_factory=list)  # percorsi locali, guidano /v1/images/edits
    stile_ai: Optional[str] = None  # sostituisce _STILE_OPENAI_IMAGES se valorizzato (da categoria.stile_immagine)


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


def _a_capo_forzato(draw, testo, font, larghezza_max):
    """Come _a_capo, ma se una singola parola e' piu' larga di
    larghezza_max la spezza carattere per carattere invece di lasciarla
    sconfinare: ultima rete di sicurezza per parole composte lunghe (es.
    "amministrativo-gestionali") quando anche la dimensione minima del
    font non basta a farle stare per intero."""
    righe = []
    for riga_parole in _a_capo(draw, testo, font, larghezza_max):
        if draw.textlength(riga_parole, font=font) <= larghezza_max:
            righe.append(riga_parole)
            continue
        corrente = ""
        for carattere in riga_parole:
            tentativo = corrente + carattere
            if draw.textlength(tentativo, font=font) <= larghezza_max or not corrente:
                corrente = tentativo
            else:
                righe.append(corrente)
                corrente = carattere
        if corrente:
            righe.append(corrente)
    return righe


def _adatta_testo_multilinea(draw, testo, dimensione_max, dimensione_min, bold,
                             larghezza_max, altezza_disponibile, interlinea=1.22, passo=2):
    """Trova la dimensione di font piu' grande (fino a dimensione_min) per
    cui l'intero testo, andato a capo, sta entro larghezza_max e
    altezza_disponibile — MAI troncato con ellissi: se non entra, va a
    capo o rimpicciolisce, mai perde contenuto. Ultima rete di sicurezza a
    dimensione minima: spezza anche a meta' parola (_a_capo_forzato)
    piuttosto che sconfinare oltre il margine."""
    dimensione = dimensione_max
    while dimensione >= dimensione_min:
        font = _font(dimensione, bold=bold)
        altezza_riga = int(dimensione * interlinea)
        righe = _a_capo(draw, testo, font, larghezza_max)
        larghezza_massima_riga = max((draw.textlength(r, font=font) for r in righe), default=0)
        if larghezza_massima_riga <= larghezza_max and altezza_riga * len(righe) <= altezza_disponibile:
            return font, righe, altezza_riga
        dimensione -= passo
    font = _font(dimensione_min, bold=bold)
    altezza_riga = int(dimensione_min * interlinea)
    return font, _a_capo_forzato(draw, testo, font, larghezza_max), altezza_riga


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


def _numero_di_elementi_che_entrano(y_iniziale, passo, y_limite):
    """Quanti elementi alti 'passo' partendo da y_iniziale stanno prima di
    y_limite, senza mai disegnare a meta' fuori dall'area sicura. Isolata
    dal rendering Pillow per essere testabile senza generare immagini —
    stessa logica di sicurezza gia' usata per il bug titolo/footer e
    dati_chiave/bordo destro, qui centralizzata invece di ripetuta."""
    if passo <= 0:
        return 0
    disponibile = y_limite - y_iniziale
    if disponibile <= 0:
        return 0
    return int(disponibile // passo)


def _hex_a_rgba(colore_hex, alpha=255):
    colore_hex = colore_hex.lstrip("#")
    r, g, b = (int(colore_hex[i:i + 2], 16) for i in (0, 2, 4))
    return (r, g, b, alpha)


def _sfondo_con_blob(larghezza, altezza, palette, scala):
    """Sfondo con leggera sfumatura verticale + un paio di forme sfocate
    (i 'blob' decorativi tipici del brand nei mockup di riferimento),
    invece dello sfondo piatto in tinta unita usato finora."""
    alto = _hex_a_rgba("#EEF1FC")
    basso = _hex_a_rgba(palette["sfondo"])
    immagine = Image.new("RGBA", (larghezza, altezza), basso)
    draw = ImageDraw.Draw(immagine)
    zona_sfumata = int(altezza * 0.5)
    for y in range(zona_sfumata):
        t = y / max(1, zona_sfumata)
        colore = tuple(int(alto[i] + (basso[i] - alto[i]) * t) for i in range(3))
        draw.line([(0, y), (larghezza, y)], fill=colore)

    blob = Image.new("RGBA", (larghezza, altezza), (0, 0, 0, 0))
    blob_draw = ImageDraw.Draw(blob)
    raggio1 = int(230 * scala)
    blob_draw.ellipse(
        [larghezza - raggio1 * 1.1, -raggio1 * 0.7, larghezza + raggio1 * 0.9, raggio1 * 1.1],
        fill=_hex_a_rgba(palette["primario"], 30))
    raggio2 = int(190 * scala)
    blob_draw.ellipse(
        [-raggio2 * 0.9, altezza - raggio2 * 1.1, raggio2 * 0.9, altezza + raggio2 * 0.9],
        fill=_hex_a_rgba(palette["viola"], 26))
    blob = blob.filter(ImageFilter.GaussianBlur(int(70 * scala)))
    immagine = Image.alpha_composite(immagine, blob)
    return immagine


def _disegna_ombra(immagine, box, radius, scala):
    """Ombra morbida sotto un riquadro (card bianca): disegnata su un layer
    separato e sfocata, poi composta sotto — la card vera si disegna dopo,
    sopra l'ombra."""
    ombra = Image.new("RGBA", immagine.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(ombra)
    offset = int(10 * scala)
    x0, y0, x1, y1 = box
    draw.rounded_rectangle([x0, y0 + offset, x1, y1 + offset], radius=radius,
                           fill=(15, 23, 42, 55))
    ombra = ombra.filter(ImageFilter.GaussianBlur(int(14 * scala)))
    return Image.alpha_composite(immagine, ombra)


def _disegna_stella(draw, cx, cy, raggio, colore):
    """Piccola stella a 4 punte disegnata come poligono: un'icona 'sparkle'
    coerente in ogni ambiente di rendering, a differenza dell'emoji ✨ che
    su Linux/Docker (font DejaVu, senza glifi Dingbats) risulterebbe
    un riquadro vuoto invece dell'icona."""
    punti = []
    for angolo_gradi, r in ((0, raggio), (45, raggio * 0.28), (90, raggio),
                            (135, raggio * 0.28), (180, raggio), (225, raggio * 0.28),
                            (270, raggio), (315, raggio * 0.28)):
        rad = math.radians(angolo_gradi)
        punti.append((cx + r * math.sin(rad), cy - r * math.cos(rad)))
    draw.polygon(punti, fill=colore)


def _pillola_sfumata(larghezza, altezza, colore_da, colore_a):
    """Pillola (rounded rect a raggio pieno) con riempimento a gradiente
    orizzontale, ritagliata con una maschera arrotondata."""
    base = Image.new("RGBA", (larghezza, altezza), (0, 0, 0, 0))
    da, a = _hex_a_rgba(colore_da), _hex_a_rgba(colore_a)
    for x in range(larghezza):
        t = x / max(1, larghezza - 1)
        colore = tuple(int(da[i] + (a[i] - da[i]) * t) for i in range(3)) + (255,)
        ImageDraw.Draw(base).line([(x, 0), (x, altezza)], fill=colore)
    maschera = Image.new("L", (larghezza, altezza), 0)
    ImageDraw.Draw(maschera).rounded_rectangle(
        [0, 0, larghezza - 1, altezza - 1], radius=altezza // 2, fill=255)
    base.putalpha(maschera)
    return base


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
        margine = int(larghezza * MARGINE_SICURO)
        interno = larghezza - 2 * margine
        scala = larghezza / 1080  # i font scalano col formato

        # Sfondo sfumato + blob decorativi, stile card di prodotto (badge
        # pillola, card bianca con ombra) invece della vecchia barra piena
        # a tinta unita in alto/in basso.
        immagine = _sfondo_con_blob(larghezza, altezza, palette, scala)
        draw = ImageDraw.Draw(immagine)

        # Riservato PRIMA di disegnare: senza un limite noto, con testi
        # lunghi finivano dietro il footer invece di fermarsi prima — bug
        # reale osservato in produzione. Senza piu' una barra footer piena,
        # basta un margine di sicurezza sul fondo.
        y_limite = altezza - int(56 * scala)

        # Badge pillola col nome del template ("NUOVO CONCORSO", ecc.), in
        # alto a sinistra.
        font_etichetta = _font(int(28 * scala), bold=True)
        testo_etichetta = _ETICHETTA_TEMPLATE[request.template]
        larghezza_etichetta = draw.textlength(testo_etichetta, font=font_etichetta)
        altezza_badge = int(66 * scala)
        padding_badge = int(26 * scala)
        icona_raggio = int(9 * scala)
        larghezza_badge = int(padding_badge * 2.4 + icona_raggio * 2 + 12 * scala + larghezza_etichetta)
        badge = _pillola_sfumata(larghezza_badge, altezza_badge, palette["viola"], palette["primario"])
        y_badge = margine
        immagine.paste(badge, (margine, y_badge), badge)
        draw = ImageDraw.Draw(immagine)
        cx_icona = margine + padding_badge + icona_raggio
        _disegna_stella(draw, cx_icona, y_badge + altezza_badge // 2, icona_raggio, palette["testo_su_primario"])
        draw.text((cx_icona + icona_raggio + int(12 * scala), y_badge + altezza_badge // 2),
                  testo_etichetta, font=font_etichetta, fill=palette["testo_su_primario"], anchor="lm")

        # Logo completo del sito in alto a destra (icona + wordmark +
        # payoff gia' nell'asset: non serve ridisegnarli separatamente nel
        # footer, evitando la duplicazione "JobInPA" vista due volte).
        altezza_area_alta = altezza_badge
        logo = _logo_completo(int(altezza_area_alta * 1.05))
        if logo is not None:
            x_logo = larghezza - margine - logo.width
            y_logo = y_badge + (altezza_badge - logo.height) // 2
            immagine.paste(logo, (x_logo, y_logo), logo)

        y = y_badge + altezza_badge + int(56 * scala)

        # Puntinato decorativo, angolo in basso a sinistra (spazio libero
        # sotto la card, lontano da badge/logo in alto).
        draw = ImageDraw.Draw(immagine)
        passo_puntini = int(20 * scala)
        raggio_puntino = max(1, int(2 * scala))
        origine_x = margine
        origine_y = altezza - int(90 * scala)
        for riga in range(4):
            for colonna in range(4):
                cx = origine_x + colonna * passo_puntini
                cy = origine_y + riga * passo_puntini
                draw.ellipse([cx - raggio_puntino, cy - raggio_puntino,
                             cx + raggio_puntino, cy + raggio_puntino],
                            fill=_hex_a_rgba(palette["testo_muto"], 70))

        # Titolo: MAI troncato — va a capo e, se necessario, rimpicciolisce
        # (fino a un minimo) prima di spezzare a meta' parola come ultima
        # rete di sicurezza. Corregge il caso di parole composte lunghe
        # (es. "amministrativo-gestionali") che uscivano dal margine destro
        # perche' indivisibili per _a_capo.
        font_titolo, righe_titolo, altezza_riga_titolo = _adatta_testo_multilinea(
            draw, request.titolo, dimensione_max=int(68 * scala), dimensione_min=int(38 * scala),
            bold=True, larghezza_max=interno,
            altezza_disponibile=min(int(82 * scala) * 4, y_limite - y))
        for riga in righe_titolo[:_numero_di_elementi_che_entrano(y, altezza_riga_titolo, y_limite)]:
            draw.text((margine, y), riga, font=font_titolo, fill=palette["testo"])
            y += altezza_riga_titolo
        y += int(18 * scala)

        if request.sottotitolo:
            font_sotto, righe_sotto, altezza_riga_sotto = _adatta_testo_multilinea(
                draw, request.sottotitolo, dimensione_max=int(40 * scala), dimensione_min=int(26 * scala),
                bold=False, larghezza_max=interno,
                altezza_disponibile=min(int(52 * scala) * 3, y_limite - y))
            for riga in righe_sotto[:_numero_di_elementi_che_entrano(y, altezza_riga_sotto, y_limite)]:
                draw.text((margine, y), riga, font=font_sotto, fill=palette["testo_muto"])
                y += altezza_riga_sotto
            y += int(28 * scala)

        # Dati chiave: un'unica card bianca con ombra morbida, righe con
        # pallino colorato — al posto dei riquadri singoli con bordo. Ogni
        # dato va a capo su massimo 2 righe (mai troncato con ellissi):
        # l'altezza della riga e' percio' variabile, non fissa.
        dati = request.dati_chiave[:5]
        if dati:
            padding_card = int(30 * scala)
            raggio_pallino = int(7 * scala)
            rientro_testo = raggio_pallino * 2 + int(20 * scala)
            larghezza_testo_dato = interno - 2 * padding_card - rientro_testo
            spaziatura_item = int(22 * scala)

            righe_per_dato = []
            for dato in dati:
                font_d, righe_d, altezza_riga_d = _adatta_testo_multilinea(
                    draw, str(dato), dimensione_max=int(36 * scala), dimensione_min=int(22 * scala),
                    bold=True, larghezza_max=larghezza_testo_dato,
                    altezza_disponibile=int(36 * scala * 1.22) * 2)
                righe_per_dato.append((font_d, righe_d, altezza_riga_d,
                                       altezza_riga_d * len(righe_d) + spaziatura_item))

            y_cursore = y + padding_card
            limite_card = y_limite - padding_card
            item_da_disegnare = []
            for item in righe_per_dato:
                if y_cursore + item[3] - spaziatura_item > limite_card:
                    break
                item_da_disegnare.append(item)
                y_cursore += item[3]

            if item_da_disegnare:
                altezza_contenuto = sum(i[3] for i in item_da_disegnare) - spaziatura_item
                altezza_card = padding_card * 2 + altezza_contenuto
                box_card = (margine, y, larghezza - margine, y + altezza_card)
                immagine = _disegna_ombra(immagine, box_card, int(22 * scala), scala)
                draw = ImageDraw.Draw(immagine)
                draw.rounded_rectangle(box_card, radius=int(22 * scala), fill=palette["card"],
                                       outline=palette["bordo_card"], width=max(1, int(1.5 * scala)))
                y_item = y + padding_card
                for indice, (font_d, righe_d, altezza_riga_d, altezza_item) in enumerate(item_da_disegnare):
                    cy_pallino = y_item + altezza_riga_d // 2
                    draw.ellipse([margine + padding_card - raggio_pallino,
                                 cy_pallino - raggio_pallino,
                                 margine + padding_card + raggio_pallino,
                                 cy_pallino + raggio_pallino], fill=palette["accento"])
                    y_riga_testo = y_item
                    for riga_testo in righe_d:
                        draw.text((margine + padding_card + rientro_testo, y_riga_testo + altezza_riga_d // 2),
                                  riga_testo, font=font_d, fill=palette["testo"], anchor="lm")
                        y_riga_testo += altezza_riga_d
                    if indice < len(item_da_disegnare) - 1:
                        y_separatore = y_item + altezza_riga_d * len(righe_d) + spaziatura_item // 2
                        draw.line([(margine + padding_card, y_separatore),
                                  (larghezza - margine - padding_card, y_separatore)],
                                 fill=palette["bordo_card"], width=max(1, int(1.5 * scala)))
                    y_item += altezza_item

        self.output_dir.mkdir(parents=True, exist_ok=True)
        percorso = self.output_dir / f"{request.template}_{request.formato}_{uuid.uuid4().hex[:10]}.png"
        # PNG sRGB: Pillow salva RGB senza profilo, che i social interpretano sRGB.
        immagine.convert("RGB").save(percorso, "PNG")
        return GeneratedAsset(percorso=percorso, provider=self.nome,
                              template=request.template, formato=request.formato)


def _logo_completo(altezza_max):
    """Logo completo (icona + wordmark + payoff), in alto a destra — a
    differenza della vecchia icona piccola nel footer, e' l'unico elemento
    di brand nell'immagine: niente piu' "JobInPA" duplicato a parte in
    basso. Funzione di modulo (non solo di TemplateImageProvider) perche'
    riusata anche dal layout "promozione" di OpenAIImageProvider."""
    for nome in ("logo-jobinpa-completo.png", "logo.png"):
        percorso = config.brand_asset_path() / nome
        if percorso.exists():
            try:
                logo = Image.open(percorso).convert("RGBA")
                rapporto = altezza_max / logo.height
                return logo.resize((max(1, int(logo.width * rapporto)), altezza_max))
            except OSError:
                log.warning("logo %s non leggibile, immagine senza logo", percorso)
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


# Stile applicato SEMPRE, indipendentemente da cosa scrive l'agente in
# prompt_ai: la coerenza del brand da un post all'altro non puo' dipendere
# da un testo libero generato dall'LLM (stesso principio dei dati_chiave,
# qui applicato allo stile invece che ai fatti).
_STILE_OPENAI_IMAGES = (
    "Flat vector illustration, minimalist corporate/institutional style, "
    "clean geometric shapes, generous negative space. Color palette: navy "
    "blue #0B3D91 as dominant color, green #1FA774 as single accent, light "
    "background #F5F7FB. Classical Italian public-institution architecture "
    "motif (columns, pediment) rendered as simple flat shapes, not "
    "photorealistic. No text, no letters, no numbers, no words anywhere in "
    "the image. Leave the bottom half of the composition visually calm and "
    "uncluttered, suitable for overlaid text. Subject: "
)

def _prompt_promozione_completa(request):
    """Prompt per il template "promozione" (vedi OpenAIImageProvider.
    _genera_promozione): a differenza di _STILE_OPENAI_IMAGES, qui il testo
    NON e' vietato — al contrario, ogni stringa e' messa tra virgolette ed
    esplicitamente marcata come testo esatto da riprodurre, per aiutare il
    modello a non storpiarla/inventarne altra. dati_chiave/titolo/
    sottotitolo arrivano gia' verificati dal resto della pipeline (mai
    testo libero dell'LLM), qui vengono solo inseriti nella descrizione
    della composizione."""
    dati = request.dati_chiave[:4]
    righe_dati = "\n".join(f'   - "{dato}"' for dato in dati) or '   - "Scopri i vantaggi su JobInPA"'
    riga_sottotitolo = (
        f'3. Below the headline, a short subtitle line with this exact text: "{request.sottotitolo}".\n'
        if request.sottotitolo else "")
    soggetto = request.prompt_ai or (
        "a glossy 3D gift box with a ribbon, next to a stylized desk "
        "calendar, small sparkles around them")
    return (
        "Design a single complete Instagram promotional graphic for JobInPA "
        "(an AI-powered job search assistant for Italian public "
        "administration jobs). A polished modern SaaS marketing graphic "
        "design, NOT a plain photo — real crisp, legible, correctly spelled "
        "Italian text rendered directly in the image, exactly as quoted "
        "below. No other text, no placeholder lorem ipsum, no invented "
        "numbers or words beyond what is quoted here.\n\n"
        "Composition, top to bottom:\n"
        "1. Top-left: a small rounded pill badge with a sparkle icon and "
        "this exact bold white text: \"PROMOZIONE JOBINPA\".\n"
        "2. Top-right: the brand wordmark \"JobInPA\" in bold navy blue.\n"
        f'3. A large bold headline with this exact text: "{request.titolo}".\n'
        f"{riga_sottotitolo}"
        f"4. On the right side of the composition, {soggetto}.\n"
        "5. A white rounded card listing exactly these lines, each with a "
        f"small relevant icon (calendar, lock, or people):\n{righe_dati}\n"
        "6. At the bottom, a full-width rounded button with a purple-to-navy "
        "gradient, a white circular arrow icon, and this exact bold white "
        "text: \"Scopri di più su JobInPA\".\n"
    )


# Formati OpenAI Images supportati da gpt-image-1/dall-e-3 (no formati
# custom): si sceglie il piu' vicino al rapporto d'aspetto richiesto, poi si
# adatta con un resize "a copertura" + ritaglio centrale — mai uno stretch,
# che deforma visibilmente edifici/persone nell'illustrazione.
_TAGLIE_OPENAI = {"quadrato": "1024x1024", "verticale": "1024x1536", "orizzontale": "1536x1024"}


def _taglia_openai_per_formato(formato):
    larghezza, altezza = FORMATI[formato]
    rapporto = larghezza / altezza
    if rapporto > 1.15:
        return _TAGLIE_OPENAI["orizzontale"]
    if rapporto < 0.87:
        return _TAGLIE_OPENAI["verticale"]
    return _TAGLIE_OPENAI["quadrato"]


def _ridimensiona_a_copertura(immagine, larghezza_target, altezza_target):
    """Resize 'cover' + crop centrale: riempie il canvas target senza
    deformare l'immagine (a differenza di un resize diretto, che stira).
    Adatta a uno sfondo/illustrazione qualsiasi, dove perdere un bordo non
    ha conseguenze — MAI per una grafica gia' completa con testo/pulsanti
    fino ai bordi (vedi _ridimensiona_a_contenimento)."""
    rapporto = max(larghezza_target / immagine.width, altezza_target / immagine.height)
    nuova_larghezza = round(immagine.width * rapporto)
    nuova_altezza = round(immagine.height * rapporto)
    ridimensionata = immagine.resize((nuova_larghezza, nuova_altezza))
    sinistra = (nuova_larghezza - larghezza_target) // 2
    alto = (nuova_altezza - altezza_target) // 2
    return ridimensionata.crop((sinistra, alto, sinistra + larghezza_target, alto + altezza_target))


def _ridimensiona_a_contenimento(immagine, larghezza_target, altezza_target, colore_sfondo):
    """Resize 'contain': mai un crop, l'immagine intera resta visibile e il
    canvas target viene riempito con un bordo pieno del colore indicato se
    le proporzioni non coincidono esattamente. Le taglie fisse di OpenAI
    Images (_TAGLIE_OPENAI) non coincidono mai esattamente con i formati
    social (es. verticale 1024x1536 = rapporto 0.667 contro 1080x1350 =
    0.8): un crop "a copertura" tagliava via badge/logo in alto e il
    bottone CTA in fondo del template "promozione", che l'AI compone fino
    ai bordi (bug segnalato dall'utente: risultato 'tutto tagliato')."""
    rapporto = min(larghezza_target / immagine.width, altezza_target / immagine.height)
    nuova_larghezza = max(1, round(immagine.width * rapporto))
    nuova_altezza = max(1, round(immagine.height * rapporto))
    ridimensionata = immagine.resize((nuova_larghezza, nuova_altezza))
    sfondo = Image.new("RGB", (larghezza_target, altezza_target), colore_sfondo)
    x = (larghezza_target - nuova_larghezza) // 2
    y = (altezza_target - nuova_altezza) // 2
    sfondo.paste(ridimensionata, (x, y))
    return sfondo


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
        # "promozione" (categoria Promozioni, vedi agents.visual): layout a
        # card chiaro con illustrazione laterale + CTA, non l'immagine a
        # tutto schermo qui sotto — quel formato "foto con didascalia" e'
        # troppo lontano dal mockup atteso (segnalato dall'utente) per
        # qualsiasi post promozionale, indipendentemente dallo stile
        # dell'illustrazione AI (gia' corretto separatamente, vedi
        # stile_ai/_STILE_OPENAI_IMAGES).
        if request.template == "promozione":
            return await self._genera_promozione(request)
        import asyncio
        prezzo = self._verifica_budget()
        larghezza, altezza = FORMATI[request.formato]
        taglia = _taglia_openai_per_formato(request.formato)
        sfondo_bytes = await asyncio.to_thread(
            self._chiama_api, request.prompt_ai or request.titolo, taglia,
            request.immagini_riferimento, request.stile_ai)
        db_social.registra_costo(self.conn, "openai_images", prezzo,
                                 modello=config.openai_image_model(),
                                 content_id=request.content_id)
        import io
        sfondo = Image.open(io.BytesIO(sfondo_bytes)).convert("RGB")
        # Resize "a copertura" (mai stretch): la taglia OpenAI piu' vicina al
        # rapporto d'aspetto target puo' comunque differire leggermente.
        sfondo = _ridimensiona_a_copertura(sfondo, larghezza, altezza)
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

    async def _genera_promozione(self, request: ImageGenerationRequest) -> GeneratedAsset:
        """A differenza di ogni altro template, qui l'AI compone l'INTERA
        grafica — badge, titolo, elenco punti, bottone CTA, testo incluso —
        invece di generare solo un'illustrazione su cui poi sovrapporre un
        layout disegnato con Pillow. Deroga esplicita al principio "mai
        testo generato dall'AI nell'immagine" seguito nel resto del modulo:
        un layout composto a mano (badge+logo+card+bottone via Pillow +
        illustrazione AI laterale) e' rimasto troppo lontano dal mockup
        atteso dall'utente anche dopo piu' iterazioni sullo stile.
        Rischio noto e accettato: il testo nell'immagine generata non e'
        garantito corretto al 100% — il testo esatto e verificato resta
        comunque nella didascalia del post, mai solo nell'immagine."""
        import asyncio
        prezzo = self._verifica_budget()
        larghezza, altezza = FORMATI[request.formato]
        taglia = _taglia_openai_per_formato(request.formato)
        prompt_composto = _prompt_promozione_completa(request)
        immagine_bytes = await asyncio.to_thread(
            self._chiama_api, prompt_composto, taglia,
            request.immagini_riferimento, request.stile_ai)
        db_social.registra_costo(self.conn, "openai_images", prezzo,
                                 modello=config.openai_image_model(),
                                 content_id=request.content_id)
        import io
        immagine = Image.open(io.BytesIO(immagine_bytes)).convert("RGB")
        palette = {**PALETTE_DEFAULT, **(request.palette or {})}
        # "Contenimento", non "copertura": la grafica intera composta
        # dall'AI ha badge/logo in alto e bottone CTA in fondo, quindi non
        # deve mai perdere un bordo (vedi _ridimensiona_a_contenimento).
        immagine = _ridimensiona_a_contenimento(immagine, larghezza, altezza, palette["sfondo"])
        self.output_dir.mkdir(parents=True, exist_ok=True)
        percorso = self.output_dir / f"ai_promo_{request.formato}_{uuid.uuid4().hex[:10]}.png"
        immagine.save(percorso, "PNG")
        return GeneratedAsset(percorso=percorso, provider=self.nome,
                              template=request.template, formato=request.formato)

    def _chiama_api(self, prompt, taglia, immagini_riferimento=None, stile_ai=None):
        import base64
        import requests
        # .rstrip() + spazio esplicito: lo stile (fisso o da categoria,
        # quest'ultimo salvato senza spazi finali da crea_categoria/
        # aggiorna_categoria, vedi db_social.py) non deve mai attaccarsi al
        # soggetto per mancanza di uno spazio di separazione.
        prompt_completo = (stile_ai or _STILE_OPENAI_IMAGES).rstrip() + " " + prompt
        percorsi_validi = [p for p in (immagini_riferimento or []) if p and Path(p).is_file()]
        if percorsi_validi:
            # /v1/images/edits: le immagini di riferimento (categoria
            # personalizzata, vedi social_content_categories) guidano
            # davvero lo stile generato, non sono solo una nota testuale —
            # multipart, non JSON. Piu' file sotto la stessa chiave
            # "image[]" (gpt-image-1 accetta piu' immagini in una richiesta).
            file_aperti = [open(p, "rb") for p in percorsi_validi]
            try:
                files = [("image[]", (Path(p).name, f, "image/png"))
                        for p, f in zip(percorsi_validi, file_aperti)]
                risposta = requests.post(
                    "https://api.openai.com/v1/images/edits",
                    headers={"Authorization": f"Bearer {config.openai_api_key()}"},
                    data={"model": config.openai_image_model(), "prompt": prompt_completo,
                          "n": 1, "size": taglia},
                    files=files, timeout=120)
            finally:
                for f in file_aperti:
                    f.close()
        else:
            risposta = requests.post(
                "https://api.openai.com/v1/images/generations",
                headers={"Authorization": f"Bearer {config.openai_api_key()}"},
                json={"model": config.openai_image_model(), "prompt": prompt_completo,
                      "n": 1, "size": taglia},
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
