"""Interface interactive (REPL) pour le shell.

Usage :
    from AgentsCatalogue.lib.shell.interactive import InteractiveShell
    repl = InteractiveShell(shell)
    repl.run()  # boucle read-eval-print

Ou pour les LLM :
    result = repl.execute("ls *.py")  # une seule commande
    repl.close()
"""

from typing import Any, Dict, Optional

from .shell import Shell


class InteractiveShell:
    """REPL interactif pour le shell interne.

    Wrapper qui gère le cycle read-eval-print.
    Peut être utilisé par un agent LLM pour interagir
    avec le shell comme le ferait un humain : une commande
    à la fois, avec l'historique visible."""

    def __init__(self, shell: Shell, prompt: str = "λ "):
        self.shell = shell
        self.prompt = prompt
        self._running = False
        self._saved_cmd: Optional[str] = None

    def execute(self, cmd: str) -> Dict[str, Any]:
        """Exécute une commande et retourne le résultat formaté."""
        if not cmd.strip():
            return {"exit_code": 0, "stdout": "", "stderr": "", "status": "empty"}
        result = self.shell.run(cmd)
        out = result.get("stdout", "")
        err = result.get("stderr", "")
        code = result.get("exit_code", 1)
        return {
            "exit_code": code,
            "stdout": out,
            "stderr": err,
            "status": "success" if code == 0 else "error",
        }

    def run(self) -> None:
        """Boucle REPL interactive (pour humain)."""
        self._running = True
        print(f"Shell interne — {self.shell.shell_id}")
        print(f"Workdir : {self.shell.workdir}")
        print("Tapez 'exit' ou Ctrl-C pour quitter.")
        print()
        while self._running:
            try:
                cmd = input(self.prompt).strip()
                if not cmd:
                    continue
                if cmd == "exit":
                    break
                result = self.execute(cmd)
                if result["stdout"]:
                    print(result["stdout"].rstrip())
                if result["stderr"]:
                    print(result["stderr"].rstrip(), file=__import__("sys").stderr)
            except (KeyboardInterrupt, EOFError):
                print()
                break
        self.close()

    def close(self) -> None:
        self._running = False
        try:
            self.shell.close()
        except Exception:
            pass