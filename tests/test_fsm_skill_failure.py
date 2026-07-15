"""Tests V0.6.23 — les échecs de skills (notamment git : merge/commit/push en
erreur) sont désormais détectés et remontés au FSM au lieu de passer inaperçus.

Deux niveaux :
  A. skill_manager : un git merge en conflit renvoie ok=False + conflict=True.
  B. fsm_interpreter._step_call : un échec de skill =>
       - pose _last_call_ok / _last_call_error (exploitable par switch/model),
       - branche vers on_error si défini, sinon arrête le FSM en status=failed.
"""

import os
import shutil
import unittest

from services._common import mw_home
from services.skill_manager import call_skill
from AgentFrameWork.fsm_interpreter import FSMInterpreter


AGENT = "fsmfail"
PROJ = "fsmproj"
WS = str(mw_home() / "memagent" / AGENT)


def _setup_repo():
    repo = mw_home() / "repos" / f"{PROJ}.git"
    clone = mw_home() / "memagent" / AGENT / "workspace" / PROJ
    shutil.rmtree(repo, ignore_errors=True)
    shutil.rmtree(clone, ignore_errors=True)
    call_skill("system/repo_init@v1", {"project_id": PROJ}, WS)
    call_skill("system/git_clone@v1", {"project_id": PROJ, "agent_id": AGENT}, WS)


def _teardown_repo():
    shutil.rmtree(mw_home() / "repos" / f"{PROJ}.git", ignore_errors=True)
    shutil.rmtree(mw_home() / "memagent" / AGENT, ignore_errors=True)


class TestGitMergeConflict(unittest.TestCase):
    """Le skill git_merge signale explicitement un conflit."""

    @classmethod
    def setUpClass(cls):
        _setup_repo()
        # master : a.txt v1
        call_skill("system/project_write@v1",
                   {"project_id": PROJ, "agent_id": AGENT, "path": "a.txt",
                    "content": "base\n"}, WS)
        call_skill("system/git_commit@v1",
                   {"project_id": PROJ, "agent_id": AGENT, "message": "base"}, WS)
        # branche feat : modifie a.txt
        call_skill("system/git_branch@v1",
                   {"project_id": PROJ, "agent_id": AGENT, "name": "feat",
                    "create": True}, WS)
        call_skill("system/project_write@v1",
                   {"project_id": PROJ, "agent_id": AGENT, "path": "a.txt",
                    "content": "feat edit\n"}, WS)
        call_skill("system/git_commit@v1",
                   {"project_id": PROJ, "agent_id": AGENT, "message": "feat"}, WS)
        # retour master + modif concurrente
        call_skill("system/git_checkout@v1",
                   {"project_id": PROJ, "agent_id": AGENT, "name": "master"}, WS)
        call_skill("system/project_write@v1",
                   {"project_id": PROJ, "agent_id": AGENT, "path": "a.txt",
                    "content": "master edit\n"}, WS)
        call_skill("system/git_commit@v1",
                   {"project_id": PROJ, "agent_id": AGENT, "message": "master"}, WS)

    @classmethod
    def tearDownClass(cls):
        _teardown_repo()

    def test_merge_conflict_detected(self):
        r = call_skill("system/git_merge@v1",
                       {"project_id": PROJ, "agent_id": AGENT, "name": "feat"}, WS)
        self.assertFalse(r["ok"], f"merge conflit doit échouer: {r}")
        self.assertTrue(r.get("conflict"), f"conflit non signalé: {r}")
        self.assertIn("CONFLICT",
                       r.get("stderr", "") + r.get("stdout", "") + r.get("error", ""))


class TestFsmSurfacesFailure(unittest.TestCase):
    """_step_call remonte un échec de skill au FSM."""

    @classmethod
    def setUpClass(cls):
        _setup_repo()

    @classmethod
    def tearDownClass(cls):
        _teardown_repo()

    def _run(self, workflow):
        fsm = FSMInterpreter()
        return fsm.run(workflow, messages=[], variables={"agent_id": AGENT})

    def test_failure_aborts_fsm(self):
        wf = {
            "max_iterations": 10,
            "steps": [
                {"id": "s1", "type": "call",
                 "fn": "system/git_checkout@v1",
                 "inputs": {"project_id": PROJ, "name": "branche_inexistante"}},
                {"id": "s2", "type": "end", "status": "SUCCESS"},
            ],
        }
        res = self._run(wf)
        self.assertEqual(res.status, "failed", res.end_reason)
        self.assertFalse(res.variables.get("_last_call_ok"))
        self.assertTrue(res.variables.get("_last_call_error"))

    def test_on_error_branch_continues(self):
        wf = {
            "max_iterations": 10,
            "steps": [
                {"id": "s1", "type": "call", "fn": "system/git_checkout@v1",
                 "inputs": {"project_id": PROJ, "name": "branche_inexistante"},
                 "on_error": "err"},
                {"id": "err", "type": "set_variable",
                 "name": "_handled", "value": "yes", "next": "done"},
                {"id": "done", "type": "end", "status": "SUCCESS"},
            ],
        }
        res = self._run(wf)
        self.assertEqual(res.status, "success", res.end_reason)
        self.assertEqual(res.variables.get("_handled"), "yes")
        self.assertFalse(res.variables.get("_last_call_ok"))
        self.assertTrue(res.variables.get("_last_call_error"))

    def test_success_keeps_running(self):
        wf = {
            "max_iterations": 10,
            "steps": [
                {"id": "s1", "type": "call", "fn": "system/git_status@v1",
                 "inputs": {"project_id": PROJ}, "next": "s2"},
                {"id": "s2", "type": "end", "status": "SUCCESS"},
            ],
        }
        res = self._run(wf)
        self.assertEqual(res.status, "success", res.end_reason)
        self.assertTrue(res.variables.get("_last_call_ok"))
        self.assertNotIn("_last_call_error", res.variables)

    def test_agent_id_spoof_forced(self):
        # agent courant (AGENT) possede x.txt ; un 'call' tente un agent_id
        # intrus -> le FSM doit forcer l'identite courante et lire x.txt.
        call_skill("system/project_write@v1",
                   {"project_id": PROJ, "agent_id": AGENT, "path": "x.txt",
                    "content": "X"}, WS)
        wf = {
            "max_iterations": 10,
            "steps": [
                {"id": "s1", "type": "call", "fn": "system/project_read@v1",
                 "inputs": {"project_id": PROJ, "agent_id": "agent_intru",
                            "path": "x.txt"},
                 "capture": {"content": "_read"}, "next": "s2"},
                {"id": "s2", "type": "end", "status": "SUCCESS"},
            ],
        }
        res = self._run(wf)
        self.assertEqual(res.status, "success", res.end_reason)
        self.assertEqual(res.variables.get("_read"), "X",
                         "l'agent_id intrus n'a pas ete ignore (spoof possible)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
