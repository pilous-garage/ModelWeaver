#!/usr/bin/env python3
"""ModelWeaver — orchestration et installation de composants IA."""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import textwrap
import time
import typing
import urllib.request
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / ".modelweaver_config"
MANIFEST_FILE = APP_DIR / "manifest.json"
CACHE_DIR = APP_DIR / ".modelweaver" / "cache"
CLEANUP_MANIFEST = APP_DIR / "cleanup_manifest.json"
REQUIRED_PYTHON = (3, 10)

# Dépendances système par OS
SYSTEM_DEPS = {
    "linux": [
        ("curl", "curl", "apt", "curl"),
        ("git", "git", "apt", "git"),
        ("ca-certificates", "update-ca-certificates", "apt", "ca-certificates"),
        ("build-essential", "gcc", "apt", "build-essential"),
        ("python3-venv", "python3 -m venv --help", "apt", "python3-venv"),
        ("python3-pip", "pip3 --version", "apt", "python3-pip"),
        ("unzip", "unzip", "apt", "unzip"),
        ("zstd", "zstd --version", "apt", "zstd"),
    ],
    "darwin": [
        ("git", "git", "brew", "git"),
    ],
    "win32": [
        ("git", "git", "winget", "Git.Git"),
    ],
}


# ─── Mode ──────────────────────────────────────────────────────────────

def read_mode() -> str:
    if CONFIG_FILE.exists():
        return CONFIG_FILE.read_text().strip()
    return ""


def write_mode(mode: str) -> None:
    CONFIG_FILE.write_text(mode)


def is_interactive() -> bool:
    return sys.stdin.isatty()


def select_mode() -> str:
    mode = read_mode()
    if mode:
        print(f"⚙️  Mode configuré : {mode}")
        return mode

    if not is_interactive():
        print("⚠️  Non-interactif détecté. Passage en mode automatique (YES).")
        write_mode("YES")
        return "YES"

    while True:
        print("\n🛠  ModelWeaver — Mode d'installation")
        print("  [1/Y] Oui à tout (Automatique)")
        print("  [2/N] Non à tout (Check-up seulement)")
        print("  [3/A] Demander à chaque étape (Manuel)")
        print("  [Q]   Quitter")
        choice = input("> ").strip().lower()

        if choice in ("1", "y"):
            mode = "YES"
        elif choice in ("2", "n"):
            mode = "NO"
        elif choice in ("3", "a"):
            mode = "ASK"
        elif choice in ("q",):
            print("👋  Fermeture.")
            sys.exit(0)
        else:
            print("❌  Choix invalide.")
            continue
        break

    write_mode(mode)
    print(f"⚙️  Mode configuré : {mode}")
    return mode


# ─── Audit système ────────────────────────────────────────────────────

def check_python(mode: str) -> None:
    v = sys.version_info
    if v >= REQUIRED_PYTHON:
        print(f"✅  Python {v.major}.{v.minor}.{v.micro} — OK")
        return

    print(f"⚠️  Python {v.major}.{v.minor} détecté, requis {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}+.")
    print(f"   ℹ️  Certains composants peuvent ne pas fonctionner. Pensez à mettre à jour Python.")
    if mode == "NO":
        print("   ⚠️  Check-only : aucune action.")
    elif mode == "ASK" and is_interactive():
        ans = input("   Continuer quand même ? (y/n) > ").strip().lower()
        if ans not in ("y", "yes"):
            sys.exit(1)


def check_ram() -> typing.Tuple[int, str]:
    try:
        if sys.platform == "linux":
            mem = Path("/proc/meminfo").read_text()
            total_kb = int([l for l in mem.splitlines() if "MemTotal" in l][0].split()[1])
            total_mb = total_kb // 1024
        elif sys.platform == "darwin":
            r = subprocess.run(["sysctl", "hw.memsize"], capture_output=True, text=True)
            total_mb = int(r.stdout.strip().split()[1]) // 1024 // 1024
        elif sys.platform == "win32":
            r = subprocess.run(["wmic", "memorychip", "get", "capacity"], capture_output=True, text=True)
            total_bytes = sum(int(l) for l in r.stdout.splitlines()[1:] if l.strip())
            total_mb = total_bytes // 1024 // 1024
        else:
            return (0, "⚠️  RAM : détection non supportée sur ce système")
    except Exception:
        return (0, "⚠️  RAM : impossible de détecter")

    if total_mb < 2048:
        msg = f"⚠️  RAM : {total_mb} Mo — très faible, attendez-vous à des lenteurs"
    elif total_mb < 4096:
        msg = f"⚠️  RAM : {total_mb} Mo — suffisant pour le routage, pas pour Ollama"
    elif total_mb < 8192:
        msg = f"✅  RAM : {total_mb} Mo — correct"
    else:
        msg = f"✅  RAM : {total_mb} Mo — confortable"
    return (total_mb, msg)


def check_os() -> dict:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
    }


def dep_is_present(_name: str, check_cmd: str) -> bool:
    """Vérifie si une dépendance est installée."""
    if shutil.which(check_cmd.split()[0]):
        return True
    # Fallback : exécute la commande de check
    try:
        subprocess.run(check_cmd, shell=True, capture_output=True, check=True)
        return True
    except Exception:
        return False


def dep_install_cmd(dep: tuple) -> typing.Optional[typing.List[str]]:
    """Retourne la commande d'installation selon l'OS."""
    _name, _check, pkg_mgr, pkg = dep
    if sys.platform == "linux":
        if shutil.which("apt"):
            return ["apt", "install", "-y", "-qq",
                    "-o", "APT::Keep-Downloaded-Packages=true", pkg]
        if shutil.which("dnf"):
            return ["dnf", "install", "-y", pkg]
        if shutil.which("pacman"):
            return ["pacman", "-Sy", "--noconfirm", pkg]
    elif sys.platform == "darwin" and shutil.which("brew"):
        return ["brew", "install", pkg]
    elif sys.platform == "win32" and shutil.which("winget"):
        return ["winget", "install", "--accept-source-agreements", pkg]
    return None


def audit(mode: str) -> None:
    print("\n🔍  Audit système")

    check_python(mode)

    os_info = check_os()
    print(f"💻  OS : {os_info['system']} {os_info['release']} ({os_info['machine']})")

    ram_mb, ram_msg = check_ram()
    print(ram_msg)

    # Dépendances système
    deps = SYSTEM_DEPS.get(sys.platform, [])
    if not deps:
        print("⚠️  OS non supporté pour l'installation automatique des dépendances.")
        return

    needs_sudo = shutil.which("sudo") and os.geteuid() != 0

    # Mise à jour des indexes apt si nécessaire
    if sys.platform == "linux" and shutil.which("apt-get"):
        try:
            subprocess.run(
                (["sudo"] if needs_sudo else []) + ["apt-get", "update", "-qq"],
                check=True, capture_output=True,
            )
        except subprocess.CalledProcessError:
            print("   ⚠️  apt-get update a échoué, tentative d'installation quand même")

    for dep in deps:
        name, check_cmd, _pkg_mgr, _pkg = dep
        if dep_is_present(name, check_cmd):
            print(f"✅  {name} — présent")
            continue

        print(f"⚠️  {name} — manquant")
        if mode == "NO":
            continue

        if mode == "ASK" and is_interactive():
            ans = input(f"   Installer {name} ? (y/n) > ").strip().lower()
            if ans not in ("y", "yes"):
                print("   ⏭️  Ignoré")
                continue

        cmd = dep_install_cmd(dep)
        if cmd is None:
            print(f"   ❌  Aucun gestionnaire de paquets connu pour {name}")
            continue

        full_cmd = (["sudo"] if needs_sudo else []) + cmd
        print(f"   📦  Installation de {name}...")
        try:
            subprocess.run(full_cmd, check=True, capture_output=True)
            print(f"   ✅  {name} installé")
        except subprocess.CalledProcessError:
            print(f"   ❌  Échec de l'installation de {name}")
            if mode != "ASK":
                sys.exit(1)

    print()


# ─── Cache ────────────────────────────────────────────────────────────

def init_cache() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def cached_path(filename: str) -> Path:
    return CACHE_DIR / filename


def cache_has(filename: str, expected_sha256: typing.Optional[str] = None) -> bool:
    path = cached_path(filename)
    if not path.exists():
        return False
    if expected_sha256:
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        return h == expected_sha256
    return True


def _download_url(url: str, dest: Path) -> None:
    """Télécharge une URL vers dest, avec curl si disponible."""
    if shutil.which("curl"):
        subprocess.run(
            ["curl", "-fsSL", "--retry", "3", "--retry-delay", "5",
             "-o", str(dest), url],
            check=True, capture_output=True,
        )
    else:
        urllib.request.urlretrieve(url, dest)


