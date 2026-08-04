"""Test : la FSM vérifie le flag de pause global avant chaque step.

Comportement attendu :
- pause : l'agent en cours finit son step courant puis se met en attente
  (pas de nouveau step) ;
- resume : reprend au step suivant.
- mécanisme de notification (event/flag) pour réveiller les agents en attente.
"""

import json
import threading
import time

import pytest

from AgentFrameWork.fsm_interpreter import FSMInterpreter, FSMResult, AgentAbort
from modules.control.pause_flag import get_pause_store


class DummyDeltaStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._i = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self._i >= len(self._chunks):
            raise StopIteration
        c = self._chunks[self._i]
        self._i += 1
        return c


class DummyBridge:
    def __init__(self, response="ok", stream_chunks=None):
        self.response = response
        self.stream_chunks = stream_chunks or ["hello", " world"]
        self.calls = []

    def chat(self, provider_ref, model_ref, messages, temperature=0.7, max_tokens=4096, agent_id=None, **kwargs):
        self.calls.append(("chat", messages))
        time.sleep(0.05)

        class R:
            content = self.response
            provider_used = provider_ref
            model_used = model_ref
            fallbacks = 0
            tool_calls = None
            usage = {"total_tokens": 10}
            budget = {}

        return R()

    def chat_stream(self, provider_ref, model_ref, messages, temperature=0.7, max_tokens=4096, agent_id=None, **kwargs):
        self.calls.append(("chat_stream", messages))
        return DummyDeltaStream(self.stream_chunks)


def test_fsm_checks_pause_before_each_step():
    fsm = FSMInterpreter(bridge=DummyBridge())
    workflow = {
        "steps": [
            {"id": "s1", "type": "llm_call", "next": "s2"},
            {"id": "s2", "type": "llm_call", "next": "s3"},
            {"id": "s3", "type": "llm_call", "next": "end"},
            {"id": "end", "type": "end", "status": "SUCCESS"},
        ]
    }
    messages = [{"role": "user", "content": "hi"}]
    variables = {
        "project_id": "proj_x",
        "team_name": "team_a",
        "agent_id": "agent_1",
    }

    seen = []
    original_chat = fsm.bridge.chat

    def chat_with_trace(*args, **kwargs):
        seen.append("chat")
        return original_chat(*args, **kwargs)

    fsm.bridge.chat = chat_with_trace

    store = get_pause_store()
    store.set_flag("project", "proj_x", "paused", reason="maintenance")
    try:
        result = fsm.run(
            workflow,
            messages,
            variables=variables,
            provider_ref="p",
            model_ref="m",
        )
        # Le workflow ne doit pas avoir avancé tant que la pause est active
        assert result.status == "running"
        assert result.iterations == 0
        assert seen == []
    finally:
        store.clear_flag("project", "proj_x")


def test_fsm_resumes_after_global_resume():
    fsm = FSMInterpreter(bridge=DummyBridge())
    workflow = {
        "steps": [
            {"id": "s1", "type": "llm_call", "next": "end"},
            {"id": "end", "type": "end", "status": "SUCCESS"},
        ]
    }
    messages = [{"role": "user", "content": "hi"}]
    variables = {
        "project_id": "proj_y",
        "team_name": "team_b",
        "agent_id": "agent_2",
    }

    seen = []
    original_chat = fsm.bridge.chat

    def chat_with_trace(*args, **kwargs):
        seen.append("chat")
        return original_chat(*args, **kwargs)

    fsm.bridge.chat = chat_with_trace

    result_box = {}

    def target():
        result_box["result"] = fsm.run(
            workflow,
            messages,
            variables=variables,
            provider_ref="p",
            model_ref="m",
        )

    store = get_pause_store()
    store.set_flag("project", "proj_y", "paused", reason="maintenance")
    try:
        t = threading.Thread(target=target)
        t.start()

        # attendre que le thread soit bien bloqué en pause
        time.sleep(0.4)
        assert seen == []

        # resume : réveille les agents en attente
        store.clear_flag("project", "proj_y")
        t.join(timeout=5)

        result = result_box.get("result")
        assert result is not None
        assert result.status == "success"
        assert len(seen) == 1
    finally:
        store.clear_flag("project", "proj_y")


def test_fsm_notifies_waiters_on_pause_change():
    fsm = FSMInterpreter(bridge=DummyBridge())
    workflow = {
        "steps": [
            {"id": "s1", "type": "llm_call", "next": "end"},
            {"id": "end", "type": "end", "status": "SUCCESS"},
        ]
    }
    messages = [{"role": "user", "content": "hi"}]
    variables = {
        "project_id": "proj_z",
        "team_name": "team_c",
        "agent_id": "agent_3",
    }

    seen = []
    original_chat = fsm.bridge.chat

    def chat_with_trace(*args, **kwargs):
        seen.append("chat")
        return original_chat(*args, **kwargs)

    fsm.bridge.chat = chat_with_trace

    result_box = {}

    def target():
        result_box["result"] = fsm.run(
            workflow,
            messages,
            variables=variables,
            provider_ref="p",
            model_ref="m",
        )

    store = get_pause_store()
    store.set_flag("project", "proj_z", "paused", reason="maintenance")
    try:
        t = threading.Thread(target=target)
        t.start()

        # attendre que le thread soit bien bloqué en pause
        time.sleep(0.4)
        assert seen == []

        # resume : réveille les agents en attente
        store.clear_flag("project", "proj_z")
        t.join(timeout=5)

        result = result_box.get("result")
        assert result is not None
        assert result.status == "success"
        assert len(seen) == 1
    finally:
        store.clear_flag("project", "proj_z")


def test_fsm_does_not_block_when_not_paused():
    fsm = FSMInterpreter(bridge=DummyBridge())
    workflow = {
        "steps": [
            {"id": "s1", "type": "llm_call", "next": "end"},
            {"id": "end", "type": "end", "status": "SUCCESS"},
        ]
    }
    messages = [{"role": "user", "content": "hi"}]
    variables = {
        "project_id": "proj_ok",
        "team_name": "team_ok",
        "agent_id": "agent_ok",
    }

    result = fsm.run(
        workflow,
        messages,
        variables=variables,
        provider_ref="p",
        model_ref="m",
    )
    assert result.status == "success"
    assert result.iterations == 1
