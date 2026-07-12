#!/usr/bin/env python3
"""
ModelWeaver — SDK client Python de l'API locale.

Utilisé par le CLI, le testeur, et tout futur outil Python. Découvre
automatiquement le port + le token écrits par le daemon dans ~/.modelweaver.

Exemple:
    from mw_client import MWClient
    mw = MWClient()
    print(mw.catalogue.tools.list())
    mw.jobs.add(ref="opencode", job_type="install")
"""
import json
import urllib.request
import urllib.error
from pathlib import Path


class MWError(Exception):
    pass


class _Namespace:
    """Sucre syntaxique : mw.catalogue.tools.list() -> call('catalogue/tools/list')."""
    def __init__(self, client, prefix):
        self._client = client
        self._prefix = prefix

    def __getattr__(self, name):
        return _Namespace(self._client, f"{self._prefix}/{name}")

    def __call__(self, **params):
        return self._client.call(self._prefix, **params)


class MWClient:
    def __init__(self, port=None, token=None, base_dir=None, timeout=930):
        self.base_dir = Path(base_dir) if base_dir else (Path.home() / ".modelweaver")
        self.timeout = timeout
        self.port = port or self._read("api.port")
        self.token = token or self._read("api.token")
        if not self.port or not self.token:
            raise MWError(
                "daemon introuvable : api.port/api.token absents de "
                f"{self.base_dir} (le daemon est-il démarré ?)")
        self.base_url = f"http://127.0.0.1:{self.port}"

    def _read(self, name):
        p = self.base_dir / name
        return p.read_text().strip() if p.exists() else None

    # ── appel bas niveau ──
    def call(self, route, **params):
        url = f"{self.base_url}/v1/{route.strip('/')}"
        data = json.dumps(params).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read())
            except Exception:
                raise MWError(f"HTTP {e.code} sur {route}")
            raise MWError(f"{route}: {payload.get('error')} {payload.get('detail','')}")
        except urllib.error.URLError as e:
            raise MWError(f"daemon injoignable ({self.base_url}): {e.reason}")
        if not payload.get("ok"):
            raise MWError(f"{route}: {payload.get('error')}")
        return payload.get("result")

    def health(self):
        with urllib.request.urlopen(f"{self.base_url}/health", timeout=5) as resp:
            return json.loads(resp.read())

    # ── namespaces (mw.<domaine>.<action>(...)) ──
    def __getattr__(self, name):
        return _Namespace(self, name)
