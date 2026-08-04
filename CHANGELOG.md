# Changelog — ModelWeaver

## [Unreleased]

### Removed
- TODO #0.11 — Suppression de `~/.modelweaver/api.token`.
  - **Sécurité** : ce fichier stockait un jeton en clair sur le disque.
    Il est remplacé par le keyring OS / stockage chiffré via `modules/key_manager/key_manager.py`.
  - **Action** : supprimer `~/.modelweaver/api.token` avant la version 0.11.
  - **Référence** : voir `modules/key_manager/key_manager.py` pour le nouveau mécanisme de stockage sécurisé.
