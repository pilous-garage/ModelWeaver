"""FSM Interpreter — Moteur universel d'exécution d'agents.

Remplace l'ancien Worker. Exécute un workflow (graphe d'étapes) où
`llm_call` utilise le LiteLLMBridge (pas d'HTTP direct), et `tool_call`
délègue au ToolExecutor.

Étapes supportées (Phase 2) :
  llm_call     → Bridge.chat(provider_ref, model_ref, messages, ...)
  tool_call    → ToolExecutor.execute(tool_name, args)
  switch       → Branchement conditionnel sur variables
  sleep        → Pause déportée (retour SLEEPING)
  end          → Fin du workflow
  set_variable → Définit une variable
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

from modules.llm_manager.litellm_bridge import LiteLLMBridge
from modules.llm_manager.base_bridge import BridgeError
from AgentFrameWork.tool_executor import ToolExecutor

logger = logging.getLogger("modelweaver.fsm")


class AgentAbort(Exception):
    """Levée par un signal_check (kill) pour interrompre le FSM."""


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
        }


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
        bridge: Optional[LiteLLMBridge] = None,
        tool_executor: Optional[ToolExecutor] = None,
        max_iterations: int = 100,
    ):
        self.bridge = bridge or LiteLLMBridge()
        self.tool_executor = tool_executor or ToolExecutor(workspace_root="/tmp")
        self.max_iterations = max_iterations

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
        lifecycle_mgr: Optional[Any] = None,
    ) -> FSMResult:
        """Exécute le workflow."""
        result = FSMResult()
        result.messages = list(messages)
        result.variables = dict(variables or {})
        self._signal_check = signal_check
        self._spawn_handler = spawn_handler
        self._handoff_handler = handoff_handler
        self._lifecycle_mgr = lifecycle_mgr

        steps = workflow.get("steps", [])
        steps_by_id = {s["id"]: s for s in steps}
        max_iter = workflow.get("max_iterations", self.max_iterations)

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

        # Construire les messages
        msgs = list(result.messages)
        if skill_prompt:
            msgs.append({"role": "system", "content": skill_prompt})

        p_ref = step.get("provider_ref") or provider_ref
        m_ref = step.get("model_ref") or model_ref
        temperature = step.get("temperature", 0.7)
        max_tokens = step.get("max_tokens", 4096)

        try:
            content = ""
            tokens = 0
            if stream_sink is not None:
                # Streaming : diffusion chunk par chunk
                for delta in self.bridge.chat_stream(
                    provider_ref=p_ref, model_ref=m_ref,
                    messages=msgs, temperature=temperature, max_tokens=max_tokens,
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
            else:
                response = self.bridge.chat(
                    provider_ref=p_ref, model_ref=m_ref,
                    messages=msgs, temperature=temperature, max_tokens=max_tokens,
                )
                content = response.content if hasattr(response, 'content') else str(response)
                if hasattr(response, 'usage') and isinstance(response.usage, dict):
                    tokens = response.usage.get("total_tokens", 0)
                if hasattr(response, 'budget') and isinstance(response.budget, dict):
                    result.budget = response.budget
                if stream_sink:
                    stream_sink(content)
        except BridgeError as e:
            result.status = "failed"
            result.end_reason = f"LLM error: {e}"
            return False
        except AgentAbort:
            result.status = "aborted"
            result.end_reason = "Interrompu par signal kill"
            return False
        except Exception as e:
            result.status = "failed"
            result.end_reason = f"LLM call error: {e}"
            return False

        if output_capture:
            result.variables[output_capture] = content
        result.messages.append({"role": "assistant", "content": content})
        result.content = content
        result.tokens_used += tokens
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
        inputs = step.get("inputs", {})
        resolved = {k: self._resolve(v, result.variables) if isinstance(v, str) else v
                    for k, v in inputs.items()}
        # agent_id disponible pour les skills (memory/host/log)
        agent_id = result.variables.get("agent_id", "")
        if agent_id and "agent_id" not in resolved:
            resolved["agent_id"] = agent_id
        try:
            from services.skill_manager import call_skill
            from services._common import mw_home
            if agent_id:
                ws = str(mw_home() / "memagent" / str(agent_id))
            else:
                ws = self.tool_executor.workspace_root if self.tool_executor else "/tmp"
            out = call_skill(fn, resolved, ws)
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
            err = out.get("stderr") or out.get("error") or ""
            if out.get("conflict"):
                err = "CONFLIT DE MERGE — " + err
            return False, str(err)
        if out.get("status") in ("error", "failed", "FAILED"):
            return False, str(out.get("error") or out.get("message") or "")
        if isinstance(out.get("exit_code"), int) and out["exit_code"] != 0:
            return False, str(out.get("stderr") or "")
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
        }

        skill_ref, arg_map = TOOL_TO_SKILL.get(tool_name, ("", {}))
        if skill_ref:
            mapped_args = {skill_arg: resolved_args[tool_arg]
                           for tool_arg, skill_arg in arg_map.items()
                           if tool_arg in resolved_args}
            agent_id = result.variables.get("agent_id", "")
            if agent_id and "agent_id" not in mapped_args:
                mapped_args["agent_id"] = agent_id
            try:
                from services.skill_manager import call_skill
                from services._common import mw_home
                if agent_id:
                    ws = str(mw_home() / "memagent" / str(agent_id))
                else:
                    ws = self.tool_executor.workspace_root if self.tool_executor else "/tmp"
                out = call_skill(skill_ref, mapped_args, ws)
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
        var_value = str(result.variables.get(var_name, ""))

        for cond in step.get("conditions", []):
            operator = cond.get("operator", "EQUALS")
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
        """Fin du workflow."""
        end_status = step.get("status", "SUCCESS")
        result.status = "success" if end_status == "SUCCESS" else "failed"
        result.end_reason = end_status
        return False

    def _step_set_variable(
        self, step: Dict, result: FSMResult,
        provider_ref: str = "", model_ref: str = "",
        **kwargs: Any,
    ) -> bool:
        """Définit une variable."""
        name = step.get("name", "")
        value = self._resolve(step.get("value", ""), result.variables)
        result.variables[name] = value
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

    # ── Utils ──────────────────────────────────────────

    def _resolve(self, value: str, variables: Dict) -> str:
        """Remplace {{variable}} dans une chaîne par sa valeur."""
        result = value
        for k, v in variables.items():
            result = result.replace(f"{{{{{k}}}}}", str(v))
        return result