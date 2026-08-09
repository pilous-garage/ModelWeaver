# Audit et Recommandations : Intégration de la Télémétrie dans le Dashboard

Ce document compile les conclusions de l'audit des panneaux de monitoring et les recommandations stratégiques pour l'unification de la télémétrie.

## 1. État des Lieux (Audit)

L'écosystème actuel souffre d'une fragmentation des outils de monitoring, rendant le diagnostic complexe :

*   **Prometheus/Grafana** : Focalisé sur les métriques système.
*   **Elastic/Kibana** : Focalisé sur les logs, mais isolé des métriques.
*   **Sentry** : Silo pour les erreurs.
*   **Custom Dashboard** : Métriques métier sans standardisation (OpenTelemetry).

**Problématique majeure** : Absence de corrélation native entre les logs, les traces et les métriques.

## 2. Recommandations Stratégiques

Pour moderniser l'observabilité, nous recommandons le passage à un standard unifié :

1.  **Adoption d'OpenTelemetry (OTel)** : Standardiser l'instrumentation.
2.  **Architecture de Collecte** : Déploiement d'un *OTel Collector* pour agréger et router les données vers le backend (Prometheus, Loki, Tempo).
3.  **Corrélation Corrélation** : Injection systématique du `trace_id` dans les logs et métriques pour permettre le "drill-down" dans Grafana.
4.  **Dashboard Unifié** : Configuration de vues Grafana combinant métriques (d'état) et traces (d'analyse).
5.  **Alerting** : Centralisation des alertes avec contexte (liens vers les traces/logs).

## 3. Plan d'action

| Étape | Priorité | Description |
|---|---|---|
| 1 | Haute | Déploiement du *OTel Collector*. |
| 2 | Haute | Instrumentation des services critiques avec OTel. |
| 3 | Moyenne | Mise en place de la corrélation métrique/log/trace. |
| 4 | Moyenne | Création des dashboards Grafana unifiés. |
| 5 | Basse | Automatisation du déploiement via CI/CD. |

---
*Document généré le 2025-05-22 pour le projet mw-swarm.*
