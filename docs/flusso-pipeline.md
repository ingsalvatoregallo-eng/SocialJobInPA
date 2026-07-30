# Flusso del sistema: chi avvia, chi esegue

Questo documento traccia il percorso reale di un contenuto, dalla creazione
alla pubblicazione, indicando per ogni passaggio **chi lo avvia** e **chi lo
esegue davvero**. Per la mappa statica dei componenti (processi, database,
agenti) vedi [architecture.md](architecture.md); qui l'ottica è il percorso
nel tempo, non l'elenco dei pezzi.

## Attori

| Attore | Cos'è | Sincrono/asincrono |
|---|---|---|
| **Utente** | Chi usa la dashboard nel browser (editor/reviewer/admin) | — |
| **FastAPI** (processo `app`) | `src/social/web.py`: riceve le richieste HTTP, legge/scrive il DB, **non esegue mai l'AI o la pubblicazione lui stesso** | Sincrono: risponde entro la richiesta |
| **Worker** (processo `worker`, `worker_main.py`) | Consuma la coda `social_scheduled_jobs` uno alla volta, loop ogni ~10s | Asincrono: il lavoro vero (AI, immagini, pubblicazione) avviene qui |
| **Scheduler** (processo `scheduler`, `scheduler_main.py`) | Semina job ricorrenti (piano settimanale, raccolta metriche), loop ogni 5 min | Asincrono, non esegue nulla lui stesso: crea solo righe in coda |
| **JobInPA** | API interne (`/api/internal/bandi`, `/promozioni`, `/funzionalita`) | Servizio esterno, sola lettura |
| **Anthropic** | LLM per i testi (Research/Copywriting/Quality&Risk) | Servizio esterno |
| **OpenAI** | Generazione immagini (quando abilitata) | Servizio esterno |
| **Instagram/LinkedIn** | Pubblicazione reale | Servizio esterno |

Il punto chiave: **FastAPI non fa mai lavoro pesante o lento nella richiesta
HTTP**. Crea una riga in `social_scheduled_jobs` e risponde subito — è
sempre il Worker, in un processo separato, a eseguire ricerca, scrittura
testi, generazione immagini e pubblicazione. Per questo la scheda di un
contenuto appena creato mostra "in coda" invece del risultato immediato.

## Flusso principale: creazione → pubblicazione

```mermaid
flowchart TD
    subgraph U["👤 Utente"]
        U1["Nuovo contenuto:<br/>sceglie Categoria + brief/promo/funzionalità"]
        U2["Revisione:<br/>approva / rifiuta / richiede modifiche"]
    end

    subgraph F["🌐 FastAPI — web.py (processo 'app', sincrono)"]
        F1["POST /contenuti<br/>salva la riga (stato IDEA)<br/>accoda job 'pipeline'"]
        F2["Redirect immediato<br/>alla scheda contenuto"]
        F3["POST /approvazioni/{id}<br/>se approva → programma_pubblicazione<br/>accoda job 'publish'"]
    end

    subgraph W["⚙️ Worker — worker_main.py (processo 'worker', loop ~10s)"]
        W1["Prende il job 'pipeline'<br/>(agents.esegui_pipeline)"]
        W2["Research: bandi/promo/funzionalità<br/>da JobInPA, o brief libero"]
        W3["Copywriting: testo<br/>Instagram + LinkedIn"]
        W4["Visual: immagine<br/>(template o OpenAI)"]
        W5["Quality &amp; Risk:<br/>classe verde / giallo / rosso"]
        W6{"Decisione"}
        W7["Verde: programma la<br/>pubblicazione da solo"]
        W8["Giallo/Rosso: coda<br/>di approvazione umana"]
        W9["Prende il job 'publish'<br/>quando arriva l'orario"]
        W10["Pubblica davvero"]
    end

    subgraph S["🕒 Scheduler — scheduler_main.py (processo 'scheduler', loop 5 min)"]
        S1["Ogni venerdì:<br/>accoda 'generate_week_plan'"]
        S2["Ogni 6 ore:<br/>accoda 'collect_metrics'"]
    end

    subgraph E["☁️ Servizi esterni"]
        E1[("JobInPA")]
        E2[("Anthropic")]
        E3[("OpenAI")]
        E4[("Instagram / LinkedIn")]
    end

    U1 --> F1 --> F2
    F1 -. crea riga in coda .-> W1
    W1 --> W2 --> W3 --> W4 --> W5 --> W6
    W2 <--> E1
    W3 <--> E2
    W4 <--> E3
    W6 -->|verde| W7 --> W9
    W6 -->|giallo/rosso| W8
    W8 --> U2 --> F3
    F3 -. crea riga in coda .-> W9
    W9 --> W10 <--> E4

    S1 -. crea riga in coda .-> W1
    S2 -. crea riga in coda .-> W1
```

