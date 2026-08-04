"""Panel Creator — compile les panneaux GUI isolément et les ajoute au runtime.

Service supervisé (process séparé). Rôle :
  1. VALIDER le contrat d'un panneau (.panel.tsx → expose `Panel: PanelDef`).
  2. VÉRIFIER les dépendances (partagées vs autonomes).
  3. COMPILER le panneau isolément (esbuild) → dist/panels/<id>.js (module ES).
  4. AJOUTER au registre runtime (panels/index.json) que le daemon sert.

Les panneaux "essentiels" restent dans le monolithe (bundle Vite) — ce service
ne gère que les panneaux EXTERNES (extensions / marketplace).

Architecture cible (hybride) :
  - Monolithe : panels essential:true, compilés dans le bundle GUI.
  - Modules  : panels externes, compilés ici, servis par le daemon
    (route panels/get/<id>), chargés par la GUI via import() dynamique.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services._common import mw_home

# Dossiers
PANELS_SRC = REPO_ROOT / "interfaces" / "main" / "GUI" / "official" / "gui" / "src" / "panels"
DIST_DIR = mw_home() / "panels-dist"       # JS compilés servis par le daemon
REGISTRY = mw_home() / "panels" / "index.json"  # registre des panels externes
GUI_NODE_MODULES = REPO_ROOT / "interfaces" / "main" / "GUI" / "official" / "gui" / "node_modules"

# Dépendances partagées (fournies par la GUI hôte, pas bundlées)
SHARED_DEPS = {"react", "react-dom"}

# Contrat minimal d'un panneau : champs obligatoires du PanelDef
REQUIRED_FIELDS = ["id", "label", "version", "description", "component"]

# Intervalle de compilation automatique (sec) — 0 = désactivé (compilation manuelle)
SCAN_INTERVAL_S = 0.0


# ── Contrat ──────────────────────────────────────────────────────────


def _extract_panel_source(tsx: Path) -> str:
    return tsx.read_text(encoding="utf-8")


def validate_contract(source: str) -> dict:
    """Valide qu'un fichier .panel.tsx expose bien un `Panel: PanelDef` valide.

    Retourne {ok, errors: [...], id, label, version, essential, shared_deps,
    auto_deps}. Ne compile pas — vérifie le CONTRAT statiquement."""
    errors = []
    # Export `export const Panel: PanelDef`
    if "export const Panel" not in source and "export const Panel:" not in source:
        errors.append("export const Panel (PanelDef) manquant")
    # Champs obligatoires : id = "..."
    for field in ("id", "label", "version", "description", "component"):
        if f"{field}:" not in source and f'"{field}"' not in source:
            errors.append(f"champ obligatoire '{field}' manquant")

    id_m = re.search(r'id\s*:\s*["\']([^"\']+)["\']', source)
    label_m = re.search(r'label\s*:\s*["\']([^"\']+)["\']', source)
    version_m = re.search(r'version\s*:\s*["\']([^"\']+)["\']', source)
    essential = "essential: true" in source

    # Dépendances importées (pour décider partagé/autonome)
    imports = set(re.findall(r'^import\s+.*?from\s+["\']([^"\']+)["\']', source, re.M))
    imports |= set(re.findall(r'from\s+["\']([^"\']+)["\']', source))
    shared = sorted(i for i in imports if i in SHARED_DEPS or i.startswith("react"))
    auto = sorted(i for i in imports if i not in SHARED_DEPS)

    return {
        "ok": not errors,
        "errors": errors,
        "id": id_m.group(1) if id_m else None,
        "label": label_m.group(1) if label_m else None,
        "version": version_m.group(1) if version_m else None,
        "essential": essential,
        "shared_deps": shared,
        "auto_deps": auto,
    }


# ── Compilation ──────────────────────────────────────────────────────


def _esbuild_available() -> bool:
    return (GUI_NODE_MODULES / "esbuild" / "bin" / "esbuild").exists() or \
        shutil.which("npx") is not None


def compile_panel(tsx: Path, output: Path, external_deps=None) -> dict:
    """Compile un .panel.tsx en module ES autonome via esbuild.

    `external_deps` : dépendances à NE PAS bundler (marquées externes, fournies
    par l'hôte). Vide = panneau totalement autonome.
    Retourne {ok, out, error}."""
    if not _esbuild_available():
        return {"ok": False, "error": "esbuild indisponible (npm i dans la GUI)"}
    # esbuild est une dépendance du GUI — on lance npx depuis le dossier GUI
    # pour résoudre correctement react/react-dom et les modules locaux.
    args = ["npx", "esbuild", str(tsx),
            "--bundle", "--format=esm", "--jsx=automatic",
            "--platform=browser", f"--outfile={output}",
            "--log-level=warning"]
    for dep in (external_deps or []):
        args.append(f"--external:{dep}")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=120,
                           cwd=str(GUI_NODE_MODULES.parent),
                           env={**os.environ})
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or r.stdout)[-500:]}
        return {"ok": True, "out": str(output), "size": output.stat().st_size}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Registre ─────────────────────────────────────────────────────────


def _load_registry() -> list:
    try:
        return json.loads(REGISTRY.read_text(encoding="utf-8")) if REGISTRY.exists() else []
    except Exception:
        return []


def _save_registry(registry: list) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(registry, indent=2, ensure_ascii=False),
                        encoding="utf-8")


def build_all(force: bool = False) -> dict:
    """Compile tous les .panel.tsx NON-essentiels → dist/panels/<id>.js.

    Les panels essentiels (essential: true) restent dans le monolithe et sont
    ignorés ici. Retourne un résumé {compiled, skipped, errors}."""
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    compiled, skipped, errors = [], [], []
    for tsx in sorted(PANELS_SRC.rglob("*.panel.tsx")):
        src = _extract_panel_source(tsx)
        contract = validate_contract(src)
        if contract["essential"]:
            skipped.append(f"{tsx.name} (essentiel, monolithe)")
            continue
        if not contract["ok"]:
            errors.append(f"{tsx.name}: {contract['errors']}")
            continue
        pid = contract["id"] or tsx.stem
        out = DIST_DIR / f"{pid}.js"
        if out.exists() and not force:
            skipped.append(f"{tsx.name} (déjà compilé)")
            continue
        # Partagé par défaut : react/react-dom en externe ; le reste bundlé.
        res = compile_panel(tsx, out, external_deps=contract["shared_deps"])
        if res["ok"]:
            compiled.append({"id": pid, "file": f"panels/{pid}.js",
                             "label": contract["label"], "version": contract["version"]})
        else:
            errors.append(f"{tsx.name}: {res['error']}")
    # Mettre à jour le registre (garder les non-essentiels compilés)
    registry = _load_registry()
    registry = [r for r in registry if r.get("id") not in {c["id"] for c in compiled}]
    registry.extend(compiled)
    _save_registry(registry)
    return {"compiled": compiled, "skipped": skipped, "errors": errors}


def add_panel(tsx_path: str) -> dict:
    """Ajoute un panneau externe : valide le contrat, compile, enregistre."""
    tsx = Path(tsx_path).resolve()
    if not tsx.exists() or not tsx.name.endswith(".panel.tsx"):
        return {"ok": False, "error": f"fichier .panel.tsx introuvable: {tsx_path}"}
    src = _extract_panel_source(tsx)
    contract = validate_contract(src)
    if not contract["ok"]:
        return {"ok": False, "errors": contract["errors"], "contract": contract}
    if contract["essential"]:
        return {"ok": False, "error": "panel essentiel (à garder dans le monolithe)"}
    pid = contract["id"]
    out = DIST_DIR / f"{pid}.js"
    res = compile_panel(tsx, out, external_deps=contract["shared_deps"])
    if not res["ok"]:
        return {"ok": False, "error": res["error"]}
    registry = [r for r in _load_registry() if r.get("id") != pid]
    registry.append({"id": pid, "file": f"panels/{pid}.js",
                     "label": contract["label"], "version": contract["version"],
                     "source": "external"})
    _save_registry(registry)
    return {"ok": True, "id": pid, "file": f"panels/{pid}.js",
            "label": contract["label"], "version": contract["version"]}


def list_panels() -> list:
    """Liste les panels externes enregistrés (pour le daemon / GUI)."""
    return _load_registry()


def status() -> dict:
    """État : panels compilés, tailles, santé des fichiers."""
    out = []
    for entry in _load_registry():
        f = DIST_DIR / (entry["file"].split("/")[-1] if entry.get("file") else f"{entry['id']}.js")
        out.append({**entry, "exists": f.exists(),
                    "size": f.stat().st_size if f.exists() else 0})
    return {"count": len(out), "panels": out}


def run(interval: float = SCAN_INTERVAL_S) -> None:
    """Boucle : recompile si de nouveaux panels non-essentiels apparaissent."""
    print("panel-creator: démarrage (scan:", PANELS_SRC, ")", flush=True)
    build_all()
    if interval <= 0:
        print("panel-creator: scan automatique désactivé", flush=True)
        return
    while True:
        try:
            res = build_all()
            if res["compiled"]:
                print(f"panel-creator: {len(res['compiled'])} panneau(x) compilé(s)", flush=True)
        except Exception as e:
            print(f"panel-creator: erreur: {e}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    run()
