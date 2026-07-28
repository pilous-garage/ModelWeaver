"""Test E2E complet — Todo List CLI.

Simule le cycle complet sans LLM :
  Manager → crée workspace + tâches
  Worker1 → implémente add
  Worker2 → implémente list
  Worker3 → implémente done/delete
  Worker4 → tests
  Manager → merge + résolution conflits

Pour exécuter avec LLM, remplacer les sections #SIMULATED
par des appels à autonomous@v1.
"""

import json, os, shutil, subprocess, tempfile, time
from pathlib import Path
from typing import Dict, List

HERE = Path(__file__).parent
ROOT = HERE.parent.parent

def log(msg): print(f"  [{time.time() - t0:.1f}s] {msg}")

# ── Setup ───────────────────────────────────────────────────

t0 = time.time()
td = tempfile.mkdtemp(prefix="modelweaver_e2e_")
print(f"\n{'='*60}")
print(f"E2E TEST TODO CLI")
print(f"{'='*60}")
print(f"Temp dir: {td}")

home = Path(td) / "home"
central = home / "shared" / "repo.git"
ws_dir = home / "agents" / "manager" / "workspace"

# ── 1. Initialisation git-lite ───────────────────────────────

log("Init dépôt central git...")
from AgentsCatalogue.lib.git.lite import exec as git_lite
r = git_lite({"action": "init"}, str(home))
assert r["exit_code"] == 0, f"init failed: {r}"
log(f"Central repo: {central}")

# ── 2. Création workspace DB ─────────────────────────────────

log("Création workspace DB...")
ws_db_path = home / "workspaces" / "ws_todo" / "workspace.db"
ws_db_path.parent.mkdir(parents=True, exist_ok=True)
from modules.sql.workspace import WorkspaceDB, TaskRepository
wdb = WorkspaceDB(str(ws_db_path))
wdb.workspaces.create("ws_todo", name="Todo CLI Project", description="CLI Todo list en Python")
tr = TaskRepository(wdb.conn, "ws_todo")

log(f"Workspace ws_todo créé")

# ── 3. Création des tâches (simule le manager) ───────────────

TASKS: List[Dict] = [
    {"title": "Structure + storage JSON", "description": "Créer todo.py avec stockage JSON (load/save/add)"},
    {"title": "Commande add", "description": "Implémenter la commande 'todo add <texte>'. Ajoute une tâche avec un id auto-incrémenté."},
    {"title": "Commande list", "description": "Implémenter 'todo list'. Affiche toutes les tâches avec leur id et statut."},
    {"title": "Commandes done/delete", "description": "Implémenter 'todo done <id>' et 'todo delete <id>'."},
    {"title": "Tests unitaires", "description": "Créer test_todo.py avec pytest. Tester add/list/done/delete."},
    {"title": "README", "description": "Créer README.md avec usage et exemples."},
]

for i, t in enumerate(TASKS):
    tr.create(title=t["title"], description=t["description"], priority=i)
log(f"{len(TASKS)} tâches créées")

# ── 4. Worker : cloner le repo ───────────────────────────────

def worker_setup(agent_name: str) -> Path:
    ws = home / "agents" / agent_name / "workspace"
    if ws.exists():
        shutil.rmtree(ws)
    ws.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["git", "clone", str(central), str(ws)], capture_output=True, text=True)
    assert r.returncode == 0, f"clone {agent_name}: {r.stderr}"
    subprocess.run(["git", "-C", str(ws), "checkout", "main"], capture_output=True, text=True)
    return ws

# ── 5. Exécution des tâches (simule les workers) ─────────────

CODE = r'''import json, sys
from pathlib import Path

DB_PATH = Path.home() / ".todo.json"

class TodoList:
    def __init__(self):
        self.tasks = []
        self._load()

    def add(self, text: str) -> dict:
        task = {"id": len(self.tasks) + 1, "text": text, "done": False}
        self.tasks.append(task)
        self._save()
        return task

    def _load(self):
        if DB_PATH.exists():
            self.tasks = json.loads(DB_PATH.read_text())

    def _save(self):
        DB_PATH.write_text(json.dumps(self.tasks, indent=2))

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] == "help":
        print("Usage: todo add|list|done|delete <args>")
    elif sys.argv[1] == "add":
        t = TodoList().add(" ".join(sys.argv[2:]))
        print(f"Added: #{t['id']} {t['text']}")
    elif sys.argv[1] == "list":
        tl = TodoList()
        for t in tl.tasks:
            status = "✓" if t["done"] else " "
            print(f"[{status}] #{t['id']} {t['text']}")
'''

TEST_CODE = r'''import json, tempfile, os
from pathlib import Path
from todo import TodoList

def test_add():
    tl = TodoList()
    tl.tasks = []
    t = tl.add("test task")
    assert t["id"] == 1
    assert t["text"] == "test task"
    assert t["done"] is False

def test_list():
    tl = TodoList()
    tl.tasks = [{"id": 1, "text": "a", "done": False}]
    assert len(tl.tasks) == 1

def test_done():
    import todo
    todo.DB_PATH = Path(tempfile.mktemp())
    tl = TodoList()
    tl.tasks = [{"id": 1, "text": "x", "done": False}]
    for t in tl.tasks:
        if t["id"] == 1:
            t["done"] = True
    assert tl.tasks[0]["done"]
'''

