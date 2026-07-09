# Versions — Périmètres et Limites

## V0.1 (Terminée ✅)
... (Content preserved from backup)

## V0.2 (Terminée ✅) — Le Grand Split en Modules
... (Content preserved from backup)

## V0.3 (Terminée ✅) — Intégration SQLite Complète
... (Content preserved from backup)

## V0.4 (Terminée ✅) — Agent Factory & Orchestration
... (Content preserved from backup)

## V0.5 (Terminée ✅) — GUI Installateur & Catalogue Shardé
**Objectif** : Interface graphique d'installation et passage à une architecture de recettes atomiques (YAML) pour une scalabilité massive.

### Périmètre
- **UI Installateur** : Navigation dans le catalogue, installation/désinstallation d'outils et de modèles.
- **Architecture de Recettes** : Migration vers des fichiers `.mw.yaml` shardés (`/shard1/shard2/tool.mw/os/arch/manager.yaml`).
- **Métrologie** : Mesure automatique de l'espace disque et du poids de téléchargement via Docker.
- **Catalogue Distant** : Synchronisation avec un serveur Turso (libSQL) pour un catalogue communautaire.

### Sous-versions
- **V0.5.0 à V0.5.4** : Socle Tauri, bridge Rust/Python, détection des gestionnaires de paquets OS.
- **V0.5.5** : Implémentation du `RecipeParser` et passage au format YAML externe.
- **V0.5.6** : Interface d'ajout d'outils au catalogue.
- **V0.5.7** : CLI wrapper `modelweaver-install` et pipeline de mesure d'espace disque automatisé.

---

## V0.6 (Planifiée 📋) — Studio de Rôles & Sécurisation
**Objectif** : Interface visuelle pour composer les rôles et mise en sécurité critique des données.

### 1. Studio de Rôles (GUI)
- **Éditeur Visuel** : Composition de pipelines d'agents via blocks (drag-and-drop).
- **Bibliothèque** : Gestion des rôles prédéfinis et export/import YAML.
- **Prévisualisation** : Génération en temps réel du YAML du rôle.

### 2. Sécurisation & Hardening (Priorité ⭐⭐⭐⭐⭐)
- **Vault Chiffré** : Chiffrement AES-256 des clés API et tokens en BDD (Master Password).
- **Shell Sandboxing** : Remplacement des `shell=True` par des appels sécurisés et validation des endpoints.
- **Validation Pydantic** : Typage strict des données catalogue et agents pour éviter les crashs.

---

## V0.7 (Planifiée 📋) — Studio d'Agents & Validation
**Objectif** : Configuration visuelle des instances d'agents et tests de fonctionnement.

### 1. Définition d'Agent (GUI)
- **Configuration** : Liaison (Rôle $\to$ Modèle $\to$ Hardware).
- **Topologie** : Branchements visuels vers chatrooms, queues ou autres agents.
- **Déploiement** : Compilation de la config vers la BDD.

### 2. Validation Opérationnelle
- **Sandbox Testing** : Bouton "Test" pour vérifier la réponse d'un agent et l'usage de ses outils.
- **Log Trace** : Visualisation du raisonnement (Chain-of-Thought) en temps réel.

---

## V0.8 (Planifiée 📋) — Dashboard de Contrôle
**Objectif** : Tour de contrôle pour le monitoring et le pilotage des agents.

### 1. Monitoring Temps Réel
- **Agent Grid** : Statut visuel (IDLE, BUSY, ERROR) et usage CPU/RAM.
- **Unified Logs** : Flux de logs centralisé avec filtres par agent ou session.

### 2. Pilotage & Orchestration
- **Command Center** : Play/Stop/Restart global ou par agent.
- **Graph View** : Visualisation des dépendances et flux de messages entre agents.

---

## V0.9 (Planifiée 📋) — Benchmark & Test Complet
**Objectif** : Validation E2E et preuve de concept "Zéro Budget".

### 1. Le Challenge "Zero-Cost"
- **Scénario** : Création d'un projet logiciel complet (ex: FastAPI + React) sans intervention humaine.
- **Stack** : Exclusivement des modèles locaux (Ollama) ou free tiers.
- **Metrics** : Taux d'autonomie, qualité du code et cohérence inter-agents.

### 2. Validation E2E
- Tests complets : GUI Install $\to$ GUI Role $\to$ GUI Agent $\to$ Dashboard $\to$ Exécution.

---

## V0.10 (Planifiée 📋) — Adaptation Windows
- **OS Abstraction** : Migration des scripts bash vers Python/PowerShell.
- **Windows Build** : Optimisation Tauri pour Win10/11 et gestion des binaires `.exe`.

## V0.11 (Planifiée 📋) — Préparation Public Release
- **Code Hardening** : Gestion globale des exceptions, logging structuré et audit de secrets.
- **Documentation** : Guide contributeur, API Reference et Tutoriels.

## V0.12 (Planifiée 📋) — Beta Test & Debugging
- Tests sur machines variées (Low-end vs High-end) et OS multiples.

## V0.13 (Planifiée 📋) — Branding & UX
- Nouveau nom, nouvelle identité visuelle et refonte de l'expérience utilisateur (One-Click).

## V0.14 (Planifiée 📋) — Finalisation Légale & Marketing
- Définition de la licence, README professionnel et stratégie de lancement (Reddit, YT).

## V1.0 (Cible 🚀) — Publication Officielle
- Release stable, documentation complète et campagne de communication.
