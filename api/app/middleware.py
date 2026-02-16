"""
Request Logging Middleware
==========================

Logs every HTTP request/response with timing, correlation ID, and client info.
Catches unhandled exceptions and logs them with full traceback.
"""

import time
import uuid
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from json_logging import ctx_correlation_id

logger = logging.getLogger("api.access")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        token = ctx_correlation_id.set(correlation_id)

        start = time.perf_counter()
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Correlation-ID"] = correlation_id
            return response
        except Exception:
            logger.exception(
                "Unhandled exception",
                extra={
                    "event": "unhandled_exception",
                    "method": request.method,
                    "path": request.url.path,
                    "client_ip": request.client.host if request.client else "",
                },
            )
            raise
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            logger.info(
                "%s %s %s",
                request.method,
                request.url.path,
                status_code,
                extra={
                    "event": "request",
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "client_ip": request.client.host if request.client else "",
                    "query_string": str(request.url.query) if request.url.query else "",
                },
            )
            ctx_correlation_id.reset(token)
