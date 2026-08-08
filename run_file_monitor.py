#!/usr/bin/env python3
"""
Simple monitoring script that logs file changes in the current directory.
Runs for a short duration (default 30s), detects create/modify/delete events
by hashing file contents, and writes a report to a log file.

Usage:
    python run_file_monitor.py [duration_seconds]
"""
import os
import sys
import time
import hashlib
import datetime

WATCH_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(WATCH_DIR, 'file_monitor_report.log')
SKIP = {os.path.basename(__file__), 'monitor.py', 'file_change_monitor.py', 'monitor_simple.py'}


def get_file_hash(path):
    try:
        with open(path, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()
    except (OSError, IOError):
        return None


def snapshot():
    state = {}
    for root, _dirs, files in os.walk(WATCH_DIR):
        # skip .git and __pycache__
        if '.git' in root or '__pycache__' in root or '.modelweaver' in root:
            continue
        for fn in files:
            if fn in SKIP:
                continue
            full = os.path.join(root, fn)
            state[full] = get_file_hash(full)
    return state


def main():
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    print(f"[{datetime.datetime.now().isoformat()}] Monitoring {WATCH_DIR}")
    print(f"[{datetime.datetime.now().isoformat()}] Duration: {duration}s (Ctrl+C to stop early)")

    baseline = snapshot()
    events = []
    start = time.time()

    try:
        while time.time() - start < duration:
            time.sleep(1)
            current = snapshot()

            for p, h in current.items():
                if p not in baseline:
                    events.append((datetime.datetime.now().isoformat(), 'CREATED', p))
                    print(f"  CREATED  {p}")
                elif baseline[p] != h:
                    events.append((datetime.datetime.now().isoformat(), 'MODIFIED', p))
                    print(f"  MODIFIED {p}")

            for p in list(baseline):
                if p not in current:
                    events.append((datetime.datetime.now().isoformat(), 'DELETED', p))
                    print(f"  DELETED  {p}")

            baseline = current
    except KeyboardInterrupt:
        print("Interrupted by user.")

    # Write report
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"\n=== File Monitor Session {datetime.datetime.now().isoformat()} ===\n")
        if events:
            for ts, kind, path in events:
                f.write(f"{ts} {kind} {path}\n")
        else:
            f.write("No file changes detected during this session.\n")
        f.write(f"Total events: {len(events)}\n")

    print(f"[{datetime.datetime.now().isoformat()}] Done. Report appended to {LOG_FILE}")
    print(f"[{datetime.datetime.now().isoformat()}] Total events: {len(events)}")


if __name__ == '__main__':
    main()