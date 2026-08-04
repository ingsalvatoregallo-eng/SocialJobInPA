"""
llm.py — provider LLM astratto (sez. 6 del prompt master).

La business logic (agents.py) parla solo con LLMProvider.generate_structured:
riceve un'istanza Pydantic validata, mai testo libero. AnthropicProvider usa
il tool-use per forzare l'output nello schema; MockLLMProvider produce
risposte deterministiche per test e modalita' mock.

Protezioni: timeout, retry con backoff esponenziale, circuit breaker (dopo
N errori consecutivi il provider si apre e rifiuta le chiamate per un
cooldown), budget giornaliero/mensile con blocco al 100% e incidente all'80%,
log token e costi in social_cost_entries.
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from social import config, db_social  # noqa: E402

log = logging.getLogger(__name__)


class ErroreProvider(RuntimeError):
    pass


class BudgetEsaurito(ErroreProvider):
    pass


class CircuitAperto(ErroreProvider):
    pass


class LLMProvider(Protocol):
    async def generate_structured(self, system_prompt: str, user_prompt: str,
                                  schema: type[BaseModel], **options) -> BaseModel:
        ...


# --- Budget ------------------------------------------------------------------

class BudgetManager:
    """Contabilita' e limiti di spesa per provider, sul DB condiviso."""

    def __init__(self, conn, provider, budget_mensile_eur, budget_giornaliero_eur=None):
        self.conn = conn
        self.provider = provider
        self.mensile = budget_mensile_eur
        self.giornaliero = budget_giornaliero_eur

    def verifica(self, costo_stimato_eur=0.0):
        """Solleva BudgetEsaurito se la spesa (piu' la stima) supera un limite;
        registra un incidente al superamento della soglia di alert (80%)."""
        speso_mese = db_social.costo_periodo(self.conn, self.provider)
        soglia = db_social.get_setting(self.conn, "alert_budget_percent", 80) / 100
        if self.mensile and speso_mese + costo_stimato_eur >= self.mensile:
            db_social.registra_incidente(
                self.conn, "budget",
                f"budget mensile {self.provider} esaurito: {speso_mese:.2f}/{self.mensile:.2f} EUR")
            raise BudgetEsaurito(f"budget mensile {self.provider} esaurito")
        if self.mensile and speso_mese >= self.mensile * soglia:
            gia_segnalato = any(
                f"soglia {int(soglia*100)}% {self.provider}" in (r["dettaglio"] or "")
                for r in db_social.incidenti_aperti(self.conn))
            if not gia_segnalato:
                db_social.registra_incidente(
                    self.conn, "budget",
                    f"soglia {int(soglia*100)}% {self.provider}: "
                    f"{speso_mese:.2f}/{self.mensile:.2f} EUR")
        if self.giornaliero:
            speso_giorno = db_social.costo_periodo(self.conn, self.provider, giorni=1)
            if speso_giorno + costo_stimato_eur >= self.giornaliero:
                raise BudgetEsaurito(f"budget giornaliero {self.provider} esaurito")

    def registra(self, costo_eur, *, modello=None, content_id=None, agente=None,
                 token_input=None, token_output=None, stimato=False):
        db_social.registra_costo(
            self.conn, self.provider, costo_eur, modello=modello,
            content_id=content_id, agente=agente, token_input=token_input,
            token_output=token_output, stimato=stimato)


# --- Circuit breaker ---------------------------------------------------------

class CircuitBreaker:
    def __init__(self, soglia_errori=3, cooldown_secondi=120):
        self.soglia = soglia_errori
        self.cooldown = cooldown_secondi
        self.errori_consecutivi = 0
        self.aperto_fino_a = 0.0

    def verifica(self):
        if time.monotonic() < self.aperto_fino_a:
            raise CircuitAperto("circuito aperto: provider in cooldown dopo errori ripetuti")

    def successo(self):
        self.errori_consecutivi = 0

    def errore(self):
        self.errori_consecutivi += 1
        if self.errori_consecutivi >= self.soglia:
            self.aperto_fino_a = time.monotonic() + self.cooldown


# --- Anthropic ---------------------------------------------------------------

