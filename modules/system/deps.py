import subprocess
import os
import shutil
import json
import sys
from pathlib import Path


# Répertoire de ce module (modules/system)
_MODULE_DIR = Path(__file__).resolve().parent
_MANIFEST = _MODULE_DIR / "deps_manifest.json"


def detect_target() -> str:
    """Détecte la cible (ex: 'ubuntu24') depuis /etc/os-release.

    Retourne '' si inconnue.
    """
    os_release = Path("/etc/os-release")
    if not os_release.exists():
        return ""
    data = {}
    for line in os_release.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip().strip('"')
    os_id = data.get("ID", "")
    os_ver = data.get("VERSION_ID", "")
    # ubuntu 24.04 -> ubuntu24
    if os_id == "ubuntu" and os_ver.startswith("24"):
        return "ubuntu24"
    # debian 12 etc. : étendre ici
    return ""


def load_manifest() -> dict:
    if not _MANIFEST.exists():
        raise RuntimeError(f"manifeste absent: {_MANIFEST}")
    return json.loads(_MANIFEST.read_text())


def required_dependencies(target: str) -> list:
    """Deps requises pour une cible = safe + light + non optionnelles."""
    m = load_manifest()
    out = []
    for dep in m.get("dependencies", []):
        if dep.get("optional"):
            continue
        if not dep.get("safe"):
            continue
        if dep.get("weight") != "light":
            continue
        t = dep.get("targets", {}).get(target)
        if t:
            out.append((dep["name"], dep["language"], t))
    return out


def resolve_target_script(target: str) -> Path:
    """Retourne le chemin du script compilé pour la cible.

    Lève FileNotFoundError si le script (artefact compilé) est absent —
    c'est le 'fail à la compilation : fichier *** absent'.
    """
    m = load_manifest()
    tgt = m.get("targets", {}).get(target)
    if not tgt:
        raise FileNotFoundError(f"cible inconnue: {target}")
    script_rel = tgt.get("script")
    if not script_rel:
        raise FileNotFoundError(f"script non défini pour la cible: {target}")
    script_path = _MODULE_DIR / script_rel
    if not script_path.exists():
        # Échec explicite : artefact compilé manquant
        raise FileNotFoundError(str(script_path))
    return script_path


def install_target_dependencies(target: str = "", include_optional: bool = False) -> dict:
    """Installe les dépendances requises de la cible via le script compilé.

    - target vide -> auto-détecté.
    - script absent -> erreur explicite 'file <script> absent'.
    """
    try:
        if not target:
            target = detect_target()
        if not target:
            return {"status": "error",
                    "error": "cible non détectée (os-release non supporté)"}
        script = resolve_target_script(target)  # FileNotFoundError si absent
        cmd = [str(script)]
        if include_optional:
            cmd.append("--include-optional")
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            return {"status": "ok", "target": target,
                    "optional": include_optional,
                    "log": res.stdout.strip()}
        return {"status": "error", "target": target,
                "error": (res.stderr or res.stdout).strip()}
    except FileNotFoundError as e:
        # Fail à la compilation : artefact cible manquant
        return {"status": "error",
                "error": f"fichier {Path(str(e)).name} absent (compiler la cible {target} manquant)"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _escalate() -> list:
    """Retourne le préfixe de commande pour obtenir les droits root si besoin.

    - si déjà root : []
    - sinon : sudo si dispo, pkexec sinon (lève si aucun)
    """
    if os.geteuid() == 0:
        return []
    if shutil.which("sudo"):
        return ["sudo"]
    if shutil.which("pkexec"):
        return ["pkexec"]
    raise RuntimeError("privilege escalation unavailable (need sudo or pkexec)")


def install_system_package(package_name: str) -> dict:
    """Installe un paquet via apt (sans terminal interactif).

    - root          : apt direct
    - non-root      : sudo/pkexec (auto, sans mot de passe si policykit autorise)
    - `apt-get update` n'est lancé QUE si les listes ne connaissent pas le
      paquet (évite un update réseau redondant à chaque installation).
    """
    try:
        pre = _escalate()
        def _install():
            return subprocess.run(pre + ["apt-get", "install", "-y", package_name],
                                  capture_output=True, text=True)
        res = _install()
        # Paquet inconnu → on rafraîchit les listes une fois puis on retry.
        if res.returncode != 0 and "Unable to locate" in (res.stderr or ""):
            subprocess.run(pre + ["apt-get", "update", "-qq"],
                           check=False, capture_output=True)
            res = _install()
        if res.returncode == 0:
            return {"status": "ok", "package": package_name}
        return {"status": "error", "error": res.stderr.strip() or res.stdout.strip()}
    except Exception as e:
        return {"status": "error", "error": str(e)}
