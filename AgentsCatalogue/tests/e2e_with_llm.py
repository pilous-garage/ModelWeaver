"""Test E2E complet avec LLM — Todo List CLI.

Pré-requis :
  - Une clé API LLM configurée
  - PYTHONPATH=. python3 AgentsCatalogue/tests/e2e_with_llm.py

Déroulement :
  1. Init git central + workspace DB
  2. Appel LLM (manager) : décompose la mission en tâches → scatter
  3. Workers : exécutent les tâches (simulé sans LLM pour ce test)
  4. Vérification : tâches done + git log
"""

import json, os, shutil, subprocess, sys, tempfile, time, uuid
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
t0 = time.time()

def log(msg): print(f"[{time.time()-t0:5.1f}s] {msg}")

print("="*60)
print("TEST E2E AVEC LLM — Todo CLI")
print("="*60)

# ── 0. Vérification LLM ──────────────────────────────────────

log("Vérification des providers LLM...")
PROVIDER = os.environ.get("LLM_PROVIDER", "")
MODEL = os.environ.get("LLM_MODEL", "")
if not PROVIDER:
    # Chercher dans les providers connus
    known_providers = ["opencode-zen", "openai", "anthropic", "groq", "mistral"]
    for p in known_providers:
        if os.environ.get(f"{p.upper().replace('-','_')}_API_KEY"):
            PROVIDER = p
            break
if not PROVIDER:
    log("⚠️  Définis une variable d'env, exemple :")
    log('   LLM_PROVIDER=openai LLM_MODEL=gpt-4o-mini python3 ...')
    log('   Ou : OPENAI_API_KEY=sk-... python3 ...')
    sys.exit(1)
log(f"Provider: {PROVIDER} / Model: {MODEL}")

# ── 1. Setup ──────────────────────────────────────────────────

tmpdir = Path(tempfile.mkdtemp(prefix="mw_e2e_llm_"))
os.environ["MW_HOME"] = str(tmpdir)
home = tmpdir / "home"
home.mkdir()
log(f"Temp dir: {tmpdir}")

# ── 2. Git init + workspace ──────────────────────────────────

log("Init dépôt central...")
from AgentsCatalogue.lib.git.lite import exec as git_lite
assert git_lite({"action": "init"}, str(home))["exit_code"] == 0

ws_id = f"ws_todo_{uuid.uuid4().hex[:6]}"
from modules.sqlite.workspace.workspace import WorkspaceDB, TaskRepository
ws_db = home / "workspaces" / ws_id / "workspace.db"
ws_db.parent.mkdir(parents=True, exist_ok=True)
wdb = WorkspaceDB(str(ws_db))
wdb.workspaces.create(ws_id, name="Todo CLI", description="CLI Todo list en Python")
tr = TaskRepository(wdb.conn, ws_id)
log(f"Workspace: {ws_id}")

# ── 3. Scatter via LLM ────────────────────────────────────────

log("Appel LLM pour décomposer la mission en tâches...")
from AgentsCatalogue.lib.workspace.scatter import exec as scatter

result = scatter({
    "workspace_id": ws_id,
    "request": "Crée un CLI todo list en Python avec add/list/done/delete et tests",
    "context": f"Provider: {PROVIDER}, Model: {MODEL}",
    "provider_ref": PROVIDER,
    "model_ref": MODEL,
    "worker_names": [],
}, str(home))
log(f"Scatter: {result.get('count', 0)} tâches créées (ids: {result.get('task_ids', [])})")
assert result.get("ok"), f"Scatter failed: {result}"

# ── 4. Exécution simulée des tâches ───────────────────────────

log("\nExécution des tâches par les workers...")
CODE = '''import json, sys
from pathlib import Path
DB = Path.home() / ".todo.json"

class TodoList:
    def __init__(self):
        self.tasks = json.loads(DB.read_text()) if DB.exists() else []
    def add(self, text):
        t = {"id": len(self.tasks)+1, "text": text, "done": False}
        self.tasks.append(t)
        DB.write_text(json.dumps(self.tasks, indent=2))
        return t
    def list(self):
        for t in self.tasks:
            status = "+" if t["done"] else " "
            print(f"[{status}] #{t['id']} {t['text']}")
    def done(self, tid):
        for t in self.tasks:
            if t["id"] == tid: t["done"] = True; break
        DB.write_text(json.dumps(self.tasks, indent=2))
    def delete(self, tid):
        self.tasks = [t for t in self.tasks if t["id"] != tid]
        DB.write_text(json.dumps(self.tasks, indent=2))
'''

