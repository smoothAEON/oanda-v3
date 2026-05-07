"""Compatibility entrypoint for running the bot with embedded MCP enabled."""

from __future__ import annotations

from bot.bot import main


if __name__ == "__main__":
    raise SystemExit(main())
