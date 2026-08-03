"""Executor — lexer, pipe handler, built-in dispatch, syscall fallback."""

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .auth import ShellAuth
from .lexer import (
    Token,
    tokenize,
    expand_vars,
    expand_cmd_subst,
    expand_glob,
    split_pipeline,
    parse_segment,
)
from .log import ShellLog


class ExecuteError(Exception):
    pass


def _which(cmd: str) -> Optional[str]:
    """Cherche une commande dans PATH."""
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    for d in path_dirs:
        full = Path(d) / cmd
        if full.exists() and os.access(str(full), os.X_OK):
            return str(full)
    return None


def _resolve_shebang(path: Path) -> Optional[str]:
    """Lit le #! d'un script, retourne l'interpréteur."""
    try:
        with open(path, "rb") as f:
            header = f.read(128)
        if header.startswith(b"#!"):
            raw = header[2:].split(b"\n")[0].decode("latin-1").strip()
            return raw.split()[0] if raw else None
    except Exception:
        return None
    return None


class ShellExecutor:
    def __init__(
        self,
        workdir: str,
        auth: ShellAuth,
        shell_id: str,
        env: Optional[Dict[str, str]] = None,
        log: Optional[ShellLog] = None,
        aliases: Optional[Dict[str, str]] = None,
        last_exit_code: int = 0,
        bg_jobs: Optional[Dict[int, dict]] = None,
    ):
        self.workdir = Path(workdir).resolve()
        self.auth = auth
        self.shell_id = shell_id
        self._env = env if env is not None else {}
        self._log = log
        self._aliases = aliases if aliases is not None else {}
        self._last_exit_code = last_exit_code
        self._bg_jobs = bg_jobs if bg_jobs is not None else {}
        self._next_job_id = 1

    # ── entry point ───────────────────────────────

    def execute(self, cmd_line: str) -> dict:
        cmd_line = cmd_line.strip()
        if not cmd_line:
            return {"status": "success", "stdout": "", "stderr": "", "exit_code": 0}

        cmd_line = self._expand_alias(cmd_line)
        cmd_line = self._expand_bang(cmd_line)

        # $? dans l'env pour expand_vars
        full_env = {**self._env, "?": str(self._last_exit_code)}
        tokens = tokenize(cmd_line)
        tokens = expand_vars(tokens, full_env)
        tokens = expand_cmd_subst(tokens, self._exec_cmd_subst)
        tokens = expand_glob(tokens, str(self.workdir))

        # Détecter background &
        bg = any(t.type == "background" for t in tokens)
        tokens = [t for t in tokens if t.type != "background"]

        segments = split_pipeline(tokens)
        if not segments or not segments[0]:
            return {"status": "success", "stdout": "", "exit_code": 0}

        result = self._execute_pipeline(segments, bg=bg)
        return result

    def _exec_cmd_subst(self, cmd: str) -> dict:
        return self.execute(cmd)

    def _expand_alias(self, cmd: str) -> str:
        if not self._aliases:
            return cmd
        first_word = cmd.split()[0] if cmd else ""
        if first_word in self._aliases:
            return self._aliases[first_word] + cmd[len(first_word):]
        return cmd

    def _expand_bang(self, cmd: str) -> str:
        if not cmd.startswith("!"):
            return cmd
        rest = cmd[1:]
        if not rest.isdigit():
            return cmd
        n = int(rest)
        if self._log is None:
            return f"echo \"!{n}: historique non disponible\""
        cmds = self._log.all_commands()
        if n < 1 or n > len(cmds):
            return f"echo \"!{n}: événement introuvable\""
        return cmds[n - 1].get("cmd", cmd)

    # ── pipeline ────────────────────────────────────

    def _execute_pipeline(self, segments: List[List[Token]], bg: bool = False) -> dict:
        stdin_data: Optional[str] = None
        result = {}
        for i, seg in enumerate(segments):
            result = self._execute_segment(seg, stdin_data)
            if result.get("exit_code", 0) != 0 and i < len(segments) - 1:
                return result
            stdin_data = result.get("stdout")

        if bg:
            job_id = self._next_job_id
            self._next_job_id += 1
            self._bg_jobs[job_id] = {
                "pid": result.get("pid", 0),
                "cmd": " | ".join(
                    " ".join(t.text for t in seg if t.type == "word")
                    for seg in segments
                ),
                "status": "running",
            }
            return {
                "exit_code": 0,
                "stdout": f"[{job_id}] {result.get('pid', 0)}\n",
                "stderr": "",
                "status": "background",
                "job_id": job_id,
            }

        return result

    def _execute_segment(self, tokens: List[Token], stdin_data: Optional[str] = None) -> dict:
        args, redirects = parse_segment(tokens)
        if not args:
            return {"status": "success", "stdout": "", "exit_code": 0}
        base_cmd = args[0]
        cmd_args = args[1:]

        if "stdin" in redirects:
            stdin_data = self._read_vfs_file(redirects["stdin"])

        # Essayer builtin
        result = self._try_builtin(base_cmd, cmd_args, stdin_data)
        if result is not None:
            return self._apply_redirects(result, redirects)

        # source inline
        if base_cmd == "source":
            return self._exec_source(cmd_args)

        return self._run_fallback(base_cmd, cmd_args, stdin_data, redirects)

    # ── dispatch builtins ───────────────────────────

    def _try_builtin(self, cmd: str, args: List[str], stdin: Optional[str]) -> Optional[dict]:
        try:
            self.auth._shell_env = self._env
            self.auth._shell_log = self._log
            self.auth._shell_aliases = self._aliases
            self.auth._shell_bg_jobs = self._bg_jobs
            from .builtins import execute_builtin
            return execute_builtin(cmd, args, stdin, str(self.workdir), self.auth, None, None)
        except RuntimeError:
            return None
        except ExecuteError as e:
            return {"status": "error", "error": str(e), "exit_code": 1, "stdout": "", "stderr": str(e)}
        except Exception as e:
            return {"status": "error", "error": f"builtin {cmd}: {e}", "exit_code": 1, "stdout": "", "stderr": str(e)}

    # ── source — exécute un fichier ligne à ligne ───

    def _exec_source(self, args: List[str]) -> dict:
        if not args:
            return {"exit_code": 1, "stdout": "", "stderr": "usage: source <fichier>"}
        target = self.auth.check_path(self.workdir / args[0])
        if not target.exists():
            return {"exit_code": 1, "stdout": "", "stderr": f"fichier introuvable : {target}"}
        try:
            lines = target.read_text(encoding="utf-8").splitlines()
        except Exception as e:
            return {"exit_code": 1, "stdout": "", "stderr": str(e)}
        last_result = {"exit_code": 0, "stdout": "", "stderr": ""}
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            last_result = self.execute(line)
        return last_result

    # ── redirections VFS-aware ──────────────────────

    def _apply_redirects(self, result: dict, redirects: Dict[str, str]) -> dict:
        if "stdout" in redirects:
            target = self._check_redirect_path(redirects["stdout"])
            mode = "a" if redirects.get("append") else "w"
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, mode, encoding="utf-8") as f:
                f.write(result.get("stdout", ""))
            result["stdout"] = ""
        if "stderr" in redirects:
            target = self._check_redirect_path(redirects["stderr"])
            mode = "a" if redirects.get("append") else "w"
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, mode, encoding="utf-8") as f:
                f.write(result.get("stderr", ""))
            result["stderr"] = ""
        return result

    def _check_redirect_path(self, path_str: str) -> Path:
        p = Path(path_str)
        if not p.is_absolute():
            p = self.workdir / p
        return self.auth.check_path(p)

    def _read_vfs_file(self, path_str: str) -> str:
        p = self._check_redirect_path(path_str)
        if not p.exists():
            raise ExecuteError(f"fichier introuvable pour redirection < : {p}")
        return p.read_text(encoding="utf-8")

    # ── fallback subprocess ──────────────────────────

    def _rewrite_git_clone(self, args: List[str]) -> Optional[Tuple[List[str], Optional[str]]]:
        """Réécrit `git clone <url-github>` vers le dépôt central BARE local.

        Équivalent du skill git_clone (git_ops.py) : si l'URL cible un projet
        présent dans {mw_home}/repos/, on clone depuis le bare local au lieu
        du réseau (le repo GitHub mw-swarm n'existe pas publiquement). Retourne
        (args_réécrits, destination_du_clone) ou None si rien à réécrire.
        """
        try:
            from services._common import mw_home
        except Exception:
            return None
        if not args or args[0] != "clone":
            return None
        url_idx = None
        for i, a in enumerate(args[1:], start=1):
            if a.startswith(("https://", "http://", "git@")) or a.endswith(".git"):
                url_idx = i
                break
        if url_idx is None:
            return None
        url = args[url_idx].rstrip("/")
        name = url.rsplit("/", 1)[-1]
        if name.endswith(".git"):
            name = name[:-4]
        local = mw_home() / "repos" / f"{name}.git"
        if not local.exists():
            return None
        new_args = list(args)
        new_args[url_idx] = str(local)
        # Destination du clone : dernier argument non optionnel, sinon défaut
        # (nom du repo dans le workdir courant).
        dest = None
        for a in new_args[url_idx + 1:]:
            if not a.startswith("-"):
                dest = a
        if dest is None:
            dest = name
        return new_args, dest

    def _run_fallback(self, cmd: str, args: List[str], stdin: Optional[str], redirects: Dict[str, str]) -> dict:
        if not self.auth.is_command_allowed(cmd):
            return {"status": "error", "error": f"commande '{cmd}' non autorisée (whitelist)", "exit_code": 126, "stdout": "", "stderr": f"Command '{cmd}' not in whitelist"}

        lib_path = self.auth.load_system_lib(cmd)
        executable = lib_path or cmd

        # Vérifier si c'est un chemin de fichier exécutable
        cmd_path = Path(self.workdir / cmd) if "/" in cmd else None
        if cmd_path and cmd_path.exists() and os.access(str(cmd_path), os.X_OK):
            executable = str(cmd_path)
            # Shebang
            interp = _resolve_shebang(cmd_path)
            if interp:
                full_args = [interp, str(cmd_path)] + args
            else:
                full_args = [str(cmd_path)] + args
        else:
            found_path = _which(cmd)
            if found_path:
                executable = found_path
            full_args = [executable] + args

        # git clone d'une URL non publique → dépôt central local
        cloned_dest = None
        if cmd == "git":
            rewritten = self._rewrite_git_clone(args)
            if rewritten is not None:
                new_args, cloned_dest = rewritten
                full_args = [executable] + new_args

        full_env = {**os.environ, **self._env}
        try:
            proc = subprocess.Popen(
                full_args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(self.workdir),
                env=full_env,
            )
            from .tracker import tracker
            tracker.register(proc.pid, self.auth.agent_id, self.auth.team_id, cmd)
            stdout, stderr = proc.communicate(input=stdin, timeout=30)
            exit_code = proc.returncode
        except FileNotFoundError:
            return {"status": "error", "error": f"commande introuvable : {cmd}", "exit_code": 127, "stdout": "", "stderr": f"Command not found: {cmd}"}
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            exit_code = 124

        if exit_code == 0 and cloned_dest:
            # Identité git locale (sinon commit échoue en env vierge) —
            # identique au comportement de git_clone (git_ops._git_identity).
            dest_path = Path(self.workdir / cloned_dest)
            if dest_path.exists():
                for c in (["config", "user.email", f"{self.auth.agent_id}@modelweaver.local"],
                          ["config", "user.name", f"agent-{self.auth.agent_id}"]):
                    try:
                        subprocess.run(["git", "-C", str(dest_path)] + c,
                                       capture_output=True, timeout=10)
                    except Exception:
                        pass
                stderr += "\n[git_clone] cloné depuis le dépôt central local"

        return {"status": "success" if exit_code == 0 else "error", "stdout": stdout, "stderr": stderr, "exit_code": exit_code}