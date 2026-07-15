"""Tests V0.6.24 - resolution de conflits git.

Skills ajoutes :
  - git_add            : stage un fichier (ou tout)
  - git_merge(strategy=ours|theirs) : resolution auto d'un conflit
  - git_merge(abort=true)           : annule un merge en cours
  - git_resolve_conflict(path, side): resout un fichier en conflit (ours/theirs)
"""

import shutil
import unittest

from services._common import mw_home
from services.skill_manager import call_skill


AGENT = "fsmresolve"
PROJ = "fsmproj24"
WS = str(mw_home() / "memagent" / AGENT)


def _make_conflict():
    repo = mw_home() / "repos" / f"{PROJ}.git"
    clone = mw_home() / "memagent" / AGENT / "workspace" / PROJ
    shutil.rmtree(repo, ignore_errors=True)
    shutil.rmtree(clone, ignore_errors=True)
    call_skill("system/repo_init@v1", {"project_id": PROJ}, WS)
    call_skill("system/git_clone@v1", {"project_id": PROJ, "agent_id": AGENT}, WS)
    # base commun sur master
    call_skill("system/project_write@v1",
               {"project_id": PROJ, "agent_id": AGENT, "path": "a.txt",
                "content": "base\n"}, WS)
    call_skill("system/git_commit@v1",
               {"project_id": PROJ, "agent_id": AGENT, "message": "base"}, WS)
    # feat diverge du base
    call_skill("system/git_branch@v1",
               {"project_id": PROJ, "agent_id": AGENT, "name": "feat",
                "create": True}, WS)
    call_skill("system/project_write@v1",
               {"project_id": PROJ, "agent_id": AGENT, "path": "a.txt",
                "content": "feat edit\n"}, WS)
    call_skill("system/git_commit@v1",
               {"project_id": PROJ, "agent_id": AGENT, "message": "feat"}, WS)
    # master diverge aussi du base -> conflit 3-way reel
    call_skill("system/git_checkout@v1",
               {"project_id": PROJ, "agent_id": AGENT, "name": "master"}, WS)
    call_skill("system/project_write@v1",
               {"project_id": PROJ, "agent_id": AGENT, "path": "a.txt",
                "content": "master edit\n"}, WS)
    call_skill("system/git_commit@v1",
               {"project_id": PROJ, "agent_id": AGENT, "message": "master"}, WS)


def _read(path):
    r = call_skill("system/project_read@v1",
                   {"project_id": PROJ, "agent_id": AGENT, "path": path}, WS)
    return r.get("content", "")


class TestGitAdd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _make_conflict()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(mw_home() / "repos" / f"{PROJ}.git", ignore_errors=True)
        shutil.rmtree(mw_home() / "memagent" / AGENT, ignore_errors=True)

    def test_git_add_single_file(self):
        call_skill("system/project_write@v1",
                   {"project_id": PROJ, "agent_id": AGENT, "path": "x.txt",
                    "content": "hello\n"}, WS)
        r = call_skill("system/git_add@v1",
                       {"project_id": PROJ, "agent_id": AGENT, "path": "x.txt"}, WS)
        self.assertTrue(r["ok"], r)
        commit = call_skill("system/git_commit@v1",
                            {"project_id": PROJ, "agent_id": AGENT,
                             "message": "add x"}, WS)
        self.assertTrue(commit["ok"], commit)


class TestConflictResolution(unittest.TestCase):
    def setUp(self):
        _make_conflict()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(mw_home() / "repos" / f"{PROJ}.git", ignore_errors=True)
        shutil.rmtree(mw_home() / "memagent" / AGENT, ignore_errors=True)

    def test_merge_strategy_theirs(self):
        r = call_skill("system/git_merge@v1",
                       {"project_id": PROJ, "agent_id": AGENT, "name": "feat",
                        "strategy": "theirs"}, WS)
        self.assertTrue(r["ok"], r)
        self.assertEqual(_read("a.txt"), "feat edit\n")

    def test_merge_strategy_ours(self):
        r = call_skill("system/git_merge@v1",
                       {"project_id": PROJ, "agent_id": AGENT, "name": "feat",
                        "strategy": "ours"}, WS)
        self.assertTrue(r["ok"], r)
        self.assertEqual(_read("a.txt"), "master edit\n")

    def test_resolve_conflict_per_file(self):
        bad = call_skill("system/git_merge@v1",
                         {"project_id": PROJ, "agent_id": AGENT, "name": "feat"}, WS)
        self.assertFalse(bad["ok"])
        self.assertTrue(bad.get("conflict"))
        rc = call_skill("system/git_resolve_conflict@v1",
                        {"project_id": PROJ, "agent_id": AGENT, "path": "a.txt",
                         "side": "ours"}, WS)
        self.assertTrue(rc["ok"], rc)
        self.assertEqual(_read("a.txt"), "master edit\n")
        commit = call_skill("system/git_commit@v1",
                            {"project_id": PROJ, "agent_id": AGENT,
                             "message": "resolve ours"}, WS)
        self.assertTrue(commit["ok"], commit)

    def test_merge_abort(self):
        bad = call_skill("system/git_merge@v1",
                         {"project_id": PROJ, "agent_id": AGENT, "name": "feat"}, WS)
        self.assertFalse(bad["ok"])
        ab = call_skill("system/git_merge@v1",
                        {"project_id": PROJ, "agent_id": AGENT, "abort": True}, WS)
        self.assertTrue(ab["ok"], ab)
        st = call_skill("system/git_status@v1",
                        {"project_id": PROJ, "agent_id": AGENT}, WS)
        self.assertTrue(st.get("clean"), st)

    def test_git_merge_lists_conflicts(self):
        bad = call_skill("system/git_merge@v1",
                         {"project_id": PROJ, "agent_id": AGENT, "name": "feat"}, WS)
        self.assertFalse(bad["ok"])
        self.assertIn("a.txt", bad.get("conflicts", []))

    def test_git_status_lists_conflicts(self):
        call_skill("system/git_merge@v1",
                   {"project_id": PROJ, "agent_id": AGENT, "name": "feat"}, WS)
        st = call_skill("system/git_status@v1",
                        {"project_id": PROJ, "agent_id": AGENT}, WS)
        self.assertIn("a.txt", st.get("conflicts", []))

    def test_resolve_conflict_all(self):
        call_skill("system/git_merge@v1",
                   {"project_id": PROJ, "agent_id": AGENT, "name": "feat"}, WS)
        rc = call_skill("system/git_resolve_conflict@v1",
                        {"project_id": PROJ, "agent_id": AGENT, "path": "all",
                         "side": "ours"}, WS)
        self.assertTrue(rc["ok"], rc)
        self.assertIn("a.txt", rc.get("resolved", []))
        st = call_skill("system/git_status@v1",
                        {"project_id": PROJ, "agent_id": AGENT}, WS)
        self.assertTrue(st.get("clean"), st)


if __name__ == "__main__":
    unittest.main(verbosity=2)