def download_with_cache(
    url: str,
    filename: str,
    expected_sha256: typing.Optional[str] = None,
    label: str = "",
) -> Path:
    dest = cached_path(filename)
    label = label or filename

    if cache_has(filename, expected_sha256):
        print(f"   ♻️  {label} déjà en cache")
        return dest

    # Basic sanity : fichier plancher
    min_size = 1024  # 1 Ko minimum

    print(f"   ⬇️  Téléchargement de {label}...")
    try:
        _download_url(url, dest)
        if dest.stat().st_size < min_size:
            raise ValueError(f"Fichier trop petit ({dest.stat().st_size} octets)")
    except Exception as e:
        print(f"   ❌  Échec du téléchargement : {e}")
        if dest.exists():
            dest.unlink()
        sys.exit(1)

    if expected_sha256:
        h = hashlib.sha256(dest.read_bytes()).hexdigest()
        if h != expected_sha256:
            print(f"   ❌  Checksum invalide ({h[:16]}...), fichier supprimé")
            dest.unlink()
            sys.exit(1)

    print(f"   ✅  {label} mis en cache")
    return dest


# ─── Python Package Manager ──────────────────────────────────────────

PACKAGE_MANAGERS = {
    "pip": {
        "check": "pip3 --version",
        "install_cmd": None,
        "priority": 1,
        "label": "pip (standard, pré-installé)",
    },
    "uv": {
        "check": "uv --version",
        "install_cmd": "curl -fsSL https://astral.sh/uv/install.sh | sh",
        "priority": 3,
        "label": "uv (Astral — 10-100x plus rapide, recommandé)",
    },
    "rye": {
        "check": "rye --version",
        "install_cmd": "curl -fsSL https://rye.astral.sh/get | sh",
        "priority": 2,
        "label": "rye (Astral — rapide + gestion de projet)",
    },
}

PKG_MGR_FILE = APP_DIR / ".modelweaver" / ".python_pkg_mgr"


def _pkg_mgr_available(mid: str, info: dict) -> bool:
    """Vérifie si un gestionnaire est disponible (dans PATH ou via check)."""
    if shutil.which(mid):
        return True
    try:
        subprocess.run(info["check"], shell=True, capture_output=True, check=True, executable="/bin/sh")
        return True
    except Exception:
        return False


def _local_bin() -> str:
    """Retourne le chemin ~/.local/bin et s'assure qu'il existe."""
    p = Path.home() / ".local" / "bin"
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def select_package_manager(mode: str) -> str:
    print("\n📦  Python Package Manager")

    # Vérifier les PATH pour les binaires installés localement
    local_bin = _local_bin()
    extra_path = os.environ.get("PATH", "")
    if local_bin not in extra_path:
        os.environ["PATH"] = f"{local_bin}:{extra_path}"

    # Détecter les gestionnaires déjà installés
    installed = [mid for mid, info in PACKAGE_MANAGERS.items() if _pkg_mgr_available(mid, info)]

    for mid in installed:
        print(f"   ✅  {PACKAGE_MANAGERS[mid]['label']} — disponible")

    if not installed:
        print("   ⚠️  Aucun gestionnaire trouvé, pip sera installé via audit")

    # Mode NO : juste reporter
    if mode == "NO":
        choice = installed[0] if installed else "pip"
        print(f"   ℹ️  Mode check — gestionnaire disponible : {choice}")
        return choice

    # Mode YES : priorité uv > rye > pip
    if mode in ("YES",):
        for mid in ("uv", "rye", "pip"):
            if mid in installed:
                print(f"   → Utilisation de {PACKAGE_MANAGERS[mid]['label']}")
                PKG_MGR_FILE.write_text(mid)
                return mid
            info = PACKAGE_MANAGERS[mid]
            if info["install_cmd"] is None:
                continue  # pip : déjà traité dans les installed
            print(f"   → Installation de {mid}...")
            try:
                subprocess.run(info["install_cmd"], shell=True, check=True, capture_output=True, executable="/bin/sh")
                if _pkg_mgr_available(mid, info):
                    print(f"      ✅  {info['label']} installé")
                    PKG_MGR_FILE.write_text(mid)
                    return mid
                print(f"      ⚠️  Binaire introuvable après installation")
            except subprocess.CalledProcessError as e:
                print(f"      ⚠️  Échec ({e.returncode}), fallback...")
        # Dernier recours : pip
        print(f"   → Fallback sur pip")
        PKG_MGR_FILE.write_text("pip")
        return "pip"

    # Mode ASK
    choices = list(PACKAGE_MANAGERS.keys())
    print("   Choisissez le gestionnaire de paquets Python :")
    for i, mid in enumerate(choices, 1):
        info = PACKAGE_MANAGERS[mid]
        status = "✅ disponible" if mid in installed else "📥 à installer"
        print(f"   [{i}] {info['label']} — {status}")

    while True:
        try:
            ans = input("   Votre choix (1-3) > ").strip()
            idx = int(ans) - 1
            if 0 <= idx < len(choices):
                selected = choices[idx]
                break
        except (ValueError, IndexError, EOFError):
            pass
        print("   Choix invalide.")

    info = PACKAGE_MANAGERS[selected]
    if selected not in installed and info["install_cmd"] is not None:
        print(f"   → Installation de {selected}...")
        try:
            subprocess.run(info["install_cmd"], shell=True, check=True, executable="/bin/sh")
            print(f"      ✅  Installé")
        except subprocess.CalledProcessError:
            print(f"      ❌  Échec, fallback sur pip")
            selected = "pip"

    PKG_MGR_FILE.write_text(selected)
    print(f"   ✅  Gestionnaire sélectionné : {selected}")
    return selected


# ─── Manifeste ────────────────────────────────────────────────────────

def load_manifest() -> dict:
    if not MANIFEST_FILE.exists():
        print("❌  manifest.json introuvable.")
        sys.exit(1)
    return json.loads(MANIFEST_FILE.read_text())


# ─── Installation ─────────────────────────────────────────────────────

def _ollama_models() -> None:
    """Vérifie les modèles Ollama existants."""
    if not shutil.which("ollama"):
        return
    try:
        r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            models = [l for l in r.stdout.strip().splitlines() if l.strip() and not l.startswith("NAME")]
            if models:
                print(f"      📋  Modèles existants ({len(models)}) :")
                for m in models:
                    print(f"         {m.split()[0]}")
            else:
                print(f"      📋  Aucun modèle installé")
        else:
            print(f"      ℹ️  Serveur Ollama pas encore prêt")
    except Exception as e:
        print(f"      ℹ️  Vérification modèles : {e}")


def _install_ollama(comp_id: str, comp: dict, mode: str) -> None:
    name = comp["name"]
    print(f"   → Installation du binaire {name}...")

    # Vérifier RAM
    ram_mb, ram_msg = check_ram()
    if ram_mb > 0 and ram_mb < 8192:
        print(f"   ⚠️  RAM : {ram_mb} Mo (< 8 Go) — Ollama désactivé")
        return

    # Vérifier si déjà installé
    if shutil.which("ollama"):
        print(f"   ✅  {name} déjà installé")
        _ollama_models()
        return

    # Déterminer OS/ARCH
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch_map = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
    arch = arch_map.get(machine)
    if not arch or system not in ("linux", "darwin"):
        print(f"   ⚠️  Système non supporté : {system} {machine}")
        return

    # Télécharger
    ver_param = f"?{os.environ.get('OLLAMA_VERSION', '')}"
    base_url = "https://ollama.com/download"
    filename = f"ollama-{system}-{arch}.tar.zst"
    url = f"{base_url}/{filename}{ver_param}"
    print(f"   📦  Téléchargement d'Ollama...")
    dest = download_with_cache(url, filename, label="Ollama")

    # Extraire dans /usr/local
    print(f"   📂  Extraction...")
    # Vérifier l'intégrité avant extraction
    try:
        subprocess.run(
            f"zstd -t '{dest}'",
            shell=True, check=True, capture_output=True, executable="/bin/sh",
        )
    except subprocess.CalledProcessError:
        print(f"      ⚠️  Archive corrompue, re-téléchargement...")
        dest.unlink(missing_ok=True)
        dest = download_with_cache(url, filename, label="Ollama")
    try:
        subprocess.run(
            f"zstd -d -c '{dest}' | tar xf - -C /usr/local",
            shell=True, check=True, executable="/bin/sh",
        )
        print(f"      ✅  Binaire extrait dans /usr/local/bin/ollama")
    except subprocess.CalledProcessError as e:
        print(f"      ❌  Erreur d'extraction : {e}")
        if mode != "ASK":
            sys.exit(1)
        return

    # Démarrer le service
    print(f"   🚀  Démarrage du serveur Ollama...")
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Attendre que le serveur soit prêt
        for _ in range(10):
            time.sleep(1)
            r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                print(f"      ✅  Serveur prêt")
                break
        else:
            print(f"      ⚠️  Serveur pas encore prêt (timeout)")
    except Exception as e:
        print(f"      ⚠️  Impossible de démarrer le serveur : {e}")

    # Vérifier les modèles
    _ollama_models()


