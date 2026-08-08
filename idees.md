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

## Sources de benchmark — audit + élargissement (mission swarm)

État actuel : model_benchmarks_raw = 92% synthétique (synthetic_catalogue,
frontier_estimate), seulement ~445 lignes réelles (lmsys_arena, arena_hard).
artificial_analysis.py ne produit AUCUNE donnée (bug à diagnostiquer).

Sources officielles à couvrir (Top prioritaire → académique → spécialisé) :
1. Artificial Analysis (À RÉPARER — référence qualité/latence/prix, TTFT,
   tokens/s, variance providers). Notre fichier est vide.
2. OpenRouter Rankings (leaderboard usage réel, ratio qualité/prix).
3. Hugging Face Open LLM Leaderboard v2 (MMLU-Pro, GPQA, MuSR — open-source).
4. LiveBench (AbacusAI — anti-triche, questions changent chaque mois).
5. SWE-bench / SWE-agent (code réel, résolution issues GitHub).
6. MMLU-Pro / GPQA (raisonnement complexe niveau doctorat).
7. Vectara Hughes Hallucination Leaderboard (taux d'hallucination).

Pour CHAQUE source :
  - créer une table dédiée complète (source, modèle, score, métadonnées),
  - scraper un maximum (≤ 10k modèles les plus populaires),
  - stocker avec les NOMS DE LA SOURCE,
  - réconcilier les noms source → model_ref du catalogue.

Tâches transverse : réconciliation par provider ; recherche doublon/manquant
dans catalogue_models (via noms provider). Le tout SANS brancher sur
l'allocation tant que non validé.

## Isolation des agents en subprocess (idée 2026-08-08)

État actuel : les agents s'exécutent en THREADS du process agent_manager (le
code kill() le dit : « agents INLINE dans le processus du daemon »). Isolation
en process séparé = « architecture prévue » mais PAS implémentée.

Conséquences : redémarrer agent_manager tue tous les threads des agents en
cours ; un agent qui plante (segfault/OOM) peut faire tomber les autres ; le
GIL limite le vrai parallélisme CPU.

Question résolue : un subprocess doit-il recharger le résolveur FSM ?
- Le FSM interpreter (fsm_interpreter.py, ~1200 lignes) est MONOLITHIQUE :
  résolution du workflow et boucle d'exécution entrelacées dans run() →
  pas séparable sans refactor → chaque subprocess devrait le charger.
- MAIS le résolveur est LÉGER (code pur). Le vrai surcoût du subprocess =
  imports LLM Manager + bridges (~1-2s + RAM), qui sont DÉJÀ chargés par
  chaque agent aujourd'hui (en thread). Donc isolation faisable sans explosion.

Pistes d'architecture (à décider) :
- Subprocess « fat » par agent : autonome, charge tout (isolation totale).
- Subprocess « thin » : parent garde le FSM, le subprocess exécute les steps
  reçues (appels LLM + tools) — plus léger mais agent non isolé pour le LLM.
- Pool de subprocess partagés vs 1 process/agent.

