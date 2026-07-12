import subprocess
import os
import shutil


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
