"""file_watcher — vérificateur de fichiers sources (data_genere stale).

Surveille les FICHIERS référencés par des dépendances (data_type `fichier`
du catalogue local) et, à chaque passage, compare leur mtime disque au
dernier mtime connu :

  - mtime disque > last_modif_at stocké → le fichier a été modifié (en
    dehors du système : édition manuelle, git pull, générateur tiers) →
    on re-hash le contenu ;
  - si le nouveau hash diffère → PROPAGATION STALE : on marque stale
    (récursivement) les data_genere qui dépendent de ce fichier, via le
    rebond dans gen_dependance.

Seuls les fichiers ENREGISTRÉS (ceux du graphe de dépendances) sont
vérifiés — pas tout le projet : un stat ≈ 1-5 µs, ~50-200 fichiers actifs
→ un check complet < 1 ms.

Usage:
    from services.file_watcher import FileWatcher
    fw = FileWatcher()
    fw.register_files([Path("AgentsCatalogue/lib/workspacedb/taskflow.py")])
    fw.start()          # ticker de fond (stale_check_s)
    fw.check_once()     # ou un check ponctuel
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from modules.sql.catalogue_genere import GenereCatalogue
from modules.sql.catalogue_local import LocalCatalogue

os_walk = os.walk

# data_type du catalogue local pour les fichiers sources.
DATA_FILE_TYPE = "fichier"

# Défaut de règles de scan par projet (surchargeable via set_file_rules).
DEFAULT_RULES: Dict[str, Any] = {
    "include_ext": [".py", ".c", ".cpp", ".h", ".go", ".rs", ".js", ".ts",
                    ".tsx", ".java", ".rb", ".php", ".sql", ".sh", ".txt",
                    ".md", ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg"],
    "exclude_ext": [],
    "include_dirs": [],
    "exclude_dirs": [".git", ".venv", "node_modules", "__pycache__",
                     "target", "dist", "build", ".modelweaver"],
    "include_files": [],
    "exclude_files": ["package-lock.json", "yarn.lock", "Cargo.lock",
                      "poetry.lock"],
    "include_globs": [],
    "exclude_globs": ["*.min.js", "*.pyc"],
    "gitignore": False,
}


def file_hash(path: Path) -> str:
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime_ns / 1e9
    except OSError:
        return 0.0


def _mtime_ns(path: Path) -> int:
    """mtime en nanosecondes (résolution maximale, comparaison exacte)."""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return -1


class FileWatcher:
    """Surveille les fichiers sources et propage la staleness aux data_genere.

    Deux BDD : le catalogue local (data_type `fichier` : mtime/hash par
    fichier) et le domaine genere (gen_dependance : qui dépend de quoi).
    """

    def __init__(self, catalogue: Optional[LocalCatalogue] = None,
                 genere: Optional[GenereCatalogue] = None,
                 write_token: str = "write_catalogue"):
        self.cat = catalogue or LocalCatalogue(mode="w",
                                               write_token=write_token)
        # s'assure que le data_type fichier existe
        try:
            if not self.cat.catalogue_exists(DATA_FILE_TYPE):
                self.cat.create_type(DATA_FILE_TYPE,
                                     "fichiers sources suivis (mtime+hash)",
                                     token=write_token)
        except Exception:
            pass
        self.gen = genere or GenereCatalogue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._conf_runtime: List[str] = []

    # ── Enregistrement ─────────────────────────────────────

    def register_files(self, paths: Iterable[Path]) -> int:
        """Enregistre des fichiers sources (data_type fichier). Stocke le
        mtime (ns) + hash INITIAL. Idempotent (upsert).

        mtime stocké dans last_modify_data.last_modify_at en TEXTE =
        str(st_mtime_ns) : résolution nanoseconde, comparaison exacte."""
        n = 0
        table = f"{DATA_FILE_TYPE}_catalogue"
        for p in paths:
            try:
                h = file_hash(p)
                m = _mtime_ns(p)
                self.cat.conn.execute(
                    f"INSERT INTO {table} (ref, name, ref_file, data_type, "
                    f"value, status) VALUES (?, ?, ?, ?, ?, 'active') "
                    f"ON CONFLICT(ref) DO UPDATE SET ref_file = excluded.ref_file, "
                    f"value = excluded.value, data_type = excluded.data_type",
                    (str(p), p.name, str(p), DATA_FILE_TYPE, h))
                self.cat.conn.execute(
                    "INSERT INTO last_modify_data(data_type, ref, last_modify_at, "
                    "modify_count) VALUES (?, ?, ?, 1) "
                    "ON CONFLICT(data_type, ref) DO UPDATE SET last_modify_at = ?",
                    (DATA_FILE_TYPE, str(p), str(m), str(m)))
                n += 1
            except Exception:
                pass
        try:
            self.cat.conn.commit()
        except Exception:
            pass
        return n

    def list_files(self) -> List[dict]:
        table = f"{DATA_FILE_TYPE}_catalogue"
        rows = self.cat.conn.execute(
            f"SELECT ref, ref_file, value FROM {table} "
            f"WHERE data_type = ? ORDER BY ref", (DATA_FILE_TYPE,)).fetchall()
        return [dict(r) for r in rows]

    # ── Règles de scan par projet ───────────────────────────

    def get_file_rules(self, project_id: int = 0) -> Dict[str, Any]:
        """Règles de scan du projet (merge défaut + surcharge en BDD)."""
        rules = json.loads(json.dumps(DEFAULT_RULES))
        try:
            row = self.gen.disk.execute(
                "SELECT rules_json FROM gen_file_rules WHERE project_id = ?",
                (project_id,)).fetchone()
            if row:
                stored = json.loads(row["rules_json"] or "{}")
                # merge profond (listes fusionnées, scalaires surchargés)
                for k, v in stored.items():
                    if isinstance(v, list) and isinstance(rules.get(k), list):
                        rules[k] = list(dict.fromkeys(list(rules.get(k, [])) + v))
                    else:
                        rules[k] = v
        except Exception:
            pass
        return rules

    def set_file_rules(self, rules: Dict[str, Any],
                       project_id: int = 0) -> None:
        """Écrase (remplace) les règles de scan d'un projet."""
        self.gen.disk.execute(
            "INSERT INTO gen_file_rules (project_id, rules_json, updated_at) "
            "VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(project_id) DO UPDATE SET "
            "rules_json = excluded.rules_json, updated_at = excluded.updated_at",
            (project_id, json.dumps(rules)))
        try:
            self.gen.disk.commit()
        except Exception:
            pass

    def _gitignore_patterns(self, root: Path) -> List[str]:
        """Lit .gitignore du projet (recoupage EXPLICITE — activé par la règle
        `gitignore: true`, mis à jour par les skills commit)."""
        gi = root / ".gitignore"
        if not gi.exists():
            return []
        out: List[str] = []
        try:
            for line in gi.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                out.append(line)
        except Exception:
            pass
        return out

    def scan_and_register(self, root: Path, project_id: int = 0,
                          limit: int = 100000) -> int:
        """SCAN récursif d'un projet → enregistre les fichiers sources.

        Utilise les RÈGLES en COUCHES :
          1. `.conf_gitignore.yaml` (projet, explicite) : règles include/exclude
             résolues par SPÉCIFICITÉ (le pattern le plus précis gagne) ;
          2. gen_file_rules (défaut BDD) si le .conf_gitignore.yaml n'existe pas
             ou ne matche pas ;
          3. `.gitignore` (recoupé si `gitignore: true`).

        Idempotent : les fichiers déjà enregistrés ne sont pas re-hashés.
        """
        rules = self.get_file_rules(project_id)
        inc_ext = {e.lower() for e in rules.get("include_ext", [])}
        exc_ext = {e.lower() for e in rules.get("exclude_ext", [])}
        inc_dirs = set(rules.get("include_dirs", []))
        exc_dirs = set(rules.get("exclude_dirs", []))
        inc_files = set(rules.get("include_files", []))
        exc_files = set(rules.get("exclude_files", []))
        inc_globs = list(rules.get("include_globs", []))
        exc_globs = list(rules.get("exclude_globs", []))

        # ── Couche 2 : .conf_gitignore.yaml (règles par spécificité) ──
        conf_rules, conf_gitignore = self._load_conf_rules(root)
        if conf_gitignore is not None:
            rules["gitignore"] = conf_gitignore
        if rules.get("gitignore"):
            exc_globs.extend(self._gitignore_patterns(root))

        def keep(p: Path) -> bool:
            name = p.name
            ext = p.suffix.lower()
            try:
                rel = p.relative_to(root).parts
            except Exception:
                rel = (p.name,)
            # 1) .conf_gitignore.yaml : résolution par spécificité
            if conf_rules:
                decision = self._conf_match(conf_rules, rel)
                if decision is not None:
                    return decision
            # 2) règles BDD (défaut)
            if any(seg in exc_dirs for seg in rel):
                return False
            if inc_dirs and not any(seg in inc_dirs for seg in rel):
                return False
            if name in exc_files:
                return False
            if ext in exc_ext:
                return False
            if any(p.match(g) for g in exc_globs):
                return False
            if ext in inc_ext or name in inc_files \
                    or any(p.match(g) for g in inc_globs):
                return True
            return False

        # dossiers à pruner pendant la marche : exclus + cachés (si le conf
        # exclut '.*').
        prune_hidden = conf_rules and any(
            r.get("pattern") in (".*", "**/.*", "**/.**", "**/.*/**")
            for r in conf_rules)
        # patterns de dossiers exclus du conf (ex. "**/node_modules/**" →
        # prunes tout dossier nommé node_modules).
        conf_dir_names = set()
        if conf_rules:
            for r in conf_rules:
                pat = (r.get("pattern") or "").strip()
                if (r.get("action") or "") == "exclude" and "**/" in pat:
                    base = pat.replace("**/", "").rstrip("/")
                    if base and "/" not in base and "*" not in base:
                        conf_dir_names.add(base)
        prune_names = set(exc_dirs) | conf_dir_names
        found: List[Path] = []
        try:
            for dirpath, dirnames, filenames in os_walk(root):
                # prunes la descente : retire les dossiers exclus/cachés
                keep_dirs = []
                for d in sorted(dirnames):
                    if d in prune_names:
                        continue
                    if prune_hidden and d.startswith("."):
                        continue
                    dp = Path(dirpath) / d
                    try:
                        rel = dp.relative_to(root).parts
                    except Exception:
                        rel = (d,)
                    if conf_rules and self._conf_match(conf_rules, rel) is False:
                        continue
                    keep_dirs.append(d)
                dirnames[:] = keep_dirs
                for fn in filenames:
                    p = Path(dirpath) / fn
                    if keep(p):
                        found.append(p)
                        if len(found) >= limit:
                            break
                if len(found) >= limit:
                    break
        except Exception:
            pass
        known = {f["ref"] for f in self.list_files()}
        to_add = [p for p in found if str(p) not in known]
        return self.register_files(to_add)

    # ── Résolution .conf_gitignore.yaml (spécificité) ───────

    def _load_conf_rules(self, root: Path) -> (Optional[List[dict]], Optional[bool]):
        """Charge .conf_gitignore.yaml → (rules, gitignore_flag). (None, None)
        si le fichier n'existe pas. Les `runtime_globs` sont stockés sur
        l'instance (self._conf_runtime) pour que scan/get puissent les
        enregistrer auprès des data_genere."""
        conf = root / ".conf_gitignore.yaml"
        if not conf.exists():
            return None, None
        try:
            import yaml
            data = yaml.safe_load(conf.read_text()) or {}
        except Exception:
            return None, None
        rules = data.get("rules") or []
        self._conf_runtime = [g for g in (data.get("runtime_globs") or [])]
        return rules, bool(data.get("gitignore", False))

    def _conf_match(self, rules: List[dict], rel_parts) -> Optional[bool]:
        """Résout les règles du .conf_gitignore.yaml par SPÉCIFICITÉ.

        Pour chaque règle dont le pattern matche le chemin relatif, on garde
        la PLUS SPÉCIFIQUE (le plus de caractères littéraux). Retourne
        include/exclude de la gagnante, ou None si aucun match."""
        best = None
        best_score = -1
        for r in rules:
            pat = (r.get("pattern") or "").strip()
            if not pat:
                continue
            action = (r.get("action") or "").strip()
            forced_include = pat.startswith("!")
            if forced_include:
                pat = pat[1:]
            score = _pattern_score(pat)
            if _pattern_match(pat, rel_parts):
                if score > best_score:
                    best_score = score
                    best = "include" if (action == "include" or forced_include) \
                        else "exclude"
        if best == "include":
            return True
        if best == "exclude":
            return False
        return None

    # ── Fichiers runtime (glob) : brancher + vérifier au get ──

    def attach_runtime(self, data_ref: str, root: Path,
                       project_id: int = 0) -> int:
        """Enregistre les runtime_globs du .conf_gitignore.yaml auprès de la
        data_genere `data_ref` (gen_runtime_files). Retourne le nb de globs."""
        if not self._conf_runtime:
            self._load_conf_rules(root)
        n = 0
        for g in self._conf_runtime:
            self.gen.register_runtime_glob(project_id, data_ref, g)
            n += 1
        return n

    def check_runtime(self, data_ref: str, project_id: int = 0,
                      base: Optional[Path] = None) -> bool:
        """Vérification INVERSE au get : si un fichier d'un glob runtime de la
        data a changé → stale. À appeler au GET (avant de renvoyer la data).
        Retourne True si la data est stale. `base` : racine du projet."""
        return self.gen.check_runtime(project_id, data_ref, base=base)

    def register_runtime_glob(self, data_ref: str, glob: str,
                              project_id: int = 0) -> None:
        """Déclare un glob runtime pour une data (programmatique, sans conf)."""
        self.gen.register_runtime_glob(project_id, data_ref, glob)

    # ── Vérification (le cœur) ──────────────────────────────

    def check_once(self, propagate: bool = True) -> dict:
        """Un passage de vérification : stat chaque fichier enregistré, re-hash
        si mtime (ns) bougé, propage stale aux dépendants si hash changé.
        Fichiers SUPPRIMÉS : marqués stale (le contenu n'existe plus).

        Coût : un stat (~1-5 µs) par fichier de la table — on ne HASH que
        les fichiers dont le mtime a changé. 10k fichiers → ~10-50 ms de
        stat, hash seulement sur les modifiés.

        Retourne {checked, modified, stale_propagated}."""
        checked = modified = 0
        stale_refs: List[str] = []
        for f in self.list_files():
            p = Path(f.get("ref_file") or f.get("ref") or "")
            m = _mtime_ns(p)
            checked += 1
            # mtime disque (ns) vs last_modify_at stocké (texte ns)
            row = self.cat.conn.execute(
                "SELECT last_modify_at FROM last_modify_data "
                "WHERE data_type = ? AND ref = ?",
                (DATA_FILE_TYPE, f["ref"])).fetchone()
            last_m = int(row["last_modify_at"]) if row else 0
            if m < 0:
                # fichier supprimé → stale (plus de contenu valide)
                modified += 1
                stale_refs.append(f["ref"])
                if propagate:
                    self._propagate(f["ref"])
                try:
                    self.cat.conn.execute(
                        f"UPDATE {DATA_FILE_TYPE}_catalogue SET status = 'missing' "
                        f"WHERE ref = ?", (f["ref"],))
                except Exception:
                    pass
                continue
            if m <= last_m:
                continue  # pas modifié hors système
            # modifié → re-hash (uniquement ici)
            h = file_hash(p)
            if h != (f.get("value") or ""):
                modified += 1
                stale_refs.append(f["ref"])
                if propagate:
                    self._propagate(f["ref"])
            # met à jour le mtime stocké (qu'on ait re-propagé ou non)
            self.cat.conn.execute(
                "INSERT INTO last_modify_data(data_type, ref, last_modify_at, "
                "modify_count) VALUES (?, ?, ?, 1) "
                "ON CONFLICT(data_type, ref) DO UPDATE SET last_modify_at = ?",
                (DATA_FILE_TYPE, f["ref"], str(m), str(m)))
            try:
                self.cat.conn.execute(
                    f"UPDATE {DATA_FILE_TYPE}_catalogue SET value = ? "
                    f"WHERE ref = ? AND status = 'missing'", (h, f["ref"]))
                self.cat.conn.execute(
                    f"UPDATE {DATA_FILE_TYPE}_catalogue SET status = 'active', "
                    f"value = ? WHERE ref = ?", (h, f["ref"]))
            except Exception:
                pass
        try:
            self.cat.conn.commit()
        except Exception:
            pass
        return {"checked": checked, "modified": modified,
                "stale_refs": stale_refs}

    def _propagate(self, file_ref: str, _seen=None) -> int:
        """Marque STALE les data_genere qui dépendent de ce fichier,
        récursivement (rebond dans gen_dependance : data → dépendants).

        Deux liens :
          - les SYMBOLS qui vivent DANS ce fichier (id_data =
            `symbol:<fichier>:<fonction>`) ;
          - les data qui déclarent ce fichier comme dépendance
            (gen_dependance.dep_ref)."""
        if _seen is None:
            _seen = set()
        n = 0
        if file_ref in _seen:
            return n
        _seen.add(file_ref)
        # 1) symbols du fichier : id_data 'symbol:<file_ref>:...'
        try:
            for row in self.gen.list(0):
                if row["id_data"].startswith(f"symbol:{file_ref}:"):
                    self.gen.set_status(0, row["id_data"], "stale")
                    n += 1
                    n += self._propagate(row["id_data"], _seen)
        except Exception:
            pass
        # 2) data qui dépendent directement de ce fichier (gen_dependance)
        for dep in self.gen.dependents(0, file_ref):
            data_ref = dep["data_ref"]
            try:
                self.gen.set_status(0, data_ref, "stale")
                n += 1
            except Exception:
                pass
            n += self._propagate(data_ref, _seen)
        return n

    # ── Ticker ──────────────────────────────────────────────

    def start(self, interval_s: Optional[float] = None) -> None:
        if interval_s is None:
            try:
                interval_s = float(self.gen.cfg("stale_check_s", "60"))
            except Exception:
                interval_s = 60.0
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop,
                                        args=(max(1.0, interval_s),),
                                        name="file_watcher", daemon=True)
        self._thread.start()

    def _loop(self, interval: float) -> None:
        while not self._stop.wait(interval):
            try:
                self.check_once()
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)


