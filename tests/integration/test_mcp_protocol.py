from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from config.settings import Settings, load_settings
from mcp_server.adapters import BotMcpService
from mcp_server.server import TOOL_SPECS, build_mcp_server


def write_env_file(path: Path, **overrides: str) -> Path:
    values = {
        "OANDA_API_KEY": "api-key",
        "OANDA_ACCOUNT_ID": "account-id",
        "OANDA_ENVIRONMENT": "practice",
        "TINYDB_PATH": str(path.parent / "bot.json"),
        "LOG_LEVEL": "warning",
    }
    values.update(overrides)
    path.write_text("\n".join(f"{key}={value}" for key, value in values.items()), encoding="utf-8")
    return path


def build_settings(tmp_path: Path, **overrides: str) -> Settings:
    env_file = write_env_file(tmp_path / ".env", **overrides)
    return load_settings(env_file=env_file)


def test_mcp_stdio_surface_builds_with_current_resources(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    runtime = SimpleNamespace(settings=settings)

    server = build_mcp_server(runtime=runtime, settings=settings)
    capabilities = BotMcpService.capabilities_payload()
    tool_surface = BotMcpService.tool_surface_payload(TOOL_SPECS)

    assert server is not None
    assert capabilities["transport"] == "stdio"
    assert {spec["name"] for spec in tool_surface["tools"]} == {spec["name"] for spec in TOOL_SPECS}
    assert "render_chart" in {spec["name"] for spec in TOOL_SPECS}
    assert "create_price_alert" not in {spec["name"] for spec in TOOL_SPECS}
