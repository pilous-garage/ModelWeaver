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


class SkillProxy:
    """Référence runtime à un skill du catalogue (objet-foncteur).

    Permet d'avoir une variable OBJET dans un foncteur :
        this.sort = catalogue.utils.bubble_sort   # SkillProxy
        this.sort([3, 1, 2])                      # → call_skill main
        this.sort.second(x)                       # → entrypoint second

    L'appel avec une liste/objet non-dict est converti en
    inputs = {"value": <arg>} (entrée unique par défaut) ; un dict est passé
    tel quel. Le proxy se comporte comme un callable, pas une fonction simple.
    """

    def __init__(self, ref: str, agent_id: str = ""):
        self._ref = ref
        self._agent_id = agent_id

    def __call__(self, *args, **kwargs):
        entrypoint = kwargs.pop("entrypoint", "main")
        if len(args) == 1 and isinstance(args[0], dict) and not kwargs:
            inputs = dict(args[0])
        else:
            inputs = dict(kwargs)
            if args:
                inputs["value"] = args[0] if len(args) == 1 else list(args)
        return call_skill(self._ref, inputs, "/tmp",
                          agent_id=self._agent_id, entrypoint=entrypoint)

    def __getattr__(self, entrypoint: str):
        if entrypoint.startswith("_"):
            raise AttributeError(entrypoint)
        return _ProxyEntrypoint(self, entrypoint)

    def __repr__(self):
        return f"<skill-proxy:{self._ref}>"


class _ProxyEntrypoint:
    """f.second(x) → appelle l'entrypoint `second` du skill référencé."""

    def __init__(self, proxy: "SkillProxy", entrypoint: str):
        self._proxy = proxy
        self._entrypoint = entrypoint

    def __call__(self, *args, **kwargs):
        return self._proxy(*args, entrypoint=self._entrypoint, **kwargs)


