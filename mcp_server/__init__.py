"""Embedded MCP server for Gold Signal Bot V3."""

from mcp_server.server import TOOL_SPECS, build_mcp_http_app, build_mcp_server

__all__ = ["TOOL_SPECS", "build_mcp_http_app", "build_mcp_server"]
