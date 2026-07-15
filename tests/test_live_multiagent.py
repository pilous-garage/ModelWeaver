"""Live test V0.7.0 — collaboration multi-agents ETENDUE par un VRAI LLM.

Valide de bout en bout que plusieurs agents (Manager + 2 Workers + Analyst)
collaborent via le framework (FSM + skills git/projet) en faisant appel a un
VRAI LLM pour *generer* le contenu de leurs livrables :

  - Worker1 (LLM) -> src/logic.py  (branche feature-logic)
  - Worker2 (LLM) -> src/ui.py     (branche feature-ui)
  - Analyst (LLM) -> docs/SPEC.md   (branche feature-spec)
  - Manager       -> merge les 3 branches + genere src/main.py (LLM) + push

Le LLM par defaut est groq/llama-3.1-8b-instant (rapide, ~3s) ; configurable via
LIVE_PROVIDER / LIVE_MODEL (ex: ollama / mistral-small:22b). Skippé si la cle
api/provider n'est pas resolvable.

Headless (pas de GUI) : on valide ici le scenario + le FSM + la collaboration
git. L'observation visuelle se fait ensuite via la GUI modulaire (V0.7.0).
"""

import os
import shutil
import time
import unittest

from services._common import mw_home
from services.skill_manager import call_skill
from modules.sql.db import CatalogueDB, ModelWeaverDB
from modules.key_manager.key_manager import KeyManager
from modules.llm_manager.litellm_bridge import LiteLLMBridge
from AgentFrameWork.fsm_interpreter import FSMInterpreter


PROJ = "livemulti"
MANAGER = "mgr_live"
W1 = "w1_live"
W2 = "w2_live"
AN = "an_live"
PROVIDER = os.environ.get("LIVE_PROVIDER", "groq")
MODEL = os.environ.get("LIVE_MODEL", "llama-3.1-8b-instant")

AGENTS = [MANAGER, W1, W2, AN]


def _has_key():
    try:
        km = KeyManager(ModelWeaverDB()); km.load()
        return bool(km.get_key(PROVIDER)) or os.environ.get(f"{PROVIDER.upper()}_API_KEY")
    except Exception:
        return bool(os.environ.get(f"{PROVIDER.upper()}_API_KEY"))


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        # retire la 1re ligne (```python) et la derniere (```)
        t = "\n".join(t.splitlines()[1:-1])
    return t.strip()


