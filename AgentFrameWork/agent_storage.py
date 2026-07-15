"""AgentStorage — Espace disque proprio par agent (V0.6.8).

Chaque agent reçoit un dossier `mw_home()/memagent/{agent_id}/` avec :
  - mem/     : mémoire long-terme (fichiers, notes, apprentissages)
  - ctx/     : contexte (system prompt, personnalité, préférences)
  - history/ : historiques de chat/exécution (fichiers, logs)
  - work/    : workspace dédié — RW complet pour l'agent

Un quota soft (par défaut 10 Mo) est appliqué : les écritures qui
dépassent lèvent `QuotaExceeded`. L'escalade à l'utilisateur passe
par `request_quota_increase()` (enregistre une demande pending) et un
endpoint daemon `storage/quota/approve`.

Intégré au cycle Phénix++ : `ensure()` au hydrate, `destroy()` au delete.
"""

import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Dict, Optional, Union


MEMAGENT_DEFAULT_QUOTA = 10 * 1024 * 1024       # 10 Mo
MEMAGENT_MAX_QUOTA = 10 * 1024 * 1024 * 1024     # 10 Go (docker)
SUBDIRS = ("mem", "ctx", "history", "work")


class QuotaExceeded(Exception):
    """Le quota disque de l'agent est atteint."""
    def __init__(self, agent_id: int, limit: int, used: int, needed: int):
        self.agent_id = agent_id
        self.limit = limit
        self.used = used
        self.needed = needed
        super().__init__(
            f"Agent #{agent_id} quota disque dépassé : {used:,}/{limit:,} octets, "
            f"besoin de {needed:,} octets supplémentaires."
        )


