import os
import json
import requests
import re
from pathlib import Path

# Load NVIDIA_API_KEY from .env
def load_nvidia_key():
    env_path = Path("/app/.env")
    if not env_path.exists():
        # Try parent dir if not in /app
        env_path = Path("/app/../.env")
        
    if not env_path.exists():
        print(f"❌ Error: .env file not found at {env_path}")
        return None

    with open(env_path, "r") as f:
        for line in f:
            if line.startswith("NVIDIA_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                return key
    return None

def test_models():
    api_key = load_nvidia_key()
    if not api_key:
        print("❌ Error: NVIDIA_API_KEY not found in .env")
        return

    print(f"🚀 Testing NVIDIA models with key: {api_key[:8]}...")
    
    models_url = "https://integrate.api.nvidia.com/v1/models"
    chat_url = "https://integrate.api.nvidia.com/v1/chat/completions"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(models_url, headers={"Authorization": f"Bearer {api_key}"})
        response.raise_for_status()
        models_data = response.json()
    except Exception as e:
        print(f"❌ Failed to fetch models: {e}")
        return

    models = [m["id"] for m in models_data.get("data", [])]
    print(f"Found {len(models)} models. Starting tests...\n")

    success_count = 0
    fail_count = 0
    
    # We'll test a subset if the list is too long, or just all of them.
    # Let's try all of them, but maybe with a timeout.
    
    for model_id in models:
        print(f"Testing {model_id}...", end="", flush=True)
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 5
        }
        
        try:
            resp = requests.post(chat_url, headers=headers, json=payload, timeout=10)
            if resp.status_code == 200:
                print(" ✅ OK")
                success_count += 1
            else:
                print(f" ❌ FAILED ({resp.status_code}: {resp.text[:50]})")
                fail_count += 1
        except Exception as e:
            print(f" ❌ ERROR ({type(e).__name__})")
            fail_count += 1

    print(f"\n{'='*30}")
    print(f"Results:")
    print(f"  ✅ Success: {success_count}")
    print(f"  ❌ Failed:  {fail_count}")
    print(f"{'='*30}")

if __name__ == "__main__":
    test_models()
