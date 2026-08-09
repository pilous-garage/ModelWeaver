"""workflow/autonomous@v1 — boucle LLM autonome avec signaux de sortie.

Appelé depuis un workflow FSM :
    - type: call
      fn: workflow/autonomous@v1
      inputs:
        request: "Implémente la feature X"
        bundles: ["dev", "workflow"]
        context: "informations supplémentaires"
        max_loops: 30

Retourne un dict avec signal + résultat :
    {"signal": "tool_finish", "tool": "git.lite.commit", "stdout": "...", "exit_code": 0}
    {"signal": "loop_end", "stdout": "Tâche terminée", "exit_code": 0}
    {"signal": "max_loops", "stdout": "...", "exit_code": 1}
"""

import json
import time
from pathlib import Path
from pathlib import Path as _Path
from typing import Any, Callable, Dict, List, Optional

import yaml as _yaml

from .bundles import resolve as resolve_bundles


def _signal_match(signal_key: str, pattern_list: List[str]) -> bool:
    """Vérifie si signal_key matche un pattern dans la liste."""
    for pattern in pattern_list:
        if pattern == signal_key:
            return True
        if pattern.endswith(".*") and signal_key.startswith(pattern[:-1]):
            return True
    return False


def _auto_git_sync(home: str, phase: str, branch: str = "") -> None:
    """Discipline git automatique sur le clone workspace/{projet} du membre.

    phase="pre"  : fetch + pull avant de commencer (voir le travail des autres).
    phase="post" : add + commit + push après l'action (publier son travail).

    `branch` = la branche de travail du swarm (ex. auto_code_<team_id>). Si
    fournie, on l'utilise (elle est créée si absente) au lieu de la branche
    par défaut du clone (master) — sinon le travail part sur master.

    Infrastructurelle : ne dépend pas du LLM. Ne casse pas si pas de clone.
    """
    import subprocess as _sp
    ws = _Path(home) / "workspace"
    if not ws.is_dir():
        return
    for clone in ws.iterdir():
        if not (clone / ".git").is_dir():
            continue
        c = ["git", "-C", str(clone)]
        try:
            _sp.run(c + ["config", "user.name", "agent-auto"], capture_output=True, timeout=10)
            _sp.run(c + ["config", "user.email", "agent-auto@modelweaver.local"], capture_output=True, timeout=10)
            if phase == "pre":
                _sp.run(c + ["fetch", "-q", "origin"], capture_output=True, timeout=30)
                if branch:
                    # Branche de travail du swarm : on la crée/suivit depuis la
                    # distante si elle existe, sinon depuis la branche par défaut.
                    _sp.run(c + ["checkout", "-q", "-B", branch,
                                 f"origin/{branch}"], capture_output=True, timeout=30)
                    # Si la branche distante n'existe pas encore, checkout -B a
                    # échoué → on la crée depuis HEAD (la branche par défaut).
                    cur = _sp.run(c + ["branch", "--show-current"],
                                  capture_output=True, text=True, timeout=10)
                    if cur.stdout.strip() != branch:
                        _sp.run(c + ["checkout", "-q", "-B", branch],
                                capture_output=True, timeout=30)
                else:
                    # Branche distante courante (origin/HEAD), défaut master
                    try:
                        head = _sp.run(c + ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
                                       capture_output=True, text=True, timeout=10)
                        remote_branch = head.stdout.strip().split("/", 1)[-1] or "master"
                    except Exception:
                        remote_branch = "master"
                    # checkout -B : resynchronise la branche locale sur la distante
                    # (le pre-pull se fait AVANT l'action, rien à perdre localement)
                    _sp.run(c + ["checkout", "-q", "-B", remote_branch,
                                 f"origin/{remote_branch}"], capture_output=True, timeout=30)
            else:
                _git_add_safe(c)
                changed = _sp.run(c + ["diff", "--cached", "--quiet"],
                                  capture_output=True, timeout=10)
                if changed.returncode != 0:
                    _sp.run(c + ["commit", "-q", "-m", "auto-commit agent"], capture_output=True, timeout=10)
                    # Pull --rebase AVANT push : si d'autres membres ont avancé
                    # le repo central depuis notre fetch (push non-fast-forward),
                    # on réconcilie d'abord — sinon le push est rejeté et le
                    # membre boucle (98 tours observé sur coder-b).
                    _sp.run(c + ["fetch", "-q", "origin"], capture_output=True, timeout=30)
                    _sp.run(c + ["pull", "-q", "--rebase", "origin"], capture_output=True, timeout=30)
                    ref = f"HEAD:{branch}" if branch else "HEAD"
                    _sp.run(c + ["push", "-q", "origin", ref], capture_output=True, timeout=30)
        except Exception:
            continue


def _auto_git_head(home: str) -> Dict[str, Any]:
    """HEAD (commit) d'entrée de chaque clone workspace/{projet} du membre.

    Retourne {clone_path: head_sha}. Utilisé pour vérifier ensuite si le
    membre a produit du travail (le HEAD a bougé OU des changements non
    commités existent). Best-effort, vide si pas de clone.
    """
    import subprocess as _sp
    heads: Dict[str, Any] = {}
    ws = _Path(home) / "workspace"
    if not ws.is_dir():
        return heads
    for clone in ws.iterdir():
        if not (clone / ".git").is_dir():
            continue
        c = ["git", "-C", str(clone)]
        try:
            r = _sp.run(c + ["rev-parse", "-q", "HEAD"],
                        capture_output=True, text=True, timeout=10)
            heads[str(clone)] = r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            continue
    return heads


def _auto_git_verify(home: str, branch: str = "") -> Dict[str, Any]:
    """Vérifie que le membre a produit du code dans son clone workspace.

    Compare l'état git à la sortie de boucle :
      - changements NON commités → commit + push auto (le travail est là)
      - HEAD a bougé depuis l'entrée (déjà commité par le membre) → push auto
      - rien de tout ça → `produced_code=False` (aucun travail git)

    Retourne {produced_code, committed, pushed, clones}. Infrastructurel.
    """
    import subprocess as _sp
    result = {"produced_code": False, "committed": False, "pushed": False,
              "clones": 0}
    ws = _Path(home) / "workspace"
    if not ws.is_dir():
        return result
    for clone in ws.iterdir():
        if not (clone / ".git").is_dir():
            continue
        c = ["git", "-C", str(clone)]
        result["clones"] += 1
        try:
            # 1. Changements non commités ?
            _sp.run(c + ["add", "-A"], capture_output=True, timeout=10)
            changed = _sp.run(c + ["diff", "--cached", "--quiet"],
                              capture_output=True, timeout=10)
            has_work = changed.returncode != 0
            # 2. Commit auto si travail présent.
            if has_work:
                _sp.run(c + ["commit", "-q", "-m", "auto-commit agent"],
                        capture_output=True, timeout=10)
                result["committed"] = True
                result["produced_code"] = True
            # 3. Push (si commit local non poussé).
            unpushed = _sp.run(c + ["rev-list", "-q", "--count",
                                    "@{u}..HEAD"], capture_output=True,
                               text=True, timeout=10)
            if unpushed.returncode == 0 and unpushed.stdout.strip().lstrip("0") != "":
                _sp.run(c + ["fetch", "-q", "origin"], capture_output=True, timeout=30)
                _sp.run(c + ["pull", "-q", "--rebase", "origin"],
                        capture_output=True, timeout=30)
                ref = f"HEAD:{branch}" if branch else "HEAD"
                _sp.run(c + ["push", "-q", "origin", ref],
                        capture_output=True, timeout=30)
                result["pushed"] = True
                result["produced_code"] = True
        except Exception:
            continue
    return result


