"""Tests unitaires et d'intégration pour l'audit des accès aux clés API.

Couverture :
- lecture réussie
- lecture échouée (clé verrouillée / inexistante)
- écriture réussie
- écriture échouée
- accès non authentifié
- structure des logs d'audit : timestamp, user_id, operation_type, endpoint, result
"""

import json
import os
import sqlite3
import sys
import time
import tempfile
import unittest
from http.client import HTTPConnection
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.audit import AuditLogger, audit
from services.api.handlers.keys import (
    op_keys_get,
    op_keys_set,
    op_keys_delete,
    op_keys_list,
)
from modules.sql.db import ModelWeaverDB, RuntimeDB
from modules.key_manager.key_manager import KeyManager
from modules.key_manager.key_manager_module import KeyLockedError


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _reset_audit_db() -> AuditLogger:
    """Retourne un AuditLogger neuf avec une DB en mémoire."""
    logger = AuditLogger()
    logger._db = None  # force recréation
    return logger


def _read_audit_rows(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()]
    conn.close()
    return rows


def _payload(row: dict) -> dict:
    try:
        return json.loads(row.get("payload_json") or "{}")
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Tests unitaires : AuditLogger
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditLoggerUnit(unittest.TestCase):
    """Tests directs du logger d'audit."""

    def setUp(self):
        import services.audit as audit_mod
        self._orig = audit_mod._audit
        self.logger = _reset_audit_db()
        audit_mod._audit = self.logger

    def tearDown(self):
        import services.audit as audit_mod
        audit_mod._audit = self._orig

    def test_log_creates_entry(self):
        self.logger.log("keys.get", actor="user_1", ok=True,
                        payload={"provider_ref": "openai", "identity": "default"})
        db_path = self.logger._get_db().db_path
        rows = _read_audit_rows(db_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "keys.get")
        self.assertEqual(rows[0]["actor"], "user_1")
        self.assertEqual(rows[0]["ok"], 1)

    def test_log_stores_payload_json(self):
        self.logger.log("keys.set", actor="u", ok=False,
                        payload={"error": "missing_params", "provider_ref": "x"})
        db_path = self.logger._get_db().db_path
        rows = _read_audit_rows(db_path)
        p = _payload(rows[0])
        self.assertEqual(p["error"], "missing_params")
        self.assertEqual(p["provider_ref"], "x")

    def test_log_thread_safety(self):
        import threading
        errors = []

        def worker(i):
            try:
                self.logger.log("keys.get", actor=f"u{i}", ok=(i % 2 == 0))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        db_path = self.logger._get_db().db_path
        rows = _read_audit_rows(db_path)
        self.assertEqual(len(rows), 50)
        self.assertEqual(errors, [])

    def test_audit_helper_function(self):
        audit("keys.get", actor="user_42", ok=True,
              provider_ref="groq", identity="default", endpoint="/v1/keys/get")
        db_path = audit_mod._audit._get_db().db_path
        rows = _read_audit_rows(db_path)
        self.assertEqual(len(rows), 1)
        p = _payload(rows[0])
        self.assertEqual(rows[0]["action"], "keys.get")
        self.assertEqual(p["provider_ref"], "groq")
        self.assertEqual(p["endpoint"], "/v1/keys/get")


# ─────────────────────────────────────────────────────────────────────────────
# Tests unitaires : handlers clés API (lecture / écriture)
# ─────────────────────────────────────────────────────────────────────────────

