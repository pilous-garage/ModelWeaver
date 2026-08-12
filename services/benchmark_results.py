"""benchmark_results — journalisation des résultats de benchmark avec timestamp.

Chaque run de benchmark (bench_swarm_live, run_inspect_swarm, benchmark_runner)
loggue son résultat :
  - en BDD : table `bench_scores` (suite, task, score, passed, total, meta_json,
    created_at) — le catalogue local (writer).
  - en fichier : {MW_HOME}/logs/benchmark_results.jsonl (lisible, JSON par ligne).

`meta_json` contient les détails : statut, durée, picked/done, modèles utilisés,
erreurs. Le timestamp `created_at` (UTC) permet de suivre l'historique des runs.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _jsonl_path() -> Path:
    mw = Path(os.environ.get("MODELWEAVER_HOME")
              or os.environ.get("MW_HOME") or Path.home() / ".modelweaver")
    logs = mw / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return logs / "benchmark_results.jsonl"


def log_result(suite: str, task: str = "", score: float = 0.0,
               passed: int = 0, total: int = 0,
               meta: dict | None = None) -> int:
    """Loggue un résultat de benchmark (timestampé) dans bench_scores + JSONL.

    Retourne l'id BDD (0 si échec d'écriture BDD — le JSONL reste écrit)."""
    created = _now_iso()
    meta = meta or {}
    meta.setdefault("created_at", created)

    # 1) Fichier JSONL (toujours).
    try:
        rec = {
            "suite": suite, "task": task, "score": score,
            "passed": passed, "total": total,
            "created_at": created, "meta": meta,
        }
        with open(_jsonl_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

    # 2) BDD bench_scores (writer catalogue local).
    bid = 0
    try:
        from modules.sql.catalogue_local import LocalCatalogue
        db = LocalCatalogue(mode="w", write_token="write_catalogue")
        cur = db.conn.execute(
            "INSERT INTO bench_scores (suite, task, score, passed, total, "
            "meta_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (suite, task, float(score), int(passed), int(total),
             json.dumps(meta, ensure_ascii=False), created))
        bid = cur.lastrowid
        db.conn.commit()
        db.close()
    except Exception:
        pass
    return bid


def log_report(suite: str, report: dict, meta: dict | None = None) -> int:
    """Loggue un rapport complet de benchmark_runner / bench_swarm_live.

    `report` : {status, task_id, picked_s, done_s, duration_s, score, ...}.
    Construit un enregistrement normalisé (suite, score, passed/total, meta)."""
    status = report.get("status", "?")
    passed = 1 if status in ("ok", "done") else 0
    total = 1
    meta = dict(meta or {})
    meta.update({
        "status": status,
        "task_id": report.get("task_id"),
        "picked_s": report.get("picked_s"),
        "done_s": report.get("done_s"),
        "duration_s": report.get("duration_s"),
        "agents_running": report.get("agents_running"),
        "last_status": report.get("last_status"),
        "reason": report.get("reason"),
    })
    score = 1.0 if status in ("ok", "done") else 0.0
    return log_result(suite, task=str(report.get("task_id") or ""),
                      score=score, passed=passed, total=total, meta=meta)


def read_results(suite: str = "", limit: int = 20) -> list:
    """Lit les derniers résultats loggés (JSONL), filtrés par suite."""
    out = []
    path = _jsonl_path()
    if not path.exists():
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if suite and rec.get("suite") != suite:
                continue
            out.append(rec)
    return out[-limit:]
