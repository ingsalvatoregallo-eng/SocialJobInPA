# Operazioni quotidiane — Modulo Social AI

## Flusso editoriale tipo

1. **Piano settimanale**: lo scheduler crea ogni settimana il job
   `generate_week_plan`; il Supervisor genera 3 argomenti (opportunita',
   guida, scadenza) → visibili nel Calendario, ciascuno con la sua idea di
   contenuto. In alternativa: Calendario → "Genera 3 argomenti".
2. **Pipeline**: da un contenuto in stato IDEA → "Avvia pipeline agenti".
   Il worker esegue ricerca → copy IG/LI → visual → quality & risk.
3. **Esiti possibili**: verde → APPROVED e programmato in automatico alla
   prossima finestra oraria; giallo → AWAITING_APPROVAL + email ai
   revisori; rosso → BLOCKED.
4. **Approvazione**: dashboard → Approvazioni → Approva / Richiedi
   modifiche / Rifiuta (con motivazione, tutto in audit).
5. **Pubblicazione**: il worker esegue il job `publish` all'orario
   programmato (o "Pubblica ora" con permesso `social.publish`). In
   sandbox/mock la pubblicazione e' sempre simulata.
6. **Metriche e commenti**: job `collect_metrics` ogni 6 ore; le risposte
   proposte ai commenti si approvano in dashboard → Commenti.

## Kill switch

- Dashboard → home → "ATTIVA KILL SWITCH" (permesso `social.publish`), o
  `POST /api/v1/social/system/kill-switch`;
- livello environment: `GLOBAL_PUBLISHING_ENABLED=false` in `.env` (vince
  su tutto);
- per un singolo canale: Impostazioni → account → Disabilita.

## Passaggio a produzione (procedura completa)

1. checklist Instagram e LinkedIn tutte verdi (vedi docs/meta-instagram-
   setup.md e docs/linkedin-setup.md), token OAuth salvati;
2. account marcati `verificato` e publishing per account abilitato;
3. SMTP reale configurato e revisori impostati;
4. budget AI verificati (dashboard → Analytics e costi);
5. `SOCIAL_MODE=production` e `GLOBAL_PUBLISHING_ENABLED=true` in `.env`;
6. kill switch spento in dashboard;
7. primo contenuto: farlo passare da approvazione umana anche se verde
   (prudenza), verificare il post pubblicato e le metriche.

## Monitoraggio

- Dashboard home: incidenti aperti, job dead-letter, costi vs budget;
- Log e audit: esecuzioni agenti (con costi), audit trail, email;
- `docker compose logs -f worker scheduler` per i processi di fondo.

## Job in dead-letter

Un job passa a `dead` dopo 5 tentativi (backoff esponenziale). Va ispezionato
in Log e audit → "Job"; dopo aver rimosso la causa si ricrea dal punto
appropriato (es. ripartire con "Avvia pipeline" sul contenuto).
