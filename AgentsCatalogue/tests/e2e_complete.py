"""Test E2E complet — création équipe → mission → décomposition → exécution automatique.

Usage :
    LLM_PROVIDER=groq LLM_MODEL=groq/llama-3.1-8b-instant \
    PYTHONPATH=. python3 AgentsCatalogue/tests/e2e_complete.py
"""

import json, os, shutil, subprocess, sys, tempfile, time, uuid
from pathlib import Path

t0 = time.time()
def log(msg): print(f"[{time.time()-t0:5.1f}s] {msg}")

print("="*60)
print("TEST E2E COMPLET — Mission → Code")
print("="*60)

PROVIDER = os.environ.get("LLM_PROVIDER", "groq")
MODEL = os.environ.get("LLM_MODEL", "groq/llama-3.1-8b-instant")
log(f"Provider: {PROVIDER} / Model: {MODEL}")

# ── 1. Setup ──────────────────────────────────────────────────

tmpdir = Path(tempfile.mkdtemp(prefix="mw_e2e_full_"))
os.environ["MW_HOME"] = str(tmpdir)
home = tmpdir / "home"
home.mkdir()
log(f"Temp: {tmpdir}")

# Git central
from AgentsCatalogue.lib.git.lite import exec as gl
assert gl({"action": "init"}, str(home))["exit_code"] == 0

# ── 2. Création des tâches (explicites, pas de LLM) ──────────

log("Création des tâches...")

ws_id = f"ws_{uuid.uuid4().hex[:6]}"
from modules.sqlite.workspace.workspace import WorkspaceDB, TaskRepository

ws_db_path = Path(str(home)) / "workspaces" / ws_id / "workspace.db"
ws_db_path.parent.mkdir(parents=True, exist_ok=True)
wdb = WorkspaceDB(str(ws_db_path))
wdb.workspaces.create(ws_id, name="Todo CLI", description="Projet de test")
tr = TaskRepository(wdb.conn, ws_id)

from AgentsCatalogue.lib.workspace.scatter import exec as scatter

r = scatter({
    "workspace_id": ws_id,
    "tasks": [
        {"title": "hello.py", "description": "Créer hello.py avec print('Hello from ModelWeaver')"},
        {"title": "test.py", "description": "Créer test_hello.py avec un assert sur l'import de hello"},
    ],
}, str(home))
assert r.get("ok"), f"Scatter failed: {r}"
log(f"{r['count']} tâches créées: {r['task_ids']}")

# ── 3. Worker LLM : exécute chaque tâche ──────────────────────

from AgentsCatalogue.lib.workflow.autonomous import exec as auto

for t in tr.list_all():
    log(f"\n  Tâche #{t['task_id']}: {t['title']}")
    r2 = auto({
        "request": f"Implémente : {t['description']}",
        "bundles": ["dev"],
        "provider_ref": PROVIDER,
        "model_ref": MODEL,
        "max_loops": 6,
    }, str(home))
    sig = r2.get("signal", "")
    out = r2.get("stdout", "")[:150]
    if out: log(f"  → {sig}: {out}")
    else: log(f"  → {sig}")
    if sig != "llm_timeout":
        tr.done(t["task_id"])
    time.sleep(3)

# ── 4. Vérification ───────────────────────────────────────────

log("\nVérification...")
workdir = home / "work"
files = list(workdir.iterdir())
log(f"Fichiers dans work/: {[f.name for f in files]}")

hello = workdir / "hello.py"
if hello.exists():
    content = hello.read_text()
    log(f"  ✅ hello.py créé ({len(content)} octets)")
    if "Hello" in content or "print" in content:
        log(f"  ✅ Contenu valide")
    # Tester l'exécution
    r = subprocess.run([sys.executable, str(hello)], capture_output=True, text=True)
    log(f"  Exécution: {r.stdout.strip() or '(vide)'}")
else:
    log("  ❌ hello.py manquant")

# ── 5. Nettoyage ──────────────────────────────────────────────

shutil.rmtree(tmpdir)
print(f"\n{'='*60}")
print(f"TEST E2E COMPLET TERMINÉ")
print(f"Temps: {time.time()-t0:.0f}s")
print(f"{'='*60}")
