import os
import signal
import subprocess
import resource
from typing import Optional, Tuple


class SandboxError(RuntimeError):
    pass


class Sandbox:
    def __init__(self, max_mem_mb: int = 512, max_fsize_mb: int = 10,
                 default_timeout: int = 30):
        self.max_mem_mb = max_mem_mb
        self.max_fsize_mb = max_fsize_mb
        self.default_timeout = default_timeout

    def run(self, command: str, *, timeout: Optional[int] = None,
            cwd: Optional[str] = None, shell: bool = True,
            max_output_chars: int = 100_000) -> Tuple[str, str, int]:
        timeout = timeout or self.default_timeout

        def _set_limits():
            mem = self.max_mem_mb * 1024 * 1024
            fsize = self.max_fsize_mb * 1024 * 1024
            for rlim, val in [
                (resource.RLIMIT_AS, mem),
                (resource.RLIMIT_FSIZE, fsize),
                (resource.RLIMIT_NOFILE, 64),
            ]:
                try:
                    soft, hard = resource.getrlimit(rlim)
                    if hard != resource.RLIM_INFINITY:
                        val = min(val, hard)
                    resource.setrlimit(rlim, (val, hard))
                except Exception:
                    pass
            try:
                soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
                cpu_val = timeout + 5
                if hard != resource.RLIM_INFINITY:
                    cpu_val = min(cpu_val, hard)
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_val, hard))
            except Exception:
                pass

        try:
            proc = subprocess.Popen(
                command if isinstance(command, list) else command,
                shell=shell if isinstance(command, str) else False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                preexec_fn=_set_limits,
                start_new_session=True,
                # no stdin
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError as e:
            raise SandboxError(f"Commande introuvable : {e}") from e
        except PermissionError as e:
            raise SandboxError(f"Permission refusée : {e}") from e

        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait()
            raise SandboxError(
                f"Timeout après {timeout}s"
            ) from None

        stdout_str = stdout.decode("utf-8", errors="replace")[:max_output_chars]
        stderr_str = stderr.decode("utf-8", errors="replace")[:max_output_chars]
        return stdout_str, stderr_str, proc.returncode
