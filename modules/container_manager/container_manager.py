import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

class ContainerManager:
    def __init__(self, image: str = "python:3.12-slim"):
        self.image = image

    def run_command(self, command: List[str], volume_mounts: Optional[Dict[str, str]] = None) -> str:
        """Runs a command inside a temporary Docker container."""
        docker_cmd = ["docker", "run", "--rm"]

        if volume_mounts:
            for host_path, container_path in volume_mounts.items():
                docker_cmd.extend(["-v", f"{host_path}:{container_path}"])

        docker_cmd.append(self.image)
        docker_cmd.extend(command)

        try:
            result = subprocess.run(docker_cmd, capture_output=True, text=True, check=True)
            return result.stdout
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Docker command failed: {e.stderr}")

    def list_images(self) -> List[str]:
        """Lists available Docker images."""
        try:
            result = subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], capture_output=True, text=True, check=True)
            return result.stdout.strip().split('\n')
        except subprocess.CalledProcessError:
            return []

    # ── Conteneurs persistants / nommés ──────────────────────────
    # Ces opérations ne passent PAS par `--rm` : le conteneur survit
    # entre les appels (batteries de tests sur un même environnement).

    def create(self, name: str, image: Optional[str] = None,
               volumes: Optional[Dict[str, str]] = None,
               env: Optional[Dict[str, str]] = None,
               workdir: Optional[str] = None) -> Tuple[bool, str]:
        """Crée un conteneur nommé (sans le démarrer). Retourne (ok, message)."""
        image = image or self.image
        cmd = ["docker", "create", "--name", name]
        if workdir:
            cmd.extend(["-w", workdir])
        for k, v in (env or {}).items():
            cmd.extend(["-e", f"{k}={v}"])
        for host_path, ctr_path in (volumes or {}).items():
            cmd.extend(["-v", f"{host_path}:{ctr_path}"])
        cmd.append(image)
        cmd.append("tail")
        cmd.append("-f")
        cmd.append("/dev/null")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                return True, r.stdout.strip()
            return False, r.stderr.strip()
        except Exception as e:
            return False, str(e)

    def start(self, name: str) -> Tuple[bool, str]:
        """Démarre un conteneur nommé existant."""
        try:
            r = subprocess.run(["docker", "start", name],
                               capture_output=True, text=True, timeout=60)
            return (r.returncode == 0), (r.stderr or r.stdout).strip()
        except Exception as e:
            return False, str(e)

    def exec(self, name: str, command: List[str],
             volumes: Optional[Dict[str, str]] = None,
             workdir: Optional[str] = None,
             timeout: int = 600) -> Dict[str, Any]:
        """Exécute une commande dans un conteneur DÉJÀ démarré (persistant).

        Retourne {ok, exit_code, stdout, stderr}.
        """
        cmd = ["docker", "exec"]
        if workdir:
            cmd.extend(["-w", workdir])
        cmd.append(name)
        cmd.extend(command)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return {
                "ok": proc.returncode == 0,
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "exit_code": 124,
                    "stdout": "", "stderr": f"timeout ({timeout}s)"}
        except Exception as e:
            return {"ok": False, "exit_code": -1,
                    "stdout": "", "stderr": str(e)}

    def commit(self, name: str, image_tag: str) -> Tuple[bool, str]:
        """Enregistre l'état courant d'un conteneur comme image (cache)."""
        try:
            r = subprocess.run(["docker", "commit", name, image_tag],
                               capture_output=True, text=True, timeout=120)
            return (r.returncode == 0), (r.stderr or r.stdout).strip()
        except Exception as e:
            return False, str(e)

    def stop(self, name: str, timeout: int = 30) -> Tuple[bool, str]:
        """Arrête un conteneur (sans le supprimer)."""
        try:
            r = subprocess.run(["docker", "stop", "-t", str(timeout), name],
                               capture_output=True, text=True, timeout=timeout + 15)
            return (r.returncode == 0), (r.stderr or r.stdout).strip()
        except Exception as e:
            return False, str(e)

    def remove(self, name: str, force: bool = True) -> Tuple[bool, str]:
        """Supprime un conteneur."""
        cmd = ["docker", "rm"]
        if force:
            cmd.append("-f")
        cmd.append(name)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            return (r.returncode == 0), (r.stderr or r.stdout).strip()
        except Exception as e:
            return False, str(e)

    def exists(self, name: str) -> bool:
        """Vrai si un conteneur nommé existe (démarré ou non)."""
        try:
            r = subprocess.run(["docker", "inspect", "-f", "{{.Id}}", name],
                               capture_output=True, text=True, timeout=30)
            return r.returncode == 0
        except Exception:
            return False

    def status(self, name: str) -> str:
        """État du conteneur (running/exited/absent)."""
        try:
            r = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Status}}", name],
                capture_output=True, text=True, timeout=30)
            return r.stdout.strip() if r.returncode == 0 else "absent"
        except Exception:
            return "absent"

    def image_exists(self, image_tag: str) -> bool:
        """Vrai si une image existe localement."""
        return image_tag in self.list_images()


if __name__ == "__main__":
    # Quick test
    cm = ContainerManager()
    print("Available images:", cm.list_images())
    try:
        print("Running 'echo hello' in container...")
        output = cm.run_command(["echo", "hello"])
        print(f"Output: {output.strip()}")
    except Exception as e:
        print(f"Error: {e}")