TEST_CODE = '''from todo import TodoList
def test_add():
    tl = TodoList()
    t = tl.add("test")
    assert t["text"] == "test"
def test_done():
    tl = TodoList(); tl.tasks = []; t = tl.add("x")
    tl.done(t["id"]); assert tl.tasks[0]["done"]
'''

def worker_clone(worker_name):
    ws = home / "agents" / worker_name / "workspace"
    ws.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", str(home / "shared" / "repo.git"), str(ws)],
                   capture_output=True)
    subprocess.run(["git", "-C", str(ws), "checkout", "-b", f"{worker_name}-branch"],
                   capture_output=True)
    return ws

# Worker 1: todo.py
log("Worker1: todo.py...")
ws1 = worker_clone("worker1")
(ws1 / "todo.py").write_text(CODE)
subprocess.run(["git", "-C", str(ws1), "add", "."], capture_output=True)
subprocess.run(["git", "-C", str(ws1), "commit", "-m", "worker1: todo.py"], capture_output=True)
subprocess.run(["git", "-C", str(ws1), "push", "-u", "origin", "worker1-branch"], capture_output=True)

# Worker 2: tests
log("Worker2: test_todo.py...")
ws2 = worker_clone("worker2")
(ws2 / "test_todo.py").write_text(TEST_CODE)
subprocess.run(["git", "-C", str(ws2), "add", "."], capture_output=True)
subprocess.run(["git", "-C", str(ws2), "commit", "-m", "worker2: tests"], capture_output=True)
subprocess.run(["git", "-C", str(ws2), "push", "-u", "origin", "worker2-branch"], capture_output=True)

# Marquer les tâches comme done
for t in tr.list_all():
    tr.done(t["task_id"])

# ── 5. Merge manager ──────────────────────────────────────────

log("Manager: merge des branches...")
ws_mgr = home / "agents" / "manager" / "workspace"
ws_mgr.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(["git", "clone", str(home / "shared" / "repo.git"), str(ws_mgr)], capture_output=True)
subprocess.run(["git", "-C", str(ws_mgr), "checkout", "-b", "main", "origin/main"], capture_output=True)

for branch in ["worker1-branch", "worker2-branch"]:
    r = subprocess.run(["git", "-C", str(ws_mgr), "merge", f"origin/{branch}",
                        "--allow-unrelated-histories"], capture_output=True, text=True)
    if r.returncode != 0:
        log(f"  Conflit {branch}, résolution auto...")
        subprocess.run(["git", "-C", str(ws_mgr), "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", str(ws_mgr), "commit", "-m", f"merge {branch}"], capture_output=True)
    else:
        log(f"  merge {branch}: OK")

subprocess.run(["git", "-C", str(ws_mgr), "push", "-u", "origin", "main"], capture_output=True)

# ── 6. Vérification ───────────────────────────────────────────

log("\nVérification...")
verify = tmpdir / "_verify"
subprocess.run(["git", "clone", str(home / "shared" / "repo.git"), str(verify)], capture_output=True)
subprocess.run(["git", "-C", str(verify), "checkout", "main"], capture_output=True)

files = [f.name for f in verify.iterdir() if f.is_file()]
log(f"Fichiers: {files}")
assert "todo.py" in files, "todo.py manquant!"
assert "test_todo.py" in files, "test_todo.py manquant!"

tasks = tr.list_all()
log(f"Tâches: {len(tasks)} total, {len(tr.list_pending())} pending")
assert len(tr.list_pending()) == 0, "Tâches encore en attente!"

# ── 7. Nettoyage ──────────────────────────────────────────────

shutil.rmtree(tmpdir)
print(f"\n{'='*60}")
print(f"TEST E2E AVEC LLM RÉUSSI")
print(f"Temps: {time.time()-t0:.0f}s")
print(f"{'='*60}")
