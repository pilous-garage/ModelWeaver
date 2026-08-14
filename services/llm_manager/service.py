#!/usr/bin/env python3
"""Service LLM Manager — décision et allocation LLM centralisées.

Processe séparé, supervisé. Écoute sur un socket Unix `llm.sock` et répond
aux requêtes :
  - allocate : choisir un provider/modèle selon use_case/contraintes
  - state    : état global (modèles en repos, budgets)
  - report   : enregistrer un échec d'appel (unavailable/noretryuntil)
  - status   : état du service

Ne contient PAS le bridge d'exécution : les agents gardent leur bridge local
(DirectBridge) pour les appels directs. Ce service ne fait que la DÉCISION et
la gestion de l'état (repos après échec, exclusion des modèles indisponibles).

Le bridge écrit l'état (unavailable/noretryuntil) en BDD ; ce service le lit
pour exclure les modèles en repos lors de l'allocation. La BDD SQLite partagée
est le canal d'état.
"""

import json
import os
import socket
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services._common import acquire_instance_lock, mw_home  # noqa: E402

LLM_SOCK = mw_home() / "llm.sock"
STATE_DB = mw_home() / "catalogue.db"


def _read_rest_models() -> set:
    """Modèles en repos runtime : unavailable=1 ou noretryuntil dans le futur.

    Retourne {(provider_ref, provider_model_name)} — la clé utilisée par
    l'allocation (ref brute côté provider). Le provider_model_name est NORMALISÉ
    sans préfixe provider redondant (certains providers stockent
    `huggingface/deepseek-ai/…`, d'autres `01-ai/…`) pour que l'exclusion
    `{provider_ref}/{model}` matche la ref du candidat.
    """
    import sqlite3
    try:
        conn = sqlite3.connect(str(STATE_DB), timeout=2)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("""
                SELECT p.ref AS provider_ref, pm.provider_model_name
                FROM provider_models pm
                JOIN catalogue_providers p ON p.id = pm.provider_id
                WHERE pm.noretryuntil > ?
            """, (time_now(),)).fetchall()
            out = set()
            for r in rows:
                prov = r["provider_ref"]
                mname = r["provider_model_name"]
                if mname.startswith(prov + "/"):
                    mname = mname[len(prov) + 1:]
                out.add((prov, mname))
            return out
        finally:
            conn.close()
    except Exception:
        return set()


def time_now() -> float:
    import time
    return time.time()


def _probe_model(provider_ref: str, model_ref: str) -> bool:
    """Teste réellement qu'un modèle répond (appel max_tokens=1).

    Le probe passe un OUTIL simple : un modèle qui refuse le tool calling
    (ex. groq/allam-2-7b) échoue ici et ne sera jamais alloué aux agents
    qui exécutent des workflows à outils.

    Retourne True si le modèle répond. Best-effort : False si erreur
    (rate-limit, 401, 404, crédit insuffisant, tool calling non supporté…).
    """
    try:
        from modules.llm_manager.direct_bridge import DirectBridge
        bridge = DirectBridge(cat=None)
        probe_tools = [{
            "type": "function",
            "function": {
                "name": "ping_probe",
                "description": "Test",
                "parameters": {"type": "object", "properties": {}},
            },
        }]
        resp = bridge.chat(provider_ref, model_ref,
                           [{"role": "user", "content": "ok"}],
                           max_tokens=1, temperature=0, tools=probe_tools,
                           caller_id="probe:llm-manager")
        return resp is not None
    except Exception:
        return False


