"""openai_compat — Endpoint OpenAI-compatible pour le swarm.

Transforme le swarm ModelWeaver en un « modèle virtuel » compatible
/chat/completions : les frameworks de benchmark (Inspect, Promptfoo,
OpenCompass) bombardent cet endpoint comme s'il s'agissait d'un LLM unique.

payload envoyé par le benchmark :
    {model, messages: [{role, content}], stream?, max_tokens?, temperature?}

Traduction :
  - le DERNIER message user.content → le prompt du swarm
  - `model` sélectionne le mode :
      * modèle contenant "build"   → dev-chat mode=build (génère + exécute)
      * modèle contenant "plan"    → dev-chat mode=plan
      * sinon                      → plan (défaut)
  - le résultat du swarm (dev-chat/send) → format OpenAI :

    {choices: [{message: {role: assistant, content}, finish_reason}]}

Le `stream` n'est pas encore supporté (réponse non-stream d'abord).
"""

import json
from typing import Any, Dict

from services.api.router import register


def _last_user_content(messages) -> str:
    """Le contenu du dernier message user."""
    if not isinstance(messages, list):
        return ""
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            return str(m.get("content", ""))
    return ""


def _mode_from_model(model: str) -> str:
    m = (model or "").lower()
    if "build" in m:
        return "build"
    return "plan"


def op_openai_chat_completions(params: Dict[str, Any]) -> Dict[str, Any]:
    """/v1/chat/completions — le swarm répond comme un vrai LLM.

    Le fake-api-llm-manager (orchestration swarm_llm_manager) reçoit la prompt
    telle quelle, crée un dépôt local + branche vierge, y dépose la prompt et
    les éventuels fichiers fournis (tool_calls/JSON), l'analyste découpe en
    tâches par rôle, les greedy travaillent, puis on renvoie les fichiers
    produits + résumé AU FORMAT LLM (choices[0].message.content).

    params (payload OpenAI) : {model, messages, tool_calls?, ...}.
    """
    model = params.get("model", "mw-swarm")
    messages = params.get("messages", [])
    prompt = _last_user_content(messages)
    if not prompt:
        return {"ok": False,
                "error": {"message": "aucun message user", "type": "invalid_request_error"}}
    # Fichiers fournis par le benchmark (tool_calls / JSON) → déposés dans la
    # branche. On supporte : messages[].tool_calls (fichiers à créer) et un
    # éventuel param files (dict name→content).
    files: Dict[str, Any] = {}
    for m in (messages or []):
        for tc in (m.get("tool_calls") or []):
            fn = tc.get("function", {})
            if fn.get("name") in ("write_file", "create_file"):
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                    path = args.get("path") or args.get("filename") or args.get("file")
                    content = args.get("content", "")
                    if path:
                        files[str(path)] = str(content)
                except Exception:
                    pass
    if isinstance(params.get("files"), dict):
        files.update(params.get("files"))
    try:
        from services.swarm_llm_manager import run_completion
        res = run_completion(prompt, files=files or None)
    except Exception as e:  # noqa: BLE001
        return {"ok": False,
                "error": {"message": str(e), "type": "server_error"}}
    if not res.get("ok"):
        return {"ok": False,
                "error": {"message": res.get("error", "swarm échoué"),
                          "type": "server_error"}}
    res["model"] = model
    return res


register("chat/completions", op_openai_chat_completions)


def _bench_db():
    from modules.sql.catalogue_local import LocalCatalogue
    return LocalCatalogue(mode="w", write_token="write_catalogue")


def op_bench_submit(params: Dict[str, Any]) -> Dict[str, Any]:
    """Soumet un résultat de benchmark (feedback du swarm).

    params : {suite, task, score, passed, total, meta?, token}.
    Écrit dans bench_scores (writer catalogue). Retourne {ok, id}."""
    try:
        db = _bench_db()
        db._check_write(params.get("token", ""))
        db.conn.execute(
            "INSERT INTO bench_scores (suite, task, score, passed, total, meta_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (params.get("suite", ""), params.get("task", ""),
             float(params.get("score", 0)),
             int(params.get("passed", 0)), int(params.get("total", 0)),
             json.dumps(params.get("meta") or {}, ensure_ascii=False)))
        bid = db.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.conn.commit()
        return {"ok": True, "id": bid}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def op_bench_stats(params: Dict[str, Any]) -> Dict[str, Any]:
    """Stats des scores d'une suite (ou toutes).

    params : {suite?} → {suite, count, avg_score, passed, total}."""
    try:
        from modules.sql.schema import _default_local_catalogue_db
        import sqlite3
        conn = sqlite3.connect(f"file:{_default_local_catalogue_db()}?mode=ro",
                               uri=True)
        conn.row_factory = sqlite3.Row
        suite = params.get("suite", "")
        if suite:
            rows = conn.execute(
                "SELECT COUNT(*) n, AVG(score) avg, SUM(passed) p, SUM(total) t "
                "FROM bench_scores WHERE suite = ?", (suite,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT suite, COUNT(*) n, AVG(score) avg, SUM(passed) p, "
                "SUM(total) t FROM bench_scores GROUP BY suite").fetchall()
        conn.close()
        out = []
        for r in rows:
            d = dict(r)
            d["avg_score"] = round(d["avg"] or 0, 4)
            out.append(d)
        return {"ok": True, "stats": out}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


register("bench/submit", op_bench_submit)
register("bench/stats", op_bench_stats)


def op_test_benchmark_auto(params: Dict[str, Any]) -> Dict[str, Any]:
    """test-benchmark-auto — branche la team sur swarm-as-llm, exécute un
    benchmark, retourne le rapport (providers/modèles, nb_req, tok_in/tok_out,
    coûts, durées globale + par step, notes).

    params :
      - benchmark_name : 'factorial' | 'fibonacci' | 'inspect'
      - team           : nom de la team (défaut 'llm-code')
      - restrict_llm   : liste de modèles OU budget
                        {tok_in, tok_out, nb_req, dollars, time_s}
      - n              : nb de tâches (défaut 3)
      - per_task       : collecte les durées/coûts par step (bool)
      - api_key        : token du daemon (défaut lu depuis ~/.modelweaver)
    """
    from services.benchmark_runner import run_benchmark
    team = params.get("team", "llm-code")
    bname = params.get("benchmark_name", "")
    if not bname:
        return {"status": "error", "error": "benchmark_name requis"}
    # token du daemon pour l'endpoint
    api_key = params.get("api_key", "")
    if not api_key:
        try:
            from services._common import mw_home
            tf = mw_home() / "api.token"
            api_key = tf.read_text().strip() if tf.exists() else ""
        except Exception:
            api_key = ""
    try:
        report = run_benchmark(
            team=team,
            benchmark_name=bname,
            restrict_llm=params.get("restrict_llm"),
            n=int(params.get("n", 3)),
            base_url=params.get("base_url", "http://127.0.0.1:8770/v1"),
            api_key=api_key,
            per_task=bool(params.get("per_task", False)),
        )
        report["status"] = "ok"
        return report
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


register("test-benchmark-auto", op_test_benchmark_auto)
