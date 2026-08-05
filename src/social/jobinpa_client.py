"""
jobinpa_client.py — client verso le API private di JobInPA (VM Aruba).

E' l'UNICO canale con cui SocialJobInPA legge i dati del portale: bandi con
classificazione AI (sintesi, requisiti, titoli di studio, competenze),
filtrabili con lo stesso vocabolario gia' usato da /api/tenders su JobInPA
(regione/categoria/settore/ente/competenza/ambito/inquadramento/
titolo_studio/tipo_contratto/posti_minimi/lavoro_agile, piu' una ricerca
full-text libera). Autenticazione con API key dedicata nell'header
X-Internal-Api-Key — mai token utente. Configurazione:

    JOBINPA_API_URL=https://jobinpa.it        (o http://localhost:8000 in dev)
    JOBINPA_API_KEY=...                        (stessa INTERNAL_API_KEY della VM)

Senza configurazione il client e' "vuoto" ma non rompe nulla: il Research
Agent lavora col solo brief (utile in mock/demo). I dati che arrivano dal
portale sono comunque trattati come input, non come istruzioni: finiscono
nei blocchi <fonte> dei prompt come ogni altra fonte.
"""

import logging
import time

import requests

from social import config

log = logging.getLogger(__name__)

_TIMEOUT = 30
_FILTRI_CACHE_TTL_SECONDI = 3600  # 1 ora: i vocabolari chiusi cambiano raramente


