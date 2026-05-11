from __future__ import annotations

import bootstrap
from mcp_server.main import main as mcp_main


def test_bootstrap_exposes_local_mcp_entrypoint() -> None:
    assert bootstrap.main is mcp_main