def _now_shift(seconds: float) -> str:
    from datetime import timedelta
    dt = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=seconds)
    return dt.isoformat()


# ── Pattern matching .conf_gitignore.yaml ─────────────────────

def _pattern_to_regex(pattern: str) -> "re.Pattern":
    """Convertit un glob (gitignore-like) en regex.

    - `**` → n'importe quelle profondeur (y compris rien)
    - `*`  → n'importe quelle séquence dans un segment
    - `?`  → un caractère
    - `[abc]` → classe de caractères
    - `.`  → littéral (échappé)
    - sans `/` au début : match sur le nom OU sur un suffixe de chemin ;
      avec `/` : match relatif à la racine.
    """
    import re
    i = 0
    out = []
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                out.append(".*")
                i += 2
                if i < n and pattern[i] == "/":
                    i += 1  # "**/" → optionnel
                    out.append("(?:.*/)?")
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = i + 1
            while j < n and pattern[j] != "]":
                j += 1
            if j < n:
                out.append(pattern[i:j + 1])
                i = j + 1
            else:
                out.append(r"\[")
                i += 1
        elif c in (".", "+", "(", ")", "^", "$", "|", "\\", "{", "}"):
            out.append("\\" + c)
            i += 1
        else:
            out.append(c)
            i += 1
    return re.compile("".join(out))


