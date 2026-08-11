"""Auth — VFS bounds, command whitelist, lib_système translator, and pkill authorization.

This module has been refactored to support **dynamic, role-based permissions**
via :class:`~permission_engine.PermissionEngine`.  The legacy fixed whitelist
API (``allowed_commands`` set) is still fully supported for backward
compatibility — when a caller passes ``allowed_commands`` directly, it is
wrapped into a single-role permission config.
"""

import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Pattern, Set, Tuple, Union

from .permission_config import (
    DEFAULT_COMMANDS,
    DEFAULT_ROLE,
    DEFAULT_ROLE_HIERARCHY,
    PermissionConfig,
    PermissionConfigLoader,
)
from .permission_engine import PermissionEngine


# ── VFS bounds ──────────────────────────────────────────────────────────

class VFSPathError(ValueError):
    """Tentative d'accès en dehors du VFS autorisé."""


class AuthorizationError(ValueError):
    """Tentative d'opération non autorisée (pskill, etc.)."""


# ── ShellAuth ───────────────────────────────────────────────────────────

class ShellAuth:
    """Contrôle d'accès VFS + whitelist commande + traduction système + autorisation pkill.

    Parameters
    ----------
    home_root :
        Racine absolue du VFS de l'agent.
    allowed_roots :
        Racines VFS supplémentaires autorisées (au-delà de home_root).
    allowed_commands :
        *Legacy* — whitelist fixe de commandes.  Quand fourni, crée un
        :class:`PermissionEngine` en mode legacy (tous les rôles partagent
        ce même ensemble).  Ignoré si *permission_engine* ou *permission_config*
        est fourni.
    permission_engine :
        Un :class:`PermissionEngine` pré-configuré.  Prise en priorité sur
        *permission_config* et *allowed_commands*.
    permission_config :
        Chemin vers un fichier de config (JSON/YAML) ou une instance de
        :class:`PermissionConfig`.  Utilisé pour créer un
        :class:`PermissionEngine` si *permission_engine* n'est pas fourni.
    cache_ttl :
        TTL (secondes) du cache du moteur de permissions.
    agent_id :
        Identifiant de l'agent utilisant ce shell.
    team_id :
        Identifiant de la team de l'agent.
    role :
        Rôle de l'agent dans la team : ``member``, ``leader``, ``owner``
        (pour pkill) **ou** ``admin``, ``dev``, ``user`` (pour les
        permissions de commandes).  Par défaut ``"member"``.
    """

    # Répertoires VFS autorisés (absolus)
    allowed_roots: List[Path]
    # Whitelist de commandes autorisées en fallback (système) — legacy
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
        permission_engine: Optional[PermissionEngine] = None,
        permission_config: Optional[Union[str, PermissionConfig]] = None,
        cache_ttl: float = 60.0,
    ):
        self.home_root = home_root.resolve()
        self.allowed_roots = (
            [r.resolve() for r in allowed_roots] if allowed_roots else [self.home_root]
        )
        root_set = {r.resolve() for r in self.allowed_roots}
        root_set.add(self.home_root)
        self.allowed_roots = sorted(root_set)

        self.agent_id = agent_id
        self.team_id = team_id
        self.role = role

        # ── Permission engine setup ──────────────────────────────
        if permission_engine is not None:
            self._permission_engine: PermissionEngine = permission_engine
        elif permission_config is not None:
            self._permission_engine = PermissionEngine(
                config=permission_config, cache_ttl=cache_ttl
            )
        elif allowed_commands is not None:
            # Legacy mode: wrap the set into a PermissionEngine
            self._permission_engine = PermissionEngine(
                allowed_commands=allowed_commands, cache_ttl=cache_ttl
            )
        else:
            # Default: load from the shipped permissions.yaml, fallback to
            # DEFAULT_COMMANDS if the file is missing.
            default_config_path = Path(__file__).parent / "permissions.yaml"
            if default_config_path.exists():
                self._permission_engine = PermissionEngine(
                    config=str(default_config_path), cache_ttl=cache_ttl
                )
            else:
                self._permission_engine = PermissionEngine(
                    allowed_commands=set(DEFAULT_COMMANDS), cache_ttl=cache_ttl
                )

        # ── Backward-compatible allowed_commands property ──────────
        # Expose the effective command set as a set for legacy callers
        # that read ``auth.allowed_commands`` directly.
        self.allowed_commands = self._permission_engine.get_allowed_commands(
            self._resolve_permission_role()
        )

    # ── Role resolution ────────────────────────────────────────────────

    def _resolve_permission_role(self) -> str:
        """Map the team role to a permission role.

        Team roles (``member``, ``leader``, ``owner``) are mapped to
        permission roles (``user``, ``dev``, ``admin``) based on
        privilege level.  If the role already matches a permission role,
        it is used directly.
        """
        # Direct permission-role match
        if self.role in DEFAULT_ROLE_HIERARCHY:
            return self.role

        # Map team roles to permission roles
        role_map = {
            "member": "user",
            "leader": "dev",
            "owner": "admin",
        }
        return role_map.get(self.role, DEFAULT_ROLE)

    # ── Permission engine access ───────────────────────────────────────

    @property
    def permission_engine(self) -> PermissionEngine:
        """The :class:`PermissionEngine` used by this auth instance."""
        return self._permission_engine

    def get_permission_role(self) -> str:
        """Return the permission role derived from the team role."""
        return self._resolve_permission_role()

    def is_allowed(self, command: str) -> bool:
        """Check whether the current agent's role can execute *command*.

        This is the primary permission-checking method.  It delegates
        to :meth:`PermissionEngine.is_allowed` with the resolved
        permission role.

        Les GRANTS mémorisés (authorisations accordées/refusées avec portée)
        passent en priorité : un grant accordé autorise même si la commande
        n'est pas dans la whitelist ; un refus mémorisé bloque sans re-demander.
        """
        # Refus mémorisé ? (ne pas re-demander sans fin)
        try:
            from .auth_request import request_handler
            cmd_key = command.strip()
            if request_handler.is_denied(self.agent_id, "command", {"command": cmd_key}):
                return False
            # Grant accordé ?
            if request_handler.is_granted(self.agent_id, "command", {"command": cmd_key}):
                return True
        except Exception:
            pass
        return self._permission_engine.is_allowed(
            self._resolve_permission_role(), command
        )

    # ── Legacy whitelist (backward-compatible) ───────────────────────

    @staticmethod
    def _default_command_whitelist() -> Set[str]:
        return set(DEFAULT_COMMANDS)

    def reload_permissions(self) -> None:
        """Force a reload of the permission configuration and clear cache."""
        self._permission_engine.reload()
        # Refresh the backward-compatible view
        self.allowed_commands = self._permission_engine.get_allowed_commands(
            self._resolve_permission_role()
        )

    # ── pkill authorization ─────────────────────────────────────────────

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

    # ── demande d'autorisation ─────────────────────────────────────────

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

    # ── VFS verification ────────────────────────────────────────────────

    def check_path(self, target: Path) -> Path:
        """Valide que target est dans un répertoire VFS autorisé.

        Lève VFSPathError si la cible est hors bornes. Un grant d'autorisation
        path accordé à l'agent (is_granted) lève cette restriction pour le
        chemin concerné ; un refus mémorisé la bloque."""
        resolved = target.resolve()
        # Refus mémorisé pour ce chemin ? (bloque)
        try:
            from .auth_request import request_handler
            mode = "read"  # par défaut lecture
            if request_handler.is_denied(self.agent_id, "path_read", {"path": str(resolved)}):
                raise VFSPathError(f"chemin '{resolved}' refusé (mémorisé)")
        except VFSPathError:
            raise
        except Exception:
            pass
        for root in self.allowed_roots:
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue
        # Hors VFS : un privilège du catalogue (chemin autorisé) peut lever
        # la restriction — résolution par (agent_id|-1) × (team|-1), level max.
        try:
            if self.check_privilege_path(resolved, op="read",
                                         agent_id=self.agent_id,
                                         team_id=self._team_int()):
                return resolved
        except Exception:
            pass
        # Hors VFS : un grant accordé peut lever la restriction.
        try:
            from .auth_request import request_handler
            if request_handler.is_granted(self.agent_id, "path_read", {"path": str(resolved)}):
                return resolved
        except Exception:
            pass
        raise VFSPathError(
            f"chemin '{resolved}' hors du VFS autorisé : {self.allowed_roots}"
        )

    def _team_int(self) -> Optional[int]:
        try:
            return int(self.team_id) if self.team_id is not None else None
        except (TypeError, ValueError):
            return None

    def is_within_home(self, target: Path) -> bool:
        try:
            target.resolve().relative_to(self.home_root)
            return True
        except ValueError:
            return False

    # ── résolution des privilèges via le catalogue_local ──────────────

    def _catalogue_privileges(self):
        """Accès au résolveur de privilèges du catalogue local (best-effort)."""
        try:
            from services.api.handlers.catalogue_local import _get
            return _get()
        except Exception:
            return None

    def check_privilege_path(self, target: Path, op: str = "read",
                             agent_id: Optional[str] = None,
                             team_id: Optional[int] = None) -> bool:
        """Vrai si un privilège du catalogue autorise `op` sur `target`.

        Niveau demandé = agent_with_root (les agents passent par le mini_shell
        avec éventuellement root_privilege). Résolution : lignes
        (agent_id|-1) × (team|-1) qui matchent le chemin, level max,
        intersection. MAX_UINT32 = toujours.

        Le rôle shell détermine le niveau : member → 'agent',
        leader/owner → 'agent_with_root' (privilèges team_leader)."""
        db = self._catalogue_privileges()
        if db is None:
            return False
        path_str = str(target.resolve())
        # Niveau privilège demandé selon le rôle shell.
        level = "agent_with_root" if self.role in ("leader", "owner") else "agent"
        try:
            return db.check_privilege(
                path_str, level=level, op=op, kind="path",
                agent_id=int(agent_id) if agent_id else -1,
                team=int(team_id) if team_id else -1)
        except Exception:
            return False

    # ── command whitelist (backward-compatible API) ──────────────────

    def is_command_allowed(self, cmd: str) -> bool:
        """Check whether *cmd* is allowed for the current agent's role.

        .. deprecated::
            Prefer :meth:`is_allowed` which delegates to the
            :class:`PermissionEngine`.  This method is kept for
            backward compatibility with existing callers (e.g. the
            executor's ``_run_fallback``).
        """
        base = cmd.split(" ")[0] if " " in cmd else cmd
        base = base.split("|")[0].strip()
        return self._permission_engine.is_allowed(
            self._resolve_permission_role(), base
        )

    def get_allowed_commands(self) -> Set[str]:
        """Return the full set of commands allowed for the current role."""
        return self._permission_engine.get_allowed_commands(
            self._resolve_permission_role()
        )

    # ── système translation (lib_système) ──────────────────────────────

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

    # ── lib_système : chargement dynamique ────────────────────────────

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

    # ── utilitaires internes ───────────────────────────────────────────

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


__all__ = [
    "ShellAuth",
    "VFSPathError",
    "AuthorizationError",
    "PermissionEngine",
    "PermissionConfig",
    "PermissionConfigLoader",
    "DEFAULT_ROLE_HIERARCHY",
    "DEFAULT_ROLE",
    "DEFAULT_COMMANDS",
]
