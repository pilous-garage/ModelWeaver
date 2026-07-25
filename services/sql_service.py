"""Service sql : accès aux bases de données.

Usage depuis les handlers daemon :
    from services.sql_service import init_db, check_db, get_db_versions
"""
from modules.sql.sql_module import read_db_version


def init_db(_params: dict) -> dict:
    from services.api._shared import _get_mw
    mw = _get_mw()
    mw.init_database()
    from services.api._shared import _get_cat
    _get_cat().init_database()
    return {"status": "ok", "db": "initialized"}


def check_db(_params: dict) -> dict:
    from services.api._shared import _get_mw
    mw = _get_mw()
    report = mw.check_database(random_read=3)
    return {"status": "ok" if report.get("errors", 0) == 0 else "degraded"}


def get_db_versions(_params: dict) -> dict:
    """Renvoie les `PRAGMA data_version` par DB (+ meta 'dependencies')."""
    from services.api._shared import _get_mw, _get_cat, _get_rt
    from modules.sql.sql_module import read_db_version
    out = {}
    try:
        out["inventory"] = read_db_version(_get_mw().conn)
    except Exception:
        out["inventory"] = 0
    try:
        out["catalogue"] = max(read_db_version(_get_cat().conn), _get_rt().read_meta("catalogue"))
    except Exception:
        out["catalogue"] = 0
    try:
        out["runtime"] = read_db_version(_get_rt().conn)
    except Exception:
        out["runtime"] = 0
    try:
        out["dependencies"] = _get_rt().read_meta("dependencies")
    except Exception:
        out["dependencies"] = 0
    return out
