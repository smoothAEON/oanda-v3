from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_DIRS = ("smc", "indicators", "filters")
CODE_DIRS = (
    "alerts",
    "background",
    "bot",
    "charting",
    "config",
    "core",
    "data",
    "filters",
    "indicators",
    "journal",
    "notifications",
    "orchestration",
    "providers",
    "smc",
    "tracking",
)
FORBIDDEN_IMPORTS = (
    "providers.oanda_execution",
    "providers.account_client",
    "providers.stream_client",
)
FORBIDDEN_ACCOUNT_NAMES = (
    "get_account_summary",
    "get_open_positions",
    "get_open_orders",
    "get_open_trades",
    "get_trade_detail",
    "get_pricing",
    "stream_prices",
)


def iter_analysis_files() -> list[Path]:
    files: list[Path] = []
    for directory in ANALYSIS_DIRS:
        files.extend(sorted((REPO_ROOT / directory).glob("*.py")))
    return files


def iter_code_files() -> list[Path]:
    files: list[Path] = []
    for directory in CODE_DIRS:
        files.extend(sorted((REPO_ROOT / directory).glob("*.py")))
    return files


def test_analysis_modules_do_not_import_execution_provider() -> None:
    for path in iter_analysis_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in FORBIDDEN_IMPORTS, path
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "") not in FORBIDDEN_IMPORTS, path


def test_analysis_modules_do_not_reference_account_access_methods() -> None:
    for path in iter_analysis_files():
        source = path.read_text(encoding="utf-8")
        for name in FORBIDDEN_ACCOUNT_NAMES:
            assert name not in source, path


def test_repo_code_does_not_call_or_expose_fvg_paths() -> None:
    for path in iter_code_files():
        source = path.read_text(encoding="utf-8").lower()
        assert "smc.fvg(" not in source, path
        assert '"/fvg"' not in source, path
        assert "'/fvg'" not in source, path


def test_public_models_do_not_expose_fvg_fields() -> None:
    source = (REPO_ROOT / "core" / "models.py").read_text(encoding="utf-8").lower()
    assert "fvg" not in source


def test_production_modules_do_not_import_execution_provider() -> None:
    for path in iter_code_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "providers.oanda_execution", path
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "") != "providers.oanda_execution", path
