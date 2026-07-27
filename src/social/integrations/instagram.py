"""
instagram.py — adapter Instagram via "Instagram API with Instagram Login"
(solo API ufficiali).

Flusso nativo Instagram (sostituisce il vecchio "Facebook Login for
Business" legato a una Pagina): login OAuth direttamente su instagram.com,
scambio del code su api.instagram.com, pubblicazione su graph.instagram.com.
L'Instagram Business Account ID (identificativo) non e' piu' una variabile
di ambiente: si ottiene dal token stesso subito dopo l'autorizzazione
(completa_oauth) e resta salvato su social_accounts.identificativo.

Pubblicazione (quando configurato): flusso ufficiale in due passi del
Content Publishing API — POST /{ig-user-id}/media (container con image_url +
caption) e POST /{ig-user-id}/media_publish. Richiede che l'immagine sia
raggiungibile via URL pubblico: per questo ogni immagine generata viene
caricata anche su Cloudflare R2 (vedi asset_storage.py e
social_media_assets.url_pubblico), invece di esporre l'intera app dietro
un dominio pubblico — resta privata, pubblico e' solo il singolo file.

Post carosello (2-10 immagini, es. una per bando trovato dal Research
Agent): flusso a tre passi, distinto da quello a immagine singola — un
container 'figlio' per immagine (is_carousel_item=true, senza caption),
poi un container padre (media_type=CAROUSEL, children, con la caption
unica), infine la pubblicazione. Stesso requisito di URL pubblico per
ciascuna immagine figlio.
"""

import logging
import time

import requests

from social import config, db_social, security
from social.images import MASSIMO_IMMAGINI_CAROSELLO
from social.integrations.base import PublishResult, asset_a_lista

log = logging.getLogger(__name__)

_AUTORIZZA = "https://api.instagram.com/oauth/authorize"
_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
_GRAPH = "https://graph.instagram.com"
_SCOPE = "instagram_business_basic,instagram_business_content_publish"

# Dopo la creazione, un container puo' restare IN_PROGRESS per qualche
# secondo (Meta scarica e valida l'immagine): pubblicarlo subito puo'
# fallire con 400 in modo intermittente (osservato su un contenuto reale,
# riprodotto manualmente subito dopo con successo perche' nel frattempo
# il container era gia' FINISHED).
_POLL_INTERVALLO_SECONDI = 2
_POLL_TIMEOUT_SECONDI = 30


def _attendi_container_pronto(container_id, token, versione):
    scadenza = time.monotonic() + _POLL_TIMEOUT_SECONDI
    while True:
        risposta = requests.get(
            f"{_GRAPH}/{versione}/{container_id}",
            params={"fields": "status_code", "access_token": token}, timeout=30)
        risposta.raise_for_status()
        stato = risposta.json().get("status_code")
        if stato == "FINISHED":
            return
        if stato == "ERROR":
            raise RuntimeError(
                f"elaborazione immagine Instagram fallita (container {container_id})")
        if time.monotonic() >= scadenza:
            raise RuntimeError(
                f"timeout in attesa che Instagram elabori il container {container_id} "
                f"(ultimo stato: {stato})")
        time.sleep(_POLL_INTERVALLO_SECONDI)