def _git_add_safe(c: List[str]) -> None:
    """git add -A PROTÉGÉ contre les suppressions accidentelles de masse.

    Problème observé : un agent clonait le repo central, travaillait, puis
    `git add -A` stagisait comme "supprimés" TOUS les fichiers absents de son
    working tree (clone incomplet, fichiers effacés par un tool, checkout d'une
    branche incomplète…) → le commit auto supprimait des pans entiers du
    framework (121 fichiers : GUI, _contract, bootstrap…).

    Règle : on ajoute tout, puis on RESTAURE depuis HEAD les fichiers
    supprimés qui dépassent un seuil — un agent de tâche ne supprime JAMAIS
    volontairement des dizaines de fichiers existants. Si le working tree est
    incomplet (fichiers absents non supprimés par la tâche), on les réécrit.
    """
    import subprocess as _sp
    # git add -A normal (stage les ajouts/modifs/suppressions)
    _sp.run(c + ["add", "-A"], capture_output=True, timeout=10)
    # Fichiers supprimés stagés (rapport à HEAD)
    st = _sp.run(c + ["diff", "--cached", "--name-status"],
                 capture_output=True, text=True, timeout=10)
    deleted = []
    for line in st.stdout.splitlines():
        if line.startswith("D"):
            deleted.append(line.split("\t", 1)[-1].strip() if "\t" in line else "")
    deleted = [d for d in deleted if d]
    if not deleted:
        return
    # Un working tree sain ne supprime pas de fichiers sans raison. On
    # restaure TOUTES les suppressions depuis HEAD : si la tâche voulait
    # VRAIMENT supprimer un fichier, elle le refera explicitement.
    # Le seuil de garde : si c'est un petit nombre (<=2) et que la tâche
    # l'a demandé, on les laisse — sinon on restaure.
    if len(deleted) <= 2:
        return
    for f in deleted:
        _sp.run(c + ["checkout", "-q", "HEAD", "--", f], capture_output=True, timeout=10)
        _sp.run(c + ["reset", "-q", "HEAD", "--", f], capture_output=True, timeout=10)


def _resolve_skill_candidates(fn_name: str) -> List[str]:
    """Candidats de noms de skill pour un tool call.

    Le LLM peut appeler `git_clone_v1` (nom d'outil) ou `git_clone` (naturel).
    On génère plusieurs candidats, du plus précis au plus large :
      git_clone_v1        -> git_clone@v1, git/clone@v1, clone@v1
    """
    base = fn_name
    out: List[str] = []

    # 0. Nettoyage du nom brut : le LLM ajoute souvent des caractères
    # parasites (glob_v1>, list_dir_varglob_v1) qui cassent la résolution
    # → 4 échecs consécutifs → run aborté sans livrable.
    import re as _re
    cleaned = _re.sub(r"[^A-Za-z0-9_@/]", "", base)
    # `list_dir_varglob_v1` : le LLM a collé le début de la prochaine
    # sélection ("arg...") à la fin — on reteste la base sans ce suffixe.
    if cleaned != base:
        base = cleaned

    # 1. Tel quel (déjà un nom de skill complet) : git/git_clone@v1
    if "@" in base or "/" in base:
        out.append(base)

    # 2. Conversion outil -> skill : git_clone_v1 -> git_clone@v1 -> git/clone@v1
    no_v = base.replace("_v1", "")
    named = no_v + "@v1"
    if named not in out:
        out.append(named)
    converted = no_v.replace("_", "/") + "@v1"
    if converted not in out:
        out.append(converted)

    # 3. Sans le préfixe de catégorie (dernier segment) : git/clone@v1 -> clone@v1
    if "/" in converted:
        last = converted.split("/")[-1]
        if last not in out:
            out.append(last)

    # 4. Nom sans suffixe : git_clone -> git/git_clone@v1 (tentative préfixe catégorie)
    if "_" in base and "@" not in base:
        parts = base.split("_")
        for k in range(1, len(parts)):
            cand = "/".join(parts[:k]) + "/" + "_".join(parts[k:]) + "@v1"
            if cand not in out:
                out.append(cand)

    return out


def _log_no_tools_call(cat, p_ref: str, m_ref: str, agent_id: str = "") -> None:
    """Trace un « faux appel » : le LLM a répondu en texte SANS tool call.

    Ces réponses « descriptives » ne font rien (l'agent décrit au lieu d'agir).
    On les enregistre dans model_call_log avec error_code='no_tools' pour que
    le scoring d'allocation pénalise les modèles qui n'utilisent pas les
    outils (taux de faux appels) — sinon un modèle bavard non-agentic reste
    scoré comme fiable alors qu'il bloque le swarm.
    """
    if cat is None:
        return
    try:
        cat.conn.execute("""
            INSERT INTO model_call_log
                (provider_id, model_id, provider_model_id, agent_id, success,
                 tokens_in, tokens_out, tokens_thinking, latency_ms,
                 error_code, error_msg, call_type)
            VALUES (
                COALESCE((SELECT id FROM catalogue_providers WHERE ref = ?), 0),
                COALESCE((SELECT id FROM catalogue_models WHERE ref = ?), 0),
                (SELECT pm.id FROM provider_models pm
                  JOIN catalogue_providers p ON p.id = pm.provider_id
                 WHERE p.ref = ? AND pm.provider_model_name = ?),
                ?, 0, 0, 0, 0, 0, 'no_tools', 'réponse texte sans tool call', 'chat')
        """, (p_ref, m_ref, p_ref, m_ref,
              (str(agent_id)[:80] if agent_id else None)))
        cat.conn.commit()
    except Exception:
        try:
            cat.conn.rollback()
        except Exception:
            pass


def _extract_toolcalls_from_text(content: str):
    """Extrait des toolcalls sérialisés dans la réponse texte d'un LLM.

    Certains LLM (ex. poolside/laguna) répondent en texte avec les appels
    d'outils SÉRIALISÉS au lieu du mécanisme natif de tool_calling, ex. :
      {"name": "write_file_v1", "parameters": {...}}
      {"tool": "git_commit_v1", "args": {...}}
    Le script texte→toolcalls les parse en toolcalls natifs
    (format OpenAI {function:{name, arguments}}) pour que la boucle les
    exécute réellement — sinon l'agent « décrit » le travail sans jamais agir.

    Retourne [] si aucun toolcall exploitable n'est trouvé.
    """
    import json as _j
    import re as _re
    if not content:
        return []
    out = []
    # Forme 1 : blocs JSON `{"name": "...", "parameters": {...}}` souvent
    # produits par les LLM « descriptifs » (liste d'appels planifiés).
    for m in _re.finditer(r'\{"name"\s*:\s*"([A-Za-z0-9_>]+)"\s*,\s*"parameters"\s*:\s*(\{.*?\})\s*\}', content, _re.DOTALL):
        try:
            params = _j.loads(m.group(2))
        except Exception:
            continue
        fn = m.group(1)
        out.append({"id": f"texttc_{len(out)}", "type": "function",
                    "function": {"name": fn, "arguments": _j.dumps(params, default=str)}})
        if len(out) >= 6:
            break
    if out:
        return out
    # Forme 2 : `{"tool": "write_file_v1", "args": {...}}` ou `{...}` avec clé
    # `name`/`function.name` simple.
    for m in _re.finditer(r'\{"tool"\s*:\s*"([A-Za-z0-9_>]+)"\s*,\s*"args"\s*:\s*(\{.*?\})\s*\}', content, _re.DOTALL):
        try:
            args = _j.loads(m.group(2))
        except Exception:
            continue
        out.append({"id": f"texttc_{len(out)}", "type": "function",
                    "function": {"name": m.group(1), "arguments": _j.dumps(args, default=str)}})
        if len(out) >= 6:
            break
    return out


def _try_repick_next_task(messages: List[Dict], _fsm_log, agent_id: str,
                          cat, p_ref: str, m_ref: str, skill_home: str) -> bool:
    """Re-pioche directement la tâche suivante pour un greedy « occupation
    continue » qui vient de clôturer une tâche.

    Lit le workspace_id + role_required de l'agent (variables_json), appelle
    workspace/task_claim_next_v1 et, si une tâche est dispo, injecte le résultat
    dans messages (comme si le LLM l'avait demandé) pour que la boucle continue
    dessus. Retourne True si une tâche a été piochée, False sinon.

    Évite de dépendre du LLM (qui a tendance à répondre « tâche terminée » en
    texte) : la re-pioche est décisionnelle et immédiate.
    """
    try:
        from services.skill_manager import call_skill
        import json as _json

        # Récupérer workspace_id + role_required depuis les variables de l'agent.
        ws_id = ""
        role_req = ""
        try:
            from modules.sql.db import AgentsDB
            db = AgentsDB()
            row = db.conn.execute(
                "SELECT variables_json FROM agents WHERE agent_id = ?",
                (agent_id,)).fetchone()
            if row:
                vars_j = _json.loads(row["variables_json"] or "{}")
                ws_id = vars_j.get("workspace_id", "") or ""
                role_req = vars_j.get("role_required", "") or ""
            db.close()
        except Exception:
            pass
        if not ws_id:
            # Fallback : le workspace du run (injecté dans le wait_for/skill).
            ws_id = "mw-dev-chat"

        result = None
        for cand in _resolve_skill_candidates("task_claim_next_v1"):
            try:
                result = call_skill(cand, {
                    "workspace_id": ws_id,
                    "role_required": role_req,
                    "team_id": -1,
                    "agent_id": agent_id or "",
                }, home=skill_home)
                if isinstance(result, dict) and not result.get("error", "").startswith("skill"):
                    break
            except Exception:
                result = None
        if not isinstance(result, dict) or result.get("ok") is not True:
            return False

        task = result.get("task") or {}
        # Injecte le résultat dans l'historique (message tool) pour que le LLM
        # voie la tâche piochée et travaille dessus au round suivant.
        asst_entry = {"role": "assistant", "content": "",
                      "tool_calls": [{"id": "repick", "type": "function",
                                      "function": {"name": "task_claim_next_v1",
                                                   "arguments": _json.dumps(
                                                       {"workspace_id": ws_id,
                                                        "role_required": role_req})}}]}
        messages.append(asst_entry)
        messages.append({"role": "tool", "tool_call_id": "repick",
                         "name": "task_claim_next_v1",
                         "content": _json.dumps(result, default=str)})
        return True
    except Exception:
        return False


