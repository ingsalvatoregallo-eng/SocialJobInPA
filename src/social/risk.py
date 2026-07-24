"""
risk.py — classificazione del rischio (sez. 5 del prompt master).

Due livelli complementari:
1. regole deterministiche (questo modulo): pattern che DEVONO forzare rosso o
   giallo a prescindere da cosa dica il modello — il giudizio AI non puo'
   declassare un rischio rilevato dalle regole, solo aggravarlo;
2. valutazione AI (Quality & Risk Agent, vedi agents.py) che produce punteggi
   e propone una classe: la classe finale e' la peggiore delle due.

Decisioni: verde -> auto_publish, giallo -> human_approval, rosso -> blocked.
"""

import re

CLASSI = ("verde", "giallo", "rosso")
DECISIONE_PER_CLASSE = {
    "verde": "auto_publish",
    "giallo": "human_approval",
    "rosso": "blocked",
}

# Pattern -> classe rossa: contenuti mai pubblicabili senza intervento.
_PATTERN_ROSSO = (
    (r"\b(garantiamo|garantisce|assicuriamo) (il|la|l')?\s*(successo|vincita|assunzione)", "promessa di successo"),
    (r"\bvincerai\b|\bsarai assunt", "promessa di successo"),
    (r"\b(codice fiscale|carta d'identita|numero di passaporto)\b", "possibile dato personale"),
    (r"\b(partito|elettoral|propaganda)\w*\b", "contenuto politico"),
    (r"\b(incompetent|corrott|truffa|scandalo)\w*\b", "possibile accusa verso enti"),
    (r"ignora (le|tutte le) istruzioni|disregard (all )?(previous |prior )?instructions", "prompt injection"),
)

# Pattern -> almeno giallo: serve revisione umana.
_PATTERN_GIALLO = (
    (r"\b(decreto|riforma|legge|normativ|circolare)\w*\b", "aggiornamento/interpretazione normativa"),
    (r"\b(requisit\w+ (di|per)|interpretazion)\w*\b", "interpretazione dei requisiti"),
    (r"\b\d+\s*%|\bstatistic\w+\b|\bmedia (nazionale|dei)\b", "statistiche o confronti"),
    (r"\b(sconto|promo(zione)?|offerta|abbonati|acquista)\b", "contenuto commerciale"),
    (r"\bmiglior\w* (di|rispetto a)\b", "confronto"),
)


def classifica_regole(testo, *, fonti_verificate=True, fonti_in_conflitto=False,
                      e_risposta_commento=False):
    """(classe, motivi) dal solo livello deterministico."""
    testo = testo or ""
    motivi = []
    classe = "verde"
    for pattern, motivo in _PATTERN_ROSSO:
        if re.search(pattern, testo, re.IGNORECASE):
            motivi.append(f"rosso: {motivo}")
            classe = "rosso"
    if fonti_in_conflitto:
        motivi.append("rosso: dati discordanti fra fonti")
        classe = "rosso"
    if classe != "rosso":
        for pattern, motivo in _PATTERN_GIALLO:
            if re.search(pattern, testo, re.IGNORECASE):
                motivi.append(f"giallo: {motivo}")
                classe = "giallo"
        if not fonti_verificate:
            motivi.append("giallo: fonti non tutte verificate")
            classe = "giallo"
        if e_risposta_commento:
            motivi.append("giallo: le risposte ai commenti richiedono sempre approvazione")
            classe = "giallo"
    return classe, motivi


def peggiore(classe_a, classe_b):
    """La classe finale e' sempre la piu' severa fra regole e giudizio AI."""
    ordine = {c: i for i, c in enumerate(CLASSI)}
    if classe_a not in ordine:
        classe_a = "rosso"   # classe sconosciuta dal modello -> mai fidarsi
    if classe_b not in ordine:
        classe_b = "rosso"
    return classe_a if ordine[classe_a] >= ordine[classe_b] else classe_b


def decisione(classe):
    return DECISIONE_PER_CLASSE.get(classe, "blocked")
