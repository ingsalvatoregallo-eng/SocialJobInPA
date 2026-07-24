# Setup Meta / Instagram

Stato attuale (rilevato dalla checklist in dashboard): account Instagram
Business e Business Portfolio **presenti**; **mancano** la Pagina Facebook
collegata e la Meta Developer App. Finche' la checklist non e' completa la
dashboard mostra "Instagram non pronto per la pubblicazione API" e ogni
pubblicazione reale su Instagram e' bloccata (la modalita' mock funziona).

Solo API ufficiali Meta: niente scraping ne' automazioni di terze parti.

## Passi per completare la configurazione

1. **Pagina Facebook**: creala da business.facebook.com dentro il Business
   Portfolio JobInPA e collega l'account Instagram Business alla Pagina
   (Impostazioni Pagina → Account collegati → Instagram).
2. **Meta Developer App**: su developers.facebook.com → Create App, tipo
   *Business*, associata al Business Portfolio. Aggiungi i prodotti
   *Facebook Login for Business* e *Instagram Graph API*.
3. Compila in `.env`:
   ```
   META_APP_ID=...
   META_APP_SECRET=...
   META_REDIRECT_URI=http://localhost:8000/social/oauth/instagram/callback
   META_GRAPH_API_VERSION=v21.0
   FACEBOOK_PAGE_ID=...        # id della Pagina appena creata
   INSTAGRAM_ACCOUNT_ID=...    # GET /{page-id}?fields=instagram_business_account
   ```
4. **Token OAuth**: dalla dashboard → Impostazioni → account Instagram →
   "Autorizza (OAuth)". Il flusso (`/social/oauth/instagram/start` →
   consenso su Facebook → `/social/oauth/instagram/callback`) scambia il
   code per un token utente, lo scambia per uno long-lived (~60 giorni),
   poi recupera il Page Access Token della Pagina configurata
   (`FACEBOOK_PAGE_ID`) — è quest'ultimo il token salvato, cifrato
   (`ENCRYPTION_KEY`), perché è quello che la Content Publishing API accetta
   in modo affidabile per l'account Instagram Business collegato alla Pagina.
5. **Immagini via URL pubblico**: l'endpoint ufficiale
   `POST /{ig-user-id}/media` accetta solo `image_url` raggiungibili da
   Internet. In locale non c'e' esposizione pubblica: questo requisito resta
   aperto finche' non esiste `social.jobinpa.it` (vedi
   docs/deployment-future.md) o uno storage pubblico. **La checklist lo
   marca volutamente rosso**: niente workaround non ufficiali.
6. Rilancia la dashboard: quando tutte le voci sono verdi, marca l'account
   come `verificato` e abilita il publishing per account. Per pubblicare
   davvero servono anche `SOCIAL_MODE=production`,
   `GLOBAL_PUBLISHING_ENABLED=true` e kill switch spento.

## Flusso di pubblicazione usato (Content Publishing API)

1. `POST /{ig-user-id}/media` (container con `image_url` + `caption`);
2. `POST /{ig-user-id}/media_publish` (`creation_id`).

Metriche: `GET /{media-id}/insights` (impression, reach, like, commenti,
condivisioni, salvataggi). Commenti: `GET /{media-id}/comments`.
