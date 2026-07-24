# Testing — Modulo Social AI

```powershell
.\scripts\test.ps1            # tutta la suite, in Docker
.\scripts\test.ps1 -Local     # col Python locale
```

I test vivono in `tests/` e girano su un **DB temporaneo proprio**
(fixture `conn` in `conftest.py`, mai il DB reale in `data/`), in
modalita' `mock` (nessuna chiamata esterna, mai — nemmeno verso le API di
JobInPA: il Research Agent viene testato con un client finto iniettato).

## Copertura per requisito (sez. 24 del prompt master)

| Area                              | File                        |
|-----------------------------------|-----------------------------|
| State machine (transizioni/audit) | `test_state_machine.py`     |
| Risk scoring (regole, precedenza) | `test_risk.py`, `test_e2e_pipeline.py` |
| Cifratura token, SSRF, sanitizzazione, CSRF | `test_security_social.py` |
| Data layer: settings, job (lock/backoff/dead/recovery), idempotenza pubblicazioni, whitelist, audit senza segreti | `test_db_social.py` |
| Template immagine (8 template × 4 formati) | `test_images.py`   |
| Provider LLM: mock, budget 80%/100%, circuit breaker | `test_llm.py` |
| Publishing: catena kill switch a 5 livelli, doppia pubblicazione, adapter | `test_publishing.py` |
| E2E pipeline mock (verde/giallo/rosso, approvazioni, supervisor, seed demo) | `test_e2e_pipeline.py` |
| API/dashboard: 401/403, RBAC per ruolo, CSRF, kill switch, checklist | `test_api_social.py` |

## Baseline preesistente

Al 2026-07-24 la suite del repo aveva gia' 5 failure NON legati al modulo
social (`test_cv_parser` ×2, `test_cv_matching`, `test_db_cv_profilo`,
`test_semantic_search_quota`): sono invariati prima e dopo l'integrazione.

## Integrazione SMTP

In sviluppo le email si verificano a occhio su Mailpit
(<http://localhost:8025>); l'esito di ogni invio e' comunque registrato in
`social_email_notifications` (inviata/fallita/saltata), che e' cio' che i
test asseriscono.
