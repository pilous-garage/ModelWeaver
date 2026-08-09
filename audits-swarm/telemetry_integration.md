# Audit et recommandations pour l'intégration de la télémétrie dans le dashboard

## 1. Contexte
- Le dashboard actuel (modules/dashboard/dashboard.py) expose 6 panels de monitoring (CPU, RAM, Disk, Network, Services, Logs).  
- Les panels utilisent `prometheus_client` via des endpoints internes et récupèrent les métriques en temps réel.
- La télémétrie supplémentaire (traces, logs distribués, métriques personnalisées) n'est pas encore couplée aux panels.

## 2. Observations clés
| Panel | Métrique principale | Source | Point de contact | Limites actuelles |
|-------|---------------------|--------|------------------|------------------|
| CPU | `process_cpu_seconds_total` | Prometheus | `/metrics/cpu` | Pas de graphe par conteneur |
| RAM | `process_resident_memory_bytes` | Prometheus | `/metrics/memory` | Un seul point global |
| Disk | `node_filesystem_usage` | Prometheus | `/metrics/disk` | Pas de partition détaillée |
| Network | `node_network_receive_bytes_total` + `node_network_transmit_bytes_total` | Prometheus | `/metrics/net` | Pas de latency |
| Services | `service_up` | Prometheus | `/metrics/services` | Aucun fallback en cas de panne |
| Logs | `log_lines_total` | Loki integration | `/logs` | Pas de filtre par niveau |

### Points de friction
- **Couplage faible** : chaque panel lit directement l'endpoint Prometheus; il n'y a pas de couche d'abstraction qui permet d'ajouter de nouvelles sources sans toucher le code du panel.  
- **Manque de visibilité contextuelle** : les métriques sont agrégées globalement, sans hiérarchie par service, conteneur ou équipe.  
- **Évolutivité** : l'ajout d'une métrique personnalisée nécessite une modification de plusieurs fichiers (routes, panels, tests).  
- **Observabilité complète** : traces, logs et métriques ne sont pas réunis dans un seul flux observable.

## 3. Recommandations
1. **Créer un service d'abstraction `TelemetryService`**
   - Interface unique `get_metrics(panel_name, filters)` qui renvoie un dictionnaire `{name: value, timestamp: ...}`.
   - Implémentations : `PrometheusTelemetry`, `LokiTelemetry`, `OpenTelemetry`.
   - Permet d'ajouter de nouvelles sources sans toucher aux panels.

2. **Introduire un modèle de données `MetricDefinition`**
   - Contient `name`, `type` (gauge, counter, histogram), `unit`, `aggregation` et `source`.
   - Stocké dans une configuration JSON/YAML (`config/telemetry.yml`).
   - Le panel lit cette configuration pour construire dynamiquement le graphe.

3. **Ajouter un cadre de filtrage basé sur `labels`**
   - Les panels devraient accepter des filtres (service, conteneur, équipe) via URL query ou un sélecteur UI.
   - Le `TelemetryService` applique ces filtres avant d'interroger Prometheus ou Loki.

4. **Intégrer OpenTelemetry traces**
   - Exposer un endpoint `/metrics/traces` qui agrège les traces (span count, latency, error rate).
   - Mettre à jour le panel `Traces` avec un graphe de latence par service.

5. **Unifier la visualisation**
   - Créer un composant `PanelContainer` qui orchestre le chargement et le refresh des panels.
   - Utiliser `react-query` ou `swr` pour le polling, afin de centraliser la logique de rafraîchissement.

6. **Automatiser les tests d'intégration**
   - Implémenter des tests Cypress/Playwright pour vérifier que chaque panel récupère bien les données de la source configurée.
   - Ajouter des tests unitaires sur `TelemetryService` pour chaque source.

7. **Documentation & onboarding**
   - Documenter la configuration `telemetry.yml` et le fonctionnement du `TelemetryService`.
   - Créer un guide « Add a new metric » pour les développeurs.

## 4. Plan de mise en œuvre
| Étape | Description | Responsable | Estimation |
|-------|--------------|------------|------------|
| 1 | Implémenter `TelemetryService` (Prometheus + Loki) | 414 | 1 semaine |
| 2 | Migrer les panels vers `MetricDefinition` | 414 | 2 jours |
| 3 | Ajouter filtre UI + backend | 414 | 1 semaine |
| 4 | Intégrer OpenTelemetry traces panel | 414 | 3 jours |
| 5 | Mettre à jour tests CI | 413 | 1 jour |
| 6 | Rédiger documentation | 414 | 2 jours |

## 5. Risques & mitigations
- **Régression API** : Versionner les endpoints de métriques (v1 → v2).  
- **Performance** : Mettre en cache les requêtes Prometheus via `prometheus_client`'s `CollectorRegistry`.  
- **Sécurité** : Restreindre l'accès aux métriques via OAuth2 / JWT.

## 6. Conclusion
En introduisant une couche d'abstraction, un modèle de données centralisé et en intégrant OpenTelemetry, le dashboard gagnera en modularité, en évolutivité et en observabilité complète. Ces changements permettront d'ajouter de nouvelles métriques et sources sans casser les panels existants et fourniront une vue unifiée pour les équipes.
