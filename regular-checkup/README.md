# Regular Checkup — Catalogue & Santé du système

Scripts à exécuter régulièrement (manuellement ou via cron) pour
maintenir le catalogue DB à jour et détecter les problèmes.

## Usage

```bash
# Tout synchroniser
./sync_all.sh

# Ou étape par étape
python3 sync_from_providers.py          # depuis les API des providers
python3 sync_from_awesome_list.py       # depuis awesome-free-llm-apis/data.json
python3 sync_capabilities.py            # capacités des modèles (function calling…)
python3 health_check.py                 # état des providers configurés
```

## Scripts

| Script | Source | Ce qu'il fait |
|--------|--------|---------------|
| `sync_from_providers.py` | API des providers | Liste les modèles via `/v1/models` pour chaque provider configuré |
| `sync_from_awesome_list.py` | `data.json` remote | Récupère la liste curated des providers free et peuple la DB |
| `sync_capabilities.py` | Base de connaissance | Remplit `model_capabilities` (function calling, vision, etc.) |
| `health_check.py` | DirectBridge | Teste chaque provider clé en main et rapporte les problèmes |

## Scripts legacy liés

Ces scripts existent déjà dans le projet mais sont utiles ici aussi :

- `scripts/sync_provider_models.py` — sync avancée avec `--ping`, `--pricing`, `--pricing-openrouter`
- `modules/llm_manager/catalogue_sync.py` — sync par provider + capacités par famille
- `modules/llm_manager/catalogue_remote.py` — sync depuis endpoint central