def _count_match(fn_name: str, counts: Dict[str, int], thresholds: Dict[str, int]) -> Optional[str]:
    """Vérifie si un tool a atteint son seuil de comptage."""
    for pattern, limit in thresholds.items():
        actual = 0
        if pattern.endswith(".*"):
            prefix = pattern[:-1]
            for k, v in counts.items():
                if k.startswith(prefix):
                    actual += v
        else:
            actual = counts.get(pattern, 0)
        if actual >= limit:
            return f"count_{pattern}:{actual}"
    return None


def _stream_llm_round(bridge: Any, p_ref: str, m_ref: str, messages: List[Dict],
                      tools: List[Dict], agent_id: str,
                      on_event: Optional[Callable[[str, str], None]],
                      temperature: float = 0.7,
                      max_tokens: Optional[int] = None) -> Any:
    """Tour LLM en streaming : agrège thinking/content/tool_calls depuis
    bridge.chat_stream_events et diffuse chaque delta via on_event(kind, text).

    Retourne un objet avec .content, .tool_calls, .finish_reason, .usage
    (équivalent ChatResponse) construit depuis les événements de flux.
    """
    import types as _types
    from modules.llm_manager.base_bridge import BridgeError

    provider = str(agent_id) if agent_id else ""
    try:
        chunks = bridge.chat_stream_events(
            provider_ref=p_ref, model_ref=m_ref, messages=messages,
            temperature=temperature, max_tokens=max_tokens,
            tools=tools if tools else None, agent_id=provider or None)
    except AttributeError:
        # Bridge sans chat_stream_events (litellm…) : repli sur chat() non-stream.
        if on_event:
            on_event("info", "bridge sans streaming — repli synchrone")
        resp = bridge.chat(p_ref, m_ref, messages, tools=tools or None,
                           temperature=temperature, max_tokens=max_tokens,
                           agent_id=provider or None)
        return resp

    content = ""
    tool_acc: Dict[int, Dict] = {}
    finish = "stop"
    try:
        for ev in chunks:
            et = ev.get("type")
            delta = ev.get("delta", "") or ""
            if et == "thinking":
                if on_event and delta:
                    on_event("thinking", delta)
            elif et == "content":
                if delta:
                    content += delta
                    if on_event:
                        on_event("content", delta)
            elif et == "tool_calls":
                tc = ev.get("delta") or {}
                idx = tc.get("index", 0)
                acc = tool_acc.setdefault(idx, {"id": "", "type": "function",
                                                "function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    acc["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    acc["function"]["name"] += fn["name"]
                if fn.get("arguments"):
                    acc["function"]["arguments"] += fn["arguments"]
            elif et == "finish":
                finish = ev.get("reason") or finish
    except BridgeError:
        raise
    except Exception as exc:
        err = BridgeError("unknown", f"stream {p_ref}/{m_ref}: {exc}", p_ref, m_ref)
        err.__cause__ = exc
        raise err

    tool_calls = None
    if tool_acc:
        tool_calls = [tool_acc[i] for i in sorted(tool_acc)]
    resp = _types.SimpleNamespace(
        content=content, model=m_ref, finish_reason=finish, usage={},
        raw=None, tool_calls=tool_calls)
    return resp


def _chat_with_tools(request: str, context: str, tools: List[Dict],
                     max_loops: int = 30, grouping: str = "none",
                     break_on_signals: Optional[List[str]] = None,
                     break_on_counts: Optional[Dict[str, int]] = None,
                     llm_timeout: Optional[float] = None,
                     global_timeout: Optional[float] = None,
                     provider_ref: str = "", model_ref: str = "",
                     skill_home: str = "/tmp",
                     on_event: Optional[Callable[[str, str], None]] = None,
                     stream_events: bool = False,
                     bridge: Any = None,
                     role_type: str = "",
                     cat: Any = None) -> List[Dict]:
    import time as _time
    from pathlib import Path as _Path
    from modules.sql.db import CatalogueDB
    _cat = CatalogueDB()
    # Logger FSM attaché au run parent (même fichier log/fsm_*.log du home) :
    # trace les appels LLM et tool calls pour le debugging du swarm.
    _fsm_log = None
    try:
        from AgentFrameWork.fsm_logger import FSMLogger
        _fsm_log = FSMLogger.attach(skill_home)
    except Exception:
        _fsm_log = None
    if _fsm_log is not None:
        _fsm_log.log("info", "fsm/start",
                     f"autonomous request={request[:80]}")
    # Bridge via LLMManager (façade, DirectBridge par défaut) avec KeyManager
    # (clés API en BDD) : sans lui, les providers fallback (google…) ne
    # trouvent jamais leur clé → échec de bascule.
    _km = None
    try:
        from modules.key_manager.key_manager import KeyManager
        from modules.sql.db import ModelWeaverDB
        _km = KeyManager(ModelWeaverDB())
    except Exception:
        pass
    from services.skill_manager import call_skill

    # Initialiser un shell pour le home (nécessaire pour les skills shell/exec)
    from services.agent_shell_manager import agent_shell_manager
    agent_shell_manager.init()
    _agent_id = _Path(skill_home).name
    _role_type = ""
    if agent_shell_manager.get(_agent_id) is None:
        # Le pilote de dev-chat (rôle 'chat') a les autorités MANAGER : rôle
        # leader + accès en lecture aux homes des AUTRES agents (pour analyser
        # l'activité de sa team). Les membres greedy gardent le rôle member.
        _shell_role = "member"
        _allowed_roots = None
        try:
            _cat = CatalogueDB()
            _arow = _cat.conn.execute(
                "SELECT role_type FROM agents WHERE agent_id = ?", (int(_agent_id),)
            ).fetchone()
            if _arow:
                _role_type = _arow["role_type"] or ""
            if _arow and _arow["role_type"] == "chat":
                from services._common import mw_home as _mwh
                _allowed_roots = [(_mwh() / "agent_home").resolve()]
                _shell_role = "leader"
        except Exception:
            pass
        agent_shell_manager.get_or_create(agent_id=_agent_id,
                                          home_root=_Path(skill_home),
                                          role=_shell_role,
                                          allowed_roots=_allowed_roots)

    # agent_id du home (agent_home/{agent_id}/…) — injecté dans chaque tool
    # call : les skills git/workspace en ont besoin (git_clone project_id+agent_id)
    # et le LLM ne le connaît pas.
    _aid_from_home = ""
    parts = _Path(skill_home).parts
    if "agent_home" in parts:
        _aid_from_home = str(parts[parts.index("agent_home") + 1])

    # LLMManager pour assignation et fallback (optionnel — si DB lockée, ignore)
    _llm_mgr = None
    try:
        from modules.llm_manager.llm_manager import LLMManager
        _llm_mgr = LLMManager(_cat, km=_km)
    except Exception:
        pass

    # Bridge actif via la façade (DirectBridge par défaut)
    try:
        if _llm_mgr is not None:
            bridge = _llm_mgr.get_bridge()
        else:
            from modules.llm_manager.direct_bridge import DirectBridge
            bridge = DirectBridge(cat=_cat, km=_km)
    except Exception:
        from modules.llm_manager.direct_bridge import DirectBridge
        bridge = DirectBridge(cat=_cat, km=_km)

    grouping_hints = {
        "req-optimal": ("Jusqu'à 10 outils indépendants par réponse."),
        "tok-optimal": ("Un seul outil par réponse. Analyse avant chaque appel."),
    }
    hint = grouping_hints.get(grouping, "")
    role = "Tu exécutes les tâches UNIQUEMENT via les outils. Ne génère JAMAIS de code dans ta réponse. Appelle directement l'outil shell_exec_v1 pour écrire les fichiers."
    if context:
        role = f"Tu exécutes les tâches UNIQUEMENT via les outils. Contexte : {context}"
    # Aide des outils : si le repo central est cloné, le fichier tools_help.md
    # décrit les outils dispo et l'ordre d'usage (repo_list → repo_init →
    # git_clone → …). Sinon, rappel des bases.
    tools_hint = ("Après git_clone_v1, lis tools_help.md à la racine du clone "
                  "pour connaître les outils disponibles et leur ordre d'usage "
                  "(repo_list_v1, repo_init_v1, git_clone_v1, …).")
    role = f"{role} {tools_hint}"
    system_msg = f"{role} {hint}" if hint else role
    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": request},
    ]
    signals = []
    break_list = break_on_signals or []
    count_thresholds = break_on_counts or {}
    tool_counts: Dict[str, int] = {}
    consecutive_failures = 0
    successful_tools = 0  # garde-fou : au moins un outil doit réussir
    loop_start = _time.time()

    # Auto-assigner un LLM si non spécifié (une fois pour toute la boucle)
    p_ref = provider_ref or ""
    m_ref = model_ref or ""
    if not p_ref:
        try:
            llm = _llm_mgr.assign_llm(use_case="coding", agent_id=_aid_from_home or None)
            if llm:
                p_ref = llm.get("provider_ref", "")
                m_ref = llm.get("model_ref", "")
        except Exception:
            pass

    excluded_providers: set = set()
    excluded_models: set = set()
    _original_retried = False  # re-try du provider original limité à 1 fois/membre
    # PIN du provider demandé (menus déroulants) : si l'utilisateur a choisi
    # un provider explicite (ex. opencode-zen), une erreur TEMPORAIRE
    # (upstream/timeout/rate-limit passager) ne doit pas le faire fuir vers le
    # pool de fallback (souvent des modèles morts sans crédit). On re-tente le
    # provider original jusqu'à PIN_RETRIES échecs consécutifs avant de basculer.
    _pin_provider = provider_ref or ""
    _pin_model = model_ref or ""
    _pin_fail_streak = 0
    _PIN_RETRIES = 3
    # Tentatives max de fallback LLM AVANT d'abandonner le run (quand rien n'a
    # été produit). Élevé : un problème LLM (rate-limit, pool saturé) ne doit
    # pas mettre fin au run — le fallback re-tente le pool rafraîchi (les
    # cooldowns RPM expirent). Un greedy qui a déjà produit des outils termine
    # en succès bien avant ce seuil.
    LLM_FALLBACK_MAX_TRIES = 30
    # Compteurs séparés : tours d'outils réels vs tours d'échec/fallback LLM.
    # Un tour d'échec = l'appel LLM a raté (rate-limit, modèle mort, fallback)
    # sans exécuter d'outil. max_loops borne les DEUX ; on trace séparément
    # pour diagnostiquer et pour peupler model_call_log avec les vrais essais.
    _tool_rounds = 0
    _llm_fail_rounds = 0
    # Nombre de fois où le LLM a répondu en texte sans outils (no_action).
    # On re-sollicite UNE fois en rappelant d'utiliser les outils (certains
    # modèles de fallback répondent en texte au 1er tour), puis échec.
    _no_action_retries = 0
    # Tâches clôturées dans CE run (task_done_v1 réussi). Les greedy
    # « occupation continue » doivent ENCHAÎNER : après un task_done, ils
    # re-piochent une autre tâche au lieu de conclure. On ne les endort
    # que si task_claim_next répond « aucune tâche dispo ».
    _tasks_done_in_run = 0
    # Force une re-pioche après chaque task_done réussi : le LLM a tendance à
    # répondre en texte (« tâche terminée ») au lieu de rappeler task_claim.
    # On injecte une consigne de re-pioche tant qu'il reste du travail.
    _greedy_repick_pending = False
    # Compteurs anti « liseur sans conclusion » : un membre qui lit beaucoup
    # (read_file/list_dir/glob…) sans jamais produire de livrable (write_file,
    # git_commit, task_done…) tourne jusqu'à max_loops sans rien livrer.
    # Après quelques tours de lecture pure, on injecte UN rappel système qui
    # force la synthèse/écriture — sinon les agents « analysent » à l'infini.
    _conclusion_pushed = False
    _write_tools_ok = 0
    _READ_TOOLS = ("read_file", "list_dir", "glob", "grep", "search",
                   "task_list", "task_get", "chat_recent", "get_env",
                   "workspace_task_list", "workspace_task_get", "list_files",
                   "exec")
    _WRITE_TOOLS = ("write_file", "git_commit", "git_push", "task_done",
                    "workspace_task_done", "git_lite", "memory_write",
                    "workspace_task_create", "task_create")
    # Échecs LLM consécutifs SANS outil réussi : si le fallback enchaîne les
    # modèles morts (chacun "réussi" par assign_llm mais échoue au vrai appel),
    # on doit abandonner vite — sinon coder-c/tester-a font 99 tours de
    # fallback mort après avoir déjà (ou pas) fini leur travail.
    _consec_llm_fails = 0

    # Suivi du modèle actif pour le chat : on émet un événement 'llm' à chaque
    # CHANGEMENT (fallback ou retour au modèle voulu). Le GUI affiche la ligne
    # « fall-back <provider>/<model> » et le modèle actuellement branché.
    _emitted_llm_key = ""
    _wanted_llm_key = f"{provider_ref}/{model_ref}" if (provider_ref and model_ref) else ""

    def _emit_llm_event(tag: str, prov: str, mod: str) -> None:
        nonlocal _emitted_llm_key
        try:
            key = f"{prov}/{mod}"
            if key == _emitted_llm_key:
                return
            # Retour au modèle voulu (menus déroulants) après un fallback.
            if _emitted_llm_key and _wanted_llm_key and key == _wanted_llm_key:
                tag = "retour"
            if on_event:
                on_event("llm", f"{tag} {key}")
            _emitted_llm_key = key
        except Exception:
            pass

    _emit_llm_event("branché", p_ref, m_ref)

    for _round in range(max_loops):
        if global_timeout is not None and (_time.time() - loop_start) >= global_timeout:
            signals.append({"signal": "global_timeout", "stdout": f"Limite de {global_timeout}s atteinte", "exit_code": 124})
            return signals

        try:
            if _fsm_log is not None:
                _fsm_log.log("debug", "llm/call",
                             f"provider={p_ref} model={m_ref} "
                             f"round={_round} tools={len(tools)}")
            # Marque le modèle actif (fallback ou retour) dans le chat.
            _emit_llm_event("branché", p_ref, m_ref)
            if stream_events:
                response = _stream_llm_round(
                    bridge, p_ref, m_ref, messages, tools, _aid_from_home,
                    on_event, temperature=0.7)
            else:
                response = bridge.chat(p_ref, m_ref, messages, tools=tools, temperature=0.7,
                                       agent_id=_aid_from_home or None)
            if _fsm_log is not None:
                _fsm_log.log("debug", "llm/ok",
                             f"provider={p_ref} model={m_ref} "
                             f"tools={len(getattr(response, 'tool_calls', None) or [])}")
            # Le LLM a répondu (fallback réussi) : reset des échecs consécutifs —
            # le pool re-fonctionne, on ne doit pas abandonner le run sur un
            # compteur d'échecs anciens.
            _consec_llm_fails = 0
            # Journal de conversation complet (réponses + tool calls) pour
            # l'analyse en profondeur (boucles, qualité). Rotation 10 Mo.
            try:
                from AgentsCatalogue.lib.llm_conversation_log import log_llm_exchange
                log_llm_exchange(skill_home, p_ref, m_ref, _round, response,
                                 ok=True, messages=messages)
            except Exception:
                pass
        except Exception as e:
            _llm_fail_rounds += 1
            _consec_llm_fails += 1
            err_str = str(e)[:500]
            # Diffuser l'erreur dans le flux (affichage chat) : l'utilisateur
            # voit POURQUOI le modèle a échoué avant de voir le fall-back.
            if on_event:
                try:
                    _cat = getattr(getattr(e, "category", None), "value", "")
                    on_event("llm", f"err {p_ref}/{m_ref} {_cat} {err_str[:500]}")
                except Exception:
                    pass
            # Journal de conversation : tracer l'erreur aussi.
            try:
                from AgentsCatalogue.lib.llm_conversation_log import log_llm_exchange
                log_llm_exchange(skill_home, p_ref, m_ref, _round, ok=False,
                                 error=err_str, messages=messages)
            except Exception:
                pass
            if _fsm_log is not None:
                _fsm_log.log("warn", "llm/error",
                             f"provider={p_ref} model={m_ref} err={err_str[:200]}")
            # NE PAS ajouter un message tool orphelin (_api_error) à l'historique :
            # litellm rejette "Missing corresponding tool call for tool response
            # message" au tour suivant, ce qui casse le fallback provider.
            signals.append({"signal": "tool_finish", "tool": "_api_error",
                            "stdout": "", "stderr": err_str, "exit_code": 1})
            # Seuil d'échecs LLM consécutifs SANS outil réussi : si le pool de
            # fallback est saturé de modèles morts (chacun échoue au vrai
            # appel), on abandonne au lieu de faire des centaines de tours.
            # ÉLEVÉ (30) : un problème LLM ne met pas fin au run tout de suite —
            # le fallback continue de tenter le pool rafraîchi (les cooldowns
            # RPM des modèles en repos expirent). Seul un agent qui n'a RIEN
            # produit après 30 tentatives abandonne.
            if _consec_llm_fails >= LLM_FALLBACK_MAX_TRIES:
                if successful_tools > 0:
                    signals.append({"signal": "loop_end",
                                    "stdout": (f"Travail effectué ({successful_tools} outils réussis) "
                                               f"mais pool LLM saturé: {err_str}"),
                                    "exit_code": 0})
                else:
                    signals.append({"signal": "llm_timeout",
                                    "stdout": f"pool LLM saturé après {_consec_llm_fails} échecs: {err_str}",
                                    "exit_code": 124})
                return signals
            # Fallback : en cas d'erreur API (rate_limit notamment), on bascule
            # sur un autre provider même si un provider explicite était fixé
            # (ex. quota gemini épuisé → groq). Le provider fautif est exclu.
            err_low = err_str.lower()
            # Catégorie BridgeError (auth/rate_limit/context/timeout/server/
            # unknown) : un MODÈLE MORT (deprecated, 404, clé invalide,
            # crédit insuffisant, erreur inconnue) ne redeviendra jamais bon →
            # on DOIT basculer sur un autre modèle. Seules les erreurs de
            # contexte (request too large) ne se résolvent pas par le fallback
            # si tous les modèles ont la même petite fenêtre.
            _err_cat = getattr(e, "category", None)
            _err_cat_val = getattr(_err_cat, "value", "") if _err_cat is not None else ""
            is_retryable = ("rate_limit" in err_low or "rate limit" in err_low
                            or "budget" in err_low or "quota" in err_low
                            or "429" in err_str or "404" in err_str
                            or "timeout" in err_low or "timed out" in err_low
                            or "deprecated" in err_low
                            or "not found" in err_low
                            or "insufficient" in err_low
                            or "credit" in err_low
                            or _err_cat_val in ("auth", "unknown")
                            # Gemini : functionCall rejoué sans thoughtSignature →
                            # erreur 400 côté API. Un retry sur un autre provider
                            # (ou une réponse régénérée) la résout.
                            or "thought_signature" in err_low
                            or "thoughtsignature" in err_low)
            # Type de limite (BridgeError enrichie) : un quota épuisé ne se
            # re-tente PAS sur place — on bascule immédiatement.
            limit_type = getattr(e, "limit_type", None)
            is_quota = (limit_type in ("quota", "daily_quota")
                        or "per-day" in err_low or "per day" in err_low
                        or ("quota" in err_low and "rate limit" not in err_low))

            # ── PIN du provider demandé ──
            # Si un provider explicite est fixé (menus déroulants) et que l'on
            # est dessus, une erreur passagère (upstream/timeout/rate-limit
            # transitoire) ne justifie pas de fuir vers le pool de fallback
            # (souvent des modèles morts sans crédit — openai no credits,
            # huggingface Not Found…). On re-tente le provider piné jusqu'à
            # PIN_RETRIES échecs consécutifs, puis on bascule.
            if (_pin_provider and p_ref == _pin_provider
                    and m_ref == _pin_model
                    and _pin_fail_streak < _PIN_RETRIES
                    and not is_quota):
                _pin_fail_streak += 1
                if _fsm_log is not None:
                    _fsm_log.log("warn", "llm/pin_retry",
                                 f"provider piné {p_ref}/{m_ref} échec "
                                 f"{_pin_fail_streak}/{_PIN_RETRIES}: {err_str[:150]}")
                if _fsm_log is not None and _pin_fail_streak > 1:
                    pass
                _time.sleep(3)
                continue  # re-tente le provider piné (même p_ref/m_ref)

            # Backoff : un seul retry court (1s) avant de basculer. On ne veut
            # PAS s'attarder sur un modèle rate-limité (les retries 0/5/5s
            # faisaient perdre 10s × 50 tours = boucles interminables). On
            # échoue vite et on passe au modèle suivant via assign_llm.
            if is_retryable and not is_quota and "rate limit" in err_low:
                _time.sleep(1)
                try:
                    response = bridge.chat(p_ref, m_ref, messages, tools=tools, temperature=0.7,
                                           agent_id=_aid_from_home or None)
                    continue  # réponse obtenue, on traite les tools
                except Exception:
                    pass  # échoue vite → fallback provider
            if is_retryable and p_ref not in excluded_providers:
                # Rate-limit = PAR MODÈLE (RPM) : on exclut le modèle, pas le
                # provider — un provider a souvent plusieurs modèles fiables
                # (ex. google/gemini-3.5-flash-lite rate-limité → essayer
                # google/gemini-3.5-flash, google/gemini-2.5-flash…). On ne
                # blackliste le provider QUE si c'est un problème provider-wide
                # (auth/clé invalide, quota org épuisé).
                err_cat = getattr(e, "category", None)
                limit_type = getattr(e, "limit_type", None)
                err_cat_val = getattr(err_cat, "value", "") if err_cat is not None else ""
                provider_wide = (err_cat_val == "auth"
                                 or limit_type in ("quota", "daily_quota"))
                excluded_models.add(m_ref)
                if provider_wide:
                    excluded_providers.add(p_ref)

                # ── REVENIR au provider PINÉ ──
                # Si on est tombé sur un modèle du POOL (non pinné) qui échoue
                # (ex. openai sans crédit), on ne doit PAS enchaîner les modèles
                # morts du pool : on RETOURNE au provider demandé (opencode-zen).
                # Le pin garde ses échecs cumulés pour ne pas re-tenter à l'infini
                # si opencode-zen est vraiment down.
                if (_pin_provider and p_ref != _pin_provider
                        and _pin_provider not in excluded_providers
                        and _pin_fail_streak < _PIN_RETRIES):
                    if _fsm_log is not None:
                        _fsm_log.log("warn", "llm/pin_return",
                                     f"pool en échec, retour au provider piné "
                                     f"{_pin_provider}/{_pin_model}")
                    p_ref, m_ref = _pin_provider, _pin_model
                    _time.sleep(2)
                    continue  # re-tente le provider pinné

                # Demander un autre LLM au gestionnaire (assign_llm) : c'est la
                # seule source de vérité (respecte noretryuntil/unavailable et
                # exclut les providers/modèles déjà essayés). Pas de liste
                # hardcodée — ça multiplierait les appels et court-circuiterait
                # le LLMManager.
                switched = False
                if _llm_mgr:
                    # Taille approximative de l'historique (chars→tokens ~ /4) :
                    # le fallback ne doit pas choisir un modèle dont la fenêtre
                    # est trop petite (ex. groq/llama-3.1-8b = 8k) sinon on
                    # repart en "request too large" en boucle.
                    try:
                        _hist_tokens = sum(len(str(m.get("content", ""))) for m in messages) // 4
                    except Exception:
                        _hist_tokens = 0
                    for _try in range(6):
                        try:
                            llm = _llm_mgr.assign_llm(use_case="coding",
                                                      exclude_providers=list(excluded_providers),
                                                      exclude_models=list(excluded_models),
                                                      min_window=_hist_tokens,
                                                      agent_id=_aid_from_home or None)
                            if not llm:
                                break
                            np_ = llm.get("provider_ref", "")
                            nm_ = llm.get("model_ref", "")
                            if not np_ or np_ in excluded_providers:
                                if np_:
                                    excluded_providers.add(np_)
                                continue
                            # Pas de probe : on bascule directement, le tour
                            # suivant fera le vrai appel. S'il échoue (modèle
                            # mort), _mark_call_failed le met en repos et le
                            # fallback re-basculera — sans requête gaspillée.
                            p_ref, m_ref = np_, nm_
                            _emit_llm_event("fall-back", p_ref, m_ref)
                            switched = True
                            break
                        except Exception:
                            break
                if switched:
                    continue
                # Le pool de fallback a été épuisé à CET instant (tous les
                # modèles proposés ont échoué). Un problème LLM ne doit PAS
                # mettre fin au run : on exclut le provider fautif, on attend un
                # court délai (les cooldowns RPM des modèles en repos expirent
                # vite, ex. 5-10 min → on re-tente le pool rafraîchi) et on
                # repart pour un nouveau round de fallback. On ne termine que
                # si le pool reste saturé très longtemps SANS qu'aucun outil
                # n'ait réussi (l'agent n'a de toute façon rien produit).
                if _consec_llm_fails < LLM_FALLBACK_MAX_TRIES:
                    _time.sleep(min(20.0, 3.0 * _consec_llm_fails))
                    continue
            # Travail déjà effectué : si le membre a exécuté des outils avec
            # succès (ex. write_file + commit + push OK) et que seul l'appel
            # LLM de CONFIRMATION finale échoue (pool saturé), on considère le
            # travail comme fait — sinon coder-c refait 99 tours de fallback
            # mort après avoir déjà poussé son fichier.
            if successful_tools > 0:
                signals.append({"signal": "loop_end",
                                "stdout": (f"Travail effectué ({successful_tools} outils réussis) "
                                           f"mais confirmation LLM impossible: {err_str}"),
                                "exit_code": 0})
                return signals
            signals.append({"signal": "llm_timeout", "stdout": f"LLM error ({p_ref}/{m_ref}): {err_str}", "exit_code": 124})
            return signals

        tool_calls = getattr(response, "tool_calls", None)
        content = getattr(response, "content", "") or ""

        if not tool_calls and content:
            # Garde-fou : répondre en texte sans avoir exécuté AUCUN outil
            # (même en échec) = l'agent décrit au lieu d'agir → pas un succès.
            if successful_tools == 0:
                # Script texte→toolcalls : certains LLM (ex. poolside) répondent
                # en texte avec les toolcalls SÉRIALISÉS dans le contenu au lieu
                # d'appeler le mécanisme natif. On tente de les extraire
                # ({"name": "...", "parameters": {...}} ou {"tool": ...}) et de
                # les exécuter — sinon l'agent « décrit » sans jamais agir.
                parsed_calls = _extract_toolcalls_from_text(content)
                if parsed_calls:
                    tool_calls = parsed_calls
                    if _fsm_log is not None:
                        _fsm_log.log("warn", "tool/text_to_toolcalls",
                                     f"{len(parsed_calls)} toolcalls extraits du texte")
                    # On laisse le flux normal construire asst_msg AVEC les
                    # tool_calls extraits (tc_list) — pas de message séparé.
                elif _no_action_retries < 1 and not _greedy_repick_pending:
                    _no_action_retries += 1
                    if _fsm_log is not None:
                        _fsm_log.log("warn", "tool/no_action_retry",
                                     "LLM a répondu en texte, re-sollicitation")
                    messages.append({"role": "system",
                                     "content": ("Tu dois EXÉCUTER la tâche via les outils "
                                                 "(git_clone_v1, write_file_v1, git_commit_v1, "
                                                 "git_push_v1). Ne réponds pas en texte : appelle "
                                                 "directement un outil pour faire le travail.")})
                    continue
                else:
                    # Greedy « occupation continue » : une tâche vient d'être
                    # clôturée → on re-pioche directement une autre tâche du
                    # même rôle au lieu de conclure. Seule l'absence de tâche
                    # (task_claim_next → « aucune tâche dispo ») endort l'agent.
                    if _greedy_repick_pending:
                        _greedy_repick_pending = False
                        _repick = _try_repick_next_task(
                            messages, _fsm_log, _aid_from_home or "",
                            _cat, p_ref, m_ref, skill_home)
                        if _repick:
                            if _fsm_log is not None:
                                _fsm_log.log("warn", "tool/greedy_repick",
                                             "tâche clôturée — re-pioche d'une nouvelle tâche")
                            continue
                        # Plus aucune tâche dispo → l'agent s'endort proprement.
                        if _fsm_log is not None:
                            _fsm_log.log("info", "tool/greedy_idle",
                                         "tâche clôturée et plus rien à piocher — agent endormi")
                        signals.append({"signal": "loop_end",
                                        "stdout": "toutes les tâches dispo traitées — agent endormi",
                                        "exit_code": 0})
                        return signals
                    # Faux appel : réponse texte sans tool call ni action.
                    # On le trace pour pénaliser le modèle au scoring.
                    try:
                        _log_no_tools_call(_cat, p_ref, m_ref, _aid_from_home or "")
                    except Exception:
                        pass
                    signals.append({"signal": "no_action",
                                    "stdout": "Réponse texte sans aucun outil exécuté — rien n'a été fait",
                                    "exit_code": 1})
                    return signals
            signals.append({"signal": "loop_end", "stdout": content, "exit_code": 0})
            return signals
        if not tool_calls and not content:
            signals.append({"signal": "loop_end", "stdout": "réponse vide", "exit_code": 0})
            return signals

        asst_msg = {"role": "assistant", "content": content or ""}
        tc_list = []
        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            tc_entry = {"id": tc.get("id"), "type": "function", "function": {"name": fn_name, "arguments": tc["function"]["arguments"]}}
            # Gemini exige la thoughtSignature quand on rejoue les functionCall.
            if tc.get("thoughtSignature"):
                tc_entry["thoughtSignature"] = tc["thoughtSignature"]
            tc_list.append(tc_entry)

            tool_counts[fn_name] = tool_counts.get(fn_name, 0) + 1
            call_signal = {"signal": "tool_call", "tool": fn_name, "args": tc["function"]["arguments"], "count": tool_counts[fn_name]}
            signals.append(call_signal)

            if _signal_match(f"tool_call.{fn_name}", break_list):
                return signals
            count_reason = _count_match(fn_name, tool_counts, count_thresholds)
            if count_reason:
                signals.append({"signal": count_reason, "stdout": f"Seuil atteint pour {fn_name}", "exit_code": 0})
                return signals

        if tc_list:
            asst_msg["tool_calls"] = tc_list
        _tool_rounds += 1 if tc_list else 0
        messages.append(asst_msg)

        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            try:
                raw_args = json.loads(tc["function"]["arguments"])
            except Exception:
                raw_args = {}

            if _fsm_log is not None:
                _fsm_log.log("debug", "tool/call",
                             f"name={fn_name} args={raw_args}")
            if on_event:
                try:
                    on_event("tool", f"call {fn_name} {json.dumps(raw_args, default=str)[:200]}")
                except Exception:
                    pass

            # Résolution du nom de skill : plusieurs candidats (le nom peut
            # être tronqué par le LLM, ex. git_clone_v1 pour git/git_clone@v1).
            # L'agent_id du home est injecté (le LLM ne le connaît pas, les
            # skills git/workspace le requièrent).
            tool_result = None
            _injected = dict(raw_args)

            # ── GATE d'accomplissement avant task_done ──
            # Un agent ne peut clôturer une tâche sans avoir exécuté AU MOINS
            # un outil réussi dans la session (tout le workflow passe par des
            # tool_calls). Pour les codeurs/mergers, on exige en plus du
            # travail git (le commit auto de sortie le prouvera). Le gate
            # REFUSE le task_done en renvoyant une erreur au LLM — il doit
            # réellement agir (ou obtenir l'exception via le skill dédié).
            if fn_name in ("task_done_v1", "workspace_task_done_v1"):
                if successful_tools == 0:
                    _reject = {
                        "ok": False,
                        "error": ("task_done refusé : aucun outil réussi dans "
                                  "cette session. Tu dois AGIR (écrire un "
                                  "fichier, faire un git_diff/commit, exécuter "
                                  "un test) avant de clôturer. Marquer une tâche "
                                  "done sans avoir produit de travail est "
                                  "interdit."),
                        "exit_code": 1,
                    }
                    tool_result = _reject
                    if _fsm_log is not None:
                        _fsm_log.log("warn", "task_done/rejected",
                                     "task_done refusé (0 outil réussi)")
                    if on_event:
                        try:
                            on_event("tool", "err task_done_v1 refusé : 0 outil réussi")
                        except Exception:
                            pass
                else:
                    # Rôles à impératif de code : la vérification git se fait à
                    # la sortie de boucle (auto_git_sync post). On note ici
                    # qu'une vérification agentic peut être nécessaire si le
                    # codeur n'a RIEN produit dans le repo.
                    _injected["delivered"] = (_write_tools_ok > 0)
            # L'agent_id du home est TOUJOURS forcé (le LLM l'invente souvent,
            # ex. "agent_388" ou un nom de membre) — les skills git/workspace
            # s'en servent pour le home et le clone.
            if _aid_from_home:
                _injected["agent_id"] = _aid_from_home
            candidates = _resolve_skill_candidates(fn_name)
            for cand in candidates:
                try:
                    tool_result = call_skill(cand, _injected, home=skill_home)
                    if isinstance(tool_result, dict) and not tool_result.get("error", "").startswith("skill"):
                        break
                except Exception:
                    tool_result = None
            if tool_result is None:
                tool_result = {"ok": False, "error": f"skill '{fn_name}' introuvable"}

            # Anti-boucle : trop d'échecs consécutifs → on arrête avec le
            # dernier résultat au lieu de tourner jusqu'à max_loops (le LLM
            # peut re-tenter un outil en échec en boucle, ex. docker sans clone).
            failed = (isinstance(tool_result, dict)
                      and (tool_result.get("ok") is False
                           or tool_result.get("status") in ("error", "failed")
                           or tool_result.get("exit_code") not in (None, 0)))
            consecutive_failures = consecutive_failures + 1 if failed else 0
            # Diffusion du résultat de l'outil dans le flux (affichage chat).
            if on_event:
                try:
                    _out = _err = ""
                    if isinstance(tool_result, dict):
                        _out = tool_result.get("stdout") or ""
                        _err = tool_result.get("error") or tool_result.get("stderr") or ""
                    _status = "ok" if not failed else "err"
                    on_event("tool", f"{_status} {fn_name} {str(_out or _err)[:200]}")
                except Exception:
                    pass
            # PAS DE TÂCHE À PIOCHER : au lieu de re-boucler le LLM sans fin
            # (gaspi massif — les greedy « occupation continue » faisaient des
            # milliers de requêtes/h), l'agent s'ENDORT via wait_for et termine
            # proprement. Le waker le réveillera quand une tâche dispo arrive.
            if (not failed and fn_name in ("task_claim_next_v1", "workspace_task_claim_next_v1")
                    and "aucune tâche" in str(tool_result.get("error") or "").lower()):
                try:
                    from services.skill_manager import call_skill
                    role_required = ""
                    try:
                        ra = json.loads(tc["function"].get("arguments", "{}"))
                        role_required = ra.get("role_required", "") or ""
                    except Exception:
                        pass
                    call_skill("workspace/wait_for@v1", {
                        "workspace_id": "mw-dev-chat", "type": "task_for_role",
                        "role": role_required, "team_id": -1,
                        "agent_id": _aid_from_home or ""}, home=skill_home)
                except Exception:
                    pass
                signals.append({"signal": "loop_end",
                                "stdout": "aucune tâche dispo — agent endormi (wait_for)",
                                "exit_code": 0})
                return signals
            if not failed:
                successful_tools += 1
                # Un outil réussi = le membre avance → reset les échecs LLM
                # consécutifs (le pool n'est plus la cause du blocage).
                _consec_llm_fails = 0
                # Tâche clôturée → l'agent greedy doit re-piocher une autre
                # tâche (occupation continue) au lieu de conclure.
                if fn_name in ("task_done_v1", "workspace_task_done_v1"):
                    _tasks_done_in_run += 1
                    _greedy_repick_pending = True
                # Suivi des outils de lecture vs d'écriture : si le membre
                # enchaîne les lectures sans produire de livrable, on le pousse
                # à conclure (sinon il « analyse » jusqu'à max_loops).
                if any(w in fn_name for w in _WRITE_TOOLS):
                    _write_tools_ok += 1
                elif any(r in fn_name for r in _READ_TOOLS):
                    if _write_tools_ok == 0 and not _conclusion_pushed \
                            and (_tool_rounds >= 5 or consecutive_failures >= 3):
                        _conclusion_pushed = True
                        messages.append({
                            "role": "system",
                            "content": ("Tu as suffisamment lu/exploré. Tu DOIS maintenant "
                                        "PRODUIRE le livrable : écris le rapport/fichier "
                                        "demandé avec write_file_v1, puis commite-le avec "
                                        "git_commit_v1 et marque la tâche done avec "
                                        "task_done_v1. Ne relis plus de fichiers "
                                        "inutilement : synthétise et écris.")})
            if _fsm_log is not None:
                _fsm_log.log("warn" if failed else "debug", "tool/" + ("error" if failed else "ok"),
                             f"name={fn_name} exit={tool_result.get('exit_code')} "
                             f"err={str(tool_result.get('error') or tool_result.get('stderr') or '')[:80]}")
            if consecutive_failures >= 10:
                signals.append({"signal": "tool_loop_break",
                                "stdout": f"{consecutive_failures} échecs d'outil consécutifs sur {fn_name}: {tool_result.get('stderr') or tool_result.get('error') or tool_result.get('stdout', '')}",
                                "exit_code": 1})
                return signals

            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                             "name": fn_name, "content": json.dumps(tool_result, default=str)})

            finish_signal = {"signal": "tool_finish", "tool": fn_name, "stdout": tool_result.get("stdout", ""), "stderr": tool_result.get("stderr", ""), "exit_code": tool_result.get("exit_code", 1)}
            signals.append(finish_signal)

            if _signal_match(f"tool_finish.{fn_name}", break_list):
                return signals

    signals.append({"signal": "max_loops",
                    "stdout": f"Limite de {max_loops} boucles atteinte (tools={_tool_rounds}, échecs_llm={_llm_fail_rounds})",
                    "exit_code": 1})

    # ── Test agentic automatique ──
    # Un codeur/merger qui termine SANS avoir produit de livrable (aucun outil
    # d'écriture réussi) est suspect : soit il n'a rien fait, soit le dernier
    # LLM n'est pas agentic (répond en texte sans jamais appeler les outils).
    # On vérifie la capacité du dernier modèle utilisé AVANT de conclure.
    if _write_tools_ok == 0 and role_type in ("codeur", "orchestrateur") \
            and bridge is not None and p_ref and m_ref:
        try:
            _agentic = None
            if cat is not None:
                from modules.sql.catalogue_repo import ModelCapaciteRepository
                try:
                    _repo = ModelCapaciteRepository(cat.conn)
                    # Trouver l'endpoint/provider du modèle courant.
                    _ep = cat.conn.execute("""
                        SELECT kem.endpoint_id, kem.provider_id
                        FROM provider_models_mapping kem
                        JOIN catalogue_models cm ON cm.id = kem.model_id
                        WHERE (cm.ref = ? OR cm.model_key = ?)
                          AND kem.provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?)
                        LIMIT 1
                    """, (m_ref, m_ref, p_ref)).fetchone()
                    if _ep:
                        _mid = cat.conn.execute(
                            "SELECT id FROM catalogue_models "
                            "WHERE ref = ? OR model_key = ? LIMIT 1",
                            (m_ref, m_ref)).fetchone()
                        if _mid:
                            _agentic = _repo.resolve_bool(
                                _mid["id"], _ep["endpoint_id"], _ep["provider_id"],
                                "agentic")
                except Exception:
                    _agentic = None
            # Seuil 0.8 : si déjà ≥ 0.8 (capacité prouvée), on ne re-teste pas.
            if _agentic is None or _agentic < 0.8:
                try:
                    _test = bridge.test_agentic(p_ref, m_ref)
                    _ok = bool(_test.get("ok"))
                    if _fsm_log is not None:
                        _fsm_log.log(
                            "warn" if not _ok else "info",
                            "agentic/test",
                            f"{p_ref}/{m_ref} agentic={'OUI' if _ok else 'NON'} "
                            f"(tc={_test.get('tool_calls')} coh={_test.get('coherent')} "
                            f"tested={_test.get('tested')} skip={_test.get('skipped_errors')})")
                    # Observer dans model_endpoint_provider_capacite.
                    if cat is not None:
                        from modules.sql.catalogue_repo import ModelCapaciteRepository
                        try:
                            _ep = cat.conn.execute("""
                                SELECT kem.endpoint_id, kem.provider_id
                                FROM provider_models_mapping kem
                                JOIN catalogue_models cm ON cm.id = kem.model_id
                                WHERE (cm.ref = ? OR cm.model_key = ?)
                                  AND kem.provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?)
                                LIMIT 1
                            """, (m_ref, m_ref, p_ref)).fetchone()
                            _mid = cat.conn.execute(
                                "SELECT id FROM catalogue_models "
                                "WHERE ref = ? OR model_key = ? LIMIT 1",
                                (m_ref, m_ref)).fetchone()
                            if _ep and _mid:
                                ModelCapaciteRepository(cat.conn).observe_bool(
                                    _mid["id"], _ep["endpoint_id"],
                                    _ep["provider_id"], "agentic", _ok,
                                    source="api", strength=0.5)
                                cat.conn.commit()
                        except Exception:
                            pass
                    signals.append({
                        "signal": "agentic_test",
                        "stdout": (f"codeur sans livrable → test agentic "
                                   f"{p_ref}/{m_ref}: {'agentic' if _ok else 'NON agentic'}"),
                        "agentic": _ok,
                        "provider": p_ref, "model": m_ref,
                    })
                except Exception:
                    pass
        except Exception:
            pass
    return signals


