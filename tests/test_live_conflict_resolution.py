"""Live test V0.6.24 — resolution d'un VRAI conflit de merge par un VRAI LLM,
via le branchement on_error du FSM (V0.6.23).

Scenario :
  1. Setup deterministe (skills) : repo bare + clone, deux branches editent
     le meme fichier => conflit 3-way reel.
  2. FSM avec un vrai LLM (groq/llama-3.1-8b-instant par defaut) :
       - git_merge feat  -> CONFLIT -> on_error
       - project_read du fichier en conflit (capture)
       - etape model : le LLM produit le contenu fusionne
       - project_write du contenu resolu
       - git_commit conclut le merge
  3. Assertions : le FSM finit en success et le fichier ne contient plus de
     marqueurs de conflit.

Le LLM est configurable via LIVE_PROVIDER / LIVE_MODEL (ex: ollama /
mistral-small:22b). Skippé si la cle api/provider n'est pas resolvable.
"""

import os
import shutil
import unittest

from services._common import mw_home
from services.skill_manager import call_skill
from modules.sql.db import CatalogueDB, ModelWeaverDB
from modules.key_manager.key_manager import KeyManager
from modules.llm_manager.litellm_bridge import LiteLLMBridge
from AgentFrameWork.fsm_interpreter import FSMInterpreter


AGENT = "liveagent"
PROJ = "liveproj"
WS = str(mw_home() / "memagent" / AGENT)
PROVIDER = os.environ.get("LIVE_PROVIDER", "groq")
MODEL = os.environ.get("LIVE_MODEL", "llama-3.1-8b-instant")


def _make_conflict():
    repo = mw_home() / "repos" / f"{PROJ}.git"
    clone = mw_home() / "memagent" / AGENT / "workspace" / PROJ
    shutil.rmtree(repo, ignore_errors=True)
    shutil.rmtree(clone, ignore_errors=True)
    call_skill("system/repo_init@v1", {"project_id": PROJ}, WS)
    call_skill("system/git_clone@v1", {"project_id": PROJ, "agent_id": AGENT}, WS)
    # base commun
    call_skill("system/project_write@v1",
               {"project_id": PROJ, "agent_id": AGENT, "path": "a.txt",
                "content": "ligne base\n"}, WS)
    call_skill("system/git_commit@v1",
               {"project_id": PROJ, "agent_id": AGENT, "message": "base"}, WS)
    # feat diverge
    call_skill("system/git_branch@v1",
               {"project_id": PROJ, "agent_id": AGENT, "name": "feat",
                "create": True}, WS)
    call_skill("system/project_write@v1",
               {"project_id": PROJ, "agent_id": AGENT, "path": "a.txt",
                "content": "ligne apportee par feat\n"}, WS)
    call_skill("system/git_commit@v1",
               {"project_id": PROJ, "agent_id": AGENT, "message": "feat"}, WS)
    # master diverge aussi => conflit reel
    call_skill("system/git_checkout@v1",
               {"project_id": PROJ, "agent_id": AGENT, "name": "master"}, WS)
    call_skill("system/project_write@v1",
               {"project_id": PROJ, "agent_id": AGENT, "path": "a.txt",
                "content": "ligne apportee par master\n"}, WS)
    call_skill("system/git_commit@v1",
               {"project_id": PROJ, "agent_id": AGENT, "message": "master"}, WS)


def _has_key():
    try:
        km = KeyManager(ModelWeaverDB()); km.load()
        return bool(km.get_key(PROVIDER)) or os.environ.get(f"{PROVIDER.upper()}_API_KEY")
    except Exception:
        return bool(os.environ.get(f"{PROVIDER.upper()}_API_KEY"))


@unittest.skipUnless(_has_key(), f"pas de cle/provider pour {PROVIDER}")
class TestLiveConflictResolution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _make_conflict()
        km = KeyManager(ModelWeaverDB()); km.load()
        cls.bridge = LiteLLMBridge(cat=CatalogueDB(), km=km)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(mw_home() / "repos" / f"{PROJ}.git", ignore_errors=True)
        shutil.rmtree(mw_home() / "memagent" / AGENT, ignore_errors=True)

    def test_live_on_error_resolves_conflict(self):
        workflow = {
            "max_iterations": 20,
            "steps": [
                {"id": "merge", "type": "call",
                 "fn": "system/git_merge@v1",
                 "inputs": {"project_id": PROJ, "name": "feat"},
                 "capture": {"conflict": "_merge_conflict"},
                 "on_error": "read_conflict"},
                {"id": "read_conflict", "type": "call",
                 "fn": "system/project_read@v1",
                 "inputs": {"project_id": PROJ, "path": "a.txt"},
                 "capture": {"content": "_conflict_text"},
                 "next": "llm_resolve"},
                {"id": "llm_resolve", "type": "llm_call",
                 "provider_ref": PROVIDER, "model_ref": MODEL,
                 "skill_prompt": (
                     "Un merge git est en conflit sur le fichier a.txt. "
                     "Voici son contenu actuel avec les marqueurs de conflit :\n"
                     "<<<\n{{_conflict_text}}\n>>>\n"
                     "Produis UNIQUEMENT le contenu final fusionne de ce fichier, "
                     "sans explication et sans bloc de code markdown. Combine "
                     "proprement les deux cotes."),
                 "output_capture": "_resolved",
                 "next": "write"},
                {"id": "write", "type": "call",
                 "fn": "system/project_write@v1",
                 "inputs": {"project_id": PROJ, "path": "a.txt",
                            "content": "{{_resolved}}"},
                 "next": "commit"},
                {"id": "commit", "type": "call",
                 "fn": "system/git_commit@v1",
                 "inputs": {"project_id": PROJ, "message": "resolve via LLM"},
                 "next": "done"},
                {"id": "done", "type": "end", "status": "SUCCESS"},
            ],
        }
        fsm = FSMInterpreter(bridge=self.bridge)
        res = fsm.run(workflow, messages=[],
                      variables={"agent_id": AGENT, "project_id": PROJ},
                      provider_ref=PROVIDER, model_ref=MODEL)

        self.assertEqual(res.status, "success", res.end_reason)
        self.assertTrue(res.variables.get("_merge_conflict"),
                         "le merge initial doit avoir signale un conflit "
                         "(branchement on_error)")
        # pas de marqueurs de conflit residuels
        final = call_skill("system/project_read@v1",
                           {"project_id": PROJ, "agent_id": AGENT, "path": "a.txt"}, WS)
        content = final.get("content", "")
        self.assertNotIn("<<<<<<<", content)
        self.assertNotIn(">>>>>>>", content)
        # le merge est conclu (arbre propre)
        st = call_skill("system/git_status@v1",
                        {"project_id": PROJ, "agent_id": AGENT}, WS)
        self.assertTrue(st.get("clean"), st)


if __name__ == "__main__":
    unittest.main(verbosity=2)
