import subprocess
from typing import List, Dict, Any, Optional
from modules.container_manager.container_manager import ContainerManager

class TestRunner:
    def __init__(self, container_manager: ContainerManager):
        self.container_manager = container_manager

    def run_test_script(self, script_content: str, working_dir: Optional[str] = None) -> Dict[str, Any]:
        """Runs a provided script content in a container and returns the result."""
        # For simplicity, we'll write the script to a file in the host,
        # then mount it and run it.
        import tempfile
        import os
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            script_file = tmp_path / "test_script.py"
            script_file.write_text(script_content)
            
            mounts = {str(tmp_path): "/app"}
            
            try:
                # We use python3 to run the script
                output = self.container_manager.run_command(["python3", "/app/test_script.py"], volume_mounts=mounts)
                return {
                    "success": True,
                    "output": output,
                    "error": None
                }
            except Exception as e:
                return {
                    "success": False,
                    "output": "",
                    "error": str(e)
                }

if __name__ == "__main__":
    # Quick test
    from modules.container_manager.container_manager import ContainerManager
    cm = ContainerManager()
    tr = TestRunner(cm)
    
    script = "print('Hello from container!')"
    result = tr.run_test_script(script)
    print(f"Result: {result}")