class SkillManager:
    def __init__(self, home_root: str = "/tmp"):
        self.home_root = home_root
        self._defs: Dict[str, dict] = {}
        self._categories: Dict[str, List[str]] = {}
        self._lock = threading.Lock()
        self._loaded = False
        # Instances de foncteurs vivantes : {(agent_id, skill_ref) -> instance}
        # L'état de chaque instance vit en MÉMOIRE (thread) ; c'est le FSM qui
        # le persiste (save_step / instant_mem JSON) à chaque step.
        self._functors: Dict[Tuple[str, str], Any] = {}
        self._functor_classes: Dict[str, Any] = {}   # skill_ref -> classe compilée
        self._functor_ns: Dict[str, Dict[str, Any]] = {}  # skill_ref -> ns exec

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
                for f in sorted(cat_dir.rglob("*.skill.yaml")):
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
        # Format sans catégorie : "read_file@v1" -> base="read_file" ver="v1"
        if "/" not in ref:
            base, _, ver = ref.partition("@")
            return self._resolve_by_suffix(base, ver)
        cat, rest = (ref.split("/", 1) + [""])[:2]
        base, _, ver = rest.partition("@")
        if ver:
            exact = f"{cat}/{base}@{ver}"
            if exact in self._defs:
                return exact
            return self._resolve_by_suffix(base, ver)
        entries = [k for k in self._defs if k.startswith(f"{cat}/{base}")]
        if not entries:
            return self._resolve_by_suffix(base, "")
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

    def _resolve_by_suffix(self, base: str, ver: str) -> str:
        """Match par fin de nom normalisé : 'git/clone@v1' et 'clone@v1'
        doivent matcher 'git/git_clone@v1' (le segment contient le préfixe)."""
        norm = base.replace("_", "")
        if not norm:
            return base or ver
        hits = []
        for k in self._defs:
            kbase = k.split("/")[-1].split("@")[0]
            kver = k.split("@")[-1] if "@" in k else ""
            knorm = kbase.replace("_", "")
            if ver and kver != ver:
                continue
            if not ver and kver != "v1":
                continue
            if knorm.endswith(norm) or norm.endswith(knorm):
                hits.append(k)
        if not hits:
            # Fuzzy fallback : le LLM insère parfois du bruit dans le nom
            # (list_dir_varglob_v1 → varglob au lieu de glob). On cherche un
            # skill dont le nom contient un sous-mot significatif (≥4 lettres)
            # de la base demandée, priorisé par la plus longue correspondance.
            fuzzy = []
            for k in self._defs:
                kbase = k.split("/")[-1].split("@")[0]
                kver = k.split("@")[-1] if "@" in k else ""
                if ver and kver != ver:
                    continue
                if not ver and kver != "v1":
                    continue
                knorm = kbase.replace("_", "")
                best = 0
                for i in range(len(norm) - 3):
                    sub = norm[i:i + 4]
                    if sub in knorm and len(sub) > best:
                        best = len(sub)
                if best >= 4:
                    fuzzy.append((best, k))
            if fuzzy:
                fuzzy.sort(key=lambda x: (-x[0], len(x[1])))
                return fuzzy[0][1]
            return base or ver
        if len(hits) == 1:
            return hits[0]
        # Plusieurs hits : prioriser les matchs exacts (fin de nom complète),
        # puis le nom le plus long (le plus précis).
        # Règle clé : un skill dont la BASE == norm (ex. docker/run@v1 pour
        # "run") prime sur un skill dont la base contient juste le suffixe
        # (ex. host/host_run@v1) — sinon le LLM qui appelle `run_v1` reçoit
        # host_run au lieu du skill voulu.
        exact = [k for k in hits
                 if k.split("/")[-1].split("@")[0].replace("_", "").endswith(norm)]
        exact_base = [k for k in exact
                      if k.split("/")[-1].split("@")[0].replace("_", "") == norm]
        if exact_base:
            exact = exact_base
        if len(exact) == 1:
            return exact[0]
        if exact:
            # À longueur égale, préférer la catégorie `file/` (workspace) —
            # ex. `glob` → file/glob plutôt que system/home/glob, sinon le
            # LLM globbe dans le home de l'agent au lieu des fichiers projet.
            file_exact = [k for k in exact if k.split("/")[0] == "file"]
            if file_exact:
                return sorted(file_exact, key=len)[-1]
            return sorted(exact, key=len)[-1]
        return sorted(hits, key=len)[-1]

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

    def _get_functor_class(self, fn: str, inline_code: str) -> Optional[type]:
        """Compile et cache la CLASSE foncteur d'un skill (clé = skill_ref).

        La classe est compilée une seule fois ; les INSTANCES sont créées par
        _get_functor_instance (une par agent_id). Un skill inline peut définir :
          - une fonction `run(inputs, home)` (ancien contrat, sans état) ;
          - une classe `Skill` (ou `skill`) avec entrypoints : `main` (f()),
            et tout autre entrypoint (f.second(), f.run_shell()...).
        """
        if fn in self._functor_classes:
            return self._functor_classes[fn]
        from services.catalogue_runtime import build_namespace
        ns: Dict[str, Any] = build_namespace()   # injecte `catalogue`
        try:
            exec(inline_code, ns)
        except Exception as e:
            raise SkillInputError(f"skill '{fn}' : erreur compilation code inline : {e}")
        cls = None
        for name in ("Skill", "skill", "Functor", "Foncteur"):
            cand = ns.get(name)
            if isinstance(cand, type):
                cls = cand
                break
        self._functor_classes[fn] = cls
        self._functor_ns[fn] = ns
        return cls

    def _get_functor_instance(self, fn: str, cls: type, agent_id: str,
                              home: str) -> Any:
        """Retourne l'instance vivante du foncteur pour (agent_id, skill_ref).

        L'état est conservé en mémoire entre les appels (variables `self.*`).
        Le FSM le persiste à chaque step (save_step / instant_mem JSON)."""
        key = (str(agent_id), fn)
        inst = self._functors.get(key)
        if inst is None:
            # Rebinde `catalogue` dans les globales de la classe (résolution
            # runtime par agent : self.sort = catalogue.utils.bubble_sort).
            ns = self._functor_ns.get(fn)
            if ns is not None:
                from services.catalogue_runtime import (
                    make_catalogue, call_skill as _cs,
                )
                ns["catalogue"] = make_catalogue(agent_id=str(agent_id))
                ns["call_skill"] = _cs
                # team/daemon/eval_path — objets racines par agent.
                try:
                    from services.catalogue_objects import build_resolution_namespace
                    rns = build_resolution_namespace(
                        int(agent_id) if str(agent_id).isdigit() else None)
                    ns.update(rns)
                except Exception:
                    pass
            try:
                inst = cls()   # __init__(self)
            except TypeError:
                inst = cls.__new__(cls)
            self._functors[key] = inst
        return inst

    def call(self, fn: str, inputs: Dict[str, Any],
             home_root: Optional[str] = None,
             agent_id: str = "",
             entrypoint: str = "main") -> Dict[str, Any]:
        spec = self.get(fn)
        return self.call_spec(spec, inputs, home_root=home_root,
                              agent_id=agent_id, entrypoint=entrypoint)

    def call_spec(self, spec: Dict[str, Any], inputs: Dict[str, Any],
                  home_root: Optional[str] = None,
                  agent_id: str = "",
                  entrypoint: str = "main") -> Dict[str, Any]:
        """Exécute une définition de skill (spec dict) — y compris une
        SUB-SKILL inline (définie au plus haut niveau de l'agent), sans
        passer par le catalogue de fichiers."""
        fn = spec.get("name") or "?"
        impl = spec.get("implementation", {}) or {}
        impl_type = impl.get("type", "")
        func_name = impl.get("function", "")
        inline_code = impl.get("code", "")

        home = home_root or self.home_root

        # 1. Code inline dans le YAML (sandboxé par l'agent hôte) :
        #    fonction `run(inputs, home)` (ancien contrat) OU classe foncteur
        #    (entrypoints main/second/... + état via self.*).
        if inline_code and impl_type == "python":
            try:
                cls = self._get_functor_class(fn, inline_code)
                if cls is not None:
                    inst = self._get_functor_instance(fn, cls, agent_id, home)
                    entry = getattr(inst, entrypoint, None)
                    if not callable(entry):
                        raise SkillInputError(
                            f"skill '{fn}' : entrypoint '{entrypoint}' introuvable "
                            f"sur le foncteur (disponibles: {[m for m in dir(inst) if not m.startswith('_')]})")
                    return entry(inputs, home)
                # Fonction simple (contrat historique)
                ns: Dict[str, Any] = {}
                exec(inline_code, ns)
                run_fn = ns.get("run")
                if not callable(run_fn):
                    raise SkillInputError(f"skill '{fn}' : ni classe foncteur ni fonction `run` dans le code inline")
                return run_fn(inputs, home)
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
            return lib_func(inputs, home)

        raise SkillInputError(f"skill '{fn}' : fonction lib introuvable : {lib_ref}")

    def _safe_path(self, path: str, home_root: str) -> str:
        home_root_abs = os.path.abspath(home_root)
        norm = os.path.normpath(path)
        if os.path.isabs(norm):
            # Chemin absolu pointant déjà dans le home de l'agent : le
            # re-router vers son équivalent relatif (sinon arborescence
            # dupliquée home/{home_root}/…).
            if norm == home_root_abs or norm.startswith(home_root_abs + os.sep):
                norm = os.path.relpath(norm, home_root_abs)
            else:
                norm = norm.lstrip("/")
        full = os.path.join(home_root_abs, norm)
        full_norm = os.path.normpath(full)
        if not (full_norm == home_root_abs
                or full_norm.startswith(home_root_abs + os.sep)):
            raise PermissionError("chemin hors home")
        return full_norm

    # ── Résolution de chemin dans le home de l'agent (relatif) ──
    def _read_index(self, home: str) -> dict:
        p = os.path.join(home, INDEX_FILE)
        if os.path.exists(p):
            try:
                return json.loads(Path(p).read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _write_index(self, home: str, idx: dict) -> None:
        os.makedirs(home, exist_ok=True)
        Path(os.path.join(home, INDEX_FILE)).write_text(
            json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")

    def _index_add(self, home: str, abs_file: str) -> None:
        home = os.path.abspath(home)
        imp = os.path.join(home, "important")
        af = os.path.abspath(abs_file)
        if not (af == imp or af.startswith(imp + os.sep)):
            return
        stem = os.path.splitext(os.path.basename(af))[0].lower()
        if not stem:
            return
        idx = self._read_index(home)
        if stem in idx:  # ambiguïté : déjà indexé
            return
        idx[stem] = os.path.relpath(af, home)
        self._write_index(home, idx)

    def _index_remove(self, home: str, abs_file: str) -> None:
        home = os.path.abspath(home)
        imp = os.path.join(home, "important")
        af = os.path.abspath(abs_file)
        if not (af == imp or af.startswith(imp + os.sep)):
            return
        stem = os.path.splitext(os.path.basename(af))[0].lower()
        idx = self._read_index(home)
        rel = os.path.relpath(af, home)
        if idx.get(stem) == rel:
            del idx[stem]
            self._write_index(home, idx)

    def _resolve_read_path(self, path: str, home: str) -> str:
        """Résout un chemin de lecture : alias d'index puis relatif sous home."""
        home = os.path.abspath(home)
        idx = self._read_index(home)
        base = path.split("/")[-1]
        if "/" not in path and path in idx:
            return os.path.join(home, idx[path])
        if "/" not in path and base in idx:
            return os.path.join(home, idx[base])
        return self._safe_path(path, home)

    def _classify_write_path(self, path: str, home: str) -> str:
        """Résout un chemin d'écriture : sous-dossier explicite honoré,
        sinon nom connu -> important/, sinon -> work/."""
        home = os.path.abspath(home)
        if "/" in path:
            return self._safe_path(path, home)
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
        return mw_home() / "agent_home" / str(agent_id) / "mem"

    # ── Fichiers étendus (relatif au home de l'agent) ──

    # ── Classification important / work ──

    def _agent_id_from_home(self, home: str, inputs: dict) -> str:
        aid = inputs.get("agent_id", "")
        if aid:
            return str(aid)
        parts = Path(home).parts
        if "agent_home" in parts:
            return str(parts[parts.index("agent_home") + 1])
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
    #   - Clone par agent     : mw_home()/agent_home/{agent_id}/workspace/{project_id}
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
        return (mw_home() / "agent_home" / str(agent_id)
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


def _get(home_root: str = "/tmp") -> SkillManager:
    global _INSTANCE
    if _INSTANCE is _EMPTY:
        _INSTANCE = SkillManager(home_root)
    else:
        _INSTANCE.home_root = home_root
    _INSTANCE.load_all()
    return _INSTANCE


def get_skill(ref: str) -> dict:
    return _get().get(ref)


def expand_workflow(workflow: dict) -> dict:
    return _get().expand(workflow)


# ── Entrypoints réservés par défaut ───────────────────────────────────────
# Un agent peut ne PAS déclarer les entrypoints de supervision (cancel, pause,
# pause_hard, resume, reset, clear_home). Le FSM inlinera alors la version
# DÉFAUT. Si l'agent les déclare, sa version prévaut. Voir docs/entrypoints_spec.md.
_DEFAULT_ENTRYPOINT_STEPS = {
    "cancel": [
        {"id": "cancel_agent", "type": "end", "status": "CANCELLED"},
    ],
    "pause": [
        {"id": "pause_wait", "type": "end", "status": "PAUSED"},
    ],
    "pause_hard": [
        {"id": "pause_hard_do", "type": "end", "status": "PAUSED"},
    ],
    "resume": [
        {"id": "resume_continue", "type": "end", "status": "RUNNING"},
    ],
    "reset": [
        {"id": "reset_vars", "type": "call",
         "fn": "workflow/reset_variable_after_change_task@v1",
         "inputs": {"agent_id": "{{agent_id}}"},
         "next": "reset_end"},
        {"id": "reset_end", "type": "end", "status": "RESET"},
    ],
    "clear_home": [
        {"id": "clear_home_do", "type": "call",
         "fn": "host/clear_home@v1",
         "next": "clear_end"},
        {"id": "clear_end", "type": "end", "status": "RESET"},
    ],
}

# hard : true = interruption immédiate ; false = attendre la fin de la step.
_DEFAULT_ENTRYPOINT_FLAGS = {
    "cancel": True,
    "pause": False,
    "pause_hard": True,
    "resume": False,
    "reset": True,
    "clear_home": True,
}


def with_default_entrypoints(workflow: dict, is_sub_agent: bool = False) -> dict:
    """Garantit les entrypoints réservés par défaut dans un workflow d'agent.

    Le workflow reçu est un dict {entrypoints: {nom: {steps, hard?...}}} (ou
    {steps: [...]} si c'est déjà l'entrypoint résolu). Pour chaque entrypoint
    réservé absent, on injecte la version par défaut (steps + flag hard).

    Conforme à docs/entrypoints_spec.md : tous les agents héritent d'agent_default
    (entrypoints basiques + variables) ; les SUB-agents (spawnés par un maître,
    is_sub_agent=True) héritent de sub_agent_default (variable `master` en plus).
    Les entrypoints déclarés par l'agent lui-même GAGNENT (redéfinition).
    """
    wf = dict(workflow)
    eps = wf.get("entrypoints")
    if not isinstance(eps, dict):
        return wf  # workflow simple (steps) — pas de réservé à injecter
    # Héritage agent_default / sub_agent_default (catalogue).
    try:
        from services.api.catalogue_agents import _load_agent_yaml_config
        _base_name = "sub_agent_default" if is_sub_agent else "agent_default"
        base = _load_agent_yaml_config("", agent_name=_base_name) or {}
        base_eps = base.get("entrypoints") or {}
        base_vars = base.get("variables") or {}
    except Exception:
        base_eps, base_vars = {}, {}
    for name, body in (base_eps or {}).items():
        if name not in eps and isinstance(body, dict):
            eps[name] = {
                "hard": body.get("hard", False),
                "steps": [dict(s) for s in (body.get("steps") or [])],
            }
    # Variables basiques héritées (agent_default / sub_agent_default).
    wf.setdefault("variables", {})
    for k, v in (base_vars or {}).items():
        wf["variables"].setdefault(k, v)
    # Fallback : steps réservés inline (si catalogue indisponible).
    for name, steps in _DEFAULT_ENTRYPOINT_STEPS.items():
        if name not in eps:
            eps[name] = {
                "hard": _DEFAULT_ENTRYPOINT_FLAGS.get(name, False),
                "steps": [dict(s) for s in steps],
            }
    return wf


def call_skill(fn: str, inputs: dict, home: str = "/tmp",
               agent_id: str = "", entrypoint: str = "main") -> dict:
    return _get(home).call(fn, inputs, agent_id=agent_id, entrypoint=entrypoint)


def call_skill_spec(spec: dict, inputs: dict, home: str = "/tmp",
                    agent_id: str = "", entrypoint: str = "main") -> dict:
    """Exécute une SUB-SKILL inline (spec dict, sans passer par le catalogue)."""
    return _get(home).call_spec(spec, inputs, home_root=home,
                                agent_id=agent_id, entrypoint=entrypoint)


def functor_state(agent_id: str, fn: str) -> dict:
    """État actuel (en mémoire) d'un foncteur → dict JSON-sérialisable.

    Retourne les attributs `self.*` de l'instance vivante. Vides si le
    foncteur n'a pas encore été instancié. Le FSM persiste cet état à chaque
    step (save_step / instant_mem)."""
    mgr = _get()
    key = (str(agent_id), fn)
    inst = mgr._functors.get(key)
    if inst is None:
        return {}
    return dict(getattr(inst, "__dict__", {}))


def functor_restore_state(agent_id: str, fn: str, state: dict) -> None:
    """Restaure l'état d'un foncteur au démarrage (hydratation instant_mem).

    Crée l'instance si nécessaire puis injecte les attributs sauvegardés."""
    mgr = _get()
    key = (str(agent_id), fn)
    if key not in mgr._functors:
        # force l'instance (défaut) pour pouvoir injecter l'état
        try:
            spec = mgr.get(fn)
            impl = spec.get("implementation", {}) or {}
            code = impl.get("code", "")
            if code:
                cls = mgr._get_functor_class(fn, code)
                if cls is not None:
                    inst = mgr._get_functor_instance(fn, cls, str(agent_id), "/tmp")
                    mgr._functors[key] = inst
        except Exception:
            pass
    inst = mgr._functors.get(key)
    if inst is not None:
        for k, v in (state or {}).items():
            setattr(inst, k, v)


def functor_state_all(agent_id: str) -> dict:
    """État de TOUS les foncteurs d'un agent → {skill_ref: {attr: val}}."""
    mgr = _get()
    out = {}
    for (aid, fn), inst in mgr._functors.items():
        if aid == str(agent_id):
            out[fn] = dict(getattr(inst, "__dict__", {}))
    return out


def list_skills() -> List[dict]:
    mgr = _get()
    mgr.load_all()
    return [dict(v) for v in mgr._defs.values()]
