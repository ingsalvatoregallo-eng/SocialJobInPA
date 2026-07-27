"""
asset_storage.py — carica le immagini generate su uno storage oggetti
pubblico S3-compatibile (Cloudflare R2), cosi' Instagram (che a differenza
di LinkedIn accetta solo un image_url raggiungibile da Internet, mai i
byte direttamente) puo' scaricarle. Nessuna esposizione dell'app: resta
privata, solo il singolo file immagine diventa pubblico.

Difensivo come le altre integrazioni opzionali del progetto (vedi
smtp_config/jobinpa_client): se R2 non e' configurato, carica_pubblico()
ritorna None invece di sollevare — il resto della pipeline prosegue
usando il percorso locale (va bene per LinkedIn/mock, blocca solo la
pubblicazione reale su Instagram, gia' segnalata dalla checklist).
"""

import logging
import mimetypes
import os
import uuid

from social import config

log = logging.getLogger(__name__)


def carica_pubblico(percorso_locale):
    """Carica il file su R2 e ritorna l'URL pubblico, o None se R2 non e'
    configurato o l'upload fallisce (mai un'eccezione che rompe la
    pipeline: la generazione dell'immagine e' gia' avvenuta con successo).
    percorso_locale puo' essere str o Path (os.path.basename gestisce
    entrambi, e sia '/' che '\\' come separatore)."""
    if not config.r2_configurato():
        return None
    cfg = config.r2_config()
    percorso_locale = str(percorso_locale)
    try:
        import boto3
        client = boto3.client(
            "s3",
            endpoint_url=f"https://{cfg['account_id']}.r2.cloudflarestorage.com",
            aws_access_key_id=cfg["access_key_id"],
            aws_secret_access_key=cfg["secret_access_key"],
            region_name="auto")
        chiave = f"{uuid.uuid4().hex}-{os.path.basename(percorso_locale)}"
        content_type = mimetypes.guess_type(percorso_locale)[0] or "application/octet-stream"
        with open(percorso_locale, "rb") as f:
            client.put_object(Bucket=cfg["bucket"], Key=chiave, Body=f,
                              ContentType=content_type)
        return f"{cfg['public_base_url'].rstrip('/')}/{chiave}"
    except Exception as errore:
        log.warning("upload R2 fallito per %s: %s", percorso_locale, errore)
        return None
