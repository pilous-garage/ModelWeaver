# Audit des 6 panels monitoring

Cette audit décrit les panels suivants :
- **monitoring-llm-distant**
- **monitoring-processus**
- **monitoring-projets**
- **monitoring-workspace**
- **ressources**
- **systeme-ressources**

## 1. monitoring-llm-distant
- **id** : monitoring-llm-distant
- **version** : 1.2.0 (extrait de `monitoring-llm-distant.bundle.js`)
- **bundle** : monitoring-llm-distant-1.2.0.zip
- **rôle attendu** : afficher les métriques de latence et de disponibilité de l’API LLM distante.
- **rôle affiché** : le panel affiche correctement la latence mais ne montre pas l’état d’erreur quand l’API est hors service (fallback absent).

## 2. monitoring-processus
- **id** : monitoring-processus
- **version** : 1.1.3 (extrait du fichier `monitoring-processus.bundle.js`)
- **bundle** : monitoring-processus-1.1.3.zip
- **rôle attendu** : affichage des métriques CPU, mémoire et nombre de processus.
- **rôle affiché** : panel fonctionne mais rafraîchit trop souvent (sans throttling) entraînant surcharge réseau.

## 3. monitoring-projets
- **id** : monitoring-projets
- **version** : 1.0.4
- **bundle** : monitoring-projets-1.0.4.zip
- **rôle attendu** : afficher l’état des projets (build, tests, déploiement).
- **rôle affiché** : panel présente les bonnes informations mais n’indique pas les erreurs de build.

## 4. monitoring-workspace
- **id** : monitoring-workspace
- **version** : 2.0.1
- **bundle** : monitoring-workspace-2.0.1.zip
- **rôle attendu** : état du workspace (utilisation, espace disque, etc.).
- **rôle affiché** : panel fonctionne mais ne montre pas les alertes de dépassement de quota.

## 5. ressources
- **id** : ressources
- **version** : 1.3.0
- **bundle** : ressources-1.3.0.zip
- **rôle attendu** : affichage des ressources serveur utilisées par les agents.
- **rôle affiché** : panel affiche les métriques mais manque la visualisation de la consommation d’API.

## 6. systeme-ressources
- **id** : systeme-ressources
- **version** : 3.0.2
- **bundle** : systeme-ressources-3.0.2.zip
- **rôle attendu** : métriques système globales (CPU, mémoire, I/O, réseau).
- **rôle affiché** : panel fonctionne mais ne signale pas les pics de charge.

## Synthèse
- Tous les panels utilisent la même version du bundle (zip) mais leurs versions logiques diffèrent.
- Les rôles attendus sont majoritairement affichés, à l’exception de la gestion des erreurs et des alertes.
- Recommandations : ajouter un fallback pour `monitoring-llm-distant`, throttling pour `monitoring-processus`, et métriques d’erreur pour les autres panels.

---

**Date** : 2026-08-08T12:00:00Z