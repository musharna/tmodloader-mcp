"""Drive the real game OVER THE PROTOCOL, not through the tool functions.

`live_check.py` runs the same journey by importing the module and calling the
tools. That is a real check of the game and a real check of the code beneath
the surface, and it is still not a check of the surface: a Python call returns
a dict nobody validates, while over MCP the same dict is checked against a
schema generated from the return annotation. `status` was broken that way for
its whole life with 197 tests green.

`test_mcp_protocol.py` closed that for everything reachable without a game -
the five read-only tools and the one resource - by satisfying `config.check`
with a directory tree. The seven tools here cannot be reached that way. They
need tModLoader actually running, so they need this, run by hand, on a machine
with the game installed:

    .venv/bin/python tests/live_protocol_check.py

ONE SESSION FOR THE WHOLE JOURNEY, AND IT IS NOT A STYLE CHOICE. The server
keeps `_session` as a module global - one game, one save directory, one trigger
file. Each `stdio_client` spawn is a NEW server process with its own empty
global, so a `launch` in one connection and a `diag` in the next would be a
`diag` with no session at all, and the failure would look like the game had
died rather than like the test had.

WHAT THIS DELIBERATELY DOES NOT DO: capture anything at the OS level. Every
picture comes from the mod reading the game's own back buffer, which cannot
contain another window by construction. An earlier attempt at screen capture
came back with a Teams inbox and a Discord friend list in frame; that is the
whole reason the trigger-file protocol exists.
"""

import asyncio
import signal
import sys
import traceback
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

#: The game takes tens of seconds to become answerable and the build can take
#: longer, so the client must outwait the tools rather than the other way
#: round. A client timeout firing mid-launch leaves a game running with nothing
#: holding its pids - the one failure this script cannot clean up after.
#:
#: SECONDS, as a float. `ClientSession` takes `read_timeout_seconds: float`, not
#: the `timedelta` the name suggests and older versions wanted - a `timedelta`
#: gets as far as the first request and dies inside anyio with
#: "unsupported operand type(s) for +: 'float' and 'datetime.timedelta'".
CLIENT_TIMEOUT = 600.0

#: Total wall clock. A hung launch otherwise pins a game process indefinitely.
signal.signal(
    signal.SIGALRM,
    lambda *_: (sys.stderr.write("\nABORT: walltime guard\n"), sys.exit(2)),
)
signal.alarm(1200)

#: Not 7810/7812 - those are what `live_check.py` and the manual sweeps use, and
#: a port collision presents as a launch timeout that blames the game.
PORT = 7814

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    """Record rather than raise, so one failure does not hide the next.

    An assert here would stop the run at the first problem and leave the rest of
    the surface unmeasured - and, worse, skip the `stop` that shuts the game
    down. Collecting them means one run reports everything it saw.

    DETAIL IS PRINTED ONLY WHEN THE CHECK FAILS. It is written as the
    explanation of a failure, so printing it on success produced lines like
    `OK   shot 1 is a PNG: bad magic` and `OK   captures differ: both returned
    ...` - a passing run that reads as a failing one. What a caller wants to see
    on the way past is the OBSERVATION, and that is what `note` is for.
    """
    print(
        f"  {'OK  ' if condition else 'FAIL'} {label}{'' if condition else f': {detail}'}"
    )
    if not condition:
        failures.append(f"{label}: {detail}" if detail else label)
    return condition


def note(text: str) -> None:
    """A measurement worth seeing on a PASSING run, printed as itself."""
    print(f"       {text}")


def structured(result, label: str):
    """A tool's output as the PROTOCOL validated it, or None with a failure."""
    if result.is_error:
        text = "".join(c.text for c in result.content if hasattr(c, "text"))
        check(label, False, text[:300])
        return None
    if result.structured_content is None:
        check(label, False, "no structured output")
        return None
    return result.structured_content


