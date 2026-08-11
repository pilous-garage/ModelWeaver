#!/usr/bin/env python3
"""catalogue_runtime — Résolveur runtime "catalogue.namespace....fn".

Exposé dans l'environnement d'exécution des foncteurs (skills) :
    this.sort = catalogue.utils.bubble_sort   # résolu à RUNTIME
    this.sort([3, 1, 2])                      # → appelle utils/bubble_sort

Un objet `catalogue` est injecté dans le namespace exec du code inline.
`catalogue.<ns>.<name>` retourne un callable qui route l'appel vers le skill
du catalogue local (skill_manager.call_skill). Les namespaces peuvent être
imbriqués : catalogue.a.b.fn résout a/b/fn@latest.

RÉSOLUTION À RUNTIME (pas inliné) : si la donnée est référencée via
`catalogue.*`, elle n'est PAS embarquée dans l'inline — elle est chargée au
moment de l'appel depuis le catalogue local (couche chaude), et à terme depuis
catalogue_dur/buffer/distant (carnet d'idées #3).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from services.skill_manager import call_skill, _get as _skill_mgr


class _CatalogueNS:
    """Namespace imbriqué : catalogue.a.b → .fn appelable."""

    def __init__(self, path: str, agent_id: str = ""):
        self._path = path
        self._agent_id = agent_id

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return _CatalogueEntry(f"{self._path}/{name}", self._agent_id)

    def __repr__(self) -> str:
        return f"<catalogue:{self._path}>"


class _CatalogueEntry:
    """Référence runtime à un skill : callable, entrypoints via attributs."""

    def __init__(self, ref: str, agent_id: str = ""):
        self._ref = ref
        self._agent_id = agent_id

    def __call__(self, *args, **kwargs):
        entrypoint = kwargs.pop("entrypoint", "main")
        if len(args) == 1 and isinstance(args[0], dict) and not kwargs:
            inputs = dict(args[0])
        else:
            inputs = dict(kwargs)
            if args:
                inputs["value"] = args[0] if len(args) == 1 else list(args)
        return call_skill(self._ref, inputs, "/tmp",
                          agent_id=self._agent_id, entrypoint=entrypoint)

    def __getattr__(self, entrypoint: str) -> "_CatalogueEntryPoint":
        if entrypoint.startswith("_"):
            raise AttributeError(entrypoint)
        return _CatalogueEntryPoint(self, entrypoint)

    def __repr__(self) -> str:
        return f"<catalogue:{self._ref}>"


class _CatalogueEntryPoint:
    def __init__(self, entry: "_CatalogueEntry", name: str):
        self._entry = entry
        self._name = name

    def __call__(self, *args, **kwargs):
        return self._entry(*args, entrypoint=self._name, **kwargs)


class _CatalogueRoot:
    """Racine : catalogue.<ns>[.<sub>].<fn> → résolution runtime."""

    def __init__(self, agent_id: str = ""):
        self._agent_id = agent_id

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return _CatalogueNS(name, self._agent_id)


def make_catalogue(agent_id: str = "") -> "_CatalogueRoot":
    """Retourne l'objet `catalogue` injectable dans un namespace exec."""
    return _CatalogueRoot(agent_id=agent_id)


def build_namespace(agent_id: str = "") -> Dict[str, Any]:
    """Namespace complet à injecter dans exec() des foncteurs.

    catalogue : skills du catalogue (résolus à runtime).
    team      : la team du membre courant (objet runtime navigable).
    daemon    : le daemon restreint (borné par privilèges).
    eval_path : évalue une chaîne de résolution typée (ex.
                team.chatroom.read(), team.members.reduce_pattern(...)).
    """
    ns: Dict[str, Any] = {
        "catalogue": make_catalogue(agent_id),
        "call_skill": call_skill,
    }
    # team / daemon / eval_path — le langage de résolution typée.
    try:
        from services.catalogue_objects import build_resolution_namespace
        rns = build_resolution_namespace(agent_id=int(agent_id) if agent_id else None)
        ns.update(rns)
    except Exception:
        pass   # best-effort : les objets racines sont optionnels
    return ns


def resolve(ref: str) -> Optional[Dict[str, Any]]:
    """Résout une ref runtime catalogue → déf skill (catalogue local)."""
    try:
        return _skill_mgr().get(ref)
    except Exception:
        return None
