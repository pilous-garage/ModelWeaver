import os
import sys
import json
import yaml
import platform
import subprocess
import shutil
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class RecipeError(Exception):
    pass


class RecipeParser:
    """Parse les fichiers .mw.yaml et exécute les recettes d'install/uninstall."""

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.recipe_dir_local = self.project_root / "install_recipe"
        self.installed_dir = self.project_root / "installed_recipe"

        self.os_type = platform.system()
        self.os_key = self._os_key()
        self.arch = self._normalize_arch(platform.machine())

    def _get_tool_dir(self, ref: str) -> Path:
        """Calculates the sharded directory for a given tool reference."""
        padded = ref.lower().ljust(2, 'x')
        return self.recipe_dir_local / padded[0] / padded[1] / f"{ref}.mw"

    # ── Lecture des recettes ──

    def get_index(self) -> Dict[str, Any]:
        path = self.recipe_dir_local / "index.mw.json"
        if not path.exists():
            return {}
        with open(path) as f:
            return json.load(f)

    def _fetch_remote_recipe(self, relative_path: Path) -> Optional[str]:
        """[OBSOLÈTE] Les recettes viennent du catalogue, plus de GitHub.

        Conservé pour compatibilité mais non utilisé par la résolution.
        """
        return None

    def load_recipe(self, ref: str) -> Optional[Dict[str, Any]]:
        """Charge la recette globale d'un outil (Priorité Local -> Fallback Catalogue)."""
        tool_dir = self._get_tool_dir(ref)
        global_file = tool_dir / "global.yaml"

        # 1. Local d'abord (recettes shippées avec l'app)
        if global_file.exists():
            with open(global_file) as f:
                recipe = yaml.safe_load(f)
                if recipe:
                    recipe["_ref"] = ref
                    return recipe

        # 2. Fallback catalogue (recettes synchronisées depuis le catalogue distant)
        return self._load_recipe_from_catalogue(ref)


    def _parse_yaml(self, content: str) -> Dict[str, Any]:
        """Parse un YAML basique (sans dépendance PyYAML)."""
        import json as _json

        # Stocker les lignes qui commencent par des clés top-level
        result = {}
        current_key = None
        current_value: Any = None
        indent_stack: List[Tuple[int, str, Any]] = []
        in_block = False
        block_start = None

        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            # Détecter le début d'un bloc YAML (---)
            if stripped == "---":
                continue

            # Détecter le début d'un bloc de version (clé simple)
            if stripped.startswith("- "):
                # Item de liste
                item = stripped[2:].strip()
                continue

            # Détecter les blocs de clés imbriquées
            indent = len(line) - len(line.lstrip())
            if ":" in stripped:
                key_part = stripped.split(":", 1)[0].strip()
                val_part = stripped.split(":", 1)[1].strip() if ":" in stripped else ""

                # Si c'est une clé avec une valeur (ou une sous-clé)
                if val_part:
                    # Valeur simple: key: value
                    self._set_nested(result, line, stripped)
                else:
                    # Sous-clé: va créer un nouveau niveau
                    pass

        # Fallback: essayer de parser avec json (car notre YAML est assez simple)
        try:
            import yaml
            return yaml.safe_load(content)
        except ImportError:
            pass

        return self._yaml_simple_parse(content)

    def _yaml_simple_parse(self, content: str) -> Dict[str, Any]:
        """Parser YAML minimaliste."""
        lines = content.splitlines()
        root: Dict[str, Any] = {}
        stack: List[Tuple[int, Dict[str, Any]]] = [(-1, root)]
        list_context: Optional[str] = None
        in_list = False
        current_list: List[Any] = []

        for line in lines:
            if not line.strip() or line.strip().startswith("#") or line.strip() == "---":
                continue

            indent = len(line) - len(line.lstrip())
            stripped = line.strip()

            # Ajuster l'indentation
            while stack and indent <= stack[-1][0]:
                stack.pop()
                if not stack:
                    stack.append((-1, root))

            if stripped.startswith("- "):
                # Item de liste
                item_text = stripped[2:].strip()
                if ":" in item_text:
                    item_key = item_text.split(":", 1)[0].strip()
                    item_val = item_text.split(":", 1)[1].strip()
                    # Sous-dictionnaire dans une liste
                    d = {item_key: self._parse_value(item_val)}
                    current_list.append(d)
                else:
                    current_list.append(self._parse_value(item_text))

                # Enregistrer la liste dans le parent
                parent_dict = stack[-1][1] if stack else root
                if list_context and current_list:
                    parent_dict[list_context] = current_list.copy()
                continue

            # Clé-valeur normale
            if ":" in stripped:
                colon_pos = stripped.index(":")
                key = stripped[:colon_pos].strip()
                value_str = stripped[colon_pos + 1:].strip()

                # Si c'est une liste
                if value_str == "" or value_str.startswith("#"):
                    # Sous-clé, nouveau niveau
                    current_list = []
                    list_context = key
                    new_dict = {}
                    parent = stack[-1][1] if stack else root
                    parent[key] = new_dict
                    stack.append((indent, new_dict))
                    continue

                if value_str.startswith("[") and value_str.endswith("]"):
                    inner = value_str[1:-1].strip()
                    if inner:
                        value = [self._parse_value(v.strip()) for v in inner.split(",")]
                    else:
                        value = []
                else:
                    value = self._parse_value(value_str)

                # Assigner la valeur dans le dictionnaire courant
                parent = stack[-1][1] if stack else root
                parent[key] = value

        return root

    @staticmethod
    def _parse_value(val: str) -> Any:
        if not val:
            return None
        if val.lower() == "true":
            return True
        if val.lower() == "false":
            return False
        if val.lower() == "null":
            return None
        if val.startswith('"') and val.endswith('"'):
            return val[1:-1]
        if val.startswith("'") and val.endswith("'"):
            return val[1:-1]
        try:
            return int(val)
        except ValueError:
            pass
        return val

    @staticmethod
    def _set_nested(result: Dict[str, Any], line: str, stripped: str):
        """Place une valeur dans le dictionnaire."""
        if ":" not in stripped:
            return
        key = stripped.split(":", 1)[0].strip()
        val = stripped.split(":", 1)[1].strip()
        if val and val != "":
            result[key] = val

    # ── Résolution de la meilleure recette ──

    def resolve(self, recipe: Dict[str, Any],
                      version: Optional[str] = None,
                      forced_manager: Optional[str] = None) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Trouve le meilleur manager pour l'OS courant.
        
        Phase 1 : catalogue local (catalogue.db)
        Phase 2 : catalogue distant (Turso)
        
        Retourne (version, manager_block) ou None.
        """
        ref = recipe.get("_ref")
        if not ref:
            return None

        # Ordre de priorité des managers
        priority = ["apt", "brew", "winget", "choco", "snap", "pip", "pacman",
                     "dnf", "yum", "zypper", "apk", "emerge", "nix", "flatpak",
                     "cargo", "npm", "go", "binary", "source", "container",
                     "package-//manager"]

        # Phase 1 : catalogue local (catalogue_recettes)
        result = self._resolve_from_catalogue(ref, priority, forced_manager, local=True)
        if result:
            return result

        # Phase 2 : catalogue distant (Turso)
        result = self._resolve_from_catalogue(ref, priority, forced_manager, local=False)
        if result:
            return result

        return None

    def _resolve_from_catalogue(self, ref: str, priority: List[str],
                                 forced_manager: Optional[str],
                                 local: bool) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Résout une recette depuis le catalogue local ou Turso distant."""
        for os_key in [self.os_key, "all"]:
            for arch_key in [self.arch, "all"]:
                if forced_manager:
                    content = (self._catalogue_recipe_content(ref, os_key, arch_key, forced_manager)
                               if local else
                               self._turso_recipe_content(ref, os_key, arch_key, forced_manager))
                    if content:
                        data = yaml.safe_load(content)
                        if data and "block" in data:
                            return (data.get("version", "latest"), data["block"])
                else:
                    for mgr in priority:
                        content = (self._catalogue_recipe_content(ref, os_key, arch_key, mgr)
                                   if local else
                                   self._turso_recipe_content(ref, os_key, arch_key, mgr))
                        if content:
                            data = yaml.safe_load(content)
                            if data and "block" in data:
                                return (data.get("version", "latest"), data["block"])
        return None

    # ── Résolution depuis le catalogue (catalogue_recettes) ──

    def _catalogue_recipe_content(self, ref: str, os_key: str, arch_key: str,
                                  mgr: str) -> Optional[str]:
        """Lit le contenu d'une recette depuis le catalogue (catalogue.db).

        Le catalogue est la source de vérité des recettes (recettes locales shippées
        OU recettes synchronisées depuis le catalogue distant). Pas de GitHub.
        """
        try:
            from modules.sql.db import CatalogueDB
            cat = CatalogueDB()
            row = cat.conn.execute(
                "SELECT r.content FROM catalogue_recettes r "
                "JOIN catalogue_versions v ON v.version_id = r.version_id "
                "JOIN catalogue_outils o ON o.outil_id = v.outil_id "
                "WHERE o.ref = ? AND r.manager = ? "
                "  AND r.os IN (?, 'all') AND r.arch IN (?, 'all') "
                "ORDER BY (r.os = ?) DESC, (r.arch = ?) DESC, r.confidence DESC "
                "LIMIT 1",
                (ref, mgr, os_key, arch_key, os_key, arch_key)).fetchone()
            return row["content"] if row and row["content"] else None
        except Exception:
            return None

    def _turso_recipe_content(self, ref: str, os_key: str, arch_key: str,
                              mgr: str) -> Optional[str]:
        """Lit le contenu d'une recette depuis le catalogue distant (Turso)."""
        try:
            from modules.sql.db import TursoCatalogueDB
            turso = TursoCatalogueDB()
            return turso.get_recipe_content(ref, mgr, os_key, arch_key)
        except Exception:
            return None

    def _load_recipe_from_catalogue(self, ref: str) -> Optional[Dict[str, Any]]:
        """Reconstruit la recette globale d'un outil depuis le catalogue.
        
        Phase 1 : catalogue local (catalogue.db)
        Phase 2 : catalogue distant (Turso)
        """
        # Phase 1 : local
        result = self._load_recipe_from_db(ref, local=True)
        if result:
            return result
        # Phase 2 : Turso distant
        return self._load_recipe_from_db(ref, local=False)

    def _load_recipe_from_db(self, ref: str, local: bool) -> Optional[Dict[str, Any]]:
        """Charge une recette depuis le catalogue local ou Turso distant."""
        try:
            if local:
                from modules.sql.db import CatalogueDB
                db = CatalogueDB()
                o = db.conn.execute(
                    "SELECT ref, nom, description FROM catalogue_outils WHERE ref = ?",
                    (ref,)).fetchone()
                if not o:
                    return None
                rows = db.conn.execute(
                    "SELECT os, arch, manager FROM catalogue_recettes r "
                    "JOIN catalogue_versions v ON v.version_id = r.version_id "
                    "JOIN catalogue_outils o2 ON o2.outil_id = v.outil_id "
                    "WHERE o2.ref = ?", (ref,)).fetchall()
            else:
                from modules.sql.db import TursoCatalogueDB
                db = TursoCatalogueDB()
                o = db.get_tool(ref)
                if not o:
                    return None
                cur = db.client.execute(
                    "SELECT r.os, r.arch, r.manager FROM catalogue_recettes r "
                    "JOIN catalogue_versions v ON v.version_id = r.version_id "
                    "JOIN catalogue_outils o2 ON o2.outil_id = v.outil_id "
                    "WHERE o2.ref = ?", (ref,))
                cols = [d[0] for d in cur.description]
                rows_raw = cur.fetchall()
                rows = [dict(zip(cols, r)) for r in rows_raw]

            rec = {
                "_ref": ref,
                "name": o["nom"] if local else o.get("nom") or o.get("name"),
                "description": o.get("description"),
                "versions": {"default": {}},
                "pre_install": [],
            }
            ver = rec["versions"]["default"]
            for r in rows:
                ver.setdefault(r["os"], {})[r["manager"]] = {}
            if not ver:
                return None
            return rec
        except Exception:
            return None


    @staticmethod
    def find_manager(recipe: Dict[str, Any], version: Optional[str] = None) -> Optional[str]:
        """Trouve le premier manager disponible pour l'OS courant."""
        # Lookup par OS
        os_key = platform.system().lower()
        if os_key == "darwin":
            os_key = "macos"
        elif os_key == "windows":
            os_key = "windows"
        else:
            os_key = "linux"

        versions = recipe.get("versions", {})
        target = version or versions.get("default")
        if not target or target not in versions:
            return None

        managers = versions[target].get(os_key, versions[target].get("all", {}))
        if not managers:
            return None

        # Return the first manager key
        for key in managers:
            return key
        return None

    # ── Exécution ──

    def execute_install(self, recipe: Dict[str, Any],
                            version: Optional[str] = None,
                            progress_callback=None,
                            download_path: Optional[Path] = None,
                            forced_manager: Optional[str] = None) -> bool:
        """Exécute l'install complète à partir d'une recette."""
        print(f"DEBUG: Executing install for recipe: {recipe.get('name')}")
        print(f"DEBUG: Pre-install steps: {recipe.get('pre_install')}")
        resolved = self.resolve(recipe, version)
        if not resolved:
            if progress_callback:
                progress_callback(100, "No compatible manager found")
            return False

        target_version, manager_block = resolved
        manager_name = None
        versions = recipe.get("versions", {})
        ver = version or target_version
        if isinstance(ver, str) and ver in versions:
            os_block = versions[ver].get(self.os_key, versions[ver].get("all", {}))
            for k, v in os_block.items():
                if v == manager_block:
                    manager_name = k
                    break

        step = 0

        # 1. pre_install
        pre = recipe.get("pre_install", [])
        for i, cmd in enumerate(pre):
            step += 1
            if progress_callback:
                progress_callback(int(step / (len(pre) + 4) * 100), f"Pre: {self._cmd_text(cmd)}")
            if not self._run_command(cmd, recipe, target_version, download_path=download_path):
                if progress_callback:
                    progress_callback(100, "Pre-install step failed")
                return False

        # 2. pre_steps du manager
        pre_steps = manager_block.get("pre_steps", [])
        for i, cmd in enumerate(pre_steps):
            step += 1
            if progress_callback:
                progress_callback(int(step / (len(pre) + len(pre_steps) + 4) * 100),
                                   f"Prep: {self._cmd_text(cmd)}")
            if not self._run_command(cmd, recipe, target_version, manager_block, download_path=download_path):
                if progress_callback:
                    progress_callback(100, "Preparation step failed")
                return False


        # 3. Install commands
        install_cmds = manager_block.get("install", manager_block.get("commands", []))
        if not install_cmds and manager_block.get("package"):
            # Default install command
            default_cmd = self._default_install_cmd(manager_name, manager_block)
            if default_cmd:
                install_cmds = [default_cmd]

        if not install_cmds:
            if progress_callback:
                progress_callback(100, "No install commands")
            return False

        for i, cmd in enumerate(install_cmds):
            step += 1
            pct = int(step / (len(pre) + len(pre_steps) + len(install_cmds) + 3) * 100)
            if progress_callback:
                progress_callback(pct, f"Install: {self._cmd_text(cmd)}")
            if not self._run_command(cmd, recipe, target_version, manager_block, download_path=download_path):
                if progress_callback:
                    progress_callback(100, f"Install command {i+1} failed")
                return False

        # 4. post_install
        post = recipe.get("post_install", [])
        for i, cmd in enumerate(post):
            step += 1
            pct = int(step / (len(pre) + len(pre_steps) + len(install_cmds) + len(post) + 2) * 100)
            if progress_callback:
                progress_callback(pct, f"Post: {self._cmd_text(cmd)}")
            self._run_command(cmd, recipe, target_version)

        if progress_callback:
            progress_callback(100, f"Installed via {manager_name or '?'}")

        return True

    def execute_uninstall(self, recipe: Dict[str, Any],
                          version: Optional[str] = None,
                          progress_callback=None) -> bool:
        """Désinstalle à partir d'une recette."""
        resolved = self.resolve(recipe, version)
        if not resolved:
            if progress_callback:
                progress_callback(100, "No compatible manager found")
            return False

        target_version, manager_block = resolved
        uninstall_cmds = manager_block.get("uninstall", [])
        if not uninstall_cmds and manager_block.get("package"):
            uninstall_cmds = [self._default_uninstall_cmd(manager_block)]

        if not uninstall_cmds:
            if progress_callback:
                progress_callback(100, "No uninstall commands")
            return False

        total = len(uninstall_cmds)
        for i, cmd in enumerate(uninstall_cmds):
            if progress_callback:
                progress_callback(int(i / total * 80), f"Uninstall: {self._cmd_text(cmd)}")
            self._run_command(cmd, recipe, target_version)

        # post_uninstall
        post = recipe.get("post_uninstall", [])
        for i, cmd in enumerate(post):
            if progress_callback:
                progress_callback(80 + int(i / len(post) * 20), f"Cleanup: {self._cmd_text(cmd)}")
            self._run_command(cmd, recipe, target_version)

        if progress_callback:
            progress_callback(100, "Uninstalled")

        return True

    # ── Commandes ──

    def _run_command(self, cmd_spec: Any, recipe: Dict[str, Any],
                        version: str, manager_block: Optional[Dict] = None,
                        download_path: Optional[Path] = None) -> bool:
        """Exécute une commande unique (string ou dict)."""
        if isinstance(cmd_spec, str):
            cmd_str = cmd_spec
            sudo = False
            shell = True
            continue_on_error = False
        else:
            cmd_str = cmd_spec.get("command", "")
            sudo = cmd_spec.get("sudo", False)
            shell = cmd_spec.get("shell", True)
            continue_on_error = cmd_spec.get("continue_on_error", False)

        if not cmd_str:
            return True

        # Remplacer les variables
        cmd_str = self._subst(cmd_str, recipe, version, manager_block, download_path=download_path)

        try:
            if sudo and hasattr(os, "getuid") and os.getuid() != 0:
                cmd_str = f"sudo {cmd_str}"

            # Fix pour PEP 668 (Ubuntu 24.04+)
            if "--break-system-packages" not in cmd_str and ("pip install" in cmd_str or "pip uninstall" in cmd_str):
                cmd_str += " --break-system-packages"

            if shell:
                print(f"DEBUG: Executing shell command: {cmd_str}")
                result = subprocess.run(cmd_str, shell=True, capture_output=True,
                                        text=True, timeout=1800)
            else:
                print(f"DEBUG: Executing list command: {cmd_str.split()}")
                result = subprocess.run(cmd_str.split(), capture_output=True,
                                        text=True, timeout=120)

            if result.returncode != 0 and not continue_on_error:
                print(f"DEBUG: Command failed with return code {result.returncode}. stderr: {result.stderr}")
                return False
            return True

        except subprocess.TimeoutExpired:
            print("DEBUG: Command timed out")
            return False
        except Exception as e:
            print(f"DEBUG: Exception during command execution: {e}")
            return False

    def _subst(self, text: str, recipe: Dict[str, Any],
                   version: str, manager: Optional[Dict] = None,
                   download_path: Optional[Path] = None) -> str:
        """Remplace les variables {} dans le texte."""
        home = Path.home()
        extract_to = manager.get("extract_to", "/usr/local/bin") if manager else "/usr/local/bin"
        package = manager.get("package", recipe.get("name", "")) if manager else recipe.get("name", "")

        return text.format(
            tool_name=recipe.get("name", ""),
            version=version,
            home=str(home),
            extract_to=extract_to,
            archive=str(download_path) if download_path else "{download_path}",
            download_path=str(download_path) if download_path else "{download_path}",
            package=package,
            os=self.os_key,
        )

    @staticmethod
    def _cmd_text(cmd_spec: Any) -> str:
        if isinstance(cmd_spec, str):
            return cmd_spec[:60]
        return cmd_spec.get("command", "?")[:60]

    @staticmethod
    def _default_install_cmd(manager: str, block: Dict) -> Optional[str]:
        return None

    @staticmethod
    def _default_uninstall_cmd(block: Dict) -> Optional[str]:
        pkg = block.get("package")
        if pkg:
            return f"pip uninstall -y --break-system-packages {pkg}"
        return None

    def _os_key(self) -> str:
        s = self.os_type.lower()
        if s == "darwin":
            return "macos"
        if s == "windows":
            return "windows"
        return "linux"

    def get_ideal_sharding_path(self, ref: str, manager: str, os_override: Optional[str] = None, arch_override: Optional[str] = None) -> Path:
        """Suggère le chemin de sharding idéal selon le manager et les requirements.
        
        Logique :
        - pip, npm, go, cargo -> all/all
        - apt, dnf, pacman, snap -> linux/all
        - brew -> macos/all
        - winget, choco -> windows/all
        - binary, source -> os/arch (spécifique)
        """
        os_val = os_override or "all"
        arch_val = arch_override or "all"
        
        universal_managers = ["pip", "npm", "go", "cargo"]
        linux_managers = ["apt", "dnf", "pacman", "snap"]
        macos_managers = ["brew"]
        windows_managers = ["winget", "choco"]
        specific_managers = ["binary", "source"]
        
        if manager in universal_managers:
            os_val, arch_val = "all", "all"
        elif manager in linux_managers:
            os_val = "linux"
        elif manager in macos_managers:
            os_val = "macos"
        elif manager in windows_managers:
            os_val = "windows"
        elif manager in specific_managers:
            # Pour binary/source, on force l'utilisation de l'OS/Arch courants si non fournis
            os_val = os_override or self.os_key
            arch_val = arch_override or self.arch
            
        tool_dir = self._get_tool_dir(ref)
        return tool_dir / os_val / arch_val / f"{manager}.yaml"

    @staticmethod
    def _normalize_arch(raw: str) -> str:
        mapping = {"amd64": "x86_64", "x86_64": "x86_64",
                   "aarch64": "aarch64", "arm64": "aarch64"}
        return mapping.get(raw, raw)
