from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from alerts.alert_repository import AlertRepository
from config.settings import Settings, load_settings
from data.persistence.trade_store import TradeStore
from mcp_server.server import TOOL_SPECS, build_mcp_http_app


def write_env_file(path: Path, **overrides: str) -> Path:
    values = {
        "OANDA_API_KEY": "api-key",
        "OANDA_ACCOUNT_ID": "account-id",
        "OANDA_ENVIRONMENT": "practice",
        "TELEGRAM_BOT_TOKEN": "telegram-token",
        "TELEGRAM_CHAT_ID": "123456789",
        "TELEGRAM_BOT_PASSWORD": "bot-password",
        "TELEGRAM_ADMIN_IDS": "111,222",
        "TINYDB_PATH": str(path.parent / "bot.json"),
        "MCP_HTTP_ENABLED": "true",
        "MCP_HTTP_HOST": "127.0.0.1",
        "MCP_HTTP_PORT": "8080",
        "MCP_HTTP_PATH": "/mcp",
        "MCP_HTTP_API_KEY": "secret-key",
        "MCP_DEFAULT_CHAT_ID": "555",
        "LOG_LEVEL": "warning",
    }
    values.update(overrides)
    path.write_text("\n".join(f"{key}={value}" for key, value in values.items()), encoding="utf-8")
    return path


def build_settings(tmp_path: Path, **overrides: str) -> Settings:
    env_file = write_env_file(tmp_path / ".env", **overrides)
    return load_settings(env_file=env_file)


def reserve_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def wait_for_health(base_url: str) -> None:
    async with httpx.AsyncClient() as client:
        for _ in range(100):
            try:
                response = await client.get(f"{base_url}/healthz")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.05)
    raise AssertionError("Embedded MCP server did not become healthy in time.")


@pytest.mark.asyncio
async def test_mcp_streamable_http_session_initializes_and_executes_alert_tool(tmp_path: Path) -> None:
    port = reserve_tcp_port()
    settings = build_settings(tmp_path, MCP_HTTP_PORT=str(port))
    store = TradeStore(db_path=settings.tinydb_path, settings=settings)
    alert_repository = AlertRepository(store=store)
    runtime = SimpleNamespace(settings=settings, alert_repository=alert_repository)
    app = build_mcp_http_app(runtime=runtime, settings=settings)
    config = uvicorn.Config(
        app,
        host=settings.mcp_http_host,
        port=settings.mcp_http_port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    base_url = f"http://{settings.mcp_http_host}:{settings.mcp_http_port}"

    try:
        await wait_for_health(base_url)

        async with httpx.AsyncClient() as client:
            unauthorized = await client.get(f"{base_url}/mcp")
        assert unauthorized.status_code == 401

        async with streamable_http_client(
            f"{base_url}/mcp?api_key=secret-key",
            terminate_on_close=True,
        ) as (read_stream, write_stream, _get_session_id):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                resources = await session.list_resources()
                tool_surface = await session.read_resource("marketsignal://tool-surface")
                created = await session.call_tool(
                    "create_price_alert",
                    {
                        "instrument": "spx500usd",
                        "target_price": 3350.0,
                        "direction": "above",
                        "note": "protocol audit",
                    },
                )
                listed = await session.call_tool("list_price_alerts", {})
                created_time = await session.call_tool(
                    "create_time_alert",
                    {
                        "kind": "at",
                        "local_time": "2027-04-05 09:30",
                        "schedule": "daily",
                        "note": "protocol audit",
                    },
                )
                listed_time = await session.call_tool("list_time_alerts", {})

        assert initialized.serverInfo.name == "Market Signal Bot V3 MCP"
        assert len(tools.tools) == len(TOOL_SPECS)
        assert {str(resource.uri) for resource in resources.resources} == {
            "marketsignal://alert-defaults",
            "marketsignal://capabilities",
            "marketsignal://supported-instruments",
            "marketsignal://tool-surface",
        }
        assert tool_surface.contents[0].mimeType == "application/json"
        assert created.isError is False
        assert created.structuredContent["chat_id"] == 555
        assert created.structuredContent["instrument"] == "SPX500_USD"
        assert created.structuredContent["status"] == "PENDING"
        assert listed.isError is False
        assert listed.structuredContent["chat_id"] == 555
        assert len(listed.structuredContent["alerts"]) == 1
        assert listed.structuredContent["alerts"][0]["instrument"] == "SPX500_USD"
        assert created_time.isError is False
        assert created_time.structuredContent["schedule"] == "once"
        assert created_time.structuredContent["local_time"] == "2027-04-05 09:30"
        assert created_time.structuredContent["next_fire_at"] == "2027-04-05T01:30:00Z"
        assert listed_time.isError is False
        assert listed_time.structuredContent["chat_id"] == 555
        assert len(listed_time.structuredContent["alerts"]) == 1
        assert listed_time.structuredContent["alerts"][0]["local_time"] == "2027-04-05 09:30"
    finally:
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=10)
        store.close()
