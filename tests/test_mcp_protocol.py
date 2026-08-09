"""The server as a CLIENT sees it: started as a subprocess, spoken to over stdio.

THE LAYER EVERY OTHER TEST HERE SKIPS. The rest of this suite imports the module
and calls the tool functions, and `live_check.py` says it drives "the same code
paths the MCP tools use" - which is true and still not the protocol. A Python
call returns a dict and nobody checks it. Over MCP the same dict is validated
against a schema generated from the return annotation, and that is a different
question with a different answer.

It had a different answer. `status` was broken over the protocol in the state it
exists to report - no game running - and 197 tests were green the whole time:

    Error executing tool status: 4 validation errors for StatusOut
    mode / port / player / started_pids: Field required

`NotRequired` was spelled correctly. `from __future__ import annotations` made
every annotation a string, and `typing.TypedDict` computes __required_keys__ at
class creation without resolving them, so the optional fields were silently
promoted to required. Nothing that calls a function can see that.

Only `status` is called here, deliberately: every other read-only tool goes
through `_cfg()`, which needs a real tModLoader install and would make this a
test of the machine rather than of the surface. What it does cover is the part
that broke - the handshake, schema generation, and one round trip whose output
is validated - and it runs on any runner, with no game.
"""

import asyncio
import json
import shutil
import sys

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from tmodloader_mcp import server as server_mod

EXPECTED_TOOLS = {
    "build_mod",
    "launch",
    "trigger",
    "commands",
    "diag",
    "shot",
    "captures",
    "read_capture",
    "status",
    "logs",
    "log_files",
    "stop",
}


def _server_params() -> StdioServerParameters:
    # `-m` rather than the console script: the entry point is only on PATH once
    # the package is installed, and this has to run from a source checkout too.
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "tmodloader_mcp.server"],
    )


async def _talk(call):
    async with (
        stdio_client(_server_params()) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        return await call(session)


def _run(call):
    return asyncio.run(_talk(call))


needs_subprocess = pytest.mark.skipif(
    shutil.which(sys.executable) is None, reason="no interpreter to spawn"
)


@needs_subprocess
def test_the_server_starts_and_advertises_every_tool():
    """A tool that fails to register is invisible, not loud.

    The decorator runs at import; a tool whose signature the schema generator
    cannot handle drops out of the listing and everything else still works.
    Counting them is the only thing that notices.
    """

    async def call(session):
        return (await session.list_tools()).tools

    tools = _run(call)
    names = {t.name for t in tools}

    assert names == EXPECTED_TOOLS, (
        f"missing {EXPECTED_TOOLS - names}, extra {names - EXPECTED_TOOLS}"
    )
    assert all(t.description for t in tools), (
        "a tool with no description is unusable to a model"
    )


@needs_subprocess
def test_status_survives_the_round_trip_with_no_game_running():
    """THE ONE THAT WAS BROKEN, in the state it exists to report.

    Not `status() == {...}` - that is the assertion that passed throughout. The
    output has to come back THROUGH the protocol, where it is validated against
    the generated schema.
    """

    async def call(session):
        return await session.call_tool("status", {})

    result = _run(call)

    assert not result.is_error, "".join(
        c.text for c in result.content if hasattr(c, "text")
    )

    text = "".join(c.text for c in result.content if hasattr(c, "text"))
    parsed = json.loads(text)

    # The shape is the contract: every field present, absence expressed as null.
    assert set(parsed) == set(server_mod.StatusOut.__annotations__)
    assert parsed["running"] is False
    assert parsed["started_pids"] is None


def test_output_annotations_are_resolved_types_not_forward_refs():
    """The defect underneath, named where it lives rather than by symptom.

    `from __future__ import annotations` leaves these as ForwardRef, and a
    ForwardRef is what TypedDict cannot see NotRequired through - which is how
    every optional field became required with nothing complaining.

    The first version of this guard tested `isinstance(a, str)` and passed
    happily against the defect, because PEP 563 stores ForwardRef objects
    rather than bare strings. A predicate that cannot observe the thing it
    guards is not a guard; this one was rewritten after watching it fail to
    fail.
    """
    for cls in (server_mod.StatusOut, server_mod.StopOut, server_mod.CommandsOut):
        annotations = cls.__annotations__
        unresolved = [
            name
            for name, a in annotations.items()
            if isinstance(a, str) or type(a).__name__ == "ForwardRef"
        ]
        assert not unresolved, (
            f"{cls.__name__} has unresolved annotations {unresolved}, so "
            "TypedDict cannot see optionality through them - is "
            "`from __future__ import annotations` back at the top of server.py?"
        )
