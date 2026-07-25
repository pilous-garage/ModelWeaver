"""Routeur central du daemon ModelWeaver.

Usage :
    from services.api.router import register, dispatch, ROUTES

    register("system/info", op_system_info)
    result = dispatch("system/info", {})  # intra-processus, pas HTTP

dispatch() ne fait PAS de rate limiting, auth ou audit (c'est le travail
du HTTP handler MWAPIHandler dans daemon.py). dispatch() est réservé aux
appels internes entre services.
"""
from typing import Any, Callable, Optional


ROUTES: dict[str, Callable] = {}
STREAMING_ROUTES: dict[str, Callable] = {}


def register(route: str, handler: Callable):
    ROUTES[route] = handler


def register_streaming(route: str, handler: Callable):
    STREAMING_ROUTES[route] = handler


def register_dynamic(route: str, handler: Callable):
    """Enregistre une route à chaud (agent-as-service, etc.)."""
    ROUTES[route] = handler


def unregister(route: str):
    """Retire une route dynamique."""
    ROUTES.pop(route, None)


def unregister_streaming(route: str):
    STREAMING_ROUTES.pop(route, None)


def dispatch(route: str, params: dict = {}) -> Any:
    """Appel intra-processus d'un handler (pas HTTP).

    ATTENTION : ne fait pas de rate limiting / auth.
    Réservé aux services internes.
    """
    handler = ROUTES.get(route)
    if handler:
        return handler(params)
    handler = STREAMING_ROUTES.get(route)
    if handler:
        return handler(params)
    raise KeyError(f"unknown route: {route}")


def get_streaming(route: str) -> Optional[Callable]:
    return STREAMING_ROUTES.get(route)