class AnthropicProvider:
    """Output strutturato via tool-use: il modello DEVE chiamare il tool
    'rispondi' il cui input_schema e' lo schema Pydantic richiesto."""

    nome = "anthropic"

    def __init__(self, conn, *, budget=None, timeout=60.0, max_retry=3):
        self.conn = conn
        self.modello = config.anthropic_model()
        self.max_tokens = config.anthropic_max_tokens()
        self.timeout = timeout
        self.max_retry = max_retry
        self.budget = budget or BudgetManager(
            conn, "anthropic", config.anthropic_monthly_budget_eur(),
            config.anthropic_daily_budget_eur())
        self.circuit = CircuitBreaker()
        chiave = config.anthropic_api_key()
        if not chiave:
            raise ErroreProvider("ANTHROPIC_API_KEY non impostata (usa SOCIAL_MODE=mock senza chiave)")
        import anthropic
        self._client = anthropic.AsyncAnthropic(api_key=chiave, timeout=timeout)

    def _costo_eur(self, token_input, token_output):
        prezzi = db_social.get_setting(self.conn, "prezzi_token_eur",
                                       {"input": 2.7, "output": 13.5})
        return (token_input * prezzi["input"] + token_output * prezzi["output"]) / 1_000_000

    async def generate_structured(self, system_prompt, user_prompt, schema, **options):
        self.circuit.verifica()
        self.budget.verifica(costo_stimato_eur=0.01)
        tool = {
            "name": "rispondi",
            "description": "Restituisci il risultato nello schema richiesto.",
            "input_schema": schema.model_json_schema(),
        }
        # immagine_bytes (opzionale, vedi agents._verifica_testo_immagine):
        # allega un'immagine al messaggio, PNG in base64 -- serve per far
        # giudicare a un modello con visione un'immagine gia' generata
        # (es. verificare il testo disegnato dentro una grafica AI), non
        # solo per generare testo da un prompt libero.
        contenuto = user_prompt
        immagine_bytes = options.get("immagine_bytes")
        if immagine_bytes:
            import base64
            contenuto = [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.b64encode(immagine_bytes).decode()}},
                {"type": "text", "text": user_prompt},
            ]
        ultimo_errore = None
        for tentativo in range(self.max_retry):
            try:
                risposta = await self._client.messages.create(
                    model=options.get("model", self.modello),
                    max_tokens=options.get("max_tokens", self.max_tokens),
                    system=system_prompt,
                    messages=[{"role": "user", "content": contenuto}],
                    tools=[tool],
                    tool_choice={"type": "tool", "name": "rispondi"},
                )
                token_in = risposta.usage.input_tokens
                token_out = risposta.usage.output_tokens
                costo = self._costo_eur(token_in, token_out)
                self.budget.registra(
                    costo, modello=self.modello, content_id=options.get("content_id"),
                    agente=options.get("agente"), token_input=token_in,
                    token_output=token_out)
                blocco = next(b for b in risposta.content if b.type == "tool_use")
                risultato = schema.model_validate(blocco.input)
                self.circuit.successo()
                # attributo di servizio per agent_runs (object.__setattr__:
                # Pydantic v2 non consente attributi arbitrari via assegnazione)
                object.__setattr__(risultato, "_token_usage", (token_in, token_out, costo))
                return risultato
            except (BudgetEsaurito, CircuitAperto):
                raise
            except (ValidationError, StopIteration) as errore:
                # output fuori schema: riprovare puo' aiutare, ma e' un errore
                # del modello, non di rete — niente backoff lungo
                ultimo_errore = errore
                self.circuit.errore()
            except Exception as errore:  # rete/API: backoff esponenziale
                ultimo_errore = errore
                self.circuit.errore()
                if tentativo < self.max_retry - 1:
                    await asyncio.sleep(min(2 ** tentativo, 8))
        raise ErroreProvider(f"Anthropic fallito dopo {self.max_retry} tentativi: {ultimo_errore}")


# --- Mock --------------------------------------------------------------------

class MockLLMProvider:
    """Risposte deterministiche per test e modalita' mock. Si possono
    registrare risposte specifiche per schema con `imposta(schema, istanza)`;
    altrimenti viene costruita una risposta demo plausibile."""

    nome = "mock"

    def __init__(self, conn=None):
        self.conn = conn
        self._risposte = {}
        self.chiamate = []  # (system, user, schema) — ispezionabili nei test
        self.immagini_ricevute = []  # immagine_bytes di ogni chiamata che ne allegava una

    def imposta(self, schema, istanza):
        self._risposte[schema.__name__] = istanza

    async def generate_structured(self, system_prompt, user_prompt, schema, **options):
        self.chiamate.append((system_prompt, user_prompt, schema))
        if options.get("immagine_bytes"):
            self.immagini_ricevute.append(options["immagine_bytes"])
        if self.conn is not None:
            db_social.registra_costo(self.conn, "anthropic", 0.0, modello="mock",
                                     content_id=options.get("content_id"),
                                     agente=options.get("agente"),
                                     token_input=0, token_output=0)
        if schema.__name__ in self._risposte:
            return self._risposte[schema.__name__]
        return _risposta_demo(schema)


