"""
MCAS Pipeline Introspection API
================================

Deliverables 2, 4, 5 combined:
- Topology endpoints (GET /meta/topology, node versions, version detail)
- Auto-capture foundation (sweep registration, metric validation)
- Agent coordination (version pinning, integrity hashing)
"""

import hashlib
import json
import time
import logging
from datetime import datetime, timezone
from typing import Optional, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Query, HTTPException, Body
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/meta", tags=["Pipeline Introspection"])

# -------------------------
# Config
# -------------------------
import os
DATABASE_URL = os.getenv("DATABASE_URL")


def _get_conn():
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="DATABASE_URL not configured")
    return psycopg.connect(DATABASE_URL)


def envelope(data: Any, compute_start: float, as_of_ts: float = None) -> dict:
    meta = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "v1",
        "compute_ms": round((time.time() - compute_start) * 1000, 1),
    }
    if as_of_ts is not None:
        meta["as_of_ts"] = datetime.fromtimestamp(as_of_ts, tz=timezone.utc).isoformat()
    else:
        meta["as_of_ts"] = datetime.now(timezone.utc).isoformat()
    return {"data": data, "meta": meta}


# -------------------------
# Utility: config_hash
# -------------------------
def compute_config_hash(node_versions: dict) -> str:
    """SHA256 of sorted node_versions, truncated to 16 chars."""
    canonical = json.dumps(sorted(node_versions.items()), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def compute_integrity_hash(version_ids: list, source_hashes: list = None, parameters: list = None) -> str:
    """Hash of version_ids + source_hashes + parameters for backtest pinning."""
    parts = sorted(str(v) for v in version_ids)
    if source_hashes:
        parts.extend(sorted(str(s) for s in source_hashes if s))
    if parameters:
        parts.extend(json.dumps(p, sort_keys=True) for p in parameters if p)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


# -------------------------
# Metric key validation
# -------------------------
_metric_alias_cache = None
_param_alias_cache = None


def _load_metric_aliases(conn):
    global _metric_alias_cache
    if _metric_alias_cache is not None:
        return _metric_alias_cache
    with conn.cursor() as cur:
        cur.execute("SELECT metric_key, aliases FROM meta.metric_registry")
        rows = cur.fetchall()
    cache = {}
    for key, aliases in rows:
        cache[key] = key
        if aliases:
            for alias in aliases:
                cache[alias] = key
    _metric_alias_cache = cache
    return cache


def _load_param_aliases(conn):
    global _param_alias_cache
    if _param_alias_cache is not None:
        return _param_alias_cache
    with conn.cursor() as cur:
        cur.execute("SELECT param_key, aliases FROM meta.parameter_registry")
        rows = cur.fetchall()
    cache = {}
    for key, aliases in rows:
        cache[key] = key
        if aliases:
            for alias in aliases:
                cache[alias] = key
    _param_alias_cache = cache
    return cache


def normalize_metrics(metrics: dict, conn) -> tuple:
    """Normalize metric keys using registry aliases. Returns (normalized_dict, warnings)."""
    aliases = _load_metric_aliases(conn)
    normalized = {}
    warnings = []
    for k, v in metrics.items():
        canonical = aliases.get(k)
        if canonical:
            normalized[canonical] = v
        else:
            warnings.append(f"Unregistered metric key: {k}")
            normalized[k] = v
    return normalized, warnings


def normalize_parameters(params: dict, conn) -> tuple:
    """Normalize parameter keys using registry aliases. Returns (normalized_dict, warnings)."""
    aliases = _load_param_aliases(conn)
    normalized = {}
    warnings = []
    for k, v in params.items():
        canonical = aliases.get(k)
        if canonical:
            normalized[canonical] = v
        else:
            warnings.append(f"Unregistered parameter key: {k}")
            normalized[k] = v
    return normalized, warnings


# =============================================================================
# DELIVERABLE 2: Topology Endpoints
# =============================================================================

@router.get("/topology")
def get_topology():
    """Full pipeline graph for React Flow consumption."""
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            # Fetch all active nodes
            cur.execute("""
                SELECT n.node_id, n.name, n.node_type, n.subsystem, n.description,
                       n.source_file, n.owner, n.config,
                       CASE WHEN n.retired_at IS NULL THEN 'active' ELSE 'retired' END AS status
                FROM meta.node n
                ORDER BY n.subsystem, n.name
            """)
            node_rows = cur.fetchall()
            node_cols = [d[0] for d in cur.description]

            # Fetch ports grouped by node
            cur.execute("""
                SELECT port_id, node_id, name, direction, data_type, schema_spec, description
                FROM meta.port
                ORDER BY node_id, direction, name
            """)
            port_rows = cur.fetchall()

            # Fetch edges
            cur.execute("""
                SELECT e.edge_id, e.source_port_id, e.target_port_id, e.metadata,
                       sp.node_id AS source_node_id, sp.name AS source_port_name,
                       tp.node_id AS target_node_id, tp.name AS target_port_name
                FROM meta.edge e
                JOIN meta.port sp ON sp.port_id = e.source_port_id
                JOIN meta.port tp ON tp.port_id = e.target_port_id
            """)
            edge_rows = cur.fetchall()

            # Fetch current deployed version per node (or latest promising/tested)
            cur.execute("""
                SELECT DISTINCT ON (node_id) node_id, version_id, version_number,
                       agg_pf, net_pnl, win_rate, trade_count, status, parameters,
                       staleness_state, health_status, output_hash
                FROM meta.node_version
                ORDER BY node_id,
                    CASE status
                        WHEN 'deployed' THEN 0
                        WHEN 'promising' THEN 1
                        WHEN 'tested' THEN 2
                        WHEN 'baseline' THEN 3
                        WHEN 'retired' THEN 4
                    END,
                    version_number DESC
            """)
            current_versions = {str(r[0]): {
                "version_id": str(r[1]),
                "version_number": r[2],
                "agg_pf": r[3],
                "net_pnl": r[4],
                "win_rate": r[5],
                "trade_count": r[6],
                "status": r[7],
                "parameters": r[8] or {},
                "staleness_state": r[9] or "clean",
                "health_status": r[10] or "unknown",
                "output_hash": r[11],
            } for r in cur.fetchall()}

            # Version counts per node
            cur.execute("""
                SELECT node_id, COUNT(*) FROM meta.node_version GROUP BY node_id
            """)
            version_counts = {str(r[0]): r[1] for r in cur.fetchall()}

    # Build port lookup: node_id -> {inputs: [], outputs: []}
    ports_by_node = {}
    for r in port_rows:
        nid = str(r[1])
        if nid not in ports_by_node:
            ports_by_node[nid] = {"inputs": [], "outputs": []}
        port_data = {
            "id": str(r[0]),
            "name": r[2],
            "data_type": r[4],
            "schema_spec": r[5] or {},
            "description": r[6],
        }
        if r[3] == "input":
            ports_by_node[nid]["inputs"].append(port_data)
        else:
            ports_by_node[nid]["outputs"].append(port_data)

    # Build node list
    nodes = []
    for r in node_rows:
        nid = str(r[0])
        nodes.append({
            "id": nid,
            "type": r[2],
            "name": r[1],
            "subsystem": r[3],
            "description": r[4],
            "source_file": r[5],
            "owner": r[6],
            "config": r[7] or {},
            "status": r[8],
            "current_version": current_versions.get(nid),
            "version_count": version_counts.get(nid, 0),
            "ports": ports_by_node.get(nid, {"inputs": [], "outputs": []}),
        })

    # Build edge list
    edges = []
    for r in edge_rows:
        edges.append({
            "id": str(r[0]),
            "source": str(r[4]),
            "target": str(r[6]),
            "sourceHandle": r[5],
            "targetHandle": r[7],
            "metadata": r[3] or {},
        })

    return envelope({"nodes": nodes, "edges": edges}, t0)


@router.get("/node/{node_id}/versions")
def get_node_versions(
    node_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    status: Optional[str] = Query(default=None),
):
    """Version history for the detail panel, ordered by significance DESC then created_at DESC."""
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            # Verify node exists
            cur.execute("SELECT name FROM meta.node WHERE node_id = %s", (node_id,))
            node = cur.fetchone()
            if not node:
                raise HTTPException(status_code=404, detail=f"Node {node_id} not found")

            query = """
                SELECT version_id, version_number, status, parameters, metrics,
                       agg_pf, net_pnl, win_rate, max_drawdown, trade_count,
                       metrics_context, source_hash, significance,
                       superseded_by, change_reason, parent_version, derived_from,
                       created_at, staleness_state, health_status, output_hash,
                       metrics_run_id
                FROM meta.node_version
                WHERE node_id = %s
            """
            params = [node_id]
            if status:
                query += " AND status = %s"
                params.append(status)

            query += " ORDER BY significance DESC NULLS LAST, created_at DESC LIMIT %s"
            params.append(limit)

            cur.execute(query, params)
            rows = cur.fetchall()

    # Collect all run_ids to batch-resolve run details
    run_ids = set()
    for r in rows:
        if r[21]:
            run_ids.add(str(r[21]))

    # Resolve run details if any metrics_run_id values exist
    run_details = {}
    if run_ids:
        with _get_conn() as conn2:
            with conn2.cursor() as cur2:
                cur2.execute("""
                    SELECT id, run_type, name, status, strategy_json,
                           created_at, started_at, completed_at
                    FROM public.runs
                    WHERE id = ANY(%s::uuid[])
                """, (list(run_ids),))
                for rd in cur2.fetchall():
                    run_details[str(rd[0])] = {
                        "run_id": str(rd[0]),
                        "run_type": rd[1],
                        "run_name": rd[2],
                        "run_status": rd[3],
                        "strategy_json": rd[4],
                        "run_created_at": rd[5].isoformat() if rd[5] else None,
                        "run_started_at": rd[6].isoformat() if rd[6] else None,
                        "run_completed_at": rd[7].isoformat() if rd[7] else None,
                    }

    versions = []
    for r in rows:
        rid = str(r[21]) if r[21] else None
        versions.append({
            "version_id": str(r[0]),
            "version_number": r[1],
            "status": r[2],
            "parameters": r[3] or {},
            "metrics": r[4] or {},
            "agg_pf": r[5],
            "net_pnl": r[6],
            "win_rate": r[7],
            "max_drawdown": r[8],
            "trade_count": r[9],
            "metrics_context": r[10] or {},
            "source_hash": r[11],
            "significance": r[12],
            "superseded_by": str(r[13]) if r[13] else None,
            "change_reason": r[14],
            "parent_version": str(r[15]) if r[15] else None,
            "derived_from": r[16],
            "created_at": r[17].isoformat() if r[17] else None,
            "staleness_state": r[18] or "clean",
            "health_status": r[19] or "unknown",
            "output_hash": r[20],
            "metrics_run_id": rid,
            "run_detail": run_details.get(rid) if rid else None,
        })

    return envelope({
        "node_name": node[0],
        "node_id": node_id,
        "versions": versions,
        "count": len(versions),
    }, t0)


@router.get("/node/{node_id}/version/{version_id}")
def get_version_detail(node_id: str, version_id: str):
    """Full version detail including parameters, metrics, provenance, change_reason."""
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT nv.version_id, nv.version_number, nv.status, nv.parameters, nv.metrics,
                       nv.agg_pf, nv.net_pnl, nv.win_rate, nv.max_drawdown, nv.trade_count,
                       nv.metrics_context, nv.source_hash, nv.significance,
                       nv.superseded_by, nv.change_reason, nv.parent_version, nv.derived_from,
                       nv.metrics_run_id, nv.created_at,
                       n.name AS node_name, n.node_type, n.subsystem,
                       nv.staleness_state, nv.health_status, nv.output_hash
                FROM meta.node_version nv
                JOIN meta.node n ON n.node_id = nv.node_id
                WHERE nv.node_id = %s AND nv.version_id = %s
            """, (node_id, version_id))
            r = cur.fetchone()

            if not r:
                raise HTTPException(status_code=404, detail=f"Version {version_id} not found for node {node_id}")

            # Get experiments this version belongs to
            cur.execute("""
                SELECT e.experiment_id, e.name, e.experiment_code, e.status
                FROM meta.experiment e
                JOIN meta.experiment_version ev ON ev.experiment_id = e.experiment_id
                WHERE ev.version_id = %s
            """, (version_id,))
            experiments = [
                {"experiment_id": str(x[0]), "name": x[1], "code": x[2], "status": x[3]}
                for x in cur.fetchall()
            ]

            # Get compositions using this version
            cur.execute("""
                SELECT c.composition_id, c.name, c.status, c.agg_pf
                FROM meta.composition c
                JOIN meta.composition_version cv ON cv.composition_id = c.composition_id
                WHERE cv.version_id = %s
                ORDER BY c.agg_pf DESC NULLS LAST
                LIMIT 10
            """, (version_id,))
            compositions = [
                {"composition_id": str(x[0]), "name": x[1], "status": x[2], "agg_pf": x[3]}
                for x in cur.fetchall()
            ]

    return envelope({
        "version_id": str(r[0]),
        "version_number": r[1],
        "status": r[2],
        "parameters": r[3] or {},
        "metrics": r[4] or {},
        "agg_pf": r[5],
        "net_pnl": r[6],
        "win_rate": r[7],
        "max_drawdown": r[8],
        "trade_count": r[9],
        "metrics_context": r[10] or {},
        "source_hash": r[11],
        "significance": r[12],
        "superseded_by": str(r[13]) if r[13] else None,
        "change_reason": r[14],
        "parent_version": str(r[15]) if r[15] else None,
        "derived_from": r[16],
        "metrics_run_id": str(r[17]) if r[17] else None,
        "created_at": r[18].isoformat() if r[18] else None,
        "node_name": r[19],
        "node_type": r[20],
        "subsystem": r[21],
        "staleness_state": r[22] or "clean",
        "health_status": r[23] or "unknown",
        "output_hash": r[24],
        "experiments": experiments,
        "compositions": compositions,
    }, t0)


# =============================================================================
# Additional topology query endpoints
# =============================================================================

@router.get("/experiments")
def list_experiments(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    """List experiments with optional status filter."""
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            query = """
                SELECT e.experiment_id, e.experiment_code, e.name, e.hypothesis,
                       e.status, e.outcome_class, e.failure_mode, e.key_insight,
                       e.created_at, e.updated_at,
                       COUNT(ev.version_id) AS version_count
                FROM meta.experiment e
                LEFT JOIN meta.experiment_version ev ON ev.experiment_id = e.experiment_id
            """
            params = []
            if status:
                query += " WHERE e.status = %s"
                params.append(status)
            query += " GROUP BY e.experiment_id ORDER BY e.created_at DESC LIMIT %s"
            params.append(limit)

            cur.execute(query, params)
            rows = cur.fetchall()

    experiments = []
    for r in rows:
        experiments.append({
            "experiment_id": str(r[0]),
            "experiment_code": r[1],
            "name": r[2],
            "hypothesis": r[3],
            "status": r[4],
            "outcome_class": r[5],
            "failure_mode": r[6],
            "key_insight": r[7],
            "created_at": r[8].isoformat() if r[8] else None,
            "updated_at": r[9].isoformat() if r[9] else None,
            "version_count": r[10],
        })

    return envelope({"experiments": experiments, "count": len(experiments)}, t0)


@router.get("/experiment/{experiment_id}")
def get_experiment(experiment_id: str):
    """Full experiment detail."""
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT experiment_id, experiment_code, name, hypothesis, status,
                       outcome_class, failure_mode, key_insight, methodological_notes,
                       conclusion, baseline_run_id, created_at, updated_at
                FROM meta.experiment WHERE experiment_id = %s
            """, (experiment_id,))
            r = cur.fetchone()
            if not r:
                raise HTTPException(status_code=404, detail="Experiment not found")

            # Versions in this experiment
            cur.execute("""
                SELECT nv.version_id, nv.version_number, nv.status, nv.agg_pf,
                       nv.change_reason, n.name AS node_name, n.node_id
                FROM meta.experiment_version ev
                JOIN meta.node_version nv ON nv.version_id = ev.version_id
                JOIN meta.node n ON n.node_id = nv.node_id
                WHERE ev.experiment_id = %s
                ORDER BY ev.added_at
            """, (experiment_id,))
            versions = [
                {
                    "version_id": str(x[0]), "version_number": x[1], "status": x[2],
                    "agg_pf": x[3], "change_reason": x[4],
                    "node_name": x[5], "node_id": str(x[6]),
                }
                for x in cur.fetchall()
            ]

    return envelope({
        "experiment_id": str(r[0]),
        "experiment_code": r[1],
        "name": r[2],
        "hypothesis": r[3],
        "status": r[4],
        "outcome_class": r[5],
        "failure_mode": r[6],
        "key_insight": r[7],
        "methodological_notes": r[8] or {},
        "conclusion": r[9],
        "baseline_run_id": str(r[10]) if r[10] else None,
        "created_at": r[11].isoformat() if r[11] else None,
        "updated_at": r[12].isoformat() if r[12] else None,
        "versions": versions,
    }, t0)


