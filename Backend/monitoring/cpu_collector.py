#!/usr/bin/env python3
"""
MCAS VPS CPU Metrics Collector
===============================
Lightweight daemon that samples /proc/stat and Docker container stats,
writes structured metrics to Redis for direct querying by Claude Code.

Redis Key Schema:
  cpu:latest              → Most recent snapshot (JSON)
  cpu:history:{timestamp} → Historical snapshots (TTL-managed)
  cpu:containers:latest   → Per-container CPU breakdown (JSON)
  cpu:alerts:latest       → Current alert state (JSON)
  cpu:summary:5m          → 5-minute rolling summary
  cpu:summary:15m         → 15-minute rolling summary
  cpu:summary:1h          → 1-hour rolling summary

Runs inside Docker with host /proc mounted at /host/proc.
Reads HOST_PROC env var (default: /proc) for portability.
"""

import json
import time
import os
import signal
import sys
import subprocess
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Configuration (env-overridable)
# ---------------------------------------------------------------------------
PROC_PATH = os.getenv("HOST_PROC", "/proc")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)
SAMPLE_INTERVAL = int(os.getenv("CPU_SAMPLE_INTERVAL", "5"))  # seconds
HISTORY_TTL = int(os.getenv("CPU_HISTORY_TTL", "86400"))  # 24 hours
DOCKER_STATS = os.getenv("CPU_DOCKER_STATS", "true").lower() == "true"
ALERT_THRESHOLD = float(os.getenv("CPU_ALERT_THRESHOLD", "85.0"))  # percent
ALERT_SUSTAINED_SECONDS = int(os.getenv("CPU_ALERT_SUSTAINED", "30"))

# Summary windows in seconds
SUMMARY_WINDOWS = {"5m": 300, "15m": 900, "1h": 3600}

# ---------------------------------------------------------------------------
# Redis connection (lazy import to allow graceful failure messaging)
# ---------------------------------------------------------------------------
try:
    import redis
except ImportError:
    print("ERROR: redis package not installed. Run: pip install redis")
    sys.exit(1)

rdb = None
running = True


def connect_redis():
    global rdb
    rdb = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    rdb.ping()
    print(f"[cpu_collector] Connected to Redis at {REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}")


def signal_handler(sig, frame):
    global running
    print(f"\n[cpu_collector] Received signal {sig}, shutting down...")
    running = False


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


# ---------------------------------------------------------------------------
# /proc/stat parser
# ---------------------------------------------------------------------------
def read_proc_stat():
    """Read /proc/stat and return per-CPU jiffy counters."""
    cpus = {}
    stat_path = os.path.join(PROC_PATH, "stat")
    with open(stat_path, "r") as f:
        for line in f:
            if line.startswith("cpu"):
                parts = line.split()
                name = parts[0]  # "cpu" (aggregate) or "cpu0", "cpu1", ...
                # Fields: user, nice, system, idle, iowait, irq, softirq, steal
                values = [int(x) for x in parts[1:9]]
                cpus[name] = {
                    "user": values[0],
                    "nice": values[1],
                    "system": values[2],
                    "idle": values[3],
                    "iowait": values[4] if len(values) > 4 else 0,
                    "irq": values[5] if len(values) > 5 else 0,
                    "softirq": values[6] if len(values) > 6 else 0,
                    "steal": values[7] if len(values) > 7 else 0,
                }
    return cpus


def compute_usage(prev, curr):
    """Compute CPU usage percentages from two jiffy snapshots."""
    results = {}
    for name in curr:
        if name not in prev:
            continue
        p, c = prev[name], curr[name]
        total_prev = sum(p.values())
        total_curr = sum(c.values())
        total_delta = total_curr - total_prev
        if total_delta == 0:
            results[name] = {
                "usage_pct": 0.0, "user_pct": 0.0, "system_pct": 0.0,
                "iowait_pct": 0.0, "steal_pct": 0.0, "idle_pct": 100.0,
            }
            continue

        idle_delta = c["idle"] - p["idle"]
        user_delta = (c["user"] + c["nice"]) - (p["user"] + p["nice"])
        system_delta = (c["system"] + c["irq"] + c["softirq"]) - (
            p["system"] + p["irq"] + p["softirq"]
        )
        iowait_delta = c["iowait"] - p["iowait"]
        steal_delta = c["steal"] - p["steal"]

        results[name] = {
            "usage_pct": round((1 - idle_delta / total_delta) * 100, 2),
            "user_pct": round(user_delta / total_delta * 100, 2),
            "system_pct": round(system_delta / total_delta * 100, 2),
            "iowait_pct": round(iowait_delta / total_delta * 100, 2),
            "steal_pct": round(steal_delta / total_delta * 100, 2),
            "idle_pct": round(idle_delta / total_delta * 100, 2),
        }
    return results