def _risposta_demo(schema):
    from social import models
    demo = {
        models.VarianteCopy: models.VarianteCopy(
            testo="[DEMO] Nuovo concorso PA: scopri se il tuo profilo e' in "
                  "linea con i requisiti su JobInPA.",
            hashtags=["#concorsipubblici", "#lavoroPA", "#jobinpa"],
            call_to_action="Scopri di piu' su jobinpa.it"),
        models.RisultatoRicerca: models.RisultatoRicerca(
            fatti=[models.FattoVerificato(
                fatto="[DEMO] Concorso demo: 10 posti, scadenza 2026-12-31",
                fonte_url="https://www.inpa.gov.it/", confidenza=0.95)],
            sintesi="[DEMO] Sintesi di ricerca simulata.",
            fonti_consultate=["https://www.inpa.gov.it/"]),
        models.CopyMultiPiattaforma: models.CopyMultiPiattaforma(
            instagram=models.VarianteCopy(
                testo="[DEMO] Nuovo concorso PA: scopri se fa per te su JobInPA!",
                hashtags=["#concorsipubblici", "#lavoroPA", "#jobinpa"],
                call_to_action="Scopri di piu' su jobinpa.it"),
            linkedin=models.VarianteCopy(
                testo="[DEMO] È uscito un nuovo concorso pubblico. Con JobInPA "
                      "l'AI ti aiuta a capire in pochi secondi se il tuo profilo "
                      "e' in linea con i requisiti.",
                hashtags=["#pubblicaamministrazione", "#concorsi"],
                call_to_action="Vai su jobinpa.it")),
        models.VisualBrief: models.VisualBrief(
            template="nuovo_concorso", titolo="[DEMO] Nuovo concorso",
            sottotitolo="Ente demo", dati_chiave=["10 posti", "Scadenza 31/12/2026"]),
        models.ValutazioneRischio: models.ValutazioneRischio(
            classe="verde", punteggio_accuratezza=0.95, punteggio_brand=0.9,
            punteggio_conformita=0.95, motivi=["[DEMO] dati da fonte ufficiale"]),
        models.RispostaCommento: models.RispostaCommento(
            testo="[DEMO] Grazie per il commento! Trovi tutti i dettagli sul "
                  "bando ufficiale linkato nel post."),
        models.PianoSettimanale: models.PianoSettimanale(voci=[
            models.VoceCalendario(tema="[DEMO] Opportunita' della settimana",
                                  pillar="opportunita", obiettivo="traffico",
                                  fascia_oraria="12:00-14:00", giorno_settimana="martedi",
                                  categoria_nome="[DEMO] Categoria"),
            models.VoceCalendario(tema="[DEMO] Guida: come leggere un bando",
                                  pillar="guida", obiettivo="notorieta",
                                  fascia_oraria="08:00-10:00", giorno_settimana="giovedi",
                                  categoria_nome="[DEMO] Categoria"),
            models.VoceCalendario(tema="[DEMO] Concorsi in scadenza",
                                  pillar="scadenza", obiettivo="traffico",
                                  fascia_oraria="17:00-19:00", giorno_settimana="venerdi",
                                  categoria_nome="[DEMO] Categoria")]),
        models.SintesiAnalytics: models.SintesiAnalytics(
            sintesi="[DEMO] Engagement stabile.",
            raccomandazioni=["[DEMO] pubblicare in fascia 12-14"]),
    }
    if schema in demo:
        return demo[schema]
    # Schema non previsto: istanza con i soli default (fallira' la validazione
    # se lo schema ha campi obbligatori — meglio un errore chiaro nei test).
    return schema()


def provider_llm(conn, mode=None):
    """Factory: mock in modalita' mock (o senza chiave), Anthropic altrimenti."""
    mode = mode or db_social.get_setting(conn, "mode_override") or config.mode()
    if mode == "mock" or not config.anthropic_api_key():
        return MockLLMProvider(conn)
    return AnthropicProvider(conn)
