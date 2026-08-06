#!/usr/bin/env python3
"""gen_fake_md.py — Génère des .fake.md de concaténation de sources.

Parcourt récursivement le PROJET (ou un dossier racine), respecte .gitignore,
et pour CHAQUE dossier non gitignoré contenant des fichiers sources, écrit un
fichier `source-<dossier>.fake.md` (concaténation de ses fichiers).

Les .fake.md sont gitignorés (règle `*.fake.md`) : support de revue/copie
(ex. coller dans un chat IA), pas commités.

Usage :
  python3 scripts/gen_fake_md.py [--root <dossier>] [--dry] [--all] [--min <n>]
  --root : racine du parcours (défaut : racine du repo / dossier courant)
  --dry  : liste les dossiers cibles sans écrire
  --all  : inclure tous les types de fichiers (par défaut : code/seulement)
  --min  : nombre minimal de fichiers sources par dossier pour générer (défaut 1)
"""

import argparse
import sys
from pathlib import Path

try:
    from pathspec import PathSpec
    from pathspec.patterns import GitWildMatchPattern
except ImportError:
    print("pathspec requis : pip install pathspec", file=sys.stderr)
    sys.exit(1)

# Extensions de code/contenu à inclure par défaut.
KEEP_SUFFIXES = {
    ".ts", ".tsx", ".js", ".jsx", ".py", ".rs", ".go", ".java", ".c", ".h",
    ".sh", ".bash", ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".conf",
    ".md", ".txt", ".html", ".css", ".sql",
}
# Noms de dossiers toujours ignorés (même si pas dans .gitignore).
ALWAYS_IGNORE_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache",
                      "node_modules", "target", "dist", ".venv", "venv", ".modelweaver"}


def load_gitignore(root: Path) -> PathSpec:
    """Charge les règles .gitignore depuis root jusqu'à la racine du repo."""
    patterns = []
    chain = []
    d = root
    while d != d.parent:
        chain.append(d)
        if (d / ".git").exists():
            break
        d = d.parent
    for gi_dir in reversed(chain):
        gi = gi_dir / ".gitignore"
        if gi.exists():
            patterns.extend(gi.read_text(encoding="utf-8", errors="ignore").splitlines())
    patterns += [".git/", "__pycache__/", ".pytest_cache/", ".mypy_cache/",
                 "node_modules/", "*.fake.md"]
    return PathSpec.from_lines(GitWildMatchPattern, patterns)


def is_ignored(spec: PathSpec, root: Path, rel: str) -> bool:
    """True si `rel` (relatif au root) est gitignoré OU dans un dossier interdit."""
    if spec.match_file(rel):
        return True
    parts = Path(rel).parts
    return any(p in ALWAYS_IGNORE_DIRS for p in parts)


def list_source_files(directory: Path, spec: PathSpec, root: Path, include_all: bool) -> list:
    """Fichiers sources directs du dossier (non récursif), non gitignorés."""
    out = []
    for p in sorted(directory.iterdir()):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if is_ignored(spec, root, str(rel)):
            continue
        if not include_all and p.suffix.lower() not in KEEP_SUFFIXES:
            continue
        if p.suffix == ".md" and ".fake.md" in p.name:
            continue  # ne pas re-concaténer un .fake.md
        out.append(p)
    return out


def is_gitignored_dir(directory: Path, spec: PathSpec, root: Path) -> bool:
    """True si le dossier lui-même est gitignoré (ou un de ses parents)."""
    rel = directory.relative_to(root)
    return is_ignored(spec, root, str(rel) + "/")


def render(files: list, root: Path, dir_name: str) -> str:
    parts = [f"# Sources concaténées : {dir_name}", ""]
    parts.append(f"{len(files)} fichiers non gitignorés. Généré par scripts/gen_fake_md.py.")
    parts.append("Chaque section = un fichier. Les fences internes des .md sont échappées.")
    parts.append("---")
    parts.append("")
    for p in files:
        rel = p.relative_to(root)
        parts.append(f"## {rel}")
        parts.append("")
        parts.append("```")
        content = p.read_text(encoding="utf-8", errors="replace")
        content = "\n".join("````" if ln.strip() and set(ln.strip()) == {"`"} else ln for ln in content.splitlines())
        parts.append(content)
        parts.append("```")
        parts.append("")
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser(description="Génère des .fake.md dans chaque dossier de sources")
    ap.add_argument("--root", default=None, help="racine du parcours (défaut : racine du repo)")
    ap.add_argument("--dry", action="store_true", help="liste les dossiers cibles sans écrire")
    ap.add_argument("--all", action="store_true", help="inclure tous les types (pas que code)")
    ap.add_argument("--min", type=int, default=1, help="min de fichiers sources par dossier (défaut 1)")
    a = ap.parse_args()

    # racine : la racine du repo (là où il y a .git) ou --root
    root = Path(a.root).resolve() if a.root else Path(__file__).resolve().parent.parent
    if not root.is_dir():
        sys.exit(f"racine introuvable: {root}")
    spec = load_gitignore(root)

    generated = 0
    skipped = 0
    # Parcourt TOUS les dossiers (récursif), sauf gitignorés
    for directory in sorted([root] + [d for d in root.rglob("*") if d.is_dir()]):
        if directory != root and is_gitignored_dir(directory, spec, root):
            continue
        files = list_source_files(directory, spec, root, include_all=a.all)
        if len(files) < a.min:
            continue
        rel = directory.relative_to(root)
        name = rel.as_posix().replace("/", "_") if str(rel) != "." else root.name
        out = directory / f"source-{name}.fake.md"
        if a.dry:
            print(f"  {rel} : {len(files)} fichiers → {out.name}")
            generated += 1
        else:
            out.write_text(render(files, root, str(rel) if str(rel) != "." else root.name), encoding="utf-8")
            print(f"✓ {rel} : {len(files)} fichiers → {out.name}")
            generated += 1

    print(f"\n{generated} dossier(s) traité(s), {skipped} ignoré(s)")


if __name__ == "__main__":
    main()
