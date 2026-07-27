# Immagini pubbliche per Instagram — Cloudflare R2

Il modulo gira SOLO in locale (porte su 127.0.0.1, nessun tunnel, nessun
port forwarding) e resta cosi': non serve esporre l'intera app dietro un
dominio pubblico per pubblicare su Instagram.

## Perche' non serve esporre l'app

Instagram (Content Publishing API) accetta solo un `image_url`
raggiungibile da Internet — a differenza di LinkedIn, che riceve i byte
dell'immagine direttamente dall'app via API, senza bisogno di un URL
pubblico. Esporre l'intera dashboard (login, sessioni, logica di business)
solo per soddisfare questo unico requisito sarebbe sproporzionato, e sulla
VM Aruba che gia' serve jobinpa.it (risorse limitate) sarebbe anche
rischioso: quella VM non ha Docker installato e non e' dimensionata per
reggere lo stack aggiuntivo.

## Soluzione: solo le immagini diventano pubbliche

Ogni immagine generata (`agents.visual()`, vedi `asset_storage.py`) viene
caricata anche su un bucket Cloudflare R2 con lettura pubblica, e l'URL
risultante (`social_media_assets.url_pubblico`) e' quello usato per
Instagram. L'app e la dashboard restano completamente private, raggiungibili
solo dal login — pubblico e' solo il singolo file immagine.

## Setup

1. [dash.cloudflare.com](https://dash.cloudflare.com) → R2 → crea un bucket
   (piano gratuito: 10 GB storage, nessun costo di banda in uscita).
2. Abilita l'accesso pubblico al bucket (dominio `<bucket>.r2.dev`, o un
   dominio personalizzato collegato).
3. R2 → "Manage API Tokens" → crea un token con permesso di scrittura sul
   bucket: da qui si ottengono Account ID, Access Key ID, Secret Access Key.
4. Compila in `.env`:
   ```
   R2_ACCOUNT_ID=...
   R2_ACCESS_KEY_ID=...
   R2_SECRET_ACCESS_KEY=...
   R2_BUCKET_NAME=...
   R2_PUBLIC_BASE_URL=https://<bucket>.r2.dev   # o il dominio personalizzato
   ```
5. Rilancia la dashboard: quando la checklist Instagram segna verde anche
   "Storage pubblico immagini (Cloudflare R2) configurato", servono ancora
   `SOCIAL_MODE=production`, `GLOBAL_PUBLISHING_ENABLED=true` e kill switch
   spento per pubblicare davvero (vedi docs/meta-instagram-setup.md).

## Se in futuro servisse comunque un dominio pubblico

Per altri motivi (link "Richiesta di approvazione" cliccabili da fuori
rete, notifiche email con link diretti, ecc.) l'applicazione e' comunque
gia' pronta per un reverse proxy senza modifiche sostanziali (URL relativi
ovunque, cookie `Secure` automatico quando `APP_BASE_URL` e' https, header
`X-Forwarded-For`/`X-Real-IP` gia' gestiti). In quel caso, valutare
Cloudflare Tunnel verso il PC locale invece di spostare lo stack sulla VM
Aruba (che non regge Docker) — non configurato ora, per scelta.