GITHUB_ARCH = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}
GITHUB_OS = {"linux": "linux", "darwin": "darwin", "win32": "windows"}


def _install_opencode(comp_id: str, comp: dict, mode: str) -> None:
    name = comp["name"]
    print(f"   → Installation du binaire {name}...")

    if shutil.which("opencode"):
        print(f"   ✅  {name} déjà installé")
        return

    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = GITHUB_ARCH.get(machine)
    os_name = GITHUB_OS.get(system)
    if not arch or not os_name:
        print(f"   ⚠️  Système non supporté : {system} {machine}")
        return

    archive = f"opencode-{os_name}-{arch}.tar.gz"
    url = f"https://github.com/anomalyco/opencode/releases/latest/download/{archive}"
    print(f"   📦  Téléchargement d'OpenCode...")
    dest = download_with_cache(url, archive, label="OpenCode")

    print(f"   📂  Extraction...")
    try:
        subprocess.run(
            ["tar", "xzf", str(dest), "-C", "/usr/local/bin"],
            check=True, capture_output=True,
        )
        print(f"      ✅  Binaire extrait dans /usr/local/bin/opencode")
    except subprocess.CalledProcessError as e:
        print(f"      ❌  Erreur d'extraction : {e}")
        if mode != "ASK":
            sys.exit(1)


def _pip_break() -> typing.List[str]:
    """Retourne le flag --break-system-packages si nécessaire."""
    try:
        r = subprocess.run(["pip3", "install", "--help"], capture_output=True, text=True)
        if "--break-system-packages" in r.stdout:
            return ["--break-system-packages"]
    except Exception:
        pass
    return []


def _pip_install_cmd(pkg: str) -> typing.List[str]:
    """Retourne la commande pip selon le gestionnaire sélectionné."""
    mgr = "pip"
    if PKG_MGR_FILE.exists():
        mgr = PKG_MGR_FILE.read_text().strip()
    # Fallback sur pip pour les vieux Python (uv build bug avec py3.8)
    if mgr == "uv" and sys.version_info < (3, 9):
        mgr = "pip"
    break_flag = _pip_break()
    base = ["pip3", "install", pkg] + break_flag
    if mgr == "uv":
        base = ["uv", "pip", "install", "--system", pkg] + break_flag
    elif mgr == "rye":
        base = ["rye", "install", pkg]
    return base


def _pip_installed(pkg: str) -> bool:
    """Vérifie si un package pip est installé (ignore les extras)."""
    base = pkg.split("[")[0] if "[" in pkg else pkg
    try:
        r = subprocess.run(["pip3", "list", "--format=columns"], capture_output=True, text=True)
        return base.lower() in r.stdout.lower()
    except Exception:
        return False


def _install_python_module(comp: dict, mode: str) -> None:
    name = comp["name"]
    pkg = comp.get("package")
    print(f"   → Installation du module Python {name}...")

    if not pkg:
        print(f"      ⏭️  Pas de package PyPI défini dans manifest.json")
        print(f"      ℹ️  Installation spécifique prévue dans une version ultérieure")
        return

    # Ajuster la version selon le Python dispo
    old_python = sys.version_info < (3, 9)
    if old_python:
        print(f"      ℹ️  Python < 3.9 — mise à jour de pip")
        subprocess.run(
            ["pip3", "install", "--upgrade", "pip"],
            capture_output=True, text=True,
        )

    base_pkg = pkg.split("[")[0] if "[" in pkg else pkg
    base_pkg = base_pkg.split("<")[0] if "<" in base_pkg else base_pkg

    # Vérifier si déjà installé (les extras ne sont pas vérifiables proprement)
    if "[" not in pkg and _pip_installed(pkg):
        print(f"      ✅  {name} déjà installé")
        return

    cmd = _pip_install_cmd(pkg)
    print(f"      📦  Installation avec {cmd[0]}...")

    env = None
    if cmd[0] == "uv":
        uv_cache = CACHE_DIR / "uv"
        uv_cache.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["UV_CACHE_DIR"] = str(uv_cache)

    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        stderr = r.stderr.strip()
        # Retry sans pinning de version si échec
        if "<" in pkg and ("not found" in stderr or "No matching" in stderr
                           or "Invalid requirement" in stderr):
            base = pkg.split("<")[0].strip()
            print(f"      ⏭️  {pkg} : pinning ignoré, tentative avec {base}")
            cmd2 = _pip_install_cmd(base)
            r = subprocess.run(cmd2, capture_output=True, text=True, env=env)
            if r.returncode == 0:
                print(f"      ✅  {name} installé (fallback version)")
                return
            stderr = r.stderr.strip()
        if "no matching distribution" in stderr.lower():
            print(f"      ⏭️  {pkg} introuvable sur PyPI (version incompatible)")
            return
        print(f"      ❌  Erreur : {stderr}")
        if mode != "ASK" and not old_python:
            sys.exit(1)
        return
    print(f"      ✅  {name} installé")


