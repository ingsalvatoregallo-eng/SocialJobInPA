"""
base.py — interfaccia comune degli adapter social.

publish() e' l'unico punto che tocca le API di pubblicazione: publishing.py
lo chiama SOLO dopo che l'intera catena di controlli (kill switch, stato
account, approvazione, rischio, budget) e' passata.
"""

import uuid
from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class PublishResult:
    remote_id: str
    remote_url: Optional[str] = None


def asset_a_lista(asset_path):
    """Normalizza asset_path (singolo percorso/URL, lista, o None) in una
    lista: publishing.py passa sempre tutti gli asset del contenuto per
    quella piattaforma, che siano 1 (post normale) o piu' (carosello
    Instagram) — ogni adapter decide qui quanti usarne."""
    if asset_path is None:
        return []
    if isinstance(asset_path, (list, tuple)):
        return [str(p) for p in asset_path if p]
    return [str(asset_path)]


class SocialAdapter(Protocol):
    piattaforma: str

    def health_check(self) -> dict:
        """{pronto: bool, checklist: [{voce, ok, dettaglio}], messaggio}."""
        ...

    def publish(self, testo: str, asset_path=None) -> PublishResult:
        """asset_path: percorso/URL singolo, lista di percorsi/URL (post
        multi-immagine, es. carosello Instagram fino a 10), o None."""
        ...

    def fetch_metrics(self, remote_id: str) -> dict:
        ...

    def fetch_comments(self, remote_id: str) -> list:
        ...


class MockAdapter:
    """Pubblicazione simulata: nessuna chiamata esterna, id deterministici
    riconoscibili (prefisso mock-)."""

    def __init__(self, piattaforma):
        self.piattaforma = piattaforma
        self.pubblicati = []

    def health_check(self):
        return {"pronto": True, "checklist": [
            {"voce": "Modalita' mock attiva", "ok": True,
             "dettaglio": "nessuna chiamata esterna"}],
            "messaggio": f"{self.piattaforma}: publisher mock"}

    def publish(self, testo, asset_path=None):
        remote_id = f"mock-{self.piattaforma}-{uuid.uuid4().hex[:10]}"
        percorsi = asset_a_lista(asset_path)
        self.pubblicati.append({"remote_id": remote_id, "testo": testo,
                                "asset": percorsi or None})
        return PublishResult(remote_id=remote_id,
                             remote_url=f"https://example.invalid/{remote_id}")

    def fetch_metrics(self, remote_id):
        # Metriche mock chiaramente riconoscibili: mai spacciate per reali.
        if self.piattaforma == "instagram":
            return {"demo": True, "impressions": 120, "reach": 100, "likes": 8,
                    "comments": 1, "shares": 0, "saves": 2, "profile_visits": 4,
                    "engagement_rate": 0.09}
        return {"demo": True, "impressions": 90, "clicks": 6, "reactions": 5,
                "comments": 1, "reposts": 0, "engagement_rate": 0.13}

    def fetch_comments(self, remote_id):
        return [{"remote_id": f"{remote_id}-c1", "autore": "utente_demo",
                 "testo": "[DEMO] Interessante! Dove trovo il bando completo?"}]
