"""
jobinpa_client.py — client verso le API private di JobInPA (VM Aruba).

E' l'UNICO canale con cui SocialJobInPA legge i dati del portale: bandi
con classificazione AI (sintesi, requisiti, titoli di studio, competenze).
Autenticazione con API key dedicata nell'header X-Internal-Api-Key — mai
token utente. Configurazione:

    JOBINPA_API_URL=https://jobinpa.it        (o http://localhost:8000 in dev)
    JOBINPA_API_KEY=...                        (stessa INTERNAL_API_KEY della VM)

Senza configurazione il client e' "vuoto" ma non rompe nulla: il Research
Agent lavora col solo brief (utile in mock/demo). I dati che arrivano dal
portale sono comunque trattati come input, non come istruzioni: finiscono
nei blocchi <fonte> dei prompt come ogni altra fonte.
"""

import logging

import requests

from social import config

log = logging.getLogger(__name__)

_TIMEOUT = 30


class JobInPAClient:
    def __init__(self, base_url=None, api_key=None):
        self.base_url = (base_url or config.jobinpa_api_url()).rstrip("/")
        self.api_key = api_key or config.jobinpa_api_key()

    @property
    def configurato(self):
        return bool(self.base_url and self.api_key)

    def _get(self, percorso, params=None):
        risposta = requests.get(
            f"{self.base_url}{percorso}", params=params,
            headers={"X-Internal-Api-Key": self.api_key}, timeout=_TIMEOUT)
        risposta.raise_for_status()
        return risposta.json()

    def bandi(self, *, stato="OPEN", limit=5, competenza=None, solo_classificati=True):
        """Bandi con classificazione AI. Lista di dict; [] se non configurato
        o in caso di errore di rete (loggato): la pipeline non deve mai
        fallire perche' il portale e' irraggiungibile."""
        if not self.configurato:
            log.info("JobInPA API non configurata (JOBINPA_API_URL/KEY): nessun bando")
            return []
        params = {"stato": stato, "limit": limit,
                  "solo_classificati": "true" if solo_classificati else "false"}
        if competenza:
            params["competenza"] = competenza
        try:
            return self._get("/api/internal/bandi", params)["bandi"]
        except requests.RequestException as errore:
            log.warning("lettura bandi da JobInPA fallita: %s", errore)
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


_client_default = None


def client():
    """Istanza condivisa (la config e' letta una volta; nei test si passa
    un client esplicito alle funzioni che lo accettano)."""
    global _client_default
    if _client_default is None:
        _client_default = JobInPAClient()
    return _client_default
