from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.test_chart_renderer import (
    as_mapping,
    build_renderer,
    build_request,
    get_first_present,
    get_renderer_method,
)


@pytest.mark.live
def test_live_chart_render_produces_png_and_cleans_up(
    live_settings,
    live_provider,
    scan_orchestrator,
    market_state,
    tmp_path: Path,
) -> None:
    scan_orchestrator.refresh_snapshot("XAU_USD", "H1")
    renderer = build_renderer(
        settings=live_settings,
        market_state=market_state,
        market_data_provider=live_provider,
        scan_orchestrator=scan_orchestrator,
    )
    build_render_payload = get_renderer_method(
        renderer,
        ("build_render_payload", "prepare_render_payload", "_build_render_payload"),
    )
    payload = build_render_payload(
        build_request(
            instrument="XAU_USD",
            timeframe="H1",
            count=50,
            smc=("orderblocks",),
        )
    )
    payload_mapping = as_mapping(payload)
    order_blocks = get_first_present(
        payload_mapping,
        ("order_block_annotations", "order_blocks", "smc_order_blocks"),
    )
    first_order_block = order_blocks[0] if isinstance(order_blocks, (list, tuple)) else order_blocks
    snapshot = market_state.get_snapshot("XAU_USD", "H1")
    assert snapshot is not None
    assert get_first_present(first_order_block, ("anchor_time", "created_at", "time", "start_time")) == (
        snapshot.zones.order_blocks[0].created_at
    )

    render_chart = get_renderer_method(renderer, ("render", "render_chart", "_render"))
    result = render_chart(
        build_request(
            instrument="XAU_USD",
            timeframe="H1",
            count=50,
            smc=("orderblocks",),
        )
    )
    resolved = as_mapping(result)
    artifact = get_first_present(resolved, ("artifact", "chart_artifact", "output"))
    artifact_path = Path(get_first_present(artifact, ("path", "file_path")))

    assert artifact_path.exists()
    assert artifact_path.stat().st_size > 0

    close = getattr(artifact, "close", None) or getattr(result, "close", None)
    assert close is not None
    close()
    assert not artifact_path.exists()
