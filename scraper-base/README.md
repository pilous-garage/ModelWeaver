# scraper-base — Benchmarks LLM

Scrapers de benchmarks publics pour alimenter les scores d'efficacité
des modèles LLM dans le catalogue ModelWeaver.

## Principe

Ce dossier contient des scripts **standalone** destinés à tourner sur
**une machine admin** (pas sur le poste de l'utilisateur). Ils scrappent
les leaderboards publics, normalisent les scores en percentiles, et
écrivent le résultat sur la base Turso distante.

## Sources

| Source | Benchmark Key | Métriques |
|---|---|---|
| LMSYS Chatbot Arena | `lmsys_arena_elo` | Elo (qualité perçue humaine) |
| Artificial Analysis | `artificial_analysis` | Quality, Speed (tps), Cost ($/M tok) |

## Usage

```bash
# Écrire sur Turso (distant — machine admin)
python scraper-base/benchmarks/run_all.py

# Écrire sur le catalogue local (dev/test)
python scraper-base/benchmarks/run_all.py --local
```

## Cron hebdomadaire (machine admin)

```cron
0 6 * * 1 cd /opt/modelweaver && ./scraper-base/benchmarks/run_all.py >> /var/log/modelweaver-benchmarks.log 2>&1
```

## Tables

- `model_benchmarks_raw` : données brutes par source (une ligne par métrique)
- `model_efficacy` : scores consolidés (qualité, vitesse, coût, fiabilité)
