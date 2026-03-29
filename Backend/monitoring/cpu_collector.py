"""
CPU Collector — host CPU/memory metrics → Redis.

Reads /proc/stat and /proc/meminfo (or /host/proc in containers) to compute
CPU utilization. Optionally collects per-container stats via Docker socket.
Writes metrics to Redis every CPU_SAMPLE_INTERVAL seconds.

GOVERNANCE_DEBT: connects via REDIS_HOST/REDIS_PORT/REDIS_DB instead of REDIS_URL.
"""

import json
import os
import subprocess
import time
import logging

logger = logging.getLogger("cpu_collector")

# Config from env (GOVERNANCE_DEBT: not via TypedConfigRegistry)
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
SAMPLE_INTERVAL = int(os.getenv("CPU_SAMPLE_INTERVAL", "5"))
HISTORY_TTL = int(os.getenv("CPU_HISTORY_TTL", "86400"))
DOCKER_STATS = os.getenv("CPU_DOCKER_STATS", "true").lower() == "true"
ALERT_THRESHOLD = int(os.getenv("CPU_ALERT_THRESHOLD", "85"))
ALERT_SUSTAINED = int(os.getenv("CPU_ALERT_SUSTAINED", "30"))
HOST_PROC = os.getenv("HOST_PROC", "/proc")


def _read_cpu_times():
    """Read aggregate CPU times from /proc/stat."""
    try:
        with open(f"{HOST_PROC}/stat") as f:
            for line in f:
                if line.startswith("cpu "):
                    parts = line.split()
                    # user nice system idle iowait irq softirq steal
                    times = [int(x) for x in parts[1:9]]
                    return times
    except Exception as e:
        logger.debug(f"Failed to read CPU times: {e}")
    return None


def _read_meminfo():
    """Read memory info from /proc/meminfo."""
    info = {}
    try:
        with open(f"{HOST_PROC}/meminfo") as f:
            for line in f:
                parts = line.split()
                if parts[0] in ("MemTotal:", "MemAvailable:", "MemFree:", "Buffers:", "Cached:"):
                    info[parts[0].rstrip(":")] = int(parts[1])  # kB
    except Exception as e:
        logger.debug(f"Failed to read meminfo: {e}")
    return info


def _read_loadavg():
    """Read load averages."""
    try:
        with open(f"{HOST_PROC}/loadavg") as f:
            parts = f.read().split()
            return [float(parts[0]), float(parts[1]), float(parts[2])]
    except Exception:
        return [0, 0, 0]


def _get_ncpus():
    """Get number of CPUs."""
    count = 0
    try:
        with open(f"{HOST_PROC}/stat") as f:
            for line in f:
                if line.startswith("cpu") and line[3] != " ":
                    count += 1
    except Exception:
        count = os.cpu_count() or 1
    return count


def _docker_container_stats():
    """Get per-container CPU/memory stats via docker CLI."""
    if not DOCKER_STATS:
        return []
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"],
            capture_output=True, text=True, timeout=10,
        )
        stats = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 4:
                stats.append({
                    "name": parts[0],
                    "cpu_pct": parts[1].rstrip("%"),
                    "mem_usage": parts[2],
                    "mem_pct": parts[3].rstrip("%"),
                })
        return stats
    except Exception as e:
        logger.debug(f"Docker stats failed: {e}")
        return []


def main_loop():
    """Main collection loop. Runs forever, writes to Redis."""
    import redis as redis_lib

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    r = redis_lib.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
                        decode_responses=True)
    logger.info(f"CPU collector starting: interval={SAMPLE_INTERVAL}s, "
                f"host_proc={HOST_PROC}, docker_stats={DOCKER_STATS}")

    ncpus = _get_ncpus()
    prev_times = _read_cpu_times()
    alert_count = 0

    while True:
        time.sleep(SAMPLE_INTERVAL)

        now = time.time()
        cur_times = _read_cpu_times()
        meminfo = _read_meminfo()
        loadavg = _read_loadavg()

        # Compute CPU utilization
        cpu_pct = 0.0
        iowait_pct = 0.0
        steal_pct = 0.0
        if prev_times and cur_times:
            deltas = [c - p for c, p in zip(cur_times, prev_times)]
            total = sum(deltas)
            if total > 0:
                idle = deltas[3]
                cpu_pct = round((1.0 - idle / total) * 100, 1)
                iowait_pct = round(deltas[4] / total * 100, 1) if len(deltas) > 4 else 0
                steal_pct = round(deltas[7] / total * 100, 1) if len(deltas) > 7 else 0
        prev_times = cur_times

        # Memory
        mem_total = meminfo.get("MemTotal", 0)
        mem_available = meminfo.get("MemAvailable", 0)
        mem_used_pct = round((1.0 - mem_available / mem_total) * 100, 1) if mem_total > 0 else 0

        # Build snapshot
        snapshot = {
            "ts": now,
            "cpu_pct": cpu_pct,
            "iowait_pct": iowait_pct,
            "steal_pct": steal_pct,
            "mem_used_pct": mem_used_pct,
            "mem_total_mb": round(mem_total / 1024, 0),
            "mem_available_mb": round(mem_available / 1024, 0),
            "load_1m": loadavg[0],
            "load_5m": loadavg[1],
            "load_15m": loadavg[2],
            "ncpus": ncpus,
        }

        # Write to Redis
        try:
            pipe = r.pipeline(transaction=False)
            # Current snapshot
            pipe.set("cpu:current", json.dumps(snapshot, separators=(",", ":")))
            # History (capped list)
            pipe.lpush("cpu:history", json.dumps(snapshot, separators=(",", ":")))
            pipe.ltrim("cpu:history", 0, int(HISTORY_TTL / SAMPLE_INTERVAL))
            pipe.execute()
        except Exception as e:
            logger.error(f"Redis write failed: {e}")
            time.sleep(5)
            try:
                r = redis_lib.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
                                    decode_responses=True)
            except Exception:
                pass
            continue

        # Per-container stats (less frequent — every 6th sample ≈ 30s)
        if DOCKER_STATS and int(now) % (SAMPLE_INTERVAL * 6) < SAMPLE_INTERVAL:
            containers = _docker_container_stats()
            if containers:
                try:
                    r.set("cpu:containers", json.dumps(containers, separators=(",", ":")))
                except Exception:
                    pass

        # Alert tracking
        if cpu_pct > ALERT_THRESHOLD:
            alert_count += 1
            if alert_count >= ALERT_SUSTAINED / SAMPLE_INTERVAL:
                try:
                    r.set("cpu:alert", json.dumps({
                        "ts": now, "cpu_pct": cpu_pct,
                        "sustained_s": alert_count * SAMPLE_INTERVAL,
                    }, separators=(",", ":")))
                except Exception:
                    pass
        else:
            if alert_count > 0:
                try:
                    r.delete("cpu:alert")
                except Exception:
                    pass
            alert_count = 0


async def run():
    """Async wrapper for supervisor."""
    import asyncio
    await asyncio.to_thread(main_loop)


if __name__ == "__main__":
    main_loop()