# ---------------------------------------------------------------------------
# Docker container stats (optional)
# ---------------------------------------------------------------------------
def get_docker_stats():
    """Get per-container CPU usage via Docker CLI (avoids Docker SDK dep)."""
    try:
        result = subprocess.run(
            [
                "docker", "stats", "--no-stream",
                "--format", '{"name":"{{.Name}}","cpu":"{{.CPUPerc}}","mem":"{{.MemPerc}}","mem_usage":"{{.MemUsage}}","pids":"{{.PIDs}}"}'
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None

        containers = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                c = json.loads(line)
                c["cpu_pct"] = float(c["cpu"].rstrip("%"))
                c["mem_pct"] = float(c["mem"].rstrip("%"))
                del c["cpu"], c["mem"]
                containers.append(c)
            except (json.JSONDecodeError, ValueError):
                continue

        # Sort by CPU usage descending
        containers.sort(key=lambda x: x["cpu_pct"], reverse=True)
        return containers
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


# ---------------------------------------------------------------------------
# Load average
# ---------------------------------------------------------------------------
def get_load_average():
    """Read /proc/loadavg."""
    loadavg_path = os.path.join(PROC_PATH, "loadavg")
    with open(loadavg_path, "r") as f:
        parts = f.read().split()
    return {
        "1m": float(parts[0]),
        "5m": float(parts[1]),
        "15m": float(parts[2]),
        "running_procs": parts[3],  # e.g., "3/412"
    }


def get_num_cores():
    """Count CPU cores from /proc/stat."""
    count = 0
    stat_path = os.path.join(PROC_PATH, "stat")
    with open(stat_path, "r") as f:
        for line in f:
            if line.startswith("cpu") and line[3:4].isdigit():
                count += 1
    return count


# ---------------------------------------------------------------------------
# Alert state tracking
# ---------------------------------------------------------------------------
alert_state = {
    "triggered": False,
    "sustained_since": None,
    "peak_usage": 0.0,
}


def check_alerts(usage_pct, timestamp):
    global alert_state
    if usage_pct >= ALERT_THRESHOLD:
        if alert_state["sustained_since"] is None:
            alert_state["sustained_since"] = timestamp
        duration = timestamp - alert_state["sustained_since"]
        alert_state["peak_usage"] = max(alert_state["peak_usage"], usage_pct)
        if duration >= ALERT_SUSTAINED_SECONDS:
            alert_state["triggered"] = True
    else:
        alert_state["triggered"] = False
        alert_state["sustained_since"] = None
        alert_state["peak_usage"] = 0.0

    return {
        "alert_active": alert_state["triggered"],
        "threshold_pct": ALERT_THRESHOLD,
        "current_pct": usage_pct,
        "peak_pct": alert_state["peak_usage"],
        "sustained_seconds": (
            int(timestamp - alert_state["sustained_since"])
            if alert_state["sustained_since"]
            else 0
        ),
    }


# ---------------------------------------------------------------------------
# Rolling summary computation
# ---------------------------------------------------------------------------
def update_summaries(timestamp):
    """Compute rolling summaries from recent history keys."""
    pipe = rdb.pipeline()

    for label, window_secs in SUMMARY_WINDOWS.items():
        cutoff = timestamp - window_secs
        members = rdb.zrangebyscore("cpu:history:index", cutoff, "+inf")

        if not members:
            pipe.set(
                f"cpu:summary:{label}",
                json.dumps({"error": "no data", "window": label}),
            )
            continue

        usages = []
        for ts_key in members:
            raw = rdb.get(f"cpu:history:{ts_key}")
            if raw:
                try:
                    snap = json.loads(raw)
                    usages.append(snap["aggregate"]["usage_pct"])
                except (json.JSONDecodeError, KeyError):
                    continue

        if usages:
            summary = {
                "window": label,
                "window_seconds": window_secs,
                "samples": len(usages),
                "mean_pct": round(sum(usages) / len(usages), 2),
                "max_pct": round(max(usages), 2),
                "min_pct": round(min(usages), 2),
                "p95_pct": round(
                    sorted(usages)[int(len(usages) * 0.95)], 2
                ) if len(usages) >= 2 else round(max(usages), 2),
                "latest_pct": round(usages[-1], 2),
                "computed_at": datetime.fromtimestamp(
                    timestamp, tz=timezone.utc
                ).isoformat(),
            }
            pipe.set(f"cpu:summary:{label}", json.dumps(summary))

    pipe.execute()


# ---------------------------------------------------------------------------
# Main collection loop
# ---------------------------------------------------------------------------
def main():
    connect_redis()
    num_cores = get_num_cores()
    print(f"[cpu_collector] PROC_PATH={PROC_PATH}")
    print(f"[cpu_collector] Detected {num_cores} CPU cores")
    print(f"[cpu_collector] Sample interval: {SAMPLE_INTERVAL}s | History TTL: {HISTORY_TTL}s")
    print(f"[cpu_collector] Docker stats: {'enabled' if DOCKER_STATS else 'disabled'}")
    print(f"[cpu_collector] Alert threshold: {ALERT_THRESHOLD}% sustained for {ALERT_SUSTAINED_SECONDS}s")

    prev_stat = read_proc_stat()
    time.sleep(1)  # Need at least one delta to compute usage

    summary_counter = 0

    while running:
        try:
            timestamp = time.time()
            ts_iso = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
            ts_int = int(timestamp)

            # CPU usage
            curr_stat = read_proc_stat()
            usage = compute_usage(prev_stat, curr_stat)
            prev_stat = curr_stat

            # Load average
            loadavg = get_load_average()

            # Build snapshot
            snapshot = {
                "timestamp": ts_iso,
                "epoch": ts_int,
                "num_cores": num_cores,
                "aggregate": usage.get("cpu", {}),
                "per_core": {k: v for k, v in usage.items() if k != "cpu"},
                "load_average": loadavg,
            }

            # Docker container stats (every other cycle to reduce overhead)
            containers = None
            if DOCKER_STATS and summary_counter % 2 == 0:
                containers = get_docker_stats()

            # Alert check
            agg_usage = usage.get("cpu", {}).get("usage_pct", 0)
            alert_info = check_alerts(agg_usage, timestamp)

            # Write to Redis (pipeline for atomicity)
            pipe = rdb.pipeline()

            # Latest snapshot
            pipe.set("cpu:latest", json.dumps(snapshot))

            # Historical snapshot with TTL
            history_key = f"cpu:history:{ts_int}"
            pipe.set(history_key, json.dumps(snapshot), ex=HISTORY_TTL)

            # Sorted set index for efficient range queries
            pipe.zadd("cpu:history:index", {str(ts_int): timestamp})
            # Trim old entries from index
            pipe.zremrangebyscore(
                "cpu:history:index", "-inf", timestamp - HISTORY_TTL
            )

            # Container stats
            if containers is not None:
                pipe.set("cpu:containers:latest", json.dumps({
                    "timestamp": ts_iso,
                    "containers": containers,
                    "total_container_cpu_pct": round(
                        sum(c["cpu_pct"] for c in containers), 2
                    ),
                }))

            # Alert state
            pipe.set("cpu:alerts:latest", json.dumps(alert_info))

            pipe.execute()

            # Update summaries every 6 cycles (~30s at 5s interval)
            summary_counter += 1
            if summary_counter % 6 == 0:
                update_summaries(timestamp)

        except redis.ConnectionError as e:
            print(f"[cpu_collector] Redis connection lost: {e}. Reconnecting...")
            time.sleep(5)
            try:
                connect_redis()
            except Exception:
                pass
        except Exception as e:
            print(f"[cpu_collector] Error: {e}")

        # Sleep for remaining interval
        elapsed = time.time() - timestamp
        sleep_time = max(0, SAMPLE_INTERVAL - elapsed)
        time.sleep(sleep_time)

    print("[cpu_collector] Stopped.")


if __name__ == "__main__":
    main()
