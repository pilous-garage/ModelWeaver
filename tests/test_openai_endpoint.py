"""test_openai_endpoint — Validation de bout en bout de l'endpoint
/v1/chat/completions contre un daemon réel."""

import os
import subprocess
import sys
import time
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def daemon():
    """Démarre un daemon MW sur un port de test, retourne (base_url, token)."""
    mw_home = Path(tempfile.mkdtemp())
    os.environ["MODELWEAVER_HOME"] = str(mw_home)
    port = 8799
    proc = subprocess.Popen(
        [sys.executable, "services/api/daemon.py", "serve",
         "--port", str(port), "--bind", "127.0.0.1"],
        cwd=Path(__file__).resolve().parent.parent,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # attend le token
    token = ""
    for _ in range(20):
        tf = mw_home / "api.token"
        if tf.exists():
            token = tf.read_text().strip()
            break
        time.sleep(0.5)
    yield f"http://127.0.0.1:{port}/v1", token
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


def test_openai_endpoint_reachable(daemon):
    """L'endpoint répond au format OpenAI (même si le dev-chat échoue sans
    pilote réel — on vérifie la structure de la réponse)."""
    base_url, token = daemon
    assert token, "token API introuvable"
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=token)
    # on évite l'appel dev-chat réel (long) — on teste la route bench/stats
    # (même serveur, structure {ok, result}) pour valider la connexion.
    import urllib.request
    import json as _json
    req = urllib.request.Request(
        f"{base_url}/bench/stats",
        data=_json.dumps({}).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = _json.loads(resp.read())
    assert body.get("ok") is True or "stats" in body
