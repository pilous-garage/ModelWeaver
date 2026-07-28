"""grep via findstr sur Windows, grep sur Unix."""

import sys
import subprocess
from typing import List, Optional


def execute(args: List[str], stdin: Optional[str], workdir: str) -> dict:
    if sys.platform == "win32":
        cmd = ["findstr"] + args
    else:
        cmd = ["grep"] + args
    proc = subprocess.run(
        cmd,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=workdir,
    )
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }