# Recommandations pour l'Intégration de la Télémétrie dans le Dashboard

## Contexte
Le projet `mw-swarm` dispose d'un module de télémétrie (`modules/telemetry`) capable de collecter des métriques sur les fournisseurs LLM. Le `Dashboard` actuel (`modules/dashboard/dashboard.py`) se concentre uniquement sur l'état système et le catalogue de modèles.

## Recommandations d'Intégration

Pour améliorer la visibilité sur les performances des fournisseurs, nous recommandons les étapes suivantes :

1.  **Injection de dépendance** : Modifier le constructeur de `Dashboard` pour accepter une instance de `TelemetryCollector`.
2.  **Affichage des métriques** : Ajouter une section `[Provider Metrics]` dans la méthode `show_status` du `Dashboard`.
3.  **Visualisation** :
    *   Afficher la latence moyenne (`latency_ms`).
    *   Afficher le taux d'erreur (`error_rate`).
    *   Indiquer l'état de santé (`is_up`).

## Exemple de modification (Pseudo-code)

```python
# Dans Dashboard.__init__
def __init__(self, checker, catalogue, telemetry_collector):
    self.telemetry = telemetry_collector

# Dans Dashboard.show_status
print("\n[Provider Metrics]")
metrics = self.telemetry.get_all_metrics()
for name, m in metrics.items():
    status = "🟢" if m.is_up else "🔴"
    print(f"{status} {name}: {m.latency_ms}ms, Error: {m.error_rate*100}%")
```

## Conclusion
Cette intégration permettra aux administrateurs de corréler rapidement l'état du système avec la santé des services LLM externes, améliorant ainsi la réactivité face aux incidents.
