import sys
import json
import subprocess
from pathlib import Path


def list_models():
    try:
        r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return {"status": "success", "models": []}
        lines = r.stdout.strip().split("\n")
        models = []
        for line in lines[1:]:
            parts = line.split()
            if not parts:
                continue
            models.append({
                "name": parts[0],
                "size": parts[2] if len(parts) > 2 else "",
                "modified": parts[3] if len(parts) > 3 else "",
            })
        return {"status": "success", "models": models}
    except FileNotFoundError:
        return {"status": "success", "models": []}
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "ollama list timed out"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def pull_model(model_name: str, progress_callback=None):
    try:
        process = subprocess.Popen(
            ["ollama", "pull", model_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            if progress_callback:
                progress_callback(line)
        process.wait()
        if process.returncode == 0:
            return {"status": "success", "message": f"{model_name} pulled"}
        return {"status": "error", "error": f"ollama pull failed (exit {process.returncode})"}
    except FileNotFoundError:
        return {"status": "error", "error": "ollama not found"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def remove_model(model_name: str):
    try:
        r = subprocess.run(["ollama", "rm", model_name], capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            return {"status": "success", "message": f"{model_name} removed"}
        return {"status": "error", "error": r.stderr.strip() or f"ollama rm failed (exit {r.returncode})"}
    except FileNotFoundError:
        return {"status": "error", "error": "ollama not found"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def main():
    if len(sys.argv) < 2:
        print(json.dumps(list_models()), flush=True)
        return

    cmd = sys.argv[1]
    if cmd == "list":
        print(json.dumps(list_models()), flush=True)
    elif cmd == "pull" and len(sys.argv) >= 3:
        model_name = sys.argv[2]
        streaming = "--stream" in sys.argv
        if streaming:
            def cb(msg):
                line = json.dumps({"type": "progress", "message": msg})
                print(line, flush=True)
            result = pull_model(model_name, cb)
            print(json.dumps({"type": "result", **result}), flush=True)
        else:
            result = pull_model(model_name)
            print(json.dumps(result), flush=True)
    elif cmd == "rm" and len(sys.argv) >= 3:
        model_name = sys.argv[2]
        print(json.dumps(remove_model(model_name)), flush=True)
    else:
        print(json.dumps({"status": "error", "error": f"Unknown command: {cmd}"}), flush=True)


if __name__ == "__main__":
    main()
