#!/usr/bin/env python3
"""
Profile sim_d1_matrix.main() under cProfile.

Writes:
  - .prof binary (for snakeviz / pstats interactive)
  - .txt sorted text summary (top 60 by cumulative + top 60 by tottime)

Usage:
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/profile_sim_d1.py \
            --batch-id 'backfill_20260303_D1v1%%' \
            --position-usd 1000

Output files logged at script start.
"""

import cProfile
import os
import pstats
import sys
import io
from datetime import datetime, timezone

# Resolve output dir
if os.path.isdir("/scripts/output"):
    OUTPUT_DIR = "/scripts/output"
elif os.path.isdir(os.path.join(os.path.dirname(__file__), "output")):
    OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
else:
    OUTPUT_DIR = "/scripts/output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

ts_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
prof_path = os.path.join(OUTPUT_DIR, f"profile_sim_d1_{ts_tag}.prof")
txt_path = os.path.join(OUTPUT_DIR, f"profile_sim_d1_{ts_tag}.txt")

print(f"Profile binary: {prof_path}")
print(f"Profile text:   {txt_path}")

# Import sim_d1_matrix.main — resolve paths same way the script does
if os.path.isdir("/app"):
    sys.path.insert(0, "/app")
    sys.path.insert(0, "/scripts")

from sim_d1_matrix import main as sim_main

# Run under cProfile
profiler = cProfile.Profile()
profiler.enable()
try:
    sim_main()
finally:
    profiler.disable()

# Save binary .prof
profiler.dump_stats(prof_path)

# Save text summary
buf = io.StringIO()
stats = pstats.Stats(profiler, stream=buf)

buf.write("=" * 90 + "\n")
buf.write("TOP 60 BY CUMULATIVE TIME\n")
buf.write("=" * 90 + "\n")
stats.sort_stats("cumulative")
stats.print_stats(60)

buf.write("\n" + "=" * 90 + "\n")
buf.write("TOP 60 BY TOTAL TIME (self)\n")
buf.write("=" * 90 + "\n")
stats.sort_stats("tottime")
stats.print_stats(60)

buf.write("\n" + "=" * 90 + "\n")
buf.write("CALLERS OF check_exit_simple\n")
buf.write("=" * 90 + "\n")
stats.print_callers("check_exit_simple")

buf.write("\n" + "=" * 90 + "\n")
buf.write("CALLERS OF simulate_opportunity\n")
buf.write("=" * 90 + "\n")
stats.print_callers("simulate_opportunity")

buf.write("\n" + "=" * 90 + "\n")
buf.write("CALLERS OF PositionState.__init__\n")
buf.write("=" * 90 + "\n")
stats.print_callers("__init__")

with open(txt_path, "w") as f:
    f.write(buf.getvalue())

print(f"\nProfile complete.")
print(f"  Binary: {prof_path}")
print(f"  Text:   {txt_path}")

# Print key hotspots to console
print("\n" + "=" * 70)
print("KEY HOTSPOTS (top 20 by tottime)")
print("=" * 70)
stats2 = pstats.Stats(profiler, stream=sys.stdout)
stats2.sort_stats("tottime")
stats2.print_stats(20)
