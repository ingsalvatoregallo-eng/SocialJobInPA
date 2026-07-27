# Setup Instagram (Instagram API with Instagram Login)

Flusso nativo Instagram: login OAuth direttamente su instagram.com, non piu'
tramite una Pagina Facebook. Sostituisce il vecchio flusso "Facebook Login
for Business" (Page Access Token) usato in precedenza.

Solo API ufficiali Meta/Instagram: niente scraping ne' automazioni di terze parti.

## Passi per completare la configurazione

1. **Account Instagram Business**: converti il tuo account Instagram in
   account Business/Creator se non lo e' gia' (Impostazioni → Account →
   "Passa a un account professionale").
2. **Meta Developer App**: su developers.facebook.com → Create App, tipo
   *Business*, associata al Business Portfolio JobInPA. Nel wizard "Casi
   d'uso", aggiungi **"Gestisci i messaggi e i contenuti su Instagram"**
   (Instagram API). Questo aggiunge automaticamente anche il prodotto
   "Facebook Login for Business" nella sidebar, ma per il publishing non
   serve configurarlo: quello che conta e' la sezione **Instagram** della
   app.
3. Nella sidebar della app, sezione **Instagram** → troverai un **App ID e
   App Secret Instagram separati** (es. "Jobinpa-IG"), diversi dall'App
   ID/Secret "Meta" generale dell'app. Sono questi quelli da usare qui.
4. Nella stessa sezione, passo **"Configura Instagram Business Login"**:
   registra il redirect URI OAuth:
   ```
   http://localhost:8100/social/oauth/instagram/callback
   ```
   e verifica/aggiungi i permessi `instagram_business_basic` e
   `instagram_business_content_publish` (sezione "Aggiungi le
   autorizzazioni necessarie" della pagina Instagram → potrebbero servire
   passaggi diversi per i permessi di pubblicazione rispetto a quelli di
   messaggistica: segui il wizard passo-passo mostrato in dashboard).
5. Compila in `.env`:
   ```
   INSTAGRAM_APP_ID=...            # ID app Instagram (sezione Instagram della app)
   INSTAGRAM_APP_SECRET=...        # Chiave segreta Instagram (bottone "Mostra")
   INSTAGRAM_REDIRECT_URI=http://localhost:8100/social/oauth/instagram/callback
   INSTAGRAM_GRAPH_API_VERSION=v21.0
   ```
   `INSTAGRAM_ACCOUNT_ID` e `FACEBOOK_PAGE_ID` **non servono piu'**: l'ID
   dell'account Instagram Business si ottiene automaticamente dal token
   dopo l'autorizzazione (vedi punto 6) e resta salvato nel database
   (`social_accounts.identificativo`), non in `.env`.
6. **Token OAuth**: dalla dashboard → Impostazioni → account Instagram →
   "Autorizza (OAuth)". Il flusso (`/social/oauth/instagram/start` →
   consenso su instagram.com → `/social/oauth/instagram/callback`) scambia
   il code per un token utente su `api.instagram.com`, lo scambia per uno
   long-lived (~60 giorni) su `graph.instagram.com`, poi recupera
   l'Instagram-scoped User ID (`GET /me?fields=user_id`) — e' quest'ultimo
   l'id usato per pubblicare (`/{user_id}/media`), salvato automaticamente.
7. **Immagini via URL pubblico**: l'endpoint ufficiale
   `POST /{ig-user-id}/media` accetta solo `image_url` raggiungibili da
   Internet. In locale non c'e' esposizione pubblica: questo requisito resta
   aperto finche' non esiste `social.jobinpa.it` (vedi
   docs/deployment-future.md) o uno storage pubblico. **La checklist lo
   marca volutamente rosso**: niente workaround non ufficiali.
8. Rilancia la dashboard: quando tutte le voci sono verdi, marca l'account
   come `verificato` e abilita il publishing per account. Per pubblicare
   davvero servono anche `SOCIAL_MODE=production`,
   `GLOBAL_PUBLISHING_ENABLED=true` e kill switch spento.

## Flusso di pubblicazione usato (Content Publishing API)

1. `POST /{ig-user-id}/media` (container con `image_url` + `caption`);
2. `POST /{ig-user-id}/media_publish` (`creation_id`).

Metriche: `GET /{media-id}/insights` (impression, reach, like, commenti,
condivisioni, salvataggi). Commenti: `GET /{media-id}/comments`.
