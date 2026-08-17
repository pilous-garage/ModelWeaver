"""catalogue_verif — VÉRIFICATEUR DE COMPLÉTION DU CATALOGUE (Idée 18/P2).

Scanne les tables (catalogue/workspace/agents/runtime) et RAPPORTE :
  - les tables VIDE attendues (trous d'initialisation),
  - les discordances de référentiels (ex. sub_task_type 'analysis' présent
    dans le workflow mais pas dans scoring_task_types),
  - les redondances suspectes (plusieurs tables qui décrivent la même offre),
  - les réponses suggérées par l'audit (add_* à faire, domaines à modifier).

Ne modifie RIEN : c'est un outil de diagnostic (read-only). Les fonctions
add_* (catalogue_manage) font les corrections en CASCADE.

Usage :
    from services.catalogue_verif import run_verif
    report = run_verif()
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from modules.sql.schema import mw_home


def _count(conn, table: str) -> int:
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except Exception:
        return -1


def _tables(conn) -> List[str]:
    try:
        return [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
    except Exception:
        return []


def _connect(name: str):
    import sqlite3
    p = mw_home() / name
    if not os.path.exists(p):
        return None
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    return c


# Tables que l'on considère DEVRAIENT être peuplées (référentiels + socle).
EXPECTED_NONEMPTY = {
    "catalogue": ["catalogue_providers", "catalogue_models", "provider_models",
                  "provider_model_address", "adresse_runtime", "scoring_domaines",
                  "scoring_task_types", "budget_tags", "budget_final",
                  "llm_domaine_score", "llm_task_type_score", "task_level_cost",
                  "task_level_stats", "thinking_power_model"],
    "workspace": ["workspaces", "tasks", "sub_tasks"],
    "agents": ["agents", "agent_entrypoints"],
    "runtime": ["real_call_models"],
}


def _check_referential_discordances(cat) -> List[Dict[str, Any]]:
    """Discordances entre les référentiels (ex. type utilisé mais pas déclaré)."""
    issues: List[Dict[str, Any]] = []
    if cat is None:
        return issues
    try:
        # Types de sub_task réellement utilisés vs scoring_task_types.
        used = _conn_workspace_subtask_types()
        declared = {r[0] for r in cat.execute(
            "SELECT code FROM scoring_task_types").fetchall()}
        for t in sorted(used - declared):
            issues.append({
                "type": "discordance",
                "severity": "error",
                "table": "scoring_task_types",
                "msg": f"sub_task_type '{t}' utilisé par le workflow mais "
                       f"absent de scoring_task_types → add_task_type('{t}')",
                "fix": f"add_task_type",
            })
        # Types dans les règles supervisor vs déclarés.
        try:
            import sqlite3
            ws = _connect("workspace.db")
            if ws:
                rule_types = set()
                for r in ws.execute(
                        "SELECT in_type, out_type FROM task_supervisor_rules").fetchall():
                    rule_types.add(r["in_type"] or "")
                    rule_types.add(r["out_type"] or "")
                for t in sorted(rule_types - declared - {""}):
                    issues.append({
                        "type": "discordance", "severity": "warning",
                        "table": "task_supervisor_rules",
                        "msg": f"type '{t}' dans les règles supervisor mais "
                               f"absent de scoring_task_types",
                        "fix": "add_task_type",
                    })
                ws.close()
        except Exception:
            pass
    except Exception:
        pass
    return issues


def _conn_workspace_subtask_types() -> set:
    ws = _connect("workspace.db")
    if not ws:
        return set()
    try:
        rows = ws.execute(
            "SELECT DISTINCT sub_task_type FROM sub_tasks").fetchall()
        ws.close()
        return {r[0] for r in rows if r[0]}
    except Exception:
        try:
            ws.close()
        except Exception:
            pass
        return set()


def _check_redondances(cat, agents, runtime) -> List[Dict[str, Any]]:
    """Redondances suspectes (plusieurs tables décrivant la même offre)."""
    issues: List[Dict[str, Any]] = []
    try:
        if cat:
            n_map = _count(cat, "provider_models_mapping")
            n_addr = _count(cat, "provider_model_address")
            n_pm = _count(cat, "provider_models")
            if n_map > 0 or n_addr > 0 or n_pm > 0:
                issues.append({
                    "type": "redondance", "severity": "info",
                    "table": "provider_models*",
                    "msg": f"3 tables décrivent l'offre provider×model : "
                           f"provider_models_mapping({n_map}), "
                           f"provider_model_address({n_addr}), "
                           f"provider_models({n_pm}) — à consolider ou "
                           f"documenter les rôles",
                    "fix": "à trancher",
                })
    except Exception:
        pass
    return issues


def _check_empty_expected(cat, ws, agents, rt) -> List[Dict[str, Any]]:
    """Tables DEVRAIENT être peuplées mais vides."""
    issues: List[Dict[str, Any]] = []
    for dbname, conn in (("catalogue", cat), ("workspace", ws),
                         ("agents", agents), ("runtime", rt)):
        if conn is None:
            continue
        for t in EXPECTED_NONEMPTY.get(dbname, []):
            n = _count(conn, t)
            if n == 0:
                issues.append({
                    "type": "vide", "severity": "warning",
                    "table": t, "db": dbname,
                    "msg": f"{dbname}.{t} est VIDE — à initialiser",
                    "fix": "seed/init",
                })
    return issues


def _check_adresse_errors(cat) -> List[Dict[str, Any]]:
    """Adresses / budgets : état des états d'erreur et des allocations."""
    issues: List[Dict[str, Any]] = []
    if cat is None:
        return issues
    try:
        n_alloc = _count(cat, "agent_budget_allocation")
        if n_alloc == 0:
            issues.append({
                "type": "info", "severity": "info",
                "table": "agent_budget_allocation",
                "msg": "aucune allocation agent→budget active (normal tant que "
                       "la découpe/attribution réelle n'a pas tourné)",
                "fix": "",
            })
    except Exception:
        pass
    return issues


def run_verif(include_runtime: bool = True) -> Dict[str, Any]:
    """Point d'entrée : scanne tout et retourne le rapport."""
    report: Dict[str, Any] = {"ok": True, "issues": [], "stats": {}}
    cat = _connect("catalogue.db")
    ws = _connect("workspace.db")
    agents = _connect("agents.db")
    rt = _connect("runtime.db") if include_runtime else None

    for dbname, conn in (("catalogue", cat), ("workspace", ws),
                         ("agents", agents), ("runtime", rt)):
        if conn is None:
            continue
        n_tables = len(_tables(conn))
        n_empty = sum(1 for t in _tables(conn) if _count(conn, t) == 0)
        report["stats"][dbname] = {"tables": n_tables, "empty": n_empty}

    report["issues"] += _check_empty_expected(cat, ws, agents, rt)
    report["issues"] += _check_referential_discordances(cat)
    report["issues"] += _check_redondances(cat, agents, rt)
    report["issues"] += _check_adresse_errors(cat)

    for conn in (cat, ws, agents, rt):
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    report["count"] = len(report["issues"])
    return report