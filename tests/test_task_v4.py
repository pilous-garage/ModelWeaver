"""test_task_v4 — smoke du domaine task (fusion tasks+sub_tasks, ask_new_task).

Lancement :
    python3 -m pytest tests/test_task_v4.py -q

Couvre :
  - entry_request (racine immutable) + tasks auto-référentielles (issue-level)
  - découpe → sub_tasks + dépendances (waiting_deps) ; release quand parent done
  - attribution greedy (pick par type+priorité, claim attributed) ; reprise
  - enfant bloqué tant qu'un parent n'est pas fait
  - file ask_new_task + pending
  - garde token (WriteDenied si token faux)
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest

MW_HOME = Path(tempfile.mkdtemp()) / "mw"
os.environ["MODELWEAVER_HOME"] = str(MW_HOME)

from modules.sqlite.base import WriteDenied  # noqa: E402
from modules.sqlite import task as T  # noqa: E402
from modules.sqlite.task import write as tw, read as tr  # noqa: E402


@pytest.fixture(scope="module")
def domains():
    shutil.rmtree(MW_HOME, ignore_errors=True)
    lw = T.get_writer(T.WRITE_TASK_TOKEN)
    rw = T.db_ro()
    yield {"w": lw, "r": rw}
    lw.close()
    rw.close()


def test_entry_and_decoupe_release_assign(domains):
    lw, rw = domains["w"], domains["r"]
    er = tw.create_entry_request(lw, "ws", "h4", "Build X",
                                 token=T.WRITE_TASK_TOKEN)
    root = tw.create_task(lw, "ws", er["entry_request_id"], None,
                          title="root", token=T.WRITE_TASK_TOKEN)
    dec = tw.create_decoupe(lw, "ws", er["entry_request_id"], root["task_id"], [
        {"sub_task_type": "analysis", "title": "A"},
        {"sub_task_type": "coding", "title": "C", "tag": "done/ok"},
    ], token=T.WRITE_TASK_TOKEN)
    children = dec["children"]
    assert len(children) == 2
    # bloquantes tant que le parent n'est pas done
    assert tr.get_task(rw, children[0])["status"] == "waiting_deps"
    # parent done (tag ok) → libère les enfants
    tw.set_task_status(lw, root["task_id"], "done", tag="done/ok",
                       token=T.WRITE_TASK_TOKEN)
    assert tw.release_waiting(lw, "ws", token=T.WRITE_TASK_TOKEN) == 2
    # attribution greedy coding → la sub C
    r = tw.assign_task(lw, "ws", 1, types=[{"type": "coding"}],
                       token=T.WRITE_TASK_TOKEN)
    assert r and r["status"] == "attributed" and r["assigned_to"] == "1"
    got = tr.get_task(rw, r["task_id"])
    assert got["status"] == "attributed" and got["sub_task_type"] == "coding"
    # enfant d'un frère non terminé → reste waiting_deps
    dec2 = tw.create_decoupe(lw, "ws", er["entry_request_id"], children[0],
                             [{"sub_task_type": "coding", "title": "D"}],
                             token=T.WRITE_TASK_TOKEN)
    assert tr.get_task(rw, dec2["children"][0])["status"] == "waiting_deps"


def test_ask_new_task_pending(domains):
    lw, rw = domains["w"], domains["r"]
    aid = tw.ask_new_task(lw, "ws", 2, [{"type": "testing", "level_max": "hard"}],
                          token=T.WRITE_TASK_TOKEN)
    pends = [q["id"] for q in tr.pending_asks(rw, "ws")]
    assert aid in pends
    tw.set_ask_served(lw, aid, token=T.WRITE_TASK_TOKEN)
    assert tr.pending_asks(rw, "ws") == []


def test_token_guard(domains):
    lw = domains["w"]
    with pytest.raises(WriteDenied):
        tw.create_entry_request(lw, "ws", "x", "X", token="mauvais")
