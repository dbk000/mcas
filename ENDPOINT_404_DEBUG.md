# RESOLVED: GET /v1/control/data/availability returns 404 despite route being registered

## Symptom
The new endpoint `GET /v1/control/data/availability` returns `{"detail":"Not Found"}` (HTTP 404) from the running container, despite the route being provably registered in FastAPI's internal routing table.

## Environment
- Dev machine: `/home/darren/Documents/MCAS/Backend`
- Container: `mcas-dev-api-1` (Docker Compose, `Dockerfile.api`)
- Framework: FastAPI + uvicorn (single worker, no reload)
- Port mapping: 8002 (host) → 8000 (container)

## What works
- `/health` → 200
- `/v1/strategies` → 200 (auth via X-API-Key header)
- `/v1/control/paper/status` → 200 (same router, same auth)
- API key is valid (hmac.compare_digest confirms match)

## What fails
- `/v1/control/data/availability` → 404
- `/v1/control/runs/queue` → 500 (separate pre-existing DB issue)

## File: `api/app/routers/control.py`
- Lines 522-631: New endpoint `@router.get("/data/availability")`
- Router prefix: `APIRouter(prefix="/control")` (line 70)
- Mounted in main.py line 1503: `app.include_router(control_router, prefix="/v1", dependencies=[Depends(require_api_key)])`
- Full expected path: `/v1/control/data/availability`

## Confirmed facts (all verified inside the running container)

1. **File exists**: `docker exec mcas-dev-api-1 wc -l /backend/api/app/routers/control.py` → 631 lines (matches host)
2. **Decorator exists**: `docker exec ... grep -n "@router" ...` shows `@router.get("/data/availability")` at line 522
3. **Function exists**: AST parse finds `get_data_availability` in the function list
4. **Router has 9 routes**: Direct import of `router` from `control.py` shows 9 routes including `/control/data/availability`
5. **App route matches**: Starlette `route.matches()` returns `Match.FULL` for the path
6. **App route introspection**: Iterating `app.routes` shows `/v1/control/data/availability {'GET'}` with name `get_data_availability`
7. **OpenAPI spec does NOT include it**: `GET /openapi.json` shows only 8 control routes, missing `data/availability`
8. **Auth is not the issue**: Auth returns 401 on failure (confirmed by reading `require_api_key`), not 404
9. **No include_in_schema=False**: grep confirms no schema exclusion
10. **Rebuilt with --no-cache**: Full Docker rebuild, container recreated — same result
11. **Python syntax valid**: `ast.parse()` succeeds

## The paradox
- Python introspection says the route exists ✓
- Starlette matching says it matches ✓
- But the OpenAPI spec doesn't include it ✗
- And HTTP requests return 404 ✗

## Hypotheses to investigate

### H1: Module-level import side effect
`control.py` has module-level code:
```python
_worker_cfg = get_config().Worker
r = redis.Redis.from_url(_worker_cfg.REDIS_URL, decode_responses=True)
```
If the `@router.get("/data/availability")` decorator execution happens AFTER `main.py` calls `app.include_router(control_router)`, the route would be added to the router object but NOT to the app's already-copied route table. FastAPI's `include_router` copies routes at call time — routes added later to the source router are NOT reflected.

**How this could happen**: Python imports execute top-to-bottom. If there's a circular import or deferred execution, the decorator at line 522 might execute after `include_router` at main.py:1503. However, `include_router` is at module level in main.py and `control.py` is fully imported before it — so this shouldn't happen under normal circumstances.

### H2: __pycache__ stale bytecode
A `.pyc` from a previous build (without the endpoint) is being used instead of the new `.py`. The `Dockerfile.api` doesn't explicitly clear `__pycache__`. The `--no-cache` flag only clears Docker layer cache, not Python bytecache within the image build.

**Test**: Add `ENV PYTHONDONTWRITEBYTECODE=1` to Dockerfile.api and rebuild.

### H3: FastAPI include_router snapshot semantics
FastAPI's `include_router()` takes a snapshot of routes at call time. If the endpoint was added to the router AFTER the router was passed to `include_router`, it would exist in the router object (explaining introspection) but not in the app's actual routing table (explaining 404).

**Test**: Move `from api.app.routers.control import router as control_router` to AFTER all other imports, or add a print statement at the exact line of `@router.get("/data/availability")` to confirm execution order.

### H4: Duplicate router variable name
If `control.py` is somehow imported twice (once via the module and once via bytecache), there could be two different `router` objects. The one with 9 routes is inspected, but the one with 8 routes is what was passed to `include_router`.

**Test**: Add `print(f"control.py router id: {id(router)}, routes: {len(router.routes)}")` at the bottom of control.py and check the startup log.

## Quick diagnostic to paste into the container

```bash
docker exec mcas-dev-api-1 python3 -c "
import sys; sys.path.insert(0, '/backend')
from governance.bootstrap import bootstrap_genesis; bootstrap_genesis()

# Import main to trigger include_router
from api.app import main
from api.app.routers import control

print(f'control.router id: {id(control.router)}')
print(f'control.router routes: {len(control.router.routes)}')

# Find the control router in app.routes
for route in main.app.routes:
    if hasattr(route, 'path') and 'availability' in route.path:
        print(f'App route: {route.path} endpoint={route.endpoint}')
        print(f'Same function? {route.endpoint is control.get_data_availability}')
"
```

## Resolution

**Two API containers running on different ports.** `backend-api-1` (stale production image from `/srv/mcas`) was bound to port 8002. `mcas-dev-api-1` (dev, rebuilt with new code) was on port 8003. All `curl localhost:8002` requests hit the stale image.

```
backend-api-1     → 0.0.0.0:8002->8000  (OLD, no data/availability endpoint)
mcas-dev-api-1    → 0.0.0.0:8003->8000  (NEW, has the endpoint, WORKS)
```

**Verified working:** `curl localhost:8003/v1/control/data/availability` returns 200 with full governance block.

None of the hypotheses (H1-H4) were the issue. The route was never broken — we were hitting the wrong container.

## Files involved
- `/home/darren/Documents/MCAS/Backend/api/app/routers/control.py` (endpoint source)
- `/home/darren/Documents/MCAS/Backend/api/app/main.py` (router mounting, line 1502-1503)
- `/home/darren/Documents/MCAS/Backend/Dockerfile.api` (image build)
- `/home/darren/Documents/MCAS/Backend/docker-compose.yml` (service definition)
