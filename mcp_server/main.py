"""MCP stdio entrypoint for the local agent runtime."""

from __future__ import annotations

from agent.runtime import build_runtime
from config.settings import get_settings
from core.logging_setup import configure_logging
from mcp_server.server import build_mcp_server


def main() -> int:
    settings = get_settings()
    configure_logging(settings)
    runtime = build_runtime(settings=settings)
    try:
        server = build_mcp_server(runtime=runtime, settings=settings)
        server.run("stdio")
    finally:
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
