#!/usr/bin/env python3
"""Test script for the system metrics collector module."""
import sys
sys.path.insert(0, '/home/pierreloup2/.modelweaver/agent_home/413/workspace/mw-swarm')

from modules.system.metrics_collector import (
    SystemMetricsCollector,
    CPUMetrics,
    MemoryMetrics,
    ProcessMetrics,
    SystemMetricsSnapshot,
    get_system_metrics_collector,
    collect_cpu,
    collect_memory,
    collect_processes,
    collect_snapshot,
    snapshot_to_dict,
    snapshot_to_text,
)

print("=== Testing System Metrics Collector ===\n")

c = SystemMetricsCollector()
print(f"psutil available: {c.is_available}")

print("\n--- CPU Metrics ---")
cpu = collect_cpu()
print(f"  CPU percent: {cpu.cpu_percent}%")
print(f"  Physical cores: {cpu.cpu_count_physical}")
print(f"  Logical cores: {cpu.cpu_count_logical}")
print(f"  Load avg (1/5/15m): {cpu.load_avg_1m}, {cpu.load_avg_5m}, {cpu.load_avg_15m}")

print("\n--- Memory Metrics ---")
mem = collect_memory()
print(f"  Total: {mem.total_gb} GB")
print(f"  Used: {mem.used_gb} GB ({mem.percent_used}%)")
print(f"  Available: {mem.available_gb} GB")
print(f"  Swap total: {mem.swap_total_gb} GB")
print(f"  Swap used: {mem.swap_used_gb} GB ({mem.swap_percent_used}%)")

print("\n--- Top Processes ---")
procs = collect_processes(top_n=5)
print(f"  Found {len(procs)} processes")
for p in procs:
    print(f"  PID={p.pid} NAME={p.name} CPU={p.cpu_percent}% RSS={p.memory_rss_mb}MB")

print("\n--- Snapshot Dict ---")
d = snapshot_to_dict(top_n=3)
print(f"  Keys: {list(d.keys())}")
print(f"  CPU: {d['cpu']['cpu_percent']}%")
print(f"  Memory: {d['memory']['percent_used']}%")
print(f"  Process count: {d['process_count']}")

print("\n--- Snapshot Text ---")
text = snapshot_to_text(top_n=3)
print(text)

print("\n=== All Tests Passed ===")
