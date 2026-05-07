"""HTTP auth helpers for the embedded MCP server."""

from __future__ import annotations

from secrets import compare_digest

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class QueryParamAPIKeyMiddleware(BaseHTTPMiddleware):
    """Gate MCP HTTP requests behind a shared `api_key` query parameter."""

    def __init__(self, app, *, api_key: str) -> None:
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path == "/healthz":
            return await call_next(request)

        candidate = request.query_params.get("api_key")
        if candidate is None or not compare_digest(candidate, self.api_key):
            return JSONResponse(
                {
                    "ok": False,
                    "error": "unauthorized",
                    "message": "Missing or invalid api_key query parameter.",
                },
                status_code=401,
            )
        return await call_next(request)


__all__ = ["QueryParamAPIKeyMiddleware"]
