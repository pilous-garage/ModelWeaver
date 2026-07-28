"""Fonctions fichier (relatif au home de l'agent).

Migrées depuis services/skill_manager.py (méthodes _exec_*). La logique
interne est conservée à l'identique ; les helpers de chemin sont ceux de
._fs (module-level).
"""

import os
import shutil
from pathlib import Path

from ..system._fs import (
    safe_path,
    classify_write_path,
    resolve_read_path,
    index_add,
    index_remove,
    INDEX_FILE,
)


def read_file(inputs: dict, home: str) -> dict:
    path = inputs.get("path", "")
    full = resolve_read_path(path, home)
    with open(full, "r", encoding="utf-8") as f:
        return {"content": f.read()}


def read_range(inputs: dict, home: str) -> dict:
    """Lit une plage de lignes d'un fichier (avec numéros).

    Spécialement conçu pour les LLM : renvoie chaque ligne avec son
    numéro, ce qui permet au LLM de référencer précisément les
    lignes à modifier.

    inputs:
      path: chemin relatif du fichier
      start_line: première ligne (1-indexed, défaut 1)
      end_line: dernière ligne (1-indexed, défaut fin du fichier)
    """
    path = inputs.get("path", "")
    full = resolve_read_path(path, home)
    if not os.path.exists(full):
        return {"error": f"fichier introuvable: {path}"}
    if not os.path.isfile(full):
        return {"error": f"n'est pas un fichier: {path}"}

    start = max(1, int(inputs.get("start_line", 1)))
    end = int(inputs.get("end_line", 0))

    with open(full, "r", encoding="utf-8") as f:
        all_lines = f.readlines()

    total = len(all_lines)
    end = end if end > 0 else total
    end = min(end, total)

    if start > total:
        return {"lines": [], "total_lines": total, "start_line": start, "end_line": end}

    lines = []
    for i in range(start - 1, min(end, total)):
        lines.append({"line": i + 1, "content": all_lines[i]})

    return {
        "lines": lines,
        "total_lines": total,
        "start_line": start,
        "end_line": end,
        "preview": "".join(l["content"] for l in lines),
    }


def _find_occurrences(content: str, old_string: str) -> list:
    """Liste toutes les occurrences de old_string dans content."""
    occurrences = []
    pos = 0
    while True:
        try:
            idx = content.index(old_string, pos)
        except ValueError:
            break
        line_no = content[:idx].count("\n") + 1
        col = idx - content[:idx].rfind("\n") - 1
        start = max(0, idx - 40)
        end = min(len(content), idx + len(old_string) + 60)
        occurrences.append({
            "line": line_no,
            "col": col,
            "prefix": content[start:idx],
            "suffix": content[idx + len(old_string):end],
        })
        pos = idx + 1
    return occurrences


