#!/usr/bin/env python3
"""
ModelWeaver — CLI (interface utilisateur = client de l'API locale).

Le CLI ne contient AUCUNE logique métier : il ne fait que traduire des commandes
en appels à l'API via MWClient, au même titre que la GUI.

Exemples:
    mw_cli.py health
    mw_cli.py catalogue tools list
    mw_cli.py tools installed list
    mw_cli.py jobs add opencode install
    mw_cli.py jobs list
    mw_cli.py system info
"""
import sys
import os
import re
import json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Ancrage du dépôt sur sys.path (modules/, services/ à la racine) AVANT tout import.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from client import MWClient, MWError

# Mappe une commande CLI -> (route API, [noms des args positionnels])
COMMANDS = {
    ("health",):                    (None, []),
    ("system", "info"):             ("system/info", []),
    ("system", "deps", "check"):    ("system/deps/check", []),
    ("system", "state", "get"):     ("system/state/get", []),
    ("system", "state", "save"):    ("system/state/save", []),
    ("db", "init"):                 ("db/init", []),
    ("db", "check"):                ("db/check", []),
    ("catalogue", "tools", "list"): ("catalogue/tools/list", []),
    ("catalogue", "seed"):          ("catalogue/seed", []),
    ("catalogue", "sync"):          ("catalogue/sync", ["url"]),
    ("tools", "installed", "list"): ("tools/installed/list", []),
    ("tools", "install"):           ("tools/install", ["ref"]),
    ("tools", "install", "all"):    ("tools/install/all", []),
    ("tools", "uninstall"):         ("tools/uninstall", ["ref"]),
    ("jobs", "add"):                ("jobs/add", ["ref", "job_type"]),
    ("jobs", "list"):               ("jobs/list", []),
    ("jobs", "status"):             ("jobs/status", ["id"]),
    ("jobs", "cancel"):             ("jobs/cancel", ["id"]),
    ("jobs", "clear"):              ("jobs/clear", []),
    ("logs", "read"):               ("logs/read", []),
    # N. Chat Service (V0.6.6)
    ("chat", "session", "create"):  ("chat/session/create", ["name", "provider_ref", "model_ref", "system_prompt", "allow_read_others"]),
    ("chat", "session", "list"):    ("chat/session/list", []),
    ("chat", "session", "get"):     ("chat/session/get", ["name"]),
    ("chat", "session", "delete"):  ("chat/session/delete", ["name"]),
    ("chat", "session", "update"):  ("chat/session/update", ["name", "system_prompt", "provider_ref", "model_ref", "allow_read_others"]),
    ("chat", "session", "send"):    ("chat/session/send", ["name", "message", "provider_ref", "model_ref", "stream", "temperature", "max_tokens"]),
    ("chat", "session", "history"): ("chat/session/history", ["name"]),
    ("chat", "session", "read"):    ("chat/session/read", ["name", "other"]),
    ("chat", "session", "stream"):    ("chat/session/stream", ["name", "seq"]),
    # O. FsAuth (V0.6.20) — allowlist d'accès hôte par agent
    ("agent", "fs_auth", "list"):    ("agents/{id}/fs_auth", ["id"]),
    ("agent", "fs_auth", "grant"):   ("agents/{id}/fs_auth", ["id", "root_path", "mode"]),
    ("agent", "fs_auth", "revoke"):  ("agents/{id}/fs_auth", ["id", "root_path"]),
}


def usage():
    print("Commandes disponibles :", file=sys.stderr)
    for parts, (route, args) in COMMANDS.items():
        argstr = " ".join(f"<{a}>" for a in args)
        print(f"  {' '.join(parts)} {argstr}".rstrip(), file=sys.stderr)


def main():
    argv = sys.argv[1:]
    if not argv:
        usage()
        sys.exit(1)

    # Trouve la plus longue commande qui matche le début de argv
    match = None
    for parts in sorted(COMMANDS, key=len, reverse=True):
        if tuple(argv[:len(parts)]) == parts:
            match = parts
            break
    if not match:
        print(f"commande inconnue : {' '.join(argv)}\n", file=sys.stderr)
        usage()
        sys.exit(1)

    route, argnames = COMMANDS[match]
    positional = argv[len(match):]
    params = {}
    for i, name in enumerate(argnames):
        if i < len(positional):
            params[name] = positional[i]

    # Méthode HTTP : GET pour list, DELETE pour revoke, sinon POST.
    if match == ("agent", "fs_auth", "list"):
        method = "GET"
    elif match == ("agent", "fs_auth", "revoke"):
        method = "DELETE"
    else:
        method = "POST"

    # Substitution des placeholders {nom} dans la route depuis les params.
    def _sub(r):
        def repl(m):
            key = m.group(1)
            if key in params:
                return str(params.pop(key))
            return m.group(0)
        return re.sub(r"\{(\w+)\}", repl, r)
    route = _sub(route)

    try:
        client = MWClient()
        if match == ("health",):
            result = client.health()
        elif method == "POST":
            result = client.call(route, **params)
        else:
            _, body = client.request_raw(method, route, **params)
            result = body.get("result") if isinstance(body, dict) else body
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except MWError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
