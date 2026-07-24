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
    nessun_criterio_specifico: bool = Field(
        default=True,
        description="True se il brief NON chiede filtri specifici (es. tema generico "
                    "'novita della settimana'): in questo caso tutti gli altri campi "
                    "restano None e la ricerca NON viene considerata 'senza risultati' "
                    "se torna vuota — semplicemente non c'era un criterio da soddisfare.")


class RisultatoRicerca(BaseModel):
    fatti: list[FattoVerificato] = Field(default_factory=list)
    sintesi: str = ""
    fonti_consultate: list[str] = Field(default_factory=list)
    richiede_revisione: bool = False
    note: Optional[str] = None


class VarianteCopy(BaseModel):
    testo: str
    hashtags: list[str] = Field(default_factory=list)
    call_to_action: Optional[str] = None


class CopyMultiPiattaforma(BaseModel):
    instagram: VarianteCopy
    linkedin: VarianteCopy


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


class PianoSettimanale(BaseModel):
    voci: list[VoceCalendario] = Field(default_factory=list)


class SintesiAnalytics(BaseModel):
    sintesi: str
    raccomandazioni: list[str] = Field(default_factory=list)