def _pattern_match(pattern: str, rel_parts) -> bool:
    """Vrai si le pattern matche le chemin relatif (tuple de segments)."""
    import fnmatch
    # ".*" / "**/.*" : cachés → un segment commence par "."
    if pattern in (".*", "**/.*", "**/.**", ".*/**"):
        return any(seg.startswith(".") for seg in rel_parts)
    # cas simple : extension (".py") → suffixe du dernier segment
    if pattern.startswith(".") and "/" not in pattern:
        return rel_parts[-1].lower().endswith(pattern.lower())
    if not pattern:
        return False
    # glob relatif à la racine (on join les segments)
    rel = "/".join(rel_parts)
    rx = _pattern_to_regex(pattern)
    if rx.search(rel):
        return True
    # fallback : fnmatch sur le NOM du fichier seulement (jamais à travers /)
    try:
        if fnmatch.fnmatch(rel_parts[-1], pattern):
            return True
    except Exception:
        pass
    return False


def _pattern_score(pattern: str) -> int:
    """SPÉCIFICITÉ : nombre de caractères littéraux (non-glob) du pattern.
    Le pattern le plus précis gagne (ex. '*.agent.yaml' > '*.yaml')."""
    score = 0
    for c in pattern:
        if c not in "*?[]{}":
            score += 1
        else:
            score += 0
    # un chemin plus long (plus de '/') est plus spécifique
    score += pattern.count("/") * 2
    return score


def run_service() -> dict:
    """Point d'entrée du service à tick : un passage de vérification +
    propagation stale. Enregistré dans service_ticker (cmd
    "services.file_watcher:run_service")."""
    try:
        fw = FileWatcher()
        return fw.check_once(propagate=True)
    except Exception as e:
        return {"error": str(e)}


__all__ = ["FileWatcher", "DATA_FILE_TYPE", "file_hash", "DEFAULT_RULES",
           "_pattern_match", "_pattern_score", "run_service"]
