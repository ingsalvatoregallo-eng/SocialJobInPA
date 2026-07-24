"""
deps.py — dependency FastAPI condivise (connessione DB per richiesta,
utente autenticato via Bearer). Stesso ruolo dell'omonimo modulo di
JobInPA, ma sul database e sugli utenti propri di SocialJobInPA.
"""

from typing import Optional

from fastapi import Depends, Header, HTTPException

import auth
from social import db_social


def ottieni_conn():
    """Una connessione per richiesta, chiusa sempre alla fine."""
    conn = db_social.connect()
    try:
        yield conn
    finally:
        conn.close()


def utente_corrente(authorization: Optional[str] = Header(None),
                    conn=Depends(ottieni_conn)):
    """Legge 'Authorization: Bearer <token>', verifica, carica l'utente."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Autenticazione richiesta")
    payload = auth.verifica_token(authorization[len("Bearer "):])
    if payload is None or not auth.payload_valido_per_sessione(payload):
        raise HTTPException(status_code=401, detail="Token non valido o scaduto")
    utente = db_social.utente_per_id(conn, payload.get("utente_id"))
    if utente is None or utente["stato"] != "attivo":
        raise HTTPException(status_code=401, detail="Utente non valido")
    return utente
