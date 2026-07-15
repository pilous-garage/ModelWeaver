#!/usr/bin/env python3
"""Ressource Manager — Agrégateur global des ressources (Phase 3).

Point unique qui agrège :
  - Hardware machine (Checker.get_hardware_info : RAM/Disque/CPU global)
  - LLM disponibles (Organisateur : providers à clé + moteurs locaux)
  - Futurs gestionnaires de ressources (placeholder extensible)

Il reçoit les ressources déclarées d'un agent et rend un VERDICT
("possible" / "impossible") + l'allocation LLM, et expose cette décision
à l'AgentManager (admission / préemption).
"""

import json
from typing import Dict, Any, Optional

from modules.checker.checker import Checker
from modules.llm_manager.organisateur import Organisateur


class RessourceManager:
    """Agrégateur global de ressources + admission control."""

    def __init__(self):
        self._organisateur = Organisateur()

    # ── Snapshots agrégés ──

    def hardware_snapshot(self) -> Dict[str, Any]:
        """État hardware GLOBAL de la machine (CPU/RAM/Disque)."""
        return Checker().get_hardware_info()

    def snapshot(self) -> Dict[str, Any]:
        """Snapshot complet de toutes les ressources connues."""
        return {
            "hardware": self.hardware_snapshot(),
            "llm": {
                "available_providers": self._organisateur.available_providers(),
                "running_local": self._organisateur.running_local_engines(),
            },
        }

    # ── Admission / évaluation ──

    def evaluate(self, resources: Dict[str, Any]) -> Dict[str, Any]:
        """Évalue si un agent aux ressources données PEUT tourner maintenant.

        resources :
          {
            llm: bool,
            llm_pref: {...},
            ram: {min_gb, max_gb},
            cpu: {min_pct, max_pct},
            priority: 0-10,
            preemptible: bool
          }

        Retourne :
          {
            possible: bool,
            reasons: [str],
            hardware: {...},
            llm: {allocated, provider_ref, model_ref, source, reason},
            resources: <resources normalisés>
          }
        """
        resources = self._normalize(resources)
        hw = self.hardware_snapshot()
        reasons: list[str] = []

        # 1. LLM (tool provider) — alloué par l'Organisateur
        llm = self._organisateur.allocate(resources)
        if resources.get("llm") and not llm["allocated"]:
            reasons.append(f"LLM indisponible : {llm['reason']}")

        # 2. RAM
        ram_ok = True
        ram_min = (resources.get("ram") or {}).get("min_gb")
        if ram_min:
            avail = hw.get("ram_available_gb")
            if avail is None:
                reasons.append("RAM indisponible (psutil absent)")
            elif avail < ram_min:
                ram_ok = False
                reasons.append(f"RAM insuffisante : {avail} Go libres < {ram_min} Go requis")

        # 3. CPU (charge globale + demande <= 100%)
        cpu_ok = True
        cpu_min = (resources.get("cpu") or {}).get("min_pct")
        if cpu_min:
            cur = hw.get("cpu_percent")
            if cur is None:
                reasons.append("CPU indisponible (psutil absent)")
            elif cur + cpu_min > 100:
                cpu_ok = False
                reasons.append(f"CPU saturé : {cur}% + {cpu_min}% requis > 100%")

        possible = (not (resources.get("llm") and not llm["allocated"])) and ram_ok and cpu_ok
        return {
            "possible": possible,
            "reasons": reasons,
            "hardware": hw,
            "llm": llm,
            "resources": resources,
        }

    def can_run(self, resources: Dict[str, Any]) -> bool:
        return self.evaluate(resources)["possible"]

    def allocate_llm(self, resources: Dict[str, Any]) -> Dict[str, Any]:
        """Raccourci : allocation LLM seule."""
        return self._organisateur.allocate(self._normalize(resources))

    # ── Interne ──

    @staticmethod
    def _normalize(resources: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        r = dict(resources or {})
        r.setdefault("llm", False)
        r.setdefault("priority", 5)
        r.setdefault("preemptible", True)
        if isinstance(r.get("priority"), str):
            try:
                r["priority"] = int(r["priority"])
            except ValueError:
                r["priority"] = 5
        return r


def evaluate_resources(resources: Dict[str, Any]) -> Dict[str, Any]:
    """Utilitaire stateless."""
    return RessourceManager().evaluate(resources)


def watch_resources(interval: float = 2.0):
    """Boucle : publie le snapshot global des ressources sur stdout (une ligne
    JSON = un état). Single-instance."""
    from services._common import acquire_instance_lock
    if not acquire_instance_lock("ressource_manager"):
        return
    mgr = RessourceManager()
    while True:
        try:
            print(json.dumps(mgr.snapshot()), flush=True)
        except Exception as e:
            print(json.dumps({"error": str(e)}), flush=True)
        import time
        time.sleep(interval)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "watch":
        watch_resources()
    else:
        print(json.dumps(RessourceManager().snapshot(), indent=2))

