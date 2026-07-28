#!/usr/bin/env python3
"""CLI Dashboard ModelWeaver — interface terminal pour le backend.

Usage :
  python3 cli/dashboard.py agents                          # liste des agents
  python3 cli/dashboard.py agent stop X                    # arrêter un agent
  python3 cli/dashboard.py agent restart X                 # redémarrer un agent
  python3 cli/dashboard.py services                        # liste des services
  python3 cli/dashboard.py services start X                # démarrer un service
  python3 cli/dashboard.py services restart X              # redémarrer un service
  python3 cli/dashboard.py services stop X                 # arrêter un service
  python3 cli/dashboard.py system                          # infos système
  python3 cli/dashboard.py keys                            # liste des clés API
  python3 cli/dashboard.py providers                       # liste des providers LLM
  python3 cli/dashboard.py launch ROLE REQUEST             # lancer un agent one-shot
  python3 cli/dashboard.py signal AGENT_ID ACTION          # envoyer un signal

Toutes les commandes passent par l'API HTTP du daemon (port 8770).
"""

import sys
import json
import urllib.request
import urllib.error
from pathlib import Path

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box
    RICH = True
except ImportError:
    RICH = False


DAEMON_PORT = 8770
DAEMON_URL = f"http://127.0.0.1:{DAEMON_PORT}"


# ── Helpers HTTP ──

def _token() -> str:
    tok = Path.home() / ".modelweaver" / "api.token"
    return tok.read_text().strip() if tok.exists() else ""


def _post(route: str, body: dict = None) -> dict:
    url = f"{DAEMON_URL}/v1/{route}"
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    tok = _token()
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read())
            # Le daemon wrappe dans {"ok": True, "route": ..., "result": ...}
            return raw.get("result") if "result" in raw else raw
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _get(route: str) -> dict:
    return _post(route)


# ── Afficheurs ──

def _console():
    return Console() if RICH else None


def _print(msg: str = ""):
    if RICH:
        _console().print(msg)
    else:
        print(msg)


def _print_table(title: str, columns: list, rows: list):
    if RICH:
        t = Table(title=title, box=box.ROUNDED)
        for c in columns:
            t.add_column(c)
        for r in rows:
            t.add_row(*[str(v) for v in r])
        _console().print(t)
    else:
        sep = " | ".join(columns)
        print(f"\n=== {title} ===")
        print(sep)
        print("-" * len(sep))
        for r in rows:
            print(" | ".join(str(v) for v in r))
        print()


# ── Commandes ──

def cmd_agents(args):
    data = _post("agent/list-by-team")
    teams = data.get("teams", {})
    standalone = data.get("standalone", [])
    if not teams and not standalone:
        _print("[yellow]Aucun agent[/yellow]" if RICH else "Aucun agent")
        return
    for team_name, tdata in sorted(teams.items()):
        ti = tdata.get("team_info") or {}
        status_str = f" [{ti.get('status', '?')}]" if ti.get("status") else ""
        _print(f"\n[bold cyan]▸ Équipe: {team_name}{status_str}[/bold cyan]" if RICH else f"\n--- Équipe: {team_name}{status_str} ---")
        rows = []
        for a in tdata["agents"]:
            running = "●" if a.get("running") else "○"
            status = a.get("status", "?")
            hb = f"{a.get('heartbeat', '')}" if a.get("heartbeat") else "—"
            step = a.get("current_step", "—") or "—"
            rows.append((str(a["agent_id"]), a.get("name", "?").split("/", 1)[-1],
                         a.get("role_type", "?"), status, running, hb, step))
        if RICH:
            from rich.table import Table
            t = Table(box=box.ROUNDED)
            for c in ("ID", "Nom", "Rôle", "Status", "Actif", "Heartbeat", "Step"):
                t.add_column(c)
            for r in rows:
                t.add_row(*[str(v) for v in r])
            _console().print(t)
        else:
            sep = " | ".join(("ID", "Nom", "Rôle", "Status", "Actif", "Heartbeat", "Step"))
            print(sep)
            print("-" * len(sep))
            for r in rows:
                print(" | ".join(str(v) for v in r))
    if standalone:
        _print(f"\n[bold]Agents sans équipe:[/bold]" if RICH else f"\n--- Agents sans équipe ---")
        rows = []
        for a in standalone:
            running = "●" if a.get("running") else "○"
            status = a.get("status", "?")
            hb = f"{a.get('heartbeat', '')}" if a.get("heartbeat") else "—"
            step = a.get("current_step", "—") or "—"
            rows.append((str(a["agent_id"]), a.get("name", "?"), a.get("role_type", "?"),
                         status, running, hb, step))
        if RICH:
            from rich.table import Table
            t = Table(box=box.ROUNDED)
            for c in ("ID", "Nom", "Rôle", "Status", "Actif", "Heartbeat", "Step"):
                t.add_column(c)
            for r in rows:
                t.add_row(*[str(v) for v in r])
            _console().print(t)
        else:
            sep = " | ".join(("ID", "Nom", "Rôle", "Status", "Actif", "Heartbeat", "Step"))
            print(sep)
            print("-" * len(sep))
            for r in rows:
                print(" | ".join(str(v) for v in r))


