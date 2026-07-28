"""Bibliothèques système pour commandes multi-plateforme.

Chaque fichier .py dans ce répertoire implémente une commande
pour une plateforme donnée. Le loader dynamique de ShellAuth
retrouve le bon module via load_system_lib().

Convention de nommage : {commande}.py
Le module doit exposer une fonction execute(args: list[str], stdin: str | None, workdir: str) -> dict."""

import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


_LIB_DIR = Path(__file__).parent


def load(name: str) -> Optional[Any]:
    """Charge dynamiquement une lib_système par nom de commande.

    Retourne le module Python si trouvé, sinon None."""
    candidate = _LIB_DIR / f"{name}.py"
    if not candidate.exists():
        return None
    spec = importlib.util.spec_from_file_location(f"lib_système.{name}", str(candidate))
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        return None
    return module


def execute(
    name: str,
    args: List[str],
    stdin: Optional[str],
    workdir: str,
) -> Optional[Dict[str, Any]]:
    """Exécute une commande via sa lib_système si disponible."""
    mod = load(name)
    if mod is None:
        return None
    fn = getattr(mod, "execute", None)
    if fn is None:
        return None
    return fn(args, stdin, workdir)


def available_commands() -> List[Tuple[str, str]]:
    """Liste les commandes disponibles dans les lib_système.

    Retourne [(nom, chemin)]."""
    results = []
    if not _LIB_DIR.exists():
        return results
    for path in sorted(_LIB_DIR.iterdir()):
        if path.suffix == ".py" and not path.name.startswith("_"):
            results.append((path.stem, str(path)))
    return results