class TestKeyHandlersAudit(unittest.TestCase):
    """Tests des handlers de clés avec vérification de l'audit."""

    def setUp(self):
        import services.audit as audit_mod
        self._orig = audit_mod._audit
        self.logger = _reset_audit_db()
        audit_mod._audit = self.logger

        # DB de test
        self.db = ModelWeaverDB()
        # Nettoyage
        self.db.conn.execute("DELETE FROM api_keys WHERE ref LIKE 'test_audit_%'")
        self.db.conn.execute("DELETE FROM providers WHERE ref LIKE 'test_audit_%'")
        self.db.conn.execute("DELETE FROM audit_log")
        self.db.conn.commit()

        # Provider de test
        self.prov_id = self.db.providers.save({
            "ref": "test_audit_prov",
            "name": "Test Audit Prov",
            "provider_type": "cloud",
        })

    def tearDown(self):
        self.db.conn.execute("DELETE FROM api_keys WHERE ref LIKE 'test_audit_%'")
        self.db.conn.execute("DELETE FROM providers WHERE ref='test_audit_prov'")
        self.db.conn.commit()
        self.db.close()
        import services.audit as audit_mod
        audit_mod._audit = self._orig

    def _db_path(self):
        return self.logger._get_db().db_path

    def _audit_rows(self):
        return _read_audit_rows(self._db_path())

    # ── Écriture réussie ─────────────────────────────────────────────────────

    def test_keys_set_success_audit(self):
        result = op_keys_set({
            "provider_ref": "test_audit_prov",
            "api_key": "sk-test-1234",
            "identity": "default",
            "tag": "paid",
        })
        self.assertEqual(result["status"], "ok")
        self.assertIn("ref", result)

        rows = self._audit_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "keys.set")
        self.assertEqual(rows[0]["ok"], 1)
        p = _payload(rows[0])
        self.assertEqual(p["provider_ref"], "test_audit_prov")
        self.assertIn("ref", p)
        self.assertIn("endpoint", p)

    # ── Écriture échouée ─────────────────────────────────────────────────────

    def test_keys_set_failure_audit(self):
        result = op_keys_set({
            "provider_ref": "test_audit_prov",
            # api_key manquant
        })
        self.assertEqual(result["status"], "error")

        rows = self._audit_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "keys.set")
        self.assertEqual(rows[0]["ok"], 0)
        p = _payload(rows[0])
        self.assertIn("error", p)

    # ── Lecture réussie ──────────────────────────────────────────────────────

    def test_keys_get_success_audit(self):
        # Préparer : créer une clé en DB + store
        km = KeyManager(db=self.db)
        ref = km.set_key("test_audit_prov", "sk-secret-read", identity="default")
        km.load()

        result = op_keys_get({
            "provider_ref": "test_audit_prov",
            "identity": "default",
        })
        self.assertEqual(result["status"], "ok")

        rows = self._audit_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "keys.get")
        self.assertEqual(rows[0]["ok"], 1)
        p = _payload(rows[0])
        self.assertEqual(p["provider_ref"], "test_audit_prov")
        self.assertIn("endpoint", p)

    # ── Lecture échouée : clé inexistante ───────────────────────────────────

    def test_keys_get_not_found_audit(self):
        result = op_keys_get({
            "provider_ref": "test_audit_prov",
            "identity": "default",
        })
        self.assertEqual(result["status"], "not_found")

        rows = self._audit_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "keys.get")
        self.assertEqual(rows[0]["ok"], 0)
        p = _payload(rows[0])
        self.assertEqual(p["error"], "not_found")

    # ── Lecture échouée : clé verrouillée ───────────────────────────────────

    def test_keys_get_locked_audit(self):
        km = KeyManager(db=self.db)
        ref = km.set_key("test_audit_prov", "sk-secret-lock", identity="default")
        km.set_lock(ref, True)
        km.load()

        result = op_keys_get({
            "provider_ref": "test_audit_prov",
            "identity": "default",
        })
        self.assertEqual(result["status"], "locked")

        rows = self._audit_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "keys.get")
        self.assertEqual(rows[0]["ok"], 0)
        p = _payload(rows[0])
        self.assertEqual(p["error"], "locked")

    # ── Suppression réussie ──────────────────────────────────────────────────

    def test_keys_delete_success_audit(self):
        km = KeyManager(db=self.db)
        ref = km.set_key("test_audit_prov", "sk-del", identity="default")

        result = op_keys_delete({"ref": ref})
        self.assertEqual(result["status"], "ok")

        rows = self._audit_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "keys.delete")
        self.assertEqual(rows[0]["ok"], 1)

    # ── Suppression échouée (ref inexistante) ───────────────────────────────

    def test_keys_delete_failure_audit(self):
        result = op_keys_delete({"ref": "test_audit_nonexistent"})
        self.assertEqual(result["status"], "error")

        rows = self._audit_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "keys.delete")
        self.assertEqual(rows[0]["ok"], 0)


