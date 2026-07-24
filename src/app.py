"""
app.py — applicazione FastAPI di SocialJobInPA.

Progetto separato da JobInPA (che gira sulla VM e resta l'unica fonte dei
bandi, letti via API private: vedi social/jobinpa_client.py). Qui vivono
dashboard (/social), API (/api/v1/social) e stato di salute.

Avvio:
    uvicorn src.app:app --port 8100
"""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from social import db_social  # noqa: E402
from social.api import router as api_router  # noqa: E402
from social.web import router as web_router  # noqa: E402

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db_social.connect()
    try:
        db_social.init_social_db(conn)
    finally:
        conn.close()
    yield


app = FastAPI(title="SocialJobInPA", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)
app.include_router(web_router)


@app.get("/health")
def health():
    return {"stato": "ok", "servizio": "socialjobinpa"}


@app.get("/")
def root():
    return RedirectResponse("/social/")
