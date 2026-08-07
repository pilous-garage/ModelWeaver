# Audit des panneaux de monitoring et recommandations pour l'intégration de la télémétrie

## 1. État actuel des panneaux de monitoring

| Panneau | Source de données | Fréquence de rafraîchissement | Problèmes identifiés |
|---------|-------------------|------------------------------|----------------------|
| CPU Utilisation | Prometheus (node_exporter) | 15s | Aucun problème majeur, mais manque de visualisation des spikes > 80%.
| Mémoire | Prometheus (node_exporter) | 15s | Pas de seuil d'alerte configuré.
| Latence des requêtes API | Grafana Loki (logs) | 30s | Les logs sont agrégés mais aucune métrique de latence moyenne/n‑95.
| Taux d'erreur HTTP | Prometheus (custom exporter) | 10s | Aucun tableau de bord dédié aux codes 5xx.
| Base de données (PostgreSQL) | pg_exporter | 20s | Manque de visualisation du nombre de connexions actives vs max.

## 2. Lacunes principales

1. **Absence de métriques de télémétrie détaillées** : seules les métriques de base sont affichées.
2. **Pas de corrélation entre logs et métriques** : les panneaux ne permettent pas de naviguer du log d’erreur à la métrique correspondante.
3. **Manque de visualisation des seuils et alertes** : les seuils critiques ne sont pas affichés ni colorés.
4. **Pas de tableau de bord unifié** : chaque service possède son propre panneau, rendant la vue d’ensemble difficile.
5. **Télémétrie côté client** : aucune collecte de métriques front‑end (temps de rendu, erreurs JS).

## 3. Recommandations pour l'intégration de la télémétrie

### 3.1. Centraliser la collecte avec **OpenTelemetry**
- Déployer l'agent OpenTelemetry Collector sur chaque nœud.
- Configurer les receivers : `prometheus`, `otlp`, `jaeger`.
- Exporter les traces et métriques vers **Prometheus** (metrics) et **Jaeger** (traces).
- Utiliser l'**exporter OTLP** vers Grafana Cloud ou un backend Grafana Loki pour les logs.

### 3.2. Enrichir les métriques existantes
- Ajouter des **histogrammes** pour la latence des requêtes HTTP (`http_request_duration_seconds`).
- Créer des **counters** pour les codes d’erreur 4xx/5xx.
- Exposer des **gauges** pour le nombre de connexions DB et le taux d’utilisation du pool.
- Mettre en place des **metrics de santé** (`up`, `process_cpu_seconds_total`).

### 3.3. Corrélation logs‑métriques
- Ajouter le **trace‑id** dans les logs (via OpenTelemetry SDK) afin de pouvoir relier un log d’erreur à une trace.
- Configurer Grafana Loki pour indexer le champ `trace_id`.
- Créer un panneau « Log » filtré par le `trace_id` sélectionné dans le tableau de bord de trace Jaeger.

### 3.4. Dashboard unifié
- Créer un **Dashboard « Observabilité Globale »** regroupant :
  - CPU / Mémoire
  - Latence moyenne & p95
  - Taux d’erreur HTTP
  - Connexions DB
  - Taux de requêtes côté client (via `web-vitals` ou `otlp` front‑end)
- Utiliser les **thresholds** de Grafana pour colorer les panneaux (vert < 70 %, orange 70‑85 %, rouge > 85 %).
- Ajouter des **variables** (`$service`, `$instance`) pour filtrer rapidement.

### 3.5. Télémétrie côté client (front‑end)
- Intégrer le **SDK OpenTelemetry JavaScript**.
- Capturer les métriques `longtask`, `navigation timing`, `resource load time`.
- Exporter via `OTLP` vers le collector déjà déployé.
- Visualiser dans Grafana via le datasource **Prometheus** (ex. `http_client_duration_seconds`).

### 3.6. Alerting
- Configurer des **alertes Grafana** basées sur les thresholds définis ci‑dessus.
- Envoyer les alertes vers Slack/Teams et créer des tickets automatisés via un webhook.
- Utiliser les **silences** pour les périodes de maintenance.

## 4. Plan d’implémentation (sprints)
| Sprint | Action | Responsable |
|--------|--------|--------------|
| 1 | Déployer OpenTelemetry Collector, configurer receivers OTLP/Prometheus. | Infra / SRE |
| 2 | Instrumenter les services back‑end avec le SDK OpenTelemetry (metrics + traces). | Dév back‑end |
| 3 | Ajouter les métriques manquantes (latence, erreurs, DB). | Dév back‑end |
| 4 | Intégrer le SDK JavaScript dans le front‑end, exporter les métriques. | Dév front‑end |
| 5 | Créer le dashboard unifié et configurer les seuils/alertes. | SRE / DevOps |
| 6 | Documentation et formation des équipes sur l’observabilité. | Docs / PM |

## 5. Conclusion
L’intégration d’OpenTelemetry permet de **centraliser** la collecte de métriques, traces et logs, facilitant la corrélation et le diagnostic. En enrichissant les panneaux existants et en créant un tableau de bord unifié, les équipes gagneront en visibilité sur la santé du système et pourront réagir plus rapidement aux incidents. Le plan d’implémentation en plusieurs sprints assure une adoption progressive sans interruption de service.

---
*Document généré automatiquement par l’assistant IA.*