class AgentStorage:
    """API de stockage pour un agent.

    L'identité disque est l'agent_id (immutable). Le root est créé par
    `ensure()` au cycle hydrate, et détruit par `destroy()` au delete.
    """

    def __init__(self, agent_id: int, conn: sqlite3.Connection):
        self.agent_id = agent_id
        self.conn = conn
        self._cfg = self._load_config()
        self.root = self._resolve_root()

    # ── Props ──

    def path(self, sub: str = "work") -> Path:
        """Chemin absolu d'un sous-dossier (work par défaut)."""
        if sub not in SUBDIRS:
            raise ValueError(f"sous-dossier inconnu: {sub} (valides: {', '.join(SUBDIRS)})")
        return self.root / sub

    @property
    def max_bytes(self) -> int:
        return self._cfg.get("max_bytes", MEMAGENT_DEFAULT_QUOTA)

    @max_bytes.setter
    def max_bytes(self, value: int):
        self._cfg["max_bytes"] = min(value, MEMAGENT_MAX_QUOTA)
        self._save_config()

    @property
    def used_bytes(self) -> int:
        return self._cfg.get("used_bytes_cache", 0)

    @used_bytes.setter
    def used_bytes(self, value: int):
        self._cfg["used_bytes_cache"] = max(0, value)
        self._save_config()

    def quota_request(self) -> Optional[Dict]:
        return self._cfg.get("quota_request")

    def request_quota_increase(self, needed_bytes: int):
        """Enregistre une demande d'augmentation de quota (pending → utilisateur)."""
        self._cfg["quota_request"] = {
            "needed_bytes": needed_bytes,
            "status": "pending",
        }
        self._save_config()

    def approve_quota_request(self, new_max_bytes: int):
        """Approuve la demande pending → max_bytes mis à jour, request cleared."""
        qr = self._cfg.get("quota_request")
        if qr and qr.get("status") == "pending":
            self.max_bytes = new_max_bytes
            self._cfg.pop("quota_request", None)
            self._save_config()

    # ── Cycle de vie (Phénix++) ──

    def ensure(self) -> bool:
        """Crée l'arborescence si elle n'existe pas. Retourne False si déjà existant."""
        existed = self.root.exists()
        self.root.mkdir(parents=True, exist_ok=True)
        for sub in SUBDIRS:
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        if not existed:
            self._save_config()
        return not existed

    def destroy(self):
        """Supprime le dossier entier de l'agent (déshydratation définitive)."""
        if self.root.exists():
            shutil.rmtree(str(self.root), ignore_errors=True)
        # nettoyer storage_json en BDD
        self.conn.execute(
            "UPDATE agents SET storage_json = NULL WHERE agent_id = ?",
            (self.agent_id,),
        )
        self.conn.commit()

    def recalc_used(self) -> int:
        """Recalcule la taille réelle du dossier (walk complet).

        Utile pour synchroniser le cache avec le disque (ex. si l'agent
        écrit des fichiers hors de notre API)."""
        total = 0
        if self.root.exists():
            for dirpath, _dirs, filenames in os.walk(str(self.root)):
                for fn in filenames:
                    fp = os.path.join(dirpath, fn)
                    try:
                        total += os.path.getsize(fp)
                    except OSError:
                        pass
        self.used_bytes = total
        return total

    # ── Opérations RW (enforce quota) ──

    def write(self, sub: str, relpath: str, data: Union[str, bytes]) -> Path:
        """Écrit un fichier dans le sous-dossier `sub` (ex: 'work', 'mem').

        Vérifie le quota AVANT écriture. Si le quota est dépassé, lève
        QuotaExceeded. Retourne le Path absolu du fichier écrit.

        Exemple
            st.write("work", "script.py", "print('hello')")
        """
        if sub not in SUBDIRS:
            raise ValueError(f"sous-dossier inconnu: {sub}")
        d = self.path(sub)
        fp = d / relpath
        # sécurité : pas de traversée de dossier
        resolved = fp.resolve()
        if not str(resolved).startswith(str(d.resolve())):
            raise ValueError("chemin interdit (traversée de dossier)")
        fp.parent.mkdir(parents=True, exist_ok=True)

        payload = data.encode("utf-8") if isinstance(data, str) else data
        needed = len(payload)
        current = self.used_bytes
        limit = self.max_bytes
        after = current + needed

        if after > limit:
            raise QuotaExceeded(self.agent_id, limit, current, after - limit)

        fp.write_bytes(payload)
        self.used_bytes = after
        return fp

    def read(self, sub: str, relpath: str) -> str:
        """Lit un fichier texte dans le sous-dossier."""
        fp = self.path(sub) / relpath
        return fp.read_text(encoding="utf-8")

    def read_bytes(self, sub: str, relpath: str) -> bytes:
        fp = self.path(sub) / relpath
        return fp.read_bytes()

    def exists(self, sub: str, relpath: str) -> bool:
        return (self.path(sub) / relpath).exists()

    def delete(self, sub: str, relpath: str):
        """Supprime un fichier et met à jour le compteur."""
        fp = self.path(sub) / relpath
        if fp.exists():
            size = fp.stat().st_size
            fp.unlink()
            self.used_bytes = max(0, self.used_bytes - size)

    # ── interne ──

    def _resolve_root(self) -> Path:
        from services._common import mw_home
        return mw_home() / "memagent" / str(self.agent_id)

    def _load_config(self) -> dict:
        row = self.conn.execute(
            "SELECT storage_json FROM agents WHERE agent_id = ?",
            (self.agent_id,),
        ).fetchone()
        if row and row["storage_json"]:
            try:
                cfg = json.loads(row["storage_json"])
                cfg.pop("top", None)
                return cfg
            except (json.JSONDecodeError, TypeError):
                pass
        return {
            "max_bytes": MEMAGENT_DEFAULT_QUOTA,
            "used_bytes_cache": 0,
        }

    def _save_config(self):
        self.conn.execute(
            "UPDATE agents SET storage_json = ? WHERE agent_id = ?",
            (json.dumps(self._cfg), self.agent_id),
        )
        self.conn.commit()


# ── helpers ──

def agent_storage_root() -> Path:
    """Racine de tous les dossiers agent (mw_home()/memagent)."""
    from services._common import mw_home
    return mw_home() / "memagent"