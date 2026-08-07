# Carnet d'idées — features futures (à implémenter APRÈS le swarm minimum)

## Allocation "budget" au lieu de "score global" (idée utilisateur 2026-08-07)

Le scoring actuel élit le "meilleur modèle" pour tous → RPM saturé. Vision future :
un pipeline d'allocation par BESOIN + CAPACITÉ :

```
besoin_agent (req/min, tok/min attendus, agentic? coding? contexte?)
  → ① filtre compétence minimale (multiplication par contrainte,
       ex. "non-agentic mais besoin d'agentic" → ×0.8)
  → ② filtre budget (capacité rate_limit réelle du modèle)
  → ③ classement par score qualité (benchmark)
```

- Donner les limites_rate (RPM/TPM/RPS) AUX agents : s'il y a TKM/TPM, l'agent peut
  préférer attendre X secondes plutôt que de saturer.
- Calcul du temps d'attente selon la séquence : si la séquence a fait < 1 min,
  attendre plus ; le début de séquence indique le backoff à poser.
- Modèles avec TKM/TPM → basculer en chat / chat_streaming, contexte
  minimal/large, selon les restrictions et le type de limite.
- Patch "agentic avec texte pur" : boucle + analyse de texte quand le modèle
  n'a pas de tool calling natif.
- Skill spécialisé de LECTURE du rate_limit (pas encore existant) + décision.

## Séquences de réussite → calibrage rate_limit (partiellement codé)

Les séquences (model_success_runs) donnent la CAPACITÉ réelle par modèle :
  - p90 des req/séquence = débit max tenu sans échec → limite RPM/TPM à viser
  - p10 = repli conservateur
Usage : calibrage rate_limit_rpm/tpm, budget d'allocation, bonus de
réhabilitation. Pas un "score" à part entière.

## Divers
- Bonus réhabilitation : modèle qui revient de repos et répond → bonus si
  longues séquences récentes.
- Fenêtres de monitoring : 5m/15m/1h/4h/24h/7j (fait) ; mode avancé avec
  graphes (fait, panel monitoring-llm-avance).
