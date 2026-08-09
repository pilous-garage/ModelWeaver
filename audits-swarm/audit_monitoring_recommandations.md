# Audit des panneaux de monitoring

## Contexte
Les panneaux de monitoring actuels (Grafana, Prometheus, Loki, Alertmanager) sont configurés pour collecter, visualiser et alerter sur les métriques de l’infrastructure. Cependant, ils ne sont pas encore intégrés dans le dashboard principal de l’équipe, ce qui limite la visibilité et la prise de décision.

## Points d’audit
1. **Collecte** – Sources de métriques (node_exporter, cAdvisor, custom exporters) sont correctement configurées.
2. **Visualisation** – Dashboards existants couvrent la plupart des services mais manquent d’une vue globale.
3. **Alertes** – Réglages d’alerte adéquats, mais absence de corrélations.
4. **Sécurité** – Accès public aux panels, besoin de mise en place d’authentification.
5. **Performance** – Temps de requête PromQL acceptable, mais indexage Loki non optimisé.

## Recommandations
| Domaine | Action | Priorité |
|---|---|---|
| Intégration dashboard | Créer un dashboard global “Monitoring & Telemetry” | Haute |
| Centralisation | Utiliser un Prometheus remote_write vers un stockage central | Haute |
| Alertes | Ajouter des alertes de corrélation (ex: CPU > 80% + Memory > 70%) | Moyenne |
| Sécurité | Mettre en place OAuth2 via Grafana | Haute |
| Performance | Activer les règles de retention Loki, compresser logs | Moyenne |
| Documentation | Mettre à jour README avec lien vers dashboard | Basse |

## Étapes concrètes
1. **Dashboard** – Copier le template `dashboard_global.json`, adapter les requêtes, placer dans `workplace/mw-swarm/docs/dashboards`.
2. **Prometheus** – Modifier `prometheus.yml` pour ajouter `remote_write` vers `remote_write_endpoint`.
3. **Grafana** – Configurer `grafana.ini` pour OAuth2, créer un datasource `Prometheus` et un datasource `Loki`.
4. **Alertmanager** – Définir un fichier `alertmanager.yml` avec des routes de corrélation.
5. **Tests** – Vérifier les dashboards via `curl` et l’API Grafana.

---
**Ressources**
- Prometheus docs: https://prometheus.io/docs/introduction/overview/
- Grafana docs: https://grafana.com/docs/grafana/latest/
- Loki docs: https://grafana.com/docs/loki/latest/
