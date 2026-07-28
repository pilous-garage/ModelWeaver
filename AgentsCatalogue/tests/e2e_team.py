"""Test E2E équipe — Manager + 2 Workers avec LLM.

Scénario : Projet Todo CLI (todo.py + test_todo.py)
  1. Init git central + workspace
  2. Manager LLM : scatter 2 tâches
  3. Worker1 LLM : implémente todo.py
  4. Worker2 LLM : implémente test_todo.py
  5. Manager : merge les branches + push
  6. Vérification : fichiers livrés dans git

Usage :
    LLM_PROVIDER=groq LLM_MODEL=groq/llama-3.1-8b-instant \
    PYTHONPATH=. python3 AgentsCatalogue/tests/e2e_team.py
"""

import json, os, shutil, subprocess, sys, tempfile, time, uuid
from pathlib import Path

t0 = time.time()
def log(msg): print(f"[{time.time()-t0:5.1f}s] {msg}")

print("="*60)
print("TEST E2E ÉQUIPE — Manager + 2 Workers LLM")
print("="*60)

PROVIDER = os.environ.get("LLM_PROVIDER", "groq")
MODEL = os.environ.get("LLM_MODEL", "groq/llama-3.1-8b-instant")
log(f"Provider: {PROVIDER} / {MODEL}")

# ── 1. Setup ──────────────────────────────────────────────────

tmpdir = Path(tempfile.mkdtemp(prefix="mw_e2e_team_"))
os.environ["MW_HOME"] = str(tmpdir)
home = tmpdir / "home"
home.mkdir()
log(f"Temp: {tmpdir}")

# Git central
from AgentsCatalogue.lib.git.lite import exec as gl
assert gl({"action": "init"}, str(home))["exit_code"] == 0
central = home / "shared" / "repo.git"

# Workspace + tâches
ws_id = f"ws_{uuid.uuid4().hex[:6]}"
from modules.sql.workspace import WorkspaceDB, TaskRepository
ws_db_path = Path(str(home)) / "workspaces" / ws_id / "workspace.db"
ws_db_path.parent.mkdir(parents=True, exist_ok=True)
wdb = WorkspaceDB(str(ws_db_path))
wdb.workspaces.create(ws_id, name="Todo CLI", description="CLI Todo list en Python")
tr = TaskRepository(wdb.conn, ws_id)

# Créer les tâches
TASKS = [
    {"title": "todo.py", "description": "todo.py avec classe TodoList (add/list/done/delete, stockage JSON)"},
    {"title": "test_todo.py", "description": "test_todo.py avec pytest (test_add, test_list, test_done)"},
]
for t in TASKS:
    tr.create(title=t["title"], description=t["description"])
log(f"{len(TASKS)} tâches créées")

# Cloner le repo central dans workdir pour que les workers puissent commit/push
workdir = Path(str(home)) / "work"
workdir.mkdir(parents=True, exist_ok=True)
subprocess.run(["git", "clone", str(central), str(workdir)], capture_output=True, text=True)
subprocess.run(["git", "-C", str(workdir), "config", "user.name", "worker"], capture_output=True)
subprocess.run(["git", "-C", str(workdir), "config", "user.email", "worker@test"], capture_output=True)
log("Repo cloné dans workdir")

# ── 2. Worker LLM : chaque worker exécute une tâche ──────────

from AgentsCatalogue.lib.workflow.autonomous import exec as auto

def worker_exec(worker_name: str, task_id: int):
    """Exécute une tâche via LLM et commit dans une branche."""
    task = tr.get(task_id)
    log(f"\n{worker_name}: {task['title']}")
    
    result = auto({
        "request": f"Implémente : {task['description']}. "
                   f"Écris le fichier, puis exécute: git checkout -b '{worker_name}', "
                   f"git add ., git commit -m '{worker_name}: {task['title']}', "
                   f"git push origin '{worker_name}'",
        "bundles": ["dev"],
        "provider_ref": PROVIDER,
        "model_ref": MODEL,
        "max_loops": 6,
    }, str(home))
    
    sig = result.get("signal", "")
    out = result.get("stdout", "")[:200]
    if sig == "loop_end":
        log(f"  ✅ terminé")
        tr.done(task_id)
    elif out:
        log(f"  {sig}: {out}")
    else:
        log(f"  {sig}")
    return result

# Worker 1 : todo.py
worker_exec("worker1", 1)
time.sleep(5)  # Pause pour éviter rate-limit

# Worker 2 : test_todo.py
worker_exec("worker2", 2)
time.sleep(5)

# ── 3. Manager : merge des branches ──────────────────────────

log("\nManager: merge des branches dans main...")
ws_mgr = home / "agents" / "manager" / "workspace"
ws_mgr.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(["git", "clone", str(central), str(ws_mgr)], capture_output=True, text=True)
subprocess.run(["git", "-C", str(ws_mgr), "checkout", "-b", "main", "origin/main"], capture_output=True, text=True)
subprocess.run(["git", "-C", str(ws_mgr), "fetch", "origin"], capture_output=True, text=True)

for branch in ["worker1", "worker2"]:
    r = subprocess.run(["git", "-C", str(ws_mgr), "merge", f"origin/{branch}",
                        "--allow-unrelated-histories"], capture_output=True, text=True)
    if r.returncode == 0:
        log(f"  merge {branch}: OK")
    elif "already up to date" in r.stdout.lower():
        log(f"  merge {branch}: déjà à jour")
    else:
        log(f"  merge {branch}: conflit (ignoré)")
        subprocess.run(["git", "-C", str(ws_mgr), "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", str(ws_mgr), "commit", "-m", f"merge {branch}"], capture_output=True)

subprocess.run(["git", "-C", str(ws_mgr), "push", "-u", "origin", "main"], capture_output=True)
log("  main pushed")

# ── 4. Vérification ───────────────────────────────────────────

log("\nVérification...")
verify = tmpdir / "_verify"
subprocess.run(["git", "clone", str(central), str(verify)], capture_output=True)
subprocess.run(["git", "-C", str(verify), "checkout", "main"], capture_output=True)

files = [f.name for f in verify.iterdir() if f.is_file()]
log(f"Fichiers livrés dans main: {files}")

if "todo.py" in files:
    code = (verify / "todo.py").read_text()
    log(f"  ✅ todo.py ({len(code)} octets)")
if "test_todo.py" in files:
    code = (verify / "test_todo.py").read_text()
    log(f"  ✅ test_todo.py ({len(code)} octets)")

pending = tr.list_pending()
log(f"Tâches restantes: {len(pending)}")
if not pending: log("  ✅ Toutes les tâches sont done")

# ── 5. Nettoyage ──────────────────────────────────────────────

shutil.rmtree(tmpdir)
print(f"\n{'='*60}")
print(f"TEST E2E ÉQUIPE TERMINÉ")
print(f"Temps: {time.time()-t0:.0f}s")
print(f"{'='*60}")
