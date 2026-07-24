# Troubleshooting — Modulo Social AI

## L'app parte ma /social non esiste

Il montaggio dei router social e' protetto da try/except: guarda i log di
avvio di `app` — c'e' un traceback "router social non montati" o "init
modulo social fallita" con la causa esatta.

## "ENCRYPTION_KEY non impostata"

Serve per cifrare/decifrare i token OAuth. `.\scripts\setup.ps1` la genera;
a mano: `python -c "from cryptography.fernet import Fernet;
print(Fernet.generate_key().decode())"` → in `.env`.

## "Token cifrato con una ENCRYPTION_KEY diversa"

La chiave e' cambiata dopo il salvataggio dei token: riautorizza gli
account social (docs/security.md, rotazione chiavi).

## Le email non arrivano

1. dashboard → Log e audit → Email: esito `saltata` = SMTP non configurato,
   `fallita` = errore col dettaglio;
2. in dev: le email vanno a Mailpit (<http://localhost:8025>), non a
   caselle reali — e' voluto;
3. controlla `SMTP_USE_TLS`/`SMTP_USE_SSL` rispetto alla porta (587 =
   STARTTLS, 465 = SSL diretto).

## La pipeline resta in RESEARCH_FAILED

Guarda `errore` nel dettaglio contenuto e i log del worker. Cause tipiche:
budget Anthropic esaurito (`BudgetEsaurito`: alza il budget o attendi il
nuovo mese — i job restano in coda), circuit breaker aperto (attendi 2
minuti), chiave API mancante in modalita' sandbox (senza chiave sei in mock).

## "database is locked"

Raro (WAL + timeout 30 s), ma possibile con molti processi. Verifica di non
avere un terzo processo batch che scrive per minuti; i job falliti per lock
fanno comunque retry con backoff.

## Il worker non esegue i job

- il job e' `pending` con `esegui_at` futuro? E' solo programmato piu' tardi;
- e' `running` da piu' di 15 minuti? Il lock verra' reclamato da solo
  (recovery); se succede spesso, il job impiega troppo: guarda i log;
- e' `dead`? Vedi docs/operations.md.

## Pubblicazione bloccata: da dove ricomincio?

Il motivo esatto e' in audit (`pubblicazione_bloccata`) e negli esiti della
chiamata. La catena e': GLOBAL_PUBLISHING_ENABLED → kill switch DB →
account verificato/abilitato → approvazione → classe di rischio.

## Reset completo dell'ambiente demo

```powershell
.\scripts\stop.ps1
Remove-Item data\inpa.db      # SOLO se il DB contiene esclusivamente dati di sviluppo!
.\scripts\setup.ps1
```
