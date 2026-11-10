#!/usr/bin/env python3
"""Drive each standalone skill with the official MCP Python client over stdio, the way a stranger's agent would.

For every skill under ``skills/``: start it from its own source tree, initialise, list the tools and check each has a description, an input
schema and an output schema, call the first tool with no arguments (it must come back as a tool error, not a crash), and call every tool
that takes no required argument (the client validates the structured result against the declared output schema, so a result that breaks its
own schema fails here). Nothing leaves the machine except what a skill itself would fetch, and no tool is called with real arguments that
reach a network.
The client library is not a dependency of this project: ``uv pip install mcp`` into a scratch environment, then
``python scripts/mcp_client_check.py``. Exit status 0 when every skill passes.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

SKILLS = Path(__file__).resolve().parents[1] / "skills"


def package(skill: Path) -> str:
    """The import name of a skill's package."""
    return next(p.name for p in sorted((skill / "src").iterdir()) if p.is_dir() and not p.name.startswith(("_", ".")))


def stdio_command(skill: Path, env: dict[str, str]) -> str:
    """The sub-command a skill uses to serve MCP over stdio, read from its own help text."""
    help_text = subprocess.run(  # noqa: S603 - a fixed argument list, no shell
        [sys.executable, "-m", package(skill), "--help"], capture_output=True, text=True, env=env, check=False, timeout=30
    ).stdout
    return "serve-stdio" if "serve-stdio" in help_text else "mcp"


async def check(skill: Path) -> list[str]:
    """Problems found with one skill, empty when it passes."""
    # the client library is optional, so it is imported by name and the type checker does not look for it
    client: Any = importlib.import_module("mcp")
    stdio: Any = importlib.import_module("mcp.client.stdio")
    ClientSession, StdioServerParameters, stdio_client = client.ClientSession, client.StdioServerParameters, stdio.stdio_client

    env = {**os.environ, "PYTHONPATH": str(skill / "src")}
    params = StdioServerParameters(command=sys.executable, args=["-m", package(skill), stdio_command(skill, env)], env=env)
    problems: list[str] = []
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools: Any = (await session.list_tools()).tools
        if not tools:
            return ["lists no tools"]
        for tool in tools:
            for what, value in (
                ("description", tool.description),
                ("input schema", tool.input_schema),
                ("output schema", tool.output_schema),
            ):
                if not value:
                    problems.append(f"{tool.name}: no {what}")
            if not tool.input_schema.get("required") and (await session.call_tool(tool.name, {})).is_error:
                problems.append(f"{tool.name}: a call with no arguments is an error although none is required")
        if not (await session.call_tool(tools[0].name, {})).is_error and tools[0].input_schema.get("required"):
            problems.append(f"{tools[0].name}: an empty call is not refused although arguments are required")
    return problems


async def main() -> int:
    failed = 0
    for skill in sorted(p for p in SKILLS.iterdir() if (p / "src").is_dir()):
        try:
            problems = await asyncio.wait_for(check(skill), 60)
        except Exception as error:  # a skill that cannot be driven at all is a failure, not a crash of the check
            problems = [f"could not be driven: {type(error).__name__}: {error}"]
        sys.stdout.write(f"{skill.name}: {'ok' if not problems else '; '.join(problems)}\n")
        failed += bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