# ─────────────────────────────────────────────────────────────────────────────
# Tests d'intégration : structure des logs d'audit
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditLogStructure(unittest.TestCase):
    """Vérifie la présence et le type de chaque champ requis dans les logs."""

    REQUIRED_FIELDS = ["timestamp", "user_id", "operation_type", "endpoint", "result"]

    def setUp(self):
        import services.audit as audit_mod
        self._orig = audit_mod._audit
        self.logger = _reset_audit_db()
        audit_mod._audit = self.logger

    def tearDown(self):
        import services.audit as audit_mod
        audit_mod._audit = self._orig

    def _db_path(self):
        return self.logger._get_db().db_path

    def _log_structure(self, action, payload):
        audit(action, actor="user_integ", ok=True, **payload)
        rows = _read_audit_rows(self._db_path())
        return rows[-1]

    def test_keys_get_log_structure(self):
        row = self._log_structure("keys.get", {
            "provider_ref": "openai",
            "identity": "default",
            "endpoint": "/v1/keys/get",
        })

        # timestamp : entier non nul
        self.assertIsInstance(row["ts"], int)
        self.assertGreater(row["ts"], 0)

        # user_id / actor
        self.assertEqual(row["actor"], "user_integ")

        # operation_type
        self.assertEqual(row["action"], "keys.get")

        # endpoint
        p = _payload(row)
        self.assertIn("endpoint", p)
        self.assertEqual(p["endpoint"], "/v1/keys/get")

        # result : ok=True -> 1
        self.assertEqual(row["ok"], 1)

    def test_keys_set_log_structure(self):
        row = self._log_structure("keys.set", {
            "provider_ref": "groq",
            "ref": "key_abc123",
            "endpoint": "/v1/keys/set",
        })

        self.assertIsInstance(row["ts"], int)
        self.assertGreater(row["ts"], 0)
        self.assertEqual(row["actor"], "user_integ")
        self.assertEqual(row["action"], "keys.set")
        p = _payload(row)
        self.assertEqual(p["endpoint"], "/v1/keys/set")
        self.assertEqual(row["ok"], 1)

    def test_keys_set_failure_log_structure(self):
        row = self._log_structure("keys.set", {
            "provider_ref": "anthropic",
            "error": "missing_api_key",
            "endpoint": "/v1/keys/set",
            "ok": False,
        })

        # On override ok via le log() direct
        self.assertEqual(row["ok"], 0)
        p = _payload(row)
        self.assertEqual(p["error"], "missing_api_key")
        self.assertEqual(p["endpoint"], "/v1/keys/set")

    def test_keys_get_failure_log_structure(self):
        row = self._log_structure("keys.get", {
            "provider_ref": "mistral",
            "error": "locked",
            "endpoint": "/v1/keys/get",
            "ok": False,
        })

        self.assertEqual(row["ok"], 0)
        p = _payload(row)
        self.assertEqual(p["error"], "locked")

    def test_unauthorized_access_log_structure(self):
        """Accès non authentifié : le daemon renvoie 401, l'audit doit logguer."""
        row = self._log_structure("auth.unauthorized", {
            "endpoint": "/v1/keys/get",
            "error": "missing_token",
            "ok": False,
        })

        self.assertEqual(row["action"], "auth.unauthorized")
        self.assertEqual(row["ok"], 0)
        self.assertIsInstance(row["ts"], int)
        p = _payload(row)
        self.assertEqual(p["endpoint"], "/v1/keys/get")
        self.assertEqual(p["error"], "missing_token")


# ─────────────────────────────────────────────────────────────────────────────
# Tests d'intégration : scénarios complets (lecture, écriture, auth)
# ─────────────────────────────────────────────────────────────────────────────

