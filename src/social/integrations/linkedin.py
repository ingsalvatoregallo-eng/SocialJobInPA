"""
linkedin.py — adapter LinkedIn via REST API ufficiale (Community Management).

Stato reale: la Pagina aziendale JobInPA esiste e l'utente e' amministratore.
Per pubblicare servono una app su developer.linkedin.com con il prodotto
"Community Management API" (o "Share on LinkedIn" + "Advertising API" per le
organizzazioni) e un token OAuth con scope w_organization_social. La checklist
guida la configurazione; senza configurazione completa l'adapter non e' pronto
e si usa il publisher mock.

Post testuali e con immagine: POST /rest/posts (versioned API, header
LinkedIn-Version) con author = URN dell'organizzazione; per le immagini il
flusso ufficiale initializeUpload -> PUT bytes -> riferimento nel post.
"""

import logging

import requests

from social import config, db_social, security

log = logging.getLogger(__name__)

_API = "https://api.linkedin.com"


class LinkedInAdapter:
    piattaforma = "linkedin"

    def __init__(self, conn):
        self.conn = conn
        self.cfg = config.linkedin_config()
        self.account = db_social.account_per_piattaforma(conn, "linkedin")

    def health_check(self):
        cfg = self.cfg
        token = self._token()
        checklist = [
            {"voce": "App LinkedIn Developer (LINKEDIN_CLIENT_ID/SECRET)",
             "ok": bool(cfg["client_id"] and cfg["client_secret"]),
             "dettaglio": "developer.linkedin.com > Create app, associata alla Pagina JobInPA"},
            {"voce": "Prodotto Community Management API approvato",
             "ok": bool(cfg["client_id"]) and token is not None,
             "dettaglio": "Richiesta dal pannello Products dell'app; serve la verifica della Pagina"},
            {"voce": "Organization URN (LINKEDIN_ORGANIZATION_URN)",
             "ok": bool(cfg["organization_urn"]),
             "dettaglio": "urn:li:organization:<id> — l'id e' nell'URL admin della Pagina"},
            {"voce": "Token OAuth con scope w_organization_social salvato",
             "ok": token is not None,
             "dettaglio": "Login OAuth dalla dashboard; verifica privilegi admin inclusa"},
        ]
        pronto = all(v["ok"] for v in checklist)
        return {"pronto": pronto, "checklist": checklist,
                "messaggio": ("LinkedIn pronto" if pronto
                              else "LinkedIn non pronto per la pubblicazione API")}

    def _token(self):
        if self.account is None:
            return None
        riga = db_social.oauth_token_attivo(self.conn, self.account["id"])
        if riga is None:
            return None
        try:
            return security.decrypt_token(riga["token_cifrato"])
        except security.ConfigurazioneMancante as errore:
            log.warning("token LinkedIn non decifrabile: %s", errore)
            return None

    def completa_oauth(self, code):
        """Scambia il 'code' OAuth per un access token. Ritorna
        (token, expires_in_secondi). LinkedIn non emette refresh token per
        i prodotti standard: quando scade va rifatta l'autorizzazione dalla
        dashboard (Impostazioni)."""
        risposta = requests.post(
            "https://www.linkedin.com/oauth/v2/accessToken",
            data={"grant_type": "authorization_code", "code": code,
                  "redirect_uri": self.cfg["redirect_uri"],
                  "client_id": self.cfg["client_id"],
                  "client_secret": self.cfg["client_secret"]},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30)
        if not risposta.ok:
            raise RuntimeError(f"scambio code->token LinkedIn fallito: {risposta.text[:300]}")
        dati = risposta.json()
        token = dati.get("access_token")
        if not token:
            raise RuntimeError("risposta LinkedIn senza access_token")
        return token, dati.get("expires_in")

    def oauth_authorize_url(self, state):
        cfg = self.cfg
        scope = "w_organization_social%20r_organization_social%20rw_organization_admin"
        return (f"https://www.linkedin.com/oauth/v2/authorization?response_type=code"
                f"&client_id={cfg['client_id']}&redirect_uri={cfg['redirect_uri']}"
                f"&state={state}&scope={scope}")

    def _headers(self, token):
        return {"Authorization": f"Bearer {token}",
                "LinkedIn-Version": self.cfg["api_version"],
                "X-Restli-Protocol-Version": "2.0.0",
                "Content-Type": "application/json"}

    def verifica_privilegi_admin(self):
        """True se il token corrente amministra l'organizzazione configurata
        (organizationAcls con ruolo ADMINISTRATOR)."""
        token = self._token()
        if not token or not self.cfg["organization_urn"]:
            return False
        risposta = requests.get(
            f"{_API}/rest/organizationAcls?q=roleAssignee&role=ADMINISTRATOR",
            headers=self._headers(token), timeout=60)
        if risposta.status_code != 200:
            return False
        elementi = risposta.json().get("elements", [])
        return any(e.get("organization") == self.cfg["organization_urn"] for e in elementi)

    def publish(self, testo, asset_path=None):
        salute = self.health_check()
        if not salute["pronto"]:
            raise RuntimeError(salute["messaggio"])
        token = self._token()
        corpo = {
            "author": self.cfg["organization_urn"],
            "commentary": testo,
            "visibility": "PUBLIC",
            "distribution": {"feedDistribution": "MAIN_FEED",
                             "targetEntities": [], "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        if asset_path:
            corpo["content"] = {"media": {"id": self._upload_image(token, asset_path)}}
        risposta = requests.post(f"{_API}/rest/posts", json=corpo,
                                 headers=self._headers(token), timeout=60)
        risposta.raise_for_status()
        from social.integrations.base import PublishResult
        post_urn = risposta.headers.get("x-restli-id", "")
        return PublishResult(
            remote_id=post_urn,
            remote_url=f"https://www.linkedin.com/feed/update/{post_urn}/")

    def _upload_image(self, token, asset_path):
        init = requests.post(
            f"{_API}/rest/images?action=initializeUpload",
            json={"initializeUploadRequest": {"owner": self.cfg["organization_urn"]}},
            headers=self._headers(token), timeout=60)
        init.raise_for_status()
        valore = init.json()["value"]
        with open(asset_path, "rb") as f:
            put = requests.put(valore["uploadUrl"], data=f.read(),
                               headers={"Authorization": f"Bearer {token}"}, timeout=120)
        put.raise_for_status()
        return valore["image"]

    def fetch_metrics(self, remote_id):
        token = self._token()
        if not token:
            return {}
        risposta = requests.get(
            f"{_API}/rest/organizationalEntityShareStatistics",
            params={"q": "organizationalEntity",
                    "organizationalEntity": self.cfg["organization_urn"],
                    "shares[0]": remote_id},
            headers=self._headers(token), timeout=60)
        if risposta.status_code != 200:
            log.warning("statistiche LinkedIn %s: %s", remote_id, risposta.text[:200])
            return {}
        elementi = risposta.json().get("elements", [])
        if not elementi:
            return {}
        stats = elementi[0].get("totalShareStatistics", {})
        return {k: v for k, v in stats.items() if isinstance(v, (int, float))}

    def fetch_comments(self, remote_id):
        token = self._token()
        if not token:
            return []
        risposta = requests.get(
            f"{_API}/rest/socialActions/{remote_id}/comments",
            headers=self._headers(token), timeout=60)
        if risposta.status_code != 200:
            return []
        return [{"remote_id": c.get("id"),
                 "autore": c.get("actor"),
                 "testo": (c.get("message") or {}).get("text", "")}
                for c in risposta.json().get("elements", [])]