def _resolve_agent_name(name: str) -> str | None:
    """Résout un nom court d'agent vers le nom complet stocké (ex: team:xxx/name)."""
    data = _post("agent/list-by-team")
    candidates = []
    for tdata in data.get("teams", {}).values():
        for a in tdata.get("agents", []):
            full = a.get("name", "")
            if full == name:
                return full
            if full.endswith("/" + name):
                candidates.append(full)
    for a in data.get("standalone", []):
        full = a.get("name", "")
        if full == name:
            return full
        tail = full.rsplit("/", 1)[-1]
        if tail == name:
            candidates.append(full)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        _print(
            f"[yellow]Ambigüité: '{name}' correspond à {candidates}. Utilisez le nom complet.[/yellow]" if RICH else
            f"Ambigüité: '{name}' correspond à {candidates}. Utilisez le nom complet."
        )
        return None
    _print(
        f"[red]Agent '{name}' introuvable[/red]" if RICH else
        f"Agent '{name}' introuvable"
    )
    return None


def cmd_agent_action(args):
    name = args.agent_name
    action = args.agent_action
    full = _resolve_agent_name(name)
    if full is None:
        return
    if action == "stop":
        r = _post("agent/stop", {"name": full})
    elif action == "restart":
        r = _post("agent/restart", {"name": full, "request": args.request or ""})
    else:
        _print(f"[red]Action inconnue: {action}[/red]" if RICH else f"Action inconnue: {action}")
        return
    status = r.get("status", "error")
    if status == "ok":
        _print(f"[green]{action} {name} → commande envoyée[/green]" if RICH else f"{action} {name} → commande envoyée")
    else:
        _print(f"[red]Erreur: {r.get('error', '?')}[/red]" if RICH else f"Erreur: {r.get('error', '?')}")


def cmd_services(args):
    if args.service_action:
        name = args.service_name
        action = args.service_action
        if action == "start":
            r = _post("service/start", {"name": name})
        elif action == "restart":
            r = _post("service/restart", {"name": name})
        elif action == "stop":
            r = _post("service/stop", {"name": name})
        else:
            _print(f"[red]Action inconnue: {action}[/red]" if RICH else f"Action inconnue: {action}")
            return
        status = r.get("status", "error")
        if status == "ok":
            _print(f"[green]{action} {name} → commande envoyée[/green]" if RICH else f"{action} {name} → commande envoyée")
        else:
            _print(f"[red]Erreur: {r.get('error', '?')}[/red]" if RICH else f"Erreur: {r.get('error', '?')}")
        return

    data = _post("service/list")
    svcs = data.get("services") or []
    if not svcs:
        _print("[yellow]Aucun service[/yellow]" if RICH else "Aucun service")
        return
    rows = []
    for s in svcs:
        pid = s.get("pid") or "—"
        if pid != "—" and pid == -1:
            pid = "—"
        status = s.get("status", "?")
        color = ""
        if RICH:
            color = {"running": "green", "crashed": "red", "restarting": "yellow", "stopped": "dim"}.get(status, "")
            status = f"[{color}]{status}[/{color}]"
        restarts = str(s.get("restarts", 0))
        rows.append((s.get("name", "?"), s.get("mode", "?"), status,
                     str(pid), restarts))
    _print_table("Services supervisés", ("Nom", "Mode", "Status", "PID", "Redém.",), rows)


def cmd_system(args):
    data = _post("system/state/get")
    state = data.get("result") or data
    if not state:
        _print("[red]Impossible de récupérer l'état système[/red]" if RICH else "État système indisponible")
        return
    lines = []
    for k, v in sorted(state.items()):
        if isinstance(v, dict) or isinstance(v, list):
            v = json.dumps(v, indent=2)[:120]
        lines.append((k, str(v)))
    if RICH:
        t = Table(title="État système", box=box.ROUNDED)
        t.add_column("Clé")
        t.add_column("Valeur")
        for k, v in lines:
            t.add_row(k, v)
        _console().print(t)
    else:
        _print("\n=== État système ===")
        for k, v in lines:
            print(f"  {k}: {v}")