async def journey(session: ClientSession) -> None:
    print(">>> the surface is all there")
    tools = (await session.list_tools()).tools
    check("12 tools advertised", len(tools) == 12, f"got {len(tools)}")

    print(">>> singleplayer is REFUSED, over the protocol, with a reason")
    refusal = await session.call_tool("launch", {"mode": "singleplayer"})
    text = "".join(c.text for c in refusal.content if hasattr(c, "text"))
    check("singleplayer refused", refusal.is_error, "it launched something")
    check("refusal explains itself", "headless" in text.lower(), text[:200])

    print(">>> status BEFORE anything is launched")
    idle = structured(await session.call_tool("status", {}), "status idle")
    if idle is not None:
        check("not running", idle.get("running") is False, str(idle))
        check("every key present", len(idle) == 5, str(sorted(idle)))

    print(">>> build_mod")
    built = structured(await session.call_tool("build_mod", {}), "build_mod")
    if built is not None:
        check("build ok", built.get("ok") is True, built.get("summary", "")[:300])
        check("errors is an int", isinstance(built.get("errors"), int), str(built))

    print(f">>> launch server_client on {PORT}")
    launched = structured(
        await session.call_tool("launch", {"mode": "server_client", "port": PORT}),
        "launch",
    )
    if launched is None:
        print("!!! launch failed - nothing below can run")
        return

    check("mode echoed", launched.get("mode") == "server_client", str(launched))
    check("port echoed", launched.get("port") == PORT, str(launched))
    check(
        "pids reported",
        isinstance(launched.get("started_pids"), list)
        and len(launched["started_pids"]) > 0,
        str(launched.get("started_pids")),
    )
    note(f"player={launched.get('player')} pids={launched.get('started_pids')}")

    try:
        print(
            ">>> status WITH a live session - the branch the fake install cannot reach"
        )
        live = structured(await session.call_tool("status", {}), "status live")
        if live is not None:
            check("running", live.get("running") is True, str(live))
            check("port matches launch", live.get("port") == PORT, str(live))
            check(
                "pids match launch",
                sorted(live.get("started_pids") or [])
                == sorted(launched["started_pids"]),
                f"{live.get('started_pids')} vs {launched['started_pids']}",
            )

        print(">>> commands - learned from the mod, not from a copy of it")
        cmds = structured(await session.call_tool("commands", {}), "commands")
        if cmds is not None:
            check("responder present", cmds.get("responder") is True, str(cmds)[:200])
            names = [c["name"] for c in cmds.get("commands", [])]
            check("commands listed", len(names) > 0, "the mod named none")
            check("diag is among them", "diag" in names, f"only {names}")
            note(f"{len(names)} commands: {', '.join(names)}")

        print(">>> diag - dict[str, Any] is the annotation most likely to surprise")
        # THE HIGHEST-VALUE ASSERTION IN THIS FILE. `DiagOut.fields` is
        # `dict[str, Any]` and `records` is `dict[str, list[str]]`; neither has
        # ever been through the schema generator with real data in it. A dict
        # whose values are ints, strings and None at once is exactly what a
        # generated schema is most likely to reject.
        diag = structured(await session.call_tool("diag", {}), "diag")
        if diag is not None:
            check(
                "side reported",
                diag.get("side") in {"client", "singleplayer"},
                str(diag.get("side")),
            )
            fields = diag.get("fields") or {}
            check("fields is populated", len(fields) > 0, "the dump carried none")
            note(
                f"side={diag.get('side')} {len(fields)} fields: {', '.join(list(fields)[:8])}..."
            )
            check(
                "a counter survived as an int",
                isinstance(fields.get("ambient-motes"), int),
                f"ambient-motes={fields.get('ambient-motes')!r}",
            )
            check(
                "records is a dict",
                isinstance(diag.get("records"), dict),
                str(type(diag.get("records"))),
            )

        print(">>> trigger creep, server-authoritative")
        fired = structured(
            await session.call_tool("trigger", {"command": "creep", "server": True}),
            "trigger creep",
        )
        if fired is not None:
            check("trigger ok", fired.get("ok") is True, fired.get("text", "")[:200])
            check("not refused", fired.get("refused") is False, str(fired))
            note(fired.get("text", "")[:160])

        print(">>> diag again - the census must still parse after the world changed")
        after = structured(await session.call_tool("diag", {}), "diag after")
        if after is not None:
            f2 = after.get("fields") or {}
            note(
                f"sources={f2.get('creep-sources')} tiles={f2.get('creep-tiles')} "
                f"census={f2.get('creep-census')} remembered={f2.get('creep-remembered')}"
            )
            # Type, not value: creep grows over ticks and asserting a count here
            # would be a timing race wearing a correctness check's name.
            check(
                "census parses as int",
                isinstance(f2.get("creep-census"), int),
                str(f2.get("creep-census")),
            )
            check(
                "creep-remembered reached the surface",
                "creep-remembered" in f2,
                "the field added in Biomancy #12 is not in the dump",
            )

        print(">>> two shots, two files")
        s1 = structured(
            await session.call_tool("shot", {"region": "bottomleft"}), "shot 1"
        )
        s2 = structured(
            await session.call_tool("shot", {"region": "topright"}), "shot 2"
        )
        if s1 is not None and s2 is not None:
            check(
                "captures differ",
                s1["path"] != s2["path"],
                f"both returned {s1['path']}",
            )
            for tag, shot in (("shot 1", s1), ("shot 2", s2)):
                png = Path(shot["path"])
                if not check(f"{tag} on disk", png.is_file(), shot["path"]):
                    continue
                check(
                    f"{tag} non-empty",
                    png.stat().st_size > 0,
                    f"{png.stat().st_size} bytes",
                )
                check(
                    f"{tag} is a PNG",
                    png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n",
                    "bad magic",
                )
                note(f"{tag} {png.name} {png.stat().st_size} bytes")

            print(">>> and the capture reads back through the protocol")
            # Ties the game half to the read-only half: a capture the real game
            # just wrote, served by the same reader the fake-install tests cover.
            name = Path(s1["path"]).name
            served = await session.call_tool("read_capture", {"name": name})
            check(
                f"read_capture {name}",
                not served.is_error,
                "".join(c.text for c in served.content if hasattr(c, "text"))[:200],
            )
            images = [c for c in served.content if getattr(c, "type", None) == "image"]
            check("one image returned", len(images) == 1, str(len(images)))

        print(">>> an unknown command is refused")
        bad = await session.call_tool("trigger", {"command": "creeep"})
        check("bad command refused", bad.is_error, "it was accepted")

    finally:
        print(">>> stop")
        stopped = structured(await session.call_tool("stop", {}), "stop")
        if stopped is not None:
            check(
                "killed something",
                isinstance(stopped.get("killed_pids"), list),
                str(stopped),
            )
            print(
                f"       killed {stopped.get('killed_pids')} note={stopped.get('note')!r}"
            )

        print(">>> status after stop")
        done = structured(await session.call_tool("status", {}), "status after stop")
        if done is not None:
            check("no longer running", done.get("running") is False, str(done))


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "tmodloader_mcp.server"]
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write, read_timeout_seconds=CLIENT_TIMEOUT) as session,
    ):
        await session.initialize()
        await journey(session)


try:
    asyncio.run(main())
except Exception:  # noqa: BLE001 - the TYPE is not the contract, the exit code is
    # Anything that escapes `journey` has already skipped the `stop` in its
    # `finally`, so what matters here is that the run reports FAILED rather than
    # exiting 0 with a traceback scrolled off the top of the output.
    traceback.print_exc()
    failures.append("the run itself raised")

print()
if failures:
    print(f"=== {len(failures)} FAILED ===")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("=== all checks passed ===")