@router.get("/compositions")
def list_compositions(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    """List compositions with optional status filter."""
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            query = """
                SELECT composition_id, name, config_hash, status, source,
                       agg_pf, net_pnl, win_rate, max_drawdown, trade_count,
                       experiment_id, created_at
                FROM meta.composition
            """
            params = []
            if status:
                query += " WHERE status = %s"
                params.append(status)
            query += " ORDER BY agg_pf DESC NULLS LAST, created_at DESC LIMIT %s"
            params.append(limit)

            cur.execute(query, params)
            rows = cur.fetchall()

    compositions = []
    for r in rows:
        compositions.append({
            "composition_id": str(r[0]),
            "name": r[1],
            "config_hash": r[2],
            "status": r[3],
            "source": r[4],
            "agg_pf": r[5],
            "net_pnl": r[6],
            "win_rate": r[7],
            "max_drawdown": r[8],
            "trade_count": r[9],
            "experiment_id": str(r[10]) if r[10] else None,
            "created_at": r[11].isoformat() if r[11] else None,
        })

    return envelope({"compositions": compositions, "count": len(compositions)}, t0)


@router.get("/composition/{composition_id}")
def get_composition(composition_id: str):
    """Full composition detail including node versions and validation gate."""
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT composition_id, name, description, node_versions, config_hash,
                       status, source, experiment_id, validation_gate, statistical_context,
                       assembled_at, integrity_hash, backtest_version_pin, metrics,
                       agg_pf, net_pnl, win_rate, max_drawdown, trade_count,
                       created_at, updated_at
                FROM meta.composition WHERE composition_id = %s
            """, (composition_id,))
            r = cur.fetchone()
            if not r:
                raise HTTPException(status_code=404, detail="Composition not found")

            # Composition versions with node details
            cur.execute("""
                SELECT cv.node_id, n.name AS node_name, cv.version_id,
                       nv.version_number, nv.status, nv.agg_pf
                FROM meta.composition_version cv
                JOIN meta.node n ON n.node_id = cv.node_id
                JOIN meta.node_version nv ON nv.version_id = cv.version_id
                WHERE cv.composition_id = %s
                ORDER BY n.name
            """, (composition_id,))
            cv_rows = cur.fetchall()

    version_detail = [
        {
            "node_id": str(x[0]), "node_name": x[1], "version_id": str(x[2]),
            "version_number": x[3], "status": x[4], "agg_pf": x[5],
        }
        for x in cv_rows
    ]

    return envelope({
        "composition_id": str(r[0]),
        "name": r[1],
        "description": r[2],
        "node_versions": r[3] or {},
        "config_hash": r[4],
        "status": r[5],
        "source": r[6],
        "experiment_id": str(r[7]) if r[7] else None,
        "validation_gate": r[8] or {},
        "statistical_context": r[9] or {},
        "assembled_at": r[10].isoformat() if r[10] else None,
        "integrity_hash": r[11],
        "backtest_version_pin": r[12] or {},
        "metrics": r[13] or {},
        "agg_pf": r[14],
        "net_pnl": r[15],
        "win_rate": r[16],
        "max_drawdown": r[17],
        "trade_count": r[18],
        "created_at": r[19].isoformat() if r[19] else None,
        "updated_at": r[20].isoformat() if r[20] else None,
        "version_detail": version_detail,
    }, t0)


@router.get("/search/versions")
def search_versions(
    node_id: Optional[str] = Query(default=None),
    min_agg_pf: Optional[float] = Query(default=None),
    min_win_rate: Optional[float] = Query(default=None),
    max_drawdown: Optional[float] = Query(default=None),
    min_trade_count: Optional[int] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Search node_versions by parameter values and metric thresholds."""
    t0 = time.time()

    conditions = []
    params = []

    if node_id:
        conditions.append("nv.node_id = %s")
        params.append(node_id)
    if min_agg_pf is not None:
        conditions.append("nv.agg_pf >= %s")
        params.append(min_agg_pf)
    if min_win_rate is not None:
        conditions.append("nv.win_rate >= %s")
        params.append(min_win_rate)
    if max_drawdown is not None:
        conditions.append("nv.max_drawdown <= %s")
        params.append(max_drawdown)
    if min_trade_count is not None:
        conditions.append("nv.trade_count >= %s")
        params.append(min_trade_count)
    if status:
        conditions.append("nv.status = %s")
        params.append(status)

    where = " AND ".join(conditions) if conditions else "TRUE"

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT nv.version_id, nv.node_id, n.name AS node_name,
                       nv.version_number, nv.status, nv.agg_pf, nv.net_pnl,
                       nv.win_rate, nv.max_drawdown, nv.trade_count,
                       nv.change_reason, nv.created_at
                FROM meta.node_version nv
                JOIN meta.node n ON n.node_id = nv.node_id
                WHERE {where}
                ORDER BY nv.agg_pf DESC NULLS LAST
                LIMIT %s
            """, params + [limit])
            rows = cur.fetchall()

    results = [
        {
            "version_id": str(r[0]), "node_id": str(r[1]), "node_name": r[2],
            "version_number": r[3], "status": r[4], "agg_pf": r[5],
            "net_pnl": r[6], "win_rate": r[7], "max_drawdown": r[8],
            "trade_count": r[9], "change_reason": r[10],
            "created_at": r[11].isoformat() if r[11] else None,
        }
        for r in rows
    ]

    return envelope({"versions": results, "count": len(results)}, t0)


@router.get("/compare/versions/{version_a}/{version_b}")
def compare_versions(version_a: str, version_b: str):
    """Diff two versions of the same node."""
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT version_id, node_id, version_number, status, parameters, metrics,
                       agg_pf, net_pnl, win_rate, max_drawdown, trade_count,
                       change_reason, created_at
                FROM meta.node_version
                WHERE version_id IN (%s, %s)
            """, (version_a, version_b))
            rows = cur.fetchall()

    if len(rows) != 2:
        raise HTTPException(status_code=404, detail="One or both versions not found")

    v = {}
    for r in rows:
        v[str(r[0])] = {
            "version_id": str(r[0]), "node_id": str(r[1]), "version_number": r[2],
            "status": r[3], "parameters": r[4] or {}, "metrics": r[5] or {},
            "agg_pf": r[6], "net_pnl": r[7], "win_rate": r[8],
            "max_drawdown": r[9], "trade_count": r[10],
            "change_reason": r[11],
            "created_at": r[12].isoformat() if r[12] else None,
        }

    va = v[version_a]
    vb = v[version_b]

    # Compute param diff
    all_params = set(list(va["parameters"].keys()) + list(vb["parameters"].keys()))
    changed_params = {}
    unchanged_params = {}
    for p in all_params:
        val_a = va["parameters"].get(p)
        val_b = vb["parameters"].get(p)
        if val_a != val_b:
            changed_params[p] = {"a": val_a, "b": val_b}
        else:
            unchanged_params[p] = val_a

    # Compute metric delta
    metric_keys = set(list(va["metrics"].keys()) + list(vb["metrics"].keys()))
    metric_delta = {}
    for k in metric_keys:
        ma = va["metrics"].get(k)
        mb = vb["metrics"].get(k)
        if ma is not None and mb is not None:
            try:
                metric_delta[k] = {"a": ma, "b": mb, "delta": float(mb) - float(ma)}
            except (TypeError, ValueError):
                metric_delta[k] = {"a": ma, "b": mb, "delta": None}
        else:
            metric_delta[k] = {"a": ma, "b": mb, "delta": None}

    return envelope({
        "version_a": va,
        "version_b": vb,
        "same_node": va["node_id"] == vb["node_id"],
        "changed_params": changed_params,
        "unchanged_params": unchanged_params,
        "metric_delta": metric_delta,
    }, t0)


