# Esposizione futura — social.jobinpa.it

Oggi il modulo gira SOLO in locale (porte su 127.0.0.1, nessun tunnel,
nessun port forwarding). L'applicazione e' pero' gia' pronta per un reverse
proxy senza modifiche sostanziali.

## Cosa e' gia' pronto

- URL relativi ovunque; i link assoluti (email) usano `APP_BASE_URL`;
- cookie di sessione con flag `Secure` automatico quando `APP_BASE_URL`
  e' https;
- header `X-Forwarded-For`/`X-Real-IP` gia' gestiti per l'audit
  (`deps.metadati_richiesta`);
- lo stack Docker espone una sola porta (8000) da mettere dietro proxy.

## Passi quando si decidera' di esporre

1. DNS: `social.jobinpa.it` → server scelto (es. la VM Aruba esistente, che
   gia' serve jobinpa.it con nginx + certbot);
2. nginx: nuovo server block con TLS che fa `proxy_pass` verso l'host che
   esegue lo stack (o si sposta lo stack sulla VM);
3. `.env`: `APP_BASE_URL=https://social.jobinpa.it`;
4. redirect URI OAuth Meta/LinkedIn aggiornate al nuovo dominio;
5. al proxy: rate limiting sul login, security headers (HSTS,
   X-Content-Type-Options, CSP), come gia' fatto per il portale;
6. da quel momento le immagini generate diventano raggiungibili via URL
   pubblico → si sblocca l'ultimo requisito della checklist Instagram
   (`image_url` del Content Publishing API).

## Alternativa Cloudflare Tunnel

Se si preferisce non esporre il PC locale: un tunnel `cloudflared` verso
`localhost:8000` con hostname `social.jobinpa.it`. Non configurato ora, per
scelta (sez. 2 del prompt master).