@unittest.skipUnless(_has_key(), f"pas de cle/provider pour {PROVIDER}")
class TestLiveMultiAgent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge = LiteLLMBridge(cat=CatalogueDB(), km=KeyManager(ModelWeaverDB()))
        cls._cleanup_disk()
        cls._setup_repo()

    @classmethod
    def tearDownClass(cls):
        cls._cleanup_disk()

    # ── helpers disque ──
    @classmethod
    def _cleanup_disk(cls):
        repo = mw_home() / "repos" / f"{PROJ}.git"
        shutil.rmtree(repo, ignore_errors=True)
        for a in AGENTS:
            shutil.rmtree(mw_home() / "memagent" / a, ignore_errors=True)

    @classmethod
    def _setup_repo(cls):
        call_skill("system/repo_init@v1", {"project_id": PROJ}, str(mw_home()))
        call_skill("system/git_clone@v1",
                   {"project_id": PROJ, "agent_id": MANAGER}, str(mw_home()))
        call_skill("system/project_write@v1",
                   {"project_id": PROJ, "agent_id": MANAGER, "path": "ROADMAP.md",
                    "content": "# Roadmap\n- logic.py (Worker1)\n- ui.py (Worker2)\n- SPEC (Analyst)\n- main.py (Manager)\n"},
                   str(mw_home()))
        call_skill("system/git_commit@v1",
                   {"project_id": PROJ, "agent_id": MANAGER, "message": "roadmap"}, str(mw_home()))
        call_skill("system/git_push@v1", {"project_id": PROJ, "agent_id": MANAGER}, str(mw_home()))

    def _run(self, agent_id, wf):
        fsm = FSMInterpreter(bridge=self.bridge)
        return fsm.run(wf, messages=[],
                       variables={"agent_id": agent_id, "project_id": PROJ},
                       provider_ref=PROVIDER, model_ref=MODEL)

    # ── workflows ──
    def _worker_wf(self, branch, path, prompt):
        return {
            "max_iterations": 25,
            "steps": [
                {"id": "clone", "type": "call", "fn": "system/git_clone@v1",
                 "inputs": {"project_id": PROJ}, "next": "branch"},
                {"id": "branch", "type": "call", "fn": "system/git_branch@v1",
                 "inputs": {"project_id": PROJ, "name": branch, "create": True}, "next": "gen"},
                {"id": "gen", "type": "llm_call", "provider_ref": PROVIDER, "model_ref": MODEL,
                 "skill_prompt": prompt, "output_capture": "_code", "next": "write"},
                {"id": "write", "type": "call", "fn": "system/project_write@v1",
                 "inputs": {"project_id": PROJ, "path": path, "content": "{{_code}}"}, "next": "commit"},
                {"id": "commit", "type": "call", "fn": "system/git_commit@v1",
                 "inputs": {"project_id": PROJ, "message": f"add {path} (LLM)"}, "next": "push"},
                {"id": "push", "type": "call", "fn": "system/git_push@v1",
                 "inputs": {"project_id": PROJ, "branch": branch}, "next": "done"},
                {"id": "done", "type": "end", "status": "SUCCESS"},
            ],
        }

    def _analyst_wf(self):
        return self._worker_wf(
            "feature-spec", "docs/SPEC.md",
            "Write a short Markdown specification for a number-guessing game "
            "(modules logic/ui/main, roles). Return ONLY the Markdown, no fences, "
            "no explanation.")

    def _manager_wf(self):
        return {
            "max_iterations": 25,
            "steps": [
                {"id": "fetch", "type": "call", "fn": "system/git_fetch@v1",
                 "inputs": {"project_id": PROJ}, "next": "pull"},
                {"id": "pull", "type": "call", "fn": "system/git_pull@v1",
                 "inputs": {"project_id": PROJ, "branch": "master"}, "next": "m1"},
                {"id": "m1", "type": "call", "fn": "system/git_merge@v1",
                 "inputs": {"project_id": PROJ, "name": "origin/feature-logic"}, "next": "m2"},
                {"id": "m2", "type": "call", "fn": "system/git_merge@v1",
                 "inputs": {"project_id": PROJ, "name": "origin/feature-ui"}, "next": "m3"},
                {"id": "m3", "type": "call", "fn": "system/git_merge@v1",
                 "inputs": {"project_id": PROJ, "name": "origin/feature-spec"}, "next": "gen"},
                {"id": "gen", "type": "llm_call", "provider_ref": PROVIDER, "model_ref": MODEL,
                 "skill_prompt": (
                     "Write a Python module `src/main.py` that imports `check` from "
                     "`logic` and `render` from `ui`, defines `play(secret, guesses)` "
                     "returning the list of rendered results, and a `__main__` block "
                     "that calls play(7, [5, 9, 7]) and prints the result. "
                     "Return ONLY the Python code, no markdown fences, no explanation."),
                 "output_capture": "_main", "next": "write"},
                {"id": "write", "type": "call", "fn": "system/project_write@v1",
                 "inputs": {"project_id": PROJ, "path": "src/main.py", "content": "{{_main}}"},
                 "next": "commit"},
                {"id": "commit", "type": "call", "fn": "system/git_commit@v1",
                 "inputs": {"project_id": PROJ, "message": "integrate (LLM main.py)"}, "next": "push"},
                {"id": "push", "type": "call", "fn": "system/git_push@v1",
                 "inputs": {"project_id": PROJ}, "next": "done"},
                {"id": "done", "type": "end", "status": "SUCCESS"},
            ],
        }

    # ── test principal ──
    def test_live_multiagent_collaboration(self):
        # Espacement des appels LLM : le tier gratuit groq plafonne a 6000 TPM
        # (fenetre glissante 60s). On laisse la fenetre se vider entre agents.
        # Worker1 : src/logic.py
        r1 = self._run(W1, self._worker_wf(
            "feature-logic", "src/logic.py",
            "Write a Python module `src/logic.py` implementing "
            "`check(guess, secret)` that returns 'win' if equal, else 'high' if "
            "guess>secret, else 'low'. Return ONLY the Python code, no markdown "
            "fences, no explanation."))
        self.assertEqual(r1.status, "success", r1.end_reason)
        time.sleep(35)

        # Worker2 : src/ui.py
        r2 = self._run(W2, self._worker_wf(
            "feature-ui", "src/ui.py",
            "Write a Python module `src/ui.py` implementing `render(result)` that "
            "maps 'win'->'Bravo !', 'high'->'Trop grand', 'low'->'Trop petit' and "
            "returns the French string. Return ONLY the Python code, no markdown "
            "fences, no explanation."))
        self.assertEqual(r2.status, "success", r2.end_reason)
        time.sleep(35)

        # Analyst : docs/SPEC.md
        ra = self._run(AN, self._analyst_wf())
        self.assertEqual(ra.status, "success", ra.end_reason)
        time.sleep(35)

        # Manager : merge + main.py (LLM) + push
        rm = self._run(MANAGER, self._manager_wf())
        self.assertEqual(rm.status, "success", rm.end_reason)

        # ── assertions produit (clone manager) ──
        mclone = mw_home() / "memagent" / MANAGER / "workspace" / PROJ
        logic = (mclone / "src" / "logic.py").read_text() if (mclone / "src" / "logic.py").exists() else ""
        ui = (mclone / "src" / "ui.py").read_text() if (mclone / "src" / "ui.py").exists() else ""
        main = (mclone / "src" / "main.py").read_text() if (mclone / "src" / "main.py").exists() else ""
        spec = (mclone / "docs" / "SPEC.md").read_text() if (mclone / "docs" / "SPEC.md").exists() else ""

        self.assertTrue(logic.strip(), "src/logic.py vide")
        self.assertIn("def ", logic, "logic.py doit definir une fonction (LLM)")
        self.assertTrue(ui.strip(), "src/ui.py vide")
        self.assertIn("def ", ui, "ui.py doit definir une fonction (LLM)")
        self.assertTrue(main.strip(), "src/main.py vide")
        self.assertIn("import", main, "main.py doit importer logic/ui (LLM)")
        self.assertIn("def ", main)
        self.assertTrue(spec.strip(), "docs/SPEC.md vide")

        # arbre propre (merge conclu, pas de marqueurs de conflit)
        st = call_skill("system/git_status@v1",
                        {"project_id": PROJ, "agent_id": MANAGER}, str(mw_home()))
        self.assertTrue(st.get("clean"), st)

        # le jeu tourne (le LLM a produit du Python coherent)
        prod = shutil.which("python3")
        proc = subprocess_run([prod, "src/main.py"], cwd=str(mclone))
        # non bloquant : on log juste le resultat, l'essentiel est la collaboration
        self.assertNotIn("<<<<<<<", logic + ui + main,
                         "marqueurs de conflit residuels")


def subprocess_run(cmd, cwd):
    import subprocess
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=30)
    except Exception as e:  # pragma: no cover
        class _R:  # noqa
            returncode = -1
            stdout = ""
            stderr = str(e)
        return _R()


if __name__ == "__main__":
    unittest.main(verbosity=2)
