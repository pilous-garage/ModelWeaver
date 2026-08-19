"""Test E2E réel — agents LLM génèrent le code sans scénario pré-défini.

Déroulement :
  1. Création workspace + git central
  2. Manager LLM → scatter(todo CLI) → 6-8 tâches créées
  3. Worker LLM → pick task → implémente via shell/git → done
  4. Chaque worker tourne en autonome avec bundles dev
  5. Vérification : fichiers livrés dans git
"""

import json, os, shutil, subprocess, sys, tempfile, time, uuid
from pathlib import Path

HERE = Path(__file__).parent
t0 = time.time()

def log(msg): print(f"[{time.time()-t0:5.1f}s] {msg}")

print("="*60)
print("TEST E2E RÉEL — Agents LLM codent le projet")
print("="*60)

# ── 0. Provider LLM ──────────────────────────────────────────

PROVIDER = os.environ.get("LLM_PROVIDER", "")
MODEL = os.environ.get("LLM_MODEL", "")
log(f"Provider: {PROVIDER or 'auto (assign_llm)'} / Model: {MODEL or 'auto'}")

# ── 1. Setup ──────────────────────────────────────────────────

tmpdir = Path(tempfile.mkdtemp(prefix="mw_e2e_real_"))
os.environ["MW_HOME"] = str(tmpdir)
home = tmpdir / "home"; home.mkdir()
log(f"Temp: {tmpdir}")

# Git central
from AgentsCatalogue.lib.git.lite import exec as git_lite
assert git_lite({"action": "init"}, str(home))["exit_code"] == 0
central = home / "shared" / "repo.git"

# Workspace
ws_id = f"ws_{uuid.uuid4().hex[:6]}"
from modules.sqlite.workspace.workspace import WorkspaceDB, TaskRepository
ws_db = home / "workspaces" / ws_id / "workspace.db"
ws_db.parent.mkdir(parents=True, exist_ok=True)
wdb = WorkspaceDB(str(ws_db))
wdb.workspaces.create(ws_id, name="Todo CLI", description="CLI Todo list en Python")
tr = TaskRepository(wdb.conn, ws_id)
log(f"Workspace: {ws_id}")

# ── 2. Manager LLM : scatter ──────────────────────────────────

log("\nManager LLM : décomposition de la mission...")
from AgentsCatalogue.lib.workspace.scatter import exec as scatter

result = scatter({
    "workspace_id": ws_id,
    "request": "Crée un CLI todo list en Python (add/list/done/delete, stockage JSON, tests pytest)",
    "provider_ref": PROVIDER,
    "model_ref": MODEL,
}, str(home))
assert result.get("ok"), f"Scatter failed: {result}"
task_ids = result["task_ids"]
log(f"{len(task_ids)} tâches créées: {task_ids}")

# ── 3. Worker LLM : exécute chaque tâche ──────────────────────

log("\nWorker LLM : exécution des tâches...")
from AgentsCatalogue.lib.workflow.autonomous import exec as auto_exec

for i, task_id in enumerate(task_ids):
    task = tr.get(task_id)
    if not task:
        log(f"  Tâche #{task_id} introuvable, skip")
        continue
    log(f"\n--- Tâche #{task_id}: {task['title']} ---")

    # Le worker : boucle autonome avec dev bundles
    result = auto_exec({
        "request": f"Implémente cette tâche : {task['description']}\n\n"
                   f"Utilise le shell pour écrire les fichiers et git pour commiter.",
        "bundles": ["dev"],
        "context": f"Tu travailles dans le workspace {ws_id}. "
                   f"Le dépôt git est dans l'espace de travail. "
                   f"Écris le code, commit-le, puis marque la tâche comme terminée.",
        "max_loops": 15,
        "grouping": "req-optimal",
        "provider_ref": PROVIDER,
        "model_ref": MODEL,
        "llm_timeout": 30,
    }, str(home))

    log(f"  Signal: {result.get('signal', '?')}")
    log(f"  Sortie: {result.get('stdout', '')[:100]}")

    # Marquer la tâche done
    try:
        tr.done(task_id)
    except Exception:
        pass

# ── 4. Vérification git ───────────────────────────────────────

log("\nVérification du dépôt git...")
verify = tmpdir / "_verify"
subprocess.run(["git", "clone", str(central), str(verify)], capture_output=True)
files = [f.name for f in verify.iterdir() if f.is_file()]
log(f"Fichiers livrés: {files}")

# ── 5. Nettoyage ──────────────────────────────────────────────

shutil.rmtree(tmpdir)
print(f"\n{'='*60}")
print(f"TEST E2E RÉEL TERMINÉ")
print(f"Temps: {time.time()-t0:.0f}s")
print(f"Fichiers: {files}")
print(f"{'='*60}")