def cmd_keys(args):
    data = _post("keys/list")
    keys = data.get("result", {}).get("keys", []) if isinstance(data.get("result"), dict) else data.get("keys", [])
    if not keys:
        _print("[yellow]Aucune clé API[/yellow]" if RICH else "Aucune clé API")
        return
    rows = []
    for k in keys:
        rows.append((k.get("provider_ref", "?"), k.get("tag", "?"),
                     k.get("grade", "?"), k.get("locked", False) and "🔒" or "🔓"))
    _print_table("Clés API", ("Provider", "Tag", "Grade", "Verrou"), rows)


def cmd_providers(args):
    data = _get("providers")
    providers = data.get("result", {}).get("providers", []) if isinstance(data.get("result"), dict) else data.get("providers", [])
    if not providers:
        _print("[yellow]Aucun provider[/yellow]" if RICH else "Aucun provider")
        return
    rows = []
    for p in providers:
        rows.append((p.get("ref", "?"), p.get("name", "?"), p.get("provider_type", "?")))
    _print_table("Providers LLM", ("Ref", "Nom", "Type"), rows)


def cmd_launch(args):
    body = {"role": args.role, "request": args.request}
    if args.provider:
        body["provider_ref"] = args.provider
    if args.model:
        body["model_ref"] = args.model
    r = _post("agent/launch", body)
    if r.get("status") == "ok":
        _print(f"[green]Agent créé: ID={r.get('agent_id')}[/green]" if RICH else f"Agent créé: ID={r.get('agent_id')}")
        if r.get("execute"):
            _print(f"[dim]Exécution: {json.dumps(r['execute'], indent=2)[:500]}[/dim]" if RICH else f"Exécution: {json.dumps(r.get('execute', {}), indent=2)[:500]}")
    else:
        _print(f"[red]Erreur: {r.get('error', '?')}[/red]" if RICH else f"Erreur: {r.get('error', '?')}")


def cmd_signal(args):
    r = _post("agent/signal", {"agent_id": int(args.agent_id), "type": args.action})
    if r.get("status") == "ok" or r.get("ok"):
        _print(f"[green]Signal {args.action} envoyé à agent #{args.agent_id}[/green]" if RICH else f"Signal {args.action} envoyé à agent #{args.agent_id}")
    else:
        _print(f"[red]Erreur: {r.get('error', '?')}[/red]" if RICH else f"Erreur: {r.get('error', '?')}")


# ── CLI ──

def main():
    import argparse
    p = argparse.ArgumentParser(description="CLI Dashboard ModelWeaver")
    sub = p.add_subparsers(dest="cmd")

    p_agents = sub.add_parser("agents", help="Lister les agents (groupés par équipe)")
    p_agents.set_defaults(func=cmd_agents)

    p_agent = sub.add_parser("agent", help="Piloter un agent (stop/restart)")
    p_agent.add_argument("agent_action", choices=("stop", "restart"), help="Action sur un agent")
    p_agent.add_argument("agent_name", help="Nom de l'agent")
    p_agent.add_argument("--request", help="Requête pour le redémarrage")
    p_agent.set_defaults(func=cmd_agent_action)

    p_services = sub.add_parser("services", help="Lister / piloter les services")
    p_services.add_argument("service_action", nargs="?", choices=("start", "restart", "stop"), help="Action sur un service")
    p_services.add_argument("service_name", nargs="?", help="Nom du service cible")
    p_services.set_defaults(func=cmd_services)

    p_system = sub.add_parser("system", help="État système")
    p_system.set_defaults(func=cmd_system)

    p_keys = sub.add_parser("keys", help="Lister les clés API")
    p_keys.set_defaults(func=cmd_keys)

    p_providers = sub.add_parser("providers", help="Lister les providers LLM")
    p_providers.set_defaults(func=cmd_providers)

    p_launch = sub.add_parser("launch", help="Lancer un agent one-shot")
    p_launch.add_argument("role", help="Rôle de l'agent (codeur, analyse, ...)")
    p_launch.add_argument("request", help="Requête / description de tâche")
    p_launch.add_argument("--provider", help="Provider LLM (optionnel)")
    p_launch.add_argument("--model", help="Modèle LLM (optionnel)")
    p_launch.set_defaults(func=cmd_launch)

    p_signal = sub.add_parser("signal", help="Envoyer un signal à un agent")
    p_signal.add_argument("agent_id", help="ID de l'agent")
    p_signal.add_argument("action", choices=("pause", "resume", "kill"), help="Type de signal")
    p_signal.set_defaults(func=cmd_signal)

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        return

    try:
        args.func(args)
    except Exception as e:
        _print(f"[red]Erreur: {e}[/red]" if RICH else f"Erreur: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
