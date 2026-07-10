# Plan de Développement Stratégique ModelWeaver - Gemma-4 Edition

Ce document détaille la trajectoire de développement de ModelWeaver, décomposée en étapes logiques et méthodologiques.

---

## 🚀 V0.6 : Sécurisation et Abstraction des Modèles
**Objectif** : Passer d'un outil de déploiement à un système de gestion de secrets et de provisionnement LLM.

### 1. Gestionnaire de Clés (Key Vault)
#### [Sous-étape 1.1] Sécurisation du stockage
* **Problème** : Clés stockées en clair ou protégées par une clé unique en env.
* **Méthode** :
    * Implémenter un mécanisme de dérivation de clé (KDF) à partir d'un mot de passe utilisateur.
    * Utiliser l'algorithme AES-256-GCM pour l'intégrité et le chiffrement.
    * Stockage des vecteurs d'initialisation (IV) avec les données chiffrées.
#### [Sous-étape ---] Interface GUI du Vault
* **Méthode** : Créer un panneau de gestion des secrets avec masquage par défaut et validation de format.

### 2. Abstraction LLM (LLM Bridge)
#### [Sous-étape 2.1] Unification des Providers
* **Problème** : Code trop lié aux implémentations spécifiques (Ollama, OpenAI).
* **Méthode** : Créer une interface de classe abstraite `BaseProvider` (méthodes `chat`, `embed`, `list_models`).
* **Implémentation** : Créer des adaptateurs pour Ollama, LiteLLM, et les APIs natives.

### 3. Hardening du Système
#### [Sous-étape 3.1] Sandboxing des commandes
* **Méthème** : Éviter `shell=True`.
* **Méthode** : Implémenter un parseur de ligne de commande qui convertit les chaînes en listes d'arguments sécurisées et valide l'absence de caractères de contrôle.

---

## 🎨 V0.7 : Studio de Création (Roles & Agents)
**Objectif** : Permettre la conception de workflows complexes sans toucher au code.

### 1. Éditeur de Rôles (Low-Code)
#### [Sous-étape 1.1] Designer de Prompt
* **Méthode** : Interface de template avec injection de variables (ex: `{{context}}`, `{{task}}`).
#### [Sous-étape 1.2] Mapping de Capacités
* **Méthode** : Sélecteur visuel pour lier des outils (`tools`) et des contraintes de modèles à un rôle.

### 2. Factory d'Agents
#### [Sous-étape 2.1] Orchestrateur d'Instances
* **Méthode** : Interface pour instancier des agents (Mapping Rôle $\to$ Hardware $\to$ Provider).
#### [Sous-étape 2.2] Test de Santé (Health Check)
* **Méthode** : Système de "Ping" automatique pour vérifier qu'un agent peut réellement exécuter son rôle avant déploiement.

---

## 📊 V0.8 : Dashboard et Visualisation
**Objectif** : Monitoring temps réel et pilotage de l'orchestration.

### 1. Vue Graphique (Topology)
#### [Sous-étape 1.1] Visualiseur de Flux
* **Méthode** : Utiliser un moteur de graphe (ex: React Flow) pour visualiser les connections `Agent $\to$ Agent` ou `Agent $\to$ Queue`.
#### [Sous-étape 1.2] Éditeur de Pipeline
* **Méthode** : Permettre de modifier les liens de succession directement sur le graphe (Drag-and-drop).

### 2. Monitoring de Performance
#### [Sous-étape 2.1] Métriques en Temps Réel
* **Méthode** : Dashboard affichant l'usage CPU/RAM, le débit de tokens et le statut de l'agent (Idle/Busy).

---

## 🧪 V0.9 : Phase de Stress-Test (Efficacy Benchmark)
**Objectif** : Prouver la capacité d'autonomie du système.

### 1. Le Challenge "Zero-Budget"
#### [Sous-étape 1.1] Définition du Scénario
* **Méthode** : Choisir un projet complexe (ex: API FastAPI + Frontend React).
#### [Sous-étape 1.2] Configuration du Squad
* **Méthode** : Configurer un workflow multi-agents utilisant uniquement Ollama (local) ou des tiers gratuits.
#### [Sous-étape 1.3] Évaluation Automatisée
* **Méthode** : Mesurer le taux de succès, la qualité du code produit et le nombre d'interventions humaines nécessaires.

---

## 🪟 V0.10 : Portabilité Windows
**Objectif** : Rendre l'expérience utilisateur identique sur Windows.

### 1. Adaptation de l'Environnement
#### [Sous-étape 1.1] Gestionnaire de Paquets
* **Méthode** : Implémenter des drivers pour `winget` et `choco` dans le module `Installer`.
#### [Sous-étape 1.2] Abstraction Système
* **Méthode** : Utiliser systématiquement `pathlib` et remplacer les commandes Shell par des appels Python cross-platform.

---

## 🚀 V0.11 à V1.0 : Industrialisation et Lancement

### V0.11 : Hardening & Documentation
* **Méthode** : Audit de sécurité complet, documentation technique de l'API, et préparation des packages d'installation.

### V0.12 : Validation Multi-Machine
* **Méthode** : Tests de déploiement sur hardware hétérogène (Low-end vs High-end) pour valiser les modes de fonctionnement.

### V0.13 : Branding & UX
* **Méthode** : Refonte visuelle complète (Identity/Logo) et optimisation du tunnel utilisateur (One-click setup).

### V0.14 : Go-To-Market
* **Méthode** : Finalisation des licences, rédaction du README "Landing Page", et campagne de lancement (Reddit/YouTube).

### V1.0 : RELEASE STABLE
* **Méthode** : Publication de la version 1.0.0.
