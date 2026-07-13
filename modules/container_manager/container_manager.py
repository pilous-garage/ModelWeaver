import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional

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
