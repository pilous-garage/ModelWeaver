"""FSM Interpreter — Moteur universel d'exécution d'agents.

Remplace l'ancien Worker. Exécute un workflow (graphe d'étapes) où
`llm_call` utilise le bridge via LLMManager (pas d'HTTP direct), et `tool_call`
délègue au ToolExecutor.

Étapes supportées (Phase 2) :
  llm_call     → Bridge.chat(provider_ref, model_ref, messages, ...)
  tool_call    → ToolExecutor.execute(tool_name, args)
  switch       → Branchement conditionnel sur variables
  sleep        → Pause déportée (retour SLEEPING)
  end          → Fin du workflow
  set_variable → Définit une variable
  for          → Boucle bornée (range ou liste) sur un corps imbriqué
  while        → Boucle conditionnelle sur un corps imbriqué
  break        → Sort de la boucle en cours
  continue     → Passe à l'itération suivante de la boucle en cours
"""

import json
import logging
import os
import threading
import time
import queue as _queue
from typing import Any, Dict, List, Optional

from modules.llm_manager.llm_manager import LLMManager
from modules.llm_manager.base_bridge import BridgeError
from modules.control.pause_flag import get_pause_store
from AgentFrameWork.pause_flag_store import is_paused, wait_for_resume
from AgentFrameWork.tool_executor import ToolExecutor

logger = logging.getLogger("modelweaver.fsm")

import re

_FENCE_RE = re.compile(r"^\s*```[^\n]*\n(.*?)\n?```\s*$", re.DOTALL)

# Skills DÉCISIONNELS (terminaux) : leur exécution clôt la boucle tool_calls
# d'un llm_call — pas besoin de re-appeler le LLM. Les tools d'INFO (lecture/
# exploration) laissent la boucle continuer (l'agent peut demander plus de
# contexte). L'analyste découpeur n'a QUE des tools terminaux (decoupe/ask_intel).
_TERMINAL_SKILLS = {
    "workspace/decoupe@v1",
    "workspace/ask_intel@v1",
    "workspace/assign_difficulte@v1",
    "workspace/sub_task_done@v1",
    "workspace/sub_task_release@v1",
    "workspace/task_verdict@v1",
    "workspace/review_verdict@v1",
}


def _strip_code_fences(text: str) -> str:
    """Nettoie une sortie LLM censée être du code brut.

    1. Si le texte entier est un unique bloc fencé (```lang ... ```), ne
       garde que le corps.
    2. Sinon retire toute ligne de fence isolée (```...).
    3. Retire un éventuel bloc de prose ajouté en fin par le LLM
       (ex. « Note: This code is a basic implementation… ») : on tronque à
       la dernière ligne qui ressemble à du code, si les lignes suivantes
       ressemblent à du texte naturel.
    """
    if not text:
        return text
    m = _FENCE_RE.match(text)
    if m:
        text = m.group(1)
    else:
        text = "\n".join(ln for ln in text.splitlines()
                         if not ln.strip().startswith("```"))
    return _strip_trailing_prose(text)


_PROSE_PREFIXES = (
    "note:", "note that", "this code", "this implementation", "explanation",
    "here is", "here's", "the above", "in this", "you can", "to use",
    "make sure", "remember", "keep in mind", "disclaimer", "n.b.",
)


def _looks_like_prose(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    low = s.lower()
    if low.startswith(_PROSE_PREFIXES):
        return True
    return False


def _strip_trailing_prose(text: str) -> str:
    lines = text.splitlines()
    cut = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        if _looks_like_prose(lines[i]):
            cut = i
        elif lines[i].strip() == "":
            continue
        else:
            break
    return "\n".join(lines[:cut]).rstrip("\n")


class AgentAbort(Exception):
    """Levée par un signal_check (kill) pour interrompre le FSM."""


class PauseSignalError(Exception):
    """Levée par un signal_check (pause) pour mettre le FSM en attente."""


class FSMResult:
    """Résultat d'exécution du FSM."""

    def __init__(self):
        self.status: str = "running"
        self.content: str = ""
        self.variables: Dict[str, Any] = {}
        self.messages: List[Dict[str, str]] = []
        self.next_step_id: Optional[str] = None
        self.sleep_seconds: Optional[int] = None
        self.end_reason: Optional[str] = None
        self.iterations: int = 0
        self.tokens_used: int = 0
        self.budget: Dict[str, Any] = {}
        # Flags de contrôle (Phase 4 : signaux)
        self._paused: bool = False
        # Contrôle de boucle : 'break' | 'continue' | None (consommé par for/while)
        self._loop_ctl: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "content": self.content,
            "variables": self.variables,
            "iterations": self.iterations,
            "end_reason": self.end_reason,
            "sleep_seconds": self.sleep_seconds,
            "tokens_used": self.tokens_used,
            "budget": self.budget,
            "paused": self._paused,
            "error": self.end_reason if self.status in ("failed", "error", "aborted") else "",
        }


class SubAgentLink:
    """Liaison directe parent ↔ sub-agent.

    Un sub-agent est une THREAD ENFANT semi-autonome : il partage le home de
    son propriétaire (aucun nouveau home), a son propre parcours FSM et son
    propre log. La communication passe par DEUX files de messages :
      - request : parent → sub-agent (bloquant : attend l'ack du sub-agent ;
        non-bloquant : fire-and-forget)
      - respond : sub-agent → parent (bloquant : attend la réponse avec
        timeout ; non-bloquant : lecture immédiate sans attendre)
    """

    def __init__(self) -> None:
        self.request_q: "_queue.Queue" = _queue.Queue()
        self.respond_q: "_queue.Queue" = _queue.Queue()
        self.request_ack = threading.Event()   # posé quand le sub-agent a consommé la request
        self.done = threading.Event()          # posé quand le FSM du sub-agent est terminé
        self.result: Optional["FSMResult"] = None
        self.error: Optional[str] = None


