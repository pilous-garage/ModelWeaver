#!/usr/bin/env python3
"""prepare_docker.py — Préparateur de conteneurs Docker pour ModelWeaver.

Objectif : savoir si on crée/relance un conteneur, installer des logiciels ou
ajouter des fichiers dedans, et garder des IMAGES VIERGES de référence par
niveau. Ne pollue JAMAIS une image de référence : on part d'un snapshot vierge
(tag `mw-base:<niveau>`), on fait des modifs dans un conteneur jetable, et on
commit uniquement quand on veut créer un NOUVEAU niveau.

Niveaux d'images vierges :
  - mw-base:ubuntu   ubuntu nu + outils minimaux (curl, git, python3)
  - mw-base:deps     + toutes les dépendances du projet ModelWeaver (base)
  - mw-base:gui      + WebKitGTK + Xvfb + xdotool + wmctrl (pour la GUI)
  - mw-base:test     + outils de test (pytest, etc.)

Usage :
  python3 docker/prepare_docker.py list                  # images + conteneurs
  python3 docker/prepare_docker.py ensure --level gui    # crée le niveau si absent
  python3 docker/prepare_docker.py run --level gui --name gui-test [--mount]
  python3 docker/prepare_docker.py install --name gui-test --apt nano
  python3 docker/prepare_docker.py add --name gui-test --src /tmp/x --dst /app/x
  python3 docker/prepare_docker.py snapshot --name gui-test --tag mw-base:gui-next
  python3 docker/prepare_docker.py clean                 # purge conteneurs arrêtés
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Image vierge de référence par niveau (FROM réel à la création).
LEVELS = {
    "ubuntu": {"image": "ubuntu:24.04", "desc": "Ubuntu nu + minimaux"},
    "deps":   {"image": "mw-base:ubuntu", "desc": "+ dépendances du projet (modelweaver-base)"},
    "gui":    {"image": "mw-base:deps", "desc": "+ WebKitGTK/Xvfb/xdotool (GUI)"},
    "test":   {"image": "mw-base:deps", "desc": "+ outils de test"},
}

# Depuis quelles images Docker existantes on dérive chaque niveau (build-docker.sh).
DERIVE = {
    "deps": "modelweaver-base:latest",
    "gui":  "modelweaver-gui-test:latest",
}


def _sh(cmd: list, check=True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"CMD échec ({r.returncode}): {' '.join(cmd)}\n{r.stderr}")
    return r.stdout


def _docker(*args: str) -> str:
    return _sh(["docker", *args])


def _tag_exists(tag: str) -> bool:
    out = _docker("images", "--format", "{{.Repository}}:{{.Tag}}")
    return tag in out.splitlines()


def _container_exists(name: str) -> bool:
    out = _docker("ps", "-a", "--format", "{{.Names}}")
    return name in out.splitlines()


def _container_running(name: str) -> bool:
    out = _docker("ps", "--format", "{{.Names}}")
    return name in out.splitlines()


def cmd_list(_a):
    print("=== Images vierges mw-base:* ===")
    out = _docker("images", "--format", "{{.Repository}}:{{.Tag}}  ({{.Size}})")
    for line in out.splitlines():
        if line.startswith("mw-base") or line.startswith("modelweaver"):
            print(" ", line)
    print("\n=== Conteneurs ===")
    out = _docker("ps", "-a", "--format", "{{.Names}}  |  {{.Image}}  |  {{.Status}}")
    print(out if out.strip() else "  (aucun)")


def cmd_ensure(a):
    """S'assure qu'une image vierge de niveau existe (la crée si absente)."""
    level = a.level
    if level not in LEVELS:
        sys.exit(f"niveau inconnu: {level} (dispo: {list(LEVELS)})")
    tag = f"mw-base:{level}"
    if _tag_exists(tag):
        print(f"[ensure] {tag} déjà présent ✓")
        return
    # Dérive depuis une image Docker existante si possible, sinon construit.
    src = DERIVE.get(level)
    if src and _tag_exists(src):
        print(f"[ensure] création {tag} depuis {src} (re-tag, pas de copie)…")
        _docker("tag", src, tag)
        print(f"[ensure] {tag} prêt ✓")
        return
    # Sinon : build via les Dockerfiles du repo.
    df = {"deps": "Dockerfile.base", "gui": "Dockerfile.keep-gui-deps"}.get(level)
    if df and (REPO / "docker" / df).exists():
        print(f"[ensure] build {tag} depuis docker/{df}…")
        _docker("build", "-t", tag, "-f", str(REPO / "docker" / df), str(REPO))
        print(f"[ensure] {tag} prêt ✓")
        return
    sys.exit(f"[ensure] impossible de créer {tag} : pas de dérivation ni Dockerfile pour {level}")


def cmd_run(a):
    """Crée/relance un conteneur depuis une image vierge (recrée si image changée)."""
    tag = f"mw-base:{a.level}"
    if not _tag_exists(tag):
        cmd_ensure(a)
    name = a.name
    if _container_running(name):
        print(f"[run] {name} déjà en cours ✓")
        return
    if _container_exists(name):
        print(f"[run] {name} arrêté → redémarrage…")
        _docker("start", name)
        return
    args = ["docker", "run", "-d", "--name", name, "--network", "host"]
    if a.mount:
        args += ["-v", f"{REPO}:/app"]
    args += [tag, "/bin/bash", "-c", "while true; do sleep 3600; done"]
    print(f"[run] création {name} depuis {tag}…")
    _sh(args)
    print(f"[run] {name} lancé ✓")


def cmd_install(a):
    """Installe des paquets apt dans un conteneur (commit optionnel)."""
    if not _container_exists(a.name):
        sys.exit(f"[install] conteneur {a.name} introuvable")
    pkgs = " ".join(a.apt)
    print(f"[install] apt-get install {pkgs} dans {a.name}…")
    _sh(["docker", "exec", a.name, "bash", "-c",
         "apt-get update -qq && apt-get install -y --no-install-recommends " + pkgs])
    print("[install] ok")


def cmd_add(a):
    """Copie un fichier/répertoire hôte dans le conteneur."""
    if not _container_exists(a.name):
        sys.exit(f"[add] conteneur {a.name} introuvable")
    src = Path(a.src)
    if not src.exists():
        sys.exit(f"[add] source introuvable: {src}")
    # docker cp vers un chemin temporel puis mv (évite l'écrasement des montages)
    tmp = f"/tmp/mw-add-{src.name}"
    _sh(["docker", "cp", str(src), f"{a.name}:{tmp}"])
    _sh(["docker", "exec", a.name, "bash", "-c",
         f"mkdir -p {a.dst} && mv -f {tmp} {a.dst}/ && rm -rf {tmp}"])
    print(f"[add] {src} → {a.dst}/")


def cmd_snapshot(a):
    """Commit le conteneur en image (nouveau niveau/snapshot vierge)."""
    if not _container_exists(a.name):
        sys.exit(f"[snapshot] conteneur {a.name} introuvable")
    print(f"[snapshot] commit {a.name} → {a.tag}…")
    _docker("commit", a.name, a.tag)
    print(f"[snapshot] {a.tag} créé ✓")


def cmd_clean(_a):
    """Purge les conteneurs arrêtés + images dangling."""
    out = _docker("ps", "-a", "--filter", "status=exited", "--format", "{{.Names}}")
    for n in out.splitlines():
        print(f"[clean] suppression conteneur {n}")
        _docker("rm", n)
    out = _docker("images", "-f", "dangling=true", "--format", "{{.ID}}")
    for i in out.splitlines():
        print(f"[clean] suppression image dangling {i}")
        _docker("rmi", i)
    print("[clean] ok")


def main():
    p = argparse.ArgumentParser(description="Préparateur de conteneurs Docker ModelWeaver")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list").set_defaults(fn=cmd_list)

    pe = sub.add_parser("ensure")
    pe.add_argument("--level", required=True, choices=list(LEVELS))
    pe.set_defaults(fn=cmd_ensure)

    pr = sub.add_parser("run")
    pr.add_argument("--level", required=True, choices=list(LEVELS))
    pr.add_argument("--name", required=True)
    pr.add_argument("--mount", action="store_true", help="monter le repo dans /app")
    pr.set_defaults(fn=cmd_run)

    pi = sub.add_parser("install")
    pi.add_argument("--name", required=True)
    pi.add_argument("--apt", nargs="+", required=True)
    pi.set_defaults(fn=cmd_install)

    pa = sub.add_parser("add")
    pa.add_argument("--name", required=True)
    pa.add_argument("--src", required=True)
    pa.add_argument("--dst", required=True)
    pa.set_defaults(fn=cmd_add)

    ps = sub.add_parser("snapshot")
    ps.add_argument("--name", required=True)
    ps.add_argument("--tag", required=True)
    ps.set_defaults(fn=cmd_snapshot)

    pc = sub.add_parser("clean")
    pc.set_defaults(fn=cmd_clean)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
