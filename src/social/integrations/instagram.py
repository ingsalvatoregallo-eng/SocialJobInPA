"""
instagram.py — adapter Instagram via Meta Graph API (solo API ufficiali).

Stato reale dell'account JobInPA (sez. 4/32 del prompt): account Business e
Business Portfolio presenti, ma MANCANO la Pagina Facebook collegata e la
Meta Developer App. Finche' la checklist non e' completa l'adapter si dichiara
non pronto e publishing.py blocca ogni pubblicazione reale su Instagram
("Instagram non pronto per la pubblicazione API" in dashboard); la modalita'
mock resta sempre disponibile.

Pubblicazione (quando configurato): flusso ufficiale in due passi del
Content Publishing API — POST /{ig-user-id}/media (container con image_url +
caption) e POST /{ig-user-id}/media_publish. Richiede che l'immagine sia
raggiungibile via URL pubblico: finche' il modulo gira solo in locale questo
e' un requisito documentato nella checklist, non aggirato con workaround.

Post carosello (2-10 immagini, es. una per bando trovato dal Research
Agent): flusso a tre passi, distinto da quello a immagine singola — un
container 'figlio' per immagine (is_carousel_item=true, senza caption),
poi un container padre (media_type=CAROUSEL, children, con la caption
unica), infine la pubblicazione. Stesso requisito di URL pubblico per
ciascuna immagine figlio.
"""

import logging

import requests

from social import config, db_social, security
from social.images import MASSIMO_IMMAGINI_CAROSELLO
from social.integrations.base import PublishResult, asset_a_lista

log = logging.getLogger(__name__)

_GRAPH = "https://graph.facebook.com"


