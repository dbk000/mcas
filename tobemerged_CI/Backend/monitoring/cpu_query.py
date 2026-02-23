#!/usr/bin/env python3
"""
MCAS CPU Query Tool
====================
Query CPU metrics from Redis. Designed for use by Claude Code or direct CLI.

Usage:
  python cpu_query.py now              # Current CPU snapshot
  python cpu_query.py summary          # Rolling summaries (5m/15m/1h)
  python cpu_query.py containers       # Per-container CPU breakdown
  python cpu_query.py alerts           # Current alert state
  python cpu_query.py history 300      # Last 5 minutes of history (JSON array)
  python cpu_query.py hotspots         # Top CPU consumers + per-core imbalance
  python cpu_query.py health           # Collector health check
  python cpu_query.py brief            # One-liner for embedding in prompts

All output is JSON to stdout for easy parsing.
"""

import json
import sys
import os
import time

import redis

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

rdb = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    password=REDIS_PASSWORD,
    decode_responses=True,
    socket_connect_timeout=5,
)


def cmd_now():
    """Current CPU snapshot."""
    raw = rdb.get("cpu:latest")
    if not raw:
        return {"error": "No data. Is cpu_collector running?"}
    return json.loads(raw)


def cmd_summary():
    """Rolling summaries."""
    result = {}
    for window in ["5m", "15m", "1h"]:
        raw = rdb.get(f"cpu:summary:{window}")
        if raw:
            result[window] = json.loads(raw)
        else:
            result[window] = {"error": "no data yet"}
    return result


def cmd_containers():
    """Per-container CPU breakdown."""
    raw = rdb.get("cpu:containers:latest")
    if not raw:
        return {"error": "No container data. Docker stats may be disabled."}
    return json.loads(raw)


def cmd_alerts():
    """Current alert state."""
    raw = rdb.get("cpu:alerts:latest")
    if not raw:
        return {"error": "No alert data."}
    return json.loads(raw)


def cmd_history(seconds=300):
    """Historical snapshots for the last N seconds."""
    now = time.time()
    cutoff = now - seconds
    members = rdb.zrangebyscore("cpu:history:index", cutoff, "+inf")
    snapshots = []
    for ts_key in members:
        raw = rdb.get(f"cpu:history:{ts_key}")
        if raw:
            snapshots.append(json.loads(raw))
    return {
        "window_seconds": seconds,
        "samples": len(snapshots),
        "data": snapshots,
    }


def cmd_hotspots():
    """Identify CPU hotspots: top containers + per-core imbalance."""
    result = {}

    # Per-core analysis
    raw = rdb.get("cpu:latest")
    if raw:
        snap = json.loads(raw)
        cores = snap.get("per_core", {})
        if cores:
            usages = [v["usage_pct"] for v in cores.values()]
            result["core_analysis"] = {
                "num_cores": len(usages),
                "per_core_usage": {k: v["usage_pct"] for k, v in cores.items()},
                "max_core_pct": max(usages),
                "min_core_pct": min(usages),
                "spread_pct": round(max(usages) - min(usages), 2),
                "imbalanced": (max(usages) - min(usages)) > 30,
            }
        result["aggregate"] = snap.get("aggregate", {})
        result["load_average"] = snap.get("load_average", {})

    # Top containers
    raw = rdb.get("cpu:containers:latest")
    if raw:
        cdata = json.loads(raw)
        result["top_containers"] = cdata.get("containers", [])[:5]
        result["total_container_cpu_pct"] = cdata.get("total_container_cpu_pct", 0)

    return result


def cmd_health():
    """Check if collector is running and data is fresh."""
    raw = rdb.get("cpu:latest")
    if not raw:
        return {"status": "DEAD", "message": "No cpu:latest key found in Redis."}

    snap = json.loads(raw)
    epoch = snap.get("epoch", 0)
    age = time.time() - epoch

    if age > 30:
        return {
            "status": "STALE",
            "last_update_age_seconds": round(age, 1),
            "message": f"Last update was {round(age)}s ago. Collector may be down.",
        }

    return {
        "status": "OK",
        "last_update_age_seconds": round(age, 1),
        "num_cores": snap.get("num_cores"),
        "current_usage_pct": snap.get("aggregate", {}).get("usage_pct"),
    }


def cmd_brief():
    """One-liner summary for embedding in Claude Code context."""
    raw = rdb.get("cpu:latest")
    if not raw:
        return "CPU: NO DATA (collector not running)"

    snap = json.loads(raw)
    agg = snap.get("aggregate", {})
    load = snap.get("load_average", {})
    cores = snap.get("num_cores", "?")

    parts = [
        f"CPU: {agg.get('usage_pct', '?')}%",
        f"(usr:{agg.get('user_pct', '?')}% sys:{agg.get('system_pct', '?')}% steal:{agg.get('steal_pct', '?')}%)",
        f"load:{load.get('1m', '?')}/{load.get('5m', '?')}/{load.get('15m', '?')}",
        f"cores:{cores}",
    ]

    # Add top container if available
    craw = rdb.get("cpu:containers:latest")
    if craw:
        cdata = json.loads(craw)
        containers = cdata.get("containers", [])
        if containers and containers[0]["cpu_pct"] > 5:
            parts.append(f"top:{containers[0]['name']}@{containers[0]['cpu_pct']}%")

    return " | ".join(parts)


# ---------------------------------------------------------------------------
# CLI dispatch
# ---------------------------------------------------------------------------
COMMANDS = {
    "now": cmd_now,
    "summary": cmd_summary,
    "containers": cmd_containers,
    "alerts": cmd_alerts,
    "history": cmd_history,
    "hotspots": cmd_hotspots,
    "health": cmd_health,
    "brief": cmd_brief,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: cpu_query.py <command> [args]")
        print(f"Commands: {', '.join(COMMANDS.keys())}")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(json.dumps({"error": f"Unknown command: {cmd}. Available: {list(COMMANDS.keys())}"}))
        sys.exit(1)

    try:
        if cmd == "history" and len(sys.argv) > 2:
            result = COMMANDS[cmd](int(sys.argv[2]))
        else:
            result = COMMANDS[cmd]()

        if isinstance(result, str):
            print(result)
        else:
            print(json.dumps(result, indent=2))
    except redis.ConnectionError:
        print(json.dumps({"error": "Cannot connect to Redis", "host": REDIS_HOST, "port": REDIS_PORT}))
        sys.exit(1)


if __name__ == "__main__":
    main()
