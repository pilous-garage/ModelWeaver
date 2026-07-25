#!/usr/bin/env python3
"""TUI live monitor — terminal dashboard temps réel.

Affiche agents, services et état système, rafraîchi toutes les 2s.
Utilise rich.live — nécessite le daemon sur le port 8770.

Usage :
  python3 cli/monitor.py
"""

import sys
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

from rich.live import Live
from rich.table import Table
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text
from rich import box


DAEMON_PORT = 8770
DAEMON_URL = f"http://127.0.0.1:{DAEMON_PORT}"


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
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = json.loads(resp.read())
            return raw.get("result") if "result" in raw else raw
    except Exception:
        return {}


def fetch_agents() -> list:
    data = _post("agent/list")
    return data.get("agents", [])


def fetch_services() -> list:
    data = _post("service/list")
    return data.get("services", [])


def fetch_system() -> dict:
    return _post("system/state/get")


def build_table(title: str, columns: list, rows: list) -> Table:
    t = Table(title=title, box=box.ROUNDED, title_justify="left")
    for c in columns:
        t.add_column(c)
    for r in rows:
        t.add_row(*[str(v) for v in r])
    return t


def agents_table(agents: list) -> Table:
    rows = []
    for a in agents:
        active = "●" if a.get("running") else "○"
        rows.append((str(a.get("agent_id", "?")),
                     a.get("name", "?"),
                     a.get("role_type", "?"),
                     a.get("status", "?"),
                     active))
    return build_table("Agents", ("ID", "Nom", "Rôle", "Status", "Actif"), rows) if rows else Table(title="Agents (aucun)", box=box.ROUNDED)


def services_table(services: list) -> Table:
    rows = []
    for s in services:
        pid = s.get("pid") or "—"
        if pid == -1:
            pid = "—"
        rows.append((s.get("name", "?"),
                     s.get("status", "?"),
                     str(pid),
                     str(s.get("restarts", 0))))
    return build_table("Services", ("Nom", "Status", "PID", "R"), rows) if rows else Table(title="Services (aucun)", box=box.ROUNDED)


def system_panel(state: dict) -> Panel:
    cpu = state.get("cpu_count", "?")
    ram_total = state.get("ram_total_gb", "?")
    ram_used = state.get("ram_used_gb", "?")
    disk_free = state.get("disk_free_gb", "?")
    text = Text(f"CPU: {cpu} cœurs\nRAM: {ram_used}/{ram_total} Go\nDisque libre: {disk_free} Go")
    return Panel(text, title="Système", border_style="cyan")


def make_layout(agents, services, system) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="top", ratio=1),
        Layout(name="bottom", ratio=1),
    )
    layout["top"].split_row(
        Layout(agents_table(agents)),
        Layout(system_panel(system)),
    )
    layout["bottom"].update(services_table(services))
    return layout


def main():
    from rich.console import Console
    console = Console()
    console.print("[bold cyan]ModelWeaver — TUI Monitor[/bold cyan] (rafraîchi toutes les 2s, Ctrl+C pour quitter)\n")

    try:
        with Live(refresh_per_second=0.5, screen=False) as live:
            while True:
                agents = fetch_agents()
                services = fetch_services()
                system = fetch_system()
                live.update(make_layout(agents, services, system))
                time.sleep(2)
    except KeyboardInterrupt:
        console.print("\n[yellow]Monitor arrêté[/yellow]")


if __name__ == "__main__":
    main()
