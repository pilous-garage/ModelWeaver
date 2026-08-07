# Audit des panneaux de monitoring

## 1. État actuel des panneaux de monitoring

- **Panneaux existants** : liste des panneaux actuellement déployés dans le dashboard (ex. CPU usage, Memory, Disk I/O, Network latency).
- **Sources de données** : Prometheus, Grafana Loki, ElasticSearch, etc.
- **Fréquence de rafraîchissement** : 30s, 1min, etc.
- **Qualité des métriques** : précision, agrégation, tags manquants.
- **Sécurité** : accès RBAC, chiffrement des flux.

## 2. Problèmes identifiés

| Problème | Impact | Priorité |
|----------|--------|----------|
| Redondance de panneaux (CPU et Load Average) | Confusion, surcharge visuelle | Medium |
| Métriques critiques manquantes (e.g., error rate des services) | Blind spots | High |
| Temps de latence de rafraîchissement trop long pour les alertes critiques | Délai de réaction | High |
| Absence de labels standardisés (env, service, version) | Difficulté de corrélation | Medium |
| Pas de suivi de la télémétrie des logs côté front | Visibilité limitée | Low |

## 3. Recommandations

### 3.1 Consolidation des panneaux
- Fusionner les panneaux redondants en un seul tableau de bord "Performance serveur".
- Utiliser des variables de tableau de bord (e.g., `$instance`, `$service`) pour rendre les panneaux réutilisables.

### 3.2 Ajout de métriques critiques
- **Error rate** : `rate(http_requests_total{status=~"5.."}[1m])`
- **Latency percentiles** : `histogram_quantile(0.95, request_duration_seconds_bucket{service="{{service}}"})`
- **Taux de saturation CPU** : `100 - (avg by (instance) (irate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)`

### 3.3 Optimisation du rafraîchissement
- Configurer les panneaux critiques à 10 s de rafraîchissement, les autres à 30 s.
- Activer le mode *live tail* pour les logs critiques via Loki.

### 3.4 Standardisation des labels
- Appliquer un schéma de tags commun : `env`, `service`, `region`, `version`.
- Mettre à jour les jobs Prometheus pour exporter ces labels.

### 3.5 Intégration de la télémétrie front‑end
- Instrumenter le front avec **OpenTelemetry JS** pour capturer les traces et les métriques (page load time, API latency, UI error count).
- Exporter les données vers le même backend Prometheus via le **OTLP exporter**.
- Ajouter un panneau "Front‑end performance" affichant :
  - `web_vital_fcp_seconds`
  - `web_vital_lcp_seconds`
  - `api_request_duration_seconds`

### 3.6 Sécurité & gouvernance
- Restreindre l’accès aux tableaux de bord sensibles aux rôles `monitoring_viewer` et `monitoring_admin`.
- Activer le chiffrement TLS entre les agents de collecte et le serveur de métriques.

## 4. Plan de mise en œuvre

| Étape | Action | Responsable | Durée estimée |
|-------|--------|--------------|----------------|
| 1 | Audit du fichier de configuration Grafana (datasources, dashboards) | DevOps | 1 jour |
| 2 | Ajout des métriques manquantes dans les jobs Prometheus | SRE | 2 jours |
| 3 | Déploiement des panneaux consolidés (JSON) via CI/CD | DevOps | 1 jour |
| 4 | Instrumentation front‑end avec OpenTelemetry | Front‑end team | 3 jours |
| 5 | Tests de charge et validation des temps de rafraîchissement | SRE | 1 jour |
| 6 | Documentation & formation des équipes | Docs team | 0.5 jour |

## 5. Livrables
- Fichier `dashboard_monitoring.json` contenant les nouveaux panneaux.
- Scripts d’instrumentation OpenTelemetry (ex. `otel_init.js`).
- Documentation mise à jour dans le repo (`docs/monitoring_audit.md`).

---
*Ce document a été généré pour guider l’amélioration du système de monitoring et l’intégration de la télémétrie dans le tableau de bord existant.*
