"""Text Mode — Extraction d'actions depuis les réponses texte du LLM.

Quand un modèle ne supporte pas les tool_calls (function calling),
on lui demande de structurer sa réponse en texte lisible, puis on
extrait les actions via des patterns reconnus.

Supporte :
  - Blocs de code markdown (```python → write_file)
  - Blocs JSON structurés (```json → action structurée)
  - Commandes shell (```bash → shell_exec)
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple


# ── Patterns de détection ───────────────────────────────────

# Bloc de code : ```lang\n...\n```
CODE_BLOCK_RE = re.compile(r"```(\w+)?\n(.*?)```", re.DOTALL)

# Action JSON dans un bloc markdown : {"action": "...", ...}
JSON_ACTION_RE = re.compile(r"\{[^}]+\"action\"[^}]+\}", re.DOTALL)

# Action JSON en ligne (sans bloc) : {"action": "write_file", ...}
INLINE_JSON_ACTION_RE = re.compile(
    r'\{\s*"action"\s*:\s*"[^"]+"\s*.*?\}',
    re.DOTALL,
)

# Commande shell simple (dans un bloc bash)
SHELL_CMD_RE = re.compile(r"^[a-z][a-z0-9_\-]+\s", re.MULTILINE)


# ── Extraction ──────────────────────────────────────────────

def extract_actions(content: str, ws: str = "") -> List[Dict[str, Any]]:
    """Extrait les actions depuis une réponse texte du LLM.

    Parcourt les blocs de code markdown et les actions JSON, et
    retourne une liste d'actions structurées.

    Priorité : JSON > bash/shell > code python/etc.
    """
    actions = []

    # Passe 1 : JSON structuré (partout, pas seulement dans les blocs)
    actions.extend(_extract_json_actions(content))
    if actions:
        return actions

    # Passe 2 : parcourir les blocs de code markdown
    for match in CODE_BLOCK_RE.finditer(content):
        lang = (match.group(1) or "").strip().lower()
        body = match.group(2).strip()

        if not body:
            continue

        if lang in ("bash", "shell", "sh", "zsh", "powershell", "console"):
            actions.append({
                "action": "shell_exec",
                "command": body,
            })

        elif lang != "json":  # python, js, rust, html, css, txt...
            path = _extract_filename(body, lang)
            actions.append({
                "action": "write_file",
                "path": path,
                "content": body,
                "language": lang,
            })

    return actions


def _extract_json_actions(content: str) -> List[Dict[str, Any]]:
    """Extrait les actions depuis des blocs JSON.

    Format attendu :

    ```json
    {"action": "write_file", "path": "hello.py", "content": "print('hello')"}
    ```

    Ou en ligne :
    {"action": "shell_exec", "command": "git status"}
    """
    actions = []

    # Chercher les blocs JSON markdown
    for match in CODE_BLOCK_RE.finditer(content):
        lang = (match.group(1) or "").strip().lower()
        body = match.group(2).strip()
        if lang == "json" or lang == "jsonl":
            for line in body.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if "action" in data:
                        actions.append(data)
                except json.JSONDecodeError:
                    pass

    # Chercher les actions JSON inline sans bloc
    if not actions:
        for match in INLINE_JSON_ACTION_RE.finditer(content):
            try:
                data = json.loads(match.group())
                if "action" in data:
                    actions.append(data)
            except json.JSONDecodeError:
                pass

    return actions


def _extract_filename(body: str, lang: str) -> Optional[str]:
    """Tente d'extraire un nom de fichier depuis l'en-tête du code."""
    first_line = body.splitlines()[0].strip() if body.splitlines() else ""

    # Pattern: # chemin/vers/fichier.py ou // chemin/vers/fichier.js
    for prefix in ("# ", "// ", "/* ", "-- "):
        if first_line.startswith(prefix):
            candidate = first_line[len(prefix):].rstrip("*/").strip()
            # Nettoyer les caractères non-fichier
            if "/" in candidate or "." in candidate:
                return candidate

    return None


# ── Construction du prompt mode texte ───────────────────────

TEXT_MODE_INSTRUCTION = """
## Mode texte (pas de function calling)

Tu ne peux PAS utiliser d'outils. À la place, structure tes actions
dans des blocs de code :

### Écrire un fichier :
```python
# chemin/vers/fichier.py
print("Hello")
```

### Exécuter une commande :
```bash
git status
```

### Action JSON structurée (si spécifique) :
```json
{"action": "shell_exec", "command": "python hello.py"}
```

Ne mets qu'un seul fichier par bloc. Précise toujours le chemin
complet en commentaire sur la première ligne.
"""


def build_text_mode_prompt(system_prompt: str) -> str:
    """Ajoute les instructions mode texte au system prompt."""
    return system_prompt + TEXT_MODE_INSTRUCTION


# ── Exécution des actions extraites ────────────────────────

def execute_text_actions(
    actions: List[Dict[str, Any]],
    ws: str,
    dispatcher: callable,
) -> List[Dict[str, Any]]:
    """Exécute une liste d'actions extraites du texte.

    Chaque action est exécutée via le dispatcher comme si
    c'était un tool_call.

    Retourne les résultats.
    """
    results = []
    for action in actions:
        action_type = action.get("action", "")
        if action_type == "write_file":
            path = action.get("path", "")
            content = action.get("content", "")
            if not path:
                # Générer un nom à partir du langage
                lang = action.get("language", "txt")
                ext_map = {
                    "python": "py", "javascript": "js", "typescript": "ts",
                    "html": "html", "css": "css", "json": "json",
                    "yaml": "yaml", "yml": "yml", "markdown": "md",
                    "bash": "sh", "shell": "sh", "text": "txt",
                    "rust": "rs", "go": "go", "java": "java",
                    "c": "c", "cpp": "cpp", "h": "h",
                }
                ext = ext_map.get(lang, "txt")
                path = f"work/untitled.{ext}"
                action["path"] = path

            result = dispatcher("file_write_file_v1", {
                "path": path,
                "content": content,
            })
            result["_text_action"] = action
            results.append(result)

        elif action_type == "shell_exec":
            result = dispatcher("shell_exec_v1", {
                "command": action.get("command", ""),
            })
            result["_text_action"] = action
            results.append(result)

        elif action_type == "git_command":
            from AgentsCatalogue.lib.git.lite import exec as git_exec
            result = git_exec(action, ws)
            result["_text_action"] = action
            results.append(result)

        else:
            results.append({
                "ok": False,
                "error": f"Unknown text action: {action_type}",
                "_text_action": action,
            })

    return results


__all__ = [
    "extract_actions",
    "execute_text_actions",
    "build_text_mode_prompt",
    "TEXT_MODE_INSTRUCTION",
]
