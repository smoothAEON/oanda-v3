"""Deployment bootstrap that ensures split dependencies are present before startup."""

from __future__ import annotations

import importlib
import subprocess
import sys

PACKAGE_NAME = "smartmoneyconcepts"
PACKAGE_SPEC = "smartmoneyconcepts==0.0.26"
INSTALL_COMMAND = (
    sys.executable,
    "-m",
    "pip",
    "install",
    PACKAGE_SPEC,
    "--no-deps",
)


def _smc_is_installed() -> bool:
    try:
        importlib.import_module(PACKAGE_NAME)
    except ModuleNotFoundError:
        return False
    return True


def install_smartmoneyconcepts() -> None:
    try:
        subprocess.run(INSTALL_COMMAND, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Automatic smartmoneyconcepts install failed during startup."
        ) from exc


def ensure_smartmoneyconcepts_installed() -> None:
    if _smc_is_installed():
        return

    print(
        "smartmoneyconcepts is missing; installing "
        f"{PACKAGE_SPEC} with --no-deps before startup.",
        flush=True,
    )
    install_smartmoneyconcepts()
    importlib.invalidate_caches()

    if not _smc_is_installed():
        raise RuntimeError(
            "smartmoneyconcepts is still unavailable after the automatic install step."
        )


def load_runtime_entrypoint():
    from bot.bot import main as bot_main

    return bot_main


def main() -> int:
    ensure_smartmoneyconcepts_installed()
    return load_runtime_entrypoint()()


if __name__ == "__main__":
    raise SystemExit(main())
