# 🛠 Projet : ModelWeaver

## 1. Philosophie & Charte (Règles d'Or)
- **Citoyenneté Numérique** : ModelWeaver est un outil de confort respectueux de l'écosystème. Il utilise les API officielles et ne cherche pas à contourner les mécanismes de sécurité ou de facturation.
- **Respect des Fournisseurs** : Interdiction formelle du "scraping" non autorisé. Les limites de débit (rate limits) sont traitées avec respect pour éviter tout blacklistage.
- **Architecture Découplée** : Chaque brique est interchangeable. Le cœur de ModelWeaver ne doit pas dépendre d'une implémentation spécifique.
- **Maximisation de l'Offre** : Hiérarchisation des requêtes (basique/local vs complexe/API) pour maximiser le confort et le budget.
- **OpenCode first** : OpenCode est le point d'accès principal pour toutes les LLM. Les autres composants sont des backends.

### Compatibilité système

| Composant | Linux | macOS | Windows | Android |
|-----------|-------|-------|---------|---------|
| **modelweaver.sh** | ✅ natif | ⚠️ Bash dispo | ❌ PowerShell nécessaire | ❌ |
| **Python** | ✅ | ✅ | ✅ | ✅ (Termux) |
| **Ollama** (optionnel) | ✅ | ✅ | ✅ | ⚠️ non officiel |
| **LiteLLM** | ✅ | ✅ | ✅ | ✅ |
| **OpenCode** | ✅ | ✅ | ✅ | ✅ |
| **Open WebUI** (optionnel) | ✅ | ✅ | ✅ | ✅ |

**Le vrai blocage multi-système** : `modelweaver.sh` est du Bash. Le cœur Python est déjà cross-platform. Pour Windows et Android, il faudra un équivalent PowerShell ou un rewrite en Python pur.

## 2. Roadmap Technique
### V0.1 (Le Socle) — Pipeline

1. **V0.1.0** — Structure de base (fait)
2. **V0.1.1** — Installation des composants
   - **V0.1.1.1** — Ollama (binaire, MIT) **optionnel** (auto-désactivable si RAM < 8 Go)
   - **V0.1.1.2** — LiteLLM (python, MIT)
   - **V0.1.1.3** — OpenCode (python, MIT)
   - **V0.1.1.4** — Open WebUI (python, MIT) **optionnel**
3. **V0.1.2** — Linkage
4. **V0.1.3** — Gestion du Fallback
5. **V0.1.4** — Interface (CLI enrichie + base GUI)
6. **V0.1.5** — Configuration API

*Détails dans `VERSIONS.md`.*

### V0.2+ (Pool d'idées)
Tout ce qui n'est pas dans la V0.1 va dans le pool **V0.2+**. Quand on définira les limites de V0.2, on pioche dans ce pool. Une fois les limites posées, on n'ajoute rien qui ne soit pas "essentiel logiquement" (dépendance découverte en cours d'implémentation).

- Miroir des dépendances
- Analyseur de complexité (Router intelligent)
- Auto-amélioration
- Logs mythologiques, conversation archiver
- **Multi-système** : support Windows, macOS, Android (très long terme)

## 3. Architecture & Système d'Installation

### Principe : OpenCode first

OpenCode est le point d'accès prédominant pour toutes les LLM. L'utilisateur interagit avec les modèles via OpenCode (CLI agent de code). Ollama, LiteLLM, et les APIs cloud sont des backends invisibles — ils tournent en arrière-plan, OpenCode est la face visible.

### Architecture en deux phases

**Phase 1 — `modelweaver.sh` (bootstrap, ~30 lignes de sh)**
- Vérifie Python >= 3.10, l'installe si absent (apt/brew/dnf/pacman)
- Lance `modelweaver.py`

**Phase 2 — `modelweaver.py` (cœur, cross-platform)**
- Mode YES/NO/ASK
- Audit complet (Python, RAM, OS, dépendances)
- Lecture du manifeste, installation des composants
- Préparé pour GUI plus tard

### Fonctionnement
1.  **Audit** : Vérifie Python, RAM, OS, dépendances système
2.  **Manifeste** : Lit `manifest.json` pour les composants
3.  **Isolation** : Utilise des `venv` dédiés pour chaque brique
4.  **Linkage** : Injecte les paramètres de connexion

### Structure du Manifeste (`manifest.json`)
```json
{
  "version": "1.0",
  "components": {
    "engine": { "name": "Ollama", "type": "binary" },
    "bridge": { "name": "LiteLLM", "type": "python-module" },
    "agent": { "name": "OpenCode", "type": "python-module" },
    "interface": { "name": "Open WebUI", "type": "python-module" }
  }
}
```

## 4. État courant du bridge

- La commande `opencode` reste directe et n’est plus remplacée par le wrapper ModelWeaver.
- Le bridge est exposé sous `opencode-modelweaver` pour les usages explicites.
- Les appels passent par LiteLLM, produisent un plan de routage et enregistrent les rebonds dans `.modelweaver/route_trace.log`.
- Les erreurs d’authentification, de saturation et de timeout sont détectées comme des cas de fallback.

## 5. Règles de Développement

### Définition des versions
- **Les limites d'une version sont entièrement définies AVANT de commencer à coder.**
- **On n'élargit jamais le périmètre d'une version en cours** (restriction OK, extension → version suivante).
- **Parmi les limites obligatoires** : toute version peut imposer des refactors ou prérequis techniques. Par exemple, V0.2 impose le split common/install/orchestrator.
- **Une version est finie uniquement quand tout son périmètre est codé, testé, snapshoté, commité.**

### Déroulement d'une sous-version
1. Coder la fonctionnalité
2. Tester en Docker (modes check + auto)
3. Snapshot Docker du nouvel état
4. Commit (automatique, un par version)
5. Créer la branche de la version suivante

### Règles de commit
- **Commit après chaque modification de code.** Pas de code non versionné.
- Un commit par sous-version (sauf correctifs entre-temps).
- Messages clairs : `"V0.1.1.1 — Installation Ollama"`.

### Qualité
- **Échec explicite > succès silencieux.**
- **Idempotence** : exécuter deux fois = même résultat.
- **Pas de régression** : les snapshots Docker garantissent que N+1 ne casse pas N.
- **Notation Vn+** : les fonctionnalités repoussées sont notées `V0.2+`, jamais `V0.2` sans définition officielle.
