#!/usr/bin/env python3
"""run_bench_detached — Lance bench_swarm_live en processus détaché (double
fork, survivra au shell) et écrit un fichier PID + log.

Usage :
    python3 benchmarks/run_bench_detached.py [--timeout 3600] [--workspace mw-llm-code]
"""
import argparse
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--timeout", type=int, default=3600)
    p.add_argument("--workspace", default="mw-llm-code")
    p.add_argument("--pidfile", default="/tmp/bench_swarm_live.pid")
    p.add_argument("--log", default="/tmp/bench_live.log")
    args = p.parse_args()

    # Double fork pour se détacher du process group du shell.
    if os.fork() > 0:
        return 0
    os.setsid()
    if os.fork() > 0:
        sys.exit(0)
    # Redirection des fds vers le log.
    logf = open(args.log, "a", buffering=1)
    os.dup2(logf.fileno(), 1)
    os.dup2(logf.fileno(), 2)
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
    # PID file.
    try:
        Path(args.pidfile).write_text(str(os.getpid()))
    except Exception:
        pass
    # Lancement réel.
    from benchmarks.bench_swarm_live import main as bench_main
    sys.argv = ["bench_swarm_live.py",
                "--workspace", args.workspace,
                "--timeout", str(args.timeout)]
    rc = bench_main()
    sys.exit(rc)


if __name__ == "__main__":
    main()
