from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time

import pytest

from modules.sql.modelweaver_repo import (
    Agent,
    Database,
    Model,
    Project,
    Run,
    Task,
)


@pytest.fixture()
def db_path() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        yield os.path.join(tmp, "modelweaver.db")


@pytest.fixture()
def db(db_path: str) -> Database:
    database = Database(path=db_path)
    database.connect()
    yield database
    database.close()


def test_connect_applies_migrations(db: Database, db_path: str) -> None:
    assert os.path.exists(db_path)
    conn = sqlite3.connect(db_path)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
    finally:
        conn.close()

    expected = {"agents", "projects", "tasks", "runs", "models", "advisory_locks"}
    assert expected.issubset(set(tables))


def test_wal_mode_enabled(db: Database, db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert journal.lower() == "wal"


def test_upsert_and_get_agent(db: Database) -> None:
    agent = Agent(id="agent-1", name="Alice", role="coder_senior", created_at="2025-01-01T00:00:00Z")
    db.upsert_agent(agent)

    loaded = db.get_agent("agent-1")
    assert loaded == agent

    updated = Agent(id="agent-1", name="Alice Updated", role="reviewer", created_at="2025-01-01T00:00:00Z")
    db.upsert_agent(updated)
    assert db.get_agent("agent-1") == updated


def test_list_agents(db: Database) -> None:
    db.upsert_agent(Agent(id="a1", name="A", role="x", created_at="2025-01-01T00:00:00Z"))
    db.upsert_agent(Agent(id="a2", name="B", role="y", created_at="2025-01-02T00:00:00Z"))

    agents = db.list_agents()
    assert [a.id for a in agents] == ["a1", "a2"]


def test_project_lifecycle(db: Database) -> None:
    project = Project(id="p1", name="Project 1", status="active", created_at="2025-01-01T00:00:00Z")
    db.upsert_project(project)

    assert db.get_project("p1") == project
    assert db.list_projects() == [project]


def test_task_crud(db: Database) -> None:
    db.upsert_project(Project(id="p1", name="P", status="active", created_at="2025-01-01T00:00:00Z"))
    task_id = db.create_task(
        Task(id=0, project_id="p1", title="T1", status="pending", priority=1, role_required="coder", created_at="2025-01-01T00:00:00Z")
    )
    assert task_id == 1

    task = db.get_task(1)
    assert task is not None
    assert task.title == "T1"

    db.update_task_status(1, "running")
    assert db.get_task(1).status == "running"

    assert db.list_tasks("p1") == [task]
    assert len(db.list_tasks()) == 1


def test_run_lifecycle(db: Database) -> None:
    db.upsert_project(Project(id="p1", name="P", status="active", created_at="2025-01-01T00:00:00Z"))
    run = Run(id="r1", project_id="p1", agent_id="a1", status="started", started_at="2025-01-01T00:00:00Z", finished_at=None, created_at="2025-01-01T00:00:00Z")
    db.create_run(run)

    assert db.get_run("r1") == run

    db.update_run("r1", "finished", finished_at="2025-01-01T01:00:00Z")
    assert db.get_run("r1").status == "finished"
    assert db.get_run("r1").finished_at == "2025-01-01T01:00:00Z"

    assert db.list_runs("p1") == [db.get_run("r1")]


def test_model_lifecycle(db: Database) -> None:
    db.upsert_project(Project(id="p1", name="P", status="active", created_at="2025-01-01T00:00:00Z"))
    model = Model(id="m1", project_id="p1", name="M", version="1.0", path="/tmp/m.pt", created_at="2025-01-01T00:00:00Z")
    db.create_model(model)

    assert db.get_model("m1") == model
    assert db.list_models("p1") == [model]


def test_advisory_lock_same_owner(db: Database) -> None:
    with db.advisory_lock("resource-1", "owner-1") as acquired:
        assert acquired is True

        with db.advisory_lock("resource-1", "owner-1") as acquired_again:
            assert acquired_again is True


def test_advisory_lock_different_owner(db: Database) -> None:
    with db.advisory_lock("resource-1", "owner-1"):
        with db.advisory_lock("resource-1", "owner-2") as acquired:
            assert acquired is False


def test_transaction_rollback_on_error(db: Database) -> None:
    db.upsert_project(Project(id="p1", name="P", status="active", created_at="2025-01-01T00:00:00Z"))

    with pytest.raises(RuntimeError):
        with db.transaction():
            db.execute("INSERT INTO projects(id, name, status, created_at) VALUES (?, ?, ?, ?)", ("p1", "P2", "active", "2025-01-01T00:00:00Z"))
            raise RuntimeError("boom")

    assert db.get_project("p1").name == "P"


def test_thread_safety(db_path: str) -> None:
    database = Database(path=db_path)
    database.connect()
    try:
        errors = []

        def worker(thread_id: int) -> None:
            try:
                for i in range(50):
                    database.upsert_agent(
                        Agent(
                            id=f"agent-{thread_id}-{i}",
                            name=f"Agent {thread_id}-{i}",
                            role="worker",
                            created_at="2025-01-01T00:00:00Z",
                        )
                    )
                    database.query("SELECT 1")
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors
        assert len(database.list_agents()) == 400
    finally:
        database.close()


def test_advisory_lock_released_after_context(db: Database) -> None:
    with db.advisory_lock("resource-1", "owner-1"):
        pass

    with db.advisory_lock("resource-1", "owner-2") as acquired:
        assert acquired is True


def test_query_without_connect_raises(db: Database) -> None:
    db.close()
    with pytest.raises(RuntimeError):
        db.query("SELECT 1")
