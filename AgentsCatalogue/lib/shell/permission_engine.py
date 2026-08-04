"""Permission engine — dynamic, role-based command authorization with caching.

The :class:`PermissionEngine` uses a :class:`~permission_config.PermissionConfig`
to decide whether a given *role* is allowed to run a *command*.

Key features
------------
- **Hierarchical roles** — a higher role (e.g. ``admin``) inherits all
  commands of lower roles (``dev``, ``user``) plus its own extras.
- **Caching** — resolved permission sets are cached per role with a
  time-to-live, so repeated ``is_allowed`` calls are O(1).
- **Hot-reload** — if the underlying config file changes, the cache is
  invalidated automatically on the next access.
- **Backward-compatible** — the legacy ``allowed_commands`` set API is
  still supported: if a caller passes a plain set, the engine wraps it
  into a single-role config.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional, Set, Union

from .permission_config import (
    DEFAULT_COMMANDS,
    DEFAULT_ROLE,
    DEFAULT_ROLE_HIERARCHY,
    PermissionConfig,
    PermissionConfigLoader,
)


# ── Cache entry ────────────────────────────────────────────────────────

class _CacheEntry:
    """A cached permission set for a single role."""

    __slots__ = ("commands", "expires_at")

    def __init__(self, commands: Set[str], ttl: float):
        self.commands: Set[str] = commands
        self.expires_at: float = time.monotonic() + ttl


# ── Permission Engine ──────────────────────────────────────────────────

class PermissionEngine:
    """Evaluate command permissions for roles using a config + cache.

    Parameters
    ----------
    config :
        A :class:`PermissionConfig` instance, or a file path (str/Path)
        from which a config will be loaded.  If *None*, a default config
        (all default commands for every role) is used.
    cache_ttl :
        Time-to-live (seconds) for cached permission lookups.  Set to
        ``0`` to disable caching (always re-resolve).
    allowed_commands :
        *Legacy* parameter — a plain set of command names.  When
        provided, it overrides the config: every role gets exactly
        these commands (backward-compatible with ``ShellAuth``'s old
        ``allowed_commands`` parameter).
    """

    def __init__(
        self,
        config: Optional[Union[PermissionConfig, str]] = None,
        cache_ttl: float = 60.0,
        allowed_commands: Optional[Set[str]] = None,
    ):
        self._cache_ttl: float = cache_ttl
        self._allowed_commands_legacy: Optional[Set[str]] = allowed_commands
        self._cache: Dict[str, _CacheEntry] = {}
        self._cache_lock = threading.RLock()

        # Resolve config
        if allowed_commands is not None:
            # Legacy mode: wrap the set into a PermissionConfig
            self._config: PermissionConfig = PermissionConfig(
                roles=list(DEFAULT_ROLE_HIERARCHY),
                default_role=DEFAULT_ROLE,
                permissions={
                    "admin": set(allowed_commands),
                    "dev": set(allowed_commands),
                    "user": set(allowed_commands),
                },
                defaults=set(),
            )
            self._loader: Optional[PermissionConfigLoader] = None
        elif config is not None:
            if isinstance(config, PermissionConfig):
                self._config = config
                self._loader = None
            else:
                # Treat as a file path
                self._loader = PermissionConfigLoader(str(config))
                self._config = self._loader.load()
        else:
            self._config = PermissionConfig(
                roles=list(DEFAULT_ROLE_HIERARCHY),
                default_role=DEFAULT_ROLE,
                permissions={
                    "admin": set(DEFAULT_COMMANDS),
                    "dev": set(DEFAULT_COMMANDS),
                    "user": set(DEFAULT_COMMANDS),
                },
                defaults=set(),
            )
            self._loader = None

    # ── properties ─────────────────────────────────────────────────────

    @property
    def config(self) -> PermissionConfig:
        """Current config (reloaded from file if a loader is set)."""
        if self._loader is not None:
            return self._loader.load()
        return self._config

    @property
    def cache_ttl(self) -> float:
        return self._cache_ttl

    @cache_ttl.setter
    def cache_ttl(self, value: float) -> None:
        self._cache_ttl = value
        self.invalidate_cache()

    @property
    def role_hierarchy(self) -> list:
        """List of roles from most to least privileged."""
        return self.config.hierarchy

    # ── cache management ───────────────────────────────────────────────

    def invalidate_cache(self) -> None:
        """Clear all cached permission lookups."""
        with self._cache_lock:
            self._cache.clear()

    def _get_cached(self, role: str) -> Optional[Set[str]]:
        """Return cached commands for *role* if still valid."""
        with self._cache_lock:
            entry = self._cache.get(role)
            if entry is None:
                return None
            if time.monotonic() > entry.expires_at:
                del self._cache[role]
                return None
            return entry.commands

    def _set_cached(self, role: str, commands: Set[str]) -> None:
        with self._cache_lock:
            self._cache[role] = _CacheEntry(commands, self._cache_ttl)

    # ── core API ───────────────────────────────────────────────────────

    def is_allowed(self, role: str, command: str) -> bool:
        """Check whether *role* is permitted to execute *command*.

        Parameters
        ----------
        role :
            The role name (e.g. ``"admin"``, ``"dev"``, ``"user"``).
            Unknown roles fall back to the least-privileged level.
        command :
            The command string — may include arguments; only the
            base command name (first token) is checked.

        Returns
        -------
        bool
            ``True`` if the command is allowed for the role.
        """
        # Extract base command (strip args, pipes, etc.)
        base = command
        if " " in base:
            base = base.split(" ")[0]
        if "|" in base:
            base = base.split("|")[0]
        base = base.strip()
        if not base:
            return False

        # Check legacy override
        if self._allowed_commands_legacy is not None:
            return base in self._allowed_commands_legacy

        # Check cache
        cached = self._get_cached(role)
        if cached is not None:
            return base in cached

        # Resolve from config (with inheritance)
        commands = self.config.effective_commands(role)
        self._set_cached(role, commands)
        return base in commands

    def get_allowed_commands(self, role: str) -> Set[str]:
        """Return the full set of command names allowed for *role*.

        Results are cached.
        """
        if self._allowed_commands_legacy is not None:
            return set(self._allowed_commands_legacy)

        cached = self._get_cached(role)
        if cached is not None:
            return set(cached)

        commands = self.config.effective_commands(role)
        self._set_cached(role, commands)
        return set(commands)

    def reload(self) -> None:
        """Force a config reload and clear the cache."""
        if self._loader is not None:
            self._config = self._loader.load(force_reload=True)
        self.invalidate_cache()

    def get_role_level(self, role: str) -> int:
        """Return the numeric level of *role* (0 = most privileged)."""
        return self.config.role_level(role)

    def inherits(self, child: str, parent: str) -> bool:
        """True if *child* role inherits from *parent* role."""
        return self.config.inherits(child, parent)


# ── Module-level singleton (optional convenience) ──────────────────────

_default_engine: Optional[PermissionEngine] = None
_default_engine_lock = threading.Lock()


def get_permission_engine(
    config: Optional[Union[PermissionConfig, str]] = None,
    cache_ttl: float = 60.0,
    allowed_commands: Optional[Set[str]] = None,
) -> PermissionEngine:
    """Return a module-level :class:`PermissionEngine` singleton.

    On first call the engine is created from *config* / *allowed_commands*.
    Subsequent calls without arguments return the same instance.  If
    different arguments are passed, a new engine is created.
    """
    global _default_engine
    with _default_engine_lock:
        if _default_engine is None or config is not None or allowed_commands is not None:
            _default_engine = PermissionEngine(
                config=config,
                cache_ttl=cache_ttl,
                allowed_commands=allowed_commands,
            )
        return _default_engine


__all__ = [
    "PermissionEngine",
    "get_permission_engine",
]
