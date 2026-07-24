# Anthropic — provider principale (testo e ragionamento)

Implementazione: `src/social/llm.py` (`AnthropicProvider`), dietro il
protocollo `LLMProvider` — la business logic non conosce il provider.

## Configurazione (.env)

```
ANTHROPIC_API_KEY=            # condivisa con ai_classifier (gia' definita sopra nel file)
ANTHROPIC_MODEL=claude-sonnet-5
ANTHROPIC_MAX_TOKENS=2048
ANTHROPIC_MONTHLY_BUDGET_EUR=20
ANTHROPIC_DAILY_BUDGET_EUR=3
```

Senza chiave il factory `provider_llm` ripiega su `MockLLMProvider`:
tutto il flusso resta esercitabile offline.

## Come funziona una chiamata

1. verifica del circuit breaker (3 errori consecutivi → aperto 120 s);
2. verifica budget (giornaliero e mensile, da `social_cost_entries`):
   incidente all'80%, **blocco** al 100% (`BudgetEsaurito` — i job restano
   in coda con retry, niente va perso);
3. chiamata `messages.create` con **tool-use forzato**: lo schema Pydantic
   dell'output e' l'`input_schema` del tool, quindi la risposta e' sempre
   strutturata e validata (`model_validate`);
4. retry con backoff esponenziale (max 3) sugli errori di rete;
5. registrazione costi: token input/output × prezzi in
   `social_system_settings.prezzi_token_eur` (EUR per milione di token,
   aggiornabili senza deploy).

## Prompt

Versionati in `src/social/prompts.py` e registrati in
`social_prompt_versions` (nome, versione, hash SHA-256 troncato, testo).
Ogni `social_agent_runs` salva la tripla usata. Le modifiche ai prompt
passano dal repository (code review), non dalla dashboard.

## Anti prompt-injection

I contenuti esterni (pagine web, commenti) entrano SOLO nel prompt utente
dentro blocchi `<fonte>`; ogni system prompt istruisce a ignorare comandi
nel contenuto; il Research Agent non ha accesso a credenziali o tool
privilegiati. Vedi docs/security.md.
