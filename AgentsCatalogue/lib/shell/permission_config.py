"""Permission configuration loader — defines allowed commands per role.

Supports JSON and YAML config files. The config defines:
- ``roles``: hierarchical role definitions (admin > dev > user)
- ``permissions``: mapping of role -> list of allowed command base-names
- ``defaults``: fallback commands applied to all roles

The hierarchy means a higher role inherits *all* commands of lower roles
plus its own extras.  The default hierarchy is::

    admin  (most powerful)
      |
    dev
      |
    user  (least powerful)

Example YAML config::

    roles:
      hierarchy: [admin, dev, user]
      default: user
    permissions:
      user:
        - echo
        - cat
        - pwd
      dev:
        - git
        - python3
        - pytest
      admin:
        - rm
        - chmod
        - chown
        - kill
        - pskill
    defaults:
      - echo
      - true
      - false
      - exit
      - help
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    import yaml  # type: ignore
    _HAS_YAML = True
except ImportError:  # pragma: no cover - yaml is optional but available
    _HAS_YAML = False


# ── Default hierarchical role definition ──────────────────────────────

DEFAULT_ROLE_HIERARCHY: List[str] = ["admin", "dev", "user"]
DEFAULT_ROLE: str = "user"

#: Commands allowed for every role regardless of config (safety net).
DEFAULT_COMMANDS: Set[str] = {
    "echo", "cat", "head", "tail", "grep", "find", "sed", "awk",
    "sort", "uniq", "wc", "diff", "patch", "mkdir", "rmdir", "rm",
    "cp", "mv", "touch", "ln", "chmod", "chown", "ps", "kill",
    "pskill", "top", "env", "which", "uname", "date", "hostname",
    "whoami", "id", "clear", "sleep", "type", "true", "false",
    "exit", "help", "alias", "source", "export", "unset",
    "cut", "tr", "paste", "join", "split", "xargs",
    "git", "python3", "python", "pytest",
}


class PermissionConfig:
    """In-memory representation of a permission configuration.

    Attributes:
        roles: ordered list from most-privileged to least-privileged.
        default_role: role assigned when none is specified.
        permissions: mapping ``role -> set(command base-names)``.
        defaults: commands allowed for *every* role (union with role perms).
    """

    def __init__(
        self,
        roles: Optional[List[str]] = None,
        default_role: Optional[str] = None,
        permissions: Optional[Dict[str, List[str]]] = None,
        defaults: Optional[Set[str]] = None,
    ):
        self.roles: List[str] = roles or list(DEFAULT_ROLE_HIERARCHY)
        self.default_role: str = default_role or DEFAULT_ROLE
        self.permissions: Dict[str, Set[str]] = {}
        for role, cmds in (permissions or {}).items():
            self.permissions[role] = set(cmds)
        self.defaults: Set[str] = set(defaults) if defaults else set()

    # ── queries ──────────────────────────────────────────────────────

    @property
    def hierarchy(self) -> List[str]:
        """Return roles ordered most-privileged → least-privileged."""
        return list(self.roles)

    def role_level(self, role: str) -> int:
        """Return the numeric level of *role* (0 = most privileged).

        Unknown roles are treated as the least-privileged (bottom).
        """
        try:
            return self.roles.index(role)
        except ValueError:
            return len(self.roles)

    def inherits(self, child: str, parent: str) -> bool:
        """True if *child* role inherits permissions from *parent* role.

        Inheritance follows the hierarchy: a role inherits from all
        roles that are *less* privileged than itself.
        """
        cl = self.role_level(child)
        pl = self.role_level(parent)
        # A role inherits from roles below it in the hierarchy list.
        return cl < pl

    def effective_commands(self, role: str) -> Set[str]:
        """Return the full set of commands allowed for *role*.

        This includes the role's own commands, all commands from
        less-privileged roles (inheritance), and the global defaults.
        """
        result: Set[str] = set(self.defaults)
        if role not in self.roles:
            # Unknown role — treat as least-privileged (bottom of hierarchy)
            role = self.roles[-1] if self.roles else self.default_role
        role_level = self.role_level(role)
        for i, r in enumerate(self.roles):
            # Include roles at or below current role's level (less privileged)
            if i >= role_level:
                result |= self.permissions.get(r, set())
        # Also include the role's own commands explicitly
        result |= self.permissions.get(role, set())
        return result

    # ── serialisation ────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return {
            "roles": list(self.roles),
            "default_role": self.default_role,
            "permissions": {r: sorted(c) for r, c in self.permissions.items()},
            "defaults": sorted(self.defaults),
        }


# ── File loader ────────────────────────────────────────────────────────

class PermissionConfigLoader:
    """Loads a :class:`PermissionConfig` from a JSON or YAML file.

    The loader also supports a *fallback* mode: if the file does not
    exist, it returns a default config built from :data:`DEFAULT_COMMANDS`.
    """

    def __init__(self, config_path: Optional[str] = None):
        self.config_path: Optional[str] = config_path
        self._cache: Optional[PermissionConfig] = None
        self._cache_mtime: float = 0.0
        self._lock = threading.RLock()

    # ── public API ───────────────────────────────────────────────────

    def load(self, force_reload: bool = False) -> PermissionConfig:
        """Load (or reload) the permission configuration.

        Uses a file-mtime cache when a path is set.  If the file is
        missing or unreadable, a default config is returned.
        """
        with self._lock:
            if (
                not force_reload
                and self._cache is not None
                and self.config_path
                and self._config_unchanged()
            ):
                return self._cache

            if self.config_path and Path(self.config_path).exists():
                cfg = self._load_from_file(self.config_path)
            else:
                cfg = self._default_config()

            self._cache = cfg
            if self.config_path:
                try:
                    self._cache_mtime = os.path.getmtime(self.config_path)
                except OSError:
                    self._cache_mtime = 0.0
            return cfg

    def reload(self) -> PermissionConfig:
        """Force a reload from disk."""
        return self.load(force_reload=True)

    # ── internals ────────────────────────────────────────────────────

    def _config_unchanged(self) -> bool:
        """Check if the config file mtime has changed since last load."""
        try:
            mtime = os.path.getmtime(self.config_path)  # type: ignore[arg-type]
            return mtime == self._cache_mtime
        except (OSError, TypeError):
            return False

    def _load_from_file(self, path: str) -> PermissionConfig:
        """Parse a JSON or YAML file into a :class:`PermissionConfig`."""
        raw: Dict[str, Any]
        ext = os.path.splitext(path)[1].lower()
        with open(path, "r", encoding="utf-8") as fh:
            if ext in (".yaml", ".yml"):
                if not _HAS_YAML:
                    raise RuntimeError(
                        "PyYAML is required to load YAML permission configs"
                    )
                raw = yaml.safe_load(fh) or {}
            else:
                # JSON (or any other extension)
                raw = json.load(fh)

        if not isinstance(raw, dict):
            raise ValueError(
                f"Permission config must be a mapping, got {type(raw).__name__}"
            )

        # Normalise: permissions may use lists or dicts with metadata
        permissions_raw = raw.get("permissions", {})
        permissions: Dict[str, List[str]] = {}
        for role, spec in permissions_raw.items():
            if isinstance(spec, list):
                permissions[role] = list(spec)
            elif isinstance(spec, dict):
                # Allow {"commands": [...], "inherit": true/false}
                permissions[role] = list(spec.get("commands", []))
            else:
                permissions[role] = [str(spec)]

        defaults_raw = raw.get("defaults", [])
        defaults: Set[str] = set(defaults_raw) if defaults_raw else set()

        roles_cfg = raw.get("roles", {})
        if isinstance(roles_cfg, dict):
            hierarchy = roles_cfg.get("hierarchy") or list(DEFAULT_ROLE_HIERARCHY)
            default_role = roles_cfg.get("default") or DEFAULT_ROLE
        else:
            hierarchy = list(roles_cfg) if roles_cfg else list(DEFAULT_ROLE_HIERARCHY)
            default_role = DEFAULT_ROLE

        return PermissionConfig(
            roles=hierarchy,
            default_role=default_role,
            permissions=permissions,
            defaults=defaults,
        )

    def _default_config(self) -> PermissionConfig:
        """Build a config where every role gets DEFAULT_COMMANDS."""
        return PermissionConfig(
            roles=list(DEFAULT_ROLE_HIERARCHY),
            default_role=DEFAULT_ROLE,
            permissions={
                "admin": set(DEFAULT_COMMANDS),
                "dev": set(DEFAULT_COMMANDS),
                "user": set(DEFAULT_COMMANDS),
            },
            defaults=set(),
        )


# ── Module-level convenience ───────────────────────────────────────────

_default_loader = PermissionConfigLoader()


def load_permission_config(path: Optional[str] = None) -> PermissionConfig:
    """Load a permission config, optionally from *path*.

    Without a path, returns a default config (all default commands
    for every role).
    """
    if path:
        return PermissionConfigLoader(path).load(force_reload=True)
    return _default_loader.load(force_reload=True)


__all__ = [
    "PermissionConfig",
    "PermissionConfigLoader",
    "load_permission_config",
    "DEFAULT_ROLE_HIERARCHY",
    "DEFAULT_ROLE",
    "DEFAULT_COMMANDS",
]