class LLMManagerService:
    # Durée de validité d'un claim de modèle (secondes). Au-delà, un claim sans
    # libération (agent mort/crash) est considéré expiré et le modèle redevient
    # allouable. En mémoire seulement (pas de table BDD → pas de lock SQLite,
    # et le service est l'unique allocateur).
    CLAIM_TTL = 300.0

    def __init__(self):
        self._sock: Any = None
        self._running = True
        # Anti-affinité : {model_key: (agent_id, claimed_at)}
        # Un modèle déjà pris par un agent actif n'est pas ré-alloué à un autre.
        self._claims: Dict[str, tuple] = {}

    # ── Anti-affinité (claims en mémoire) ──

    def _purge_expired_claims(self) -> None:
        now = time_now()
        stale = [k for k, (_, at) in self._claims.items()
                 if now - at > self.CLAIM_TTL]
        for k in stale:
            self._claims.pop(k, None)

    def _normalize_key(self, provider_ref: str, model_ref: str) -> str:
        """Clé de modèle normalisée : retire le préfixe provider redondant.

        Ex. (google, 'google/gemini-3.1-flash-lite') → 'google/gemini-3.1-flash-lite'.
        Le pool contient des doublons (avec/sans préfixe) ; sans normalisation,
        le claim de A ('google/gemini-x') n'exclut pas le doublon préfixé que
        B reçoit ('google/google/gemini-x').
        """
        m = model_ref or ""
        if m.startswith(provider_ref + "/"):
            m = m[len(provider_ref) + 1:]
        return f"{provider_ref}/{m}"

    def _taken_model_keys(self, agent_id: str) -> List[str]:
        """Clés de modèles pris par D'AUTRES agents (pas self)."""
        self._purge_expired_claims()
        return [k for k, (aid, _) in self._claims.items() if aid != agent_id]

    def _release_claims(self, agent_id: str) -> None:
        self._claims = {k: v for k, v in self._claims.items()
                        if v[0] != agent_id}

    # ── Allocation ──

    def allocate(self, params: dict) -> dict:
        """Alloue un provider/modèle via services/llm_allocation.allocate_llm.

        Respecte l'état (modèles en repos exclus). Exclut aussi les providers/
        modèles déjà essayés (paramètre exclude).

        AUCUN probe : prober = consommer une vraie requête API (RPM) pour un
        test inutile — un modèle à RPM=1 (ex. gemini-3.5) se fait consommer
        son slot par le probe, garantissant le rate-limit du membre qui suit.
        On retourne le candidat du scoring directement ; s'il est mort, le
        premier appel réel échoue → `_mark_call_failed` le met en repos et le
        fallback passe au suivant (coût : quelques secondes de requête).
        """
        from services.llm_allocation.allocate import allocate_llm

        exclude = params.get("exclude") or []
        exclude_providers = list(params.get("exclude_providers") or [])
        exclude_models = list(params.get("exclude_models") or [])
        # Ajouter les modèles en repos à l'exclusion (à la volée)
        rest = _read_rest_models()
        for prov, model in rest:
            exclude.append(f"{prov}/{model}")
        # Anti-affinité : un modèle déjà pris par un AUTRE agent actif est
        # exclu — sinon 2 codeurs parallèles se partagent le même gemini à
        # RPM bas et se saturent mutuellement. On exclut la clé normalisée
        # ET sa variante préfixée (google/gemini-x vs google/google/gemini-x).
        agent_id = str(params.get("agent_id") or "")
        for key in self._taken_model_keys(agent_id):
            exclude.append(key)
            prov, _, rest = key.partition("/")
            if rest.startswith(prov + "/"):
                exclude.append(f"{prov}/{rest}")
            elif "/" in rest:
                # forme google/gemini-x → ajouter google/google/gemini-x
                prefix = rest.split("/", 1)[0]
                if prefix == prov:
                    exclude.append(f"{prov}/{rest}")
        p = dict(params)
        p["exclude"] = exclude

        result = allocate_llm(p)
        if result.get("status") != "ok" or not result.get("provider_ref"):
            return {
                "status": "error",
                "error": result.get("error", "aucun modèle alloué"),
                "rest_models_count": len(rest),
            }
        # Enregistrer le claim si l'appelant est identifié (clé normalisée)
        if agent_id:
            key = self._normalize_key(
                result["provider_ref"],
                result.get("provider_model_name") or result.get("model_ref"))
            self._claims[key] = (agent_id, time_now())
        result["probed"] = False
        result["rest_models_count"] = len(rest)
        return result
    # ── État ──

    def state(self) -> dict:
        rest = _read_rest_models()
        return {
            "status": "ok",
            "rest_models_count": len(rest),
            "rest_models": sorted(f"{p}/{m}" for p, m in rest),
        }

    def scores(self, limit: int = 200, provider: str = "") -> dict:
        """Tableau COMPLET des scores (id_ref, fail_rate, latency, benchmark,
        final) depuis score_batch. score_final = fail_rate × latency × etire.

        Sert au DEBUG : vérifier ce que le scoring voit réellement pour chaque
        modèle (et détecter les contrats cassés entre écriture/lecture)."""
        from services._common import runtime_db_path
        import sqlite3
        conn = sqlite3.connect(runtime_db_path())
        rows = conn.execute(
            "SELECT provider_ref, model_ref, score_fail_rate, score_latency, "
            "score_etire, score_final FROM score_batch ORDER BY score_final DESC "
            "LIMIT ?", (int(limit),)).fetchall()
        conn.close()
        out = []
        for r in rows:
            p, m, fr, lat, etire, final = r
            if provider and provider not in p:
                continue
            out.append({
                "provider_ref": p,
                "model_ref": m,
                "id_ref": f"{p}/{m}",
                "score_fail_rate": fr or 0.0,
                "score_latency": lat or 0.0,
                "score_benchmark": etire or 0.0,
                "score_final": final or 0.0,
                "score_final_check": round((fr or 0.0) * (lat or 0.0) * (etire or 0.0), 4),
            })
        return {"status": "ok", "count": len(out), "scores": out}

    # ── Report d'échec ──

    def report_failure(self, params: dict) -> dict:
        """Enregistre un échec d'appel LLM (rate-limit/erreur).

        Marque le modèle unavailable + pose noretryuntil (durée croissante).
        C'est la même logique que DirectBridge._mark_call_failed, centralisée
        ici pour que TOUS les process (agents, daemon) puissent la déclencher
        même sans bridge en mémoire.
        """
        provider = params.get("provider_ref", "")
        model = params.get("model_ref", "")
        if not provider or not model:
            return {"status": "error", "error": "provider_ref et model_ref requis"}
        try:
            _mark_unavailable(provider, model)
            return {"status": "ok", "marked": f"{provider}/{model}"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def report_ok(self, params: dict) -> dict:
        """Reset le repos après un appel réussi."""
        provider = params.get("provider_ref", "")
        model = params.get("model_ref", "")
        if not provider or not model:
            return {"status": "error", "error": "provider_ref et model_ref requis"}
        try:
            _clear_unavailable(provider, model)
            return {"status": "ok", "cleared": f"{provider}/{model}"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ── Socket ──

    def serve(self):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            LLM_SOCK.unlink(missing_ok=True)
        except OSError:
            pass
        self._sock.bind(str(LLM_SOCK))
        os.chmod(str(LLM_SOCK), 0o600)
        self._sock.listen(8)
        self._sock.settimeout(1.0)
        while self._running:
            try:
                conn, _ = self._sock.accept()
                threading.Thread(target=self._handle, args=(conn,),
                                 daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle(self, conn):
        try:
            buf = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        req = json.loads(line.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        resp = {"ok": False, "error": "invalid_json", "id": None}
                    else:
                        resp = self._dispatch(req)
                    conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _dispatch(self, req: dict) -> dict:
        call = req.get("call", "")
        rid = req.get("id")
        try:
            if call == "ping":
                result = {"status": "ok", "pong": True}
            elif call == "allocate":
                result = self.allocate(req.get("params") or {})
            elif call == "state":
                result = self.state()
            elif call == "scores":
                result = self.scores(req.get("params") or {})
            elif call == "report_failure":
                result = self.report_failure(req.get("params") or {})
            elif call == "report_ok":
                result = self.report_ok(req.get("params") or {})
            elif call == "report_end":
                # Libère les claims de modèles de cet agent (fin de tâche)
                aid = str((req.get("params") or {}).get("agent_id") or "")
                if aid:
                    self._release_claims(aid)
                result = {"status": "ok"}
            else:
                result = {"status": "error", "error": f"call inconnu: {call}"}
        except Exception as e:
            result = {"status": "error", "error": str(e)}
        return {"ok": result.get("status") in ("ok",), "result": result, "id": rid}


def _mark_unavailable(provider_ref: str, model_ref: str) -> None:
    """Marque un modèle indisponible + pose un repos croissant (DDL direct).

    Matche le provider_model_name avec ou sans préfixe provider (les colonnes
    sont incohérentes : huggingface/... vs nvidia/...).
    """
    import sqlite3
    from modules.llm_manager.direct_bridge import (
        TIME_NO_RESTART_INIT, TIME_NO_RESTART_MULTIPLY, TIME_NO_RESTART_MAX,
    )
    prefixed = f"{provider_ref}/{model_ref}" if not model_ref.startswith(provider_ref + "/") else model_ref
    now = time_now()
    conn = sqlite3.connect(str(STATE_DB), timeout=5, isolation_level=None)
    try:
        row = conn.execute("""
            SELECT notrytime FROM provider_models
            WHERE provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?)
              AND (provider_model_name = ? OR provider_model_name = ?)
        """, (provider_ref, model_ref, prefixed)).fetchone()
        last = row[0] if row else 0.0
        duration = TIME_NO_RESTART_INIT if not last else last * TIME_NO_RESTART_MULTIPLY
        duration = min(duration, TIME_NO_RESTART_MAX)
        conn.execute("""
            UPDATE provider_models
            SET unavailable = 1, noretryuntil = ?, notrytime = ?
            WHERE provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?)
              AND (provider_model_name = ? OR provider_model_name = ?)
        """, (now + duration, duration, provider_ref, model_ref, prefixed))
    finally:
        conn.close()


def _clear_unavailable(provider_ref: str, model_ref: str) -> None:
    import sqlite3
    prefixed = f"{provider_ref}/{model_ref}" if not model_ref.startswith(provider_ref + "/") else model_ref
    conn = sqlite3.connect(str(STATE_DB), timeout=5, isolation_level=None)
    try:
        conn.execute("""
            UPDATE provider_models
            SET unavailable = 0, noretryuntil = 0, notrytime = 0
            WHERE provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?)
              AND (provider_model_name = ? OR provider_model_name = ?)
        """, (provider_ref, model_ref, prefixed))
    finally:
        conn.close()


def main():
    if not acquire_instance_lock("llm_manager"):
        print("[llm-manager] déjà en cours (lock llm_manager)", file=sys.stderr)
        sys.exit(1)
    svc = LLMManagerService()
    print(f"[llm-manager] prêt (pid {os.getpid()}, socket {LLM_SOCK})", flush=True)
    try:
        svc.serve()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
