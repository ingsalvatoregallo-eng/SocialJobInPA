# SMTP — email di approvazione

Le richieste di approvazione (sez. 12 del prompt) partono via SMTP del
dominio `jobinpa.it`. Implementazione: `src/social/approvals.py`
(smtplib, STARTTLS o SSL diretto — stesso meccanismo di `notifiche.py`).

## Configurazione (.env)

```
SMTP_HOST=authsmtp.securemail.pro    # esempio Register.it
SMTP_PORT=587
SMTP_USERNAME=social@jobinpa.it
SMTP_PASSWORD=...
SMTP_FROM_EMAIL=social@jobinpa.it
SMTP_FROM_NAME=JobInPA Social AI
SMTP_USE_TLS=true                    # STARTTLS; per SSL diretto (porta 465): SMTP_USE_SSL=true
```

Se le `SMTP_*` sono vuote si usano le `INPA_SMTP_*` gia' configurate per il
bot; se anche quelle mancano l'invio viene **saltato senza errori** (esito
`saltata` in `social_email_notifications`): la pipeline non dipende mai
dall'email.

## Destinatari

I revisori si impostano da dashboard → Impostazioni → "Revisori (email
approvazioni)" (`social_system_settings.revisori_email`).

## Contenuto dell'email

Titolo del contenuto, piattaforme, classe di rischio e **link alla
dashboard locale** (`APP_BASE_URL/social/approvazioni`). Mai token o link
firmati: l'approvazione avviene solo autenticati in dashboard. Ogni invio
e' registrato (`social_email_notifications` + `social_approval_events`).

## Sviluppo

Con `.\scripts\start.ps1 -Dev` tutte le email vanno a **Mailpit**
(<http://localhost:8025>): nessun invio reale.
