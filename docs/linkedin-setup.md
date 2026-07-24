# Setup LinkedIn

Stato attuale: la Pagina aziendale JobInPA esiste e l'utente e'
amministratore. Serve una app developer e l'accesso al prodotto Community
Management API. Solo API ufficiali.

## Passi

1. **App**: developer.linkedin.com → Create app, associata alla Pagina
   JobInPA (richiede la verifica della Pagina: LinkedIn manda una richiesta
   all'amministratore).
2. **Prodotti**: dal tab *Products* richiedi **Community Management API**
   (consente post organici della Pagina, commenti e statistiche).
3. Compila in `.env`:
   ```
   LINKEDIN_CLIENT_ID=...
   LINKEDIN_CLIENT_SECRET=...
   LINKEDIN_REDIRECT_URI=http://localhost:8000/social/oauth/linkedin/callback
   LINKEDIN_ORGANIZATION_URN=urn:li:organization:<id>   # id nell'URL admin della Pagina
   LINKEDIN_API_VERSION=202411
   ```
4. **Token OAuth**: dalla dashboard → Impostazioni → account LinkedIn →
   "Autorizza (OAuth)". Il flusso (`/social/oauth/linkedin/start` →
   consenso su LinkedIn con scope `w_organization_social
   r_organization_social rw_organization_admin` →
   `/social/oauth/linkedin/callback`) scambia il code per un token, lo
   salva cifrato e verifica subito i privilegi admin
   (`GET /rest/organizationAcls?q=roleAssignee&role=ADMINISTRATOR`): se
   l'utente autorizzato NON amministra la Pagina configurata
   (`LINKEDIN_ORGANIZATION_URN`), l'account resta `in_configurazione` e
   viene registrato un incidente, mai marcato `verificato` per errore.
5. Quando il callback conferma checklist verde + privilegi admin: account
   → `verificato` in automatico. Il publishing per account va comunque
   abilitato a parte (Impostazioni). Per pubblicare davvero servono anche
   modalita' production, `GLOBAL_PUBLISHING_ENABLED=true`, kill switch
   spento e contenuto approvato/verde.
6. LinkedIn non emette refresh token per i prodotti standard: quando il
   token scade, ripeti "Autorizza (OAuth)" dalla dashboard.

## Chiamate usate (REST versioned API, header `LinkedIn-Version`)

- Post: `POST /rest/posts` (author = URN organizzazione, `commentary`,
  `lifecycleState=PUBLISHED`);
- Immagini: `POST /rest/images?action=initializeUpload` → `PUT` dei byte →
  riferimento `content.media.id` nel post;
- Metriche: `GET /rest/organizationalEntityShareStatistics`;
- Commenti: `GET /rest/socialActions/{urn}/comments`.