def patch_file(inputs: dict, home: str) -> dict:
    """Remplace une chaîne exacte dans un fichier (old_string → new_string).

    Utilise old_string → new_string pour un remplacement ciblé.
    Si old_string apparaît plusieurs fois, refuse avec les détails
    de chaque occurrence. Un paramètre optionnel ``line`` permet de
    cibler une ligne précise (1-indexed) pour parer les edits
    concurrents.

    inputs:
      path: chemin relatif du fichier
      old_string: texte exact à remplacer
      new_string: texte de remplacement
      line: ligne supposée (1-indexed, optionnel) — si fournie,
            la recherche commence à cette ligne
    """
    path = inputs.get("path", "")
    old_string = inputs.get("old_string", "")
    new_string = inputs.get("new_string", "")
    target_line = inputs.get("line")

    if not old_string:
        return {"ok": False, "error": "old_string requis pour un patch"}

    full = resolve_read_path(path, home)
    if not os.path.exists(full):
        return {"ok": False, "error": f"fichier introuvable: {path}"}

    content = Path(full).read_text(encoding="utf-8")

    # Si une ligne cible est donnée, on essaye d'abord à cet endroit
    if target_line is not None:
        try:
            target_line = int(target_line)
        except (ValueError, TypeError):
            target_line = None

    if target_line and target_line > 0:
        # Convertir la ligne en position byte
        lines_to_pos = content.splitlines(keepends=True)
        byte_pos = 0
        for i, line_text in enumerate(lines_to_pos):
            if i + 1 >= target_line:
                break
            byte_pos += len(line_text)

        # Chercher old_string à partir de cette position
        try:
            idx = content.index(old_string, byte_pos)
            # Vérifier que c'est bien à la bonne ligne
            match_line = content[:idx].count("\n") + 1
            if abs(match_line - target_line) <= 1:
                new_content = content[:idx] + new_string + content[idx + len(old_string):]
                Path(full).write_text(new_content, encoding="utf-8")
                index_add(home, full)
                return {
                    "ok": True, "path": path, "replaced": True,
                    "old_length": len(old_string), "new_length": len(new_string),
                    "line": match_line,
                }
        except ValueError:
            pass

    # Comportement normal : trouver toutes les occurrences
    occurrences = _find_occurrences(content, old_string)
    count = len(occurrences)

    if count == 0:
        lines = content.splitlines()
        hint_lines = []
        for i, line in enumerate(lines, 1):
            if old_string[:20].strip() in line or old_string[-20:].strip() in line:
                hint_lines.append(f"  L{i}: {line[:120]}")
            elif any(word in line for word in old_string.split()[:3] if len(word) > 3):
                hint_lines.append(f"  L{i}: {line[:120]}")
        hint = ""
        if hint_lines:
            hint = "Lignes proches :\n" + "\n".join(hint_lines[:5])
        return {
            "ok": False, "error": "old_string introuvable dans le fichier",
            "count": 0,
            "hint": hint or "Vérifiez que old_string correspond exactement au contenu (espaces, sauts de ligne, casse).",
        }

    if count > 1:
        extra = ""
        if target_line and target_line > 0:
            extra = f" (introuvable à la ligne {target_line})"
        return {
            "ok": False,
            "error": f"old_string trouvé {count} fois{extra} — fournissez plus de contexte "
                     "dans old_string (ajoutez des lignes adjacentes) ou précisez le paramètre 'line'",
            "count": count,
            "occurrences": occurrences,
        }

    # Occurrence unique
    new_content = content.replace(old_string, new_string, 1)
    Path(full).write_text(new_content, encoding="utf-8")
    index_add(home, full)
    return {
        "ok": True, "path": path, "replaced": True,
        "old_length": len(old_string), "new_length": len(new_string),
        "line": occurrences[0]["line"],
    }


def insert_lines(inputs: dict, home: str) -> dict:
    """Insère du contenu à une ligne spécifique d'un fichier.

    inputs:
      path: chemin relatif du fichier
      line: numéro de ligne (1-indexed) où insérer (défaut: fin du fichier)
      content: texte à insérer
      before: si true, insère avant la ligne spécifiée (défaut: après)
    """
    path = inputs.get("path", "")
    full = resolve_read_path(path, home)
    content = inputs.get("content", "")
    if not content:
        return {"ok": False, "error": "content requis"}

    if not os.path.exists(full):
        Path(full).parent.mkdir(parents=True, exist_ok=True)
        Path(full).write_text(content, encoding="utf-8")
        index_add(home, full)
        return {"ok": True, "path": path, "action": "created"}

    all_lines = Path(full).read_text(encoding="utf-8").splitlines(keepends=True)
    insert_at = int(inputs.get("line", len(all_lines) + 1))
    before = inputs.get("before", False)

    insert_lines_list = content.splitlines(keepends=True)
    if not insert_lines_list[-1].endswith("\n"):
        insert_lines_list[-1] += "\n"

    idx = insert_at - 1 if before else insert_at
    idx = max(0, min(idx, len(all_lines)))

    new_lines = all_lines[:idx] + insert_lines_list + all_lines[idx:]
    Path(full).write_text("".join(new_lines), encoding="utf-8")
    index_add(home, full)
    return {"ok": True, "path": path, "action": "inserted_at_line", "line": insert_at}


