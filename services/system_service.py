"""Service system : dépendances système.

Usage depuis les handlers daemon :
    from services.system_service import check_deps, install_dep, install_target, check_manifest
"""
from modules.system.system_module import install_system_package, install_target_dependencies
from services.depends import check_all_units
from modules.system import deps as _deps_mod


def check_deps(_params: dict) -> dict:
    result = check_all_units()
    return {"ok": result, "units": []}


def install_dep(params: dict) -> dict:
    return install_system_package(params["target"])


def install_target(params: dict) -> dict:
    return install_target_dependencies(
        target=params.get("target", ""),
        include_optional=params.get("include_optional", False),
    )


def check_manifest(params: dict) -> dict:
    return _deps_mod.check_manifest(params.get("target", ""), params.get("include_optional", False))
