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

from AgentsCatalogue.lib import get_func as _lib_get_func

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
        impl = spec.get("implementation", {}) or {}
        impl_type = impl.get("type", "")
        func_name = impl.get("function", "")
        inline_code = impl.get("code", "")

        ws = workspace_root or self.workspace_root

        # 1. Code inline dans le YAML (sandboxé par l'agent hôte) :
        #    le YAML fournit une fonction `run(inputs, ws) -> dict`.
        if inline_code and impl_type == "python":
            try:
                ns: Dict[str, Any] = {}
                exec(inline_code, ns)
                run_fn = ns.get("run")
                if not callable(run_fn):
                    raise SkillInputError(f"skill '{fn}' : fonction `run` introuvable dans le code inline")
                return run_fn(inputs, ws)
            except SkillInputError:
                raise
            except Exception as e:
                raise SkillInputError(f"skill '{fn}' : erreur exécution code inline : {e}")

        # 2. Fonction depuis la librairie (AgentsCatalogue/lib) — prioritaire.
        #    Résolution par référence qualifiée (system.file.append_file) ou
        #    alias legacy (_exec_append_file). Le champ implementation.lib
        #    permet de pointer explicitement une fonction de la librairie.
        if impl_type != "python" or not func_name:
            raise SkillInputError(f"skill '{fn}' : pas d'implémentation python")

        lib_ref = impl.get("lib") or func_name
        lib_func = _lib_get_func(lib_ref)
        if lib_func is not None:
            return lib_func(inputs, ws)

        raise SkillInputError(f"skill '{fn}' : fonction lib introuvable : {lib_ref}")

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

    # ── Fichiers étendus (relatif au home de l'agent) ──

    # ── Classification important / work ──

    def _agent_id_from_ws(self, ws: str, inputs: dict) -> str:
        aid = inputs.get("agent_id", "")
        if aid:
            return str(aid)
        parts = Path(ws).parts
        if "memagent" in parts:
            return str(parts[parts.index("memagent") + 1])
        return ""

    # ── Temps ──

    # ── Données / Transform ──

    # ── Texte ──

    # ── Système ──

    _ENV_ALLOWLIST = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL",
                      "MODELWEAVER_HOME", "TERM", "SHELL", "PWD", "OS")

    # ── Agent / Orchestration ──

    # ── Accès hôte (absolu, gated par FsAuthManager) ──

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

    def _unmerged_files(self, root: Path) -> List[str]:
        """Liste des fichiers en conflit de merge (état non résolu)."""
        out = self._git_run(root, ["diff", "--name-only", "--diff-filter=U"])
        return [l.strip() for l in out.get("stdout", "").splitlines() if l.strip()]

    # ── Espace projet (opère sur le clone perso de l'agent) ──

    # ── Espace commun live (non versionné) ──

    # ── Réseau 1 : messagerie directe (1:1) ──

    # ── Réseau 2 : chatroom (N:N, par salon/groupe) ──

    # ── Réseau 4 : LLM résilient (timeout + repli) ──
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