def exec(inputs: dict, home: str) -> dict:
    request = inputs.get("request", "")
    if not request:
        return {"signal": "error", "stdout": "", "stderr": "request required", "exit_code": 1}

    bundle_names = inputs.get("bundles", [])
    if not bundle_names:
        return {"signal": "error", "stdout": "", "stderr": "bundles required", "exit_code": 1}

    context = inputs.get("context", "")
    max_loops = int(inputs.get("max_loops", 30))
    grouping = inputs.get("grouping", "none")
    break_on_signals = inputs.get("break_on_signals", [])
    break_on_counts = inputs.get("break_on_counts", {})
    llm_timeout = inputs.get("llm_timeout")
    global_timeout = inputs.get("global_timeout")
    provider_ref = inputs.get("provider_ref", "")
    model_ref = inputs.get("model_ref", "")
    branch = inputs.get("branch", "") or inputs.get("branch_name", "")

    # Connexions DB + rôle de l'agent (pour les gates git/agentic de sortie).
    _cat = None
    _km = None
    _role_type = ""
    try:
        from modules.sql.db import CatalogueDB as _CDB, ModelWeaverDB as _MWDB
        from modules.key_manager.key_manager_module import KeyManager as _KM
        _cat = _CDB()
        _km = _KM(ModelWeaverDB())
    except Exception:
        pass
    try:
        _agent_id_from_home = _Path(home).name
        if _cat is not None:
            _arow = _cat.conn.execute(
                "SELECT role_type FROM agents WHERE agent_id = ?",
                (int(_agent_id_from_home),)).fetchone()
            if _arow:
                _role_type = _arow["role_type"] or ""
    except Exception:
        pass

    # Diffusion streaming : dev-chat (GUI) peut activer le mode streaming œ
    # `stream_events=true`. Les deltas thinking/content sont publiés dans le
    # StreamBus (cross-process) sous l'agent_id dérivé du home — la route SSE
    # dev-chat/stream les relit pour l'affichage temps réel.
    stream_events = bool(inputs.get("stream_events", False))
    on_event = None
    if stream_events:
        try:
            from AgentFrameWork.stream_bus import stream_bus as _sb
            import re as _re
            _parts = _Path(home).parts
            _aid = ""
            if "agent_home" in _parts:
                _aid = str(_parts[_parts.index("agent_home") + 1])
            elif _re.match(r"^\d+$", _Path(home).name):
                _aid = _Path(home).name
            if _aid:
                agent_id_int = int(_aid)
                def on_event(kind: str, text: str) -> None:
                    try:
                        _sb.publish(agent_id_int, text, kind)
                    except Exception:
                        pass
        except Exception:
            on_event = None
            stream_events = False

    tools = resolve_bundles(bundle_names)
    if not tools:
        return {"signal": "error", "stdout": "", "stderr": f"aucun outil trouvé dans bundles {bundle_names}", "exit_code": 1}

    # Bridge actif (pour le test agentic de sortie de boucle). Best-effort.
    _bridge_outer = None
    try:
        from modules.llm_manager.llm_manager import LLMManager as _LLMMgr
        _bridge_outer = _LLMMgr(_cat, km=_km).get_bridge()
    except Exception:
        try:
            from modules.llm_manager.direct_bridge import DirectBridge as _DB
            _bridge_outer = _DB(cat=_cat, km=_km)
        except Exception:
            _bridge_outer = None

    # Discipline git : pull avant d'agir (voir le travail des autres membres)
    _auto_git_sync(home, "pre", branch)
    # HEAD d'entrée : référence pour vérifier si le membre a produit du code.
    _git_heads_in = _auto_git_head(home)

    signals = _chat_with_tools(request, context, tools, max_loops, grouping,
                               break_on_signals, break_on_counts,
                               llm_timeout, global_timeout,
                               provider_ref, model_ref, home,
                               on_event=on_event, stream_events=stream_events,
                               bridge=_bridge_outer, role_type=_role_type, cat=_cat)

    # Discipline git : commit + push après l'action (publier son travail)
    _auto_git_sync(home, "post", branch)

    # ── Vérification git pour les rôles à impératif de code ──
    # codeur / orchestrateur (merger) doivent produire du code. On vérifie
    # que le clone workspace a bougé (HEAD différent de l'entrée OU commit
    # auto effectué). Sinon, un signal 'no_code_produced' est émis : le
    # workflow/gestionnaire décide (test agentic, relance, etc.).
    if _role_type in ("codeur", "orchestrateur"):
        try:
            _git_after = _auto_git_verify(home, branch)
            _heads_out = _auto_git_head(home)
            _moved = any(
                _heads_out.get(p) and _heads_out.get(p) != h
                for p, h in _git_heads_in.items())
            _produced = _git_after.get("produced_code", False) or _moved
            if not _produced:
                signals.append({
                    "signal": "no_code_produced",
                    "stdout": (f"Rôle {_role_type} : aucun changement git détecté "
                               f"dans la session (commit/push vide). Vérifier que "
                               f"le travail a bien été produit."),
                    "git_committed": _git_after.get("committed", False),
                    "git_pushed": _git_after.get("pushed", False),
                })
        except Exception:
            pass

    # Auto-commit/push si des fichiers ont été modifiés dans le workdir
    workdir = _Path(home) / "work"
    if workdir.exists() and (workdir / ".git").exists():
        import subprocess as _sp
        _sp.run(["git", "-C", str(workdir), "config", "user.name", "auto"], capture_output=True, timeout=10)
        _sp.run(["git", "-C", str(workdir), "config", "user.email", "auto@auto"], capture_output=True, timeout=10)
        _sp.run(["git", "-C", str(workdir), "checkout", "-b", "main", "origin/main"],
               capture_output=True, timeout=10)
        _sp.run(["git", "-C", str(workdir), "add", "."], capture_output=True, timeout=10)
        r = _sp.run(["git", "-C", str(workdir), "diff", "--cached", "--quiet"],
                   capture_output=True, timeout=10)
        if r.returncode != 0:
            _sp.run(["git", "-C", str(workdir), "commit", "-m", "auto-commit"],
                   capture_output=True, timeout=10)
            _sp.run(["git", "-C", str(workdir), "push", "origin", "main"],
                   capture_output=True, text=True, timeout=10)

    return signals[-1] if signals else {"signal": "loop_end", "stdout": "", "exit_code": 0}


__skills__ = ["exec"]