"""
security.py — primitive di sicurezza del modulo social.

- Cifratura applicativa dei token OAuth (Fernet, chiave ENCRYPTION_KEY):
  i token non toccano mai il DB in chiaro; nei log/audit compare solo la
  maschera (primi 4 + ultimi 4 caratteri).
- Guard anti-SSRF per il Research Agent: solo http/https, niente host locali,
  IP privati o endpoint di metadata cloud; whitelist di dominio applicata a
  monte (vedi db_social.source_domain_allowed).
- Sanitizzazione del contenuto delle fonti: via script/style/commenti e
  contenuti nascosti, testo piano con limite dimensionale — il testo delle
  fonti e' SEMPRE trattato come dato non fidato e mai come istruzione
  (vedi llm.py: va nei blocchi <fonte>, mai nel system prompt).
- Token CSRF per i form della dashboard (HMAC sul token di sessione).
"""

import hashlib
import hmac
import html
import ipaddress
import re
import socket
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken

from social import config


class ConfigurazioneMancante(RuntimeError):
    pass


def _fernet():
    chiave = config.encryption_key()
    if not chiave:
        raise ConfigurazioneMancante(
            "ENCRYPTION_KEY non impostata: impossibile cifrare/decifrare i token. "
            "Generane una con: python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
    return Fernet(chiave.encode("ascii") if isinstance(chiave, str) else chiave)


def encrypt_token(token_chiaro):
    return _fernet().encrypt(token_chiaro.encode("utf-8")).decode("ascii")


def decrypt_token(token_cifrato):
    try:
        return _fernet().decrypt(token_cifrato.encode("ascii")).decode("utf-8")
    except InvalidToken:
        raise ConfigurazioneMancante(
            "Token cifrato con una ENCRYPTION_KEY diversa da quella attuale: "
            "riautorizzare l'account social (vedi docs/security.md, rotazione chiavi)"
        )


def mask_secret(valore):
    """'EAAG1234...wxyz' -> 'EAAG…wxyz'. Per log e audit, mai il valore intero."""
    if not valore:
        return ""
    if len(valore) <= 8:
        return "…"
    return f"{valore[:4]}…{valore[-4:]}"


# --- SSRF guard --------------------------------------------------------------

_METADATA_HOSTS = {"169.254.169.254", "metadata.google.internal", "metadata"}


def url_fetch_consentito(url):
    """(ok, motivo). Applica: solo http/https, no credenziali nell'URL, no
    host locali/privati/metadata. La whitelist di dominio e' un controllo
    separato e aggiuntivo (source_domains nel DB)."""
    try:
        parti = urlparse(url)
    except ValueError:
        return False, "URL non interpretabile"
    if parti.scheme not in {"http", "https"}:
        return False, f"schema non consentito: {parti.scheme or '(vuoto)'}"
    if parti.username or parti.password:
        return False, "credenziali nell'URL non consentite"
    host = (parti.hostname or "").strip(".").lower()
    if not host:
        return False, "host mancante"
    if host in _METADATA_HOSTS or host == "localhost" or host.endswith(".local"):
        return False, f"host bloccato: {host}"
    # Risolve e controlla OGNI indirizzo: un DNS che risponde con un IP privato
    # (rebinding) non deve superare il controllo.
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False, f"host non risolvibile: {host}"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False, f"indirizzo non pubblico: {ip}"
    return True, "ok"


# --- Sanitizzazione contenuti fonte ------------------------------------------

MAX_SOURCE_CHARS = 60_000

_RE_RIMOZIONE = re.compile(
    r"<(script|style|noscript|template|iframe|object|embed)\b.*?</\1\s*>|<!--.*?-->",
    re.IGNORECASE | re.DOTALL,
)
# Elementi esplicitamente nascosti: il testo invisibile e' il veicolo classico
# della prompt injection nelle pagine web.
_RE_NASCOSTI = re.compile(
    r"<[^>]+(?:hidden|display\s*:\s*none|visibility\s*:\s*hidden)[^>]*>.*?</[^>]+>",
    re.IGNORECASE | re.DOTALL,
)
_RE_TAG = re.compile(r"<[^>]+>")
_RE_SPAZI = re.compile(r"[ \t\r\f\v]+")


def sanitizza_html(html_grezzo, max_chars=MAX_SOURCE_CHARS):
    """HTML non fidato -> testo piano, senza script/nascosti, con limite duro."""
    if not html_grezzo:
        return ""
    testo = html_grezzo[: max_chars * 4]  # limite anche sull'input da parsare
    testo = _RE_RIMOZIONE.sub(" ", testo)
    testo = _RE_NASCOSTI.sub(" ", testo)
    testo = _RE_TAG.sub(" ", testo)
    testo = html.unescape(testo)
    testo = _RE_SPAZI.sub(" ", testo)
    righe = [r.strip() for r in testo.splitlines()]
    testo = "\n".join(r for r in righe if r)
    return testo[:max_chars]


# --- CSRF (dashboard) --------------------------------------------------------

def csrf_token(session_token):
    """Derivato dal token di sessione: non serve stato aggiuntivo lato server."""
    return hmac.new(b"jobinpa-social-csrf", session_token.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def csrf_valido(session_token, ricevuto):
    if not session_token or not ricevuto:
        return False
    return hmac.compare_digest(csrf_token(session_token), ricevuto)