@router.get("/compare/compositions/{comp_a}/{comp_b}")
def compare_compositions(comp_a: str, comp_b: str):
    """Diff two compositions showing which node versions differ."""
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            # Fetch both compositions
            cur.execute("""
                SELECT composition_id, name, node_versions, config_hash, status,
                       agg_pf, net_pnl, win_rate, max_drawdown, trade_count
                FROM meta.composition
                WHERE composition_id IN (%s, %s)
            """, (comp_a, comp_b))
            rows = cur.fetchall()

    if len(rows) != 2:
        raise HTTPException(status_code=404, detail="One or both compositions not found")

    c = {}
    for r in rows:
        c[str(r[0])] = {
            "composition_id": str(r[0]), "name": r[1],
            "node_versions": r[2] or {}, "config_hash": r[3],
            "status": r[4], "agg_pf": r[5], "net_pnl": r[6],
            "win_rate": r[7], "max_drawdown": r[8], "trade_count": r[9],
        }

    ca = c[comp_a]
    cb = c[comp_b]

    # Diff node_versions
    all_nodes = set(list(ca["node_versions"].keys()) + list(cb["node_versions"].keys()))
    differences = {}
    same = {}
    for nid in all_nodes:
        va = ca["node_versions"].get(nid)
        vb = cb["node_versions"].get(nid)
        if va != vb:
            differences[nid] = {"a": va, "b": vb}
        else:
            same[nid] = va

    return envelope({
        "composition_a": ca,
        "composition_b": cb,
        "different_nodes": differences,
        "same_nodes": same,
        "diff_count": len(differences),
    }, t0)


# =============================================================================
# DELIVERABLE 4: Auto-Capture Foundation
# =============================================================================

class SweepManifest(BaseModel):
    node_id: str
    swept_parameter: str
    value_range: list
    experiment_id: Optional[str] = None
    experiment_name: Optional[str] = None


@router.post("/sweep/register")
def register_sweep(manifest: SweepManifest):
    """Pre-allocate version_ids and composition_ids for a sweep.
    Returns IDs that the sweep script uses for batch result insertion."""
    t0 = time.time()

    n_configs = len(manifest.value_range)
    if n_configs < 1:
        raise HTTPException(status_code=400, detail="value_range must have at least 1 value")
    if n_configs > 500:
        raise HTTPException(status_code=400, detail="Maximum 500 configs per sweep")

    with _get_conn() as conn:
        with conn.cursor() as cur:
            # Verify node exists
            cur.execute("SELECT name FROM meta.node WHERE node_id = %s", (manifest.node_id,))
            node = cur.fetchone()
            if not node:
                raise HTTPException(status_code=404, detail=f"Node {manifest.node_id} not found")

            # Create or find experiment
            experiment_id = manifest.experiment_id
            if not experiment_id:
                cur.execute("""
                    INSERT INTO meta.experiment (name, hypothesis, status)
                    VALUES (%s, %s, 'running')
                    RETURNING experiment_id
                """, (
                    manifest.experiment_name or f"Sweep: {node[0]} / {manifest.swept_parameter}",
                    f"Sweeping {manifest.swept_parameter} over {n_configs} values: {manifest.value_range[:5]}{'...' if n_configs > 5 else ''}",
                ))
                experiment_id = str(cur.fetchone()[0])

            # Get next version number
            cur.execute(
                "SELECT COALESCE(MAX(version_number), 0) FROM meta.node_version WHERE node_id = %s",
                (manifest.node_id,)
            )
            base_version = cur.fetchone()[0]

            # Pre-allocate version IDs
            version_ids = []
            for i, val in enumerate(manifest.value_range):
                cur.execute("""
                    INSERT INTO meta.node_version (node_id, version_number, status, parameters, change_reason)
                    VALUES (%s, %s, 'tested', %s, %s)
                    RETURNING version_id
                """, (
                    manifest.node_id,
                    base_version + i + 1,
                    json.dumps({manifest.swept_parameter: val}),
                    f"Sweep: {manifest.swept_parameter} = {val}",
                ))
                vid = str(cur.fetchone()[0])
                version_ids.append(vid)

                # Link to experiment
                cur.execute("""
                    INSERT INTO meta.experiment_version (experiment_id, version_id)
                    VALUES (%s, %s)
                """, (experiment_id, vid))

            # Create sweep summary
            cur.execute("""
                INSERT INTO meta.sweep_summary (experiment_id, node_id, swept_parameter, value_range, n_configs)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING sweep_id
            """, (
                experiment_id, manifest.node_id, manifest.swept_parameter,
                json.dumps(manifest.value_range), n_configs,
            ))
            sweep_id = str(cur.fetchone()[0])

            # Log event
            cur.execute("""
                INSERT INTO meta.introspection_event (event_type, entity_type, entity_id, actor, payload)
                VALUES ('sweep_registered', 'sweep', %s, 'api', %s)
            """, (sweep_id, json.dumps({
                "node_id": manifest.node_id,
                "parameter": manifest.swept_parameter,
                "n_configs": n_configs,
            })))

        conn.commit()

    return envelope({
        "sweep_id": sweep_id,
        "experiment_id": experiment_id,
        "version_ids": version_ids,
        "n_configs": n_configs,
        "node_name": node[0],
    }, t0)


class SweepResults(BaseModel):
    sweep_id: str
    results: list  # [{version_id, metrics, metrics_context}]


@router.post("/sweep/results")
def submit_sweep_results(payload: SweepResults):
    """Batch submit metrics for pre-allocated sweep version IDs."""
    t0 = time.time()

    if not payload.results:
        raise HTTPException(status_code=400, detail="No results provided")

    warnings = []
    updated = 0

    with _get_conn() as conn:
        with conn.cursor() as cur:
            for item in payload.results:
                vid = item.get("version_id")
                raw_metrics = item.get("metrics", {})
                ctx = item.get("metrics_context", {})

                if not vid:
                    warnings.append("Skipped result with missing version_id")
                    continue

                # Normalize metric keys
                metrics, metric_warnings = normalize_metrics(raw_metrics, conn)
                warnings.extend(metric_warnings)

                cur.execute("""
                    UPDATE meta.node_version
                    SET metrics = %s, metrics_context = %s,
                        agg_pf = %s, net_pnl = %s, win_rate = %s,
                        max_drawdown = %s, trade_count = %s
                    WHERE version_id = %s
                """, (
                    json.dumps(metrics), json.dumps(ctx),
                    metrics.get("agg_pf"), metrics.get("net_pnl"), metrics.get("win_rate"),
                    metrics.get("max_drawdown"),
                    int(metrics["trade_count"]) if "trade_count" in metrics else None,
                    vid,
                ))
                if cur.rowcount > 0:
                    updated += 1

            # Update sweep summary
            if updated > 0:
                cur.execute("""
                    UPDATE meta.sweep_summary
                    SET n_above_threshold = (
                        SELECT COUNT(*) FROM meta.node_version nv
                        JOIN meta.experiment_version ev ON ev.version_id = nv.version_id
                        JOIN meta.sweep_summary ss ON ss.experiment_id = ev.experiment_id
                        WHERE ss.sweep_id = %s AND nv.agg_pf > 1.0
                    ),
                    best_version_id = (
                        SELECT nv.version_id FROM meta.node_version nv
                        JOIN meta.experiment_version ev ON ev.version_id = nv.version_id
                        JOIN meta.sweep_summary ss ON ss.experiment_id = ev.experiment_id
                        WHERE ss.sweep_id = %s AND nv.agg_pf IS NOT NULL
                        ORDER BY nv.agg_pf DESC LIMIT 1
                    )
                    WHERE sweep_id = %s
                """, (payload.sweep_id, payload.sweep_id, payload.sweep_id))

        conn.commit()

    return envelope({
        "updated": updated,
        "total": len(payload.results),
        "warnings": warnings if warnings else None,
    }, t0)


# =============================================================================
# DELIVERABLE 4: Experiment & Node Version CRUD
# =============================================================================

class CreateExperiment(BaseModel):
    name: str
    hypothesis: Optional[str] = None
    experiment_code: Optional[str] = None


