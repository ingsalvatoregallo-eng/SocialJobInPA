"""Fixture comuni dei test: DB temporaneo proprio di SocialJobInPA (nessuna
dipendenza da JobInPA: e' un progetto separato, si parla solo via API)."""

import os
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

os.environ.setdefault("SOCIAL_AUTH_SECRET", "test-auth-secret-social")
os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ["SOCIAL_MODE"] = "mock"
os.environ.pop("GLOBAL_PUBLISHING_ENABLED", None)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("JOBINPA_API_URL", None)
os.environ.pop("JOBINPA_API_KEY", None)
# Forzato (non pop): config._carica_env_file() gira all'IMPORT di
# social.config (piu' sotto) e ricarica dal vero .env qualunque chiave non
# gia' presente in os.environ — un pop qui verrebbe subito ripristinato dal
# file. Il vero APP_BASE_URL e' https:// (richiesto da Instagram Business
# Login): con https i cookie di sessione diventano Secure e il TestClient
# (parla in http semplice con "testserver") non li rimanderebbe indietro,
# rompendo il login nei test — servono sempre sullo scenario http.
os.environ["APP_BASE_URL"] = "http://localhost:8000"

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from social import db_social  # noqa: E402


@pytest.fixture
def tmp_db_path(tmp_path):
    return str(tmp_path / "social-test.db")


@pytest.fixture
def conn(tmp_db_path, tmp_path):
    # Gli asset generati nei test finiscono in una cartella temporanea.
    os.environ["ASSET_STORAGE_PATH"] = str(tmp_path / "assets")
    connessione = db_social.connect(tmp_db_path)
    db_social.init_social_db(connessione)
    yield connessione
    connessione.close()
