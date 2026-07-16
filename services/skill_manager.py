import os
import json
import time
import random
import uuid
import base64
import hashlib
import difflib
import shutil
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import threading

_EMPTY = object()

SKILLS_ROOT = Path(__file__).resolve().parent.parent / "AgentsCatalogue" / "skills"

# Noms de fichiers « importants » routés automatiquement vers important/
KNOWN_IMPORTANT = {
    "todo", "readme", "version", "concept", "concepts", "notes", "note",
    "changelog", "ideas", "idea", "plan", "roadmap", "summary", "resume",
}
INDEX_FILE = "index.json"


class SkillNotFound(KeyError):
    pass


class SkillInputError(ValueError):
    pass


class SkillManager:
    def __init__(self, workspace_root: str = "/tmp"):
        self.workspace_root = workspace_root
        self._defs: Dict[str, dict] = {}
        self._categories: Dict[str, List[str]] = {}
        self._lock = threading.Lock()
        self._loaded = False

    def load_all(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self._defs.clear()
            self._categories.clear()
            for cat_dir in SKILLS_ROOT.iterdir():
                if not cat_dir.is_dir():
                    continue
                cat = cat_dir.name
                self._categories.setdefault(cat, [])
                for f in sorted(cat_dir.glob("*.yaml")):
                    self._load_file(cat, f)
            self._loaded = True

    def _load_file(self, cat: str, path: Path) -> None:
        import yaml
        raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
        if not data or "name" not in data:
            return
        name = data["name"]
        self._defs[name] = data
        base = name.split("@")[0]
        versions = self._categories.get(cat, [])
        if base not in versions:
            versions.append(base)

    def get(self, ref: str) -> dict:
        self.load_all()
        resolved = self._resolve_ref(ref)
        if resolved not in self._defs:
            raise SkillNotFound(f"skill '{ref}' introuvable")
        return dict(self._defs[resolved])

    def _resolve_ref(self, ref: str) -> str:
        cat, rest = (ref.split("/", 1) + [""])[:2]
        base, _, ver = rest.partition("@")
        if ver:
            return f"{cat}/{base}@{ver}"
        entries = [k for k in self._defs if k.startswith(f"{cat}/{base}")]
        if not entries:
            return ref
        if len(entries) == 1:
            return entries[0]
        vers = []
        for e in entries:
            v = e.split("@")[-1]
            if v == "latest":
                continue
            vers.append((v, e))
        vers.sort(reverse=True)
        return vers[0][1]

    def expand(self, workflow: dict) -> dict:
        self.load_all()
        steps = workflow.get("steps", [])
        new_steps = []
        for step in steps:
            if step.get("type") == "call":
                new_steps.extend(self._expand_step(step))
            else:
                new_steps.append(step)
        return {**workflow, "steps": new_steps}

    def _expand_step(self, step: dict) -> List[dict]:
        fn = step.get("fn", "")
        try:
            impl = self.get(fn)
        except SkillNotFound:
            return [step]

        impl_steps = impl.get("implementation", {}).get("steps")
        if not impl_steps:
            return [step]

        fn_inputs = step.get("inputs", {})
        capture = step.get("capture", {})
        fn_outputs = impl.get("outputs", {})

        mapped = []
        for istep in impl_steps:
            s = dict(istep)
            self._map_step_inputs(s, fn_inputs)
            if capture:
                self._map_step_outputs(s, istep, fn_outputs, capture)
            mapped.append(s)
        return mapped

    def _map_step_inputs(self, step: dict, inputs: dict) -> None:
        for key, val in inputs.items():
            for section in ("inputs", "args", "payload"):
                if key in step.get(section, {}):
                    step[section][key] = val
            if step.get("type") == "call" and key in step.get("inputs", {}):
                step["inputs"][key] = val

    def _map_step_outputs(self, step: dict, orig: dict,
                          outputs: dict, capture: dict) -> None:
        for out_name, cap_var in capture.items():
            if out_name in outputs:
                if step.get("type") == "call" and "capture" in step:
                    step["capture"][out_name] = cap_var
                oc = orig.get("output_capture") or orig.get("capture", {}).get(out_name)
                if oc and "capture" in step:
                    step["capture"][out_name] = cap_var

    def call(self, fn: str, inputs: Dict[str, Any],
             workspace_root: Optional[str] = None) -> Dict[str, Any]:
        spec = self.get(fn)
        impl_type = spec.get("implementation", {}).get("type", "")
        func_name = spec.get("implementation", {}).get("function", "")

        if impl_type != "python" or not func_name:
            raise SkillInputError(f"skill '{fn}' : pas d'implémentation python")

        handlers = {
            "_exec_read_file": self._exec_read_file,
            "_exec_write_file": self._exec_write_file,
            "_exec_run_shell": self._exec_run_shell,
            "_exec_optimize_context": self._exec_optimize_context,
            "_exec_log": self._exec_log,
            "_exec_http_get": self._exec_http_get,
            "_exec_http_post": self._exec_http_post,
            "_exec_memory_write": self._exec_memory_write,
            "_exec_memory_read": self._exec_memory_read,
            "_exec_list_dir": self._exec_list_dir,
            "_exec_glob": self._exec_glob,
            "_exec_delete_file": self._exec_delete_file,
            "_exec_copy_file": self._exec_copy_file,
            "_exec_move_file": self._exec_move_file,
            "_exec_mkdir": self._exec_mkdir,
            "_exec_append_file": self._exec_append_file,
            "_exec_file_info": self._exec_file_info,
            "_exec_sleep": self._exec_sleep,
            "_exec_upgrade_important": self._exec_upgrade_important,
            "_exec_downgrade_important": self._exec_downgrade_important,
            "_exec_timestamp": self._exec_timestamp,
            "_exec_json_query": self._exec_json_query,
            "_exec_base64": self._exec_base64,
            "_exec_hash": self._exec_hash,
            "_exec_uuid": self._exec_uuid,
            "_exec_template": self._exec_template,
            "_exec_string_ops": self._exec_string_ops,
            "_exec_diff": self._exec_diff,
            "_exec_get_env": self._exec_get_env,
            "_exec_random": self._exec_random,
            "_exec_call_agent": self._exec_call_agent,
            "_exec_get_budget": self._exec_get_budget,
            "_exec_emit_event": self._exec_emit_event,
            "_exec_ask_user": self._exec_ask_user,
            "_exec_host_read": self._exec_host_read,
            "_exec_host_write": self._exec_host_write,
            "_exec_host_run": self._exec_host_run,
            # ── Espace projet partagé (opère sur le clone perso de l'agent) ──
            "_exec_project_write": self._exec_project_write,
            "_exec_project_read": self._exec_project_read,
            "_exec_project_list": self._exec_project_list,
            "_exec_project_tree": self._exec_project_tree,
            # ── Espace commun live (non versionné) ──
            "_exec_common_write": self._exec_common_write,
            "_exec_common_read": self._exec_common_read,
            "_exec_common_list": self._exec_common_list,
            "_exec_common_tree": self._exec_common_tree,
            # ── Réseau 1 : messagerie directe (1:1) ──
            "_exec_message_send": self._exec_message_send,
            "_exec_message_recv": self._exec_message_recv,
            # ── Réseau 2 : chatroom (N:N, par salon) ──
            "_exec_chatroom_post": self._exec_chatroom_post,
            "_exec_chatroom_read": self._exec_chatroom_read,
            # ── Réseau 4 : LLM résilient (timeout + repli) ──
            "_exec_timeout_llm": self._exec_timeout_llm,
            # ── Réseau 3 : git (dépôt central bare + clones par agent) ──
            "_exec_repo_init": self._exec_repo_init,
            "_exec_git_clone": self._exec_git_clone,
            "_exec_git_branch": self._exec_git_branch,
            "_exec_git_checkout": self._exec_git_checkout,
            "_exec_git_commit": self._exec_git_commit,
            "_exec_git_diff": self._exec_git_diff,
            "_exec_git_log": self._exec_git_log,
            "_exec_git_status": self._exec_git_status,
            "_exec_git_merge": self._exec_git_merge,
            "_exec_git_add": self._exec_git_add,
            "_exec_git_resolve_conflict": self._exec_git_resolve_conflict,
            "_exec_git_fetch": self._exec_git_fetch,
            "_exec_git_pull": self._exec_git_pull,
            "_exec_git_push": self._exec_git_push,
        }
        handler = handlers.get(func_name)
        if not handler:
            raise SkillInputError(f"implémentation '{func_name}' inconnue")

        ws = workspace_root or self.workspace_root
        return handler(inputs, ws)

    def _safe_path(self, path: str, workspace_root: str) -> str:
        norm = os.path.normpath(path)
        if norm.startswith("..") or norm.startswith("/"):
            norm = norm.lstrip("/")
        full = os.path.join(workspace_root, norm)
        if not full.startswith(os.path.abspath(workspace_root)):
            raise PermissionError("chemin hors workspace")
        return full

    # ── Résolution de chemin dans le home de l'agent (relatif) ──
    def _read_index(self, ws: str) -> dict:
        p = os.path.join(ws, INDEX_FILE)
        if os.path.exists(p):
            try:
                return json.loads(Path(p).read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _write_index(self, ws: str, idx: dict) -> None:
        os.makedirs(ws, exist_ok=True)
        Path(os.path.join(ws, INDEX_FILE)).write_text(
            json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")

    def _index_add(self, ws: str, abs_file: str) -> None:
        home = os.path.abspath(ws)
        imp = os.path.join(home, "important")
        af = os.path.abspath(abs_file)
        if not (af == imp or af.startswith(imp + os.sep)):
            return
        stem = os.path.splitext(os.path.basename(af))[0].lower()
        if not stem:
            return
        idx = self._read_index(ws)
        if stem in idx:  # ambiguïté : déjà indexé
            return
        idx[stem] = os.path.relpath(af, home)
        self._write_index(ws, idx)

    def _index_remove(self, ws: str, abs_file: str) -> None:
        home = os.path.abspath(ws)
        imp = os.path.join(home, "important")
        af = os.path.abspath(abs_file)
        if not (af == imp or af.startswith(imp + os.sep)):
            return
        stem = os.path.splitext(os.path.basename(af))[0].lower()
        idx = self._read_index(ws)
        rel = os.path.relpath(af, home)
        if idx.get(stem) == rel:
            del idx[stem]
            self._write_index(ws, idx)

    def _resolve_read_path(self, path: str, ws: str) -> str:
        """Résout un chemin de lecture : alias d'index puis relatif sous home."""
        home = os.path.abspath(ws)
        idx = self._read_index(ws)
        base = path.split("/")[-1]
        if "/" not in path and path in idx:
            return os.path.join(home, idx[path])
        if "/" not in path and base in idx:
            return os.path.join(home, idx[base])
        return self._safe_path(path, ws)

    def _classify_write_path(self, path: str, ws: str) -> str:
        """Résout un chemin d'écriture : sous-dossier explicite honoré,
        sinon nom connu -> important/, sinon -> work/."""
        home = os.path.abspath(ws)
        if "/" in path:
            return self._safe_path(path, ws)
        stem = os.path.splitext(path)[0].lower()
        sub = "important" if stem in KNOWN_IMPORTANT else "work"
        return os.path.join(home, sub, path)

    def _exec_read_file(self, inputs: dict, ws: str) -> dict:
        path = inputs.get("path", "")
        full = self._resolve_read_path(path, ws)
        with open(full, "r", encoding="utf-8") as f:
            return {"content": f.read()}

    def _exec_write_file(self, inputs: dict, ws: str) -> dict:
        path = inputs.get("path", "")
        content = inputs.get("content", "")
        full = self._classify_write_path(path, ws)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        self._index_add(ws, full)
        return {"result": f"Fichier {path} écrit"}

    def _exec_run_shell(self, inputs: dict, ws: str) -> dict:
        from services.sandbox import Sandbox, SandboxError
        cmd = inputs.get("command", "")
        try:
            stdout, stderr, rc = Sandbox().run(cmd, cwd=ws, timeout=30)
            return {"stdout": stdout, "stderr": stderr, "exit_code": rc}
        except SandboxError as e:
            return {"stdout": "", "stderr": str(e), "exit_code": -1}

    def _exec_optimize_context(self, inputs: dict, ws: str) -> dict:
        messages = list(inputs.get("messages", []))
        max_chars = int(inputs.get("max_chars", 50000))
        budget = inputs.get("budget_remaining")

        total = sum(len(m.get("content", "")) for m in messages)
        ratio = 1.0
        if total <= max_chars:
            return {"messages": messages, "compression_ratio": 1.0}

        if budget is not None and budget < 1000:
            max_chars = max(max_chars // 4, 1000)

        kept = []
        chars = 0
        for m in reversed(messages):
            sz = len(m.get("content", "")) + 50
            if chars + sz > max_chars and kept:
                break
            kept.insert(0, m)
            chars += sz

        ratio = round(total / max(chars, 1), 2)
        return {"messages": kept, "compression_ratio": ratio}

    def _exec_log(self, inputs: dict, ws: str) -> dict:
        from services.audit import audit
        level = inputs.get("level", "info")
        message = inputs.get("message", "")
        action = inputs.get("action", "skill.log")
        agent_id = inputs.get("agent_id", "")
        hook_type = inputs.get("hook_type", "")
        status = inputs.get("status", "")
        error = inputs.get("error", "")
        ok = level not in ("error", "critical")
        audit(action, service="skill", actor=str(agent_id), ok=ok,
              level=level, message=message, hook_type=hook_type,
              status=status, error=error)
        return {"ok": True}

    def _exec_http_get(self, inputs: dict, ws: str) -> dict:
        return self._http_request("GET", inputs)

    def _exec_http_post(self, inputs: dict, ws: str) -> dict:
        method = str(inputs.get("method", "POST")).upper()
        if method not in ("POST", "PUT", "PATCH", "DELETE"):
            return {"status_code": 0, "headers": {}, "body": "",
                    "error": f"méthode non supportée: {method}"}
        return self._http_request(method, inputs)

    def _http_request(self, method: str, inputs: dict) -> dict:
        import requests
        url = inputs.get("url", "")
        if not url or not url.startswith(("http://", "https://")):
            return {"status_code": 0, "headers": {}, "body": "",
                    "error": "url invalide (http/https requis)"}
        headers = dict(inputs.get("headers", {}) or {})
        timeout = int(inputs.get("timeout", 15))
        max_bytes = int(inputs.get("max_bytes", 1048576))
        verify_ssl = bool(inputs.get("verify_ssl", True))
        body_text = inputs.get("body_text")
        json_body = inputs.get("body")
        req_kwargs = dict(headers=headers, timeout=timeout,
                          verify=verify_ssl, stream=True)
        if method != "GET":
            if body_text:
                req_kwargs["data"] = body_text
            elif json_body is not None:
                req_kwargs["json"] = json_body
        try:
            resp = requests.request(method, url, **req_kwargs)
            body = b""
            for chunk in resp.iter_content(chunk_size=8192):
                body += chunk
                if len(body) >= max_bytes:
                    break
            resp_headers = {k: v for k, v in resp.headers.items()}
            text = body[:max_bytes].decode("utf-8", errors="replace")
            return {"status_code": resp.status_code,
                    "headers": resp_headers, "body": text, "error": ""}
        except Exception as e:
            return {"status_code": 0, "headers": {}, "body": "",
                    "error": str(e)}

    def _memory_root(self, agent_id: str) -> Path:
        from services._common import mw_home
        return mw_home() / "memagent" / str(agent_id) / "mem"

    def _exec_memory_write(self, inputs: dict, ws: str) -> dict:
        agent_id = inputs.get("agent_id", "")
        namespace = inputs.get("namespace", "default")
        key = inputs.get("key", "")
        value = inputs.get("value")
        if not agent_id or not key:
            return {"ok": False, "path": "",
                    "error": "agent_id et key requis"}
        safe_ns = "".join(c for c in namespace if c.isalnum() or c in "-_")
        safe_key = "".join(c for c in key if c.isalnum() or c in "-_.")
        root = self._memory_root(agent_id) / safe_ns
        root.mkdir(parents=True, exist_ok=True)
        fp = root / f"{safe_key}.json"
        fp.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                      encoding="utf-8")
        return {"ok": True, "path": str(fp)}

    def _exec_memory_read(self, inputs: dict, ws: str) -> dict:
        agent_id = inputs.get("agent_id", "")
        namespace = inputs.get("namespace", "default")
        key = inputs.get("key", "")
        if not agent_id or not key:
            return {"found": False, "value": None,
                    "error": "agent_id et key requis"}
        safe_ns = "".join(c for c in namespace if c.isalnum() or c in "-_")
        safe_key = "".join(c for c in key if c.isalnum() or c in "-_.")
        root = self._memory_root(agent_id) / safe_ns
        fp = root / f"{safe_key}.json"
        if not fp.exists():
            return {"found": False, "value": None}
        try:
            value = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"found": False, "value": None, "error": "lecture échouée"}
        return {"found": True, "value": value}

    # ── Fichiers étendus (relatif au home de l'agent) ──

    def _exec_list_dir(self, inputs: dict, ws: str) -> dict:
        path = inputs.get("path", "")
        full = self._resolve_read_path(path, ws) if path else os.path.abspath(ws)
        if not os.path.isdir(full):
            return {"entries": [], "error": "n'est pas un dossier"}
        entries = []
        for name in sorted(os.listdir(full)):
            fp = os.path.join(full, name)
            entries.append({
                "name": name,
                "type": "dir" if os.path.isdir(fp) else "file",
                "size": os.path.getsize(fp) if os.path.isfile(fp) else 0,
            })
        return {"entries": entries}

    def _exec_glob(self, inputs: dict, ws: str) -> dict:
        pattern = inputs.get("pattern", "*")
        base = os.path.abspath(ws)
        results = [str(p.relative_to(base)) for p in Path(base).glob(pattern)
                   if not str(p).endswith(INDEX_FILE)]
        return {"matches": sorted(results)}

    def _exec_delete_file(self, inputs: dict, ws: str) -> dict:
        path = inputs.get("path", "")
        full = self._resolve_read_path(path, ws)
        if not os.path.exists(full):
            return {"ok": False, "error": "fichier introuvable"}
        if os.path.isdir(full):
            shutil.rmtree(full)
        else:
            os.remove(full)
        self._index_remove(ws, full)
        return {"ok": True}

    def _exec_copy_file(self, inputs: dict, ws: str) -> dict:
        src = self._resolve_read_path(inputs.get("src", ""), ws)
        dst_input = inputs.get("dst", "")
        dst = self._classify_write_path(dst_input, ws) if "/" not in dst_input \
            else self._safe_path(dst_input, ws)
        if not os.path.exists(src):
            return {"ok": False, "error": "source introuvable"}
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        self._index_add(ws, dst)
        return {"ok": True, "dst": os.path.relpath(dst, os.path.abspath(ws))}

    def _exec_move_file(self, inputs: dict, ws: str) -> dict:
        src = self._resolve_read_path(inputs.get("src", ""), ws)
        dst_input = inputs.get("dst", "")
        dst = self._classify_write_path(dst_input, ws) if "/" not in dst_input \
            else self._safe_path(dst_input, ws)
        if not os.path.exists(src):
            return {"ok": False, "error": "source introuvable"}
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        self._index_remove(ws, src)
        shutil.move(src, dst)
        self._index_add(ws, dst)
        return {"ok": True, "dst": os.path.relpath(dst, os.path.abspath(ws))}

    def _exec_mkdir(self, inputs: dict, ws: str) -> dict:
        path = inputs.get("path", "")
        if "/" in path:
            full = self._safe_path(path, ws)
        else:
            full = os.path.join(os.path.abspath(ws), "work", path)
        os.makedirs(full, exist_ok=True)
        return {"ok": True, "path": os.path.relpath(full, os.path.abspath(ws))}

    def _exec_append_file(self, inputs: dict, ws: str) -> dict:
        path = inputs.get("path", "")
        content = inputs.get("content", "")
        full = self._classify_write_path(path, ws)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "a", encoding="utf-8") as f:
            f.write(content)
        self._index_add(ws, full)
        return {"ok": True}

    def _exec_file_info(self, inputs: dict, ws: str) -> dict:
        path = inputs.get("path", "")
        full = self._resolve_read_path(path, ws)
        if not os.path.exists(full):
            return {"exists": False}
        st = os.stat(full)
        return {
            "exists": True,
            "is_dir": os.path.isdir(full),
            "size": st.st_size,
            "mtime": st.st_mtime,
        }

    def _exec_sleep(self, inputs: dict, ws: str) -> dict:
        seconds = float(inputs.get("seconds", 0))
        if seconds < 0:
            seconds = 0
        if seconds > 3600:
            seconds = 3600
        time.sleep(seconds)
        return {"ok": True, "slept": seconds}

    # ── Classification important / work ──

    def _agent_id_from_ws(self, ws: str, inputs: dict) -> str:
        aid = inputs.get("agent_id", "")
        if aid:
            return str(aid)
        parts = Path(ws).parts
        if "memagent" in parts:
            return str(parts[parts.index("memagent") + 1])
        return ""

    def _exec_upgrade_important(self, inputs: dict, ws: str) -> dict:
        src = self._resolve_read_path(inputs.get("src", ""), ws)
        if not os.path.exists(src):
            return {"ok": False, "error": "source introuvable"}
        name = inputs.get("name") or os.path.basename(src)
        dst = os.path.join(os.path.abspath(ws), "important", name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        self._index_remove(ws, src)
        shutil.move(src, dst)
        self._index_add(ws, dst)
        return {"ok": True, "path": os.path.relpath(dst, os.path.abspath(ws))}

    def _exec_downgrade_important(self, inputs: dict, ws: str) -> dict:
        src = self._resolve_read_path(inputs.get("src", ""), ws)
        if not os.path.exists(src):
            return {"ok": False, "error": "source introuvable"}
        name = inputs.get("name") or os.path.basename(src)
        dst = os.path.join(os.path.abspath(ws), "work", name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        self._index_remove(ws, src)
        shutil.move(src, dst)
        return {"ok": True, "path": os.path.relpath(dst, os.path.abspath(ws))}

    # ── Temps ──

    def _exec_timestamp(self, inputs: dict, ws: str) -> dict:
        fmt = inputs.get("format", "iso")
        now = time.time()
        if fmt == "epoch":
            return {"value": now, "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                       time.gmtime(now))}
        if fmt == "date":
            return {"value": time.strftime("%Y-%m-%d", time.localtime(now))}
        return {"value": time.strftime("%Y-%m-%dT%H:%M:%S%z",
                                       time.localtime(now)),
                "epoch": now}

    # ── Données / Transform ──

    def _exec_json_query(self, inputs: dict, ws: str) -> dict:
        data = inputs.get("data")
        path = inputs.get("path", "")
        cur = data
        for part in path.split("."):
            if part == "":
                continue
            if isinstance(cur, list):
                try:
                    cur = cur[int(part)]
                except (ValueError, IndexError):
                    return {"found": False, "error": f"index invalide: {part}"}
            elif isinstance(cur, dict):
                if part not in cur:
                    return {"found": False, "error": f"clé absente: {part}"}
                cur = cur[part]
            else:
                return {"found": False, "error": f"non navigable en '{part}'"}
        return {"found": True, "value": cur}

    def _exec_base64(self, inputs: dict, ws: str) -> dict:
        mode = inputs.get("mode", "encode")
        data = inputs.get("data", "")
        if mode == "decode":
            try:
                return {"result": base64.b64decode(data).decode("utf-8",
                                                               errors="replace")}
            except Exception as e:
                return {"result": "", "error": str(e)}
        raw = data.encode("utf-8") if isinstance(data, str) else data
        return {"result": base64.b64encode(raw).decode("ascii")}

    def _exec_hash(self, inputs: dict, ws: str) -> dict:
        algo = inputs.get("algo", "sha256")
        data = inputs.get("data", "")
        h = hashlib.new(algo)
        h.update(data.encode("utf-8") if isinstance(data, str) else data)
        return {"algo": algo, "hex": h.hexdigest()}

    def _exec_uuid(self, inputs: dict, ws: str) -> dict:
        return {"value": str(uuid.uuid4())}

    def _exec_template(self, inputs: dict, ws: str) -> dict:
        template = inputs.get("template", "")
        vars_ = inputs.get("vars", {}) or {}
        out = template
        for k, v in vars_.items():
            out = out.replace("{{" + str(k) + "}}", str(v))
        return {"result": out}

    # ── Texte ──

    def _exec_string_ops(self, inputs: dict, ws: str) -> dict:
        op = inputs.get("op", "trim")
        text = inputs.get("text", "")
        if op == "trim":
            return {"result": text.strip()}
        if op == "upper":
            return {"result": text.upper()}
        if op == "lower":
            return {"result": text.lower()}
        if op == "len":
            return {"result": len(text)}
        if op == "split":
            sep = inputs.get("sep", " ")
            mx = inputs.get("maxsplit", -1)
            parts = text.split(sep, mx) if mx and mx > 0 else text.split(sep)
            return {"result": parts}
        if op == "replace":
            return {"result": text.replace(inputs.get("old", ""),
                                          inputs.get("new", ""))}
        if op == "slice":
            a = int(inputs.get("start", 0))
            b = inputs.get("end")
            b = None if b in (None, "") else int(b)
            return {"result": text[a:b]}
        return {"result": text, "error": f"op inconnue: {op}"}

    def _exec_diff(self, inputs: dict, ws: str) -> dict:
        a = inputs.get("a", "").splitlines()
        b = inputs.get("b", "").splitlines()
        added, removed = [], []
        for line in difflib.unified_diff(a, b, lineterm=""):
            if line.startswith("+") and not line.startswith("+++ "):
                added.append(line[1:])
            elif line.startswith("-") and not line.startswith("--- "):
                removed.append(line[1:])
        return {"added": added, "removed": removed,
                "added_count": len(added), "removed_count": len(removed)}

    # ── Système ──

    _ENV_ALLOWLIST = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL",
                      "MODELWEAVER_HOME", "TERM", "SHELL", "PWD", "OS")

    def _exec_get_env(self, inputs: dict, ws: str) -> dict:
        name = inputs.get("name", "")
        if name not in self._ENV_ALLOWLIST:
            return {"value": "", "allowed": False}
        return {"value": os.environ.get(name, ""), "allowed": True}

    def _exec_random(self, inputs: dict, ws: str) -> dict:
        lo = int(inputs.get("min", 0))
        hi = int(inputs.get("max", 100))
        if hi < lo:
            hi = lo
        return {"value": random.randint(lo, hi)}

    # ── Agent / Orchestration ──

    def _exec_call_agent(self, inputs: dict, ws: str) -> dict:
        target = inputs.get("target", "")
        request = inputs.get("request", "")
        provider = inputs.get("provider_ref", "")
        model = inputs.get("model_ref", "")
        if not target:
            return {"ok": False, "error": "target requis"}
        try:
            from modules.sql.db import AgentsDB
            from services.agent_manager.service import Agent, AgentManager
            db = AgentsDB()
            mgr = AgentManager(db=db)
            row = mgr.get_by_name(target)
            if not row:
                return {"ok": False, "error": f"agent '{target}' introuvable"}
            agent = Agent.hydrate(row["agent_id"], db)
            res = agent.execute(request, provider_ref=provider, model_ref=model)
            agent.dehydrate()
            return {"ok": True, "result": res}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _exec_get_budget(self, inputs: dict, ws: str) -> dict:
        from services.tarif import check_budget
        provider = inputs.get("provider_ref", "")
        model = inputs.get("model_ref", "")
        try:
            return {"budget": check_budget(provider, model)}
        except Exception as e:
            return {"budget": {}, "error": str(e)}

    def _exec_emit_event(self, inputs: dict, ws: str) -> dict:
        from services.lifecycle import get_event_bus, HookEvent
        event_type = inputs.get("event_type", "custom")
        agent_id = self._agent_id_from_ws(ws, inputs)
        payload = inputs.get("payload", {}) or {}
        # Journal agent-level
        log_path = os.path.join(os.path.abspath(ws), "ctx", "events.jsonl")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": event_type, "payload": payload,
                                "ts": time.time()}) + "\n")
        get_event_bus().publish(HookEvent(
            hook_type=str(event_type), agent_id=agent_id, result=payload))
        return {"ok": True}

    def _exec_ask_user(self, inputs: dict, ws: str) -> dict:
        agent_id = self._agent_id_from_ws(ws, inputs)
        question = inputs.get("question", "")
        qid = str(uuid.uuid4())
        store = os.path.join(os.path.abspath(ws), "ctx", "ask")
        os.makedirs(store, exist_ok=True)
        Path(os.path.join(store, f"{qid}.json")).write_text(
            json.dumps({"question": question, "answered": False,
                        "answer": None, "ts": time.time()},
                       ensure_ascii=False), encoding="utf-8")
        return {"question_id": qid, "answered": False, "agent_id": agent_id}

    # ── Accès hôte (absolu, gated par FsAuthManager) ──

    def _exec_host_read(self, inputs: dict, ws: str) -> dict:
        from services.fs_auth import FsAuthManager, FsAuthError
        agent_id = self._agent_id_from_ws(ws, inputs)
        path = inputs.get("path", "")
        try:
            mgr = FsAuthManager()
            if not mgr.check(int(agent_id), path, want_write=False):
                raise FsAuthError(f"accès refusé: {path}")
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            mgr.close()
            return {"content": content}
        except FsAuthError as e:
            return {"content": "", "error": str(e)}
        except Exception as e:
            return {"content": "", "error": str(e)}

    def _exec_host_write(self, inputs: dict, ws: str) -> dict:
        from services.fs_auth import FsAuthManager, FsAuthError
        agent_id = self._agent_id_from_ws(ws, inputs)
        path = inputs.get("path", "")
        content = inputs.get("content", "")
        try:
            mgr = FsAuthManager()
            if not mgr.check(int(agent_id), path, want_write=True):
                raise FsAuthError(f"écriture refusée: {path}")
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            mgr.close()
            return {"ok": True}
        except FsAuthError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _exec_host_run(self, inputs: dict, ws: str) -> dict:
        from services.fs_auth import FsAuthManager, FsAuthError
        from services.sandbox import Sandbox, SandboxError
        agent_id = self._agent_id_from_ws(ws, inputs)
        command = inputs.get("command", "")
        cwd = inputs.get("cwd", "/")
        try:
            mgr = FsAuthManager()
            if not mgr.check(int(agent_id), cwd, want_write=True):
                raise FsAuthError(f"cwd non autorisé: {cwd}")
            mgr.close()
            stdout, stderr, rc = Sandbox().run(command, cwd=cwd, timeout=30)
            return {"stdout": stdout, "stderr": stderr, "exit_code": rc}
        except FsAuthError as e:
            return {"stdout": "", "stderr": str(e), "exit_code": -1}
        except SandboxError as e:
            return {"stdout": "", "stderr": str(e), "exit_code": -1}

    # ── Collaboration : helpers de chemins ──
    #
    # Modèle :
    #   - Dépôt central BARE  : mw_home()/repos/{project_id}.git  (source de vérité)
    #   - Clone par agent     : mw_home()/memagent/{agent_id}/workspace/{project_id}
    #     (= working tree versionné ; contient work + important/ + fichiers projet)
    #   - Chatroom (N:N)      : mw_home()/comms/{chatroom_id}/chatroom.jsonl
    #   - Inbox (1:1)         : mw_home()/inbox/{agent_id}/
    #   - Common (live)       : mw_home()/common/{group_id}/  (non versionné)
    #
    # Les dossiers privés de l'agent (perso/, ctx/, mem/, history/) vivent HORS
    # du clone, donc ne sont jamais versionnés.

    def _central_repo(self, project_id: str) -> Path:
        from services._common import mw_home
        return mw_home() / "repos" / f"{project_id}.git"

    def _agent_clone(self, agent_id: str, project_id: str) -> Path:
        from services._common import mw_home
        return (mw_home() / "memagent" / str(agent_id)
                / "workspace" / str(project_id))

    def _inbox_root(self, agent_id: str) -> Path:
        from services._common import mw_home
        return mw_home() / "inbox" / str(agent_id)

    def _comms_root(self, chatroom_id: str) -> Path:
        from services._common import mw_home
        return mw_home() / "comms" / str(chatroom_id)

    def _common_root(self, group_id: str) -> Path:
        from services._common import mw_home
        return mw_home() / "common" / str(group_id)

    def _safe_under(self, root: Path, path: str) -> Path:
        root_res = root.resolve()
        norm = os.path.normpath(path)
        if norm.startswith("..") or norm.startswith("/"):
            norm = norm.lstrip("/")
        full = (root_res / norm).resolve()
        if full != root_res and not str(full).startswith(str(root_res) + os.sep):
            raise PermissionError(f"chemin hors racine: {path}")
        return full

    def _git_run(self, root: Path, args: List[str], timeout: int = 60) -> dict:
        """Exécute `git -C {root} …` dans le working tree `root`.

        Normalise toujours la sortie : {stdout, stderr, exit_code, ok}.
        `ok` vaut True ssi exit_code == 0 — permet au FSM de détecter un
        échec git (commit/merge/push en erreur) au lieu de le laisser passer
        inaperçu (V0.6.23)."""
        from services.sandbox import Sandbox, SandboxError
        if not Path(root).exists():
            return {"stdout": "", "stderr": "chemin inexistant", "exit_code": -1,
                    "ok": False}
        try:
            stdout, stderr, rc = Sandbox().run(
                ["git", "-C", str(root)] + args, cwd=str(root),
                shell=False, timeout=timeout)
            return {"stdout": stdout, "stderr": stderr, "exit_code": rc,
                    "ok": rc == 0}
        except SandboxError as e:
            return {"stdout": "", "stderr": str(e), "exit_code": -1, "ok": False}

    def _git_identity(self, root: Path, agent_id: str) -> None:
        """Identité git locale : sans elle, `commit` échoue en env vierge."""
        who = str(agent_id) or "anon"
        self._git_run(root, ["config", "user.email", f"{who}@modelweaver.local"])
        self._git_run(root, ["config", "user.name", f"agent-{who}"])

    def _clone_or_err(self, inputs: dict) -> Tuple[Optional[Path], Optional[dict]]:
        pid = inputs.get("project_id", "")
        aid = inputs.get("agent_id", "")
        if not pid or not aid:
            return None, {"stdout": "", "stderr": "project_id et agent_id requis",
                          "exit_code": -1, "ok": False}
        root = self._agent_clone(aid, pid)
        if not (root / ".git").exists():
            return None, {"stdout": "", "stderr": "clone introuvable (git_clone ?)",
                          "exit_code": -1, "ok": False}
        return root, None

    # ── Réseau 3 : git (dépôt central bare + clones par agent) ──

    def _exec_repo_init(self, inputs: dict, ws: str) -> dict:
        """Crée le dépôt central BARE et y sème un commit initial (branche
        master : README + .gitignore)."""
        import tempfile
        from services.sandbox import Sandbox, SandboxError
        pid = inputs.get("project_id", "")
        if not pid:
            return {"ok": False, "error": "project_id requis"}
        bare = self._central_repo(pid)
        if bare.exists():
            return {"ok": True, "path": str(bare), "note": "déjà initialisé"}
        bare.parent.mkdir(parents=True, exist_ok=True)
        sb = Sandbox()
        try:
            o, e, rc = sb.run(["git", "init", "--bare", "-b", "master", str(bare)],
                              shell=False, timeout=60)
            if rc != 0:
                o, e, rc = sb.run(["git", "init", "--bare", str(bare)],
                                  shell=False, timeout=60)
            if rc != 0:
                return {"ok": False, "error": f"init bare: {e}"}
            tmp = Path(tempfile.mkdtemp(prefix="mw-seed-"))
            try:
                self._git_run(tmp, ["init", "-q", "-b", "master"])
                self._git_identity(tmp, "system")
                (tmp / "README.md").write_text(f"# Projet {pid}\n", encoding="utf-8")
                (tmp / ".gitignore").write_text("__pycache__/\n*.pyc\n",
                                                encoding="utf-8")
                self._git_run(tmp, ["add", "-A"])
                self._git_run(tmp, ["commit", "-q", "-m", "init"])
                self._git_run(tmp, ["branch", "-M", "master"])
                self._git_run(tmp, ["remote", "add", "origin", str(bare)])
                push = self._git_run(tmp, ["push", "-q", "origin", "master"])
                if push["exit_code"] != 0:
                    return {"ok": False, "error": f"seed push: {push['stderr']}"}
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        except SandboxError as ex:
            return {"ok": False, "error": str(ex)}
        return {"ok": True, "path": str(bare)}

    def _exec_git_clone(self, inputs: dict, ws: str) -> dict:
        """Clone le dépôt central dans le workspace perso de l'agent."""
        from services.sandbox import Sandbox, SandboxError
        pid = inputs.get("project_id", "")
        aid = inputs.get("agent_id", "")
        if not pid or not aid:
            return {"ok": False, "error": "project_id et agent_id requis"}
        bare = self._central_repo(pid)
        if not bare.exists():
            return {"ok": False, "error": "dépôt central inexistant (repo_init ?)"}
        dest = self._agent_clone(aid, pid)
        if (dest / ".git").exists():
            r = self._git_run(dest, ["fetch", "-q", "origin"])
            return {"ok": True, "path": str(dest), "note": "déjà cloné (fetch)",
                    "exit_code": r["exit_code"]}
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            o, e, rc = Sandbox().run(["git", "clone", "-q", str(bare), str(dest)],
                                     shell=False, timeout=60)
        except SandboxError as ex:
            return {"ok": False, "error": str(ex)}
        if rc != 0:
            return {"ok": False, "error": f"clone: {e}"}
        self._git_identity(dest, aid)
        return {"ok": True, "path": str(dest), "exit_code": rc}

    def _exec_git_branch(self, inputs: dict, ws: str) -> dict:
        root, err = self._clone_or_err(inputs)
        if err:
            return err
        name = inputs.get("name", "")
        create = inputs.get("create", False)
        if name and create:
            return self._git_run(root, ["checkout", "-b", name])
        return self._git_run(root, ["branch", "--list"])

    def _exec_git_checkout(self, inputs: dict, ws: str) -> dict:
        root, err = self._clone_or_err(inputs)
        if err:
            return err
        name = inputs.get("name", "")
        if not name:
            return {"stdout": "", "stderr": "name requis", "exit_code": -1}
        return self._git_run(root, ["checkout", name])

    def _exec_git_commit(self, inputs: dict, ws: str) -> dict:
        root, err = self._clone_or_err(inputs)
        if err:
            return err
        msg = inputs.get("message", "update")
        self._git_run(root, ["add", "-A"])
        return self._git_run(root, ["commit", "-q", "-m", msg])

    def _exec_git_diff(self, inputs: dict, ws: str) -> dict:
        root, err = self._clone_or_err(inputs)
        if err:
            return err
        target = inputs.get("target", "")
        return self._git_run(root, ["diff"] + ([target] if target else []))

    def _exec_git_log(self, inputs: dict, ws: str) -> dict:
        root, err = self._clone_or_err(inputs)
        if err:
            return err
        oneline = inputs.get("oneline", True)
        r = self._git_run(root, ["log"] + (["--oneline"] if oneline else []))
        r["lines"] = [l for l in r["stdout"].splitlines()]
        return r

    def _exec_git_status(self, inputs: dict, ws: str) -> dict:
        root, err = self._clone_or_err(inputs)
        if err:
            return err
        r = self._git_run(root, ["status", "--porcelain"])
        r["clean"] = (r["stdout"].strip() == "")
        r["conflicts"] = self._unmerged_files(root)
        return r

    def _unmerged_files(self, root: Path) -> List[str]:
        """Liste des fichiers en conflit de merge (état non résolu)."""
        out = self._git_run(root, ["diff", "--name-only", "--diff-filter=U"])
        return [l.strip() for l in out.get("stdout", "").splitlines() if l.strip()]

    def _exec_git_merge(self, inputs: dict, ws: str) -> dict:
        root, err = self._clone_or_err(inputs)
        if err:
            return err
        if inputs.get("abort"):
            return self._git_run(root, ["merge", "--abort"])
        name = inputs.get("name", "")
        if not name:
            return {"stdout": "", "stderr": "name requis", "exit_code": -1,
                    "ok": False}
        args = ["merge", "--no-edit"]
        strat = inputs.get("strategy")
        if strat in ("ours", "theirs"):
            args += ["-X", strat]
        args.append(name)
        r = self._git_run(root, args)
        if r["exit_code"] != 0:
            # Merge en échec : survolontairement un conflit de contenu.
            r = dict(r)
            r["conflict"] = ("CONFLICT" in r.get("stderr", "")
                             or "CONFLICT" in r.get("stdout", ""))
            r["conflicts"] = self._unmerged_files(root)
            r["error"] = ("merge en échec (conflit de contenu)" if r["conflict"]
                          else f"merge en échec: {r.get('stderr', '')[:200]}")
        return r

    def _exec_git_add(self, inputs: dict, ws: str) -> dict:
        root, err = self._clone_or_err(inputs)
        if err:
            return err
        path = inputs.get("path", "")
        if path:
            return self._git_run(root, ["add", "--", path])
        return self._git_run(root, ["add", "-A"])

    def _exec_git_resolve_conflict(self, inputs: dict, ws: str) -> dict:
        """Résout un conflit de merge en choisissant un côté (ours/theirs)
        puis stage le(s) fichier(s) — à faire après un git_merge en conflit
        avant le commit de conclusion.

        `path` :
          - "all"  : résout tous les fichiers en conflit du clone ;
          - sinon  : fichier unique (relatif au clone)."""
        root, err = self._clone_or_err(inputs)
        if err:
            return err
        side = inputs.get("side", "ours")
        if side not in ("ours", "theirs"):
            return {"ok": False, "error": "side doit être 'ours' ou 'theirs'"}
        path = inputs.get("path", "")
        if path == "all":
            files = self._unmerged_files(root)
            if not files:
                return {"ok": True, "resolved": [], "note": "aucun conflit"}
            resolved = []
            for f in files:
                co = self._git_run(root, ["checkout", f"--{side}", "--", f])
                if co["exit_code"] != 0:
                    return co
                ad = self._git_run(root, ["add", "--", f])
                if ad["exit_code"] != 0:
                    return ad
                resolved.append(f)
            return {"ok": True, "resolved": resolved}
        if not path:
            return {"ok": False, "error": "path requis (ou 'all')",
                    "exit_code": -1}
        co = self._git_run(root, ["checkout", f"--{side}", "--", path])
        if co["exit_code"] != 0:
            return co
        return self._git_run(root, ["add", "--", path])

    def _exec_git_fetch(self, inputs: dict, ws: str) -> dict:
        root, err = self._clone_or_err(inputs)
        if err:
            return err
        return self._git_run(root, ["fetch", "-q", "origin"])

    def _exec_git_pull(self, inputs: dict, ws: str) -> dict:
        root, err = self._clone_or_err(inputs)
        if err:
            return err
        branch = inputs.get("branch", "") or inputs.get("name", "")
        if branch:
            return self._git_run(root, ["pull", "--no-edit", "origin", branch])
        return self._git_run(root, ["pull", "--no-edit"])

    def _exec_git_push(self, inputs: dict, ws: str) -> dict:
        root, err = self._clone_or_err(inputs)
        if err:
            return err
        branch = inputs.get("branch", "")
        ref = branch if branch else "HEAD"
        return self._git_run(root, ["push", "-u", "origin", ref])

    # ── Espace projet (opère sur le clone perso de l'agent) ──

    def _exec_project_write(self, inputs: dict, ws: str) -> dict:
        pid = inputs.get("project_id", "")
        aid = inputs.get("agent_id", "")
        path = inputs.get("path", "")
        content = inputs.get("content", "")
        if not pid or not aid or not path:
            return {"ok": False, "error": "project_id, agent_id et path requis"}
        root = self._agent_clone(aid, pid)
        if not root.exists():
            return {"ok": False, "error": "clone introuvable (git_clone ?)"}
        full = self._safe_under(root, path)
        os.makedirs(full.parent, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(full)}

    def _exec_project_read(self, inputs: dict, ws: str) -> dict:
        pid = inputs.get("project_id", "")
        aid = inputs.get("agent_id", "")
        path = inputs.get("path", "")
        if not pid or not aid or not path:
            return {"content": "", "error": "project_id, agent_id et path requis"}
        root = self._agent_clone(aid, pid)
        full = self._safe_under(root, path)
        if not full.exists():
            return {"content": "", "error": "introuvable"}
        return {"content": full.read_text(encoding="utf-8")}

    def _exec_project_list(self, inputs: dict, ws: str) -> dict:
        pid = inputs.get("project_id", "")
        aid = inputs.get("agent_id", "")
        path = inputs.get("path", "")
        if not pid or not aid:
            return {"entries": [], "error": "project_id et agent_id requis"}
        root = self._agent_clone(aid, pid)
        full = self._safe_under(root, path) if path else root
        if not full.is_dir():
            return {"entries": []}
        return {"entries": [{"name": p.name, "type": "dir" if p.is_dir() else "file"}
                           for p in sorted(full.iterdir())]}

    def _exec_project_tree(self, inputs: dict, ws: str) -> dict:
        pid = inputs.get("project_id", "")
        aid = inputs.get("agent_id", "")
        if not pid or not aid:
            return {"files": [], "error": "project_id et agent_id requis"}
        root = self._agent_clone(aid, pid)
        if not root.exists():
            return {"files": []}
        files = [str(p.relative_to(root)) for p in root.rglob("*")
                 if p.is_file() and ".git" not in p.relative_to(root).parts]
        return {"files": sorted(files)}

    # ── Espace commun live (non versionné) ──

    def _exec_common_write(self, inputs: dict, ws: str) -> dict:
        gid = inputs.get("group_id", "")
        path = inputs.get("path", "")
        content = inputs.get("content", "")
        if not gid or not path:
            return {"ok": False, "error": "group_id et path requis"}
        root = self._common_root(gid)
        full = self._safe_under(root, path)
        os.makedirs(full.parent, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(full)}

    def _exec_common_read(self, inputs: dict, ws: str) -> dict:
        gid = inputs.get("group_id", "")
        path = inputs.get("path", "")
        if not gid or not path:
            return {"content": "", "error": "group_id et path requis"}
        root = self._common_root(gid)
        full = self._safe_under(root, path)
        if not full.exists():
            return {"content": "", "error": "introuvable"}
        return {"content": full.read_text(encoding="utf-8")}

    def _exec_common_list(self, inputs: dict, ws: str) -> dict:
        gid = inputs.get("group_id", "")
        path = inputs.get("path", "")
        if not gid:
            return {"entries": [], "error": "group_id requis"}
        root = self._common_root(gid)
        full = self._safe_under(root, path) if path else root
        if not full.is_dir():
            return {"entries": []}
        return {"entries": [{"name": p.name, "type": "dir" if p.is_dir() else "file"}
                           for p in sorted(full.iterdir())]}

    def _exec_common_tree(self, inputs: dict, ws: str) -> dict:
        gid = inputs.get("group_id", "")
        if not gid:
            return {"files": [], "error": "group_id requis"}
        root = self._common_root(gid)
        if not root.exists():
            return {"files": []}
        files = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()]
        return {"files": sorted(files)}

    # ── Réseau 1 : messagerie directe (1:1) ──

    def _exec_message_send(self, inputs: dict, ws: str) -> dict:
        to = inputs.get("to_agent_id", "")
        sender = inputs.get("from_agent_id", inputs.get("agent_id", ""))
        content = inputs.get("content", "")
        if not to:
            return {"ok": False, "error": "to_agent_id requis"}
        box = self._inbox_root(to)
        box.mkdir(parents=True, exist_ok=True)
        msg_id = str(uuid.uuid4())
        (box / f"{msg_id}.json").write_text(json.dumps({
            "from": sender, "content": content, "ts": time.time(),
        }, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "message_id": msg_id}

    def _exec_message_recv(self, inputs: dict, ws: str) -> dict:
        agent_id = inputs.get("agent_id", "")
        if not agent_id:
            return {"messages": [], "error": "agent_id requis"}
        box = self._inbox_root(agent_id)
        if not box.exists():
            return {"messages": []}
        msgs = []
        for f in sorted(box.glob("*.json")):
            try:
                msgs.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
        if inputs.get("clear", False):
            for f in box.glob("*.json"):
                f.unlink()
        return {"messages": msgs, "count": len(msgs)}

    # ── Réseau 2 : chatroom (N:N, par salon/groupe) ──

    def _exec_chatroom_post(self, inputs: dict, ws: str) -> dict:
        cid = inputs.get("chatroom_id", "")
        agent = inputs.get("agent_id", "")
        content = inputs.get("content", "")
        if not cid:
            return {"ok": False, "error": "chatroom_id requis"}
        root = self._comms_root(cid)
        root.mkdir(parents=True, exist_ok=True)
        log = root / "chatroom.jsonl"
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"agent": agent, "content": content,
                                "ts": time.time()}, ensure_ascii=False) + "\n")
        return {"ok": True}

    def _exec_chatroom_read(self, inputs: dict, ws: str) -> dict:
        cid = inputs.get("chatroom_id", "")
        last_n = int(inputs.get("last_n", 50))
        if not cid:
            return {"messages": [], "error": "chatroom_id requis"}
        log = self._comms_root(cid) / "chatroom.jsonl"
        if not log.exists():
            return {"messages": []}
        lines = log.read_text(encoding="utf-8").splitlines()
        msgs = []
        for ln in lines[-last_n:]:
            try:
                msgs.append(json.loads(ln))
            except Exception:
                pass
        return {"messages": msgs, "count": len(msgs)}

    # ── Réseau 4 : LLM résilient (timeout + repli) ──
    def _exec_timeout_llm(self, inputs: dict, ws: str) -> dict:
        """Appel LLM résilient : si le LLM ne répond pas dans `timeout`
        secondes (défaut 60), demande un autre LLM au gestionnaire de LLM
        (LLMManager.assign_llm) et réessaie."""
        provider_ref = inputs.get("provider_ref", "")
        model_ref = inputs.get("model_ref", "")
        if not provider_ref or not model_ref:
            return {"ok": False, "error": "provider_ref et model_ref requis"}
        prompt = inputs.get("prompt", "") or ""
        sys_p = inputs.get("system_prompt", "") or ""
        messages = inputs.get("messages") or []
        if not messages and prompt:
            messages = [{"role": "user", "content": prompt}]
        if not messages:
            return {"ok": False, "error": "messages ou prompt requis"}
        try:
            max_tokens = int(inputs.get("max_tokens", 1024))
            temperature = float(inputs.get("temperature", 0.7))
            timeout = int(inputs.get("timeout", 60))
        except (TypeError, ValueError) as e:
            return {"ok": False, "error": f"paramètre invalide: {e}"}
        fallback = bool(inputs.get("fallback", True))

        from modules.llm_manager.resilient import resilient_chat
        try:
            resp = resilient_chat(
                provider_ref, model_ref, messages,
                timeout=timeout, fallback=fallback,
                max_tokens=max_tokens, temperature=temperature,
                system_prompt=sys_p or None,
            )
        except BridgeError as e:
            return {"ok": False, "error": str(e),
                    "category": getattr(e.category, "value", str(e.category)),
                    "provider_ref": e.provider_ref, "model_ref": e.model_ref}
        except Exception as e:  # noqa
            return {"ok": False, "error": str(e)}
        return {
            "ok": True,
            "content": resp.content,
            "provider_used": getattr(resp, "provider_used", provider_ref),
            "model_used": getattr(resp, "model_used", model_ref),
            "fallbacks": getattr(resp, "fallbacks", 0),
        }


_INSTANCE: SkillManager = _EMPTY


def _get(workspace_root: str = "/tmp") -> SkillManager:
    global _INSTANCE
    if _INSTANCE is _EMPTY:
        _INSTANCE = SkillManager(workspace_root)
    else:
        _INSTANCE.workspace_root = workspace_root
    _INSTANCE.load_all()
    return _INSTANCE


def get_skill(ref: str) -> dict:
    return _get().get(ref)


def expand_workflow(workflow: dict) -> dict:
    return _get().expand(workflow)


def call_skill(fn: str, inputs: dict, ws: str = "/tmp") -> dict:
    return _get(ws).call(fn, inputs)


def list_skills() -> List[dict]:
    mgr = _get()
    mgr.load_all()
    return [dict(v) for v in mgr._defs.values()]
