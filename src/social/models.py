"""
models.py — schemi Pydantic degli output strutturati degli agenti.

Ogni chiamata LLM valida la risposta contro uno di questi schemi (vedi
llm.py): un output non conforme e' un errore del provider, mai dati sporchi
che proseguono nella pipeline.
"""

from typing import Optional

from pydantic import BaseModel, Field


class FattoVerificato(BaseModel):
    fatto: str
    fonte_url: Optional[str] = None
    confidenza: float = Field(ge=0, le=1)
    in_conflitto: bool = False


class CriteriRicerca(BaseModel):
    """Traduzione del brief in linguaggio naturale nei filtri REALI di
    JobInPA (vedi jobinpa_client.filtri_disponibili()). Ogni campo va
    valorizzato SOLO se il brief lo richiede esplicitamente o implicitamente
    in modo chiaro, e SOLO con un valore preso dai vocabolari forniti nel
    prompt — mai un valore inventato: se il brief non specifica un criterio,
    o il valore non esiste nei vocabolari, il campo resta None."""
    query_testuale: Optional[str] = Field(
        default=None, description="parole chiave per la ricerca full-text, se il brief ne suggerisce")
    regione: Optional[str] = None
    categoria: Optional[str] = None
    settore: Optional[str] = None
    ente: Optional[str] = None
    competenza: Optional[str] = Field(default=None, description="un valore dal vocabolario competenze")
    ambito: Optional[str] = Field(default=None, description="un valore dal vocabolario ambiti")
    inquadramento: Optional[str] = None
    titolo_studio: Optional[str] = None
    tipo_contratto: Optional[str] = None
    posti_minimi: Optional[int] = Field(default=None, ge=1)
    lavoro_agile: Optional[bool] = None
    scadenza_da: Optional[str] = Field(
        default=None,
        description="Data ISO (YYYY-MM-DD): SOLO se il brief chiede un vincolo esplicito "
                    "sulla scadenza del bando (es. 'in scadenza nei prossimi 7 giorni'). "
                    "Calcola la data concreta usando la data di oggi fornita nel prompt "
                    "-- non lasciare la frase relativa cosi' com'e', il confronto e' "
                    "sulla data reale del bando, non un giudizio dell'AI.")
    scadenza_a: Optional[str] = Field(
        default=None, description="Data ISO (YYYY-MM-DD), vedi scadenza_da.")
    nessun_criterio_specifico: bool = Field(
        default=True,
        description="True se il brief NON chiede filtri specifici (es. tema generico "
                    "'novita della settimana'): in questo caso tutti gli altri campi "
                    "restano None e la ricerca NON viene considerata 'senza risultati' "
                    "se torna vuota — semplicemente non c'era un criterio da soddisfare.")
    annuncio_funzionalita: bool = Field(
        default=False,
        description="True se il brief promuove una funzionalita'/iniziativa della "
                    "piattaforma JobInPA stessa (es. 'il premium e' gratis fino al 31 "
                    "agosto', 'c'e' la funzionalita' inviti amici', 'bandi consigliati "
                    "dall'analisi del CV') invece di cercare bandi specifici da "
                    "pubblicizzare. Quando True la ricerca su JobInPA viene saltata: il "
                    "brief stesso e' trattato come la fonte, non un criterio di ricerca.")


class RisultatoRicerca(BaseModel):
    fatti: list[FattoVerificato] = Field(default_factory=list)
    sintesi: str = ""
    fonti_consultate: list[str] = Field(default_factory=list)
    richiede_revisione: bool = False
    note: Optional[str] = None
    bandi_trovati: list[dict] = Field(
        default_factory=list,
        description="Record grezzi dei bandi JobInPA trovati (non generati dall'AI: "
                    "popolato da research() dopo la chiamata al modello). Usato dal "
                    "Visual Agent per generare un'immagine per bando in un carosello "
                    "Instagram invece di farne scegliere uno solo al modello.")
    annuncio_funzionalita: bool = Field(
        default=False,
        description="Copiato da CriteriRicerca.annuncio_funzionalita (non generato "
                    "direttamente dal modello di questo schema): usato da "
                    "esegui_pipeline per forzare SEMPRE una revisione umana prima della "
                    "pubblicazione, anche a classe di rischio verde — un annuncio sulla "
                    "piattaforma non e' verificato da una fonte esterna come un bando.")


