"""
Modulo Social AI di JobInPA — gestione automatizzata dei contenuti social
(Instagram + LinkedIn) con agenti AI, approvazioni umane e kill switch.

Vive interamente in questo package: l'unica integrazione col resto del
repository e' il montaggio dei router in src/api.py (protetto da try/except:
se questo package viene rimosso l'app esistente continua a funzionare).

Requisiti e vincoli: docs/social/jobinpa_social_ai_prompt_master.md
Piano e decisioni:   docs/social-ai-implementation-plan.md
"""
