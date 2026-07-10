# Analyse Critique du Projet ModelWeaver - Gemma-4 Edition

Cette analyse est une revue technique objective de l'état actuel du projet, visant à identifier les zones de risque et les leviers d'optimisation.

---

## 🛡️ 1. Analyse de la Sécurité (Dangers)

### 🚨 Critique : Gestion des secrets
* **État actuel** : Les clés API sont chiffrées (V0.6), mais la clé de chiffrement (`MASTER_KEY`) est stockée dans le `.env`. 
* **Risque** : Si le fichier `.env` est compromis, tout le coffre-fort tombe.
* **Solution** : Implémenter un système de mot de passe maître (Master Password) qui dérive une clé via PBKDF2, de sorte que la clé de chiffrement n'existe jamais sur le disque en clair.

### 🚨 Critique : Exécution de commandes Shell
* **État actuel** : Utilisation de `shell=True` dans certains modules (`installer`, `pipeline_executor`).
* **Risque** : Injection de commandes via des fichiers de recettes malveillants.
* **Solution** : Migrer systématiquement vers `shell=False` en passant des listes d'arguments, ou implémenter un parseur de commandes qui sandbox les caractères spéciaux.

### 🚨 Critique : Validité des endpoints
* **État actuel** : Les URLs des fournisseurs sont stockées telles quelles.
* **Risque** : Injection de URL malveillantes ou de payloads lors de l'appel aux proxies.
* **Solution** : Validation stricte des schémas d'URL et des types de contenus (Content-Type) via Pydantic.

---

## ⚙️ 2. Analyse de la Stabilité & Maintenance (Dette Technique)

### ⚠️ Risque : Monolithe de logique (Worker/Ticker)
* **État actuel** : Les modules `worker.py` et `ticker.py` gèrent trop de responsabilités (hydratation, exécution, déshydratation, gestion d'erreurs).
* **Impact** : Difficulté de test unitaire et risque de régressions lors de refactorings.
* **Solution** : Découper en micro-services ou modules spécialisés : un `Hydrator`, un `ExecutionEngine`, et un `StateManager`.

### ⚠️ Risque : Schéma BDD évolutif
* **État actuel** : Utilisation de migrations manuelles (ALTER TABLE).
* **Impact** : Difficulté de synchroniser la structure de la base entre les environnements de test et de prod.
* **Solution** : Intégrer un gestionnaire de migrations professionnel (type `Alembic`).

### ⚠️ Risque : Couverture de tests
* **État actuel** : Tests existants principalement sur le bootstrap et l'installation.
* **Impact** : Risque élevé de régression sur la logique complexe d'orchestration (DSL).
* **Solution** : Implémenter des tests de propriété (Property-based testing) pour le DSL et des tests E2E via Playwright pour la GUI.

---

## 🚀 3. Analyse de la Performance (Optimisations)

### ⚡ Optimisation : Latence des appels API
* **État actuel** : Appels HTTP synchrones dans le coeur de l'orchestration.
* **Impact** : Le Ticker peut être bloqué par un timeout réseau, ralentissant tous les autres agents.
* **Solution** : Passage complet à l'asynchrone (`asyncio` + `aiohttp`) pour tous les modules de communication (Plumber, Worker).

### ⚡ Optimisation : Accès aux données
* **État actuel** : Requêtes directes à la DB pour chaque action d'agent.
* **Impact : ** Surcharge inutile de SQLite lors de pics d'activité.
* **Solution** : Implémenter un cache de lecture (Read-Through Cache) en mémoire pour les données fréquemment consultées (Catalogue, Rôles).

---

## 🛠️ 4. Méthodes Recommandées

1.  **Validation de données** : Adopter **Pydantic** comme norme pour toute donnée entrant dans le système (API, Fichiers YAML, BDD).
2.  **Architecture de communication** : Utiliser un pattern **Pub/Sub** ou une queue de messages pour découpler les agents (évite les appels directs trop rigides).
3.  **Développement** : Privilégier le **TDD (Test Driven Development)** sur les modules critiques (Installer, KeyManager, Plumber).
