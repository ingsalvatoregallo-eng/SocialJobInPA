# Asset di brand JobInPA

Metti qui i file ufficiali del brand (NON ridisegnare il logo):

- `logo.png` — logo principale (sfondo trasparente): usato nel footer dei
  template immagine generati (vedi `src/social/images.py`);
- `icona.png` — icona quadrata;
- `favicon.ico` — favicon;
- eventuali font di brand (`.ttf`).

Se `logo.png` manca, i template usano il wordmark testuale
"JobInPA — Your PA, powered by AI": il rendering non fallisce mai.

Palette di riferimento (configurabile in `social_brands.palette`):
primario `#0B3D91`, accento `#1FA774`, sfondo `#F5F7FB`, testo `#15213B`.

Un logo Instagram esistente e' in `docs/immagini/LogoInsta.png`: copialo qui
come `logo.png` se vuoi usarlo nei template.
