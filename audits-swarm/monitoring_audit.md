# Audit des panneaux de monitoring

## Contexte
Le tableau de bord existant collecte déjà des métriques clés (CPU, mémoire, latence). Cependant, les panneaux de monitoring actuels ne couvrent pas l'ensemble des indicateurs de performance et de fiabilité nécessaires pour une supervision fine.

## Analyse des panneaux actuels
| Panneau | Métrique(s) couverte(s) | Limites |
|---|---|---|
| **CPU Usage** | Utilisation CPU globale | Pas de répartition par service / thread, pas d'histogramme d'utilisation.
| **Memory** | Mémoire totale utilisée | Aucun suivi du garbage collection, pas de distinction entre heap / non‑heap.
| **Latency** | Latence moyenne des requêtes | Pas de percentiles (p95, p99) ni de suivi des spikes.
| **Errors** | Nombre d'erreurs HTTP 5xx | Pas de classification par type d'erreur ou par endpoint.

## Recommandations

### 1. Étendre la télémétrie côté back‑end
- **Instrumentation** : Utiliser OpenTelemetry SDK pour instrumenter les services (Java, Python, Go). Exporter les traces et métriques vers un collecteur (ex. `otel-collector`).
- **Métriques additionnelles** :
  - **Histogrammes** pour la latence (p50, p95, p99).
  - **Compteurs** pour les erreurs classées par code et par endpoint.
  - **Gauge** pour la taille du heap, le nombre de threads actifs, le temps de GC.
  - **Custom metrics** : taux de requêtes par seconde, taux de succès vs échec.

### 2. Centraliser les logs et traces
- Configurer les applications pour exporter les logs en JSON vers un pipeline (ex. Loki) et les traces vers Jaeger/Tempo.
- Ajouter des labels (`service`, `environment`, `version`) pour faciliter le filtrage.

### 3. Mise à jour du dashboard
- **Prometheus** : Ajouter les nouvelles métriques dans les `scrape_configs`.
- **Grafana** : Créer de nouveaux panneaux :
  - **Latency distribution** – histogramme avec p95/p99.
  - **Error breakdown** – tableau avec top 5 endpoints en erreur.
  - **GC activity** – gauge du temps de pause GC.
  - **Thread pool** – gauge du nombre de threads actifs vs max.
- Utiliser des variables de tableau de bord (`$service`, `$env`) pour permettre le filtrage dynamique.

### 4. Alerting
- Définir des alertes basées sur les percentiles de latence (`latency_seconds{quantile="0.99"} > 1.5`) et sur le taux d'erreurs (`rate(http_requests_total{status=~"5.."}[5m]) > 0.01`).
- Configurer des notifications vers Slack/Teams.

### 5. Documentation & gouvernance
- Rédiger un guide d'intégration OpenTelemetry pour les équipes de développement.
- Mettre en place un processus de revue de la télémétrie avant le merge de nouvelles fonctionnalités.

## Plan d'action (Roadmap)
| Sprint | Action | Responsable |
|---|---|---|
| 1 | Ajouter OpenTelemetry SDK aux services critiques | Équipe backend |
| 1 | Configurer le collecteur et exporter vers Prometheus | Infra |
| 2 | Créer les nouveaux panneaux Grafana et les variables | DevOps |
| 2 | Définir les règles d'alerting initiales | SRE |
| 3 | Documenter le processus d'instrumentation | Docs Team |
| 3 | Revue et validation avec les PO | QA |

---
*Ce document doit être versionné dans le dépôt `mw-swarm` sous le répertoire `docs/` et intégré au pipeline CI pour être publié automatiquement sur le site interne.*