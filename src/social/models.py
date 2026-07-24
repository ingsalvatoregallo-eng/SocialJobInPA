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