class FSMInterpreter:
    """Exécute un workflow d'agent étape par étape.

    Phase 4 : supporte le contrôle par signaux et le streaming.
      - signal_check(result) : callable appelé avant chaque étape (et en
        boucle pendant une pause). Peut lever AgentAbort (kill) ou positionner
        result._paused (pause). Les signaux status/health/configure y sont
        traités par l'appelant.
      - stream_sink(chunk) : callable recevant chaque morceau de texte produit
        par un llm_call (diffusion temps réel).
    """

    def __init__(
        self,
        bridge=None,
        tool_executor: Optional[ToolExecutor] = None,
        max_iterations: int = 100,
    ):
        self.bridge = bridge or LLMManager(cat=None).get_bridge()
        self.tool_executor = tool_executor or ToolExecutor(home_root="/tmp")
        self.max_iterations = max_iterations
        # Mapping tool_name (format OpenAI, ex. file_write_file_v1) → nom de
        # skill catalogue (ex. file/write_file@v1), construit par
        # `_build_llm_tools`. La conversion inverse naive `_ → /` casse les
        # underscores internes des noms de skill (file_write_file_v1 → file/
        # write/file@v1), donc on mémorise le mapping exact.
        self._tool_to_skill: Dict[str, str] = {}
        # Sub-agents : {ref → déf inline} fourni au run() ; et la liaison
        # (files request/respond) quand on exécute EN TANT QUE sub-agent.
        self._sub_agents: Dict[str, Dict[str, Any]] = {}
        self._sub_link: Optional[SubAgentLink] = None

    def run(
        self,
        workflow: Dict[str, Any],
        messages: List[Dict[str, str]],
        variables: Optional[Dict[str, Any]] = None,
        provider_ref: str = "",
        model_ref: str = "",
        start_step_id: Optional[str] = None,
        signal_check: Optional[Any] = None,
        stream_sink: Optional[Any] = None,
        spawn_handler: Optional[Any] = None,
        handoff_handler: Optional[Any] = None,
        agent_call_handler: Optional[Any] = None,
        lifecycle_mgr: Optional[Any] = None,
        sub_agents: Optional[Dict[str, Dict[str, Any]]] = None,
        sub_link: Optional[SubAgentLink] = None,
        home: str = "",
    ) -> FSMResult:
        """Exécute le workflow."""
        result = FSMResult()
        result.messages = list(messages)
        result.variables = dict(variables or {})
        self._signal_check = signal_check
        self._spawn_handler = spawn_handler
        self._handoff_handler = handoff_handler
        self._agent_call_handler = agent_call_handler
        self._lifecycle_mgr = lifecycle_mgr
        self._sub_agents = dict(sub_agents or {})
        self._sub_link = sub_link
        self._home = home
        # Pile de scopes hiérarchiques ($var, agent.var=). Le scope racine
        # porte les variables du run ; les skills/subskills poussent le leur.
        from modules.agent_graph_utils.scopes import ScopeStack
        self._scopes = ScopeStack()
        self._scopes.push("agent", result.variables)
        # Expose le home aux steps ({{home}}) — les skills en ont besoin.
        if home:
            result.variables.setdefault("home", home)
        elif self.tool_executor and self.tool_executor.home_root:
            result.variables.setdefault("home", self.tool_executor.home_root)

        steps = workflow.get("steps", [])
        steps_by_id = {s["id"]: s for s in steps}
        max_iter = workflow.get("max_iterations", self.max_iterations)
        self._max_iter = max_iter

        current_id = start_step_id or self._find_entry_point(steps)
        if not current_id:
            result.status = "failed"
            result.end_reason = "Aucune étape de départ trouvée"
            return result

        while current_id and result.iterations < max_iter:
            # ── Contrôle par signaux (Phase 4) ──
            if signal_check is not None:
                self._check_signals(signal_check, result)
                if result._paused:
                    # Boucle d'attente : on reste sur l'étape courante jusqu'au
                    # signal resume (ou kill).
                    while result._paused and result.status == "running":
                        time.sleep(0.2)
                        self._check_signals(signal_check, result)
                    if result.status != "running":
                        break
                    # Reprendre sans avancer l'étape
                    continue

            # ── Pause globale projet/team/agent avant chaque step ──
            project_id = result.variables.get("project_id")
            team_name = result.variables.get("team_name")
            agent_id = result.variables.get("agent_id")
            if is_paused(project_id=project_id, team_name=team_name, agent_id=agent_id):
                wait_for_resume(project_id=project_id, team_name=team_name, agent_id=agent_id)
                continue

            step = steps_by_id.get(current_id)
            if not step:
                result.status = "failed"
                result.end_reason = f"Étape '{current_id}' introuvable"
                break

            result.iterations += 1
            step_type = step.get("type", "")
            result.next_step_id = None

            handler = getattr(self, f"_step_{step_type}", None)
            if handler is None:
                result.status = "failed"
                result.end_reason = f"Type d'étape inconnu: {step_type}"
                break

            should_continue = handler(
                step, result,
                provider_ref=provider_ref,
                model_ref=model_ref,
                stream_sink=stream_sink,
            )

            if result.status != "running":
                break
            if not should_continue:
                break

            # ── Lifecycle hook post_step ──
            if self._lifecycle_mgr:
                self._lifecycle_mgr.publish("post_step",
                    step=step, step_id=current_id, status=result.status,
                    variables=dict(result.variables))

            current_id = result.next_step_id

        if result.iterations >= max_iter and result.status == "running":
            result.status = "failed"
            result.end_reason = f"Limite d'itérations atteinte ({max_iter})"

        # ── Lifecycle hooks finaux ──
        if self._lifecycle_mgr:
            if result.status in ("failed", "aborted"):
                self._lifecycle_mgr.publish("on_error",
                    step_id=current_id, status=result.status,
                    error=result.end_reason or "")
            self._lifecycle_mgr.publish("post_exec",
                status=result.status, variables=dict(result.variables))

        return result

    @staticmethod
    def _check_signals(signal_check, result: "FSMResult") -> None:
        """Appelle le contrôleur de signaux ; traite AgentAbort (kill)."""
        try:
            signal_check(result)
        except AgentAbort:
            result.status = "aborted"
            result.end_reason = "Interrompu par signal kill"

    def _build_pause_check(self, variables: Dict[str, Any]) -> Any:
        """Retourne un callable utilisé par le bridge pour interrompre/reprendre
        les streams SSE selon le flag de pause partagé.

        Le callable lit les identifiants depuis les variables d'exécution :
        project_id, team_name, agent_id.
        """
        project_id = variables.get("project_id") or getattr(self, "_pause_project_id", None)
        team_name = variables.get("team_name") or getattr(self, "_pause_team_name", None)
        agent_id = variables.get("agent_id") or getattr(self, "_pause_agent_id", None)

        def _check() -> bool:
            return bool(is_paused(
                project_id=str(project_id) if project_id else None,
                team_name=str(team_name) if team_name else None,
                agent_id=str(agent_id) if agent_id else None,
            ))

        return _check

    def _find_entry_point(self, steps: List[Dict]) -> Optional[str]:
        referenced = set()
        for s in steps:
            nxt = s.get("next")
            if nxt:
                referenced.add(nxt)
            for cond in s.get("conditions", []):
                if cond.get("next"):
                    referenced.add(cond["next"])
            if s.get("default"):
                referenced.add(s["default"])
        for s in steps:
            if s["id"] not in referenced:
                return s["id"]
        return steps[0]["id"] if steps else None

    # ── Steps ──────────────────────────────────────────

    def _build_llm_tools(self, bundles: Optional[List[str]] = None,
                         skills: Optional[List[str]] = None) -> List[Dict]:
        """Construit la liste des outils (OpenAI function calling) depuis les skills YAML.

        `bundles` (optionnel) : expose les tools des bundles nommés (ex. ["dev"]).
        `skills` (optionnel)  : expose des refs de skills DIRECTES définies dans
        le .yaml de l'agent (ex. ["workspace/token_task_pick@v1"]) — chargées
        depuis le catalogue des skills, sans passer par un bundle.
        """
        try:
            import yaml as _yaml
        except ImportError:
            return []
        from pathlib import Path as _Path

        base = _Path(__file__).parent.parent / "AgentsCatalogue" / "skills"
        tool_skills = [
            "shell/exec@v1",
            "git/lite@v1",
            "file/read_file@v1",
            "file/write_file@v1",
        ]
        # Tools additionnels depuis les bundles nommés.
        if bundles:
            try:
                from AgentsCatalogue.lib.workflow.bundles import resolve as _resolve_bundles
                tool_skills += [s.get("name", "") for s in _resolve_bundles(bundles)]
            except Exception:
                pass
        # Tools additionnels depuis les skills DIRECTES du .yaml de l'agent.
        if skills:
            tool_skills += list(skills)
        tools = []
        seen = set()
        for ref in tool_skills:
            parts = ref.replace("@v1", "").split("/")
            candidates = list(base.rglob(f"{parts[-1]}*.skill.yaml"))
            skill_path = candidates[0] if candidates else None
            if not skill_path or not skill_path.exists():
                continue
            try:
                with open(skill_path, encoding="utf-8") as f:
                    skill = _yaml.safe_load(f)
            except Exception:
                continue
            name = skill.get("name", "").replace("/", "_").replace("@", "_").replace(".", "_")
            if name in seen:
                continue
            seen.add(name)
            # Mapping exact tool → skill (la conversion inverse naive _ → /
            # est perdue pour les noms à underscores internes).
            self._tool_to_skill[name] = skill.get("name", "") or ref
            desc = skill.get("description", "")
            inputs = skill.get("inputs", {})
            props = {}
            required = []
            for k, v in inputs.items():
                if v.get("injected"):
                    continue
                if v.get("required"):
                    required.append(k)
                props[k] = {"type": v.get("type", "string"), "description": v.get("description", "")}
            tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": {"type": "object", "properties": props, "required": required},
                },
            })
        # ── tool_as_text : échappatoire pour les modèles NON-agentic ──
        # Le modèle appelle UN tool (tool_as_text) avec {tool_name, arguments_json}
        # en texte ; le FSM le résout et exécute le tool réel. Permet à un modèle
        # qui ne maîtrise pas le function-calling (ex. hy3:free) de piloter les
        # skills sans bloc ###tool_call### dans le prompt (génère un tool_call
        # API standard → satisfait agentic:always).
        if tools and not any(t["function"]["name"] == "tool_as_text" for t in tools):
            tools.append({
                "type": "function",
                "function": {
                    "name": "tool_as_text",
                    "description": (
                        "Appelle n'importe quel outil du contexte en passant son "
                        "nom et ses arguments en JSON texte. Usage : "
                        '{"tool_name": "<nom_de_l_outil>", "arguments_json": "{\\"key\\": \\"value\\"}"}. '
                        "Utilise-le si tu ne peux pas appeler directement les fonctions."),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "tool_name": {"type": "string", "description": "Nom exact de l'outil à appeler (ex. workspace_decoupe_v1)."},
                            "arguments_json": {"type": "string", "description": "Arguments JSON (string) de l'outil."},
                        },
                        "required": ["tool_name", "arguments_json"],
                    },
                },
            })
        return tools

    def _capability_confidence(self, model_ref: str, provider_ref: str,
                               capability: str) -> Optional[float]:
        """Confidence (0..1) d'une capacité d'un modèle/provider dans la table
        d'expérience (model_endpoint_provider_capacite). None si inconnu.

        Utilisé pour décider du mode agentic d'un llm_call :
          - agentic              : tool_calls natifs via l'API
          - agentic-translation  : tools via le format texte ###tool_call:...###
        """
        try:
            from modules.sql.catalogue_repo import CatalogueDB
            cat = CatalogueDB()
            row = cat.conn.execute("""
                SELECT mepc.confidence
                FROM model_endpoint_provider_capacite mepc
                JOIN catalogue_models cm ON cm.id = mepc.model_id
                WHERE (cm.ref = ? OR cm.model_key = ?)
                  AND mepc.provider_id = (
                        SELECT id FROM catalogue_providers WHERE ref = ?)
                  AND mepc.capability = ?
                ORDER BY mepc.updated_at DESC LIMIT 1
            """, (model_ref, model_ref, provider_ref, capability)).fetchone()
            conf = float(row["confidence"]) if row else None
            cat.close()
            return conf
        except Exception:
            return None

    def _observe_capability(self, model_ref: str, provider_ref: str,
                            capability: str, ok: bool,
                            source: str = "experience") -> None:
        """Met à jour la table d'expérience (model_endpoint_provider_capacite)
        pour une capacité booléenne à chaque llm_call qui l'utilise.

        `ok` : la capacité a fonctionné (tool_call natif / traduit) ou non."""
        try:
            from modules.sql.catalogue_repo import CatalogueDB
            from modules.sql.catalogue_repo import ModelCapaciteRepository
            cat = CatalogueDB()
            # Résoudre model_id + endpoint_id + provider_id du couple.
            row = cat.conn.execute("""
                SELECT cm.id AS model_id, kem.endpoint_id,
                       kem.provider_id
                FROM provider_models_mapping kem
                JOIN catalogue_models cm ON cm.id = kem.model_id
                WHERE (cm.ref = ? OR cm.model_key = ?)
                  AND kem.provider_id = (
                        SELECT id FROM catalogue_providers WHERE ref = ?)
                LIMIT 1
            """, (model_ref, model_ref, provider_ref)).fetchone()
            if row:
                ModelCapaciteRepository(cat.conn).observe_bool(
                    row["model_id"], row["endpoint_id"], row["provider_id"],
                    capability, ok, source=source, strength=0.3)
                cat.conn.commit()
            cat.close()
        except Exception:
            pass

    def _translation_prompt(self, tools: List[Dict]) -> str:
        """Bloc d'instruction pour le mode need_translation (< ~1k tokens).

        Liste les outils disponibles et le format de réponse
        `###tool_call:nom|JSON###` pour un modèle NON-agentic mais
        capable de suivre une convention texte (JSON d'arguments BRUT,
        sans guillemets d'encadrement — plus robuste à parser)."""
        lines = [
            "## OUTILS DISPONIBLES",
            "Pour exécuter un outil, écris UNE ligne exactement au format :",
            "###tool_call:nom_outil|{\"param\": \"valeur\"}###",
            "Exemple : "
            "###tool_call:file_write_file_v1|{\"path\": \"a.py\", "
            "\"content\": \"print(1)\"}###",
            "Un appel par ligne. Après exécution des outils, donne ta réponse "
            "finale en texte.",
            "",
            "Outils disponibles :",
        ]
        for t in tools:
            fn = t.get("function", {})
            name = fn.get("name", "?")
            desc = (fn.get("description", "") or "").split("\n")[0][:120]
            params = fn.get("parameters", {}).get("properties", {})
            param_names = ", ".join(list(params.keys())[:6])
            lines.append(f"- {name} : {desc}  (params: {param_names})")
        return "\n".join(lines)[:1200]

    def _parse_translation_tools(self, text: str) -> List[tuple]:
        """Parse les ###tool_call:nom|JSON### d'une réponse de traduction.

        Retourne [(nom, args_dict)] dans l'ordre d'apparition."""
        import re as _re
        out = []
        for m in _re.finditer(r"###tool_call:([A-Za-z0-9_]+)\|(.*?)###",
                              text, _re.DOTALL):
            name = m.group(1)
            raw = m.group(2).strip()
            args = {}
            try:
                args = json.loads(raw)
            except Exception:
                args = {"content": raw}
            if not isinstance(args, dict):
                args = {"content": str(args)}
            out.append((name, args))
        return out

    def _resolve_tool_skill(self, fn_name: str) -> str:
        """Convertit un nom de tool OpenAI (file_write_file_v1) en nom de
        skill catalogue (file/write_file@v1).

        Priorité au mapping exact construit par `_build_llm_tools` (le seul
        fiable : la conversion inverse naive `_ → /` casse les underscores
        internes des noms de skill). Fallbacks :
          1. nom déjà complet (contient / ou @) → tel quel ;
          2. mapping exact ;
          3. tentative naive (file/write/file@v1 → inexploitable) + candidats
             en scindant sur le premier préfixe de catégorie
             (git_clone_v1 → git/clone@v1).
        """
        if "/" in fn_name or "@" in fn_name:
            return fn_name
        if fn_name in self._tool_to_skill:
            return self._tool_to_skill[fn_name]
        no_v = fn_name.replace("_v1", "")
        # Candidats : préfixe de catégorie (git_clone_v1 → git/clone@v1), puis
        # split à chaque underscore pour les skills à préfixe multi-segments.
        cands = [no_v + "@v1"]
        parts = fn_name.replace("_v1", "").split("_")
        for k in range(1, len(parts)):
            cands.append("/".join(parts[:k]) + "/" + "_".join(parts[k:]) + "@v1")
        # Plus longue correspondance dans le mapping connu (le plus spécifique).
        known = sorted(self._tool_to_skill.values(),
                       key=len, reverse=True)
        for k in known:
            if k.replace("/", "_").replace("@", "_").replace(".", "_") == fn_name:
                return k
        for cand in cands:
            if cand in self._tool_to_skill.values():
                return cand
        return cands[0]

    def _agent_home(self, agent_id) -> str:
        """Home du workspace d'un agent (agent_home/<agent_id>), fallback sur
        le home_root de l'exécuteur."""
        try:
            from services._common import mw_home
            if agent_id:
                return str(mw_home() / "agent_home" / str(agent_id))
        except Exception:
            pass
        return (self.tool_executor.home_root
                if self.tool_executor else "/tmp")

    def _agent_work_home(self, agent_id, variables: Dict) -> str:
        """Home de travail pour les tool_calls d'un step llm_call.

        Si le step a cloné un repo (variable `repo_eff` non vide), les skills
        file (write_file/append_file/…) doivent écrire DANS le clone
        (agent_home/<aid>/workspace/<repo_eff>), pas dans le home racine —
        sinon le code produit n'apparaît jamais dans le dépôt et la vérif git
        ne voit aucun diff. Sans repo → home agent (comportement historique).
        """
        _a_id = str(agent_id or "")
        try:
            repo_eff = str(variables.get("repo_eff", "") or "").strip()
        except Exception:
            repo_eff = ""
        if repo_eff and _a_id:
            base = self._agent_home(_a_id)
            return os.path.join(base, "workspace", repo_eff)
        return self._agent_home(agent_id)

    def _step_llm_call(
        self, step: Dict, result: FSMResult,
        provider_ref: str = "", model_ref: str = "",
        stream_sink: Optional[Any] = None,
    ) -> bool:
        """Appel LLM via le Bridge (avec streaming optionnel)."""
        skill_prompt = self._resolve(
            step.get("skill_prompt", ""), result.variables
        )
        output_capture = step.get("output_capture")

        # Construire les messages : le skill_prompt devient le message
        # utilisateur principal, pour éviter qu'un user message précédent
        # (ex: "request") noie l'instruction.
        msgs = []
        if result.messages and result.messages[0].get("role") == "system":
            msgs.append(result.messages[0])
        if skill_prompt:
            msgs.append({"role": "user", "content": skill_prompt})
        else:
            msgs.extend(result.messages)

        p_ref = step.get("provider_ref") or provider_ref
        m_ref = step.get("model_ref") or model_ref
        # Résolution des placeholders {{_llm_provider}} / {{_llm_model}} : le
        # step ask_llm capture ces variables, les steps llm_call suivants les
        # référencent pour utiliser le LLM alloué.
        p_ref = self._resolve(p_ref, result.variables)
        m_ref = self._resolve(m_ref, result.variables)
        if not p_ref:
            p_ref = provider_ref
        if not m_ref:
            m_ref = model_ref
        temperature = step.get("temperature", 0.7)
        max_tokens = step.get("max_tokens", 4096)

        try:
            content = ""
            tokens = 0
            _trace_tools: List[str] = []
            _trans_ok = 0
            # agent_id disponible pour le journal d'usage (si l'agent l'a
            # fourni via {{agent_id}} ou le contexte d'exécution).
            _agent_id = result.variables.get("agent_id", "")
            # Steps AVEC tools (skills/bundles) : le streaming assemble les
            # tool_calls par deltas et peut les corrompre selon le provider
            # (ex. Opencode Zen en échec, llama en tool_call tronqué) → on
            # force `chat` (non-stream) pour fiabiliser les tool_calls. Le
            # streaming reste pour les steps texte purs (stream_sink).
            has_tools = bool(step.get("skills") or step.get("bundles"))
            if stream_sink is not None and not has_tools:
                # Streaming : diffusion chunk par chunk
                for delta in self.bridge.chat_stream(
                    provider_ref=p_ref, model_ref=m_ref,
                    messages=msgs, temperature=temperature, max_tokens=max_tokens,
                    agent_id=_agent_id or None,
                ):
                    content += delta
                    stream_sink(delta)
                    # Interjection (Phase 4) : vérifier les signaux à chaque
                    # chunk (kill interrompt la génération en cours).
                    if self._signal_check is not None:
                        try:
                            self._signal_check(result)
                        except AgentAbort:
                            result.status = "aborted"
                            result.end_reason = "Interrompu par signal kill"
                            return False
                # Estimation tokens (approximation) pour compat metrics
                tokens = max(0, len(content) // 4)
                result.variables["_llm_provider"] = p_ref
                result.variables["_llm_model"] = m_ref
                result.variables["_llm_fallbacks"] = 0
            else:
                # Tag agentic du step llm_call :
                #   false  → AUCUN tool fourni (appel texte simple)
                #   maybe  → tools fournis, texte toléré si le LLM ne les utilise pas
                #   always → tools fournis, et AUCUN tool_call = ÉCHEC (on_error)
                _agentic_req = str(step.get("agentic", "maybe")).lower()
                # Tools : convertir les skills disponibles au format OpenAI.
                # agentic=false → pas de tools (simple génération de texte).
                if _agentic_req == "false":
                    tools = []
                else:
                    tools = self._build_llm_tools(step.get("bundles"), step.get("skills"))
                # DÉCISION du mode agentic (au niveau BRIDGE, via capacites) :
                #   native      → tools API (agentic prouvé ≥ 0.7)
                #   translation → tools dans le prompt (###tool_call:...###),
                #                 modèle prouvé non-agentic (≤ 0.3) avec
                #                 agentic-translation ≥ 0.7
                #   give_both   → tools API ET bloc texte (le LLM choisit ;
                #                 confiance inconnue ou les deux possibles)
                #   text        → pas de tools
                _agentic_mode = "text"
                _need_translation = False
                _translation_prompt = ""
                if _agentic_req != "false" and tools:
                    try:
                        from modules.llm_manager.capacites import (
                            check_capacite, transforme_agentic_send)
                        _cap = check_capacite(self.bridge, None,
                                              p_ref, m_ref)
                        _agentic_mode = _cap.get("mode", "give_both")
                    except Exception:
                        _agentic_mode = "give_both"
                    _send = transforme_agentic_send(_agentic_mode, tools, "")
                    tools = _send["tools"]
                    _translation_prompt = _send["extra_prompt"]
                    _need_translation = _send["translation"]
                tool_kwargs = {"tools": tools} if tools else {}
                # Si mode translation (pas de tools API) → forcer _need_translation
                if _agentic_mode == "translation":
                    _need_translation = True
                # agentic:always → le LLM DOIT produire un tool. Pour être sûr
                # qu'il connaît les outils (et qu'il puisse appeler tool_as_text
                # / écrire le bloc ###tool_call###), on fournit TOUJOURS la
                # tool_list en TEXTE CLAIR dans le prompt, même en mode native.
                if _agentic_req == "always" and tools and not _translation_prompt:
                    from modules.llm_manager.capacites import (
                        _translation_prompt as _mk_tool_list)
                    _translation_prompt = _mk_tool_list(tools)
                    _need_translation = True

                # Boucle tool_calls : LLM → tool → LLM → ... → text.
                # Le PREMIER appel (round 0) passe par resilient_chat (retry +
                # fallback sur un autre LLM) pour les échecs transitoires
                # (timeout, 429, 401) : un llm_call qui rate ne doit PAS être un
                # arrêt silencieux du run. Le step peut désactiver via
                # fallback=False / timeout=<s>. Les rounds suivants (tool_calls)
                # utilisent bridge.chat direct.
                _timeout = step.get("timeout", 90)
                _fallback = step.get("fallback", True)
                _trace_tools = []
                _terminal_hit = False  # tool décisionnel exécuté → sortie de boucle
                _trans_ok = 0  # nb de tools traduits exécutés (agentic-translation)
                for _tool_round in range(15):
                    _meta = {"agentic_mode": _agentic_mode,
                             "agentic_tag": _agentic_req}
                    if _need_translation:
                        # Mode TRADUCTION / GIVE_BOTH (modèle non-agentic) :
                        # le bloc ###tool_call:nom|JSON### est dans le prompt
                        # (avec batch), pas de dépendance aux tools API.
                        _msgs = msgs + [{"role": "user",
                                         "content": _translation_prompt}]
                        if _tool_round == 0:
                            from modules.llm_manager.resilient import resilient_chat
                            response = resilient_chat(
                                p_ref, m_ref, _msgs, timeout=int(_timeout),
                                fallback=_fallback, max_tokens=max_tokens,
                                temperature=temperature, agent_id=_agent_id or None,
                            )
                        else:
                            response = self.bridge.chat(
                                provider_ref=p_ref, model_ref=m_ref,
                                messages=_msgs, temperature=temperature,
                                max_tokens=max_tokens, agent_id=_agent_id or None,
                                meta=_meta,
                            )
                    else:
                        if _tool_round == 0:
                            from modules.llm_manager.resilient import resilient_chat
                            response = resilient_chat(
                                p_ref, m_ref, msgs, timeout=int(_timeout),
                                fallback=_fallback, max_tokens=max_tokens,
                                temperature=temperature, agent_id=_agent_id or None,
                                **tool_kwargs,
                            )
                        else:
                            response = self.bridge.chat(
                                provider_ref=p_ref, model_ref=m_ref,
                                messages=msgs, temperature=temperature, max_tokens=max_tokens,
                                agent_id=_agent_id or None, meta=_meta, **tool_kwargs,
                            )
                    p_ref = getattr(response, "provider_used", p_ref)
                    m_ref = getattr(response, "model_used", m_ref)
                    result.variables["_llm_provider"] = p_ref
                    result.variables["_llm_model"] = m_ref
                    result.variables["_llm_fallbacks"] = 0

                    if _need_translation and not getattr(response, "tool_calls", None):
                            # Parser les ###tool_call:nom:"args"### du texte.
                            _parsed = self._parse_translation_tools(
                                (getattr(response, "content", "") or ""))
                            if not _parsed:
                                break  # plus de tool traduit → réponse finale
                            for _name, _args in _parsed:
                                _trace_tools.append(f"{_name}(translated)")
                            _trans_ok += len(_parsed)
                            # Exécuter les tools traduits et re-appeler le LLM.
                            for _name, _args in _parsed:
                                try:
                                    from services.skill_manager import call_skill
                                    _a_id = result.variables.get("agent_id", "")
                                    if str(_a_id).startswith("agent_"):
                                        _a_id = str(_a_id).split("_")[-1]
                                    _home = self._agent_work_home(_a_id, result.variables)
                                    _res = call_skill(
                                        self._resolve_tool_skill(_name), _args,
                                        home=_home, agent_id=str(_a_id))
                                except Exception as _e:
                                    _res = {"ok": False, "error": str(_e)}
                                msgs.append({
                                    "role": "tool",
                                    "tool_call_id": f"tr-{_tool_round}-{_name}",
                                    "content": json.dumps(_res, default=str),
                                })
                            continue  # re-appeler le LLM avec les résultats
                    tool_calls = getattr(response, "tool_calls", None)
                    if not tool_calls:
                        # debug : trace le contenu texte du round final
                        logger.debug(
                            "llm/round_text provider=%s model=%s content=%s",
                            p_ref, m_ref,
                            (getattr(response, "content", "") or "")[:80])
                        break  # réponse textuelle → on sort

                    # Trace des tools appelés : le output_capture (work_out)
                    # est analysé par check_work_state qui cherche
                    # it_is_done / exit_loop_too_hard dans le TEXTE. Sans
                    # trace, un round qui appelle it_is_done (content vide)
                    # laisse work_out vide → state=continue → boucle infinie.
                    # On inclut les ARGUMENTS (JSON) pour que les skills de
                    # décision (review_decision → approve/disapprove) puissent
                    # lire le choix exact depuis la trace.
                    for _tc in tool_calls:
                        _fn = _tc.get("function", {})
                        _nm = _fn.get("name", "")
                        _args = _fn.get("arguments", "") or ""
                        if _args:
                            _trace_tools.append(f"{_nm}({_args[:200]})")
                        else:
                            _trace_tools.append(_nm)
                    logger.debug(
                        "llm/tool_round provider=%s model=%s tools=%s",
                        p_ref, m_ref,
                        ",".join(_tc.get("function", {}).get("name", "")
                                for _tc in tool_calls))

                    # Ajouter la réponse assistant avec tool_calls
                    asst_msg = {"role": "assistant", "content": response.content or ""}
                    tc_list = []
                    for tc in tool_calls:
                        tc_entry = {
                            "id": tc.get("id"),
                            "type": tc.get("type", "function"),
                            "function": {
                                "name": tc["function"]["name"],
                                "arguments": tc["function"]["arguments"],
                            },
                        }
                        tc_list.append(tc_entry)
                    if tc_list:
                        asst_msg["tool_calls"] = tc_list
                    msgs.append(asst_msg)

                    # Exécuter chaque tool
                    for tc in tool_calls:
                        fn_name = tc["function"]["name"]
                        try:
                            raw_args = json.loads(tc["function"]["arguments"])
                        except json.JSONDecodeError:
                            raw_args = {}
                        # ── tool_as_text : le modèle délègue un tool en texte ──
                        if fn_name == "tool_as_text":
                            _target = raw_args.get("tool_name", "") or ""
                            _args_txt = raw_args.get("arguments_json", "") or "{}"
                            try:
                                _target_args = json.loads(_args_txt)
                            except json.JSONDecodeError:
                                _target_args = {}
                            if _target:
                                fn_name = _target
                                raw_args = _target_args
                                _trace_tools.append(f"{_target}(via text)")
                        conv_name = self._resolve_tool_skill(fn_name)
                        # Le skill doit s'exécuter dans le home de l'agent
                        # (sinon write_file écrit dans /tmp) et connaître son
                        # agent_id (journal d'usage, scopes, mémoire).
                        try:
                            from services.skill_manager import call_skill
                            _a_id = result.variables.get("agent_id", "")
                            if str(_a_id).startswith("agent_"):
                                _a_id = str(_a_id).split("_")[-1]
                            _home = self._agent_work_home(_a_id, result.variables)
                            tool_result = call_skill(
                                conv_name, raw_args, home=_home,
                                agent_id=str(_a_id),
                            )
                        except Exception as e2:
                            tool_result = {"ok": False, "error": str(e2)}
                        msgs.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": json.dumps(tool_result, default=str),
                        })
                        # ── Tool TERMINAL → clôturer la boucle tool_calls ──
                        # Un tool décisionnel (decoupe/done/release/verdict) met
                        # fin à l'étape : pas besoin de re-appeler le LLM. Les
                        # tools d'INFO (lecture/exploration) laissent la boucle
                        # continuer (l'agent peut demander plus de contexte).
                        if conv_name in _TERMINAL_SKILLS:
                            _terminal_hit = True

                    # On GARDE les tools d'un round à l'autre : après un
                    # write_file, le LLM doit pouvoir appeler it_is_done au
                    # round suivant. La boucle est bornée par range(15).
                    if _terminal_hit:
                        break  # décision prise → sortie de la boucle tool_calls
                    else:
                        # 15 rounds sans réponse textuelle → erreur
                        content = "Tool call limit exceeded"
                        tokens = 0
                content = response.content if hasattr(response, 'content') else str(response)
                if hasattr(response, 'usage') and isinstance(response.usage, dict):
                    tokens = response.usage.get("total_tokens", 0)
                if hasattr(response, 'budget') and isinstance(response.budget, dict):
                    result.budget = response.budget
                if stream_sink:
                    stream_sink(content)
                # agentic: always → AUCUN tool_call = ÉCHEC (on_error), pas une
                # boucle silencieuse. Le modèle devait produire un tool (ex.
                # review_verdict) mais a répondu en texte : c'est une erreur.
                if _agentic_req == "always" and tools and not _trace_tools \
                        and not (_need_translation and _trans_ok > 0):
                    err = (f"agentic=always requis mais aucun tool_call "
                           f"({p_ref}/{m_ref})")
                    logger.warning("llm/agentic_fail %s", err)
                    result.variables["_last_call_error"] = err
                    result.variables["_last_call_ok"] = False
                    if step.get("on_error"):
                        result.next_step_id = step.get("on_error")
                        return True
                    result.status = "failed"
                    result.end_reason = err
                    return False
        except BridgeError as e:
            return self._branch_on_error(step, result, f"LLM error: {e}")
        except AgentAbort:
            result.status = "aborted"
            result.end_reason = "Interrompu par signal kill"
            return False
        except Exception as e:
            return self._branch_on_error(step, result, f"LLM call error: {e}")

        if step.get("strip_fences"):
            content = _strip_code_fences(content)
        # Inclure la trace des tools appelés dans le contenu capturé (les
        # check_work_state qui suivent cherchent it_is_done/too_hard dans le
        # texte ; un round tool_call pur a un content vide sinon).
        if _trace_tools and content.strip():
            content = content + "\n[tools appelés: " + ", ".join(_trace_tools) + "]"
        elif _trace_tools:
            content = "[tools appelés: " + ", ".join(_trace_tools) + "]"
        if output_capture:
            result.variables[output_capture] = content
        result.messages.append({"role": "assistant", "content": content})
        result.content = content
        result.tokens_used += tokens
        # OBSERVATION à chaque step llm_call qui utilise une capacité :
        # écrit dans la table LÉGÈRE `capacite_log` (agentic /
        # agentic-translation, ok/fail). La table d'expérience
        # model_endpoint_provider_capacite est mise à jour PAR BATCH
        # (flush_capacite_log) — pas d'écriture directe à chaque appel.
        if tools and _agentic_req != "false":
            try:
                from modules.llm_manager.capacites import log_capacite
                from modules.sql.catalogue_repo import CatalogueDB
                _cap = "agentic-translation" if _need_translation else "agentic"
                _ok = _trans_ok > 0 if _need_translation else bool(_trace_tools)
                _cat = CatalogueDB()
                log_capacite(_cat, p_ref, m_ref, _cap, _ok)
                # Renseigner success_tool / success_translation sur le dernier
                # log LLM (le meta était passé avant de connaître le résultat).
                try:
                    _cat.conn.execute(
                        "UPDATE model_call_log SET meta_json = ? "
                        "WHERE id = (SELECT MAX(id) FROM model_call_log "
                        "WHERE provider_id = (SELECT id FROM catalogue_providers "
                        "WHERE ref = ?) AND model_id = (SELECT id FROM "
                        "catalogue_models WHERE ref = ?))",
                        (json.dumps({"agentic_mode": _agentic_mode,
                                     "agentic_tag": _agentic_req,
                                     "success_tool": bool(_trace_tools),
                                     "success_translation": _trans_ok > 0},
                                    ensure_ascii=False),
                         p_ref, m_ref))
                    _cat.conn.commit()
                except Exception:
                    pass
                _cat.close()
            except Exception:
                pass
        result.next_step_id = step.get("next")
        return True

    def _step_call(
        self, step: Dict, result: FSMResult,
        provider_ref: str = "", model_ref: str = "",
        **kwargs: Any,
    ) -> bool:
        """Appel unifié d'un skill (remplace tool_call). step: {type: call, fn, inputs, capture, on_error}.

        V0.6.23 : un échec de skill (ex. merge/commit/push git en erreur,
        agent introuvable…) est désormais détecté et remonté au FSM au lieu
        de passer inaperçu :
          - `result.variables["_last_call_ok"]` / `_last_call_error` exposent
            le résultat à l'étape suivante (exploitable par switch/model).
          - si `step["on_error"]` est défini, le FSM branche vers cette étape
            (l'agent peut réagir : résoudre un conflit, notifier…) ;
          - sinon le workflow s'arrête en `status="failed"` avec un motif
            explicite dans `end_reason`."""
        fn = step.get("fn", "")
        if not fn:
            result.status = "failed"
            result.end_reason = "call: 'fn' requis"
            return False
        # Résolution $var (scope hiérarchique) au niveau du STEP : fn ET
        # inputs. Ex. fn: catalogue.$lib.data, inputs: {$var_x: 'v'}.
        fn = self._scopes.resolve_text(fn)
        inputs = step.get("inputs", {})
        resolved = {k: self._scopes.resolve_value(v) for k, v in inputs.items()}
        resolved = {k: self._resolve(v, result.variables) if isinstance(v, str) else v
                    for k, v in resolved.items()}
        # Les templates de workspace restants ({{workspace_id}} etc.) : le
        # workspace du chat de dev est le défaut connu. Sans cette résolution,
        # les membres greedy appellent task_claim_next avec la chaîne littérale
        # "{{workspace_id}}" → « aucune tâche dispo » → rien ne s'exécute.
        def _resolve_residual(value):
            if isinstance(value, str) and "{{" in value:
                value = value.replace("{{workspace_id}}", "mw-dev-chat")
            return value
        resolved = {k: _resolve_residual(v) for k, v in resolved.items()}
        # Le provider/model du run DOIT être propagé aux skills appelés
        # (ex. workflow/autonomous@v1) quand le step ne les définit pas —
        # sinon le skill appelé auto-assigne (google/nvidia…) au lieu
        # d'honorer le provider explicitement demandé (team/delegate).
        if provider_ref and not resolved.get("provider_ref"):
            resolved["provider_ref"] = provider_ref
        if model_ref and not resolved.get("model_ref"):
            resolved["model_ref"] = model_ref
        # agent_id disponible pour les skills (memory/host/log)
        # Anti-spoof : un 'call' ne peut pas usurper l'identité d'un autre
        # agent — s'il fournit un agent_id différent, on force le sien.
        agent_id = result.variables.get("agent_id", "")
        if agent_id:
            # Forcer l'agent_id NUMÉRIQUE (pas le nom "agent_388") : les skills
            # git/workspace s'en servent pour le home et le clone.
            try:
                agent_id = int(str(agent_id).split("_")[-1]) if str(agent_id).startswith("agent_") else int(agent_id)
            except (ValueError, TypeError):
                pass
            if "agent_id" in resolved and str(resolved["agent_id"]) != str(agent_id):
                resolved["agent_id"] = agent_id
            elif "agent_id" not in resolved:
                resolved["agent_id"] = agent_id
        try:
            from services._common import mw_home
            if agent_id:
                home = str(mw_home() / "agent_home" / str(agent_id))
            else:
                home = self.tool_executor.home_root if self.tool_executor else "/tmp"
            # Scope skill : poussé autour de l'appel (le skill écrit dans SON
            # scope ; $var du skill remonte ensuite vers l'agent).
            self._scopes.push(f"skill:{fn}")
            try:
                # Sub-skill inline attachée au step → exécutée directement
                # (pas de lookup catalogue). Sinon skill du catalogue.
                if step.get("skill"):
                    from services.skill_manager import call_skill_spec
                    out = call_skill_spec(step["skill"], resolved, home,
                                          agent_id=str(agent_id),
                                          entrypoint=step.get("entrypoint", "main"))
                else:
                    # Foncteur : entrypoint par défaut 'main' (f() == f.main()),
                    # sinon step.entrypoint (ex. f.second()) — instance par agent.
                    from services.skill_manager import call_skill
                    out = call_skill(fn, resolved, home,
                                     agent_id=str(agent_id),
                                     entrypoint=step.get("entrypoint", "main"))
            finally:
                self._scopes.pop()
        except Exception as e:
            result.status = "failed"
            result.end_reason = f"call {fn}: {e}"
            return False

        ok, err = self._skill_outcome(out)
        result.variables["_last_call_ok"] = ok
        if not ok:
            result.variables["_last_call_error"] = err

        capture = step.get("capture", {})
        if capture:
            for out_key, var_name in capture.items():
                if out_key in out:
                    result.variables[var_name] = out[out_key]
            # Remplir result.content si vide : la sortie d'un skill autonome
            # (ex. workflow/autonomous@v1 → stdout) devient le contenu final
            # du membre — sinon les captures agent_call du leader sont vides.
            if not result.content:
                for out_key, var_name in capture.items():
                    val = result.variables.get(var_name)
                    if isinstance(val, str) and val.strip():
                        result.content = val
                        break
        result.messages.append({
            "role": "system",
            "content": f"[{fn}] {json.dumps(out, ensure_ascii=False)[:500]}",
        })

        if not ok:
            result.messages.append({
                "role": "system",
                "content": f"[call:{fn}] ÉCHEC: {err[:300]}",
            })
            on_error = step.get("on_error")
            if on_error:
                # Branche vers un gestionnaire d'erreur ; le FSM continue.
                result.next_step_id = on_error
                return True
            result.status = "failed"
            result.end_reason = f"call {fn} a échoué: {err[:200]}"
            result.next_step_id = step.get("next")
            return False

        result.next_step_id = step.get("next")
        return True

    @staticmethod
    def _skill_outcome(out: Any) -> tuple:
        """Renvoie `(ok, message_erreur)` pour le résultat d'un skill.

        Un skill est considéré en échec si :
          - `ok` vaut explicitement False, ou
          - `status` vaut "error"/"failed", ou
          - `exit_code` (présent) est != 0."""
        if not isinstance(out, dict):
            return True, ""
        if out.get("ok") is False:
            err = out.get("stderr") or out.get("stdout") or out.get("error") or ""
            if out.get("conflict"):
                err = "CONFLIT DE MERGE — " + err
            return False, str(err)
        if out.get("status") in ("error", "failed", "FAILED"):
            return False, str(out.get("error") or out.get("message") or out.get("stdout") or "")
        if isinstance(out.get("exit_code"), int) and out["exit_code"] != 0:
            return False, str(out.get("stderr") or out.get("stdout") or "")
        return True, ""

    def _step_tool_call(
        self, step: Dict, result: FSMResult,
        provider_ref: str = "", model_ref: str = "",
        **kwargs: Any,
    ) -> bool:
        """Appel d'outil système (déprécié — utiliser `type: call` à la place).

        Mappe les tools legacy vers les skills correspondants.
        """
        tool_name = step.get("tool", "")
        args = step.get("args", {})

        resolved_args = {}
        for k, v in args.items():
            resolved_args[k] = self._resolve(v, result.variables) \
                if isinstance(v, str) else v

        # Mapping backward-compat : tool -> skill
        TOOL_TO_SKILL = {
            "read_file": ("system/read_file@v1", {"path": "path"}),
            "write_file": ("system/write_file@v1", {"path": "path", "content": "content"}),
            "run_shell": ("system/run_shell@v1", {"command": "command"}),
            "shell_exec": ("shell/exec@v1", {"command": "command"}),
        }

        skill_ref, arg_map = TOOL_TO_SKILL.get(tool_name, ("", {}))
        if skill_ref:
            mapped_args = {skill_arg: resolved_args[tool_arg]
                           for tool_arg, skill_arg in arg_map.items()
                           if tool_arg in resolved_args}
            agent_id = result.variables.get("agent_id", "")
            if agent_id:
                if "agent_id" in mapped_args and mapped_args["agent_id"] != agent_id:
                    mapped_args["agent_id"] = agent_id
                elif "agent_id" not in mapped_args:
                    mapped_args["agent_id"] = agent_id
            try:
                from services.skill_manager import call_skill
                from services._common import mw_home
                if agent_id:
                    home = str(mw_home() / "agent_home" / str(agent_id))
                else:
                    home = self.tool_executor.home_root if self.tool_executor else "/tmp"
                out = call_skill(skill_ref, mapped_args, home)
                output_capture = step.get("output_capture")
                if output_capture:
                    if tool_name == "read_file" and "content" in out:
                        result.variables[output_capture] = out["content"]
                    elif tool_name == "run_shell":
                        result.variables[output_capture] = (
                            f"stdout:\n{out.get('stdout', '')}\n"
                            f"stderr:\n{out.get('stderr', '')}\n"
                            f"exit_code: {out.get('exit_code', -1)}"
                        )
                    elif tool_name == "write_file" and "result" in out:
                        result.variables[output_capture] = out["result"]
                result.messages.append({
                    "role": "system",
                    "content": f"[{tool_name}->{skill_ref}] "
                               f"{json.dumps(out, ensure_ascii=False)[:500]}",
                })
            except Exception as e:
                result.status = "failed"
                result.end_reason = f"Tool->skill error: {e}"
                return False
        else:
            # Fallback : legacy tool_executor
            if not self.tool_executor:
                result.status = "failed"
                result.end_reason = f"Tool '{tool_name}' inconnu et pas de ToolExecutor"
                return False
            try:
                output = self.tool_executor.execute(tool_name, resolved_args)
                output_capture = step.get("output_capture")
                if output_capture:
                    result.variables[output_capture] = output
                result.messages.append({
                    "role": "system",
                    "content": f"[{tool_name}] {output[:500]}",
                })
            except Exception as e:
                result.status = "failed"
                result.end_reason = f"Tool error: {e}"
                return False

        result.next_step_id = step.get("next")
        return True

    def _step_switch(
        self, step: Dict, result: FSMResult,
        provider_ref: str = "", model_ref: str = "",
        **kwargs: Any,
    ) -> bool:
        """Branchement conditionnel."""
        var_name = step.get("variable", "")
        raw_key = var_name.strip("{}").strip()
        _missing = object()
        raw = result.variables.get(raw_key, _missing)
        # Variable à accès dict imbriqué ({{task.repo}} avec task = dict) :
        # la clé "task.repo" n'existe pas dans variables (la clé "task" pointe
        # sur le dict) → on résout via _resolve qui navigue dans les dicts.
        if raw is _missing and "." in raw_key:
            raw = self._resolve(f"{{{{{raw_key}}}}}", result.variables)
        var_value = str(raw if raw is not _missing else "")

        for cond in step.get("conditions", []):
            operator = cond.get("operator", "EQUALS")
            # Alias symboliques (cohérence avec _eval_condition)
            operator = {"==": "EQUALS", "!=": "NOT_EQUALS", "contains": "CONTAINS",
                        ">": "GREATER", "<": "LESS"}.get(operator, operator)
            cond_value = str(cond.get("value", ""))
            matched = False

            if operator == "EQUALS":
                matched = var_value == cond_value
            elif operator == "NOT_EQUALS":
                matched = var_value != cond_value
            elif operator == "CONTAINS":
                matched = cond_value in var_value
            elif operator == "GREATER":
                try:
                    matched = float(var_value) > float(cond_value)
                except (ValueError, TypeError):
                    matched = False
            elif operator == "LESS":
                try:
                    matched = float(var_value) < float(cond_value)
                except (ValueError, TypeError):
                    matched = False

            if matched:
                result.next_step_id = cond.get("next")
                return True

        result.next_step_id = step.get("default")
        return True

    def _step_sleep(
        self, step: Dict, result: FSMResult,
        provider_ref: str = "", model_ref: str = "",
        **kwargs: Any,
    ) -> bool:
        """Pause déportée."""
        result.sleep_seconds = step.get("duration_seconds", 60)
        result.status = "sleeping"
        result.next_step_id = step.get("next")
        return False

    def _step_end(
        self, step: Dict, result: FSMResult,
        provider_ref: str = "", model_ref: str = "",
        **kwargs: Any,
    ) -> bool:
        """Fin du workflow — copie les variables capturées dans le résultat.

        Capture convention (key=source, value=target) :
          {team_outputs: content} → result.content = variables['team_outputs']
        """
        end_status = step.get("status", "SUCCESS")
        result.status = "success" if end_status == "SUCCESS" else "failed"
        result.end_reason = end_status
        capture = step.get("capture", {})
        for source_var, target_field in capture.items():
            val = result.variables.get(source_var, "")
            if isinstance(val, dict):
                val = json.dumps(val, ensure_ascii=False)
            elif not isinstance(val, str):
                val = str(val)
            if target_field == "content":
                result.content = val
            else:
                result.variables[target_field] = val
        return False

    def _step_set_variable(
        self, step: Dict, result: FSMResult,
        provider_ref: str = "", model_ref: str = "",
        **kwargs: Any,
    ) -> bool:
        """Définit une variable (scope hiérarchique).

        - name = "var_x"      → scope courant (feuille).
        - name = "agent.var_x" → scope nommé remonté (préfixe requis pour
          toucher les niveaux supérieurs).
        La valeur est résolue ($var + {{var}})."""
        name = step.get("name", "")
        value = self._resolve(step.get("value", ""), result.variables)
        try:
            self._scopes.set_scoped(name, value)
        except Exception as e:  # noqa: BLE001
            result.status = "failed"
            result.end_reason = f"set_variable: {e}"
            return False
        # reflète dans les variables du résultat (scope racine = agent)
        result.variables.update(self._scopes.snapshot())
        result.next_step_id = step.get("next")
        return True

    def _step_obj_call(
        self, step: Dict, result: FSMResult,
        provider_ref: str = "", model_ref: str = "",
        **kwargs: Any,
    ) -> bool:
        """Appelle un OBJET RUNTIME via le langage de résolution typée.

        step: {type: obj_call, path, capture, next, on_error}.

        ``path`` : une chaîne de résolution, résolue au niveau du step :
            team.chatroom.send({{msg}})          → envoie via l'objet team
            team.members.reduce_pattern(name=*)  → filtre les membres
            daemon.info()                        → infos du daemon restreint
            catalogue.$lib.check_home@v1         → skill du catalogue
        L'agent_id est pris du scope (variables) pour construire la racine
        `team`. Retourne None.return si le récepteur était vide (no-op)."""
        path = step.get("path", "")
        if not path:
            result.status = "failed"
            result.end_reason = "obj_call: 'path' requis"
            return False
        # résout {{var}} et $var dans le path (scope hiérarchique)
        try:
            path = self._resolve(path, result.variables)
        except Exception as e:  # noqa: BLE001
            result.status = "failed"
            result.end_reason = f"obj_call: résolution path: {e}"
            return False
        agent_id = result.variables.get("agent_id", "")
        # PERMISSIONS DAEMON uniquement : l'accès au daemon (daemon.*) est
        # borné par privileges (fail-safe). Les objets internes
        # (team.*, catalogue.*) n'ont PAS besoin de permission.
        try:
            from services.runtime_permissions import check_access
            if not check_access(agent_id, path):
                if step.get("on_error"):
                    return self._branch_on_error(
                        step, result, f"obj_call: accès daemon refusé ({path})")
                result.status = "failed"
                result.end_reason = f"obj_call: accès daemon refusé ({path})"
                return False
        except Exception:
            pass
        try:
            from services.catalogue_objects import build_resolution_namespace
            from modules.agent_graph_utils.resolution import PathEvaluator
            rns = build_resolution_namespace(
                int(agent_id) if str(agent_id).isdigit() else None)
            root = None
            if path.startswith("team."):
                root = rns["team"]
            elif path.startswith("daemon."):
                root = rns["daemon"]
            elif path.startswith("catalogue."):
                from services.catalogue_runtime import make_catalogue
                root = make_catalogue(agent_id=str(agent_id))
            else:
                root = rns["team"]   # défaut : contexte team de l'agent
            out = PathEvaluator(root).evaluate(path)
        except Exception as e:  # noqa: BLE001
            if step.get("on_error"):
                return self._branch_on_error(step, result, f"obj_call: {e}")
            result.status = "failed"
            result.end_reason = f"obj_call {path}: {e}"
            return False

        # capture du résultat dans les variables
        capture = step.get("capture", {})
        if capture and out is not None:
            for out_key, var_name in capture.items():
                result.variables[var_name] = out
        result.messages.append({
            "role": "system",
            "content": f"[obj_call:{path}] "
                       f"{json.dumps(out, ensure_ascii=False, default=str)[:500]}",
        })
        result.next_step_id = step.get("next")
        return True

    def _step_spawn(
        self, step: Dict, result: FSMResult,
        provider_ref: str = "", model_ref: str = "",
        **kwargs: Any,
    ) -> bool:
        """Phase 5 : spawn d'un agent enfant (souvent occupation `disparate`).

        Le handler (fourni par l'Agent) crée, exécute et renvoie dormir
        l'agent enfant, puis renvoie son résultat. Le contenu est capturé
        dans `output_capture`.
        """
        if self._spawn_handler is None:
            result.status = "failed"
            result.end_reason = "spawn_handler non configuré"
            return False
        spec = {
            "name": step.get("name", f"child_{result.iterations}"),
            "role": step.get("role", "spawned"),
            "occupation": step.get("occupation", "disparate"),
            "resources": step.get("resources"),
            "config": step.get("config"),
            "provider_ref": step.get("provider_ref", ""),
            "model_ref": step.get("model_ref", ""),
        }
        request = self._resolve(step.get("request", ""), result.variables)
        try:
            out = self._spawn_handler(spec, request)
        except Exception as e:
            result.status = "failed"
            result.end_reason = f"spawn error: {e}"
            return False
        if out.get("status") != "ok":
            result.status = "failed"
            result.end_reason = f"spawn échoué: {out.get('error')}"
            return False
        child_content = (out.get("result") or {}).get("content", "")
        output_capture = step.get("output_capture")
        if output_capture:
            result.variables[output_capture] = child_content
        result.messages.append({
            "role": "system",
            "content": f"[spawn:{spec['name']}] {child_content[:500]}",
        })
        result.next_step_id = step.get("next")
        return True

    def _step_handoff(
        self, step: Dict, result: FSMResult,
        provider_ref: str = "", model_ref: str = "",
        **kwargs: Any,
    ) -> bool:
        """Phase 5 : succession — transfert de session vers un agent cible.

        `to` = nom ou id de l'agent successeur. Le handler (lié à l'agent
        courant) effectue le transfert de variables/état et chaîne successor_id.
        """
        if self._handoff_handler is None:
            result.status = "failed"
            result.end_reason = "handoff_handler non configuré"
            return False
        to = step.get("to")
        if not to:
            result.status = "failed"
            result.end_reason = "handoff: cible 'to' requise"
            return False
        try:
            out = self._handoff_handler(to)
        except Exception as e:
            result.status = "failed"
            result.end_reason = f"handoff error: {e}"
            return False
        result.variables["_handoff"] = out
        result.next_step_id = step.get("next")
        return True

    def _step_agent_call(
        self, step: Dict, result: FSMResult,
        provider_ref: str = "", model_ref: str = "",
        **kwargs: Any,
    ) -> bool:
        """Appel synchrone d'un entrypoint d'un autre agent.

        step: {type: agent_call, agent, entrypoint, inputs, capture, on_error}.
        Délègue à `agent_call_handler` fourni par l'Agent (service.py).
        """
        if self._agent_call_handler is None:
            result.status = "failed"
            result.end_reason = "agent_call_handler non configuré"
            return False
        agent_name = step.get("agent")
        if not agent_name:
            result.status = "failed"
            result.end_reason = "agent_call: 'agent' requis"
            return False
        ep = step.get("entrypoint", "main")
        inputs = {
            k: self._resolve(v, result.variables)
            for k, v in step.get("inputs", {}).items()
        }
        try:
            out = self._agent_call_handler(agent_name, ep, inputs)
        except Exception as e:
            if step.get("on_error"):
                return self._branch_on_error(step, result, f"agent_call error: {e}")
            result.status = "failed"
            result.end_reason = f"agent_call error: {e}"
            return False

        if out.get("status") not in ("ok", "success"):
            if step.get("on_error"):
                return self._branch_on_error(step, result,
                                             out.get("error", "agent_call failed"))
            result.status = "failed"
            result.end_reason = f"agent_call échoué: {out.get('error', 'inconnu')}"
            return False

        # Capture la sortie dans les variables
        capture = step.get("capture", {})
        for out_key, var_name in capture.items():
            result.variables[var_name] = out.get(out_key, out.get("content", ""))

        result.next_step_id = step.get("next")
        return True

    # ── Sub-agents : thread enfant semi-autonome, home partagé ──

    def _step_sub_agent(
        self, step: Dict, result: FSMResult,
        provider_ref: str = "", model_ref: str = "",
        stream_sink: Optional[Any] = None, **kwargs: Any,
    ) -> bool:
        """Lance un SUB-AGENT en thread enfant, home partagé.

        step: {type: sub_agent, agent, entrypoint, request, request_mode,
               respond_mode, timeout, capture, next}.

        Le sub-agent est un agent INLINE (déf dans self._sub_agents) exécuté
        dans une thread daemon : il a son propre parcours FSM + son propre log
        (même home que le parent), et communique via 2 files :
          - request  : message envoyé au sub-agent (bloquant : attend son ack ;
            non-bloquant : fire-and-forget)
          - respond  : réponse attendue du sub-agent (bloquant : timeout ;
            non-bloquant : lecture immédiate, peut être None)

        Le sub-agent peut utiliser SON propre LLM ou celui de son propriétaire :
        si la def du sub-agent déclare `provider_ref`/`model_ref`, elle prime ;
        sinon le sub-agent hérite de ceux du parent (provider_ref/model_ref).
        """
        agent = step.get("agent", "")
        sub = (self._sub_agents or {}).get(agent)
        if not sub:
            result.status = "failed"
            result.end_reason = f"sub_agent: '{agent}' introuvable dans sub_agents"
            return False
        ep_name = step.get("entrypoint", "main")
        ep = (sub.get("entrypoints") or {}).get(ep_name)
        steps = (ep or {}).get("steps") or []
        if not steps:
            result.status = "failed"
            result.end_reason = f"sub_agent '{agent}': entrypoint '{ep_name}' vide"
            return False

        # Provider/model : ceux du sub-agent s'ils sont déclarés, sinon hérités.
        sub_provider = sub.get("provider_ref") or step.get("provider_ref") or provider_ref
        sub_model = sub.get("model_ref") or step.get("model_ref") or model_ref
        request = self._resolve(step.get("request", ""), result.variables)
        request_mode = step.get("request_mode", "blocking")
        respond_mode = step.get("respond_mode", "blocking")
        timeout = step.get("timeout", 30)
        # Home : PARTAGÉ avec le parent (le sub-agent n'a pas de nouveau home).
        home = getattr(self, "_home", "") or (
            self.tool_executor.home_root if self.tool_executor else "")

        link = SubAgentLink()

        def _run_child():
            child = FSMInterpreter(bridge=self.bridge,
                                   tool_executor=self.tool_executor,
                                   max_iterations=self.max_iterations)
            try:
                child_result = child.run(
                    workflow=ep,
                    messages=[{"role": "system",
                               "content": f"[sub_agent:{agent}] {request[:500]}"}],
                    variables={**result.variables,
                               **{"_sub_parent": agent, "agent_id":
                                  result.variables.get("agent_id", "")}},
                    provider_ref=sub_provider, model_ref=sub_model,
                    stream_sink=stream_sink,
                    sub_agents=self._sub_agents,
                    sub_link=link,
                    home=home,
                )
                link.result = child_result
            except Exception as e:  # noqa: BLE001
                link.error = str(e)
            finally:
                link.done.set()

        t = threading.Thread(target=_run_child, daemon=True)
        t.start()

        # Envoyer la request (bloquante : attendre l'ack de consommation).
        link.request_q.put(request)
        if request_mode == "blocking":
            if not link.request_ack.wait(timeout):
                result.status = "failed"
                result.end_reason = f"sub_agent '{agent}': request non consommée (timeout {timeout}s)"
                return False

        # Attendre la respond (bloquante : timeout ; non-bloquante : immédiate).
        response = None
        try:
            if respond_mode == "blocking":
                response = link.respond_q.get(timeout=timeout)
            else:
                try:
                    response = link.respond_q.get_nowait()
                except _queue.Empty:
                    response = None
        except _queue.Empty:
            result.status = "failed"
            result.end_reason = f"sub_agent '{agent}': pas de respond (timeout {timeout}s)"
            return False

        capture = step.get("capture", {})
        if capture and response is not None:
            if isinstance(response, dict):
                for out_key, var_name in capture.items():
                    result.variables[var_name] = response.get(out_key, "")
            else:
                # capture: {result: nom_var} → la valeur brute
                for _ok, var_name in capture.items():
                    result.variables[var_name] = response
        result.messages.append({
            "role": "system",
            "content": f"[sub_agent:{agent}] respond: "
                       f"{json.dumps(response, ensure_ascii=False)[:500]}",
        })
        result.next_step_id = step.get("next")
        return True

    def _step_sub_await_request(
        self, step: Dict, result: FSMResult,
        provider_ref: str = "", model_ref: str = "",
        **kwargs: Any,
    ) -> bool:
        """(côté SUB-AGENT) Attend/reçoit la request du parent depuis la file.

        step: {type: sub_await_request, mode: blocking|non_blocking, timeout,
               capture: {message: var}}.
        Ack automatique au parent quand la request est consommée.
        """
        if self._sub_link is None:
            result.status = "failed"
            result.end_reason = "sub_await_request: pas de liaison sub-agent"
            return False
        mode = step.get("mode", "blocking")
        timeout = step.get("timeout", 30)
        try:
            if mode == "blocking":
                request = self._sub_link.request_q.get(timeout=timeout)
            else:
                try:
                    request = self._sub_link.request_q.get_nowait()
                except _queue.Empty:
                    request = None
        except _queue.Empty:
            result.status = "failed"
            result.end_reason = f"sub_await_request: pas de request (timeout {timeout}s)"
            return False
        self._sub_link.request_ack.set()   # le parent peut continuer
        for out_key, var_name in (step.get("capture") or {}).items():
            result.variables[var_name] = request
        result.next_step_id = step.get("next")
        return True

    def _step_sub_respond(
        self, step: Dict, result: FSMResult,
        provider_ref: str = "", model_ref: str = "",
        **kwargs: Any,
    ) -> bool:
        """(côté SUB-AGENT) Envoie la réponse au parent via la file respond.

        step: {type: sub_respond, data, mode: blocking|non_blocking}.
        """
        if self._sub_link is None:
            result.status = "failed"
            result.end_reason = "sub_respond: pas de liaison sub-agent"
            return False
        data = self._resolve(step.get("data", ""), result.variables)
        mode = step.get("mode", "blocking")
        try:
            if mode == "blocking":
                self._sub_link.respond_q.put(data)
            else:
                try:
                    self._sub_link.respond_q.put_nowait(data)
                except _queue.Full:
                    pass
        except Exception as e:  # noqa: BLE001
            result.status = "failed"
            result.end_reason = f"sub_respond: {e}"
            return False
        result.next_step_id = step.get("next")
        return True

    # ── Gestion d'erreur commune (llm_call / call / tool_call) ──

    def _branch_on_error(self, step: Dict, result: "FSMResult", msg: str) -> bool:
        """Échec d'un step : branche vers `on_error` si défini, sinon échoue.

        Expose `_last_error` (+ `_last_call_ok=False`) pour l'étape suivante.
        Retourne True si le FSM doit continuer (branche on_error), False sinon."""
        result.variables["_last_error"] = msg
        result.variables["_last_call_ok"] = False
        on_error = step.get("on_error")
        if on_error:
            result.next_step_id = on_error
            return True
        result.status = "failed"
        result.end_reason = msg
        return False

    # ── Contrôle de boucle ─────────────────────────────

    def _step_break(self, step: Dict, result: "FSMResult", **kwargs: Any) -> bool:
        """Sort de la boucle englobante (consommé par _run_body/for/while)."""
        result._loop_ctl = "break"
        return False

    def _step_continue(self, step: Dict, result: "FSMResult", **kwargs: Any) -> bool:
        """Passe à l'itération suivante de la boucle englobante."""
        result._loop_ctl = "continue"
        return False

    # ── Boucles (corps imbriqué) ───────────────────────

    @staticmethod
    def _eval_condition(cond: Dict, variables: Dict) -> bool:
        """Évalue une condition {variable, operator, value}.

        operator ∈ EQUALS | NOT_EQUALS | CONTAINS | GREATER | LESS | TRUTHY
        (+ alias symboliques == | != | > | < | contains).
        TRUTHY (défaut si pas de value) : la variable est non vide / non nulle.
        """
        if not cond:
            return False
        raw = variables.get(cond.get("variable", "").strip("{}").strip(), "")
        operator = cond.get("operator", "TRUTHY" if "value" not in cond else "EQUALS")
        # Alias symboliques → équivalents textuels (cohérence avec _step_switch)
        operator = {"==": "EQUALS", "!=": "NOT_EQUALS", "contains": "CONTAINS",
                    ">": "GREATER", "<": "LESS"}.get(operator, operator)
        if operator == "TRUTHY":
            return bool(raw) and str(raw).lower() not in ("false", "0", "")
        var_value = str(raw)
        cond_value = str(cond.get("value", ""))
        if operator == "EQUALS":
            return var_value == cond_value
        if operator == "NOT_EQUALS":
            return var_value != cond_value
        if operator == "CONTAINS":
            return cond_value in var_value
        if operator in ("GREATER", "LESS"):
            try:
                a, b = float(var_value), float(cond_value)
            except (ValueError, TypeError):
                return False
            return a > b if operator == "GREATER" else a < b
        return False

    @staticmethod
    def _body_steps(step: Dict) -> List[Dict]:
        """Extrait les steps du corps d'une boucle (body: {steps:[...]} ou [...])."""
        body = step.get("body")
        if isinstance(body, dict):
            return body.get("steps", []) or []
        if isinstance(body, list):
            return body
        return []

    def _run_body(
        self, body_steps: List[Dict], result: "FSMResult",
        provider_ref: str, model_ref: str, stream_sink: Optional[Any],
    ) -> str:
        """Exécute une passe du corps de boucle (partage variables/messages).

        Retourne un code de contrôle :
          - 'normal'   : le corps est « tombé » à court d'étapes (itération OK) ;
          - 'break'    : un step `break` a été rencontré → sortir de la boucle ;
          - 'continue' : un step `continue` → itération suivante ;
          - 'stop'     : le workflow doit s'arrêter (status ≠ running / `end`)."""
        if not body_steps:
            return "normal"
        sub_by_id = {s["id"]: s for s in body_steps}
        cur = self._find_entry_point(body_steps)
        max_iter = getattr(self, "_max_iter", self.max_iterations)
        while cur:
            if result.iterations >= max_iter:
                result.status = "failed"
                result.end_reason = f"Limite d'itérations atteinte ({max_iter})"
                return "stop"
            step = sub_by_id.get(cur)
            if not step:
                result.status = "failed"
                result.end_reason = f"Étape '{cur}' introuvable (corps de boucle)"
                return "stop"
            result.iterations += 1
            stype = step.get("type", "")
            result.next_step_id = None
            handler = getattr(self, f"_step_{stype}", None)
            if handler is None:
                result.status = "failed"
                result.end_reason = f"Type d'étape inconnu: {stype}"
                return "stop"
            cont = handler(step, result, provider_ref=provider_ref,
                           model_ref=model_ref, stream_sink=stream_sink)
            if result._loop_ctl:
                ctl = result._loop_ctl
                result._loop_ctl = None
                return ctl
            if result.status != "running":
                return "stop"
            if not cont:
                return "stop"
            if self._lifecycle_mgr:
                self._lifecycle_mgr.publish("post_step", step=step, step_id=cur,
                                            status=result.status,
                                            variables=dict(result.variables))
            cur = result.next_step_id
        return "normal"

    def _step_while(
        self, step: Dict, result: "FSMResult",
        provider_ref: str = "", model_ref: str = "",
        stream_sink: Optional[Any] = None, **kwargs: Any,
    ) -> bool:
        """Boucle conditionnelle : exécute le corps tant que `condition` est vraie."""
        body_steps = self._body_steps(step)
        cond = step.get("condition", {})
        max_iter = getattr(self, "_max_iter", self.max_iterations)
        while self._eval_condition(cond, result.variables):
            if result.iterations >= max_iter:
                result.status = "failed"
                result.end_reason = f"Limite d'itérations atteinte ({max_iter})"
                return False
            code = self._run_body(body_steps, result, provider_ref, model_ref, stream_sink)
            if code == "stop":
                return False
            if code == "break":
                break
        result.next_step_id = step.get("next")
        return True

    def _step_for(
        self, step: Dict, result: "FSMResult",
        provider_ref: str = "", model_ref: str = "",
        stream_sink: Optional[Any] = None, **kwargs: Any,
    ) -> bool:
        """Boucle bornée : itère sur une plage (start/end/step) ou une liste (items).

        La variable `var` reçoit la valeur courante à chaque itération et est
        disponible dans le corps via {{var}}."""
        body_steps = self._body_steps(step)
        var = step.get("var", "i")
        # Mode liste
        if "items" in step:
            items = step.get("items")
            if isinstance(items, str):
                name = items.strip().strip("{}").strip()
                items = result.variables.get(name, [])
            if not isinstance(items, (list, tuple)):
                items = []
            values: List[Any] = list(items)
        else:
            # Mode plage
            def _num(v, default):
                try:
                    return int(v)
                except (ValueError, TypeError):
                    return default
            start = _num(step.get("start", 0), 0)
            end = _num(step.get("end", 0), 0)
            stride = _num(step.get("step", 1), 1) or 1
            values = list(range(start, end, stride))
        max_iter = getattr(self, "_max_iter", self.max_iterations)
        for val in values:
            if result.iterations >= max_iter:
                result.status = "failed"
                result.end_reason = f"Limite d'itérations atteinte ({max_iter})"
                return False
            result.variables[var] = val
            code = self._run_body(body_steps, result, provider_ref, model_ref, stream_sink)
            if code == "stop":
                return False
            if code == "break":
                break
        result.next_step_id = step.get("next")
        return True

    def _step_if(
        self, step: Dict, result: "FSMResult",
        provider_ref: str = "", model_ref: str = "",
        stream_sink: Optional[Any] = None, **kwargs: Any,
    ) -> bool:
        """Condition simple : si vrai → exécute le corps ; sinon → next."""
        cond = step.get("condition", {})
        if self._eval_condition(cond, result.variables):
            body_steps = self._body_steps(step)
            if body_steps:
                code = self._run_body(body_steps, result, provider_ref, model_ref, stream_sink)
                if code == "stop":
                    return False
        result.next_step_id = step.get("next")
        return True

    def _step_group(
        self, step: Dict, result: "FSMResult",
        provider_ref: str = "", model_ref: str = "",
        stream_sink: Optional[Any] = None, **kwargs: Any,
    ) -> bool:
        """Groupe : exécute le corps séquentiellement, puis next."""
        body_steps = self._body_steps(step)
        if body_steps:
            code = self._run_body(body_steps, result, provider_ref, model_ref, stream_sink)
            if code == "stop":
                return False
        result.next_step_id = step.get("next")
        return True

    # ── Utils ──────────────────────────────────────────

    def _resolve(self, value: str, variables: Dict) -> str:
        """Remplace {{variable}} et $var dans une chaîne par leurs valeurs.

        Supporte les accès dict/attributs : {{issue.description}},
        {{task.task_id}}, {{task.title}}... Retourne la valeur str ou laisse
        le placeholder si introuvable.
        """
        import re

        def _lookup(path: str):
            parts = path.split(".")
            cur = variables
            for p in parts:
                if isinstance(cur, dict) and p in cur:
                    cur = cur[p]
                elif hasattr(cur, p):
                    cur = getattr(cur, p)
                else:
                    return None
            return cur

        def _repl(m):
            val = _lookup(m.group(1))
            return str(val) if val is not None else m.group(0)

        # $var (scope hiérarchique) résolu en premier, puis {{var}}
        try:
            value = self._scopes.resolve_text(value)
        except Exception:
            pass
        return re.sub(r"\{\{([\w.]+)\}\}", _repl, value)