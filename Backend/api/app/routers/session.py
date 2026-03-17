import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from governance.bootstrap import get_config
from governance.cache_contract import TF_SECONDS, STALENESS_MULTIPLIER, SIGNAL_MAX_AGE_S

router = APIRouter(prefix="/session", tags=["session"])

SESSION_COOKIE_NAME = "mcas_session"
SESSION_VERSION = 1
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60


def envelope(data, compute_start: float) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "data": data,
        "meta": {
            "timestamp": now,
            "as_of_ts": now,
            "version": "v1",
            "compute_ms": round((time.time() - compute_start) * 1000, 1),
        },
    }


def _get_api_key() -> str:
    return get_config().Worker.API_KEY


def _get_api_origin(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _urlsafe_b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")


def _build_signature(payload: str) -> str:
    api_key = _get_api_key()
    if not api_key:
        raise HTTPException(status_code=500, detail="API key not configured")

    digest = hmac.new(
        api_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return _urlsafe_b64encode(digest)


def _build_session_token() -> str:
    payload = _urlsafe_b64encode(
        json.dumps(
            {
                "v": SESSION_VERSION,
                "iat": int(time.time()),
                "exp": int(time.time()) + SESSION_TTL_SECONDS,
            },
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signature = _build_signature(payload)
    return f"{payload}.{signature}"


def _read_session_claims(token: str | None) -> dict | None:
    if not token or "." not in token:
        return None

    payload, signature = token.split(".", 1)

    try:
        expected = _build_signature(payload)
    except HTTPException:
        return None

    if not hmac.compare_digest(signature, expected):
        return None

    try:
        claims = json.loads(_urlsafe_b64decode(payload).decode("utf-8"))
    except Exception:
        return None

    if claims.get("v") != SESSION_VERSION:
        return None

    if int(claims.get("exp", 0)) <= int(time.time()):
        return None

    return claims


def has_valid_session(token: str | None) -> bool:
    return _read_session_claims(token) is not None


def _build_bootstrap_payload(request: Request, *, authenticated: bool) -> dict:
    cfg = get_config().Worker
    timeframes = cfg.DEFAULT_TIMEFRAMES.split(",")

    return {
        "has_session": authenticated,
        "has_app_access": authenticated,
        "auth": "ok" if authenticated else "missing",
        "session_source": "http_only_cookie" if authenticated else "none",
        "entitlement_mode": "preview_only",
        "api_origin": _get_api_origin(request),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        # --- Frontend governance config (NEW) ---
        "frontend_config": {
            "polling": {
                "opportunities_ms": 15_000,
                "positions_ms": 5_000,
                "trading_account_ms": 5_000,
                "paper_status_ms": 5_000,
                "ohlcv_ms": 5_000,
                "snapshot_ms": 5_000,
                "signals_top_ms": 30_000,
                "queue_status_ms": 15_000,
                "public_feed_ms": 16_000,
            },
            "timeframes": timeframes,
            "tf_seconds": {tf: TF_SECONDS.get(tf, 60) for tf in timeframes},
            "staleness_multiplier": STALENESS_MULTIPLIER,
            "signal_max_age_s": SIGNAL_MAX_AGE_S,
            "pagination": {
                "opportunities_default": 50,
                "opportunities_max": 500,
                "runs_default": 50,
                "runs_max": 200,
                "trades_default": 50,
                "trades_max": 500,
                "simulation_results_default": 100,
                "simulation_results_max": 500,
            },
            "request": {
                "timeout_ms": 30_000,
                "retry_count": 2,
                "retry_backoff_ms": 1_000,
            },
            "features": {
                # Wave 1: Graph Readability (all disabled)
                "pipeline_lod": False,
                "pipeline_keyboard_shortcuts": False,
                "pipeline_tv_scaling": False,
                "pipeline_web_worker_layout": False,
                "pipeline_collapsible_groups": False,
                "pipeline_panel_aware_fit_view": False,
                "pipeline_complexity_check": False,
                "pipeline_edge_culling": False,
                "pipeline_url_state": False,
                # Wave 2: Timeline & Biography (all enabled)
                "pipeline_event_tree": True,
                "pipeline_progressive_disclosure": True,
                "pipeline_critical_path": True,
                "pipeline_event_adjusters": True,
                "pipeline_time_gap_viz": True,
                "pipeline_table_tree_toggle": True,
                "pipeline_temporal_slider": True,
                "pipeline_before_after_state": True,
                "pipeline_configurable_facets": True,
                "pipeline_slicing": True,
                "pipeline_recent_activity": True,
                # Wave 3: Dependency Hardening (all enabled)
                "pipeline_staleness": True,
                "pipeline_output_hash": True,
                "pipeline_drift_detection": True,
                "pipeline_diff": True,
                "pipeline_tiered_confirmation": True,
                # Wave 4: Advanced Interaction (all enabled)
                "pipeline_lineage_depth": True,
                "pipeline_search": True,
                "pipeline_focus_mode": True,
                "pipeline_split_panel": True,
                "pipeline_composition_draft": True,
                # Wave 5: Interoperability (all enabled)
                "pipeline_openlineage": True,
                "pipeline_provenance_search": True,
            },
        },
    }


def set_session_cookie(request: Request, response: Response) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=_build_session_token(),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
    )


class SessionCreateRequest(BaseModel):
    api_key: str


@router.get("/bootstrap")
def get_session_bootstrap(request: Request):
    t0 = time.time()
    token = request.cookies.get(SESSION_COOKIE_NAME)
    return envelope(
        _build_bootstrap_payload(request, authenticated=has_valid_session(token)),
        t0,
    )


@router.post("")
def create_session(payload: SessionCreateRequest, request: Request, response: Response):
    t0 = time.time()
    api_key = _get_api_key()

    if not api_key:
        raise HTTPException(status_code=500, detail="API key not configured")

    if not hmac.compare_digest((payload.api_key or "").strip(), api_key):
        raise HTTPException(status_code=401, detail="Unauthorized")

    set_session_cookie(request, response)
    return envelope(_build_bootstrap_payload(request, authenticated=True), t0)


@router.delete("")
def delete_session(request: Request, response: Response):
    t0 = time.time()
    clear_session_cookie(response)
    return envelope(_build_bootstrap_payload(request, authenticated=False), t0)
