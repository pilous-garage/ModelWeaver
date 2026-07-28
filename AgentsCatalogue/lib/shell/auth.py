"""Auth — VFS bounds, command whitelist, lib_système translator, and pkill authorization."""

import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Pattern, Set, Tuple


# ── VFS bounds ──────────────────────────────────────────────

class VFSPathError(ValueError):
    """Tentative d'accès en dehors du VFS autorisé."""


class AuthorizationError(ValueError):
    """Tentative d'opération non autorisée (pskill, etc.)."""


class ShellAuth:
    """Contrôle d'accès VFS + whitelist commande + traduction système + autorisation pkill."""

    # Répertoires VFS autorisés (absolus)
    allowed_roots: List[Path]
    # Whitelist de commandes autorisées en fallback (système)
    allowed_commands: Set[str]
    # Identité de l'agent utilisant ce shell
    agent_id: Optional[str]
    # Team de l'agent
    team_id: Optional[str]
    # Rôle dans la team : member, leader, owner
    role: str

    def __init__(
        self,
        home_root: Path,
        allowed_roots: Optional[List[Path]] = None,
        allowed_commands: Optional[Set[str]] = None,
        agent_id: Optional[str] = None,
        team_id: Optional[str] = None,
        role: str = "member",
    ):
        self.home_root = home_root.resolve()
        self.allowed_roots = (
            [r.resolve() for r in allowed_roots] if allowed_roots else [self.home_root]
        )
        root_set = {r.resolve() for r in self.allowed_roots}
        root_set.add(self.home_root)
        self.allowed_roots = sorted(root_set)

        self.allowed_commands = allowed_commands or self._default_command_whitelist()
        self.agent_id = agent_id
        self.team_id = team_id
        self.role = role

    @staticmethod
    def _default_command_whitelist() -> Set[str]:
        return {
            "echo", "cat", "head", "tail", "grep", "find", "sed", "awk",
            "sort", "uniq", "wc", "diff", "patch", "mkdir", "rmdir", "rm",
            "cp", "mv", "touch", "ln", "chmod", "chown", "ps", "kill",
            "pskill", "top", "env", "which", "uname", "date", "hostname",
            "whoami", "id", "clear", "sleep", "type", "true", "false",
            "exit", "help", "alias", "source", "export", "unset",
            "cut", "tr", "paste", "join", "split", "xargs",
        }

    # ── autorisation pkill ──────────────────────

    def can_kill_process(
        self,
        target_pid: int,
        target_agent_id: Optional[str],
        target_team_id: Optional[str],
    ) -> bool:
        """Détermine si l'agent courant peut tuer un processus cible.

        Règles :
        1. Un agent peut tuer tout processus qu'il a lancé lui-même.
        2. Un team_leader peut tuer les processus des agents de sa team.
        3. Un agent ne peut PAS tuer les processus d'une autre team."""
        if target_agent_id is None:
            return False
        # Règle 1 : même agent
        if target_agent_id == self.agent_id:
            return True
        # Règle 2 : même team + rôle leader
        if (
            self.role == "leader"
            and target_team_id is not None
            and target_team_id == self.team_id
        ):
            return True
        return False

    def assert_can_kill(
        self,
        target_pid: int,
        target_agent_id: Optional[str],
        target_team_id: Optional[str],
    ) -> None:
        """Lève AuthorizationError si l'agent ne peut pas tuer le processus."""
        if not self.can_kill_process(target_pid, target_agent_id, target_team_id):
            raise AuthorizationError(
                f"agent '{self.agent_id}' (team={self.team_id}, "
                f"role={self.role}) ne peut pas tuer le pid {target_pid} "
                f"(agent={target_agent_id}, team={target_team_id})"
            )

    # ── demande d'autorisation ─────────────────────

    def submit_authorization_request(
        self,
        action: str,
        target: dict,
        reason: str = "",
        request_type: str = "live",
    ) -> Optional['AuthorizationRequest']:
        """Crée et soumet une demande d'autorisation asynchrone.

        Args:
            action: nom de l'action (ex: "pskill", "install_tool")
            target: dict avec les détails de la cible
            reason: justification
            request_type: "live" (direct) ou "pending_user" (file d'attente)

        Retourne l'AuthorizationRequest créé, ou None si l'action
        n'est pas éligible à une demande."""
        from .auth_request import AuthorizationRequest, RequestType, request_handler
        rt = RequestType.LIVE if request_type == "live" else RequestType.PENDING_USER
        req = AuthorizationRequest(
            agent_id=self.agent_id or "unknown",
            team_id=self.team_id,
            action=action,
            target=target,
            reason=reason,
            request_type=rt,
        )
        return request_handler.submit(req)

    @staticmethod
    def _default_command_whitelist() -> Set[str]:
        return {
            "echo", "cat", "head", "tail", "grep", "find", "sed", "awk",
            "sort", "uniq", "wc", "diff", "patch", "mkdir", "rm", "cp", "mv",
            "touch", "ln", "chmod", "chown", "ps", "kill", "top", "env",
            "which", "uname", "date", "hostname", "whoami", "id",
        }

    # ── vérification VFS ──────────────────────────────

    def check_path(self, target: Path) -> Path:
        """Valide que target est dans un répertoire VFS autorisé.

        Lève VFSPathError si la cible est hors bornes."""
        resolved = target.resolve()
        for root in self.allowed_roots:
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue
        raise VFSPathError(
            f"chemin '{resolved}' hors du VFS autorisé : {self.allowed_roots}"
        )

    def is_within_home(self, target: Path) -> bool:
        try:
            target.resolve().relative_to(self.home_root)
            return True
        except ValueError:
            return False

    # ── whitelist commande ─────────────────────────────

    def is_command_allowed(self, cmd: str) -> bool:
        base = cmd.split(" ")[0] if " " in cmd else cmd
        base = base.split("|")[0].strip()
        return base in self.allowed_commands

    # ── traduction système (lib_système) ──────────────

    def translate(self, cmd: str) -> Tuple[str, List[str]]:
        """Traduit une commande shell en (exécutable, args) multi-plateforme.

        Retourne (executable, args). Le caller fait subprocess.run().
        Gère la traduction des commandes selon OS (ls→dir, grep→findstr…)."""
        parts = self._tokenize(cmd)
        if not parts:
            return ("", [])
        executable, args = parts[0], parts[1:]
        translated = self._translate_command(executable)
        return (translated, args)

    def _translate_command(self, cmd: str) -> str:
        """Traduit le nom de commande selon le système courant."""
        mapping = self._platform_translations()
        return mapping.get(cmd, cmd)

    def _platform_translations(self) -> Dict[str, str]:
        """Mappage des commandes Unix vers l'équivalent Windows."""
        if sys.platform == "win32":
            return {
                "ls": "dir",
                "cat": "type",
                "cp": "copy",
                "mv": "move",
                "rm": "del",
                "mkdir": "mkdir",
                "chmod": "attrib",
                "grep": "findstr",
                "head": "more",
                "tail": "more",
                "wc": "find /c /v \"\"",
                "diff": "fc",
                "uname": "ver",
                "pwd": "cd",
                "ln": "mklink",
                "xargs": "",
                "ps": "tasklist",
                "kill": "taskkill",
                "top": "tasklist",
                "env": "set",
                "which": "where",
                "touch": "type nul >",
                "echo": "echo",
                "true": "ver >nul",
                "false": "exit 1",
            }
        return {}

    # ── lib_système : chargement dynamique ──────────────

    def load_system_lib(self, cmd: str) -> Optional[str]:
        """Tente de charger la lib_système pour la commande.

        Retourne le module path si trouvé, None sinon.
        Les lib_système sont des modules Python dans shell/lib_système/
        qui implémentent la commande pour la plateforme courante."""
        lib_dir = Path(__file__).parent / "lib_système"
        if not lib_dir.exists():
            return None
        base = os.path.splitext(os.path.basename(cmd))[0]
        candidate = lib_dir / f"{base}.py"
        if candidate.exists():
            return str(candidate)
        return None

    # ── utilitaires internes ──────────────────────────

    def _tokenize(self, cmd: str) -> List[str]:
        """Tokenisation basique (gère guillemets)."""
        tokens: List[str] = []
        current = ""
        in_quote = None
        i = 0
        while i < len(cmd):
            ch = cmd[i]
            if in_quote:
                if ch == in_quote:
                    in_quote = None
                else:
                    current += ch
            elif ch in ("'", '"'):
                in_quote = ch
            elif ch == " ":
                if current:
                    tokens.append(current)
                    current = ""
            else:
                current += ch
            i += 1
        if current:
            tokens.append(current)
        return tokens