class InstagramAdapter:
    piattaforma = "instagram"

    def __init__(self, conn):
        self.conn = conn
        self.cfg = config.meta_config()
        self.account = db_social.account_per_piattaforma(conn, "instagram")

    # --- Checklist / diagnostica --------------------------------------------

    def health_check(self):
        cfg = self.cfg
        token = self._token()
        checklist = [
            {"voce": "Meta Developer App creata (META_APP_ID/META_APP_SECRET)",
             "ok": bool(cfg["app_id"] and cfg["app_secret"]),
             "dettaglio": "developers.facebook.com > Create App (tipo Business) — vedi docs/meta-instagram-setup.md"},
            {"voce": "Pagina Facebook creata e collegata all'account Instagram Business",
             "ok": bool(cfg["facebook_page_id"]),
             "dettaglio": "La Graph API pubblica su Instagram solo tramite una Pagina collegata"},
            {"voce": "Instagram Business Account ID (INSTAGRAM_ACCOUNT_ID)",
             "ok": bool(cfg["instagram_account_id"]),
             "dettaglio": "GET /{page-id}?fields=instagram_business_account"},
            {"voce": "Token OAuth autorizzato e salvato",
             "ok": token is not None,
             "dettaglio": "Login OAuth con scope instagram_content_publish, pages_read_engagement"},
            {"voce": "Immagini raggiungibili via URL pubblico",
             "ok": False,
             "dettaglio": "Il container /media accetta solo image_url pubblici: "
                          "serve l'esposizione futura (social.jobinpa.it) o uno storage pubblico"},
        ]
        pronto = all(v["ok"] for v in checklist)
        return {"pronto": pronto, "checklist": checklist,
                "messaggio": ("Instagram pronto" if pronto
                              else "Instagram non pronto per la pubblicazione API")}

    def _token(self):
        if self.account is None:
            return None
        riga = db_social.oauth_token_attivo(self.conn, self.account["id"])
        if riga is None:
            return None
        try:
            return security.decrypt_token(riga["token_cifrato"])
        except security.ConfigurazioneMancante as errore:
            log.warning("token Instagram non decifrabile: %s", errore)
            return None

    def completa_oauth(self, code):
        """Scambia il 'code' OAuth ricevuto al callback per un Page Access
        Token utilizzabile per pubblicare sull'account Instagram Business
        collegato alla Pagina configurata (FACEBOOK_PAGE_ID). I Page Access
        Token ottenuti da un token utente long-lived non hanno una scadenza
        fissa nota (restano validi finche' l'utente non revoca l'accesso o
        cambia password) — non serve refresh automatico.
        Solleva RuntimeError con un messaggio chiaro ad ogni passaggio."""
        cfg = self.cfg
        versione = cfg["graph_api_version"]

        # 1) code -> user access token (short-lived, ~1-2 ore)
        risposta = requests.get(
            f"{_GRAPH}/{versione}/oauth/access_token",
            params={"client_id": cfg["app_id"], "redirect_uri": cfg["redirect_uri"],
                    "client_secret": cfg["app_secret"], "code": code}, timeout=30)
        if not risposta.ok:
            raise RuntimeError(f"scambio code->token Meta fallito: {risposta.text[:300]}")
        user_token = risposta.json().get("access_token")
        if not user_token:
            raise RuntimeError("risposta Meta senza access_token")

        # 2) user token -> long-lived (~60 giorni), se il provider lo concede
        risposta = requests.get(
            f"{_GRAPH}/{versione}/oauth/access_token",
            params={"grant_type": "fb_exchange_token", "client_id": cfg["app_id"],
                    "client_secret": cfg["app_secret"], "fb_exchange_token": user_token},
            timeout=30)
        if risposta.ok and risposta.json().get("access_token"):
            user_token = risposta.json()["access_token"]

        # 3) page access token per la Pagina configurata: e' quello che
        # funziona in modo affidabile per pubblicare sull'IG business account
        # collegato (la Content Publishing API lo accetta come access_token).
        if not cfg["facebook_page_id"]:
            raise RuntimeError(
                "FACEBOOK_PAGE_ID non configurato in .env: impostalo prima di autorizzare")
        risposta = requests.get(
            f"{_GRAPH}/{versione}/{cfg['facebook_page_id']}",
            params={"fields": "access_token", "access_token": user_token}, timeout=30)
        if not risposta.ok or not risposta.json().get("access_token"):
            raise RuntimeError(f"impossibile ottenere il Page Access Token: {risposta.text[:300]}")
        return risposta.json()["access_token"]

    def oauth_authorize_url(self, state):
        """URL del consenso Meta (il flusso si completa dalla dashboard)."""
        cfg = self.cfg
        scope = "instagram_basic,instagram_content_publish,pages_read_engagement,pages_show_list"
        return (f"https://www.facebook.com/{cfg['graph_api_version']}/dialog/oauth"
                f"?client_id={cfg['app_id']}&redirect_uri={cfg['redirect_uri']}"
                f"&state={state}&scope={scope}")

    # --- Pubblicazione -------------------------------------------------------

    def publish(self, testo, asset_path=None):
        """asset_path: URL singolo, o lista di URL (2-10) per un post
        carosello — una voce per bando quando il Research Agent ne ha
        trovati piu' di uno (vedi agents.visual)."""
        salute = self.health_check()
        if not salute["pronto"]:
            raise RuntimeError(salute["messaggio"])
        token = self._token()
        versione = self.cfg["graph_api_version"]
        ig_id = self.cfg["instagram_account_id"]
        immagini = asset_a_lista(asset_path)[:MASSIMO_IMMAGINI_CAROSELLO]
        if len(immagini) > 1:
            return self._pubblica_carosello(testo, immagini, token, versione, ig_id)
        dati = {"caption": testo, "access_token": token}
        if immagini:
            dati["image_url"] = immagini[0]
        creazione = requests.post(f"{_GRAPH}/{versione}/{ig_id}/media",
                                  data=dati, timeout=60)
        creazione.raise_for_status()
        container_id = creazione.json()["id"]
        pubblicazione = requests.post(
            f"{_GRAPH}/{versione}/{ig_id}/media_publish",
            data={"creation_id": container_id, "access_token": token}, timeout=60)
        pubblicazione.raise_for_status()
        media_id = pubblicazione.json()["id"]
        return PublishResult(remote_id=media_id,
                             remote_url=f"https://www.instagram.com/p/{media_id}/")

    def _pubblica_carosello(self, testo, immagini, token, versione, ig_id):
        """Post carosello: un container 'figlio' per immagine
        (is_carousel_item=true, senza caption — la caption e' unica sul
        container padre), poi il container CAROUSEL che li referenzia,
        infine la pubblicazione. Se la creazione di un figlio fallisce a
        meta', i container gia' creati restano orfani lato Meta (si
        auto-eliminano dopo 24h, comportamento normale della Content
        Publishing API — non serve una pulizia esplicita qui)."""
        figli = []
        for url in immagini:
            risposta = requests.post(
                f"{_GRAPH}/{versione}/{ig_id}/media",
                data={"image_url": url, "is_carousel_item": "true", "access_token": token},
                timeout=60)
            risposta.raise_for_status()
            figli.append(risposta.json()["id"])
        creazione = requests.post(
            f"{_GRAPH}/{versione}/{ig_id}/media",
            data={"media_type": "CAROUSEL", "caption": testo,
                  "children": ",".join(figli), "access_token": token}, timeout=60)
        creazione.raise_for_status()
        container_id = creazione.json()["id"]
        pubblicazione = requests.post(
            f"{_GRAPH}/{versione}/{ig_id}/media_publish",
            data={"creation_id": container_id, "access_token": token}, timeout=60)
        pubblicazione.raise_for_status()
        media_id = pubblicazione.json()["id"]
        return PublishResult(remote_id=media_id,
                             remote_url=f"https://www.instagram.com/p/{media_id}/")

    def fetch_metrics(self, remote_id):
        token = self._token()
        if not token:
            return {}
        versione = self.cfg["graph_api_version"]
        metriche = "impressions,reach,likes,comments,shares,saved"
        risposta = requests.get(
            f"{_GRAPH}/{versione}/{remote_id}/insights",
            params={"metric": metriche, "access_token": token}, timeout=60)
        if risposta.status_code != 200:
            log.warning("insights Instagram %s: %s", remote_id, risposta.text[:200])
            return {}
        # Solo metriche realmente restituite: mai inventarne (sez. 21).
        return {v["name"]: v["values"][0]["value"]
                for v in risposta.json().get("data", []) if v.get("values")}

    def fetch_comments(self, remote_id):
        token = self._token()
        if not token:
            return []
        versione = self.cfg["graph_api_version"]
        risposta = requests.get(
            f"{_GRAPH}/{versione}/{remote_id}/comments",
            params={"fields": "id,text,username", "access_token": token}, timeout=60)
        if risposta.status_code != 200:
            return []
        return [{"remote_id": c["id"], "autore": c.get("username"),
                 "testo": c.get("text", "")}
                for c in risposta.json().get("data", [])]
