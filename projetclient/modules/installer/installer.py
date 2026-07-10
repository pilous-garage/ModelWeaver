import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .recipe_parser import RecipeParser


class Installer:
    """Installe un outil à partir d'une ligne du catalogue ou d'une recette .mw.yaml.

    Usage:
        installer = Installer()
        tool = {"ref": "ollama", "tool_type": "binary",
                "install_method": "github-release",
                "default_download_url": "https://..."}
        installer.install(tool)
    """

    def __init__(self, cache_dir: Optional[Path] = None, project_root: Optional[Path] = None):
        self.os_type = platform.system()
        self.distro = self._get_distro()
        self.arch = self._normalize_arch(platform.machine())
        self.cache_dir = Path(cache_dir) if cache_dir else Path.home() / ".modelweaver" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.recipe_parser = RecipeParser(project_root=project_root)

    # ── API publique ──

    def install(self, tool: Dict[str, Any],
                progress_callback: Optional[Callable[[int, str], None]] = None,
                    keep_cache: bool = False,
                    forced_manager: Optional[str] = None) -> bool:
        """Installe un outil.
        
        Si le tool a un recipe_path, utilise la recette .mw.yaml prioritairement.
        Sinon, tente de charger une recette par ref, puis fallback legacy.
        """
        ref = tool.get("ref") or tool.get("name", "?")
        if progress_callback:
            progress_callback(0, f"Vérification de {ref}...")

        # Priorité 1: Recette via recipe_path
        recipe = None
        recipe_path = tool.get("recipe_path")
        if recipe_path:
            recipe = self.recipe_parser.load_recipe(ref)
        else:
            # Priorité 2: Recette via ref (recherche dans le sharding)
            recipe = self.recipe_parser.load_recipe(ref)

        if recipe:
            if progress_callback:
                progress_callback(5, f"Utilisation de la recette {ref}")
            # Résolution du chemin de téléchargement pour les binaires
            download_path = None
            if tool.get("tool_type") == "binary":
                # Résoudre le manager block pour obtenir l'URL spécifique
                resolved = self.recipe_parser.resolve(recipe, forced_manager=forced_manager)
                url = None
                if resolved:
                    version, manager_block = resolved
                    url = manager_block.get("url")
                
                if not url and recipe and "url" in recipe:
                    url = recipe["url"]
                elif not url and tool.get("default_download_url"):
                    url = tool.get("default_download_url")
                
                if url:
                    download_path = self._cached_download(url, ref)

            ok = self.recipe_parser.execute_install(
                recipe, tool.get("current_version"), progress_callback,
                forced_manager=forced_manager, download_path=download_path)
            if ok:
                print(f"  ✅ {ref} installé via recette")
            return ok

        # Fallback legacy
        return self._install_legacy(tool, ref, progress_callback)

    def uninstall(self, tool: Dict[str, Any], install_path: Optional[str] = None,
                   progress_callback: Optional[Callable[[int, str], None]] = None) -> bool:
        """Désinstalle un outil."""
        ref = tool.get("ref") or tool.get("name", "?")
        if progress_callback:
            progress_callback(10, f"Désinstallation de {ref}...")

        # Essayer la recette
        recipe = self.recipe_parser.load_recipe(ref)
        if recipe:
            ok = self.recipe_parser.execute_uninstall(
                recipe, tool.get("current_version"), progress_callback)
            return ok

        # Fallback legacy
        return self._uninstall_legacy(tool, ref, install_path, progress_callback)

    # ── Vérifications ──

    def _compatible_platform(self, tool: Dict[str, Any]) -> bool:
        allowed_platforms = tool.get("allowed_platforms")
        if allowed_platforms:
            platforms = [p.strip() for p in allowed_platforms.split(",")]
            if self.os_type not in platforms and self.distro not in platforms:
                return False
        allowed_arches = tool.get("allowed_arches")
        if allowed_arches:
            arches = [a.strip() for a in allowed_arches.split(",")]
            if self.arch not in arches:
                return False
        return True

    # ── Fallback legacy ──

    def _install_legacy(self, tool: Dict[str, Any], ref: str,
                        progress_callback: Optional[Callable[[int, str], None]] = None) -> bool:
        if not self._compatible_platform(tool):
            if progress_callback:
                progress_callback(100, f"{ref} : non compatible {self.os_type}/{self.arch}")
            print(f"  ⏭️  {ref} : non compatible {self.os_type}/{self.arch}")
            return False

        method = tool.get("install_method")
        if not method:
            raise ValueError(f"install_method manquant pour {ref}")

        dispatch = {
            "pip": self._install_via_pip,
            "apt": self._install_via_apt,
            "brew": self._install_via_brew,
            "winget": self._install_via_winget,
            "package-manager": self._install_via_pkg_mgr,
            "direct-url": self._install_via_url,
            "github-release": self._install_via_github,
            "installer-script": self._install_via_script,
        }
        handler = dispatch.get(method)
        if not handler:
            raise ValueError(f"Méthode inconnue '{method}' pour {ref}")

        if progress_callback:
            progress_callback(15, f"Installation de {ref} via {method}...")
        print(f"  📦 {ref} : {method}")
        ok = handler(tool)
        if ok:
            if progress_callback:
                progress_callback(90, f"Finalisation de {ref}...")
            print(f"  ✅ {ref} installé")
        else:
            if progress_callback:
                progress_callback(100, f"Échec de {ref}")
            print(f"  ❌ {ref} : échec")
        return ok

    def _uninstall_legacy(self, tool: Dict[str, Any], ref: str, install_path: Optional[str] = None,
                          progress_callback: Optional[Callable[[int, str], None]] = None) -> bool:
        method = tool.get("install_method", "direct-url")
        ttype = tool.get("tool_type", "binary")

        if method == "pip" or ttype == "python-module":
            pkg = tool.get("name", ref)
            if progress_callback:
                progress_callback(50, f"pip uninstall {pkg}...")
            return self._run(["pip", "uninstall", "-y", pkg])

        if method == "apt":
            pkg = tool.get("name", ref)
            if progress_callback:
                progress_callback(50, f"apt remove {pkg}...")
            return self._run_apt_remove(pkg)

        if method == "brew":
            pkg = tool.get("name", ref)
            if progress_callback:
                progress_callback(50, f"brew uninstall {pkg}...")
            return self._run(["brew", "uninstall", pkg])

        if install_path:
            path = Path(install_path)
            if path.exists():
                if progress_callback:
                    progress_callback(70, f"Suppression de {path}...")
                path.unlink()
                if progress_callback:
                    progress_callback(100, f"{ref} désinstallé")
                return True
            if progress_callback:
                progress_callback(100, f"{ref} : fichier introuvable")
            print(f"  ⏭️  {ref} : fichier introuvable ({install_path})")
            return True

        if progress_callback:
            progress_callback(100, f"{ref} : désinstall non supporté ({method})")
        print(f"  ⏸️  uninstall({ref}) pas encore implémentée pour {method}")
        return False

    # ── Handlers d'installation ──

    def _install_via_pip(self, tool: Dict[str, Any]) -> bool:
        pkg = self._get_params(tool).get("package", tool["ref"])
        return self._run(["pip", "install", pkg])

    def _install_via_apt(self, tool: Dict[str, Any]) -> bool:
        pkg = self._get_params(tool).get("package", tool["ref"])
        return self._run_apt(pkg)

    def _install_via_brew(self, tool: Dict[str, Any]) -> bool:
        pkg = self._get_params(tool).get("package", tool["ref"])
        return self._run(["brew", "install", pkg])

    def _install_via_winget(self, tool: Dict[str, Any]) -> bool:
        pkg = self._get_params(tool).get("package", tool["ref"])
        return self._run(["winget", "install", "--id", pkg])

    def _install_via_pkg_mgr(self, tool: Dict[str, Any]) -> bool:
        pkg = self._get_params(tool).get("package", tool["ref"])
        if self.os_type == "Linux":
            return self._run_apt(pkg)
        elif self.os_type == "Darwin":
            return self._run(["brew", "install", pkg])
        elif self.os_type == "Windows":
            return self._run(["winget", "install", "--id", pkg])
        print(f"  ⚠️  Pas de gestionnaire de paquets pour {self.os_type}")
        return False

    def _install_via_url(self, tool: Dict[str, Any]) -> bool:
        url = tool.get("default_download_url")
        if not url:
            print("  ⚠️  default_download_url manquant")
            return False
        dest = self._cached_download(url, tool["ref"])
        if not dest:
            return False
        return self._deploy_asset(dest, tool)

    def _install_via_github(self, tool: Dict[str, Any]) -> bool:
        """Télécharge depuis un binaire GitHub et le déploie."""
        url = tool.get("default_download_url")
        if not url:
            repo = tool.get("ref")
            url = self._github_release_url(repo)
        if not url:
            print("  ⚠️  Pas d'URL de téléchargement pour github-release")
            return False
        dest = self._cached_download(url, tool["ref"])
        if not dest:
            return False
        return self._deploy_asset(dest, tool)

    def _install_via_script(self, tool: Dict[str, Any]) -> bool:
        url = tool.get("default_download_url")
        if url:
            dest = self._cached_download(url, tool["ref"])
            if not dest:
                return False
            dest.chmod(0o755)
            return self._run([str(dest)])
        script = self._get_params(tool).get("script")
        if script:
            return self._run(["bash", "-c", script])
        print("  ⚠️  installer-script sans URL ni script param")
        return False

    # ── Utilitaires ──

    def _get_params(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        raw = tool.get("installer_params")
        if isinstance(raw, str):
            return json.loads(raw) if raw else {}
        return raw or {}

    def _run(self, cmd: list) -> bool:
        if len(cmd) >= 2 and os.path.basename(cmd[0]) == "pip" and cmd[1] == "install":
            if "--break-system-packages" not in cmd:
                cmd.append("--break-system-packages")
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return True
        except FileNotFoundError:
            print(f"  ⚠️  Commande introuvable : {cmd[0]}")
            return False
        except subprocess.CalledProcessError as e:
            print(f"  ⚠️  {cmd[0]} a échoué : {e.stderr[:200] if e.stderr else e}")
            return False
        except Exception as e:
            print(f"  ⚠️  Erreur inattendue : {e}")
            return False
        except FileNotFoundError:
            print(f"  ⚠️  Commande introuvable : {cmd[0]}")
            return False
        except subprocess.CalledProcessError as e:
            print(f"  ⚠️  {cmd[0]} a échoué : {e.stderr[:200] if e.stderr else e}")
            return False

    def _run_apt(self, pkg: str) -> bool:
        use_sudo = hasattr(os, "getuid") and os.getuid() != 0
        prefix = ["sudo"] if use_sudo else []
        try:
            subprocess.run(prefix + ["apt-get", "install", "-y", pkg],
                           capture_output=True, check=True)
            return True
        except subprocess.CalledProcessError as e:
            print(f"  ⚠️  apt install {pkg} : {e.stderr[:200] if e.stderr else e}")
            return False

    def _run_apt_remove(self, pkg: str) -> bool:
        use_sudo = hasattr(os, "getuid") and os.getuid() != 0
        prefix = ["sudo"] if use_sudo else []
        try:
            subprocess.run(prefix + ["apt-get", "remove", "-y", pkg],
                           capture_output=True, check=True)
            return True
        except subprocess.CalledProcessError as e:
            print(f"  ⚠️  apt remove {pkg} : {e.stderr[:200] if e.stderr else e}")
            return False

    def _cached_download(self, url: str, name: str) -> Optional[Path]:
        filename = url.split("/")[-1] or f"{name}.bin"
        dest = self.cache_dir / filename
        if dest.exists():
            print(f"  📦 Utilisation du cache : {dest.name}")
            return dest
        print(f"  ⬇️  Téléchargement de {name}...")
        try:
            import urllib.request
            urllib.request.urlretrieve(url, dest)
            return dest
        except Exception as e:
            print(f"  ⚠️  Téléchargement échoué : {e}")
            return None

    def _deploy_asset(self, asset: Path, tool: Dict[str, Any]) -> bool:
        ttype = tool.get("tool_type", "binary")
        if ttype == "python-module":
            return self._run([sys.executable, "-m", "pip", "install", str(asset)])

        if asset.suffix in (".tar.gz", ".tgz"):
            extract_dir = self.cache_dir / f"{tool['ref']}_extracted"
            extract_dir.mkdir(exist_ok=True)
            with tarfile.open(asset, "r:gz") as tar:
                tar.extractall(extract_dir)
            return self._install_from_dir(extract_dir, tool)

        if asset.suffix == ".zip":
            extract_dir = self.cache_dir / f"{tool['ref']}_extracted"
            extract_dir.mkdir(exist_ok=True)
            with zipfile.ZipFile(asset, "r") as z:
                z.extractall(extract_dir)
            return self._install_from_dir(extract_dir, tool)

        # Binaire simple : copier dans /usr/local/bin
        local_bin = Path("/usr/local/bin")
        if not local_bin.exists():
            local_bin = Path.home() / ".local" / "bin"
            local_bin.mkdir(parents=True, exist_ok=True)
        dest = local_bin / tool["ref"]
        shutil.copy2(asset, dest)
        dest.chmod(0o755)
        return True

    def _install_from_dir(self, extract_dir: Path, tool: Dict[str, Any]) -> bool:
        bin_name = tool.get("ref")
        candidates = list(extract_dir.rglob(bin_name))
        if candidates:
            local_bin = Path("/usr/local/bin")
            if not local_bin.exists():
                local_bin = Path.home() / ".local" / "bin"
                local_bin.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidates[0], local_bin / bin_name)
            (local_bin / bin_name).chmod(0o755)
            shutil.rmtree(extract_dir, ignore_errors=True)
            return True
        # Sinon, ajouter le dossier extrait au PATH
        print(f"  ⚠️  Binaire '{bin_name}' introuvable dans l'archive")
        shell_cfg = Path.home() / ".bashrc"
        if shell_cfg.exists():
            with open(shell_cfg, "a") as f:
                f.write(f'\nexport PATH="{extract_dir}:$PATH"\n')
        return True

    def _github_release_url(self, repo: str) -> Optional[str]:
        patterns = {
            ("Linux", "x86_64"): f"linux-amd64",
            ("Linux", "aarch64"): f"linux-arm64",
            ("Darwin", "x86_64"): f"darwin-amd64",
            ("Darwin", "aarch64"): f"darwin-arm64",
        }
        suffix = patterns.get((self.os_type, self.arch))
        if not suffix:
            return None
        return (f"https://github.com/{repo}/{repo}/releases/latest/download/"
                f"{repo}_{suffix}.tar.gz")

    def _get_distro(self) -> str:
        try:
            if os.path.exists("/etc/os-release"):
                with open("/etc/os-release") as f:
                    for line in f:
                        if line.startswith("ID="):
                            return line.split("=")[1].strip().strip('"')
        except Exception:
            pass
        return "unknown"

    @staticmethod
    def _normalize_arch(raw: str) -> str:
        mapping = {"amd64": "x86_64", "x86_64": "x86_64",
                   "aarch64": "aarch64", "arm64": "aarch64"}
        return mapping.get(raw, raw)


if __name__ == "__main__":
    inst = Installer()
    print(f"OS={inst.os_type} Distro={inst.distro} Arch={inst.arch} Cache={inst.cache_dir}")
