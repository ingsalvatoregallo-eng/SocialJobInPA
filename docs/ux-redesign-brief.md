# Brief per redesign UX/UI — Dashboard SocialJobInPA

Documento autonomo (non serve accesso al codice) per progettare mockup di
una nuova interfaccia. Copre: cosa fa il prodotto, chi lo usa, il modello
concettuale che la UI deve rappresentare, l'inventario delle schermate
attuali e i problemi di usabilità riscontrati nell'uso reale.

---

## 1. Cos'è il prodotto

SocialJobInPA genera e pubblica contenuti social (Instagram + LinkedIn) per
**JobInPA**, una piattaforma che aiuta le persone a trovare concorsi
pubblici. Il payoff del brand: *"Your PA, powered by AI"*.

Il sistema prende un'idea in linguaggio naturale, la trasforma in un post
completo (testo per due piattaforme + immagine) usando dati reali sui
concorsi pubblici, valuta il rischio del contenuto, e — a seconda del
rischio — lo pubblica da solo o lo mette in attesa di approvazione umana.

Non è un editor di post generico: è uno strumento verticale su un solo
dominio (concorsi pubblici) con un forte impianto di sicurezza (mai
pubblicare dati inventati, mai bypassare un'approvazione richiesta).

## 2. Chi lo usa

Un piccolo team (oggi: 1 persona, in futuro forse 2-4), con ruoli:

| Ruolo | Può fare |
|---|---|
| **admin** | Tutto, incluse impostazioni tecniche, eliminazione contenuti, kill switch |
| **editor** | Creare idee, avviare la pipeline AI, modificare contenuti |
| **reviewer** | Approvare/rifiutare/richiedere modifiche ai contenuti in attesa |
| **viewer** | Sola lettura |

Nella pratica quotidiana, chi usa lo strumento fa **due cose diverse** che
oggi vivono nella stessa interfaccia senza distinzione:
1. **Lavoro editoriale quotidiano** — creare idee, rivedere, approvare, pubblicare
2. **Configurazione tecnica una tantum** — collegare account social, impostare l'AI, la sicurezza

## 3. Il modello concettuale che la UI deve rappresentare

### Il "contenuto" è l'unità centrale

Ogni post nasce come **idea** (titolo + una descrizione in linguaggio
naturale, il "brief") e attraversa un percorso a tappe fino alla
pubblicazione (o all'interruzione, se qualcosa non va).

### Il percorso, in termini comprensibili (non i nomi tecnici interni)

Il sistema tiene traccia di **16 stati tecnici** molto granulari
(IDEA, RESEARCHING, RESEARCH_FAILED, DRAFTING, DRAFT_READY,
GENERATING_VISUAL, QUALITY_CHECK, BLOCKED, AWAITING_APPROVAL,
CHANGES_REQUESTED, APPROVED, SCHEDULED, PUBLISHING, PUBLISHED,
PARTIALLY_PUBLISHED, PUBLISH_FAILED, CANCELLED, ARCHIVED) — ma per l'utente
umano si raggruppano in **poche fasi con un significato chiaro**:

| Fase (da progettare) | Stati tecnici inclusi | Cosa significa per l'utente | Azione possibile |
|---|---|---|---|
| **Idea** | IDEA | Scritta, non ancora elaborata | "Avvia elaborazione" |
| **In elaborazione** | RESEARCHING, DRAFTING, GENERATING_VISUAL, QUALITY_CHECK | L'AI sta lavorando (dura secondi/minuti) | Nessuna — solo attesa |
| **Non riuscita** | RESEARCH_FAILED | Errore recuperabile (es. servizio esterno non raggiungibile) | "Riprova" |
| **Annullata** | CANCELLED | L'AI ha cercato ma non ha trovato nulla di pertinente al brief | Vedere perché, eventualmente riscrivere il brief |
| **Bloccata** | BLOCKED | Contenuto troppo rischioso, mai pubblicabile cosi' com'è | Vedere il motivo, eventualmente ricominciare |
| **Da rivedere** | AWAITING_APPROVAL, CHANGES_REQUESTED | Serve una decisione umana | "Approva" / "Rifiuta" / "Richiedi modifiche" |
| **Programmata** | APPROVED, SCHEDULED | Uscirà da sola alla prossima fascia oraria utile | "Pubblica subito" (opzionale) |
| **In pubblicazione** | PUBLISHING | Operazione in corso verso Instagram/LinkedIn | Nessuna — solo attesa |
| **Pubblicata** | PUBLISHED, PARTIALLY_PUBLISHED | Uscita (su una o entrambe le piattaforme) | Vedere il link pubblicato, le metriche |
| **Pubblicazione fallita** | PUBLISH_FAILED | Errore tecnico verso la piattaforma | "Riprova" |
| **Archiviata** | ARCHIVED | Storico, chiuso | — |

**Suggerimento per il redesign**: la UI dovrebbe mostrare sempre questa fase
semplificata come stato primario (con un colore/icona), e i dettagli tecnici
solo su richiesta (tooltip, pannello secondario).

### Il rischio, in termini comprensibili

Ogni contenuto riceve una classe: 🟢 **verde** (pubblica da solo),
🟡 **giallo** (serve conferma umana), 🔴 **rosso** (mai pubblicabile).
Oggi appare come "rischio: giallo → human_approval" — un tecnicismo.
Andrebbe reso come un badge visivo con un motivo leggibile ("Contiene un
riferimento normativo: serve una verifica umana"), non un codice.

### Il brief conta davvero, e oggi non è chiaro

Quando l'utente scrive il brief di un'idea, un agente AI lo traduce in
filtri di ricerca reali (es. "concorsi con più di 10 posti in Lombardia"
→ regione=Lombardia, posti_minimi=10). Se non trova nulla che corrisponda,
il contenuto si annulla automaticamente. **Oggi questo non è comunicato
all'utente al momento della scrittura**: non sa che il brief verrà
interpretato in modo strutturato, né riceve esempi o suggerimenti. Il
redesign dovrebbe rendere esplicito questo passaggio (es. mostrare in
anteprima i filtri che l'AI ha capito, prima di lanciare l'elaborazione).

### Calendario editoriale

Un sistema automatico propone ogni settimana 3 temi (uno per categoria:
opportunità, guida, scadenze). L'utente può anche scrivere idee proprie in
qualsiasi momento, indipendentemente dal calendario. Il calendario e
l'elenco dei contenuti oggi sono due viste separate che si sovrappongono
parzialmente — da riconciliare in un redesign.

## 4. Le pagine esistenti oggi (inventario)

Interfaccia attuale: form HTML tradizionali con tabelle, nessun
aggiornamento automatico (va sempre ricaricata la pagina a mano),
navigazione a menu laterale con 9 voci.

1. **Dashboard** — stato generale: kill switch, budget AI, checklist
   configurazione Instagram/LinkedIn, contenuti recenti, incidenti
2. **Calendario editoriale** — vista settimanale dei temi pianificati
3. **Contenuti** — elenco filtrabile per fase (Idee/Bozze/Approvazioni/
   Programmati/Pubblicati/Errori/Archivio) + form di creazione + dettaglio
   di ogni contenuto (anteprime Instagram/LinkedIn, fatti verificati,
   valutazione di rischio, pubblicazioni)
4. **Approvazioni** — coda di contenuti in attesa di decisione umana
5. **Pubblicazioni** — cosa è stato pubblicato/programmato/fallito
6. **Commenti** — commenti ricevuti sui social + risposte proposte dall'AI
   (mai inviate senza approvazione)
7. **Analytics e costi** — metriche social disponibili + spesa AI vs budget
8. **Log e audit** — log tecnico di agenti, job, audit trail, email inviate
9. **Impostazioni** — account social (con OAuth), fonti autorizzate,
   revisori email, prompt AI (sola lettura), utenti, impostazioni di sistema

## 5. Problemi di usabilità riscontrati nell'uso reale

Elenco basato su un uso reale del sistema, non ipotetico:

1. **Nessun feedback dopo un'azione asincrona.** Cliccare "Avvia
   elaborazione" ricarica la stessa pagina, identica: non si capisce se è
   partito qualcosa. Serve un indicatore di stato "in corso" (spinner,
   polling, o almeno un messaggio "richiesta ricevuta, ricarica tra poco").
2. **Troppi stati tecnici esposti direttamente.** 16 stati con nomi in
   inglese tecnico (RESEARCHING, GENERATING_VISUAL...) invece di poche fasi
   comprensibili (vedi tabella sopra).
3. **Il "perché" è nascosto.** Quando un contenuto si blocca o si annulla,
   il motivo è un campo di testo tecnico in fondo alla pagina, non il primo
   elemento visibile.
4. **Navigazione frammentata e ridondante.** Lo stato di approvazione di un
   contenuto appare sia in "Contenuti" che in "Approvazioni"; lo stato di
   pubblicazione sia nel dettaglio del contenuto che in "Pubblicazioni".
   Nessuna vista unica "cosa devo fare io, adesso".
5. **Tutto è una tabella HTML.** Nessuna gerarchia visiva, nessuna card,
   nessuna vista tipo kanban che renderebbe leggibile il flusso a colpo
   d'occhio.
6. **Configurazione tecnica mescolata al lavoro editoriale quotidiano.** La
   pagina Impostazioni contiene dettagli da sviluppatore (nomi di variabili
   d'ambiente, endpoint API) nello stesso posto dove si gestiscono i
   revisori delle email — pubblici diversi, stesso spazio.
7. **Scrittura del brief senza guida.** Nessun esempio, nessuna anteprima
   di cosa l'AI capirà dal testo scritto, nessun avviso che un brief troppo
   vago o troppo specifico cambia il risultato.
8. **Conferme con popup di sistema del browser.** Funzionali ma non
   integrate visivamente (es. l'eliminazione di un contenuto usa
   `confirm()` nativo del browser).

## 6. Cosa fa concretamente l'utente in una settimana tipo

Utile per progettare il flusso principale (non solo le schermate isolate):

1. Lunedì: il sistema ha già proposto 3 temi nel calendario (automatico)
2. L'utente apre ciascun tema, eventualmente lo modifica, avvia l'elaborazione
3. Aspetta (secondi/minuti), ricontrolla
4. Per i contenuti "da rivedere": legge l'anteprima, decide
5. I contenuti "programmati" escono da soli; l'utente controlla ogni tanto
   che siano usciti bene
6. Ogni tanto: guarda i commenti ricevuti, approva le risposte proposte
7. Meno spesso: guarda analytics/costi, aggiunge idee proprie fuori piano

## 7. Vincoli tecnici (per valutare la fattibilità, non per limitare i mockup)

- Backend oggi: FastAPI + form HTML tradizionali (Jinja2), nessun
  framework frontend. Per i mockup questo NON è un vincolo — si può
  proporre liberamente una UI più ricca (SPA, aggiornamenti in tempo
  reale) e poi valutare insieme la fattibilità dell'implementazione.
- L'esecuzione della pipeline è realmente asincrona (un processo in
  background separato): qualunque redesign deve prevedere uno stato "in
  corso" credibile, non solo un caricamento istantaneo finto.
- Multi-ruolo reale (admin/editor/reviewer/viewer): il redesign deve
  considerare viste/permessi diversi, non un'unica vista per tutti.

## 8. Obiettivo del redesign

Non "abbellire" le schermate esistenti, ma ripensare l'informazione
attorno a due domande che l'utente si fa sempre:

1. **"Cosa sta succedendo ai miei contenuti in questo momento?"**
   (vista d'insieme, fasi comprensibili, non stati tecnici)
2. **"Cosa devo fare io, adesso?"**
   (una coda unica di azioni richieste — approvazioni in attesa, errori da
   rivedere — invece di dover controllare 4 pagine diverse)

La configurazione tecnica (account social, prompt, utenti) può restare
separata concettualmente ("Impostazioni amministratore"), distinta dallo
spazio di lavoro editoriale quotidiano.
