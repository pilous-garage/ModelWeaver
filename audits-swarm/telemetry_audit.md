# Audit des panneaux de monitoring et recommandations pour l'intégration de la télémétrie

## Contexte
Le projet **mw-swarm** possède un tableau de bord existant affichant les indicateurs clés de performance (KPI) du système. Actuellement, la collecte de métriques repose sur des panneaux de monitoring classiques (Grafana, Prometheus) mais la télémétrie détaillée (traces, logs structurés) n’est pas intégrée de façon homogène.

## Analyse des panneaux de monitoring actuels
| Panneau | Source des données | Métriques principales | Fréquence de mise à jour | Points faibles |
|---------|-------------------|----------------------|--------------------------|----------------|
| `CPU Usage` | Prometheus `node_exporter` | utilisation CPU % par node | 15s | Pas de granularité par processus, aucun contexte d’application |
| `Memory` | Prometheus `node_exporter` | mémoire totale / libre | 15s | Pas de suivi des fuites mémoire applicatives |
| `Request Latency` | Grafana Loki + Prometheus | latence moyenne des API | 30s | Pas de distribution détaillée, pas de trace end‑to‑end |
| `Error Rate` | Prometheus alerts | nombre d’erreurs HTTP 5xx | 30s | Pas de corrélation avec les logs d’erreurs |
| `Queue Depth` | RabbitMQ exporter | messages en attente | 10s | Aucun suivi des temps d’attente individuels |

### Principaux constats
1. **Granularité insuffisante** – les métriques sont agrégées au niveau du serveur, mais le besoin d’observer le comportement par micro‑service ou fonction n’est pas satisfait.
2. **Absence de traces distribuées** – impossible de suivre le chemin d’une requête à travers plusieurs services.
3. **Logs non structurés** – les logs sont collectés mais restent du texte libre, rendant l’analyse automatisée difficile.
4. **Manque de corrélation** – les panneaux ne permettent pas de croiser les métriques avec les logs ou les traces pour identifier les causes racines.
5. **Alerting limité** – les alertes se basent uniquement sur des seuils statiques, sans prise en compte de tendances ou d’anomalies.

## Recommandations d’intégration de la télémétrie
### 1. Adopt‑OpenTelemetry comme couche d’instrumentation unifiée
- **Instrumentation** : ajouter les SDK OpenTelemetry (Python, Go, Java) aux services critiques.
- **Exporters** : configurer des exportateurs vers **Prometheus** (métriques), **Jaeger/Tempo** (traces) et ** Loki** (logs).
- **Auto‑instrumentation** : exploiter les agents OpenTelemetry pour les frameworks web (FastAPI, Spring, Express) afin de couvrir rapidement les services existants.

### 2. Enrichir les métriques avec des labels contextuels
- Ajouter des labels `service`, `instance`, `region`, `env` aux métriques Prometheus.
- Créer des métriques personnalisées : `request_duration_seconds_bucket{service="auth", handler="login"}`.
- Utiliser les **histograms** et **summary** pour obtenir la distribution complète des latences.

### 3. Centraliser les logs structurés
- Passer les logs au format **JSON** (ex. `structlog` en Python) incluant `trace_id` et `span_id` provenant d’OpenTelemetry.
- Configurer **Promtail** pour pousser les logs vers Loki avec les mêmes labels que les métriques.

### 4. Mettre en place des traces distribuées
- Déployer **Jaeger** ou **Tempo** en mode “all‑in‑one” pour la phase initiale, puis évoluer vers une solution scalable (Cassandra/Elastic backend).
- Activer le **sampling** dynamique : 100 % en dev, 1‑5 % en prod, avec augmentation lors d’erreurs détectées.

### 5. Créer des panneaux de corrélation dans Grafana
- Utiliser le **Data Source** **Mixed** pour combiner métriques, logs et traces.
- Exemple de panneau : `Latency (ms) by service` + `Trace ID` clickable → ouvre la trace correspondante.
- Ajouter des **dashboards** : 
  - *Service Health* : CPU, mémoire, erreurs, latence, traces récentes.
  - *Queue Insights* : profondeur, temps d’attente, traces de consommateur.

### 6. Améliorer l’alerting avec des modèles de comportement
- Déployer **Prometheus Alertmanager** avec des règles basées sur les **rate** et **increase** des métriques (ex. `rate(http_requests_total[5m]) > 1000`).
- Utiliser **Grafana Alerting** pour les seuils combinés métriques + logs (ex. latence > 500 ms **et** présence de logs d’erreur).
- Intégrer les alertes dans les canaux de incident‑response (Slack, PagerDuty).

### 7. Documentation et gouvernance
- Rédiger un guide d’instrumentation interne décrivant les conventions de nommage, les labels obligatoires et les bonnes pratiques de logs.
- Mettre en place un processus de revue de code pour valider l’ajout d’instrumentation.
- Organiser des sessions de formation sur OpenTelemetry et Grafana.

## Plan d’action (Roadmap 3 mois)
| Semaine | Action | Responsable |
|---------|--------|--------------|
| 1‑2 | Installer OpenTelemetry Collector, Jaeger, Loki dans l’environnement de staging. | DevOps |
| 3‑4 | Instrumenter les services *auth* et *gateway* avec les SDK OpenTelemetry. | Équipe backend |
| 5‑6 | Migrer les logs vers JSON + promtail, ajouter labels `trace_id`. | SRE |
| 7‑8 | Créer les premiers dashboards de corrélation (latence + trace). | SRE / Frontend |
| 9‑10 | Définir les règles d’alerting avancées et les tester. | SRE |
| 11‑12 | Documentation, formation et mise en production progressive. | PM / Docs |

## Conclusion
L’intégration d’OpenTelemetry permettra d’unifier métriques, logs et traces, offrant ainsi une visibilité complète sur le système. En enrichissant les panneaux de monitoring existants et en ajoutant des dashboards de corrélation, les équipes pourront diagnostiquer plus rapidement les incidents, réduire le MTTR et améliorer la fiabilité du tableau de bord.
