"""IPC via socket Unix pour l'Agent Framework Daemon.

Protocol : JSON ligne par ligne. Une ligne = un message.
  Requête  : {"call": ..., "id": "req-1"} → le serveur répond
  Réponse  : {"ok": true/false, "result": {...}, "id": "req-1"}

Usage serveur (AFD) :
    sock = AFDSocketServer("/path/to/sock")
    sock.serve(handler_fn)  # handler_fn(msg) -> response dict

Usage client (gateway) :
    client = AFDSocketClient("/path/to/sock")
    resp = client.send({"call": ...})
"""

import json
import os
import socket
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional


SOCK_PATH = None  # set at daemon startup


def afd_socket_path() -> str:
    from services._common import mw_home
    return str(mw_home() / "afd.sock")


class AFDSocketServer:
    """Serveur IPC : écoute sur un socket Unix, dispatche les requêtes."""

    def __init__(self, sock_path: Optional[str] = None):
        self.path = sock_path or afd_socket_path()
        self._sock: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def serve(self, handler: Callable[[Dict[str, Any]], Dict[str, Any]]):
        """Bloque sur accept(), délègue chaque connexion à handler()."""
        self._running = True
        Path(self.path).unlink(missing_ok=True)
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(self.path)
        os.chmod(self.path, 0o600)
        self._sock.listen(5)
        self._sock.settimeout(1.0)
        while self._running:
            try:
                conn, _ = self._sock.accept()
                threading.Thread(target=self._handle, args=(conn, handler),
                                 daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def serve_async(self, handler: Callable[[Dict[str, Any]], Dict[str, Any]]):
        """Lance serve() dans un thread daemon, retourne immédiatement."""
        self._thread = threading.Thread(target=self.serve, args=(handler,),
                                        daemon=True)
        self._thread.start()

    def _handle(self, conn: socket.socket, handler):
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
                        resp = handler(req)
                    payload = json.dumps(resp).encode("utf-8") + b"\n"
                    conn.sendall(payload)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        Path(self.path).unlink(missing_ok=True)


class AFDSocketClient:
    """Client IPC : envoie une requête via socket Unix et lit la réponse."""

    def __init__(self, sock_path: Optional[str] = None, timeout: float = 60.0):
        self.path = sock_path or afd_socket_path()
        self.timeout = timeout

    def send(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Envoie une requête JSON et retourne la réponse (bloquant)."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.path)
            payload = json.dumps(req).encode("utf-8") + b"\n"
            sock.sendall(payload)
            buf = b""
            while b"\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    return {"ok": False, "error": "connection_closed", "id": req.get("id")}
                buf += chunk
            line = buf.split(b"\n", 1)[0]
            return json.loads(line.decode("utf-8"))
        except FileNotFoundError:
            return {"ok": False, "error": "afd_not_running", "id": req.get("id")}
        except (ConnectionRefusedError, OSError) as e:
            return {"ok": False, "error": f"afd_unreachable: {e}", "id": req.get("id")}
        except Exception as e:
            return {"ok": False, "error": str(e), "id": req.get("id")}
        finally:
            sock.close()