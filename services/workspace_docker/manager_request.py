"""workspace_docker.manager_request — logique du manager_de_docker.

Gère un catalogue d'images disponibles, un budget d'espace, et répond aux demandes
selon des specs positives (with) et négatives (without). Quand un docker répond
au moins aux specs, il en fait une copie et donne la copie au demandeur.
"""

import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

# Catalogue statique d'images disponibles (nom -> specs)
_IMAGE_CATALOG: Dict[str, Dict[str, Any]] = {
    "python3-pytest": {
        "with": ["python3", "pytest", "pip"],
        "without": ["gcc", "node"],
        "memory_mb": 1024,
        "cpu_cores": 1,
    },
    "python3-dev": {
        "with": ["python3", "pip", "gcc"],
        "without": ["node", "rust"],
        "memory_mb": 2048,
        "cpu_cores": 2,
    },
    "node-dev": {
        "with": ["node", "npm", "gcc"],
        "without": ["python3"],
        "memory_mb": 1024,
        "cpu_cores": 1,
    },
}

# Budget par défaut (MB)
_BUDGET_LIMIT_MB: int = 50000
_COPIES_DIR = Path("/tmp/docker_copies")


def _clean_unused_copies(required_mb: int) -> int:
    """Supprime les copies non utilisées (priorité : plus anciennes d'abord)
    jusqu'à libérer au moins required_mb. Retourne l'espace libéré."""
    freed = 0
    if not _COPIES_DIR.exists():
        return freed
    # Liste des copies par âge (plus ancien d'abord)
    copies = sorted(
        [p for p in _COPIES_DIR.iterdir() if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
    )
    for copy_path in copies:
        if freed >= required_mb:
            break
        size_mb = sum(f.stat().st_size for f in copy_path.rglob("*") if f.is_file()) // (1024 * 1024)
        # Simule : on supprime la copie
        try:
            import shutil
            shutil.rmtree(copy_path)
            freed += size_mb
        except Exception:
            pass
    return freed


def manager_request(inputs: Dict[str, Any], home: str = "") -> Dict[str, Any]:
    specs = inputs.get("specs") or {}
    agent_id = inputs.get("agent_id", "unknown")
    workspace_id = inputs.get("workspace_id", "default")

    specs_with = set(specs.get("with", []))
    specs_without = set(specs.get("without", []))
    req_memory = int(specs.get("memory_mb", 1024) or 1024)
    req_cpu = int(specs.get("cpu_cores", 1) or 1)

    best_match: Optional[str] = None
    best_specs: Optional[Dict[str, Any]] = None

    for image_name, image_specs in _IMAGE_CATALOG.items():
        image_with = set(image_specs.get("with", []))
        image_without = set(image_specs.get("without", []))

        # Vérifie que le docker répond au moins aux specs positives et négatives
        if specs_with and not specs_with.issubset(image_with):
            continue
        if specs_without and not specs_without.issubset(image_without):
            # Si la spec négative demande qu'un logiciel soit absent, l'image doit aussi l'absenter
            # Ici simplifié : on vérifie que les absences demandées sont respectées
            # Pour le cas simple, on considère que si l'image a un logiciel que le demandeur interdit,
            # elle est rejetée.
            if specs_without.intersection(image_with):
                continue

        # Vérifie la mémoire et CPU
        if req_memory > image_specs.get("memory_mb", 0):
            continue
        if req_cpu > image_specs.get("cpu_cores", 0):
            continue

        best_match = image_name
        best_specs = image_specs.copy()
        break  # premier match suffit

    if best_match is None:
        # Crée un nouveau conteneur selon les specs (simulé)
        best_match = f"custom-{agent_id}-{workspace_id}"
        best_specs = {
            "with": list(specs_with),
            "without": list(specs_without),
            "memory_mb": req_memory,
            "cpu_cores": req_cpu,
        }

    # Vérifie et libère le budget si nécessaire
    estimated_size = req_memory  # simplifié : taille = mémoire requise
    current_usage = sum(
        sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        for p in _COPIES_DIR.iterdir() if p.is_dir()
    ) // (1024 * 1024) if _COPIES_DIR.exists() else 0
    if current_usage + estimated_size > _BUDGET_LIMIT_MB:
        needed = (current_usage + estimated_size) - _BUDGET_LIMIT_MB
        _clean_unused_copies(needed + 1024)  # marge de sécurité

    # Fait une copie du docker (simulée par un dossier)
    copy_dir = Path(tempfile.mkdtemp(prefix=f"docker_copy_{agent_id}_"))
    # Simule la copie d'une image
    (copy_dir / ".docker_image").write_text(best_match)
    (copy_dir / ".specs.json").write_text(str(best_specs))

    return {
        "ok": True,
        "docker_id": best_match,
        "docker_copy_path": str(copy_dir),
        "specs_matched": best_specs,
        "error": "",
    }