class VerificaTestoImmagine(BaseModel):
    """Giudizio di un secondo modello (con visione) sul testo che l'AI ha
    disegnato dentro un'immagine (badge/titolo/dati/CTA, vedi images.
    _prompt_grafica_intera): la generazione immagini non rende il testo in
    modo deterministico, quindi il prompt da solo non basta a garantirlo
    corretto — serve una verifica indipendente sull'immagine finita, non
    solo un'istruzione piu' insistente nel prompt."""
    testo_corretto: bool = Field(
        description="True SOLO se ogni stringa quotata e' riprodotta esattamente, "
                    "lettera per lettera (accenti italiani inclusi), senza lettere "
                    "mancanti, ripetute, invertite o parole troncate.")
    problemi: list[str] = Field(
        default_factory=list,
        description="Elenco breve e concreto dei problemi trovati (es. 'il badge dice "
                    "\"NUVO CONCORSO\" invece di \"NUOVO CONCORSO\"'), vuoto se "
                    "testo_corretto=True.")


class VarianteCopy(BaseModel):
    testo: str
    hashtags: list[str] = Field(default_factory=list)
    call_to_action: Optional[str] = None


class CopyMultiPiattaforma(BaseModel):
    # Opzionali: generati solo per i canali selezionati sul contenuto
    # (content.canali) — niente chiamata LLM sprecata per un canale non
    # scelto (es. LinkedIn quando l'account non e' ancora abilitato).
    instagram: Optional[VarianteCopy] = None
    linkedin: Optional[VarianteCopy] = None


class VisualBrief(BaseModel):
    template: str = Field(description="chiave di uno dei template deterministici")
    titolo: str
    sottotitolo: Optional[str] = None
    dati_chiave: list[str] = Field(default_factory=list,
                                   description="scadenze/posti/ente: SEMPRE resi in overlay deterministico")
    prompt_ai: Optional[str] = Field(default=None,
                                     description="prompt per OpenAI Images, solo se abilitato")


class ValutazioneRischio(BaseModel):
    classe: str = Field(description="verde | giallo | rosso")
    punteggio_accuratezza: float = Field(ge=0, le=1)
    punteggio_brand: float = Field(ge=0, le=1)
    punteggio_conformita: float = Field(ge=0, le=1)
    motivi: list[str] = Field(default_factory=list)


class RispostaCommento(BaseModel):
    testo: str
    tono: str = "professionale"
    da_escalare: bool = False


class VoceCalendario(BaseModel):
    tema: str
    pillar: str = Field(description="opportunita | guida | scadenza")
    obiettivo: str
    fascia_oraria: str
    giorno_settimana: str = Field(
        description="uno tra: lunedi, martedi, mercoledi, giovedi, venerdi, sabato, domenica — "
                    "il giorno della settimana in cui proporre di pubblicare questo tema")
    categoria_nome: str = Field(
        description="il nome ESATTO di una delle categorie fornite nel prompt (vocabolario "
                    "chiuso) — mai un nome inventato o simile-ma-non-uguale. Il tema deve "
                    "essere coerente con quella categoria (es. un tema su un concorso -> una "
                    "categoria che cerca bandi JobInPA, non una categoria libera generica).")


class PianoSettimanale(BaseModel):
    voci: list[VoceCalendario] = Field(default_factory=list)


class SintesiAnalytics(BaseModel):
    sintesi: str
    raccomandazioni: list[str] = Field(default_factory=list)