class JobInPAClient:
    def __init__(self, base_url=None, api_key=None):
        self.base_url = (base_url or config.jobinpa_api_url()).rstrip("/")
        self.api_key = api_key or config.jobinpa_api_key()
        self._filtri_cache = None
        self._filtri_cache_at = 0.0

    @property
    def configurato(self):
        return bool(self.base_url and self.api_key)

    def _get(self, percorso, params=None):
        risposta = requests.get(
            f"{self.base_url}{percorso}", params=params,
            headers={"X-Internal-Api-Key": self.api_key}, timeout=_TIMEOUT)
        risposta.raise_for_status()
        return risposta.json()

    def bandi(self, *, stato="OPEN", limit=5, solo_classificati=True, query=None,
              regione=None, categoria=None, settore=None, ente=None, competenza=None,
              ambito=None, inquadramento=None, titolo_studio=None, tipo_contratto=None,
              posti_minimi=None, lavoro_agile=None, scadenza_da=None, scadenza_a=None,
              solo_concorsi=None):
        """Bandi con classificazione AI, filtrati. Lista di dict; [] se non
        configurato o in caso di errore di rete (loggato): la pipeline non
        deve mai fallire perche' il portale e' irraggiungibile.

        solo_concorsi=True esclude mobilita'/distacchi/incarichi per
        collaboratori esterni (vedi db.tipologia_bando su JobInPA): usarlo
        per la categoria bandi_jobinpa, che deve trattare solo concorsi
        veri (vedi agents._contesto_jobinpa)."""
        if not self.configurato:
            log.info("JobInPA API non configurata (JOBINPA_API_URL/KEY): nessun bando")
            return []
        params = {"stato": stato, "limit": limit,
                  "solo_classificati": "true" if solo_classificati else "false"}
        opzionali = {
            "query": query, "regione": regione, "categoria": categoria, "settore": settore,
            "ente": ente, "competenza": competenza, "ambito": ambito,
            "inquadramento": inquadramento, "titolo_studio": titolo_studio,
            "tipo_contratto": tipo_contratto, "posti_minimi": posti_minimi,
            "lavoro_agile": lavoro_agile, "scadenza_da": scadenza_da, "scadenza_a": scadenza_a,
        }
        params.update({k: v for k, v in opzionali.items() if v is not None})
        if solo_concorsi is not None:
            params["solo_concorsi"] = "true" if solo_concorsi else "false"
        try:
            return self._get("/api/internal/bandi", params)["bandi"]
        except requests.RequestException as errore:
            log.warning("lettura bandi da JobInPA fallita: %s", errore)
            return []

    def bandi_semantici(self, query, *, stato="OPEN", limit=5, regione=None, categoria=None,
                       settore=None, ente=None, competenza=None, ambito=None,
                       inquadramento=None, titolo_studio=None, tipo_contratto=None,
                       lavoro_agile=None, scadenza_da=None, scadenza_a=None,
                       solo_concorsi=None):
        """Ricerca semantica (embedding + reranking AI lato JobInPA, vedi
        /api/internal/bandi/semantica): `query` e' il testo libero del brief,
        non un valore di vocabolario — la corrispondenza e' un giudizio di
        pertinenza dell'AI, non un match esatto sui filtri strutturati come
        in bandi(). Gli altri parametri restano vincoli duri applicati PRIMA
        del confronto semantico — scadenza_da/scadenza_a in particolare sono
        l'unico modo affidabile di applicare un vincolo temporale (es. "in
        scadenza nei prossimi 7 giorni"): il reranking non vede le date dei
        bandi, quindi non potrebbe verificarlo da solo (vedi
        agents.interpreta_brief). solo_concorsi=True esclude mobilita'/
        distacchi/incarichi esterni PRIMA del confronto semantico, stesso
        motivo: il reranking valuta la pertinenza del testo, non la
        tipologia contrattuale. Lista di dict (bando completo + coerenza_
        semantica/motivo_match); [] se non configurato, in caso di errore di
        rete, o se nessun candidato e' genuinamente pertinente."""
        if not self.configurato:
            log.info("JobInPA API non configurata (JOBINPA_API_URL/KEY): nessun bando")
            return []
        params = {"query": query, "stato": stato, "limit": limit}
        opzionali = {
            "regione": regione, "categoria": categoria, "settore": settore, "ente": ente,
            "competenza": competenza, "ambito": ambito, "inquadramento": inquadramento,
            "titolo_studio": titolo_studio, "tipo_contratto": tipo_contratto,
            "lavoro_agile": lavoro_agile, "scadenza_da": scadenza_da, "scadenza_a": scadenza_a,
        }
        params.update({k: v for k, v in opzionali.items() if v is not None})
        if solo_concorsi is not None:
            params["solo_concorsi"] = "true" if solo_concorsi else "false"
        try:
            return self._get("/api/internal/bandi/semantica", params)["bandi"]
        except requests.RequestException as errore:
            log.warning("ricerca semantica su JobInPA fallita: %s", errore)
            return []

    def bando(self, concorso_id):
        """Dettaglio completo di un bando (con descrizione dettagliata), o None."""
        if not self.configurato:
            return None
        try:
            return self._get(f"/api/internal/bandi/{concorso_id}")
        except requests.RequestException as errore:
            log.warning("lettura bando %s da JobInPA fallita: %s", concorso_id, errore)
            return None

    def promozioni(self):
        """Piani/pacchetti con promozione davvero attiva in questo momento
        (nome, descrizione, prezzo/prezzo promozionale, scadenza, link
        JobInPA): usate per creare contenuti 'promozione' senza inserire
        dati a mano (mai un claim commerciale senza fonte verificabile).
        Lista di dict; [] se non configurato o in caso di errore di rete."""
        if not self.configurato:
            log.info("JobInPA API non configurata (JOBINPA_API_URL/KEY): nessuna promozione")
            return []
        try:
            return self._get("/api/internal/promozioni")["promozioni"]
        except requests.RequestException as errore:
            log.warning("lettura promozioni da JobInPA fallita: %s", errore)
            return []

    def funzionalita(self):
        """Catalogo delle funzionalita' del sito + statistiche d'uso
        aggregate (mai per singolo utente): usate per creare contenuti
        'funzionalita' senza inserire dati a mano (mai un claim inventato
        su cosa fa JobInPA). Dict {"funzionalita": [...], "statistiche":
        {...}}; {} se non configurato o in caso di errore di rete."""
        if not self.configurato:
            log.info("JobInPA API non configurata (JOBINPA_API_URL/KEY): nessuna funzionalità")
            return {}
        try:
            return self._get("/api/internal/funzionalita")
        except requests.RequestException as errore:
            log.warning("lettura funzionalità da JobInPA fallita: %s", errore)
            return {}

    def filtri_disponibili(self):
        """Vocabolari chiusi/valori realmente presenti per i filtri di
        bandi() (regioni, categorie, competenze, ecc.) — serve all'interprete
        del brief per non inventare valori che poi non esistono. Cache in
        memoria di processo per un'ora: non e' un dato che cambia spesso, e
        interrogarlo ad ogni pipeline sarebbe una chiamata di rete sprecata.
        {} se non configurato o in caso di errore (l'interprete brief
        degrada a nessun filtro strutturato, non blocca la pipeline)."""
        if not self.configurato:
            return {}
        adesso = time.monotonic()
        if self._filtri_cache is not None and (adesso - self._filtri_cache_at) < _FILTRI_CACHE_TTL_SECONDI:
            return self._filtri_cache
        try:
            self._filtri_cache = self._get("/api/internal/bandi/filtri")
            self._filtri_cache_at = adesso
            return self._filtri_cache
        except requests.RequestException as errore:
            log.warning("lettura filtri da JobInPA fallita: %s", errore)
            return self._filtri_cache or {}


_client_default = None


def client():
    """Istanza condivisa (la config e' letta una volta; nei test si passa
    un client esplicito alle funzioni che lo accettano)."""
    global _client_default
    if _client_default is None:
        _client_default = JobInPAClient()
    return _client_default
