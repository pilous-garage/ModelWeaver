# Règles de Programmation - ModelWeaver V0.2

Ce document définit les protocoles de développement, de sécurité et d'exécution pour le projet ModelWeaver.

## 🏗️ Structure & Organisation
- **Modularité stricte** : L'implémentation de chaque module doit se faire exclusivement dans son dossier dédié (ex: `/catalogue`, `/key_manager`, `/checker`, etc.).
- **Isolation de la V0.1** : Ne jamais modifier ou supprimer les fichiers de la version V0.1. Toute nouvelle fonctionnalité ou refonte doit être isolée dans les nouveaux modules.
- **Conception propre** : Utilisation systématique de `pathlib` pour la gestion des chemins et isolation stricte des commandes système.

## 🛠️ Installation & Découverte
- **Installation complète** : Toujours installer l'intégralité des clés et des modèles disponibles.
- **Protocole de connectivité (Vérification)** :
    - **HuggingFace & OpenCode Zen** : Un seul test de connectivité pour l'ensemble du fournisseur. 
        - *Note : OpenCode Zen peut répondre erronément pendant ~15h, c'est un comportement attendu.*
    - **Autres fournisseurs** : Tests multiples autorisés sur tous les modèles.
    - **Anti-Spam** : Ne jamais tester le **même modèle** plus de 2 fois en moins de 10 secondes.

## 🚀 Exécution & Parallélisation
- **Parallélisation multi-fournisseurs** : Autorisée (ex: un modèle OpenAI et un modèle Groq en même temps).
- **Parallélisation intra-fournisseur** : **INTERDITE**. Un seul modèle par fournisseur à la fois.

## 🤖 Délégation d'Agents (Swarm/Pipelines)
- **Interdiction de délégation** : Ne jamais déléguer de tâches à des modèles dont le fournisseur est **HuggingFace**, **OpenCode** ou **Google-Gemini**.
- **Délégation autorisée** : Autorisée pour tous les autres fournisseurs.
- **Cas particulier Google** : La délégation est **autorisée** pour les modèles Google s'ils sont fournis par un **autre fournisseur** (ex: via OpenRouter).

## 📝 Protocole de Prompt (Contexte)
- **Contexte obligatoire** : À chaque prompt, l'agent doit impérativement avoir accès à :
    1. `programming_rules.md` (les présentes règles).
    2. `VERSIONS.md` (l'état de la version et la roadmap).
    3. Tout autre fichier technique pertinent pour la tâche en cours.
