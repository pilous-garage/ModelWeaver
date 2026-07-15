"""PipelineExecutor — Exécute les étapes de transformation d'un pipeline.

Chaque étape modifie le contexte de messages avant l'appel LLM.
L'exécuteur est appelé par le Worker juste avant _build_messages ou _http_completion.
"""

import glob
import json
import os
import subprocess
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class PipelineError(RuntimeError):
    pass


class PipelineExecutor:
    """Exécute un pipeline d'étapes de transformation.

    Usage:
        executor = PipelineExecutor(project_root="/app")
        messages, variables = executor.run(pipeline_steps, original_messages, {})
    """

    def __init__(self, project_root: str = "."):
        self.project_root = Path(project_root).resolve()
        self._functions: Dict[str, Callable] = {}

    def register_function(self, name: str, fn: Callable):
        self._functions[name] = fn

    def run(self, steps: List[Dict],
            messages: List[Dict],
            variables: Dict[str, Any] = None) -> tuple:
        """Exécute le pipeline et retourne (messages modifiés, variables)."""
        vars = dict(variables or {})
        for step in steps:
            step_type = step.get("step", "")
            method = getattr(self, f"_step_{step_type}", None)
            if method is None:
                raise PipelineError(f"Étape inconnue: {step_type}")
            messages, vars = method(step, messages, vars)
        return messages, vars

    def _resolve(self, value: str, vars: Dict) -> str:
        """Remplace {{variable}} dans une chaîne."""
        if not isinstance(value, str):
            return value
        result = value
        for key, val in vars.items():
            placeholder = "{{" + key + "}}"
            if placeholder in result:
                result = result.replace(placeholder, str(val))
        return result

    # ── Étapes ──────────────────────────────────────────────

    def _step_set_variable(self, step: Dict, messages: List, vars: Dict) -> tuple:
        name = step["name"]
        value = self._resolve(step.get("value", ""), vars)
        vars[name] = value
        return messages, vars

    def _step_concat(self, step: Dict, messages: List, vars: Dict) -> tuple:
        position = step.get("position", "after")
        target = step.get("target", "system")
        value = self._resolve(step.get("value", ""), vars)

        for msg in messages:
            if msg["role"] == target:
                if position == "before":
                    msg["content"] = value + "\n" + msg["content"]
                elif position == "after":
                    msg["content"] = msg["content"] + "\n" + value
                elif position == "replace":
                    msg["content"] = value
                break
        return messages, vars

    def _step_extract_context(self, step: Dict, messages: List, vars: Dict) -> tuple:
        sources = step.get("sources", {})
        max_chars = step.get("max_chars", 50000)
        insert_as = step.get("insert_as", "system")
        parts = []

        for fglob in sources.get("files", []):
            matched = glob.glob(str(self.project_root / fglob), recursive=True)
            for filepath in sorted(matched):
                fp = Path(filepath)
                if fp.is_file():
                    try:
                        content = fp.read_text(encoding="utf-8", errors="replace")
                        rel = fp.relative_to(self.project_root)
                        parts.append(f"--- {rel} ---\n{content[:5000]}")
                    except Exception as e:
                        parts.append(f"--- {fglob} ---\nerreur: {e}")

        for s in sources.get("strings", []):
            parts.append(self._resolve(s, vars))

        for cmd in sources.get("commands", []):
            try:
                resolved = self._resolve(cmd, vars)
                r = subprocess.run(
                    resolved, shell=True, capture_output=True, text=True, timeout=30
                )
                stdout = r.stdout.strip() or r.stderr.strip()
                parts.append(f"$ {resolved}\n{stdout[:3000]}")
            except Exception as e:
                parts.append(f"$ {cmd}\nerreur: {e}")

        result = "\n\n".join(parts)
        if len(result) > max_chars:
            result = result[:max_chars] + "\n...[tronqué]"

        for msg in messages:
            if msg["role"] == insert_as:
                msg["content"] += "\n\n[Contexte extrait]\n" + result
                break
        return messages, vars

    def _step_if(self, step: Dict, messages: List, vars: Dict) -> tuple:
        var_name = step.get("variable", "")
        equals = step.get("equals")
        exists = step.get("exists")

        condition = False
        if equals is not None and var_name in vars:
            condition = str(vars[var_name]) == str(equals)
        elif exists is not None:
            condition = (var_name in vars) == bool(exists)

        substeps = step.get("then" if condition else "else", [])
        if substeps:
            messages, vars = self.run(substeps, messages, vars)
        return messages, vars

    def _step_loop(self, step: Dict, messages: List, vars: Dict) -> tuple:
        over_expr = step.get("over", "")
        as_name = step.get("as", "item")
        do_steps = step.get("do", [])

        items = vars.get(over_expr, [])
        if isinstance(items, str):
            try:
                items = json.loads(items)
            except (json.JSONDecodeError, TypeError):
                items = items.split(",")

        if not isinstance(items, list):
            items = [items]

        for item in items:
            vars[as_name] = item
            messages, vars = self.run(do_steps, messages, vars)

        return messages, vars

    def _step_translate_context(self, step: Dict, messages: List, vars: Dict) -> tuple:
        strategy = step.get("strategy", "truncate")
        max_tokens = step.get("max_tokens", 4096)
        max_chars = max_tokens * 4  # approximation ~4 chars/token

        for msg in messages:
            if msg["role"] in ("system", "user"):
                if strategy == "truncate" and len(msg["content"]) > max_chars:
                    msg["content"] = msg["content"][:max_chars] + "\n...[tronqué]"
        return messages, vars

    def _step_call_function(self, step: Dict, messages: List, vars: Dict) -> tuple:
        name = step.get("name", "")
        if name not in self._functions:
            raise PipelineError(f"Fonction non enregistrée: {name}")
        args = step.get("args", {})
        resolved_args = {k: self._resolve(v, vars) for k, v in args.items()}
        result = self._functions[name](messages, vars, **resolved_args)
        if isinstance(result, tuple):
            messages, vars = result
        return messages, vars