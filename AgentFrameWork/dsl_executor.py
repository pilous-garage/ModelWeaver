"""DSL Executor — Exécute un workflow d'agent chaîné par `next`.

Contrairement au PipelineExecutor (qui transforme linéairement les messages),
le DSLExecutor parcours un graphe d'étapes avec branchage (switch), pauses
(sleep → wakeup_calls), appels LLM (llm_call), fin (end), signaux de
succession (signal_successor), sauvegarde d'état (save_state) et connexions.

Les étapes de transformation (concat, extract_context, if, set_variable...)
sont déléguées au PipelineExecutor.
"""

import json
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional

from AgentFrameWork.pipeline_executor import PipelineExecutor, PipelineError

logger = logging.getLogger("modelweaver.dsl")


class WorkflowResult:
    """Résultat d'exécution d'un workflow."""

    def __init__(self):
        self.status: str = "running"  # running, success, failed, sleeping, terminated
        self.variables: Dict[str, Any] = {}
        self.messages: List[Dict[str, str]] = []
        self.next_step_id: Optional[str] = None
        self.sleep_seconds: Optional[int] = None
        self.successor_role: Optional[str] = None
        self.successor_config: Optional[Dict] = None
        self.end_reason: Optional[str] = None
        self.iterations: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "variables": self.variables,
            "iterations": self.iterations,
            "successor_role": self.successor_role,
            "end_reason": self.end_reason,
            "sleep_seconds": self.sleep_seconds,
            "next_step_id": self.next_step_id,
        }


