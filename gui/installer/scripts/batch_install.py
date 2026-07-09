import sys
import json
import signal
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent.parent.parent))

from modules.installer.installer import Installer
from sql.db import ModelWeaverDB


DEFAULT_TIMEOUT = 300


class TimeoutError(Exception):
    pass


def progress_callback(percent: int, message: str):
    line = json.dumps({"type": "progress", "percent": percent, "message": message})
    print(line, flush=True)


def main():
    args = sys.argv[1:]
    timeout = DEFAULT_TIMEOUT
    refs = []

    for arg in args:
        if arg.startswith("--timeout="):
            timeout = int(arg.split("=", 1)[1])
        elif arg.startswith("--"):
            continue
        else:
            refs.append(arg)

    if not refs:
        print(json.dumps({"type": "result", "status": "error",
                          "error": "No tool refs provided"}), flush=True)
        return

    db = ModelWeaverDB()
    installer = Installer()
    results = []

    for i, tool_ref in enumerate(refs):
        progress_callback(0, f"[{i+1}/{len(refs)}] Installation de {tool_ref}...")

        def handler(signum, frame):
            raise TimeoutError(f"Installation de {tool_ref} a dépassé {timeout}s")

        signal.signal(signal.SIGALRM, handler)
        signal.alarm(timeout)

        try:
            tool = db.tools.get(tool_ref)
            if not tool:
                results.append({"ref": tool_ref, "status": "error",
                                "error": "Not found in catalog"})
                progress_callback(100, f"[{i+1}/{len(refs)}] {tool_ref} : introuvable")
                signal.alarm(0)
                continue

            success = installer.install(tool, progress_callback)
            signal.alarm(0)

            if success:
                db.tools.scan_installed(db.local_tools)
                db.commit()
                results.append({"ref": tool_ref, "status": "success"})
                progress_callback(100, f"[{i+1}/{len(refs)}] {tool_ref} : ✅ installé")
            else:
                results.append({"ref": tool_ref, "status": "error",
                                "error": "Install returned False"})
                progress_callback(100, f"[{i+1}/{len(refs)}] {tool_ref} : ❌ échec")

        except TimeoutError as e:
            signal.alarm(0)
            results.append({"ref": tool_ref, "status": "error",
                            "error": str(e)})
            progress_callback(100, f"[{i+1}/{len(refs)}] {tool_ref} : ⏱ timeout")
        except Exception as e:
            signal.alarm(0)
            results.append({"ref": tool_ref, "status": "error",
                            "error": str(e)})
            progress_callback(100, f"[{i+1}/{len(refs)}] {tool_ref} : ❌ {str(e)}")

    db.close()
    print(json.dumps({"type": "result", "status": "success", "data": results}), flush=True)


if __name__ == "__main__":
    main()