def install_component(comp_id: str, comp: dict, mode: str) -> None:
    name = comp["name"]
    ctype = comp.get("type", "")
    print(f"\n📦  {name} ({ctype})")

    if mode == "NO":
        print(f"   ⏭️  Mode check — installation ignorée")
        return
    if mode == "ASK":
        if not is_interactive():
            print("   ⏭️  Non-interactif, passage en automatique")
        else:
            try:
                ans = input(f"   Installer {name} ? (y/n) > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = "n"
            if ans not in ("y", "yes"):
                print("   ⏭️  Ignoré")
                return

    if ctype == "binary":
        if name.lower() == "ollama":
            _install_ollama(comp_id, comp, mode)
        elif name.lower() == "opencode":
            _install_opencode(comp_id, comp, mode)
        else:
            print(f"   ⏳  Installation du binaire {name} non encore codée")
    elif ctype == "python-module":
        _install_python_module(comp, mode)
    else:
        print(f"   ⚠️  Type inconnu : {ctype}")


# ─── Nettoyage ─────────────────────────────────────────────────────────

CLEANUP_SAFE_PREFIXES = ("/root/", "/var/cache/", "/var/lib/apt/", "/tmp/")

def _cleanup_path(raw: str) -> typing.Optional[Path]:
    raw = raw.replace("{APP_DIR}", str(APP_DIR))
    p = Path(raw)
    try:
        can_access = p.exists()
    except PermissionError:
        print(f"      ⚠️  Nettoyage ignoré : {p} (permission refusée)")
        return None
    if not can_access:
        return None
    # Sécurité : ne pas supprimer des d critiques
    if str(p) in ("/", "/usr", "/etc", "/var", "/root", "/home"):
        print(f"      ⚠️  Nettoyage refusé : {p} (trop sensible)")
        return None
    if str(p).startswith(str(APP_DIR)):
        return p
    if any(str(p).startswith(prefix) for prefix in CLEANUP_SAFE_PREFIXES):
        return p
    print(f"      ⚠️  Nettoyage ignoré : {p} (hors périmètre)")
    return None


def cleanup() -> None:
    if not CLEANUP_MANIFEST.exists():
        print("   ℹ️  Aucun manifeste de nettoyage trouvé.")
        return

    manifest = json.loads(CLEANUP_MANIFEST.read_text())
    entries = manifest.get("entries", [])
    if not entries:
        return

    removed = 0
    skipped = 0
    for entry in entries:
        raw_path = entry.get("path", "")
        reason = entry.get("reason", "")
        p = _cleanup_path(raw_path)
        if p is None:
            skipped += 1
            continue
        try:
            if p.is_file():
                p.unlink()
            elif p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            removed += 1
        except Exception as e:
            skipped += 1
            continue

    if removed:
        print(f"   🧹  Nettoyage : {removed} élément(s) supprimé(s)")


# ─── Configuration API ─────────────────────────────────────────────

ENV_FILE = APP_DIR / ".env"
ENV_TEMPLATE_FILE = APP_DIR / ".env.template"
LITELLM_CONFIG_FILE = APP_DIR / ".modelweaver" / "litellm_config.json"
LITELLM_PROXY_CONFIG_FILE = APP_DIR / ".modelweaver" / "litellm_config.yaml"
OPENCODE_WRAPPER_FILE = APP_DIR / ".modelweaver" / "opencode-wrapper.sh"
ROUTE_TRACE_FILE = APP_DIR / ".modelweaver" / "route_trace.log"
LITELLM_LOG_FILE = APP_DIR / ".modelweaver" / "litellm.log"

# ─── Fallback ──────────────────────────────────────────────────────

FALLBACK_LOG = APP_DIR / ".modelweaver" / "fallback.log"

ROUTER_SETTINGS = {
    "routing_strategy": "simple-shuffle",
    "allowed_fails": 3,
    "num_retries": 2,
    "timeout": 30,
    "cooldown_time": 60,
    "retry_on_status_codes": [429, 408, 500, 502, 503, 504],
}

FALLBACK_GROUPS = [
    ["groq", "openrouter", "ollama"],
    ["openai", "anthropic", "google"],
    ["mistral", "deepseek"],
    ["together", "cohere", "perplexity", "huggingface"],
    ["opencode-zen"],
]

ROUTING_ORDERS = {
    "test": [
        ["groq", "openrouter", "ollama"],
    ],
    "main": [
        ["opencode-zen"],
        ["groq", "openrouter", "ollama"],
        ["openai", "anthropic", "google"],
        ["mistral", "deepseek"],
        ["together", "cohere", "perplexity", "huggingface"],
    ],
}

ROUTING_MODE_FILE = APP_DIR / ".modelweaver" / "routing_mode"


def get_free_providers() -> set:
    return {p["id"] for p in API_PROVIDERS if p.get("free_key")}

API_PROVIDERS = [
    {
        "id": "groq",
        "env_key": "GROQ_API_KEY",
        "label": "Groq",
        "url": "https://api.groq.com/openai/v1",
        "doc_url": "https://console.groq.com/keys",
        "free_key": True,
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
    },
    {
        "id": "openrouter",
        "env_key": "OPENROUTER_API_KEY",
        "label": "OpenRouter",
        "url": "https://openrouter.ai/api/v1",
        "doc_url": "https://openrouter.ai/keys",
        "free_key": True,
        "models": [
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "nvidia/nemotron-3-super-120b-a12b:free",
            "openai/gpt-4o",
            "anthropic/claude-3.5-sonnet",
        ],
    },
    {
        "id": "ollama",
        "env_key": "",
        "label": "Ollama (local)",
        "url": "http://localhost:11434",
        "doc_url": "",
        "free_key": True,
        "models": ["tinyllama"],
    },
    {
        "id": "openai",
        "env_key": "OPENAI_API_KEY",
        "label": "OpenAI",
        "url": "https://api.openai.com/v1",
        "doc_url": "https://platform.openai.com/api-keys",
        "free_key": False,
        "models": ["gpt-4o", "gpt-4", "gpt-3.5-turbo"],
    },
    {
        "id": "anthropic",
        "env_key": "ANTHROPIC_API_KEY",
        "label": "Anthropic",
        "url": "https://api.anthropic.com/v1",
        "doc_url": "https://console.anthropic.com/",
        "free_key": False,
        "models": ["claude-3-opus-20240229", "claude-3-sonnet-20240229"],
    },
    {
        "id": "google",
        "env_key": "GOOGLE_API_KEY",
        "label": "Google Gemini",
        "url": "https://generativelanguage.googleapis.com/v1beta",
        "doc_url": "https://aistudio.google.com/app/apikey",
        "free_key": True,
        "models": ["gemini-1.5-pro", "gemini-1.5-flash"],
    },
    {
        "id": "deepseek",
        "env_key": "DEEPSEEK_API_KEY",
        "label": "DeepSeek",
        "url": "https://api.deepseek.com/v1",
        "doc_url": "https://platform.deepseek.com/api_keys",
        "free_key": False,
        "models": ["deepseek-chat"],
    },
    {
        "id": "mistral",
        "env_key": "MISTRAL_API_KEY",
        "label": "Mistral AI",
        "url": "https://api.mistral.ai/v1",
        "doc_url": "https://console.mistral.ai/api-keys/",
        "free_key": False,
        "models": ["mistral-large-latest", "mistral-medium-latest"],
    },
    {
        "id": "together",
        "env_key": "TOGETHER_API_KEY",
        "label": "Together AI",
        "url": "https://api.together.xyz/v1",
        "doc_url": "https://api.together.ai/settings/api-keys",
        "free_key": False,
        "models": ["mistralai/Mixtral-8x7B-Instruct-v0.1"],
    },
    {
        "id": "cohere",
        "env_key": "COHERE_API_KEY",
        "label": "Cohere",
        "url": "https://api.cohere.ai/v1",
        "doc_url": "https://dashboard.cohere.com/api-keys",
        "free_key": False,
        "models": ["command-r", "command-r-plus"],
    },
    {
        "id": "perplexity",
        "env_key": "PERPLEXITY_API_KEY",
        "label": "Perplexity",
        "url": "https://api.perplexity.ai",
        "doc_url": "https://www.perplexity.ai/settings/api",
        "free_key": False,
        "models": ["sonar-pro", "sonar-small"],
    },
    {
        "id": "huggingface",
        "env_key": "HUGGINGFACE_API_KEY",
        "label": "HuggingFace",
        "url": "https://api-inference.huggingface.co/models",
        "doc_url": "https://huggingface.co/settings/tokens",
        "free_key": True,
        "models": ["HuggingFaceH4/zephyr-7b-beta", "microsoft/Phi-3-mini-4k-instruct"],
    },
    {
        "id": "opencode-zen",
        "env_key": "OPENCODE_ZEN_API_KEY",
        "label": "OpenCode Zen",
        "url": "https://opencode.ai/zen/v1",
        "doc_url": "https://opencode.ai/docs/zen/",
        "free_key": True,
        "models": ["deepseek-v4-flash-free", "gpt-5-nano", "nemotron-3-ultra-free"],
    },
]


def _env_template_content() -> str:
    lines = [
        "# ModelWeaver — Configuration API",
        "# Décommentez les lignes correspondant à vos fournisseurs d'API.",
        "#",
        "# Obtenez vos clés ici :",
    ]
    for p in API_PROVIDERS:
        if p["doc_url"]:
            lines.append(f"#   {p['label']:14s} : {p['doc_url']}")
    lines.append("")
    for p in API_PROVIDERS:
        if p["env_key"]:
            lines.append(f"#{p['env_key']}=")
    lines.append("")
    return "\n".join(lines)


def _load_env_file() -> None:
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and val:
            os.environ.setdefault(key, val)


def _build_fallback_chains(configured_ids: list, routing_mode: str = "test") -> list:
    order = ROUTING_ORDERS.get(routing_mode, ROUTING_ORDERS["test"])
    chains = []
    for group in order:
        present = [pid for pid in group if pid in configured_ids]
        for i, pid in enumerate(present):
            if i + 1 < len(present):
                chains.append({pid: present[i + 1]})
    return chains


def log_route_event(event: str, detail: str = "", trace_path: typing.Optional[Path] = None) -> None:
    trace_path = Path(trace_path) if trace_path else ROUTE_TRACE_FILE
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with trace_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{ts}] {event} | {detail}\n")


def build_route_plan(providers: list, trace_path: typing.Optional[Path] = None, routing_mode: str = "test") -> list:
    plan = []
    configured_ids = [p["id"] for p in providers]
    order = ROUTING_ORDERS.get(routing_mode, ROUTING_ORDERS["test"])
    for group in order:
        present = [pid for pid in group if pid in configured_ids]
        if len(present) < 2:
            continue
        for idx, pid in enumerate(present[:-1]):
            fallback = present[idx + 1]
            entry = {"provider": pid, "fallback": fallback, "models": []}
            for provider in providers:
                if provider["id"] == pid:
                    entry["models"] = provider.get("models", [])
                    break
            plan.append(entry)

    if trace_path is not None:
        log_route_event("ROUTE_PLAN", f"routing={routing_mode} providers={','.join(configured_ids)} steps={len(plan)}", trace_path=trace_path)
    return plan


def select_default_model(configured: list, routing_mode: str = "test") -> str:
    order = ROUTING_ORDERS.get(routing_mode, ROUTING_ORDERS["test"])
    for group in order:
        for pid in group:
            for provider in configured:
                if provider.get("id") == pid and provider.get("models"):
                    return provider["models"][0]
    for provider in configured:
        if provider.get("models"):
            return provider["models"][0]
    return "tinyllama"


def build_model_candidates(initial_model: str, configured: list, routing_mode: str = "test") -> list:
    candidates = []
    seen = set()
    if initial_model and initial_model not in seen:
        seen.add(initial_model)
        candidates.append(initial_model)
    order = ROUTING_ORDERS.get(routing_mode, ROUTING_ORDERS["test"])
    for group in order:
        for pid in group:
            for provider in configured:
                if provider.get("id") == pid:
                    for model in provider.get("models", []):
                        if model not in seen:
                            seen.add(model)
                            candidates.append(model)
    for provider in configured:
        for model in provider.get("models", []):
            if model not in seen:
                seen.add(model)
                candidates.append(model)
    return candidates


