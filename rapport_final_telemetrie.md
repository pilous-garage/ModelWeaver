# Rapport Synthétique : Audit des Panneaux de Monitoring et Intégration de la Télémétrie

## 1. Synthèse de l'Audit (Monitoring Système)
Le module de monitoring actuel (`dashboard.py`) est robuste et bien intégré au système de `Checker` et `Catalogue`. Il offre une vue fiable des ressources système et de la disponibilité des modèles. Les recommandations précédentes soulignent l'importance de la persistance des logs et de l'alerting pour une meilleure résilience.

## 2. Plan d'Intégration de la Télémétrie
Pour enrichir le dashboard avec les données de télémétrie des fournisseurs LLM, nous préconisons l'architecture suivante :

### Architecture proposée
- **Couplage Loosely-coupled** : L'intégration se fera par injection de dépendance de l'instance `TelemetryCollector` dans le `Dashboard`.
- **Exposition des données** :
  - **Latence** : Suivi en temps réel (moyenne glissante).
  - **Fiabilité** : Taux de succès/échec des requêtes.
  - **État de santé** : Indicateur binaire (`is_up`) pour chaque fournisseur.

### Étapes d'implémentation
1. **Mise à jour du constructeur** : Intégrer `telemetry_collector` dans `Dashboard`.
2. **Dashboard UI** : Création d'un panneau `[Provider Metrics]` dans la sortie console du dashboard.
3. **Logique de rafraîchissement** : Inclure le rafraîchissement des métriques dans la `monitor_loop` existante.

## 3. Conclusion
L'intégration de la télémétrie dans le dashboard est une étape critique pour passer d'un simple monitoring système à une surveillance applicative complète. Cela permettra une corrélation directe entre les ressources serveur et la qualité de service des modèles LLM.
