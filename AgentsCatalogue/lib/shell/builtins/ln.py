"""ln — créer des liens (symboliques)."""

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_ln(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    symbolic = True
    filenames: List[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-s":
            symbolic = True
            i += 1
        elif a == "-f":
            i += 1
        elif a.startswith("-"):
            i += 1
        else:
            filenames.append(a)
            i += 1

    if len(filenames) < 2:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: ln [-s] <target> <link>"}
    target = auth.check_path(Path(workdir) / filenames[0])
    link = auth.check_path(Path(workdir) / filenames[1])
    if link.exists():
        link.unlink()
    if symbolic:
        os.symlink(target, link)
    else:
        os.link(target, link)
    return {"exit_code": 0, "stdout": "", "stderr": ""}