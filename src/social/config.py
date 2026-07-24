"""
config.py — configurazione del modulo social, tutta da variabili d'ambiente.

Stesso principio di notifiche.py/auth.py: un file .env locale opzionale viene
caricato senza sovrascrivere l'ambiente gia' impostato (systemd/Docker vincono
sempre). Nessun segreto nel codice.

Modalita' operative (SOCIAL_MODE):
    mock        nessuna chiamata esterna (test/sviluppo offline)
    sandbox     AI reale se configurata, publisher social SEMPRE mock  [default]
    production  publisher reali, ma solo se l'intera catena di sicurezza
                lo consente (vedi publishing.can_publish)
"""

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_FILE_ENV = _ROOT / ".env"

MODES = ("mock", "sandbox", "production")


def _carica_env_file(percorso=_FILE_ENV):
    """KEY=VALORE da .env se esiste, senza sovrascrivere l'ambiente."""
    try:
        righe = Path(percorso).read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for riga in righe:
        riga = riga.strip()
        if not riga or riga.startswith("#") or "=" not in riga:
            continue
        chiave, _, valore = riga.partition("=")
        chiave, valore = chiave.strip(), valore.strip().strip('"').strip("'")
        if chiave and chiave not in os.environ:
            os.environ[chiave] = valore


_carica_env_file()


def _bool(nome, default=False):
    valore = os.environ.get(nome)
    if valore is None:
        return default
    return valore.strip().lower() in {"1", "true", "yes", "on"}


def _float(nome, default):
    try:
        return float(os.environ.get(nome, "") or default)
    except ValueError:
        return default


def _int(nome, default):
    try:
        return int(os.environ.get(nome, "") or default)
    except ValueError:
        return default


def mode():
    valore = os.environ.get("SOCIAL_MODE", "sandbox").strip().lower()
    return valore if valore in MODES else "sandbox"


def get(nome, default=None):
    return os.environ.get(nome, default)


# --- App ---------------------------------------------------------------------

def base_url():
    return os.environ.get("APP_BASE_URL", "http://localhost:8000").rstrip("/")


def asset_storage_path():
    percorso = os.environ.get("ASSET_STORAGE_PATH") or str(_ROOT / "assets" / "generated")
    return Path(percorso)


def brand_asset_path():
    return _ROOT / "assets" / "brand"


def default_timezone():
    return os.environ.get("DEFAULT_TIMEZONE", "Europe/Rome")


def encryption_key():
    """Chiave Fernet per cifrare i token OAuth. Obbligatoria per salvarli."""
    return os.environ.get("ENCRYPTION_KEY") or os.environ.get("INPA_ENCRYPTION_KEY")


# --- Kill switch / pubblicazione ---------------------------------------------

def publishing_enabled_env():
    """Livello 1 del kill switch: la variabile d'ambiente. Default False."""
    return _bool("GLOBAL_PUBLISHING_ENABLED", False)


# --- Anthropic ---------------------------------------------------------------

def anthropic_api_key():
    # ANTHROPIC_API_KEY e' anche la variabile letta dal SDK; il repo esistente
    # (ai_classifier) usa la chiave in configurazione_api ma il modulo social
    # tiene budget e contabilita' separati, quindi chiave dedicata via env.
    return os.environ.get("ANTHROPIC_API_KEY")


def anthropic_model():
    return os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")


def anthropic_max_tokens():
    return _int("ANTHROPIC_MAX_TOKENS", 2048)


def anthropic_monthly_budget_eur():
    return _float("ANTHROPIC_MONTHLY_BUDGET_EUR", 20.0)


def anthropic_daily_budget_eur():
    return _float("ANTHROPIC_DAILY_BUDGET_EUR", 3.0)


# --- OpenAI Images -----------------------------------------------------------

def openai_api_key():
    return os.environ.get("OPENAI_API_KEY")


def openai_image_model():
    return os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")


def ai_images_enabled():
    return _bool("ENABLE_AI_IMAGES", False)


def openai_image_monthly_budget_eur():
    return _float("OPENAI_IMAGE_MONTHLY_BUDGET_EUR", 5.0)


# --- SMTP ----------------------------------------------------------------

def smtp_config():
    return {
        "host": os.environ.get("SMTP_HOST", ""),
        "port": _int("SMTP_PORT", 587),
        "username": os.environ.get("SMTP_USERNAME", ""),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "from_email": os.environ.get("SMTP_FROM_EMAIL") or os.environ.get("SMTP_USERNAME", ""),
        "from_name": os.environ.get("SMTP_FROM_NAME", "JobInPA Social AI"),
        "use_tls": _bool("SMTP_USE_TLS", True),
        "use_ssl": _bool("SMTP_USE_SSL", False),
    }


# --- JobInPA (API private, sez. "due progetti collegati") --------------------

def jobinpa_api_url():
    return os.environ.get("JOBINPA_API_URL", "")


def jobinpa_api_key():
    return os.environ.get("JOBINPA_API_KEY", "")


# --- Meta / Instagram --------------------------------------------------------

def meta_config():
    return {
        "app_id": os.environ.get("META_APP_ID", ""),
        "app_secret": os.environ.get("META_APP_SECRET", ""),
        "redirect_uri": os.environ.get("META_REDIRECT_URI", ""),
        "graph_api_version": os.environ.get("META_GRAPH_API_VERSION", "v21.0"),
        "instagram_account_id": os.environ.get("INSTAGRAM_ACCOUNT_ID", ""),
        "facebook_page_id": os.environ.get("FACEBOOK_PAGE_ID", ""),
    }


# --- LinkedIn ----------------------------------------------------------------

def linkedin_config():
    return {
        "client_id": os.environ.get("LINKEDIN_CLIENT_ID", ""),
        "client_secret": os.environ.get("LINKEDIN_CLIENT_SECRET", ""),
        "redirect_uri": os.environ.get("LINKEDIN_REDIRECT_URI", ""),
        "organization_urn": os.environ.get("LINKEDIN_ORGANIZATION_URN", ""),
        "api_version": os.environ.get("LINKEDIN_API_VERSION", "202411"),
    }


# --- Finestre editoriali predefinite (configurabili poi da system_settings) --

DEFAULT_POSTING_WINDOWS = {
    "linkedin": [["08:00", "10:00"], ["12:00", "14:00"], ["17:00", "19:00"]],
    "instagram": [["08:00", "10:00"], ["12:00", "14:00"], ["18:00", "21:00"]],
}
