"""Vérification et correction syntaxique de fichiers.

Utilise ruff (si disponible) pour lint + fix automatique,
avec fallback sur py_compile + auto-heal indentation.
"""

import os
import re
import subprocess
import sys
import tempfile
import traceback
from typing import List, Tuple


def _find_ruff() -> str:
    for cmd in ("ruff", "ruff.exe"):
        try:
            r = subprocess.run([cmd, "--version"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                return cmd
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return ""


def _find_tool(name: str) -> bool:
    try:
        r = subprocess.run([name, "--version"],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _compile_check(path: str) -> Tuple[List[dict], bool]:
    """compile() → liste d'erreurs + booléen ``is_indent``."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        compile(source, path, "exec")
        return [], False
    except IndentationError as e:
        return [{
            "line": e.lineno or 0,
            "col": e.offset or 0,
            "msg": e.msg or "IndentationError",
            "text": e.text or "",
        }], True
    except SyntaxError as e:
        return [{
            "line": e.lineno or 0,
            "col": e.offset or 0,
            "msg": e.msg or "SyntaxError",
            "text": e.text or "",
        }], False
    except Exception as e:
        return [{"line": 0, "col": 0, "msg": str(e)}], False


def _auto_heal_indent(path: str) -> int:
    """Tente de corriger une IndentationError par ajustement.
    
    Lit l'erreur, repère la ligne en cause et essaie de corriger
    l'indentation en se basant sur le contexte.
    
    Retourne le nombre de corrections appliquées.
    """
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    original = lines[:]
    fixed_count = 0

    for attempt in range(5):
        errors, is_indent = _compile_check(path)
        if not errors:
            break
        if not is_indent:
            break

        err = errors[0]
        lineno = err["line"] - 1
        if lineno < 0 or lineno >= len(lines):
            break

        line = lines[lineno]
        stripped = line.lstrip()
        if not stripped:
            break

        indent = line[:len(line) - len(stripped)]
        prev_indent = ""
        if lineno > 0:
            prev_line = lines[lineno - 1].rstrip()
            prev_indent = prev_line[:len(prev_line) - len(prev_line.lstrip())]
        else:
            prev_indent = ""

        next_indent = ""
        if lineno < len(lines) - 1:
            next_line = lines[lineno + 1].rstrip()
            next_indent = next_line[:len(next_line) - len(next_line.lstrip())]

        base = len(prev_indent) if prev_indent else 0
        target_level = base // 4
        current_level = len(indent) // 4

        if current_level < target_level:
            lines[lineno] = "    " * target_level + stripped
            fixed_count += 1
        elif current_level > target_level:
            if target_level == 0 and lineno > 0 and lines[lineno - 1].rstrip().endswith(":"):
                target_level = 1
            lines[lineno] = "    " * target_level + stripped
            fixed_count += 1
        elif current_level == target_level and lineno > 0 and lines[lineno - 1].rstrip().endswith(":"):
            lines[lineno] = "    " * (target_level + 1) + stripped
            fixed_count += 1
        else:
            break

    if fixed_count > 0:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)

    return fixed_count


def _ruff_check(path: str, fix: bool = True) -> List[dict]:
    ruff = _find_ruff()
    if not ruff:
        return []

    if fix:
        subprocess.run(
            [ruff, "check", "--fix", "--quiet", path],
            capture_output=True, text=True, timeout=30,
        )

    r = subprocess.run(
        [ruff, "check", "--output-format", "json", path],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode == 0 or not r.stdout.strip():
        return []

    import json as _json
    try:
        issues = _json.loads(r.stdout)
    except _json.JSONDecodeError:
        return []

    return [{
        "line": i.get("location", {}).get("row", 0),
        "col": i.get("location", {}).get("column", 0),
        "code": i.get("code", ""),
        "msg": i.get("message", ""),
        "fixable": i.get("fix", None) is not None,
    } for i in issues]


def _detect_language(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    lang_map = {
        ".py": "python", ".pyw": "python",
        ".js": "javascript", ".ts": "typescript",
        ".tsx": "typescriptreact", ".jsx": "javascriptreact",
        ".json": "json", ".yaml": "yaml", ".yml": "yaml",
        ".md": "markdown", ".html": "html", ".css": "css",
        ".sh": "shell", ".bash": "shell", ".rs": "rust", ".go": "go",
    }
    return lang_map.get(ext, "unknown")


def check_file(inputs: dict, home: str) -> dict:
    """Vérifie (et corrige) la syntaxe d'un fichier.

    Pipeline :
    1. ruff check --fix (lint style : espaces, lignes vides, imports)
    2. py_compile (vérification syntaxique)
    3. Auto-heal indentation si IndentationError détectée
    4. Nouvelle compilation pour confirmer

    inputs:
        path (str) : chemin du fichier
        fix (bool) : tenter la correction automatique (défaut: True)
        lang (str) : forcer le langage (optionnel)

    returns:
        ok (bool)       : aucune erreur résiduelle
        errors (list)   : [{line, col, msg, code, fixable}]
        fixed (int)     : nombre d'erreurs corrigées
        lang (str)      : langage détecté
        before (str)    : extrait du code avant correction
        after (str)     : extrait du code après correction
    """
    path = inputs.get("path", "")
    fix = inputs.get("fix", True)
    if not path:
        return {"ok": False, "error": "path requis"}

    if not os.path.isabs(path):
        path = os.path.normpath(os.path.join(home, path))

    if not os.path.isfile(path):
        return {"ok": False, "error": f"fichier introuvable: {path}"}

    lang = inputs.get("lang", "") or _detect_language(path)

    with open(path, "r", encoding="utf-8") as f:
        before = f.read()

    fixed = 0
    errors = []

    if lang == "python" and fix:
        ruff_before = open(path).read()
        _ruff_check(path, fix=True)
        if open(path).read() != ruff_before:
            fixed += 1

    errors, is_indent = _compile_check(path)

    if lang == "python" and fix and is_indent and errors:
        healed = _auto_heal_indent(path)
        if healed > 0:
            fixed += healed
            errors, _ = _compile_check(path)
            if not errors:
                is_indent = False

    if lang == "python" and fix and not errors:
        _ruff_check(path, fix=True)

    with open(path, "r", encoding="utf-8") as f:
        after = f.read()

    context_lines = 4
    diff_before = ""
    diff_after = ""
    if before != after:
        before_lines = before.splitlines()
        after_lines = after.splitlines()
        for i, (bl, al) in enumerate(zip(before_lines, after_lines)):
            if bl != al:
                start = max(0, i - context_lines)
                end = min(len(before_lines), i + context_lines + 1)
                diff_before = "\n".join(before_lines[start:end])
                diff_after = "\n".join(after_lines[start:end])
                break

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "fixed": fixed,
        "lang": lang,
        "before": diff_before if diff_before else "",
        "after": diff_after if diff_after else "",
    }


__skills__ = ["check_file"]
