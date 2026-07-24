"""Entrypoint del worker: python -m social.worker_main (da src/)."""
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from social import db_social, scheduler  # noqa: E402

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

if __name__ == "__main__":
    conn = db_social.connect()
    db_social.init_social_db(conn)
    scheduler.ciclo_worker(conn)
