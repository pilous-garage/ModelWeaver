"""Registry et exécution des commandes built-in."""

import importlib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth

BuiltInFunction = Callable[
    [List[str], Optional[str], str, "ShellAuth", Optional[str], Optional[str]],
    Dict[str, Any],
]

_BUILTIN_MODULES: Dict[str, str] = {
    "cd": ".cd",
    "echo": ".echo",
    "pwd": ".pwd",
    "ls": ".ls",
    "cat": ".cat",
    "head": ".head",
    "tail": ".tail",
    "wc": ".wc",
    "find": ".find",
    "sort": ".sort",
    "uniq": ".uniq",
    "diff": ".diff",
    "mkdir": ".mkdir",
    "rmdir": ".rmdir",
    "rm": ".rm",
    "cp": ".cp",
    "mv": ".mv",
    "touch": ".touch",
    "env": ".env",
    "which": ".which",
    "uname": ".uname",
    "hostname": ".hostname",
    "date": ".date",
    "sleep": ".sleep",
    "clear": ".clear",
    "export": ".export",
    "unset": ".unset",
    "cut": ".cut",
    "tr": ".tr",
    "paste": ".paste",
    "true": ".true",
    "false": ".false",
    "exit": ".exit",
    "help": ".help",
    "pskill": ".pskill",
    "ps": ".ps",
    "history": ".history",
    "sed": ".sed",
    "awk": ".awk",
    "chmod": ".chmod",
    "chown": ".chown",
    "ln": ".ln",
    "alias": ".alias",
    "unalias": ".unalias",
    "source": ".source",
    "jobs": ".jobs",
    "fg": ".jobs",
    "bg": ".jobs",
    "read": ".__init__",
    "write": ".__init__",
}


def _load_builtin(cmd: str) -> Callable:
    module_path = _BUILTIN_MODULES.get(cmd)
    if module_path is None:
        raise RuntimeError(f"builtin inconnu : {cmd}")
    if cmd in ("read", "write"):
        return globals()[f"cmd_{cmd}"]
    module = importlib.import_module(module_path, package="AgentsCatalogue.lib.shell.builtins")
    return getattr(module, f"cmd_{cmd}")


def cmd_read(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    if not args:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: read <path>"}
    target = auth.check_path(Path(workdir) / args[0])
    if not target.exists():
        return {"exit_code": 1, "stdout": "", "stderr": f"fichier introuvable : {target}"}
    if not target.is_file():
        return {"exit_code": 1, "stdout": "", "stderr": f"pas un fichier : {target}"}
    content = target.read_text(encoding="utf-8")
    return {"exit_code": 0, "stdout": content, "stderr": ""}


def cmd_write(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    if len(args) < 2:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: write <path> <content>"}
    target = auth.check_path(Path(workdir) / args[0])
    content = " ".join(args[1:])
    if stdin:
        content = stdin
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"exit_code": 0, "stdout": "", "stderr": ""}

BUILTINS: Dict[str, BuiltInFunction] = {}


def execute_builtin(
    cmd: str,
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    fn = BUILTINS.get(cmd) or _load_builtin(cmd)
    return fn(args, stdin, workdir, auth, stdout_to, stderr_to)