def write_file(inputs: dict, home: str) -> dict:
    """Écrit un fichier (écrase le contenu existant).

    ATTENTION : écrase tout le contenu. Pour modifier une partie,
    préférez patch_file (old_string → new_string) ou insert_lines (à
    une ligne précise). Le chemin est borné par safe_path.
    """
    path = inputs.get("path", "")
    content = inputs.get("content", "")
    full = classify_write_path(path, home)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    index_add(home, full)
    return {"result": f"Fichier {path} écrit"}


def append_file(inputs: dict, home: str) -> dict:
    """Ajoute du contenu à la fin du fichier existant."""
    path = inputs.get("path", "")
    content = inputs.get("content", "")
    full = classify_write_path(path, home)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "a", encoding="utf-8") as f:
        f.write(content)
    index_add(home, full)
    return {"ok": True}


def delete_file(inputs: dict, home: str) -> dict:
    path = inputs.get("path", "")
    full = resolve_read_path(path, home)
    if not os.path.exists(full):
        return {"ok": False, "error": "fichier introuvable"}
    if os.path.isdir(full):
        shutil.rmtree(full)
    else:
        os.remove(full)
    index_remove(home, full)
    return {"ok": True}


def copy_file(inputs: dict, home: str) -> dict:
    src = resolve_read_path(inputs.get("src", ""), home)
    dst_input = inputs.get("dst", "")
    dst = classify_write_path(dst_input, home) if "/" not in dst_input \
        else safe_path(dst_input, home)
    if not os.path.exists(src):
        return {"ok": False, "error": "source introuvable"}
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    index_add(home, dst)
    return {"ok": True, "dst": os.path.relpath(dst, os.path.abspath(home))}


def move_file(inputs: dict, home: str) -> dict:
    src = resolve_read_path(inputs.get("src", ""), home)
    dst_input = inputs.get("dst", "")
    dst = classify_write_path(dst_input, home) if "/" not in dst_input \
        else safe_path(dst_input, home)
    if not os.path.exists(src):
        return {"ok": False, "error": "source introuvable"}
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    index_remove(home, src)
    shutil.move(src, dst)
    index_add(home, dst)
    return {"ok": True, "dst": os.path.relpath(dst, os.path.abspath(home))}


def mkdir(inputs: dict, home: str) -> dict:
    path = inputs.get("path", "")
    if "/" in path:
        full = safe_path(path, home)
    else:
        full = os.path.join(os.path.abspath(home), "work", path)
    os.makedirs(full, exist_ok=True)
    return {"ok": True, "path": os.path.relpath(full, os.path.abspath(home))}


def list_dir(inputs: dict, home: str) -> dict:
    path = inputs.get("path", "")
    full = resolve_read_path(path, home) if path else os.path.abspath(home)
    if not os.path.isdir(full):
        return {"entries": [], "error": "n'est pas un dossier"}
    entries = []
    for name in sorted(os.listdir(full)):
        fp = os.path.join(full, name)
        entries.append({
            "name": name,
            "type": "dir" if os.path.isdir(fp) else "file",
            "size": os.path.getsize(fp) if os.path.isfile(fp) else 0,
        })
    return {"entries": entries}


def glob(inputs: dict, home: str) -> dict:
    pattern = inputs.get("pattern", "*")
    base = os.path.abspath(home)
    results = [str(p.relative_to(base)) for p in Path(base).glob(pattern)
               if not str(p).endswith(INDEX_FILE)]
    return {"matches": sorted(results)}


def file_info(inputs: dict, home: str) -> dict:
    path = inputs.get("path", "")
    full = resolve_read_path(path, home)
    if not os.path.exists(full):
        return {"exists": False}
    st = os.stat(full)
    return {
        "exists": True,
        "is_dir": os.path.isdir(full),
        "size": st.st_size,
        "mtime": st.st_mtime,
    }


def tree(inputs: dict, home: str) -> dict:
    path = inputs.get("path", "")
    full = resolve_read_path(path, home) if path else os.path.abspath(home)
    if not os.path.isdir(full):
        return {"tree": "", "error": "n'est pas un dossier"}
    lines = []
    base = os.path.abspath(home)
    for dirpath, dirnames, filenames in os.walk(full):
        rel = os.path.relpath(dirpath, base)
        if rel == ".":
            rel = ""
        indent = "  " * rel.count(os.sep) if rel else ""
        name = os.path.basename(dirpath) if rel else os.path.basename(base)
        lines.append(f"{indent}{name}/")
        for fn in sorted(filenames):
            lines.append(f"{indent}  {fn}")
    return {"tree": "\n".join(lines)}


