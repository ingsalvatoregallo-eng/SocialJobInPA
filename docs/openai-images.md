# OpenAI Images — provider immagini opzionale

Default: **spento** (`ENABLE_AI_IMAGES=false`). La modalita' predefinita per
gli asset e' il rendering deterministico con Pillow
(`TemplateImageProvider`, 8 template, formati IG/LinkedIn, margini sicuri,
palette e logo del brand): testi sempre esatti, costo zero.

## Attivazione

```
OPENAI_API_KEY=sk-...
OPENAI_IMAGE_MODEL=gpt-image-1
ENABLE_AI_IMAGES=true
OPENAI_IMAGE_MONTHLY_BUDGET_EUR=5
```

## Regole (sez. 7 del prompt master)

- Anthropic genera il brief e l'eventuale `prompt_ai`; OpenAI Images genera
  **solo lo sfondo**;
- i dati essenziali (scadenze, posti, enti, requisiti) vengono SEMPRE
  sovrapposti con testo deterministico (overlay Pillow), mai lasciati al
  modello;
- budget mensile dedicato: al raggiungimento si torna automaticamente ai
  template (`provider_immagini` fa il fallback, la pipeline non si ferma);
- ogni immagine genera una voce in `social_cost_entries`
  (`prezzo_immagine_ai_eur` in system settings, default 0.04 €).

## Formati generati

| Uso                | Dimensioni  |
|--------------------|-------------|
| Instagram feed     | 1080 × 1350 |
| Instagram quadrato | 1080 × 1080 |
| Instagram Story    | 1080 × 1920 |
| LinkedIn           | 1200 × 627  |