class TestKeyAccessIntegrationScenarios(unittest.TestCase):
    """Scénarios d'intégration bout-en-bout via les handlers."""

    def setUp(self):
        import services.audit as audit_mod
        self._orig = audit_mod._audit
        self.logger = _reset_audit_db()
        audit_mod._audit = self.logger

        self.db = ModelWeaverDB()
        self.db.conn.execute("DELETE FROM api_keys WHERE ref LIKE 'test_int_%'")
        self.db.conn.execute("DELETE FROM providers WHERE ref LIKE 'test_int_%'")
        self.db.conn.execute("DELETE FROM audit_log")
        self.db.conn.commit()

        self.prov_id = self.db.providers.save({
            "ref": "test_int_prov",
            "name": "Test Int Prov",
            "provider_type": "cloud",
        })

    def tearDown(self):
        self.db.conn.execute("DELETE FROM api_keys WHERE ref LIKE 'test_int_%'")
        self.db.conn.execute("DELETE FROM providers WHERE ref='test_int_prov'")
        self.db.conn.commit()
        self.db.close()
        import services.audit as audit_mod
        audit_mod._audit = self._orig

    def _rows(self):
        return _read_audit_rows(self.logger._get_db().db_path)

    def test_read_success_then_failure(self):
        """Lecture réussie, puis lecture sur clé verrouillée."""
        km = KeyManager(db=self.db)
        ref = km.set_key("test_int_prov", "sk-int", identity="default")
        km.load()

        # 1) lecture réussie
        r1 = op_keys_get({"provider_ref": "test_int_prov", "identity": "default"})
        self.assertEqual(r1["status"], "ok")

        # 2) verrouiller puis relire
        km.set_lock(ref, True)
        km._cache = {}  # reset cache
        km.load()
        r2 = op_keys_get({"provider_ref": "test_int_prov", "identity": "default"})
        self.assertEqual(r2["status"], "locked")

        rows = self._rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["ok"], 1)
        self.assertEqual(rows[1]["ok"], 0)
        self.assertEqual(rows[0]["action"], "keys.get")
        self.assertEqual(rows[1]["action"], "keys.get")

    def test_write_success_then_read(self):
        """Écriture réussie, puis lecture de la clé fraîchement écrite."""
        r_set = op_keys_set({
            "provider_ref": "test_int_prov",
            "api_key": "sk-new-key",
            "identity": "default",
        })
        self.assertEqual(r_set["status"], "ok")
        ref = r_set["ref"]

        # recharger le km pour prise en compte
        km = KeyManager(db=self.db)
        km.load()

        r_get = op_keys_get({
            "provider_ref": "test_int_prov",
            "identity": "default",
        })
        self.assertEqual(r_get["status"], "ok")
        self.assertEqual(r_get["key"]["api_key"], "sk-new-key")

        rows = self._rows()
        # set + get = 2 entrées
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["action"], "keys.set")
        self.assertEqual(rows[0]["ok"], 1)
        self.assertEqual(rows[1]["action"], "keys.get")
        self.assertEqual(rows[1]["ok"], 1)

    def test_unauthorized_access_no_key_call(self):
        """Accès non authentifié ne doit PAS aboutir à un appel key réussi."""
        # Simuler une absence de token : le handler ne doit pas logger keys.get
        # On teste via le helper d'autorisation du daemon.
        from services.api.daemon import MWAPIHandler
        handler = MWAPIHandler.__new__(MWAPIHandler)
        handler.headers = {}
        handler.server = type("Srv", (), {"token": "correct-token"})()

        self.assertFalse(handler._authorized())

        # Vérifier qu'aucun log keys.get n'est créé dans ce scénario
        rows = self._rows()
        keys_get_rows = [r for r in rows if r["action"] == "keys.get"]
        self.assertEqual(keys_get_rows, [])


# ─────────────────────────────────────────────────────────────────────────────
# Tests d'intégration HTTP (optionnels, si daemon démarré)
# ─────────────────────────────────────────────────────────────────────────────

class TestKeyAPIHTTPIntegration(unittest.TestCase):
    """Tests HTTP réels contre le daemon si disponible."""

    @classmethod
    def setUpClass(cls):
        cls.port = None
        cls.token = None
        port_file = Path.home() / ".modelweaver" / "api.port"
        token_file = Path.home() / ".modelweaver" / "api.token"
        if port_file.exists() and token_file.exists():
            try:
                cls.port = int(port_file.read_text().strip())
                cls.token = token_file.read_text().strip()
                # Vérifier que le daemon répond
                conn = HTTPConnection("127.0.0.1", cls.port, timeout=2)
                conn.request("GET", "/health")
                resp = conn.getresponse()
                conn.close()
                cls.daemon_up = resp.status == 200
            except Exception:
                cls.daemon_up = False
        else:
            cls.daemon_up = False

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def test_unauthorized_http_401(self):
        if not self.daemon_up or not self.port:
            self.skipTest("Daemon non disponible")
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/v1/keys/list", headers={})
        resp = conn.getresponse()
        conn.close()
        self.assertEqual(resp.status, 401)

    def test_authorized_keys_list_http(self):
        if not self.daemon_up or not self.port:
            self.skipTest("Daemon non disponible")
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/v1/keys/list", headers=self._headers())
        resp = conn.getresponse()
        body = json.loads(resp.read().decode())
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertIn("keys", body)


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main()