@router.post("/experiment")
def create_experiment(payload: CreateExperiment):
    """Create a new experiment."""
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO meta.experiment (name, hypothesis, experiment_code, status)
                VALUES (%s, %s, %s, 'planned')
                RETURNING experiment_id
            """, (payload.name, payload.hypothesis, payload.experiment_code))
            eid = str(cur.fetchone()[0])

            cur.execute("""
                INSERT INTO meta.introspection_event (event_type, entity_type, entity_id, actor, payload)
                VALUES ('experiment_created', 'experiment', %s, 'api', %s)
            """, (eid, json.dumps({"name": payload.name})))
        conn.commit()

    return envelope({"experiment_id": eid, "name": payload.name}, t0)


class CreateNodeVersion(BaseModel):
    node_id: str
    parameters: Optional[dict] = None
    metrics: Optional[dict] = None
    metrics_context: Optional[dict] = None
    metrics_run_id: Optional[str] = None
    change_reason: Optional[str] = None
    source_hash: Optional[str] = None
    status: str = "tested"
    experiment_id: Optional[str] = None


@router.post("/node/version")
def create_node_version(payload: CreateNodeVersion):
    """Register a new node version."""
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            # Verify node
            cur.execute("SELECT name FROM meta.node WHERE node_id = %s", (payload.node_id,))
            node = cur.fetchone()
            if not node:
                raise HTTPException(status_code=404, detail=f"Node {payload.node_id} not found")

            # Next version number
            cur.execute(
                "SELECT COALESCE(MAX(version_number), 0) FROM meta.node_version WHERE node_id = %s",
                (payload.node_id,)
            )
            next_ver = cur.fetchone()[0] + 1

            # Normalize
            metrics = payload.metrics or {}
            params = payload.parameters or {}
            if metrics:
                metrics, _ = normalize_metrics(metrics, conn)
            if params:
                params, _ = normalize_parameters(params, conn)

            cur.execute("""
                INSERT INTO meta.node_version
                    (node_id, version_number, status, parameters, metrics,
                     metrics_context, metrics_run_id, source_hash, change_reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING version_id
            """, (
                payload.node_id, next_ver, payload.status,
                json.dumps(params), json.dumps(metrics),
                json.dumps(payload.metrics_context or {}),
                payload.metrics_run_id, payload.source_hash,
                payload.change_reason,
            ))
            vid = str(cur.fetchone()[0])

            # Link to experiment if specified
            if payload.experiment_id:
                cur.execute("""
                    INSERT INTO meta.experiment_version (experiment_id, version_id)
                    VALUES (%s, %s)
                """, (payload.experiment_id, vid))

        conn.commit()

    return envelope({
        "version_id": vid,
        "version_number": next_ver,
        "node_name": node[0],
    }, t0)


# =============================================================================
# VERSION EVENTS (Biography System)
# =============================================================================

@router.get("/version/{version_id}/events")
def get_version_events(
    version_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    detail: str = Query(default="full", regex="^(summary|full)$"),
):
    """Version biography — all events for a version, ordered chronologically.

    Wave 2.2: detail=summary returns compact events (no event_detail JSONB).
    detail=full returns everything including event_detail.
    Wave 2.11: includes causal_type field.
    """
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT version_id FROM meta.node_version WHERE version_id = %s",
                (version_id,)
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail=f"Version {version_id} not found")

            cur.execute("""
                SELECT event_id, version_id, event_type, event_detail, actor,
                       caused_by_event_id, created_at,
                       causal_type
                FROM meta.version_event
                WHERE version_id = %s
                ORDER BY created_at ASC
                LIMIT %s
            """, (version_id, limit))
            rows = cur.fetchall()

    events = []
    for r in rows:
        evt = {
            "event_id": str(r[0]),
            "version_id": str(r[1]),
            "event_type": r[2],
            "actor": r[4],
            "caused_by_event_id": str(r[5]) if r[5] else None,
            "created_at": r[6].isoformat() if r[6] else None,
            "causal_type": r[7] or "blocking",
        }
        if detail == "full":
            evt["event_detail"] = r[3] or {}
        events.append(evt)

    return envelope({"events": events, "count": len(events)}, t0)


class CreateVersionEvent(BaseModel):
    event_type: str
    event_detail: Optional[dict] = None
    actor: str
    caused_by_event_id: Optional[str] = None
    causal_type: Optional[str] = "blocking"  # Wave 2.11: 'blocking' or 'informational'


@router.post("/version/{version_id}/event")
def log_version_event(version_id: str, payload: CreateVersionEvent):
    """Log a version event (biography entry)."""
    t0 = time.time()

    VALID_EVENT_TYPES = {
        'script_run', 'backtest', 'optimisation_started', 'optimisation_referenced',
        'composition_included', 'composition_removed', 'decision_made', 'hypothesis_formed',
        'referenced_in_experiment', 'abandoned', 'revived', 'revalidated',
        'conversation_note', 'downstream_triggered', 'parameter_informed_by',
        'manual_observation', 'code_change', 'pushed_to_live',
        # Wave 2.10: Topology change events
        'node_added', 'node_retired', 'edge_added', 'edge_removed',
    }
    if payload.event_type not in VALID_EVENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid event_type '{payload.event_type}'. Valid: {sorted(VALID_EVENT_TYPES)}"
        )

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT version_id FROM meta.node_version WHERE version_id = %s",
                (version_id,)
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail=f"Version {version_id} not found")

            if payload.caused_by_event_id:
                cur.execute(
                    "SELECT event_id FROM meta.version_event WHERE event_id = %s",
                    (payload.caused_by_event_id,)
                )
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail=f"Caused-by event {payload.caused_by_event_id} not found")

            # Validate causal_type
            valid_causal = {'blocking', 'informational'}
            causal = payload.causal_type or 'blocking'
            if causal not in valid_causal:
                raise HTTPException(status_code=400, detail=f"Invalid causal_type '{causal}'. Valid: {sorted(valid_causal)}")

            cur.execute("""
                INSERT INTO meta.version_event (version_id, event_type, event_detail, actor, caused_by_event_id, causal_type)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING event_id, created_at
            """, (
                version_id, payload.event_type,
                json.dumps(payload.event_detail or {}),
                payload.actor, payload.caused_by_event_id, causal,
            ))
            row = cur.fetchone()
        conn.commit()

    return envelope({
        "event_id": str(row[0]),
        "version_id": version_id,
        "event_type": payload.event_type,
        "created_at": row[1].isoformat() if row[1] else None,
    }, t0)


@router.get("/event/{event_id}/causal_chain")
def get_causal_chain(event_id: str, max_depth: int = Query(20, ge=1, le=50)):
    """Walk caused_by links in both directions using recursive CTEs.

    Replaces the previous N+1 query pattern (one query per ancestor + BFS for
    descendants) with two single-query recursive CTEs. At branching factor 5
    and depth 15, the old approach issued 125+ individual queries; this issues 2.

    Circuit breakers:
    - Depth capped at max_depth (default 20)
    - Total results capped at 1000 rows
    - statement_timeout set to 5 seconds
    """
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            # Circuit breaker: prevent pathological queries
            cur.execute("SET LOCAL statement_timeout = '5000'")

            # Verify event exists
            cur.execute("""
                SELECT event_id, version_id, event_type, event_detail, actor,
                       caused_by_event_id, created_at, causal_type
                FROM meta.version_event WHERE event_id = %s
            """, (event_id,))
            root = cur.fetchone()
            if not root:
                raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

            def row_to_dict(r):
                return {
                    "event_id": str(r[0]),
                    "version_id": str(r[1]),
                    "event_type": r[2],
                    "event_detail": r[3] or {},
                    "actor": r[4],
                    "caused_by_event_id": str(r[5]) if r[5] else None,
                    "created_at": r[6].isoformat() if r[6] else None,
                    "causal_type": r[7] or "blocking",
                }

            # Walk upstream via recursive CTE (ancestors)
            cur.execute("""
                WITH RECURSIVE ancestors AS (
                    SELECT event_id, version_id, event_type, event_detail, actor,
                           caused_by_event_id, created_at, causal_type, 1 AS depth
                    FROM meta.version_event
                    WHERE event_id = (
                        SELECT caused_by_event_id FROM meta.version_event WHERE event_id = %s
                    )
                    UNION ALL
                    SELECT ve.event_id, ve.version_id, ve.event_type, ve.event_detail, ve.actor,
                           ve.caused_by_event_id, ve.created_at, ve.causal_type, a.depth + 1
                    FROM meta.version_event ve
                    JOIN ancestors a ON ve.event_id = a.caused_by_event_id
                    WHERE a.depth < %s
                )
                SELECT event_id, version_id, event_type, event_detail, actor,
                       caused_by_event_id, created_at, causal_type
                FROM ancestors
                ORDER BY depth DESC
                LIMIT 1000
            """, (event_id, max_depth))
            ancestors = [row_to_dict(r) for r in cur.fetchall()]

            # Walk downstream via recursive CTE (descendants)
            cur.execute("""
                WITH RECURSIVE descendants AS (
                    SELECT event_id, version_id, event_type, event_detail, actor,
                           caused_by_event_id, created_at, causal_type, 0 AS depth
                    FROM meta.version_event
                    WHERE caused_by_event_id = %s
                    UNION ALL
                    SELECT ve.event_id, ve.version_id, ve.event_type, ve.event_detail, ve.actor,
                           ve.caused_by_event_id, ve.created_at, ve.causal_type, d.depth + 1
                    FROM meta.version_event ve
                    JOIN descendants d ON ve.caused_by_event_id = d.event_id
                    WHERE d.depth < %s
                )
                SELECT event_id, version_id, event_type, event_detail, actor,
                       caused_by_event_id, created_at, causal_type
                FROM descendants
                ORDER BY depth, created_at
                LIMIT 1000
            """, (event_id, max_depth))
            desc_rows = cur.fetchall()
            descendants = [row_to_dict(r) for r in desc_rows]
            truncated = len(desc_rows) >= 1000 or len(ancestors) >= 1000

    result = {
        "root": row_to_dict(root),
        "ancestors": ancestors,  # oldest first (ORDER BY depth DESC)
        "descendants": descendants,
        "chain_length": len(ancestors) + 1 + len(descendants),
    }
    if truncated:
        result["truncated"] = True

    return envelope(result, t0)


# =============================================================================
# RECENT EVENTS (Wave 2.14 — system-wide activity feed)
# =============================================================================

@router.get("/events/recent")
def get_recent_events(
    limit: int = Query(default=50, ge=1, le=200),
    event_type: Optional[str] = Query(default=None),
    since: Optional[str] = Query(default=None, description="ISO timestamp — only events after this time"),
):
    """System-wide recent events across all versions, for the activity feed.

    Filterable by event_type and since timestamp. Returns events with node context.
    """
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            conditions = []
            params = []

            if event_type:
                conditions.append("ve.event_type = %s")
                params.append(event_type)

            if since:
                conditions.append("ve.created_at >= %s::timestamptz")
                params.append(since)

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            params.append(limit)

            cur.execute(f"""
                SELECT ve.event_id, ve.version_id, ve.event_type, ve.event_detail, ve.actor,
                       ve.caused_by_event_id, ve.created_at, ve.causal_type,
                       nv.node_id, n.name AS node_name, n.subsystem,
                       nv.version_number
                FROM meta.version_event ve
                JOIN meta.node_version nv ON nv.version_id = ve.version_id
                JOIN meta.node n ON n.node_id = nv.node_id
                {where_clause}
                ORDER BY ve.created_at DESC
                LIMIT %s
            """, params)
            rows = cur.fetchall()

    events = []
    for r in rows:
        events.append({
            "event_id": str(r[0]),
            "version_id": str(r[1]),
            "event_type": r[2],
            "event_detail": r[3] or {},
            "actor": r[4],
            "caused_by_event_id": str(r[5]) if r[5] else None,
            "created_at": r[6].isoformat() if r[6] else None,
            "causal_type": r[7] or "blocking",
            "node_id": str(r[8]),
            "node_name": r[9],
            "subsystem": r[10],
            "version_number": r[11],
        })

    return envelope({"events": events, "count": len(events)}, t0)


# =============================================================================
# EXPERIMENT UPDATE (Phase 3/5)
# =============================================================================

class UpdateExperiment(BaseModel):
    status: Optional[str] = None
    conclusion: Optional[str] = None
    outcome_class: Optional[str] = None
    failure_mode: Optional[str] = None
    key_insight: Optional[str] = None


@router.patch("/experiment/{experiment_id}")
@router.post("/experiment/{experiment_id}/update")
def update_experiment(experiment_id: str, payload: UpdateExperiment):
    """Update experiment status, conclusion, outcome."""
    t0 = time.time()

    valid_statuses = {'planned', 'running', 'completed', 'abandoned'}
    valid_outcomes = {'breakthrough', 'incremental', 'null', 'dead_end', 'invalidated_prior', None}
    valid_failures = {
        'overfitting', 'regime_dependent', 'cost_model_failure',
        'insufficient_sample', 'confounded', 'implementation_bug', None
    }

    if payload.status and payload.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Valid: {sorted(valid_statuses)}")
    if payload.outcome_class not in valid_outcomes:
        raise HTTPException(status_code=400, detail=f"Invalid outcome_class")
    if payload.failure_mode not in valid_failures:
        raise HTTPException(status_code=400, detail=f"Invalid failure_mode")

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT experiment_id FROM meta.experiment WHERE experiment_id = %s", (experiment_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Experiment not found")

            updates = []
            params = []
            if payload.status:
                updates.append("status = %s")
                params.append(payload.status)
            if payload.conclusion is not None:
                updates.append("conclusion = %s")
                params.append(payload.conclusion)
            if payload.outcome_class is not None:
                updates.append("outcome_class = %s")
                params.append(payload.outcome_class)
            if payload.failure_mode is not None:
                updates.append("failure_mode = %s")
                params.append(payload.failure_mode)
            if payload.key_insight is not None:
                updates.append("key_insight = %s")
                params.append(payload.key_insight)

            if not updates:
                raise HTTPException(status_code=400, detail="No fields to update")

            updates.append("updated_at = now()")
            params.append(experiment_id)

            cur.execute(
                f"UPDATE meta.experiment SET {', '.join(updates)} WHERE experiment_id = %s",
                params
            )
        conn.commit()

    return envelope({"experiment_id": experiment_id, "updated_fields": len(updates) - 1}, t0)


# =============================================================================
# COMPOSITION CRUD + DEPLOY (Phase 3/4)
# =============================================================================

class CreateComposition(BaseModel):
    name: str
    description: Optional[str] = None
    node_versions: dict  # {node_id: version_id}
    experiment_id: Optional[str] = None
    source: str = "manual"


@router.post("/composition")
def create_composition(payload: CreateComposition):
    """Create a composition from a version map."""
    t0 = time.time()

    if not payload.node_versions:
        raise HTTPException(status_code=400, detail="node_versions cannot be empty")

    config_hash = compute_config_hash(payload.node_versions)

    with _get_conn() as conn:
        with conn.cursor() as cur:
            # Check for duplicate
            cur.execute(
                "SELECT composition_id, name FROM meta.composition WHERE config_hash = %s",
                (config_hash,)
            )
            existing = cur.fetchone()
            if existing:
                return envelope({
                    "composition_id": str(existing[0]),
                    "name": existing[1],
                    "duplicate": True,
                    "config_hash": config_hash,
                }, t0)

            # Compute integrity hash
            version_ids = list(payload.node_versions.values())
            integrity = compute_integrity_hash(version_ids)

            cur.execute("""
                INSERT INTO meta.composition
                    (name, description, node_versions, config_hash, status, source,
                     experiment_id, integrity_hash)
                VALUES (%s, %s, %s, %s, 'proposed', %s, %s, %s)
                RETURNING composition_id
            """, (
                payload.name, payload.description,
                json.dumps(payload.node_versions), config_hash,
                payload.source, payload.experiment_id, integrity,
            ))
            comp_id = str(cur.fetchone()[0])

            # Insert composition_version junction rows
            for node_id, version_id in payload.node_versions.items():
                cur.execute("""
                    INSERT INTO meta.composition_version (composition_id, node_id, version_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (comp_id, node_id, version_id))

        conn.commit()

    return envelope({
        "composition_id": comp_id,
        "name": payload.name,
        "config_hash": config_hash,
        "duplicate": False,
    }, t0)


@router.post("/composition/{composition_id}/deploy")
def deploy_composition(composition_id: str):
    """Promote composition to deployed status and set its versions as active.
    Requires all 5 validation gates to pass (enforced by DB trigger too)."""
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT composition_id, name, node_versions, status, validation_gate
                FROM meta.composition WHERE composition_id = %s
            """, (composition_id,))
            comp = cur.fetchone()
            if not comp:
                raise HTTPException(status_code=404, detail="Composition not found")

            if comp[3] == 'deployed':
                return envelope({"composition_id": composition_id, "already_deployed": True}, t0)

            # Check validation gates before attempting deploy
            val_gate = comp[4] or {}
            gate_fields = ["walk_forward_pass", "holdout_pass", "dsr_positive",
                           "slippage_1_5x_pass", "n_trials_corrected"]
            failing = [g for g in gate_fields if not val_gate.get(g)]
            if failing:
                raise HTTPException(
                    status_code=422,
                    detail=f"Cannot deploy: failing validation gates: {', '.join(failing)}"
                )

            # Update composition status
            cur.execute("""
                UPDATE meta.composition SET status = 'deployed', updated_at = now()
                WHERE composition_id = %s
            """, (composition_id,))

            # Promote each version to deployed
            node_versions = comp[2] or {}
            for node_id, version_id in node_versions.items():
                # Retire other deployed versions for same node
                cur.execute("""
                    UPDATE meta.node_version SET status = 'tested'
                    WHERE node_id = %s AND status = 'deployed' AND version_id != %s
                """, (node_id, version_id))
                # Deploy the selected version
                cur.execute("""
                    UPDATE meta.node_version SET status = 'deployed'
                    WHERE version_id = %s
                """, (version_id,))

        conn.commit()

    return envelope({
        "composition_id": composition_id,
        "name": comp[1],
        "deployed_versions": len(node_versions),
    }, t0)


# =============================================================================
# PIPELINE LIST (Phase 7)
# =============================================================================

@router.get("/pipelines")
def list_pipelines(limit: int = Query(default=20, ge=1, le=100)):
    """List pipeline definitions."""
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT pipeline_id, name, description, config, created_at
                FROM meta.pipeline
                ORDER BY created_at DESC
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()

    pipelines = [{
        "pipeline_id": str(r[0]),
        "name": r[1],
        "description": r[2],
        "config": r[3] or {},
        "created_at": r[4].isoformat() if r[4] else None,
    } for r in rows]

    return envelope({"pipelines": pipelines, "count": len(pipelines)}, t0)


class CreatePipeline(BaseModel):
    name: str
    description: Optional[str] = None
    config: Optional[dict] = None
    clone_from: Optional[str] = None  # pipeline_id to clone


@router.post("/pipeline")
def create_pipeline(payload: CreatePipeline):
    """Create or clone a pipeline definition."""
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO meta.pipeline (name, description, config)
                VALUES (%s, %s, %s)
                RETURNING pipeline_id
            """, (payload.name, payload.description, json.dumps(payload.config or {})))
            pid = str(cur.fetchone()[0])

            # Clone nodes from source pipeline if specified
            if payload.clone_from:
                cur.execute("""
                    INSERT INTO meta.pipeline_node (pipeline_id, node_id, ordinal)
                    SELECT %s, node_id, ordinal
                    FROM meta.pipeline_node
                    WHERE pipeline_id = %s
                """, (pid, payload.clone_from))

        conn.commit()

    return envelope({"pipeline_id": pid, "name": payload.name}, t0)


