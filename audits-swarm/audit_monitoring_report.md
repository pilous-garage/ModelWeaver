# Audit des panneaux de monitoring

## 1. État actuel

- Les fichiers `audit_monitoring.py`, `audit_monitoring_panel.py` et `audit_monitoring_panels.py` ne contiennent que des placeholders (`TODO`).
- Le module `modules/dashboard/dashboard.py` affiche uniquement un état système et catalogue, sans aucune métrique de télémétrie.
- Le projet ne possède pas de collecte de métriques (CPU, mémoire, latence) ni d’exposition via Prometheus / Grafana.

## 2. Recommandations

1. **Intégrer une collecte de métriques**
   - Ajouter un module `modules/telemetry/telemetry_collector.py` qui expose les métriques suivantes :
     - `cpu_usage_percent`
     - `memory_usage_gb`
     - `disk_usage_gb`
     - `http_request_latency_ms`
   - Utiliser `psutil` pour les métriques système et une instrumentation légère (ex: `prometheus_client` or custom counters).
2. **Exporter les métriques**
   - Créer un endpoint `/metrics` dans le service API (`services/api/handlers/monitoring.py`).
   - Si le projet utilise déjà un serveur HTTP (FastAPI, Flask), ajouter un routeur Prometheus.
3. **Afficher les métriques dans le dashboard**
   - Modifier `modules/dashboard/dashboard.py` pour afficher un tableau de bord avec les métriques collectées.
   - Utiliser `rich` ou `textual` pour un rendu console agréable.
4. **Automatiser la collecte**
   - Ajouter un cron job ou un thread daemon qui met à jour les métriques toutes les X secondes.
5. **Documentation**
   - Ajouter un README section "Monitoring" décrivant comment lancer le serveur métriques et visualiser via Grafana.

## 3. Implémentation rapide (demo)

```python
# modules/telemetry/telemetry_collector.py
import psutil
from prometheus_client import Gauge

cpu_gauge = Gauge('cpu_usage_percent', 'CPU usage')
mem_gauge = Gauge('memory_usage_gb', 'Memory usage')

def collect():
    cpu_gauge.set(psutil.cpu_percent())
    mem_gauge.set(psutil.virtual_memory().used / (1024 ** 3))
```

## 4. Etapes de mise en place

1. Créer le fichier `modules/telemetry/telemetry_collector.py`.
2. Ajouter l'export `/metrics` dans `services/api/handlers/monitoring.py`.
3. Mettre à jour `modules/dashboard/dashboard.py` pour afficher les métriques.
4. Commit et push sur `auto_code_412`.

---

**Remarque** : Ce rapport est destiné à guider les développeurs; les fichiers créés ci‑dessus sont un exemple de démarrage.
