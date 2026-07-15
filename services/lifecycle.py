import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from enum import Enum


class HookType(str, Enum):
    POST_STEP = "post_step"
    POST_EXEC = "post_exec"
    ON_ERROR = "on_error"
    ON_SIGNAL = "on_signal"


@dataclass
class HookEvent:
    hook_type: str
    agent_id: str
    step: Optional[Dict[str, Any]] = None
    step_id: str = ""
    status: str = ""
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    signal_type: str = ""
    signal_payload: Optional[Dict[str, Any]] = None
    variables: Dict[str, Any] = field(default_factory=dict)


HookCallback = Callable[[HookEvent], None]


class EventBus:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._subscribers: Dict[str, List[HookCallback]] = {}
                    cls._instance._bus_lock = threading.Lock()
        return cls._instance

    def subscribe(self, hook_type: str, callback: HookCallback) -> None:
        with self._bus_lock:
            self._subscribers.setdefault(str(hook_type), []).append(callback)

    def unsubscribe(self, hook_type: str, callback: HookCallback) -> None:
        with self._bus_lock:
            subs = self._subscribers.get(str(hook_type), [])
            if callback in subs:
                subs.remove(callback)

    def publish(self, event: HookEvent) -> None:
        subs = list(self._subscribers.get(str(event.hook_type), []))
        for cb in subs:
            try:
                cb(event)
            except Exception:
                pass


def get_event_bus() -> EventBus:
    return EventBus()


class LifecycleManager:
    """Intègre les hooks de cycle de vie dans l'exécution d'un agent.

    À l'initialisation, lit les hooks définis dans l'agent et y souscrit.
    """

    def __init__(self, agent_id: str, config: Dict[str, Any]):
        self.agent_id = agent_id
        self._hooks = config.get("hooks", {})
        self._skill_hooks: Dict[HookType, List[str]] = {}
        self._bus = get_event_bus()
        self._subscriptions: List[tuple] = []
        self._setup()

    def _setup(self) -> None:
        for hook_type_str, skill_refs in self._hooks.items():
            try:
                ht = HookType(hook_type_str)
            except ValueError:
                continue
            if isinstance(skill_refs, str):
                skill_refs = [skill_refs]
            self._skill_hooks[ht] = list(skill_refs)
            for ref in skill_refs:
                cb = self._make_hook_callback(ht, ref)
                self._bus.subscribe(ht, cb)
                self._subscriptions.append((ht, cb))

        # Hook post_step par défaut (auto-log debug) si non configuré.
        # Tout agent bénéficie d'une traçabilité par étape sans config explicite.
        if HookType.POST_STEP not in self._skill_hooks:
            cb = self._make_default_post_step_callback()
            self._bus.subscribe(HookType.POST_STEP, cb)
            self._subscriptions.append((HookType.POST_STEP, cb))
            self._skill_hooks[HookType.POST_STEP] = ["system/log@v1"]

    def _make_default_post_step_callback(self) -> HookCallback:
        def callback(event: HookEvent) -> None:
            if event.agent_id != self.agent_id:
                return
            try:
                from services.skill_manager import call_skill
                step = event.step or {}
                call_skill("system/log@v1", {
                    "level": "debug",
                    "message": f"step={event.step_id} type={step.get('type','')} "
                               f"status={event.status}",
                    "action": "lifecycle.post_step",
                    "agent_id": event.agent_id,
                    "hook_type": "post_step",
                    "status": event.status,
                })
            except Exception:
                pass
        return callback

    def _make_hook_callback(self, hook_type: HookType, skill_ref: str) -> HookCallback:
        def callback(event: HookEvent) -> None:
            if event.agent_id != self.agent_id:
                return
            try:
                from services.skill_manager import call_skill
                call_skill(skill_ref, {
                    "hook_type": hook_type.value,
                    "agent_id": event.agent_id,
                    "step_id": event.step_id,
                    "status": event.status,
                    "error": event.error or "",
                    "signal_type": event.signal_type,
                    "signal_payload": event.signal_payload or {},
                    "variables": event.variables,
                })
            except Exception:
                pass
        return callback

    def publish(self, hook_type: str, **kwargs: Any) -> None:
        try:
            ht = HookType(hook_type)
        except ValueError:
            return
        event = HookEvent(hook_type=ht, agent_id=self.agent_id, **kwargs)
        self._bus.publish(event)

    def cleanup(self) -> None:
        for ht, cb in self._subscriptions:
            self._bus.unsubscribe(ht, cb)
        self._subscriptions.clear()