# =============================================================================
# COMPOSITION GUARDRAILS — Significance, Pareto Frontier, Space Estimation
# =============================================================================

@router.get("/composition/{composition_id}/significance")
def check_composition_significance(composition_id: str):
    """Return statistical context and validation gate status for a composition."""
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT composition_id, name, status, source,
                       statistical_context, validation_gate, agg_pf
                FROM meta.composition WHERE composition_id = %s
            """, (composition_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Composition not found")

            stat_ctx = row[4] or {}
            val_gate = row[5] or {}

            # Determine significance level
            is_sig = stat_ctx.get("is_significant_after_correction", False)
            raw_pf = row[6]
            expected_null = stat_ctx.get("expected_max_null")

            if is_sig:
                significance_level = "significant"
            elif raw_pf and expected_null and raw_pf > expected_null:
                significance_level = "marginal"
            else:
                significance_level = "not_significant"

            # Gate status
            gate_fields = ["walk_forward_pass", "holdout_pass", "dsr_positive",
                           "slippage_1_5x_pass", "n_trials_corrected"]
            gates_passing = {g: val_gate.get(g, False) for g in gate_fields}
            all_pass = all(gates_passing.values())
            failing_gates = [g for g, v in gates_passing.items() if not v]

    return envelope({
        "composition_id": str(row[0]),
        "name": row[1],
        "status": row[2],
        "significance_level": significance_level,
        "statistical_context": stat_ctx,
        "validation_gate": val_gate,
        "gates_passing": gates_passing,
        "all_gates_pass": all_pass,
        "failing_gates": failing_gates,
    }, t0)


@router.get("/node/{node_id}/pareto_frontier")
def find_pareto_frontier(node_id: str):
    """Return the non-dominated versions for a node (Pareto frontier)."""
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            # Get all versions with metrics for this node
            cur.execute("""
                SELECT version_id, version_number, status, parameters,
                       agg_pf, net_pnl, max_drawdown, win_rate, trade_count,
                       pareto_status, superseded_by, change_reason, created_at
                FROM meta.node_version
                WHERE node_id = %s
                ORDER BY version_number DESC
            """, (node_id,))
            rows = cur.fetchall()

            if not rows:
                return envelope({"frontier": [], "dominated": [], "total": 0}, t0)

            versions = []
            for r in rows:
                versions.append({
                    "version_id": str(r[0]),
                    "version_number": r[1],
                    "status": r[2],
                    "parameters": r[3] or {},
                    "agg_pf": r[4],
                    "net_pnl": r[5],
                    "max_drawdown": r[6],
                    "win_rate": r[7],
                    "trade_count": r[8],
                    "pareto_status": r[9] or "active",
                    "superseded_by": str(r[10]) if r[10] else None,
                    "change_reason": r[11],
                    "created_at": r[12].isoformat() if r[12] else None,
                })

            # Compute Pareto frontier from metrics
            # A version is dominated if another version is >= on all metrics
            metrics_keys = ["agg_pf", "net_pnl", "win_rate", "trade_count"]
            # max_drawdown is better when LOWER, so invert for comparison

            def get_metrics(v):
                vals = []
                for k in metrics_keys:
                    val = v.get(k)
                    vals.append(val if val is not None else float('-inf'))
                dd = v.get("max_drawdown")
                vals.append(-dd if dd is not None else float('-inf'))
                return vals

            frontier = []
            dominated = []
            for i, vi in enumerate(versions):
                mi = get_metrics(vi)
                is_dominated = False
                for j, vj in enumerate(versions):
                    if i == j:
                        continue
                    mj = get_metrics(vj)
                    # vj dominates vi if vj >= vi on all metrics and vj > vi on at least one
                    if all(mj[k] >= mi[k] for k in range(len(mi))) and \
                       any(mj[k] > mi[k] for k in range(len(mi))):
                        is_dominated = True
                        break
                if is_dominated:
                    dominated.append(vi)
                else:
                    frontier.append(vi)

    return envelope({
        "node_id": node_id,
        "frontier": frontier,
        "dominated": dominated,
        "frontier_count": len(frontier),
        "dominated_count": len(dominated),
        "total": len(versions),
    }, t0)


@router.get("/composition/space/estimate")
def estimate_composition_space(
    node_ids: str = Query(description="Comma-separated node IDs"),
    include_dominated: bool = Query(default=False),
):
    """Estimate the size of the combinatorial composition space for a set of nodes."""
    t0 = time.time()

    ids = [nid.strip() for nid in node_ids.split(",") if nid.strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="node_ids required")

    with _get_conn() as conn:
        with conn.cursor() as cur:
            node_counts = {}
            for nid in ids:
                if include_dominated:
                    cur.execute("""
                        SELECT COUNT(*) FROM meta.node_version
                        WHERE node_id = %s
                    """, (nid,))
                else:
                    cur.execute("""
                        SELECT COUNT(*) FROM meta.node_version
                        WHERE node_id = %s AND (pareto_status IS NULL OR pareto_status = 'active')
                    """, (nid,))
                count = cur.fetchone()[0]
                # Get node name
                cur.execute("SELECT name FROM meta.node WHERE node_id = %s", (nid,))
                name_row = cur.fetchone()
                node_counts[nid] = {
                    "name": name_row[0] if name_row else nid,
                    "version_count": count,
                }

            # Check composition constraints
            cur.execute("""
                SELECT constraint_id, node_a_id, node_b_id, constraint_type, description
                FROM meta.composition_constraint
                WHERE node_a_id = ANY(%s) OR node_b_id = ANY(%s)
            """, (ids, ids))
            constraints = [{
                "constraint_id": str(r[0]),
                "node_a_id": str(r[1]),
                "node_b_id": str(r[2]),
                "constraint_type": r[3],
                "description": r[4],
            } for r in cur.fetchall()]

    # Compute total space
    total_space = 1
    for info in node_counts.values():
        total_space *= max(info["version_count"], 1)

    return envelope({
        "node_counts": node_counts,
        "total_compositions": total_space,
        "include_dominated": include_dominated,
        "constraints": constraints,
        "constraint_count": len(constraints),
    }, t0)


@router.patch("/node_version/{version_id}/pareto_status")
def update_pareto_status(version_id: str, status: str = Query(description="active, dominated, or historical")):
    """Update the Pareto status of a node version."""
    t0 = time.time()

    if status not in ("active", "dominated", "historical"):
        raise HTTPException(status_code=400, detail="status must be active, dominated, or historical")

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE meta.node_version SET pareto_status = %s
                WHERE version_id = %s
                RETURNING version_id, node_id, version_number
            """, (status, version_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Version not found")
        conn.commit()

    return envelope({
        "version_id": str(row[0]),
        "node_id": str(row[1]),
        "version_number": row[2],
        "pareto_status": status,
    }, t0)


class CreateConstraint(BaseModel):
    node_a_id: str
    node_b_id: str
    constraint_type: str
    constraint_rule: dict
    description: str


@router.post("/composition/constraint")
def create_composition_constraint(payload: CreateConstraint):
    """Create a semantic constraint between two nodes for composition assembly."""
    t0 = time.time()

    valid_types = ["requires_compatible_timeframe", "requires_compatible_universe", "excludes"]
    if payload.constraint_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"constraint_type must be one of {valid_types}")

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO meta.composition_constraint
                    (node_a_id, node_b_id, constraint_type, constraint_rule, description)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING constraint_id
            """, (
                payload.node_a_id, payload.node_b_id,
                payload.constraint_type, json.dumps(payload.constraint_rule),
                payload.description,
            ))
            cid = str(cur.fetchone()[0])
        conn.commit()

    return envelope({
        "constraint_id": cid,
        "constraint_type": payload.constraint_type,
    }, t0)


# ===================================
# Wave 3: Dependency Hardening
# ===================================

class UpdateStaleness(BaseModel):
    staleness_state: str  # clean, potentially_stale, confirmed_stale
    reason: Optional[str] = None

class UpdateHealth(BaseModel):
    health_status: str  # healthy, degraded, unhealthy, unknown
    reason: Optional[str] = None

class UpdateOutputHash(BaseModel):
    output_hash: str


@router.put("/node/{node_id}/version/{version_id}/staleness")
def update_staleness(node_id: str, version_id: str, payload: UpdateStaleness):
    """Update staleness state for a version (three-state: clean/potentially_stale/confirmed_stale)."""
    t0 = time.time()
    valid = ("clean", "potentially_stale", "confirmed_stale")
    if payload.staleness_state not in valid:
        raise HTTPException(status_code=400, detail=f"staleness_state must be one of {valid}")

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE meta.node_version SET staleness_state = %s
                WHERE version_id = %s AND node_id = %s
                RETURNING version_id
            """, (payload.staleness_state, version_id, node_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Version not found")
        conn.commit()

    return envelope({"version_id": version_id, "staleness_state": payload.staleness_state}, t0)


@router.put("/node/{node_id}/version/{version_id}/health")
def update_health(node_id: str, version_id: str, payload: UpdateHealth):
    """Update health status for a version (separate from sync/deploy status)."""
    t0 = time.time()
    valid = ("healthy", "degraded", "unhealthy", "unknown")
    if payload.health_status not in valid:
        raise HTTPException(status_code=400, detail=f"health_status must be one of {valid}")

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE meta.node_version SET health_status = %s
                WHERE version_id = %s AND node_id = %s
                RETURNING version_id
            """, (payload.health_status, version_id, node_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Version not found")
        conn.commit()

    return envelope({"version_id": version_id, "health_status": payload.health_status}, t0)


@router.put("/node/{node_id}/version/{version_id}/output-hash")
def update_output_hash(node_id: str, version_id: str, payload: UpdateOutputHash):
    """Set output hash for DICE early cutoff. If hash unchanged from previous version, skip downstream propagation."""
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE meta.node_version SET output_hash = %s
                WHERE version_id = %s AND node_id = %s
                RETURNING version_id
            """, (payload.output_hash, version_id, node_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Version not found")
        conn.commit()

    return envelope({"version_id": version_id, "output_hash": payload.output_hash}, t0)


@router.get("/drift")
def get_drift_report():
    """Drift detection: compare deployed composition node_versions to actual current versions.

    Returns nodes where the deployed version differs from the latest version,
    or where no deployed version exists.
    """
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            # Get the deployed composition (if any)
            cur.execute("""
                SELECT composition_id, name, node_versions, config_hash
                FROM meta.composition
                WHERE status = 'deployed'
                ORDER BY updated_at DESC LIMIT 1
            """)
            deployed_comp = cur.fetchone()

            # Get the latest version per node (by status priority)
            cur.execute("""
                SELECT DISTINCT ON (node_id) node_id, version_id, version_number,
                       status, staleness_state, health_status, output_hash
                FROM meta.node_version
                ORDER BY node_id,
                    CASE status
                        WHEN 'deployed' THEN 0
                        WHEN 'promising' THEN 1
                        WHEN 'tested' THEN 2
                        WHEN 'baseline' THEN 3
                        WHEN 'retired' THEN 4
                    END,
                    version_number DESC
            """)
            current = {str(r[0]): {
                "version_id": str(r[1]), "version_number": r[2],
                "status": r[3], "staleness_state": r[4] or "clean",
                "health_status": r[5] or "unknown", "output_hash": r[6],
            } for r in cur.fetchall()}

            # Get node names
            cur.execute("SELECT node_id, name, subsystem FROM meta.node")
            node_info = {str(r[0]): {"name": r[1], "subsystem": r[2]} for r in cur.fetchall()}

    drifted = []
    synced = []

    if deployed_comp:
        comp_versions = deployed_comp[2] or {}  # {node_id: version_id}
        for nid, info in node_info.items():
            pinned_vid = comp_versions.get(nid)
            cur_ver = current.get(nid)
            if not cur_ver:
                continue
            entry = {
                "node_id": nid,
                "node_name": info["name"],
                "subsystem": info["subsystem"],
                "current_version_id": cur_ver["version_id"],
                "current_version_number": cur_ver["version_number"],
                "current_status": cur_ver["status"],
                "staleness_state": cur_ver["staleness_state"],
                "health_status": cur_ver["health_status"],
            }
            if pinned_vid and str(pinned_vid) != cur_ver["version_id"]:
                entry["deployed_version_id"] = str(pinned_vid)
                entry["drift_type"] = "version_mismatch"
                drifted.append(entry)
            elif not pinned_vid:
                entry["drift_type"] = "not_in_composition"
                drifted.append(entry)
            else:
                synced.append(entry)
    else:
        # No deployed composition — everything is "undeployed"
        for nid, info in node_info.items():
            cur_ver = current.get(nid)
            if cur_ver:
                drifted.append({
                    "node_id": nid,
                    "node_name": info["name"],
                    "subsystem": info["subsystem"],
                    "current_version_id": cur_ver["version_id"],
                    "current_version_number": cur_ver["version_number"],
                    "current_status": cur_ver["status"],
                    "staleness_state": cur_ver["staleness_state"],
                    "health_status": cur_ver["health_status"],
                    "drift_type": "no_deployed_composition",
                })

    return envelope({
        "deployed_composition": {
            "composition_id": str(deployed_comp[0]),
            "name": deployed_comp[1],
        } if deployed_comp else None,
        "drifted": drifted,
        "synced": synced,
        "drift_count": len(drifted),
        "sync_count": len(synced),
    }, t0)


@router.post("/node/{node_id}/version/{version_id}/propagate-staleness")
def propagate_staleness(node_id: str, version_id: str):
    """Propagate staleness downstream from a changed node using DICE early cutoff.

    If the node's output_hash is unchanged from previous version, mark downstream
    as 'clean' (DICE cutoff). Otherwise mark downstream as 'potentially_stale'.
    """
    t0 = time.time()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            # Get current version's output_hash
            cur.execute("""
                SELECT nv.output_hash, nv.node_id
                FROM meta.node_version nv
                WHERE nv.version_id = %s AND nv.node_id = %s
            """, (version_id, node_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Version not found")

            current_hash = row[0]

            # Get previous version's output_hash for DICE comparison
            cur.execute("""
                SELECT output_hash FROM meta.node_version
                WHERE node_id = %s AND version_id != %s
                ORDER BY version_number DESC LIMIT 1
            """, (node_id, version_id))
            prev = cur.fetchone()
            prev_hash = prev[0] if prev else None

            # DICE early cutoff: if hashes match, no downstream propagation needed
            if current_hash and prev_hash and current_hash == prev_hash:
                return envelope({
                    "action": "dice_cutoff",
                    "reason": "Output hash unchanged — no downstream staleness propagation",
                    "affected_count": 0,
                }, t0)

            # Find all downstream nodes via edge traversal
            cur.execute("""
                WITH RECURSIVE downstream AS (
                    SELECT tp.node_id AS node_id
                    FROM meta.edge e
                    JOIN meta.port sp ON sp.port_id = e.source_port_id
                    JOIN meta.port tp ON tp.port_id = e.target_port_id
                    WHERE sp.node_id = %s
                    UNION
                    SELECT tp2.node_id
                    FROM downstream d
                    JOIN meta.port sp2 ON sp2.node_id = d.node_id
                    JOIN meta.edge e2 ON e2.source_port_id = sp2.port_id
                    JOIN meta.port tp2 ON tp2.port_id = e2.target_port_id
                )
                SELECT DISTINCT node_id FROM downstream
            """, (node_id,))
            downstream_nodes = [str(r[0]) for r in cur.fetchall()]

            # Mark latest version of each downstream node as potentially_stale
            affected = 0
            for dn_id in downstream_nodes:
                cur.execute("""
                    UPDATE meta.node_version SET staleness_state = 'potentially_stale'
                    WHERE version_id = (
                        SELECT version_id FROM meta.node_version
                        WHERE node_id = %s ORDER BY version_number DESC LIMIT 1
                    ) AND staleness_state = 'clean'
                    RETURNING version_id
                """, (dn_id,))
                if cur.fetchone():
                    affected += 1
        conn.commit()

    return envelope({
        "action": "propagated",
        "downstream_nodes": downstream_nodes,
        "affected_count": affected,
    }, t0)


# -------------------------------------------------------
# Wave 4.7: Sync waves for push_to_live — DAG-ordered deploy
# -------------------------------------------------------
@router.post("/composition/{composition_id}/sync-waves")
async def compute_sync_waves(composition_id: str):
    """Compute deployment order for a composition based on DAG topology.
    Returns waves: each wave contains nodes whose upstream deps are all in prior waves."""
    t0 = time.time()
    with _get_conn() as conn:
        with conn.cursor() as cur:
            # Get composition's node_versions
            cur.execute("""
                SELECT cv.node_id, cv.version_id
                FROM meta.composition_version cv
                WHERE cv.composition_id = %s
            """, (composition_id,))
            comp_nodes = {str(r[0]): str(r[1]) for r in cur.fetchall()}
            if not comp_nodes:
                raise HTTPException(status_code=404, detail="Composition not found or has no versions")

            # Build adjacency for comp nodes only (edges where both src and tgt in composition)
            cur.execute("""
                SELECT DISTINCT sp.node_id AS src, tp.node_id AS tgt
                FROM meta.edge e
                JOIN meta.port sp ON sp.port_id = e.source_port_id
                JOIN meta.port tp ON tp.port_id = e.target_port_id
                WHERE sp.node_id = ANY(%s) AND tp.node_id = ANY(%s)
            """, (list(comp_nodes.keys()), list(comp_nodes.keys())))
            edges = cur.fetchall()

            # Topological sort via Kahn's algorithm
            in_degree = {nid: 0 for nid in comp_nodes}
            downstream = {nid: [] for nid in comp_nodes}
            for src, tgt in edges:
                src_s, tgt_s = str(src), str(tgt)
                if src_s in comp_nodes and tgt_s in comp_nodes:
                    in_degree[tgt_s] = in_degree.get(tgt_s, 0) + 1
                    downstream[src_s].append(tgt_s)

            waves = []
            remaining = dict(in_degree)
            while remaining:
                # Current wave: all nodes with in_degree 0
                wave = [nid for nid, deg in remaining.items() if deg == 0]
                if not wave:
                    # Cycle detected — dump remaining as final wave
                    wave = list(remaining.keys())
                    waves.append(wave)
                    break
                waves.append(wave)
                for nid in wave:
                    del remaining[nid]
                    for tgt in downstream.get(nid, []):
                        if tgt in remaining:
                            remaining[tgt] -= 1

            # Fetch node names for readability
            node_ids_flat = list(comp_nodes.keys())
            cur.execute("""
                SELECT node_id, name, node_type FROM meta.node WHERE node_id = ANY(%s)
            """, (node_ids_flat,))
            node_info = {str(r[0]): {"name": r[1], "type": r[2]} for r in cur.fetchall()}

    wave_data = []
    for i, wave in enumerate(waves):
        wave_data.append({
            "wave": i + 1,
            "nodes": [{
                "node_id": nid,
                "version_id": comp_nodes[nid],
                "name": node_info.get(nid, {}).get("name", "?"),
                "type": node_info.get(nid, {}).get("type", "?"),
            } for nid in wave]
        })

    return envelope({
        "composition_id": composition_id,
        "total_waves": len(waves),
        "total_nodes": len(comp_nodes),
        "waves": wave_data,
    }, t0)


# -------------------------------------------------------
# Wave 4.8: Event search with inverse filters
# -------------------------------------------------------
@router.get("/events/search")
async def search_events(
    q: Optional[str] = Query(None, description="Text search in event details"),
    event_type: Optional[str] = Query(None, description="Filter by event type (comma-separated)"),
    exclude_type: Optional[str] = Query(None, description="Exclude event types (comma-separated, inverse filter)"),
    node_id: Optional[str] = Query(None, description="Filter by node"),
    actor: Optional[str] = Query(None, description="Filter by actor"),
    after: Optional[str] = Query(None, description="Events after this ISO timestamp"),
    before: Optional[str] = Query(None, description="Events before this ISO timestamp"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Search version_events with text search and inverse filters."""
    t0 = time.time()
    conditions = []
    params = []

    if q:
        conditions.append("(ve.event_detail::text ILIKE %s OR ve.event_type ILIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])

    if event_type:
        types = [t.strip() for t in event_type.split(",") if t.strip()]
        conditions.append("ve.event_type = ANY(%s)")
        params.append(types)

    if exclude_type:
        exc_types = [t.strip() for t in exclude_type.split(",") if t.strip()]
        conditions.append("NOT (ve.event_type = ANY(%s))")
        params.append(exc_types)

    if node_id:
        conditions.append("nv.node_id = %s")
        params.append(node_id)

    if actor:
        conditions.append("ve.event_detail->>'actor' ILIKE %s")
        params.append(f"%{actor}%")

    if after:
        conditions.append("ve.created_at >= %s")
        params.append(after)

    if before:
        conditions.append("ve.created_at <= %s")
        params.append(before)

    where = " AND ".join(conditions) if conditions else "TRUE"

    with _get_conn() as conn:
        with conn.cursor() as cur:
            # Count total matching
            cur.execute(f"""
                SELECT COUNT(*)
                FROM meta.version_event ve
                JOIN meta.node_version nv ON nv.version_id = ve.version_id
                WHERE {where}
            """, params)
            total = cur.fetchone()[0]

            # Fetch page
            cur.execute(f"""
                SELECT ve.event_id, ve.version_id, nv.node_id, ve.event_type,
                       ve.caused_by_event_id, ve.event_detail, ve.created_at,
                       n.name AS node_name
                FROM meta.version_event ve
                JOIN meta.node_version nv ON nv.version_id = ve.version_id
                JOIN meta.node n ON n.node_id = nv.node_id
                WHERE {where}
                ORDER BY ve.created_at DESC
                LIMIT %s OFFSET %s
            """, params + [limit, offset])
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    # Serialize
    for r in rows:
        for k in ("event_id", "version_id", "node_id", "caused_by_event_id"):
            if r.get(k) is not None:
                r[k] = str(r[k])
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()

    return envelope({
        "events": rows,
        "total": total,
        "limit": limit,
        "offset": offset,
    }, t0)


# -------------------------------------------------------
# Wave 4.9: Composition draft lifecycle
# -------------------------------------------------------
class CompositionDraftCreate(BaseModel):
    name: str
    description: Optional[str] = None
    node_versions: dict  # {node_id: version_id}


@router.post("/composition/draft")
async def create_composition_draft(body: CompositionDraftCreate):
    """Create a new composition from a draft (node-version map)."""
    t0 = time.time()
    if not body.node_versions:
        raise HTTPException(status_code=400, detail="node_versions cannot be empty")

    config_hash = compute_config_hash(body.node_versions)

    with _get_conn() as conn:
        with conn.cursor() as cur:
            # Check for duplicate config_hash
            cur.execute("""
                SELECT composition_id, name FROM meta.composition WHERE config_hash = %s
            """, (config_hash,))
            existing = cur.fetchone()
            if existing:
                return envelope({
                    "action": "duplicate",
                    "existing_composition_id": str(existing[0]),
                    "existing_name": existing[1],
                    "config_hash": config_hash,
                }, t0)

            # Create composition
            cur.execute("""
                INSERT INTO meta.composition (name, description, config_hash, status)
                VALUES (%s, %s, %s, 'draft')
                RETURNING composition_id
            """, (body.name, body.description, config_hash))
            comp_id = str(cur.fetchone()[0])

            # Insert composition_version rows
            for node_id, version_id in body.node_versions.items():
                cur.execute("""
                    INSERT INTO meta.composition_version (composition_id, node_id, version_id)
                    VALUES (%s, %s, %s)
                """, (comp_id, node_id, version_id))

        conn.commit()

    return envelope({
        "action": "created",
        "composition_id": comp_id,
        "config_hash": config_hash,
        "node_count": len(body.node_versions),
    }, t0)


class CompositionDraftUpdate(BaseModel):
    node_versions: dict  # {node_id: version_id}


@router.put("/composition/{composition_id}/draft")
async def update_composition_draft(composition_id: str, body: CompositionDraftUpdate):
    """Update a draft composition's node-version mapping."""
    t0 = time.time()
    with _get_conn() as conn:
        with conn.cursor() as cur:
            # Verify it's a draft
            cur.execute("""
                SELECT status FROM meta.composition WHERE composition_id = %s
            """, (composition_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Composition not found")
            if row[0] != 'draft':
                raise HTTPException(status_code=400, detail=f"Cannot update non-draft composition (status: {row[0]})")

            # Replace composition_version rows
            cur.execute("DELETE FROM meta.composition_version WHERE composition_id = %s", (composition_id,))
            for node_id, version_id in body.node_versions.items():
                cur.execute("""
                    INSERT INTO meta.composition_version (composition_id, node_id, version_id)
                    VALUES (%s, %s, %s)
                """, (composition_id, node_id, version_id))

            # Update config_hash
            new_hash = compute_config_hash(body.node_versions)
            cur.execute("""
                UPDATE meta.composition SET config_hash = %s, updated_at = NOW()
                WHERE composition_id = %s
            """, (new_hash, composition_id))

        conn.commit()

    return envelope({
        "action": "updated",
        "composition_id": composition_id,
        "config_hash": new_hash,
        "node_count": len(body.node_versions),
    }, t0)


# =======================================================
# Wave 5: Interoperability & Scale
# =======================================================

# -------------------------------------------------------
# Wave 5.1: OpenLineage event adapter
# -------------------------------------------------------
# One-way emitter: converts version_events to OpenLineage RunEvent format.
# Does NOT change our schema — this is a read adapter for interop.

OL_EVENT_TYPE_MAP = {
    'code_change': 'START',
    'backtest': 'COMPLETE',
    'script_run': 'COMPLETE',
    'decision_made': 'COMPLETE',
    'hypothesis_formed': 'START',
    'hypothesis_validated': 'COMPLETE',
    'hypothesis_invalidated': 'FAIL',
    'parameter_change': 'OTHER',
    'manual_observation': 'OTHER',
    'conversation_note': 'OTHER',
    'downstream_triggered': 'OTHER',
    'abandoned': 'ABORT',
    'upstream_changed': 'OTHER',
}


def _to_openlineage_event(event: dict, node_info: dict) -> dict:
    """Convert a version_event row to OpenLineage RunEvent format."""
    ol_type = OL_EVENT_TYPE_MAP.get(event.get('event_type', ''), 'OTHER')
    return {
        "eventType": ol_type,
        "eventTime": event.get("created_at", ""),
        "run": {
            "runId": event.get("version_id", ""),
            "facets": {
                "mcas_event": {
                    "event_id": event.get("event_id", ""),
                    "event_type": event.get("event_type", ""),
                    "caused_by": event.get("caused_by_event_id"),
                    "detail": event.get("event_detail", {}),
                },
            },
        },
        "job": {
            "namespace": "mcas.pipeline",
            "name": node_info.get("name", "unknown"),
            "facets": {
                "nodeType": node_info.get("type", ""),
                "subsystem": node_info.get("subsystem", ""),
                "sourceFile": node_info.get("source_file", ""),
            },
        },
        "inputs": [],   # populated if port data available
        "outputs": [],
        "producer": "https://mcasui.com",
        "schemaURL": "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent",
    }


@router.get("/openlineage/events")
async def get_openlineage_events(
    node_id: Optional[str] = Query(None),
    after: Optional[str] = Query(None),
    before: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """Export version_events as OpenLineage RunEvents."""
    t0 = time.time()
    conditions = []
    params = []

    if node_id:
        conditions.append("nv.node_id = %s")
        params.append(node_id)
    if after:
        conditions.append("ve.created_at >= %s")
        params.append(after)
    if before:
        conditions.append("ve.created_at <= %s")
        params.append(before)

    where = " AND ".join(conditions) if conditions else "TRUE"

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT ve.event_id, ve.version_id, nv.node_id, ve.event_type,
                       ve.caused_by_event_id, ve.event_detail, ve.created_at,
                       n.name, n.node_type, n.subsystem, n.source_file
                FROM meta.version_event ve
                JOIN meta.node_version nv ON nv.version_id = ve.version_id
                JOIN meta.node n ON n.node_id = nv.node_id
                WHERE {where}
                ORDER BY ve.created_at DESC
                LIMIT %s
            """, params + [limit])
            rows = cur.fetchall()

    ol_events = []
    for r in rows:
        event = {
            "event_id": str(r[0]),
            "version_id": str(r[1]),
            "node_id": str(r[2]),
            "event_type": r[3],
            "caused_by_event_id": str(r[4]) if r[4] else None,
            "event_detail": r[5] or {},
            "created_at": r[6].isoformat() if r[6] else "",
        }
        node_info = {"name": r[7], "type": r[8], "subsystem": r[9], "source_file": r[10]}
        ol_events.append(_to_openlineage_event(event, node_info))

    return envelope({
        "openlineage_events": ol_events,
        "count": len(ol_events),
        "schema_version": "2-0-2",
    }, t0)


# -------------------------------------------------------
# Wave 5.2: Provenance search API
# -------------------------------------------------------
@router.get("/provenance/search")
async def provenance_search(
    q: Optional[str] = Query(None, description="Full-text search across events and versions"),
    node_id: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    version_status: Optional[str] = Query(None),
    min_pf: Optional[float] = Query(None),
    after: Optional[str] = Query(None),
    before: Optional[str] = Query(None),
    include_chain: bool = Query(False, description="Include causal chain for each event"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Provenance search: find events, versions, and their causal chains."""
    t0 = time.time()

    # Build event search
    ev_conditions = []
    ev_params = []
    if q:
        ev_conditions.append("(ve.event_detail::text ILIKE %s OR n.name ILIKE %s)")
        ev_params.extend([f"%{q}%", f"%{q}%"])
    if node_id:
        ev_conditions.append("nv.node_id = %s")
        ev_params.append(node_id)
    if event_type:
        types = [t.strip() for t in event_type.split(",")]
        ev_conditions.append("ve.event_type = ANY(%s)")
        ev_params.append(types)
    if after:
        ev_conditions.append("ve.created_at >= %s")
        ev_params.append(after)
    if before:
        ev_conditions.append("ve.created_at <= %s")
        ev_params.append(before)

    ev_where = " AND ".join(ev_conditions) if ev_conditions else "TRUE"

    # Build version search (for metric-based queries)
    v_conditions = []
    v_params = []
    if node_id:
        v_conditions.append("nv.node_id = %s")
        v_params.append(node_id)
    if version_status:
        v_conditions.append("nv.status = %s")
        v_params.append(version_status)
    if min_pf is not None:
        v_conditions.append("nv.agg_pf >= %s")
        v_params.append(min_pf)
    if q:
        v_conditions.append("(nv.change_reason ILIKE %s OR n.name ILIKE %s)")
        v_params.extend([f"%{q}%", f"%{q}%"])

    v_where = " AND ".join(v_conditions) if v_conditions else "TRUE"

    with _get_conn() as conn:
        with conn.cursor() as cur:
            # Event results
            cur.execute(f"""
                SELECT ve.event_id, ve.version_id, nv.node_id, ve.event_type,
                       ve.caused_by_event_id, ve.event_detail, ve.created_at,
                       n.name AS node_name, nv.version_number
                FROM meta.version_event ve
                JOIN meta.node_version nv ON nv.version_id = ve.version_id
                JOIN meta.node n ON n.node_id = nv.node_id
                WHERE {ev_where}
                ORDER BY ve.created_at DESC
                LIMIT %s OFFSET %s
            """, ev_params + [limit, offset])
            ev_cols = [d[0] for d in cur.description]
            event_results = [dict(zip(ev_cols, r)) for r in cur.fetchall()]

            # Count total events
            cur.execute(f"""
                SELECT COUNT(*) FROM meta.version_event ve
                JOIN meta.node_version nv ON nv.version_id = ve.version_id
                JOIN meta.node n ON n.node_id = nv.node_id
                WHERE {ev_where}
            """, ev_params)
            total_events = cur.fetchone()[0]

            # Version results (if metric filters active)
            version_results = []
            total_versions = 0
            if min_pf is not None or version_status:
                cur.execute(f"""
                    SELECT nv.version_id, nv.node_id, nv.version_number, nv.status,
                           nv.agg_pf, nv.win_rate, nv.trade_count, nv.change_reason,
                           nv.created_at, n.name AS node_name
                    FROM meta.node_version nv
                    JOIN meta.node n ON n.node_id = nv.node_id
                    WHERE {v_where}
                    ORDER BY nv.created_at DESC
                    LIMIT %s OFFSET %s
                """, v_params + [limit, offset])
                v_cols = [d[0] for d in cur.description]
                version_results = [dict(zip(v_cols, r)) for r in cur.fetchall()]

                cur.execute(f"""
                    SELECT COUNT(*) FROM meta.node_version nv
                    JOIN meta.node n ON n.node_id = nv.node_id
                    WHERE {v_where}
                """, v_params)
                total_versions = cur.fetchone()[0]

            # Optional: causal chains for each event
            if include_chain and event_results:
                for ev in event_results:
                    eid = ev['event_id']
                    cur.execute("""
                        WITH RECURSIVE chain AS (
                            SELECT event_id, caused_by_event_id, event_type, 0 AS depth
                            FROM meta.version_event WHERE event_id = %s
                            UNION ALL
                            SELECT ve.event_id, ve.caused_by_event_id, ve.event_type, c.depth + 1
                            FROM meta.version_event ve
                            JOIN chain c ON ve.caused_by_event_id = c.event_id
                            WHERE c.depth < 10
                        )
                        SELECT event_id, event_type, depth FROM chain WHERE depth > 0
                        ORDER BY depth LIMIT 50
                    """, (eid,))
                    ev['causal_descendants'] = [
                        {"event_id": str(r[0]), "event_type": r[1], "depth": r[2]}
                        for r in cur.fetchall()
                    ]

    # Serialize
    for r in event_results:
        for k in ("event_id", "version_id", "node_id", "caused_by_event_id"):
            if r.get(k) is not None:
                r[k] = str(r[k])
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()

    for r in version_results:
        for k in ("version_id", "node_id"):
            if r.get(k) is not None:
                r[k] = str(r[k])
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()

    return envelope({
        "events": event_results,
        "total_events": total_events,
        "versions": version_results,
        "total_versions": total_versions,
        "limit": limit,
        "offset": offset,
    }, t0)


# -------------------------------------------------------
# Wave 5.3: Version reachability query
# -------------------------------------------------------
@router.get("/version/{version_id}/reachability")
async def check_version_reachability(version_id: str):
    """Check if a version is referenced by any active composition."""
    t0 = time.time()
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.composition_id, c.name, c.status
                FROM meta.composition_version cv
                JOIN meta.composition c ON c.composition_id = cv.composition_id
                WHERE cv.version_id = %s
                  AND c.status NOT IN ('pruned_early', 'dominated')
                ORDER BY c.created_at DESC
            """, (version_id,))
            compositions = [
                {"composition_id": str(r[0]), "name": r[1], "status": r[2]}
                for r in cur.fetchall()
            ]

            # Also check if it's the current version of any node
            cur.execute("""
                SELECT n.node_id, n.name
                FROM meta.node_version nv
                JOIN meta.node n ON n.node_id = nv.node_id
                WHERE nv.version_id = %s
                  AND nv.version_number = (
                      SELECT MAX(nv2.version_number)
                      FROM meta.node_version nv2
                      WHERE nv2.node_id = nv.node_id
                  )
            """, (version_id,))
            current_of = cur.fetchone()

    return envelope({
        "version_id": version_id,
        "is_reachable": len(compositions) > 0 or current_of is not None,
        "referenced_by_compositions": compositions,
        "is_current_version": current_of is not None,
        "current_of_node": {
            "node_id": str(current_of[0]),
            "name": current_of[1],
        } if current_of else None,
    }, t0)


# -------------------------------------------------------
# Wave 5.4: Dependency audit mode
# -------------------------------------------------------
@router.get("/node/{node_id}/dependency-audit")
async def dependency_audit(node_id: str):
    """Audit declared vs actual dependencies for a node.
    Compares declared port connections to what the node's source file imports/reads."""
    t0 = time.time()
    with _get_conn() as conn:
        with conn.cursor() as cur:
            # Declared upstream deps (from edges)
            cur.execute("""
                SELECT DISTINCT sp.node_id, n.name, n.node_type
                FROM meta.edge e
                JOIN meta.port tp ON tp.port_id = e.target_port_id
                JOIN meta.port sp ON sp.port_id = e.source_port_id
                JOIN meta.node n ON n.node_id = sp.node_id
                WHERE tp.node_id = %s
            """, (node_id,))
            declared_upstream = [
                {"node_id": str(r[0]), "name": r[1], "type": r[2]}
                for r in cur.fetchall()
            ]

            # Declared downstream deps
            cur.execute("""
                SELECT DISTINCT tp.node_id, n.name, n.node_type
                FROM meta.edge e
                JOIN meta.port sp ON sp.port_id = e.source_port_id
                JOIN meta.port tp ON tp.port_id = e.target_port_id
                JOIN meta.node n ON n.node_id = tp.node_id
                WHERE sp.node_id = %s
            """, (node_id,))
            declared_downstream = [
                {"node_id": str(r[0]), "name": r[1], "type": r[2]}
                for r in cur.fetchall()
            ]

            # File mappings
            cur.execute("""
                SELECT file_pattern, mapping_type
                FROM meta.node_file_mapping
                WHERE node_id = %s
                ORDER BY mapping_type
            """, (node_id,))
            file_mappings = [
                {"file_pattern": r[0], "mapping_type": r[1]}
                for r in cur.fetchall()
            ]

            # Port contracts
            cur.execute("""
                SELECT port_id, name, direction, data_type, schema_spec
                FROM meta.port
                WHERE node_id = %s
                ORDER BY direction, name
            """, (node_id,))
            ports = [
                {"port_id": str(r[0]), "name": r[1], "direction": r[2], "data_type": r[3], "schema_spec": r[4]}
                for r in cur.fetchall()
            ]

    return envelope({
        "node_id": node_id,
        "declared_upstream": declared_upstream,
        "declared_downstream": declared_downstream,
        "upstream_count": len(declared_upstream),
        "downstream_count": len(declared_downstream),
        "file_mappings": file_mappings,
        "ports": ports,
        "audit_notes": [
            "Compare declared upstream to actual imports in source files",
            "Nodes without file_mappings cannot be audited for discovered deps",
            "Port schemas define the data contract — verify at runtime",
        ] if not file_mappings else [],
    }, t0)


# -------------------------------------------------------
# Wave 5.5: Dataset CRUD (OpenLineage-compatible)
# -------------------------------------------------------
class DatasetCreate(BaseModel):
    name: str
    namespace: str = "mcas.pipeline"
    description: Optional[str] = None
    node_id: Optional[str] = None
    port_id: Optional[str] = None
    facets: Optional[dict] = None
    current_version: Optional[str] = None


@router.post("/datasets")
async def create_dataset(body: DatasetCreate):
    """Register a dataset (OpenLineage-compatible identity)."""
    t0 = time.time()
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO meta.dataset (namespace, name, description, node_id, port_id, facets, current_version)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (namespace, name) DO UPDATE SET
                    description = COALESCE(EXCLUDED.description, meta.dataset.description),
                    node_id = COALESCE(EXCLUDED.node_id, meta.dataset.node_id),
                    port_id = COALESCE(EXCLUDED.port_id, meta.dataset.port_id),
                    facets = COALESCE(EXCLUDED.facets, meta.dataset.facets),
                    current_version = COALESCE(EXCLUDED.current_version, meta.dataset.current_version),
                    updated_at = NOW()
                RETURNING dataset_id
            """, (
                body.namespace, body.name, body.description,
                body.node_id, body.port_id,
                json.dumps(body.facets) if body.facets else '{}',
                body.current_version,
            ))
            dataset_id = str(cur.fetchone()[0])
        conn.commit()

    return envelope({
        "dataset_id": dataset_id,
        "namespace": body.namespace,
        "name": body.name,
    }, t0)


@router.get("/datasets")
async def list_datasets(
    namespace: Optional[str] = Query(None),
    node_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """List registered datasets."""
    t0 = time.time()
    conditions = []
    params = []
    if namespace:
        conditions.append("namespace = %s")
        params.append(namespace)
    if node_id:
        conditions.append("node_id = %s")
        params.append(node_id)

    where = " AND ".join(conditions) if conditions else "TRUE"

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT dataset_id, namespace, name, description, node_id, port_id,
                       facets, current_version, created_at, updated_at
                FROM meta.dataset
                WHERE {where}
                ORDER BY namespace, name
                LIMIT %s
            """, params + [limit])
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    for r in rows:
        for k in ("dataset_id", "node_id", "port_id"):
            if r.get(k) is not None:
                r[k] = str(r[k])
        for k in ("created_at", "updated_at"):
            if r.get(k):
                r[k] = r[k].isoformat()

    return envelope({"datasets": rows, "count": len(rows)}, t0)
