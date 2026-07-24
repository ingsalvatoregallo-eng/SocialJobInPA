"""
auth.py — hashing password e token di sessione di SocialJobInPA.

Portato da JobInPA (stesso formato di hash e di token, cosi' chi conosce
quel codice ritrova le stesse scelte): PBKDF2-HMAC-SHA256 con salt per
utente e hash autodescrittivo; token firmati HMAC-SHA256 con scadenza,
niente JWT/PyJWT (li emettiamo e verifichiamo solo noi).

Il secret e' SOCIAL_AUTH_SECRET (con fallback su INPA_AUTH_SECRET per
comodita' in sviluppo, dove i due progetti convivono sulla stessa
macchina). Nessun default silenzioso: senza secret, errore chiaro.
"""

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

ITERAZIONI_PBKDF2 = 600_000
DURATA_TOKEN_SECONDI = 30 * 24 * 3600  # 30 giorni

_FILE_ENV = Path(__file__).resolve().parent.parent / ".env"


def _carica_env_file(percorso=_FILE_ENV):
    """KEY=VALORE da .env se esiste, senza sovrascrivere l'ambiente."""
    if not percorso.exists():
        return
    try:
        for riga in percorso.read_text(encoding="utf-8").splitlines():
            riga = riga.strip()
            if not riga or riga.startswith("#") or "=" not in riga:
                continue
            chiave, _, valore = riga.partition("=")
            os.environ.setdefault(chiave.strip(), valore.strip())
    except OSError:
        pass


_carica_env_file()


def _chiave_segreta():
    chiave = os.environ.get("SOCIAL_AUTH_SECRET") or os.environ.get("INPA_AUTH_SECRET")
    if not chiave:
        raise RuntimeError(
            "SOCIAL_AUTH_SECRET non impostata: obbligatoria per firmare i token "
            "di sessione. Vedi .env.example.")
    return chiave.encode("utf-8")


def hash_password(password):
    sale = os.urandom(16)
    derivato = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), sale, ITERAZIONI_PBKDF2)
    return "$".join([
        "pbkdf2_sha256",
        str(ITERAZIONI_PBKDF2),
        base64.b64encode(sale).decode("ascii"),
        base64.b64encode(derivato).decode("ascii"),
    ])


def verifica_password(password, hash_salvato):
    """True se la password corrisponde. Mai eccezioni su input malformato."""
    try:
        algoritmo, iterazioni, sale_b64, derivato_b64 = hash_salvato.split("$")
        if algoritmo != "pbkdf2_sha256":
            return False
        sale = base64.b64decode(sale_b64)
        derivato_atteso = base64.b64decode(derivato_b64)
    except (ValueError, AttributeError, TypeError):
        return False
    derivato = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), sale, int(iterazioni))
    return hmac.compare_digest(derivato, derivato_atteso)


def _b64_senza_padding(dati):
    return base64.urlsafe_b64encode(dati).rstrip(b"=")


def _decodifica_b64(dati):
    padding = b"=" * (-len(dati) % 4)
    return base64.urlsafe_b64decode(dati + padding)


def crea_token(payload, durata_secondi=DURATA_TOKEN_SECONDI):
    adesso = int(time.time())
    corpo = {**payload, "iat": adesso, "exp": adesso + durata_secondi}
    corpo_b64 = _b64_senza_padding(json.dumps(corpo, separators=(",", ":")).encode("utf-8"))
    firma = hmac.new(_chiave_segreta(), corpo_b64, hashlib.sha256).digest()
    return (corpo_b64 + b"." + _b64_senza_padding(firma)).decode("ascii")


def verifica_token(token):
    """Payload (dict) se valido e non scaduto, None altrimenti."""
    try:
        corpo_b64, firma_b64 = token.encode("ascii").split(b".")
        firma_ricevuta = _decodifica_b64(firma_b64)
    except (ValueError, AttributeError):
        return None
    firma_attesa = hmac.new(_chiave_segreta(), corpo_b64, hashlib.sha256).digest()
    if not hmac.compare_digest(firma_attesa, firma_ricevuta):
        return None
    try:
        corpo = json.loads(_decodifica_b64(corpo_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    if corpo.get("exp", 0) < time.time():
        return None
    return corpo


def payload_valido_per_sessione(payload):
    """Esclude i token firmati che non sono sessioni (es. stati OAuth)."""
    if not isinstance(payload, dict):
        return False
    return (payload.get("scope") in {None, "session"}
            and payload.get("scopo") is None)
