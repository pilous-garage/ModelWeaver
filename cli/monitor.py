#!/usr/bin/env python3
import click
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
from rich.console import Console

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

def agents_table(agents: list) -> Table:
    t = Table(title="Agents", box=box.ROUNDED, title_justify="left")
    for c in ("ID", "Nom", "Rôle", "Status", "Actif"):
        t.add_column(c)
    for a in agents:
        active = "●" if a.get("running") else "○"
        t.add_row(str(a.get("agent_id", "?")), a.get("name", "?"), a.get("role_type", "?"), a.get("status", "?"), active)
    return t

def services_table(services: list) -> Table:
    t = Table(title="Services", box=box.ROUNDED, title_justify="left")
    for c in ("Nom", "Status", "PID", "R"):
        t.add_column(c)
    for s in services:
        pid = str(s.get("pid") or "—")
        t.add_row(s.get("name", "?"), s.get("status", "?"), pid, str(s.get("restarts", 0)))
    return t

def system_panel(state: dict) -> Panel:
    text = Text(f"CPU: {state.get('cpu_count', '?')} cœurs\nRAM: {state.get('ram_used_gb', '?')}/{state.get('ram_total_gb', '?')} Go\nDisque libre: {state.get('disk_free_gb', '?')} Go")
    return Panel(text, title="Système", border_style="cyan")

@click.group(help="CLI Monitor pour ModelWeaver.")
def cli():
    pass

@cli.command(help="Lance le dashboard en temps réel.")
@click.option('--interval', default=2, help='Intervalle de rafraîchissement en secondes.')
def dashboard(interval):
    console = Console()
    console.print("[bold cyan]ModelWeaver — TUI Monitor[/bold cyan] (Ctrl+C pour quitter)\n")
    try:
        with Live(refresh_per_second=0.5, screen=False) as live:
            while True:
                layout = Layout()
                layout.split_column(Layout(name="top", ratio=1), Layout(name="bottom", ratio=1))
                layout["top"].split_row(Layout(agents_table(fetch_agents())), Layout(system_panel(fetch_system())))
                layout["bottom"].update(services_table(fetch_services()))
                live.update(layout)
                time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[yellow]Monitor arrêté[/yellow]")

@cli.command(help="Liste les agents disponibles.")
def list_agents():
    agents = fetch_agents()
    for a in agents:
        click.echo(f"{a.get('agent_id')}: {a.get('name')} ({a.get('status')})")

if __name__ == "__main__":
    cli()
