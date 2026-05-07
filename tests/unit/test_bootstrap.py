from __future__ import annotations

import bootstrap


def test_main_skips_install_when_smc_is_present(monkeypatch) -> None:
    calls = {"ensure": 0, "entrypoint": 0, "run": 0}

    def fake_ensure() -> None:
        calls["ensure"] += 1

    def fake_load_entrypoint():
        calls["entrypoint"] += 1

        def runner() -> int:
            calls["run"] += 1
            return 7

        return runner

    monkeypatch.setattr(bootstrap, "ensure_smartmoneyconcepts_installed", fake_ensure)
    monkeypatch.setattr(bootstrap, "load_runtime_entrypoint", fake_load_entrypoint)

    assert bootstrap.main() == 7
    assert calls == {"ensure": 1, "entrypoint": 1, "run": 1}


def test_ensure_smartmoneyconcepts_installs_when_missing(monkeypatch) -> None:
    installed = {"value": False}
    calls = {"install": 0}

    def fake_probe() -> bool:
        return installed["value"]

    def fake_install() -> None:
        calls["install"] += 1
        installed["value"] = True

    monkeypatch.setattr(bootstrap, "_smc_is_installed", fake_probe)
    monkeypatch.setattr(bootstrap, "install_smartmoneyconcepts", fake_install)

    bootstrap.ensure_smartmoneyconcepts_installed()

    assert calls["install"] == 1


def test_ensure_smartmoneyconcepts_raises_when_install_does_not_fix_import(
    monkeypatch,
) -> None:
    monkeypatch.setattr(bootstrap, "_smc_is_installed", lambda: False)
    monkeypatch.setattr(bootstrap, "install_smartmoneyconcepts", lambda: None)

    try:
        bootstrap.ensure_smartmoneyconcepts_installed()
    except RuntimeError as exc:
        assert "still unavailable" in str(exc)
    else:
        raise AssertionError("Expected bootstrap to fail when smartmoneyconcepts stays missing.")
