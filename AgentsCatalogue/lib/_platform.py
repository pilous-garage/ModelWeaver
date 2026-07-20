"""Détection OS / architecture (minimale, sans système de dispatch).

Le runtime agent tourne sur Linux. Windows/Fedora = V0.10 (futur).
On n'écrit PAS de variants multi-OS à l'avance (YAGNI) : les fonctions
sont codées proprement (os.path, stdlib, services.sandbox) et restent
portables. Ce module expose juste des utilitaires de détection, utilisables
plus tard par les rares fonctions réellement OS-spécifiques (ex: run_shell).
"""

import platform


def current_os() -> str:
    """'linux' | 'windows' | 'darwin' | 'freebsd' | ..."""
    return platform.system().lower()


def current_arch() -> str:
    """'x86_64' | 'aarch64' | 'armv7l' | ..."""
    a = platform.machine().lower()
    return {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64"}.get(a, a)


def is_unix_like() -> bool:
    return current_os() in ("linux", "darwin", "freebsd")


def shell_binary() -> str:
    """Binaire du shell par défaut pour le spawn."""
    return "powershell.exe" if current_os() == "windows" else "/bin/sh"