def write_litellm_proxy_config(config: dict, output_path: typing.Optional[Path] = None) -> Path:
    output_path = Path(output_path) if output_path else LITELLM_PROXY_CONFIG_FILE
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    model_list = config.get("model_list", [])
    if model_list:
        lines.append("model_list:")
        for entry in model_list:
            model_name = entry.get("model_name", "")
            lines.append(f"  - model_name: {model_name}")
            lines.append("    litellm_params:")
            for key, value in entry.get("litellm_params", {}).items():
                rendered = value
                if isinstance(value, str):
                    rendered = value
                else:
                    rendered = json.dumps(value)
                lines.append(f"      {key}: {rendered}")

    router_settings = config.get("router_settings")
    if router_settings:
        lines.append("router_settings:")
        for key, value in router_settings.items():
            if isinstance(value, list):
                lines.append(f"  {key}:")
                for item in value:
                    if isinstance(item, dict):
                        for sub_key, sub_value in item.items():
                            lines.append(f"    - {sub_key}: {sub_value}")
                    else:
                        lines.append(f"    - {item}")
            else:
                lines.append(f"  {key}: {value}")

    lines.append("litellm_settings:")
    lines.append("  set_verbose: true")
    lines.append("  drop_params: false")

    output_path.write_text("\n".join(lines) + "\n")
    return output_path


OPENCODE_CONFIG_TPL = {
    "$schema": "https://opencode.ai/config.json",
    "model": "litellm/opencode-engine",
    "instructions": [
        ".opencode/instructions.md",
        "VERSIONS.md",
        "modelweaver.md",
        ".opencode/last_session.md",
    ],
    "plugin": [],
    "provider": {
        "litellm": {
            "npm": "@ai-sdk/openai-compatible",
            "name": "LiteLLM Router",
            "options": {
                "baseURL": "http://127.0.0.1:8000/v1",
                "apiKey": "sk-litellm-master",
            },
            "models": {
                "opencode-engine": {
                    "name": "🧠 Multi-Provider (contexte projet + fallback)",
                    "output": "text",
                },
            },
        },
    },
}


def write_opencode_config(path: Path, api_base: str = "http://127.0.0.1:8000/v1",
                          description: str = "🧠 Multi-Provider (contexte projet + fallback)") -> Path:
    """Génère un fichier opencode.json pour le provider LiteLLM."""
    config = json.loads(json.dumps(OPENCODE_CONFIG_TPL))
    config["provider"]["litellm"]["options"]["baseURL"] = api_base
    config["provider"]["litellm"]["models"]["opencode-engine"]["name"] = description
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False))
    print(f"      ✅  {path}")
    return path


def write_opencode_wrapper(
    output_path: typing.Optional[Path] = None,
    api_base: str = "http://127.0.0.1:8000/v1",
    default_model: str = "gpt-4o",
    fallback_models: typing.Optional[list] = None,
    trace_path: typing.Optional[Path] = None,
) -> Path:
    output_path = Path(output_path) if output_path else OPENCODE_WRAPPER_FILE
    output_path.parent.mkdir(parents=True, exist_ok=True)

    backup_path = None
    if output_path.name == "opencode" and output_path.exists() and output_path.is_file():
        backup_path = output_path.with_suffix(".real")
        if not backup_path.exists():
            shutil.copy2(str(output_path), str(backup_path))
            backup_path.chmod(0o755)

    real_bin = str(backup_path) if backup_path and backup_path.exists() else "opencode"
    trace_file = str(Path(trace_path) if trace_path else ROUTE_TRACE_FILE)
    fallback_list = fallback_models or []
    fallback_values = " ".join([f'"{m}"' for m in fallback_list]) or '""'
    content = f"""#!/usr/bin/env bash
set -euo pipefail

export OPENAI_API_KEY="${{OPENAI_API_KEY:-${{OPENROUTER_API_KEY:-${{GROQ_API_KEY:-${{OPENCODE_ZEN_API_KEY:-dummy}}}}}}}}"
export OPENAI_API_BASE="${{OPENAI_API_BASE:-{api_base}}}"
export OPENAI_BASE_URL="${{OPENAI_BASE_URL:-${{OPENAI_API_BASE}}}}"
export OPENAI_MODEL="${{OPENAI_MODEL:-{default_model}}}"

TRACE_FILE="{trace_file}"
FALLBACK_MODELS=({fallback_values})
REAL_BIN="{real_bin}"

log_attempt() {{
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $1" >> "$TRACE_FILE"
}}

run_with_model() {{
  model="$1"
  shift
  log_attempt "opencode attempt model=$model"
  echo "▶️  Tentative avec le modèle $model" >&2
  export OPENAI_MODEL="$model"
  output_file=$(mktemp)
  "$REAL_BIN" "$@" >"$output_file" 2>&1
  status=$?
  cat "$output_file"
  if [ $status -eq 0 ]; then
    rm -f "$output_file"
    return 0
  fi
  if grep -Eqi 'request too large|tpm|rate limit|429|too many requests|timeout|overloaded|503|502|504|connection|unexpected server error|incorrect api key|invalid api key|401|403' "$output_file"; then
    log_attempt "fallback-triggered model=$model status=$status"
    echo "⚠️  Erreur détectée, passage au modèle de secours pour $model" >&2
    rm -f "$output_file"
    return 99
  fi
  log_attempt "error-no-fallback model=$model status=$status"
  rm -f "$output_file"
  return 99
}}

attempts=()
if [ -n "${{OPENAI_MODEL:-}}" ]; then
  attempts+=("${{OPENAI_MODEL}}")
fi
for candidate in "${{FALLBACK_MODELS[@]}}"; do
  if [ -n "$candidate" ]; then
    attempts+=("$candidate")
  fi
done

seen=()
for model in "${{attempts[@]}}"; do
  case " ${{seen[*]}} " in
    *" $model "*) continue ;;
  esac
  seen+=("$model")
  set +e
  run_with_model "$model" "$@"
  status=$?
  set -e
  if [ "$status" -eq 0 ]; then
    exit 0
  fi
  if [ "$status" -ne 99 ]; then
    exit "$status"
  fi
done

exit 1
"""
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp_path.write_text(content)
    tmp_path.chmod(0o755)
    os.replace(tmp_path, output_path)
    return output_path


def _resolve_api_key(env_key: str) -> str:
    """Résout une clé API depuis l'environnement."""
    val = os.environ.get(env_key, "")
    if val:
        return val
    # Fallback : lire depuis .env
    env_file = APP_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == env_key:
                return v.strip().strip('"').strip("'")
    return ""


PROVIDER_PREFIX_MAP = {
    "opencode": "opencode-zen",
    "groq": "groq",
    "openrouter": "openrouter",
    "ollama": "ollama",
}


