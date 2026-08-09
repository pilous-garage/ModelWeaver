# Audit des panneaux de monitoring et recommandations pour l'intégration de la télémétrie

## 1. Contexte
Le projet **mw-swarm** possède un tableau de bord existant qui affiche les métriques essentielles du système. L'objectif est d'évaluer les panneaux de monitoring actuels et de fournir des recommandations afin d'intégrer efficacement la télémétrie dans le dashboard.

## 2. Audit des panneaux existants
| Panneau | Métrique affichée | Source de données | Fréquence de rafraîchissement | Observations |
|---------|-------------------|-------------------|------------------------------|--------------|
| CPU Usage | Utilisation CPU (%) | Prometheus `node_cpu_seconds_total` | 15s | Affichage correct, mais manque de seuils visuels. |
| Memory | Mémoire utilisée (GB) | Prometheus `node_memory_MemTotal_bytes` & `node_memory_MemAvailable_bytes` | 15s | Les graphiques sont peu lisibles pour les pics courts. |
| Requests per sec | RPS du service API | Grafana Loki query | 30s | Pas de corrélation avec les erreurs 5xx. |
| Error Rate | % d'erreurs 5xx | Prometheus `http_requests_total` (code>=500) | 30s | Aucun seuil d'alerte configuré. |
| Queue Length | Taille de la file RabbitMQ | RabbitMQ Management API | 10s | Les valeurs ne sont pas normalisées. |

## 3. Points d'amélioration
1. **Uniformiser la fréquence de rafraîchissement** – choisir une fréquence commune (ex. 15 s) pour éviter des incohérences visuelles.
2. **Ajouter des seuils et des couleurs** – utiliser les fonctions d'alerte de Grafana (thresholds) pour mettre en évidence les dépassements (ex. CPU > 80 %).
3. **Normaliser les unités** – afficher les tailles en Mo/Go avec un formatage cohérent.
4. **Corrélation des métriques** – créer des panneaux combinés (ex. RPS vs Error Rate) pour identifier rapidement les causes d’erreurs.
5. **Documentation** – chaque panneau doit comporter une description courte et un lien vers la métrique source.

## 4. Recommandations d'intégration de la télémétrie
### 4.1 Architecture
- **Collector** : Utiliser *OpenTelemetry Collector* comme point d'entrée unique pour les traces, métriques et logs.
- **Exporters** : Configurer les exporters Prometheus (métriques) et Jaeger (traces) afin de les consommer dans le dashboard Grafana.
- **Sidecars** : Déployer des sidecars OpenTelemetry dans chaque micro‑service pour exporter automatiquement les données.

### 4.2 Implémentation côté code
1. **Instrumenter les services**
   ```python
   from opentelemetry import trace, metrics
   from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
   # Initialise le tracer et le meter
   tracer = trace.get_tracer(__name__)
   meter = metrics.get_meter(__name__)
   # Exemple de compteur de requêtes
   request_counter = meter.create_counter(
       name="http_requests_total",
       description="Nombre total de requêtes HTTP",
       unit="1",
   )
   ```
2. **Enrichir les traces** – ajouter des attributs clés (service.name, request.id, user.id) pour faciliter le filtrage dans le dashboard.
3. **Exporter les métriques** – configurer le `OTLPMetricExporter` vers le collector.

### 4.3 Dashboard
- Créer un nouveau tableau de bord **Telemetry Overview** avec les panneaux suivants :
  1. **Latency distribution** (histogramme) – trace latence des appels API.
  2. **Error rate by service** (heatmap) – erreurs 5xx.
  3. **Throughput** – séries temporelles de RPS.
  4. **Resource utilisation** – CPU/Mémoire par pod/container.
- Utiliser les variables Grafana (`$service`, `$region`) pour rendre le tableau de bord dynamique.

## 5. Plan d'action
| Étape | Action | Responsable | Deadline |
|-------|--------|--------------|----------|
| 1 | Déployer OpenTelemetry Collector (Docker) | DevOps | 2024‑09‑15 |
| 2 | Ajouter les SDK OpenTelemetry aux micro‑services | Équipe Backend | 2024‑09‑30 |
| 3 | Créer le tableau de bord *Telemetry Overview* dans Grafana | Équipe Front | 2024‑10‑05 |
| 4 | Mettre à jour la documentation du dashboard | Docs Team | 2024‑10‑07 |
| 5 | Configurer les alertes (thresholds) | SRE | 2024‑10‑10 |

---
*Ce document a été généré automatiquement pour guider l'intégration de la télémétrie dans le projet mw‑swarm.*