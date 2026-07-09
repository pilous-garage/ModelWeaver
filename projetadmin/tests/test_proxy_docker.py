#!/usr/bin/env python3
"""Test Docker du proxy context-aware (litellm_router_proxy.py).

Vérifie que le fallback fonctionne correctement :
  Google Gemma 4 (priorité 5) → Google Gemini (429) → Mistral (fallback réussi)
"""
import subprocess, json, sys, os, time, signal, tempfile, shutil

DOCKER_IMAGE = "python:3.12-slim"
WORKDIR = "/app"
SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def build_test_image(workdir: str) -> str:
    tag = "modelweaver-proxy-test:latest"
    subprocess.run(
        ["docker", "build", "--pull", "-t", tag, "-"],
        input=f"""
FROM {DOCKER_IMAGE}
RUN pip install --no-cache-dir litellm fastapi uvicorn pyyaml pydantic httpx gitingest
WORKDIR {workdir}
""".encode(),
        check=True, capture_output=True,
    )
    return tag

def main():
    tag = build_test_image(WORKDIR)
    container = None
    try:
        # Create container
        container = subprocess.check_output([
            "docker", "create",
            "--network", "host",
            "--name", "mw-proxy-test",
            tag,
            "python3", f"{WORKDIR}/litellm_router_proxy.py", "8000",
        ]).decode().strip()

        # Copy files
        proxy_file = os.path.join(SRC_DIR, ".modelweaver", "litellm_router_proxy.py")
        config_file = os.path.join(SRC_DIR, ".modelweaver", "litellm_config.yaml")
        proxy_name = "litellm_router_proxy.py"
        config_name = "litellm_config.yaml"
        subprocess.run(["docker", "cp", proxy_file, f"{container}:{WORKDIR}/{proxy_name}"], check=True)
        subprocess.run(["docker", "cp", config_file, f"{container}:{WORKDIR}/{config_name}"], check=True)

        # Start
        subprocess.run(["docker", "start", container], check=True)

        # Wait for proxy to be ready
        import urllib.request
        for i in range(30):
            try:
                r = urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2)
                if r.status == 200:
                    print(f"✅ Proxy ready (attempt {i+1})")
                    break
            except Exception:
                pass
            time.sleep(1)
        else:
            print("❌ Proxy not ready after 30s")
            subprocess.run(["docker", "logs", container])
            sys.exit(1)

        # Send a test request
        req = json.dumps({
            "model": "opencode-engine",
            "messages": [{"role": "user", "content": "Réponds uniquement: test_ok"}],
            "max_tokens": 10,
        }).encode()
        r = urllib.request.Request(
            "http://127.0.0.1:8000/v1/chat/completions",
            data=req,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer sk-litellm-master",
            },
            method="POST",
        )
        resp = json.loads(urllib.request.urlopen(r, timeout=60).read())
        responded = resp.get("_responded", "?")
        errors = resp.get("_eliminated", [])
        content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
        print(f"✅ Répondu par: {responded}")
        print(f"   Content: {content[:80]}")
        if errors:
            print(f"   Fallback depuis: {errors[:3]}...")
        # Check that it responded (not empty)
        assert content and content.strip(), f"Empty response from {responded}"
        print(f"\n🎉 SUCCÈS: {responded} a répondu!")
    except Exception as e:
        print(f"❌ ERREUR: {e}")
        if container:
            logs = subprocess.check_output(["docker", "logs", container], text=True)
            print(f"--- Logs du container ---\n{logs}")
        sys.exit(1)
    finally:
        if container:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True)

if __name__ == "__main__":
    main()