class InstagramAdapter:
    piattaforma = "instagram"

    def __init__(self, conn):
        self.conn = conn
        self.cfg = config.instagram_config()
        self.account = db_social.account_per_piattaforma(conn, "instagram")

    # --- Checklist / diagnostica --------------------------------------------

    def health_check(self):
        cfg = self.cfg
        token = self._token()
        checklist = [
            {"voce": "App Instagram creata (INSTAGRAM_APP_ID/INSTAGRAM_APP_SECRET)",
             "ok": bool(cfg["app_id"] and cfg["app_secret"]),
             "dettaglio": "developers.facebook.com > la tua app > Instagram > "
                          "Configura Instagram Business Login — vedi docs/meta-instagram-setup.md"},
            {"voce": "Token OAuth autorizzato e salvato",
             "ok": token is not None,
             "dettaglio": "Login OAuth con scope instagram_business_basic, "
                          "instagram_business_content_publish"},
            {"voce": "Instagram Business Account ID",
             "ok": bool(self.account and self.account["identificativo"]),
             "dettaglio": "Ottenuto automaticamente dal token dopo l'autorizzazione (GET /me)"},
            {"voce": "Storage pubblico immagini (Cloudflare R2) configurato",
             "ok": config.r2_configurato(),
             "dettaglio": "Il container /media accetta solo image_url pubblici: "
                          "R2_ACCOUNT_ID/ACCESS_KEY/SECRET/BUCKET/PUBLIC_BASE_URL "
                          "vanno tutti impostati (vedi docs/meta-instagram-setup.md)"},
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
        """Scambia il 'code' OAuth di Instagram Business Login per un token
        utente, poi delega a _finalizza_token per il resto (long-lived +
        identita'). Solleva RuntimeError con un messaggio chiaro ad ogni
        passaggio."""
        cfg = self.cfg
        # Instagram appende "#_" al redirect (documentazione ufficiale):
        # in un vero redirect HTTP il browser lo scarta da solo (e' un
        # fragment, mai inviato al server), ma lo togliamo comunque per
        # sicurezza se il code arrivasse gia' con quella coda.
        code = code.split("#")[0]

        # code -> short-lived user token (~1h). A differenza del vecchio
        # flusso Facebook, lo scambio e' un POST form-encoded, non un GET.
        risposta = requests.post(
            _TOKEN_URL,
            data={"client_id": cfg["app_id"], "client_secret": cfg["app_secret"],
                  "grant_type": "authorization_code", "redirect_uri": cfg["redirect_uri"],
                  "code": code},
            timeout=30)
        if not risposta.ok:
            raise RuntimeError(f"scambio code->token Instagram fallito: {risposta.text[:300]}")
        token = risposta.json().get("access_token")
        if not token:
            raise RuntimeError("risposta Instagram senza access_token")
        return self._finalizza_token(token)

    def completa_con_token_manuale(self, token):
        """Percorso alternativo al redirect OAuth completo: un token gia'
        ottenuto altrove (es. il bottone "Genera token" della dashboard
        Meta per un account tester, quando il redirect OAuth non funziona —
        problema noto lato Meta, non del nostro codice) viene comunque
        scambiato per uno long-lived e usato per recuperare l'identita',
        esattamente come farebbe completa_oauth dopo lo scambio del code."""
        return self._finalizza_token(token)

    def _finalizza_token(self, token):
        """token -> long-lived (~60 giorni, se il provider lo concede) ->
        Instagram-scoped user id, salvato su social_accounts.identificativo
        (sostituisce il vecchio Page Access Token + FACEBOOK_PAGE_ID: con
        questo flusso si pubblica direttamente sull'account)."""
        cfg = self.cfg
        risposta = requests.get(
            f"{_GRAPH}/access_token",
            params={"grant_type": "ig_exchange_token", "client_secret": cfg["app_secret"],
                    "access_token": token}, timeout=30)
        if risposta.ok and risposta.json().get("access_token"):
            token = risposta.json()["access_token"]

        risposta = requests.get(
            f"{_GRAPH}/{cfg['graph_api_version']}/me",
            params={"fields": "user_id", "access_token": token}, timeout=30)
        if not risposta.ok or not risposta.json().get("user_id"):
            raise RuntimeError(f"impossibile ottenere l'Instagram User ID: {risposta.text[:300]}")
        ig_user_id = str(risposta.json()["user_id"])
        if self.account is not None:
            db_social.aggiorna_account(self.conn, self.account["id"], identificativo=ig_user_id)
            self.account = db_social.account_per_piattaforma(self.conn, "instagram")
        return token

    def oauth_authorize_url(self, state):
        """URL del consenso Instagram Business Login (il flusso si completa
        dalla dashboard). Parametri URL-encoded: redirect_uri/scope inseriti
        senza encoding potevano non corrispondere byte-per-byte a quanto
        registrato lato Meta."""
        from urllib.parse import urlencode
        cfg = self.cfg
        query = urlencode({
            "client_id": cfg["app_id"], "redirect_uri": cfg["redirect_uri"],
            "response_type": "code", "scope": _SCOPE, "state": state})
        return f"{_AUTORIZZA}?{query}"

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
        ig_id = self.account["identificativo"]
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
        _attendi_container_pronto(container_id, token, versione)
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
        _attendi_container_pronto(container_id, token, versione)
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