def _discover_models() -> dict:
    """Runs `opencode models` to discover available models dynamically.
    Returns dict mapping provider ID -> list of model names (without prefix)."""
    if not shutil.which("opencode"):
        return {}
    try:
        result = subprocess.run(
            ["opencode", "models"],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return {}
    if result.returncode != 0:
        return {}

    models_by_provider: dict = {}
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if "/" not in line:
            continue
        prefix, _, name = line.partition("/")
        pid = PROVIDER_PREFIX_MAP.get(prefix)
        if pid is None:
            continue
        if pid not in models_by_provider:
            models_by_provider[pid] = []
        models_by_provider[pid].append(name)
    return models_by_provider


def _generate_litellm_config(providers: list, routing_mode: str = "test") -> None:
    if not providers:
        return

    discovered = _discover_models()

    model_list = []
    discovered_total = 0
    for p in providers:
        is_openai_compat = p["id"] in ("opencode-zen",)
        is_ollama = p["id"] == "ollama"

        # Use discovered models if available, else fallback to hardcoded
        p_models = discovered.get(p["id"], p["models"])
        if p["id"] in discovered:
            discovered_total += len(p_models)

        for model in p_models:
            if is_ollama:
                entry = {
                    "model_name": model,
                    "litellm_params": {
                        "model": f"ollama/{model}",
                        "api_base": p["url"],
                    },
                }
            else:
                api_key = _resolve_api_key(p["env_key"])
                entry = {
                    "model_name": model,
                    "litellm_params": {
                        "model": f"openai/{model}" if is_openai_compat else f"{p['id']}/{model}",
                        "api_key": api_key,
                    },
                }
                if is_openai_compat:
                    entry["litellm_params"]["api_base"] = p["url"]
            model_list.append(entry)

    configured_ids = [p["id"] for p in providers]
    fallback_chains = _build_fallback_chains(configured_ids, routing_mode)
    config = {"model_list": model_list}
    if fallback_chains:
        config["router_settings"] = {**ROUTER_SETTINGS, "fallbacks": fallback_chains}

    LITELLM_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LITELLM_CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")
    model_count = len(model_list)
    discovered_label = f" (+{discovered_total} découverts)" if discovered_total else ""
    print(f"      📄  Config LiteLLM : {LITELLM_CONFIG_FILE}")
    print(f"      Providers : {', '.join(p['label'] for p in providers)}")
    print(f"      Modèles   : {model_count}{discovered_label}")
    if fallback_chains:
        routes = sum(len(v) for chain in fallback_chains for v in chain.values())
        print(f"      Fallback   : activé ({routes} route(s) de repli)")

    # Generate fallback.json for the proxy
    _generate_fallback_json(discovered, routing_mode)


FALLBACK_FILE = APP_DIR / ".modelweaver" / "fallback.json"
FALLBACK_BACKOFF_BASE = 300
FALLBACK_BACKOFF_MULT = 1.5
FALLBACK_BACKOFF_MAX = 86400


def _generate_fallback_json(discovered: dict, routing_mode: str = "test") -> None:
    """Generate fallback.json with ordered model list and backoff state.

    Merges discovered models with existing fallback.json to preserve
    backoff state across runs.
    """
    existing = {}
    if FALLBACK_FILE.exists():
        try:
            existing = json.loads(FALLBACK_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    mw_groups = ROUTING_ORDERS.get(routing_mode, ROUTING_ORDERS["test"])
    flat_order = [pid for group in mw_groups for pid in group]

    order = []
    models_out = {}
    seen = set()
    for pid in flat_order:
        p_models = discovered.get(pid, [])
        if not p_models:
            continue
        for model in p_models:
            key = f"{pid}/{model}"
            if key in seen:
                continue
            seen.add(key)
            order.append(key)
            # Preserve existing state or create fresh
            old = existing.get("models", {}).get(key, {})
            models_out[key] = {
                "provider": pid,
                "enabled": old.get("enabled", True),
                "consecutive_failures": old.get("consecutive_failures", 0),
                "dont_try_until": old.get("dont_try_until", None),
                "avg_response_ms": old.get("avg_response_ms", None),
                "total_tests": old.get("total_tests", 0),
                "last_ok": old.get("last_ok", None),
                "last_error": old.get("last_error", None),
            }

    fb = {"order": order, "models": models_out,
          "backoff": {"base": FALLBACK_BACKOFF_BASE,
                      "multiplier": FALLBACK_BACKOFF_MULT,
                      "max": FALLBACK_BACKOFF_MAX}}
    FALLBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    FALLBACK_FILE.write_text(json.dumps(fb, indent=2, ensure_ascii=False) + "\n")
    print(f"      📄  Fallback     : {FALLBACK_FILE} ({len(order)} modèles)")


def start_litellm_proxy(config_path: typing.Optional[Path] = None) -> bool:
    if shutil.which("litellm") is None:
        print("   ⚠️  LiteLLM CLI introuvable")
        return False

    config_path = Path(config_path) if config_path else LITELLM_PROXY_CONFIG_FILE
    if not config_path.exists():
        print("   ⚠️  Config LiteLLM introuvable, bridge non démarré")
        return False

    if _probe("http://127.0.0.1:8000", "/health/readiness", timeout=2):
        print("   ✅  LiteLLM déjà démarré")
        return True

    # Tue un éventuel processus LiteLLM zombie
    for sig in ("TERM", "KILL"):
        try:
            subprocess.run(
                ["pkill", "-sig", sig, "-f", "litellm.*--port 8000"],
                capture_output=True, timeout=5,
            )
            if sig == "TERM":
                time.sleep(2)
        except Exception:
            pass

    print("   🚀  Démarrage du proxy LiteLLM...")
    LITELLM_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with LITELLM_LOG_FILE.open("a", encoding="utf-8") as log_handle:
            subprocess.Popen(
                ["litellm", "--host", "127.0.0.1", "--port", "8000", "--config", str(config_path), "--detailed_debug"],
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except Exception as e:
        print(f"      ⚠️  Impossible de démarrer LiteLLM : {e}")
        return False

    for _ in range(30):
        time.sleep(1)
        if _probe("http://127.0.0.1:8000", "/health/readiness", timeout=2):
            print("      ✅  Proxy LiteLLM prêt")
            return True

    print("      ⚠️  LiteLLM pas encore prêt (timeout)")
    return False


def configure_api() -> list:
    print("\n🔑  Configuration API")

    if not ENV_TEMPLATE_FILE.exists():
        ENV_TEMPLATE_FILE.write_text(_env_template_content())
        print(f"   📄  Template créé : {ENV_TEMPLATE_FILE}")

    if ENV_FILE.exists():
        print(f"   📄  {ENV_FILE} trouvé, chargement...")
        _load_env_file()
    else:
        print(f"   ⚠️  Aucun fichier .env trouvé.")
        shutil.copy(str(ENV_TEMPLATE_FILE), str(ENV_FILE))
        if ENV_FILE.exists():
            print(f"   📄  {ENV_FILE} créé à partir du template.")
            print(f"   ✏️  Éditez-le et ajoutez vos clés API, puis relancez.")
        return []

    configured = []
    for p in API_PROVIDERS:
        if not p["env_key"]:
            configured.append(p)
            print(f"   ✅  {p['label']:14s} → toujours disponible")
            continue
        val = os.environ.get(p["env_key"])
        if val:
            configured.append(p)
            masked = val[:12] + "..." if len(val) > 12 else "***"
            print(f"   ✅  {p['label']:14s} → {masked}")
        else:
            print(f"   ⬜  {p['label']:14s} → non configuré")

    routing_mode = "test"
    if ROUTING_MODE_FILE.exists():
        routing_mode = ROUTING_MODE_FILE.read_text().strip()

    if configured:
        _generate_litellm_config(configured, routing_mode)
        route_plan = build_route_plan(configured, trace_path=ROUTE_TRACE_FILE, routing_mode=routing_mode)
        if route_plan:
            print(f"   🧭  Plan de routage ({routing_mode}) : {len(route_plan)} rebond(s) configuré(s)")
            for entry in route_plan:
                print(f"      ↳ {entry['provider']} → {entry['fallback']}")
        proxy_config = write_litellm_proxy_config(
            json.loads(LITELLM_CONFIG_FILE.read_text()),
            LITELLM_PROXY_CONFIG_FILE,
        )
        default_model = select_default_model(configured, routing_mode)
        fallback_models = build_model_candidates(default_model, configured, routing_mode)
        wrapper_path = write_opencode_wrapper(
            OPENCODE_WRAPPER_FILE,
            "http://127.0.0.1:8000/v1",
            default_model,
            fallback_models,
            ROUTE_TRACE_FILE,
        )
        bridge_wrapper = Path.home() / ".local" / "bin" / "opencode-modelweaver"
        write_opencode_wrapper(
            bridge_wrapper,
            "http://127.0.0.1:8000/v1",
            default_model,
            fallback_models,
            ROUTE_TRACE_FILE,
        )
        print(f"   🧩  Wrapper OpenCode installé : {bridge_wrapper}")
        print(f"   🧩  Wrapper OpenCode : {wrapper_path}")
        print(f"   ▶️  Usage : {bridge_wrapper} [arguments]")
        print("   ℹ️  La commande opencode reste directe et n'est plus remplacée par le bridge.")

        # Génération des fichiers opencode.json (global + projet)
        global_config = Path.home() / ".config" / "opencode" / "opencode.json"
        project_config = APP_DIR / "opencode.json"
        print("   📄  Génération opencode.json...")
        write_opencode_config(global_config)
        write_opencode_config(project_config)

        start_litellm_proxy(proxy_config)
    else:
        print(f"   ⚠️  Aucune clé API détectée dans {ENV_FILE}.")
        print(f"   ℹ️  Éditez le fichier et décommentez vos fournisseurs.")

    return configured


# ─── Fallback ──────────────────────────────────────────────────────────

def _provider_label(pid: str) -> str:
    for p in API_PROVIDERS:
        if p["id"] == pid:
            return p["label"]
    return pid


def log_fallback_event(event: str, provider: str, detail: str = "") -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    entry = f"[{ts}] {event} | provider={provider} | {detail}\n"
    FALLBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(str(FALLBACK_LOG), "a") as f:
        f.write(entry)


def detect_overload(response_code: int, headers: dict = None) -> bool:
    if response_code == 429:
        return True
    if response_code in (502, 503):
        return True
    if headers:
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        if retry_after:
            return True
    return False


def validate_fallback(configured: list, routing_mode: str = "test") -> dict:
    print("\n🔄  Validation du Fallback")
    if not configured:
        print("   ⏭️  Aucun fournisseur configuré, fallback ignoré")
        return {"status": "skipped", "providers": 0}

    # Construire les chaînes de fallback
    configured_ids = [p["id"] for p in configured]
    if ROUTING_MODE_FILE.exists():
        routing_mode = ROUTING_MODE_FILE.read_text().strip()
    chains = _build_fallback_chains(configured_ids, routing_mode)
    total_routes = sum(len(v) for chain in chains for v in chain.values())

    if not chains:
        print("   ⚠️  Fallback non configurable — moins de 2 fournisseurs par groupe")
        return {"status": "no_fallback", "providers": len(configured)}

    print(f"   Providers  : {len(configured)}")
    print(f"   Routes     : {total_routes}")
    for chain in chains:
        for primary, backup in chain.items():
            print(f"      {_provider_label(primary):14s} → {_provider_label(backup)}")
    print(f"   Statut     : ✅ Config générée dans LiteLLM")

    # Test probes (light) — vérifier que curl peut atteindre chaque base URL
    unreachable = []
    for p in configured:
        url = p["url"].rstrip("/")
        if not _probe(url, "/", timeout=5):
            unreachable.append(p["id"])
            log_fallback_event("PROBE_FAIL", p["id"], f"hôte {url} injoignable")

    if unreachable:
        print(f"   ⚠️  {len(unreachable)} fournisseur(s) injoignable(s) : {', '.join(unreachable)}")
        print(f"   ℹ️  Le fallback contournera automatiquement les providers morts")
        return {"status": "degraded", "providers": len(configured), "unreachable": unreachable}

    print(f"   ✅  Tous les fournisseurs répondent")
    return {"status": "ok", "providers": len(configured)}


# ─── TinyLlama ─────────────────────────────────────────────────────────

TINYLLAMA_LOG = APP_DIR / ".modelweaver" / "tinyllama.log"
TINYLLAMA_NAME = "tinyllama"
TINYLLAMA_SIZE_MB = 637


def _ollama_running() -> bool:
    try:
        r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _start_ollama() -> bool:
    if _ollama_running():
        return True
    print("   🚀  Démarrage du serveur Ollama...")
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(15):
            time.sleep(2)
            if _ollama_running():
                print("      ✅  Serveur prêt")
                return True
        print("      ⚠️  Serveur pas encore prêt (timeout)")
        return False
    except Exception as e:
        print(f"      ⚠️  Erreur : {e}")
        return False


def download_tinyllama(mode: str) -> bool:
    print(f"\n🤏  {TINYLLAMA_NAME} — modèle de test ({TINYLLAMA_SIZE_MB} Mo)")

    if mode == "NO":
        print("   ⏭️  Mode check — ignoré")
        return False

    if not shutil.which("ollama"):
        print("   ⏭️  Ollama non installé")
        return False

    if not _start_ollama():
        print("   ❌  Serveur Ollama indisponible")
        return False

    # Vérifier si déjà présent
    try:
        r = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=10
        )
        if TINYLLAMA_NAME in r.stdout.lower():
            print(f"   ♻️  {TINYLLAMA_NAME} déjà présent")
            return True
    except Exception:
        pass

    # Pull du modèle
    print(f"   ⬇️  Téléchargement de {TINYLLAMA_NAME}...")
    try:
        r = subprocess.run(
            ["ollama", "pull", TINYLLAMA_NAME],
            capture_output=True, text=True, timeout=600,
        )
        if r.returncode != 0:
            print(f"   ❌  Échec : {r.stderr.strip()}")
            return False
        print(f"   ✅  {TINYLLAMA_NAME} téléchargé")
    except subprocess.TimeoutExpired:
        print("   ⚠️  Téléchargement interrompu (timeout 600s)")
        return False

    # Test du modèle
    print("   🧪  Test du modèle...")
    try:
        r = subprocess.run(
            ["ollama", "run", TINYLLAMA_NAME, "Réponds en un mot : 2+2 ="],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode == 0 and r.stdout.strip():
            answer = r.stdout.strip().replace("\n", " ")[:120]
            print(f"      Réponse : {answer}")
            print(f"   ✅  {TINYLLAMA_NAME} fonctionne")
            # Log
            ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            TINYLLAMA_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(str(TINYLLAMA_LOG), "a") as f:
                f.write(f"[{ts}] tinyllama OK | réponse: {answer}\n")
            return True
        else:
            print(f"   ⚠️  Réponse vide ou erreur")
            return False
    except subprocess.TimeoutExpired:
        print("   ⚠️  Test interrompu (timeout 60s)")
        return False
    except Exception as e:
        print(f"   ⚠️  Erreur : {e}")
        return False


# ─── Linkage ───────────────────────────────────────────────────────────

ENDPOINTS_FILE = APP_DIR / ".modelweaver" / "endpoints.json"

SERVICES = {
    "ollama": {"port": 11434, "health": "/api/tags", "label": "Ollama"},
    "litellm": {"port": 8000, "health": "/health/readiness", "label": "LiteLLM"},
    "open-webui": {"port": 8080, "health": "/health", "label": "Open WebUI"},
}

LINKAGE_CONFIGS = {
    "ollama": {
        "env_file": "OLLAMA_BASE_URL",
    },
    "open-webui": {
        "env_file": "OLLAMA_BASE_URL",
        "env_value": lambda ep: ep.get("ollama", "http://localhost:11434"),
    },
}


def _probe(base: str, path: str = "/", timeout: int = 3) -> bool:
    url = f"{base.rstrip('/')}{path}"
    try:
        r = subprocess.run(
            ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
             "--connect-timeout", str(timeout), "-m", str(timeout + 2), url],
            capture_output=True, timeout=timeout + 5,
        )
        if r.returncode != 0:
            return False
        code_str = r.stdout.strip()
        if not code_str:
            return False
        code = int(code_str)
        return code > 0
    except Exception:
        return False


def linkage() -> None:
    endpoints = {}
    print("   🔗  Vérification des connexions...")

    if LITELLM_PROXY_CONFIG_FILE.exists():
        start_litellm_proxy(LITELLM_PROXY_CONFIG_FILE)

    for sid, info in SERVICES.items():
        base = f"http://localhost:{info['port']}"
        ok = _probe(base, info["health"])
        endpoints[sid] = base if ok else None
        status = "✅" if ok else "⬜"
        print(f"      {status}  {info['label']} → {base}/")

    # Créer / mettre à jour le fichier endpoints
    ENDPOINTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENDPOINTS_FILE.write_text(json.dumps(endpoints, indent=2) + "\n")
    print(f"      📄  Fichier endpoints : {ENDPOINTS_FILE}")

    # Tests inter-composants
    print("      🔄  Tests de communication...")
    ollama = endpoints.get("ollama")
    litellm = endpoints.get("litellm")

    if ollama:
        if _probe(ollama, "/api/tags"):
            print(f"      ✅  Ollama répond")
        else:
            print(f"      ⚠️  Ollama injoignable")

    if litellm:
        if _probe(litellm, "/health/readiness"):
            print(f"      ✅  LiteLLM répond")
            # Test proxy Ollama via LiteLLM
            if ollama and _probe(litellm, "/ollama/api/tags"):
                print(f"      ✅  LiteLLM → Ollama (proxy OK)")
            elif ollama:
                print(f"      ⚠️  LiteLLM ne voit pas Ollama")
        else:
            print(f"      ⚠️  LiteLLM injoignable")

    # Modèle recommandé
    print("      📋  Résumé :")
    print(f"         Ollama :  {ollama or 'non détecté'}")
    print(f"         LiteLLM : {litellm or 'non détecté'}")
    model = "Aucun"
    if ollama:
        model = "Ollama (local)"
    print(f"         Modèle :  {model}")


# ─── Interface CLI ───────────────────────────────────────────────────

USAGE = """ModelWeaver — Orchestrateur IA cross-platform

Usage:
  python3 modelweaver.py [command] [options]

Commands:
  install           Installation complète (défaut)
  check             Audit système uniquement (mode NO)
  config            Configuration des clés API (.env)
  status            État actuel des composants
  menu              Menu interactif (GUI légère)
  help              Affiche cette aide

Options:
  --mode YES|NO|ASK  Forcer le mode d'installation
  --only COMPONENT   Installer uniquement ce composant (engine, bridge, agent, interface)
  --skip-audit       Ignorer l'audit système
  --skip-tinyllama   Ignorer le téléchargement de tinyllama
"""


def cmd_help() -> None:
    print(USAGE.strip())


def cmd_check() -> None:
    print(f"🚀  ModelWeaver — v{REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}")
    init_cache()
    mode = "NO"
    write_mode(mode)
    audit(mode)


def cmd_config() -> None:
    print(f"🚀  ModelWeaver — v{REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}")
    init_cache()
    configured = configure_api()
    if configured:
        print(f"\n🔑  {len(configured)} fournisseur(s) configuré(s)")
    else:
        print(f"\n🔑  Aucune clé API détectée. Éditez {ENV_FILE} et relancez.")


def cmd_status() -> None:
    print(f"🚀  ModelWeaver — v{REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}")
    init_cache()

    # Version
    print(f"\n📦  Version cible : {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}")

    # Mode
    mode = read_mode()
    print(f"⚙️  Mode : {mode or 'non configuré'}")

    # OS
    os_info = check_os()
    ram_mb, ram_msg = check_ram()
    print(f"💻  {os_info['system']} {os_info['release']} ({os_info['machine']})")
    print(f"   {ram_msg}")

    # Composants installés
    print(f"\n📋  Composants :")
    for comp_id, comp in load_manifest().get("components", {}).items():
        ctype = comp.get("type", "")
        name = comp["name"]
        if ctype == "binary" and shutil.which(name.lower()):
            print(f"   ✅  {name:12s} (binary) — installé")
        elif ctype == "python-module":
            pkg = comp.get("package", "")
            base_pkg = pkg.split("[")[0] if "[" in pkg else pkg
            if base_pkg and _pip_installed(base_pkg):
                print(f"   ✅  {name:12s} (python) — installé")
            else:
                print(f"   ⬜  {name:12s} (python) — manquant")
        else:
            print(f"   ⬜  {name:12s} ({ctype}) — inconnu")

    # Configuration API
    print(f"\n🔑  API :")
    if ENV_FILE.exists():
        _load_env_file()
        for p in API_PROVIDERS:
            val = os.environ.get(p["env_key"])
            if val:
                masked = val[:12] + "..." if len(val) > 12 else "***"
                print(f"   ✅  {p['label']:14s} → {masked}")
            else:
                print(f"   ⬜  {p['label']:14s} → non configuré")
    else:
        print(f"   ⬜  Fichier {ENV_FILE} introuvable")

    # Fallback
    fb_log = FALLBACK_LOG
    if fb_log.exists():
        entries = len(fb_log.read_text().splitlines())
        print(f"   📋  Fallback : {entries} événement(s) enregistré(s)")
    else:
        print(f"   📋  Fallback : aucun événement")

    route_trace = ROUTE_TRACE_FILE
    if route_trace.exists():
        lines = route_trace.read_text().splitlines()
        recent = lines[-5:] if len(lines) >= 5 else lines
        print(f"   🧭  Route trace : {len(lines)} entrée(s), dernière(s) :")
        for line in recent:
            print(f"      {line}")
    else:
        print(f"   🧭  Route trace : aucune entrée")

    # Tinyllama
    print(f"\n🤏  TinyLlama :")
    tl_log = TINYLLAMA_LOG
    if tl_log.exists():
        last = tl_log.read_text().splitlines()[-1] if tl_log.read_text() else "?"
        print(f"   ✅  Testé — dernier résultat : {last[:80]}")
    else:
        print(f"   ⬜  Non testé")


def gui_menu() -> None:
    """Menu interactif léger."""
    print(f"\n{'='*50}")
    print(f"  🛠  ModelWeaver — Menu interactif")
    print(f"{'='*50}")

    while True:
        mode = read_mode()
        print(f"\n  Mode actuel : {mode or 'non défini'}")
        print(f"  ─────────────────────────────")
        print(f"  [1] Installation complète")
        print(f"  [2] Check-up (audit seulement)")
        print(f"  [3] Configuration API (.env)")
        print(f"  [4] Status des composants")
        print(f"  [5] Mode : {mode or 'défaut'} → Changer")
        print(f"  [6] Télécharger tinyllama")
        print(f"  [Q] Quitter")
        print(f"  ─────────────────────────────")

        try:
            choice = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  👋  Au revoir.")
            break

        if choice in ("q",):
            print("  👋  Au revoir.")
            break
        elif choice in ("1",):
            write_mode("YES")
            return  # Laisse main() prendre le relais
        elif choice in ("2",):
            cmd_check()
        elif choice in ("3",):
            cmd_config()
        elif choice in ("4",):
            cmd_status()
        elif choice in ("5",):
            print("\n  Modes disponibles :")
            print("    [1] YES — Automatique")
            print("    [2] NO  — Check uniquement")
            print("    [3] ASK — Demander à chaque étape")
            sub = input("  > ").strip()
            mapping = {"1": "YES", "2": "NO", "3": "ASK"}
            if sub in mapping:
                write_mode(mapping[sub])
                print(f"  ✅  Mode changé pour {mapping[sub]}")
        elif choice in ("6",):
            mode = read_mode() or "YES"
            write_mode("YES")
            download_tinyllama("YES")
            write_mode(mode)
        else:
            print("  ❌  Choix invalide.")


def parse_args() -> dict:
    import argparse
    parser = argparse.ArgumentParser(
        prog="modelweaver",
        description="Orchestrateur IA cross-platform",
        add_help=False,
    )
    parser.add_argument("command", nargs="?", default="install",
                        choices=["install", "check", "config", "status", "menu", "help"])
    parser.add_argument("--mode", choices=["YES", "NO", "ASK"], help="Forcer le mode d'installation")
    parser.add_argument("--only", help="Composant à installer uniquement (engine, bridge, agent, interface)")
    parser.add_argument("--skip-audit", action="store_true", help="Ignorer l'audit système")
    parser.add_argument("--skip-tinyllama", action="store_true", help="Ignorer tinyllama")
    parser.add_argument("--routing", choices=["test", "main"], default=None, help="Ordre de routage (test: groq→openrouter→ollama, main: opencode-zen→...)")
    parser.add_argument("--help", action="store_true", help="Afficher l'aide")
    args = parser.parse_args()
    return vars(args)


def main() -> None:
    args = parse_args()

    cmd = args["command"]
    force_mode = args["mode"]
    only_comp = args["only"]
    skip_audit = args["skip_audit"]
    skip_tinyllama = args["skip_tinyllama"]
    routing_mode = args.get("routing", "")
    show_help = args["help"]

    if routing_mode:
        ROUTING_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ROUTING_MODE_FILE.write_text(routing_mode + "\n")

    if show_help or cmd == "help":
        cmd_help()
        return

    if cmd == "check":
        cmd_check()
        return

    if cmd == "config":
        cmd_config()
        return

    if cmd == "status":
        cmd_status()
        return

    if cmd == "menu":
        # gui_menu() retourne False si l'utilisateur a choisi "install" (option 1)
        # ou True s'il a quitté autrement
        mode_before = read_mode()
        gui_menu()
        mode_after = read_mode()
        if mode_after == mode_before:
            return  # L'utilisateur a quitté sans choisir "install"

    # ── install (défaut) ──────────────────────────────────────────

    print(f"🚀  ModelWeaver — v{REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}")

    init_cache()

    if force_mode:
        mode = force_mode
        write_mode(mode)
        print(f"⚙️  Mode forcé : {mode}")
    else:
        mode = select_mode()

    if not skip_audit:
        audit(mode)

    if mode == "NO":
        print("✅  Check-up terminé. Aucune installation effectuée.")
        return

    pkg_mgr = select_package_manager(mode)

    manifest = load_manifest()
    components = manifest.get("components", {})
    if not components:
        print("⚠️  Aucun composant défini dans manifest.json.")
        return

    if only_comp:
        valid_ids = {"engine": "ollama", "bridge": "litellm",
                     "agent": "opencode", "interface": "open-webui",
                     "context": "gitingest"}
        comp_id = only_comp.lower().replace("-", "_")
        comp_id_map = {"engine": "engine", "bridge": "bridge",
                       "agent": "agent", "interface": "interface",
                       "context": "context"}
        target = comp_id_map.get(comp_id)
        if not target or target not in components:
            print(f"⚠️  Composant inconnu : {only_comp}")
            print(f"   Valides : engine, bridge, agent, interface, context")
            return
        print(f"\n📋  Installation ciblée : {components[target]['name']}")
        install_component(target, components[target], mode)
    else:
        print(f"\n📋  Composants à installer : {len(components)}")
        for comp_id, comp in components.items():
            install_component(comp_id, comp, mode)

    configured = configure_api()
    cleanup()
    linkage()

    if not skip_tinyllama:
        download_tinyllama(mode)

    if configured:
        print(f"\n🔑  API : {len(configured)} fournisseur(s) configuré(s)")
        fb_result = validate_fallback(configured, routing_mode)
        if fb_result["status"] == "degraded":
            print("   ℹ️  Fallback partiel — certains fournisseurs sont hors ligne")
        log_fallback_event("FALLBACK_CHECK", "all",
                          f"status={fb_result['status']} providers={fb_result['providers']}")
    else:
        print(f"\n🔑  API : non configurée — modifiez {ENV_FILE}")

    print("\n✨  ModelWeaver terminé.")


if __name__ == "__main__":
    main()