def upgrade_important(inputs: dict, home: str) -> dict:
    src = resolve_read_path(inputs.get("src", ""), home)
    if not os.path.exists(src):
        return {"ok": False, "error": "source introuvable"}
    name = inputs.get("name") or os.path.basename(src)
    dst = os.path.join(os.path.abspath(home), "important", name)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    index_remove(home, src)
    shutil.move(src, dst)
    index_add(home, dst)
    return {"ok": True, "path": os.path.relpath(dst, os.path.abspath(home))}


def downgrade_important(inputs: dict, home: str) -> dict:
    src = resolve_read_path(inputs.get("src", ""), home)
    if not os.path.exists(src):
        return {"ok": False, "error": "source introuvable"}
    name = inputs.get("name") or os.path.basename(src)
    dst = os.path.join(os.path.abspath(home), "work", name)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    index_remove(home, src)
    shutil.move(src, dst)
    return {"ok": True, "path": os.path.relpath(dst, os.path.abspath(home))}


def grep(inputs: dict, home: str) -> dict:
    """Cherche un motif regex dans les fichiers du workspace.

    inputs:
      pattern: motif regex à chercher
      include: glob de filtrage fichiers (ex: "*.py", "src/**/*.ts")
      exclude: glob de fichiers à ignorer (ex: "*.min.js", "__pycache__")
      max_results: nombre max de résultats (défaut 50)
      context_lines: lignes de contexte avant/après chaque match (défaut 0)

    returns:
      ok (bool)
      matches (list): [{file, line, col, content, context_before, context_after}]
      count (int): nombre total de matches
      truncated (bool): vrai si max_results atteint
    """
    pattern = inputs.get("pattern", "")
    if not pattern:
        return {"ok": False, "error": "pattern requis"}

    include = inputs.get("include", "**/*")
    exclude_input = inputs.get("exclude", "")
    max_results = int(inputs.get("max_results", 50))
    context_lines = int(inputs.get("context_lines", 0))

    import re
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return {"ok": False, "error": f"regex invalide: {e}"}

    base = os.path.abspath(home)
    matches = []
    count = 0
    truncated = False

    exclude_patterns = [e.strip() for e in exclude_input.split(",") if e.strip()]

    for filepath in sorted(Path(base).rglob(include)):
        rel = str(filepath.relative_to(base))
        if not filepath.is_file():
            continue
        if filepath.name.endswith(INDEX_FILE):
            continue

        # Exclude patterns
        if exclude_patterns:
            skip = False
            for ex in exclude_patterns:
                if Path(rel).match(ex) or Path(filepath.name).match(ex):
                    skip = True
                    break
            if skip:
                continue

        try:
            lines = filepath.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        except (OSError, UnicodeDecodeError):
            continue

        for i, line in enumerate(lines):
            for m in regex.finditer(line):
                col = m.start()
                ctx_before = []
                ctx_after = []
                if context_lines > 0:
                    for ci in range(max(0, i - context_lines), i):
                        ctx_before.append(lines[ci].rstrip("\n"))
                    for ci in range(i + 1, min(len(lines), i + context_lines + 1)):
                        ctx_after.append(lines[ci].rstrip("\n"))

                matches.append({
                    "file": rel,
                    "line": i + 1,
                    "col": col + 1,
                    "content": line.rstrip("\n"),
                    "context_before": ctx_before,
                    "context_after": ctx_after,
                })
                count += 1
                if count >= max_results:
                    truncated = True
                    break
            if truncated:
                break
        if truncated:
            break

    return {
        "ok": True,
        "matches": matches,
        "count": count,
        "truncated": truncated,
    }


__skills__ = [
    "read_file", "write_file", "append_file", "read_range",
    "patch_file", "insert_lines", "delete_file",
    "copy_file", "move_file", "mkdir", "list_dir", "glob",
    "file_info", "tree", "upgrade_important", "downgrade_important",
    "grep",
]