## Passo per passo

1. **Utente** compila "Nuovo contenuto": sceglie una **Categoria** (decide
   da sola la strategia — bandi JobInPA, promozioni, funzionalità, o
   libera) e un brief/selezione.
2. **FastAPI** (`web.crea_contenuto`) salva subito la riga `social_content`
   (stato `IDEA`) e crea una riga in `social_scheduled_jobs` di tipo
   `pipeline` — poi risponde immediatamente con un redirect. Non fa altro.
3. **Worker**, al primo giro utile del suo loop, reclama quel job
   (lock atomico in `db_social.prendi_job`) ed esegue
   `agents.esegui_pipeline`, che fa scorrere lo stato del contenuto
   attraverso gli agenti in sequenza, **nello stesso processo worker**:
   - *Research* — legge bandi/promozioni/funzionalità da JobInPA (o
     accetta il brief come fatto, per le categorie senza ricerca), tramite
     `jobinpa_client.py`;
   - *Copywriting* — chiama Anthropic per le due varianti di testo;
   - *Visual* — genera l'immagine (Pillow deterministico, o OpenAI se
     abilitato e la categoria ha un prompt/immagini di riferimento);
   - *Quality & Risk* — classifica il rischio (regole fisse + giudizio
     Anthropic), decide `auto_publish` / `human_approval` / `blocked`.
4. Se la classe è **verde**, il Worker stesso chiama
   `agents.programma_pubblicazione`, che crea una riga `social_content`
   in stato `SCHEDULED` e un nuovo job `publish` con l'orario della
   prossima finestra utile — nessun umano coinvolto.
5. Se la classe è **giallo/rosso**, il contenuto entra nella coda di
   approvazione (`social_approvals`). **L'Utente** (reviewer) decide da
   "Revisione": approva, rifiuta, o chiede modifiche.
   - Approvando, è di nuovo **FastAPI** (dentro la stessa richiesta HTTP,
     sincrono) a chiamare `programma_pubblicazione` e creare il job
     `publish` — qui FastAPI esegue lavoro "vero" perché programmare non
     richiede AI né chiamate esterne lente.
   - Chiedendo modifiche, viene accodato un nuovo job `pipeline` che
     rifà research→copywriting→visual→quality tenendo conto della nota
     del revisore.
6. **Worker**, quando l'orario schedulato arriva, reclama il job
   `publish` ed esegue `publishing.pubblica_contenuto`, che dopo la
   catena di controlli di sicurezza (kill switch, account verificato,
   classe non rossa) chiama davvero l'API di Instagram o LinkedIn.
7. **Scheduler**, indipendentemente da tutto questo, ogni 5 minuti
   controlla se serve seminare job ricorrenti: il piano editoriale
   settimanale (ogni venerdì, agenti Supervisor) e la raccolta metriche
   (ogni 6 ore) — li accoda soltanto, è sempre il Worker a eseguirli.

## Perché in due processi separati (Worker/Scheduler) e non dentro FastAPI

- Una pipeline completa può richiedere diverse chiamate AI in sequenza
  (secondi, a volte oltre un minuto): tenerla nella richiesta HTTP
  bloccherebbe il browser dell'utente e rischierebbe timeout.
- Il Worker può fallire e ritentare un singolo job (backoff esponenziale,
  fino a 5 tentativi, poi stato `dead` visibile in dashboard) senza che
  l'utente se ne accorga o debba ripetere l'azione.
- `social_scheduled_jobs` (SQLite, lock atomico via `UPDATE ... WHERE`) è
  l'unico punto di coordinamento: niente Redis/Celery, un solo worker alla
  volta per job è garantito dal lock stesso.