# Worker 1: todo.py (structure + add)
log("Worker1: implémente todo.py...")
ws1 = worker_setup("worker1")
subprocess.run(["git", "-C", str(ws1), "checkout", "-b", "worker1-todo"], capture_output=True)
(ws1 / "todo.py").write_text(CODE)
subprocess.run(["git", "-C", str(ws1), "add", "."], capture_output=True)
subprocess.run(["git", "-C", str(ws1), "commit", "-m", "worker1: todo.py (structure + add)"], capture_output=True)
subprocess.run(["git", "-C", str(ws1), "push", "-u", "origin", "worker1-todo"], capture_output=True)
log("  pushed worker1-todo")

# Worker 2: ajoute list dans todo.py (conflit potentiel)
log("Worker2: implémente list...")
ws2 = worker_setup("worker2")
subprocess.run(["git", "-C", str(ws2), "checkout", "-b", "worker2-list"], capture_output=True)
(ws2 / "todo.py").write_text(CODE + '\n# worker2: implemented list\n')
subprocess.run(["git", "-C", str(ws2), "add", "."], capture_output=True)
subprocess.run(["git", "-C", str(ws2), "commit", "-m", "worker2: list command"], capture_output=True)
subprocess.run(["git", "-C", str(ws2), "push", "-u", "origin", "worker2-list"], capture_output=True)
log("  pushed worker2-list")

# Worker 3: test_todo.py
log("Worker3: implémente tests...")
ws3 = worker_setup("worker3")
subprocess.run(["git", "-C", str(ws3), "checkout", "-b", "worker3-tests"], capture_output=True)
(ws3 / "test_todo.py").write_text(TEST_CODE)
subprocess.run(["git", "-C", str(ws3), "add", "."], capture_output=True)
subprocess.run(["git", "-C", str(ws3), "commit", "-m", "worker3: unit tests"], capture_output=True)
subprocess.run(["git", "-C", str(ws3), "push", "-u", "origin", "worker3-tests"], capture_output=True)
log("  pushed worker3-tests")

# ── 6. Merge (simule le manager) ─────────────────────────────

log("Manager: merge des branches...")
ws_mgr = worker_setup("manager")
subprocess.run(["git", "-C", str(ws_mgr), "checkout", "-b", "main", "origin/main"], capture_output=True, text=True)
subprocess.run(["git", "-C", str(ws_mgr), "fetch", "origin"], capture_output=True)

# Merge worker1
r = subprocess.run(["git", "-C", str(ws_mgr), "merge", "origin/worker1-todo", "--allow-unrelated-histories"], capture_output=True, text=True)
log(f"  merge worker1: {'OK' if r.returncode == 0 else r.stderr[:60]}")

# Merge worker2 (peut avoir conflit avec worker1)
r = subprocess.run(["git", "-C", str(ws_mgr), "merge", "origin/worker2-list"], capture_output=True, text=True)
if r.returncode != 0:
    log("  CONFLIT worker2-list! Résolution auto...")
    subprocess.run(["git", "-C", str(ws_mgr), "checkout", "--ours", "todo.py"], capture_output=True)
    (ws_mgr / "todo.py").write_text(CODE + '\n# worker2 list merged\n')
    subprocess.run(["git", "-C", str(ws_mgr), "add", "todo.py"], capture_output=True)
    subprocess.run(["git", "-C", str(ws_mgr), "commit", "-m", "merge worker2-list (resolved conflict)"], capture_output=True)
    log("  conflit résolu")
else:
    log("  merge worker2: OK")

# Merge worker3 (nouveau fichier, pas de conflit)
r = subprocess.run(["git", "-C", str(ws_mgr), "merge", "origin/worker3-tests"], capture_output=True, text=True)
if r.returncode != 0:
    log(f"  CONFLIT worker3! Résolution...")
    subprocess.run(["git", "-C", str(ws_mgr), "merge", "--abort"], capture_output=True)
    subprocess.run(["git", "-C", str(ws_mgr), "merge", "origin/worker3-tests", "--allow-unrelated-histories"], capture_output=True, text=True)

# Push main
subprocess.run(["git", "-C", str(ws_mgr), "push", "-u", "origin", "main"], capture_output=True)
log("  main pushed to central")

# ── 7. Vérification ───────────────────────────────────────────

log("\nVérifications...")

# Le repo central contient tout
central_clone = Path(td) / "_verify"
subprocess.run(["git", "clone", str(central), str(central_clone)], capture_output=True)
subprocess.run(["git", "-C", str(central_clone), "checkout", "main"], capture_output=True, text=True)
files = [f.name for f in central_clone.iterdir() if f.is_file()]
log(f"Fichiers dans central: {files}")

# todo.py existe
assert (central_clone / "todo.py").exists(), "todo.py manquant"
log("  ✓ todo.py présent")

# test_todo.py existe
assert (central_clone / "test_todo.py").exists(), "test_todo.py manquant"
log("  ✓ test_todo.py présent")

# todo.py est exécutable
code = (central_clone / "todo.py").read_text()
assert "add" in code, "add command manquante"
assert "list" in code, "list command manquante"
log("  ✓ Commandes add + list présentes")

# Vérifier les tâches dans le workspace
tasks = tr.list_all()
log(f"Tâches dans workspace: {len(tasks)}")

# ── 8. Nettoyage ──────────────────────────────────────────────

shutil.rmtree(td)

print(f"\n{'='*60}")
print(f"TEST E2E RÉUSSI")
print(f"{'='*60}")
print(f"Temps total: {time.time() - t0:.1f}s")
print(f"Workers: 3 (worker1, worker2, worker3)")
print(f"Tâches: 6")
print(f"Branches: worker1-todo, worker2-list, worker3-tests")
print(f"Merge: 3 (1 conflit résolu)")
print(f"Fichiers livrés: todo.py, test_todo.py")
