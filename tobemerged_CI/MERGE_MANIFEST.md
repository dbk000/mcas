# Merge Manifest — CPU Monitoring CI Loop

**Session date:** 2026-02-20
**Purpose:** Implement real-time CPU metrics pipeline so Claude Code sessions can observe VPS resource utilisation and write better-parallelised scripts.

---

## What This Session Did

### NEW: CPU Monitoring Pipeline (`Backend/monitoring/`)

Three new files — a collector daemon, a query CLI, and a Hetzner API fallback:

| File | Purpose |
|------|---------|
| `Backend/monitoring/cpu_collector.py` | Daemon that reads `/proc/stat` + `docker stats` every 5s, writes structured metrics to Redis. Runs inside Docker with host `/proc` mounted at `/host/proc`. |
| `Backend/monitoring/cpu_query.py` | CLI query tool. Commands: `brief`, `now`, `summary`, `containers`, `hotspots`, `history <seconds>`, `health`, `alerts`. All output is JSON. |
| `Backend/monitoring/hetzner_cpu_api.py` | Secondary path — fetches CPU metrics from Hetzner Cloud API for historical views beyond the 24h Redis window. |

### MODIFIED: `Backend/docker-compose.yml`

Added `cpu_collector` service at the end of the services block (before `volumes:`). Key details:
- Image: `python:3.12-slim`
- Container name: `mcas-cpu-collector`
- Mounts: host `/proc` (ro), Docker socket (ro), Docker CLI binary (ro), monitoring scripts (ro)
- Network: `backend` (same as Redis)
- `REDIS_HOST=redis` — reaches Redis by service name
- Resource limits: 0.05 CPU, 64M RAM
- Entrypoint: `pip install --quiet redis && python cpu_collector.py`

**Merge note:** This is an additive service block. No existing services were modified. Paste the `cpu_collector:` block before the `volumes:` section.

### MODIFIED: `CLAUDE.md`

Added Section 1.6 "Resource Awareness Protocol" between Section 1.5 (Production Database Access) and Section 2 (Hard Nevers). This tells Claude Code:
- Check `brief` before running scripts >60s
- Poll every 2-3 min during long runs
- Review `history` after completion
- How to interpret usage/steal/iowait numbers

**Merge note:** Insert the new section 1.6 block. Nothing else in CLAUDE.md was touched.

---

## Files NOT From This Session (Pre-existing Uncommitted Work)

These files were already uncommitted when this session started. They are included here for completeness but were **not created or modified by this session**:

| File | Origin |
|------|--------|
| `Backend/api/app/main.py` | Pre-existing modification |
| `Backend/worker/app/setups/__init__.py` | Pre-existing modification |
| `Backend/worker/app/signal_engine.py` | Pre-existing modification |
| `Backend/worker/app/signal_worker.py` | Pre-existing modification |
| `Frontend/src/pages/Opportunities.jsx` | Pre-existing modification |
| `Backend/scripts/research_edge_discovery.py` | Pre-existing new file |
| `Backend/scripts/research_execution_feasibility.py` | Pre-existing new file |
| `Backend/scripts/research_regime_filter.py` | Pre-existing new file |
| `Backend/scripts/research_short_reversion.py` | Pre-existing new file |
| `Backend/worker/app/setups/s9_short_reversion.py` | Pre-existing new file |

---

## Merge Instructions

### 1. CPU monitoring files (copy directly)
```bash
cp -r tobemerged/Backend/monitoring /srv/mcas/Backend/monitoring
```

### 2. docker-compose.yml (add the cpu_collector service block)
The `cpu_collector:` service block needs to be added to the production `docker-compose.yml`. It's purely additive — no existing services were changed. Add it before the `volumes:` section.

### 3. CLAUDE.md (add Section 1.6)
Insert the "Resource Awareness Protocol" section between Section 1.5 and Section 2. The diff is self-contained.

### 4. Deploy
```bash
cd /srv/mcas/Backend && docker compose -f docker-compose.yml up -d --no-deps cpu_collector
```

### 5. Verify
```bash
# Wait ~10s for first data collection cycle
docker exec mcas-cpu-collector python cpu_query.py health
docker exec mcas-cpu-collector python cpu_query.py brief
docker exec mcas-cpu-collector python cpu_query.py containers
```

Expected output from `brief`:
```
CPU: 23.4% (usr:18.2% sys:5.2% steal:0.0%) | load:1.2/0.9/0.7 | cores:16 | top:backend-worker-1@45.2%
```

---

## Known Considerations

1. **pip install on every restart** — adds ~5s cold start. If annoying, create a one-line Dockerfile with `redis` baked into the image.
2. **Docker CLI binary mount** — `/usr/bin/docker:/usr/bin/docker:ro` assumes the host Docker CLI is at `/usr/bin/docker`. Verify with `which docker` on the VPS. If it's elsewhere (e.g. `/usr/local/bin/docker`), adjust the volume mount.
3. **Docker socket permissions** — the container runs as root so the socket is accessible. The `python:3.12-slim` image doesn't have a docker group.
