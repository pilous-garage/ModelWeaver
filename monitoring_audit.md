# Audit des panneaux de monitoring

## Contexte
Le tableau de bord existant collecte déjà plusieurs métriques via Prometheus et Grafana. Cependant, les panneaux de monitoring actuels présentent des lacunes en termes de couverture, de clarté et d’intégration de la télémétrie temps réel.

## Analyse des panneaux existants
| Panneau | Métrique(s) surveillée(s) | Couverture | Points faibles |
|---------|--------------------------|------------|----------------|
| CPU Usage | `node_cpu_seconds_total` | Partielle – seules les instances Linux sont couvertes | Pas de métriques pour containers Docker, manque de granularité par core |
| Mémoire | `node_memory_MemAvailable_bytes` | Bonne | Aucun seuil d’alerte, pas de suivi de la fragmentation |
| Latence API | `http_request_duration_seconds` | Incomplète – uniquement GET, pas de POST/PUT/DELETE | Aucun découpage par endpoint, pas de suivi des codes d’erreur |
| Erreurs 5xx | `http_response_status_total` | Partielle – agrégée sur toutes les instances | Pas de corrélation avec les logs, pas de drill‑down par service |
| Queue Length | `rabbitmq_queue_messages_ready` | Absente | Aucun suivi des files d’attente, risque de saturation non détecté |

## Recommandations d’amélioration
1. **Étendre la couverture des métriques**
   - Ajouter les métriques Docker (`container_cpu_user_seconds_total`, `container_memory_usage_bytes`).
   - Instrumenter les endpoints POST/PUT/DELETE avec des histogrammes de latence.
2. **Standardiser les panneaux**
   - Utiliser des panneaux de type *Stat* pour les seuils critiques (ex. CPU > 80 %).
   - Appliquer des *thresholds* et des *alert rules* directement dans Grafana.
3. **Intégrer la télémétrie temps réel**
   - Mettre en place un WebSocket ou Server‑Sent Events (SSE) depuis le backend vers le dashboard React.
   - Utiliser `react-use-websocket` ou `swr` avec polling fallback.
4. **Corrélation logs‑metrics**
   - Exporter les logs vers Loki et créer des panels de recherche de logs liés aux métriques d’erreur.
5. **Dashboard modularité**
   - Séparer les panneaux critiques (CPU, Mémoire, Erreurs) dans un *overview*.
   - Créer des sous‑dashboards pour chaque micro‑service.
6. **Documentation & gouvernance**
   - Ajouter un fichier `README.md` décrivant chaque panneau, les métriques sources et les seuils.
   - Mettre en place un processus de revue lors de l’ajout de nouveaux panneaux.

## Plan d’intégration de la télémétrie
1. **Backend**
   - Ajouter un endpoint `/telemetry/stream` qui expose les métriques au format JSON via SSE.
   - Utiliser `prom-client` (Node.js) ou `micrometer` (Java) pour exporter les nouvelles métriques.
2. **Frontend**
   - Créer un hook `useTelemetry` qui consomme le stream et met à jour le state.
   - Mapper les données du stream aux panneaux existants grâce à un *context* React.
3. **CI/CD**
   - Ajouter des tests unitaires pour vérifier que les nouvelles métriques sont bien exposées.
   - Mettre à jour le pipeline GitHub Actions pour exécuter les tests et publier le dashboard.

## Livrables attendus
- Nouveau fichier `monitoring_audit.md` (document présent).
- Mise à jour du tableau de bord (`src/dashboard/TelemetryDashboard.jsx`).
- Scripts d’instrumentation supplémentaires dans `src/telemetry/`.
- Documentation dans `README.md`.

---
*Ce document doit être revu par l’équipe d’ingénierie avant implémentation.*