class DSLExecutor:
    """Exécute un workflow d'agent étape par étape.

    Usage:
        executor = DSLExecutor(pipeline_executor, llm_call_fn, db_hook)
        result = executor.run(workflow_dict, messages, variables)
    """

    def __init__(
        self,
        pipeline_executor: Optional[PipelineExecutor] = None,
        llm_call_fn: Optional[Callable] = None,
        save_state_fn: Optional[Callable] = None,
        signal_successor_fn: Optional[Callable] = None,
        connect_fn: Optional[Callable] = None,
        tool_call_fn: Optional[Callable] = None,
    ):
        self.pipeline = pipeline_executor or PipelineExecutor()
        self.llm_call = llm_call_fn
        self.save_state = save_state_fn
        self.signal_successor = signal_successor_fn
        self.connect_fn = connect_fn
        self.tool_call = tool_call_fn

    def run(
        self,
        workflow: Dict[str, Any],
        messages: List[Dict[str, str]],
        variables: Optional[Dict[str, Any]] = None,
        start_step_id: Optional[str] = None,
    ) -> WorkflowResult:
        """Exécute le workflow à partir d'une étape donnée.

        Retourne un WorkflowResult. Si le statut est 'sleeping', le caller
        doit créer un wakeup_call et ré-invoquer run() avec start_step_id
        correspondant à l'étape après le sleep.
        """
        result = WorkflowResult()
        result.messages = list(messages)
        result.variables = dict(variables or {})

        steps = workflow.get("steps", [])
        steps_by_id = {s["id"]: s for s in steps}
        max_iterations = workflow.get("max_iterations", 100)

        current_id = start_step_id or self._find_entry_point(steps)
        if not current_id:
            result.status = "failed"
            result.end_reason = "Aucune étape de départ trouvée"
            return result

        while current_id and result.iterations < max_iterations:
            step = steps_by_id.get(current_id)
            if not step:
                result.status = "failed"
                result.end_reason = f"Étape '{current_id}' introuvable"
                break

            result.iterations += 1
            step_type = step.get("type", "")
            result.next_step_id = None

            handler = getattr(self, f"_dsl_{step_type}", None)
            if handler is None:
                result.status = "failed"
                result.end_reason = f"Type d'étape inconnu: {step_type}"
                break

            should_continue = handler(step, result)

            if result.status != "running":
                break

            if not should_continue:
                break

            current_id = result.next_step_id

        if result.iterations >= max_iterations and result.status == "running":
            result.status = "failed"
            result.end_reason = f"Limite d'itérations atteinte ({max_iterations})"

        return result

    def _find_entry_point(self, steps: List[Dict]) -> Optional[str]:
        """Trouve la première étape (celle qui n'est référencée par aucun `next`)."""
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

    # ── Handlers d'étapes ──────────────────────────────────

    def _dsl_llm_call(self, step: Dict, result: WorkflowResult) -> bool:
        """Appel LLM — délègue à la fonction injectée."""
        if not self.llm_call:
            result.status = "failed"
            result.end_reason = "Aucune fonction LLM configurée"
            return False

        skill_prompt = self._resolve(step.get("skill_prompt", ""), result.variables)
        output_capture = step.get("output_capture")

        try:
            content = self.llm_call(
                messages=result.messages,
                variables=result.variables,
                skill_prompt=skill_prompt,
                temperature=step.get("temperature"),
                max_tokens=step.get("max_tokens"),
            )

            if output_capture:
                result.variables[output_capture] = content

            result.messages.append({"role": "assistant", "content": content})
        except Exception as e:
            result.status = "failed"
            result.end_reason = f"LLM call error: {e}"
            return False

        result.next_step_id = step.get("next")
        return True

    def _dsl_switch(self, step: Dict, result: WorkflowResult) -> bool:
        """Branchement conditionnel multi-branches."""
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

    def _dsl_sleep(self, step: Dict, result: WorkflowResult) -> bool:
        """Pause déportée — le caller doit créer un wakeup_call."""
        result.sleep_seconds = step.get("duration_seconds", 60)
        result.status = "sleeping"
        result.next_step_id = step.get("next")
        return False

    def _dsl_end(self, step: Dict, result: WorkflowResult) -> bool:
        """Fin du workflow."""
        status = step.get("status", "SUCCESS")
        result.status = "success" if status == "SUCCESS" else "failed"
        result.end_reason = status

        successor_role = step.get("successor")
        if successor_role and self.signal_successor:
            result.successor_role = successor_role
            result.successor_config = step.get("successor_config")

        return False

    def _dsl_signal_successor(self, step: Dict, result: WorkflowResult) -> bool:
        """Signal de relais — l'agent demande un successeur."""
        reason = step.get("reason", "unknown")
        result.successor_role = step.get("successor_role")
        result.successor_config = step.get("successor_config")

        if self.signal_successor:
            self.signal_successor(
                reason=reason,
                variables=result.variables,
                sessions=result.messages,
            )

        result.next_step_id = step.get("next")
        return True

    def _dsl_save_state(self, step: Dict, result: WorkflowResult) -> bool:
        """Sauvegarde l'état en BDD via la fonction injectée."""
        var_names = step.get("variables", [])
        state = {}
        if var_names:
            for name in var_names:
                if name in result.variables:
                    state[name] = result.variables[name]
        else:
            state = dict(result.variables)

        if self.save_state:
            self.save_state(state)

        result.next_step_id = step.get("next")
        return True

    def _dsl_connect(self, step: Dict, result: WorkflowResult) -> bool:
        """Connexion à un canal."""
        channel = step.get("channel", "")
        target = step.get("target")

        if self.connect_fn:
            self.connect_fn(channel=channel, target=target)

        result.next_step_id = step.get("next")
        return True

    def _dsl_disconnect(self, step: Dict, result: WorkflowResult) -> bool:
        """Déconnexion d'un canal."""
        channel = step.get("channel", "")

        if self.connect_fn:
            self.connect_fn(channel=channel, target=None, disconnect=True)

        result.next_step_id = step.get("next")
        return True

    def _dsl_set_variable(self, step: Dict, result: WorkflowResult) -> bool:
        """Définit une variable."""
        name = step.get("name", "")
        value = self._resolve(step.get("value", ""), result.variables)
        result.variables[name] = value
        result.next_step_id = step.get("next")
        return True

    def _dsl_concat(self, step: Dict, result: WorkflowResult) -> bool:
        """Concaténation — délègue au PipelineExecutor."""
        result.messages, result.variables = self.pipeline._step_concat(
            step, result.messages, result.variables
        )
        result.next_step_id = step.get("next")
        return True

    def _dsl_extract_context(self, step: Dict, result: WorkflowResult) -> bool:
        """Extraction de contexte — délègue au PipelineExecutor."""
        result.messages, result.variables = self.pipeline._step_extract_context(
            step, result.messages, result.variables
        )
        result.next_step_id = step.get("next")
        return True

    def _dsl_translate_context(self, step: Dict, result: WorkflowResult) -> bool:
        """Traduction de contexte — délègue au PipelineExecutor."""
        result.messages, result.variables = self.pipeline._step_translate_context(
            step, result.messages, result.variables
        )
        result.next_step_id = step.get("next")
        return True

    def _dsl_call_function(self, step: Dict, result: WorkflowResult) -> bool:
        """Appel de fonction — délègue au PipelineExecutor."""
        result.messages, result.variables = self.pipeline._step_call_function(
            step, result.messages, result.variables
        )
        result.next_step_id = step.get("next")
        return True

    def _dsl_tool_call(self, step: Dict, result: WorkflowResult) -> bool:
        """Appel d'outil système (write_file, run_shell...)."""
        if not self.tool_call:
            result.status = "failed"
            result.end_reason = "Aucun ToolExecutor configuré"
            return False
        
        tool_name = step.get("tool", "")
        args = step.get("args", {})
        
        # Résolution des variables dans les arguments
        resolved_args = {}
        for k, v in args.items():
            if isinstance(v, str):
                resolved_args[k] = self._resolve(v, result.variables)
            else:
                resolved_args[k] = v
        
        try:
            output = self.tool_call(tool_name, resolved_args)
            output_capture = step.get("output_capture")
            if output_capture:
                result.variables[output_capture] = output
            
            result.messages.append({"role": "system", "content": f"Outil {tool_name} résultat : {output}"})
        except Exception as e:
            result.status = "failed"
            result.end_reason = f"Tool call error: {e}"
            return False
            
        result.next_step_id = step.get("next")
        return True

    def _resolve(self, value: str, variables: Dict) -> str:
        """Remplace {{variable}} dans une chaîne."""
        return self.pipeline._resolve(value, variables)
