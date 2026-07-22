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
  for          → Boucle bornée (range ou liste) sur un corps imbriqué
  while        → Boucle conditionnelle sur un corps imbriqué
  break        → Sort de la boucle en cours
  continue     → Passe à l'itération suivante de la boucle en cours
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

from modules.llm_manager.litellm_bridge import LiteLLMBridge
from modules.llm_manager.base_bridge import BridgeError
from AgentFrameWork.tool_executor import ToolExecutor

logger = logging.getLogger("modelweaver.fsm")

import re

_FENCE_RE = re.compile(r"^\s*```[^\n]*\n(.*?)\n?```\s*$", re.DOTALL)


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
        agent_call_handler: Optional[Any] = None,
        lifecycle_mgr: Optional[Any] = None,
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
            # agent_id disponible pour le journal d'usage (si l'agent l'a
            # fourni via {{agent_id}} ou le contexte d'exécution).
            _agent_id = result.variables.get("agent_id", "")
            if stream_sink is not None:
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
            else:
                timeout = step.get("timeout")
                use_fallback = step.get("fallback", False)
                if timeout:
                    # Appel LLM résilient : si le LLM ne répond pas dans
                    # `timeout` secondes, demande un autre LLM au gestionnaire
                    # de LLM (LLMManager.assign_llm) et réessaie.
                    from modules.llm_manager.resilient import resilient_chat
                    try:
                        response = resilient_chat(
                            p_ref, m_ref, msgs,
                            timeout=int(timeout), fallback=use_fallback,
                            max_tokens=max_tokens, temperature=temperature,
                            agent_id=_agent_id or None,
                        )
                    except BridgeError as e:
                        return self._branch_on_error(step, result, f"LLM error: {e}")
                    except Exception as e:
                        return self._branch_on_error(step, result, f"LLM call error: {e}")
                    p_ref, m_ref = (getattr(response, "provider_used", p_ref),
                                    getattr(response, "model_used", m_ref))
                    result.variables["_llm_provider"] = p_ref
                    result.variables["_llm_model"] = m_ref
                    result.variables["_llm_fallbacks"] = getattr(response, "fallbacks", 0)
                else:
                    response = self.bridge.chat(
                        provider_ref=p_ref, model_ref=m_ref,
                        messages=msgs, temperature=temperature, max_tokens=max_tokens,
                        agent_id=_agent_id or None,
                    )
                content = response.content if hasattr(response, 'content') else str(response)
                if hasattr(response, 'usage') and isinstance(response.usage, dict):
                    tokens = response.usage.get("total_tokens", 0)
                if hasattr(response, 'budget') and isinstance(response.budget, dict):
                    result.budget = response.budget
                if stream_sink:
                    stream_sink(content)
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
        # Anti-spoof : un 'call' ne peut pas usurper l'identité d'un autre
        # agent — s'il fournit un agent_id différent, on force le sien.
        agent_id = result.variables.get("agent_id", "")
        if agent_id:
            if "agent_id" in resolved and resolved["agent_id"] != agent_id:
                resolved["agent_id"] = agent_id
            elif "agent_id" not in resolved:
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
            if agent_id:
                if "agent_id" in mapped_args and mapped_args["agent_id"] != agent_id:
                    mapped_args["agent_id"] = agent_id
                elif "agent_id" not in mapped_args:
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

        operator ∈ EQUALS | NOT_EQUALS | CONTAINS | GREATER | LESS | TRUTHY.
        TRUTHY (défaut si pas de value) : la variable est non vide / non nulle.
        """
        if not cond:
            return False
        raw = variables.get(cond.get("variable", ""), "")
        operator = cond.get("operator", "TRUTHY" if "value" not in cond else "EQUALS")
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
        """Remplace {{variable}} dans une chaîne par sa valeur."""
        result = value
        for k, v in variables.items():
            result = result.replace(f"{{{{{k}}}}}", str(v))
        return result