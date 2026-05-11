"""Compatibility entrypoint for the local MCP stdio server.

Dependency installation is intentionally explicit for stdio safety. Install
`smartmoneyconcepts==0.0.26 --no-deps` during environment setup instead of
installing from process startup.
"""

from __future__ import annotations

from mcp_server.main import main


if __name__ == "__main__":
    raise SystemExit(main())
