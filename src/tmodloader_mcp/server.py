"""MCP server. Seven tools over a running tModLoader instance.

Tools are synchronous. A game session is process-global state — there is one
game, one save directory, and one trigger file — so serialising calls on the
event loop is what stops two requests consuming each other's reply.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

# mcp 2.x renamed FastMCP to MCPServer and moved it out of mcp.server.fastmcp,
# which no longer exists. Same class, same decorator, same kwargs.
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import build as build_mod_impl
from . import config as config_mod
from . import diag as diag_mod
from . import session as session_mod
from .triggers import COMMANDS, TriggerError

INSTRUCTIONS = """\
Drive a running tModLoader instance: launch it, ask it questions, photograph it,
and read its state back.

Typical loop: `build_mod` -> `launch` -> `trigger`/`diag`/`shot` -> `stop`.

The game answers by polling a TRIGGER FILE, so the mod under test must embed the
responder. Nothing here sends keystrokes or drives a window.

THERE IS NO HEADLESS SINGLEPLAYER. `launch` refuses that mode and explains why
rather than starting something else. Singleplayer needs a human to load a world;
every other tool then drives it normally.
"""

_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False)
_MUTATES = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)

mcp = MCPServer("tmodloader-mcp", instructions=INSTRUCTIONS)

#: The one live session, or None. Module-global because the thing it models is:
#: one game, one save directory, one trigger file.
_session: session_mod.Session | None = None


def _cfg() -> config_mod.Config:
    cfg = config_mod.load()
    problems = config_mod.check(cfg)
    if problems:
        raise RuntimeError("configuration is unusable:\n  - " + "\n  - ".join(problems))
    return cfg


class BuildOut(TypedDict):
    ok: bool
    errors: int
    warnings: int
    game_was_open: bool
    summary: str


class LaunchOut(TypedDict):
    mode: str
    port: int
    player: str
    started_pids: list[int]


class ReplyOut(TypedDict):
    command: str
    ok: bool
    refused: bool
    text: str


class DiagOut(TypedDict):
    side: str
    fields: dict[str, Any]


class ShotOut(TypedDict):
    path: str
    region: str


class StopOut(TypedDict):
    killed_pids: list[int]
    note: NotRequired[str]


@mcp.tool(
    title="Build the mod",
    annotations=_MUTATES,
    structured_output=True,
)
def build_mod() -> BuildOut:
    """Compile the configured mod source into a .tmod.

    tModLoader REFUSES to build while the game is open, and says so with an
    error that otherwise reads like a compile failure. That case is reported as
    itself, with the fix, rather than as a broken build — which is the
    difference between closing the game and hunting a syntax error that is not
    there.

    Success is read from the output, not the exit code, which is not reliable
    here.
    """
    result = build_mod_impl.build(_cfg())
    return BuildOut(
        ok=result.ok,
        errors=result.errors,
        warnings=result.warnings,
        game_was_open=result.game_was_open,
        summary=result.summary,
    )


@mcp.tool(
    title="Launch a game session",
    annotations=_MUTATES,
    structured_output=True,
)
def launch(
    mode: str = "server_client", port: int = 7810, player: str = "n43n"
) -> LaunchOut:
    """Start tModLoader and wait until it can actually answer.

    Args:
        mode: "server" for a dedicated server alone, or "server_client" for a
            server plus one joined client — the only way to observe what a
            CLIENT sees, which is where most sync bugs live. "singleplayer" is
            refused: Terraria has no headless entry point for it.
        port: Server port. Change it only if something else holds the default.
        player: Character name for the client. Must already exist — `-player`
            does not create one, and a duplicate name is kicked.

    Waits for a heartbeat that is BOTH recent and reporting a live world. Those
    fail differently — a stale-but-ready heartbeat means the process died, a
    fresh-but-not-ready one means it is still loading — and checking only
    existence conflates them.
    """
    global _session

    if _session is not None:
        raise RuntimeError(
            "a session is already running. Call `stop` first — two instances "
            "share one save directory and consume each other's trigger files."
        )

    _session = session_mod.launch(_cfg(), mode, port=port, player=player)
    return LaunchOut(
        mode=_session.mode,
        port=_session.port,
        player=_session.player,
        started_pids=sorted(_session.started),
    )


@mcp.tool(
    title="Send a dev trigger to the game",
    annotations=_MUTATES,
    structured_output=True,
)
def trigger(
    command: str,
    target: str | None = None,
    argument: str | None = None,
    server: bool = False,
) -> ReplyOut:
    """Ask the running game to do something, and return what it said.

    Args:
        command: One of the mod's dev commands — currently
            capture, diag, mutate, vat, creature, kill, strains, seed, creep,
            shot. An unknown word is refused HERE rather than written to disk,
            because a game that does not recognise it simply does nothing, and
            from outside that is indistinguishable from a hang.
        target: Address the request to one player by name. Two clients on one
            machine share a trigger file and would otherwise race for it.
        argument: For commands that take one, e.g. a region for `shot`.
        server: Send to the dedicated server rather than the client. Some
            commands are server-authoritative and refuse on a client.

    `refused` is reported separately from `ok`: a refusal is the mod
    deliberately saying no, and treating it as success is how a rejected action
    reads as a completed one.
    """
    if _session is None:
        raise RuntimeError("no session — call `launch` first")

    reply = _session.ask(command, target=target, argument=argument, server=server)
    return ReplyOut(
        command=reply.command, ok=reply.ok, refused=reply.refused, text=reply.text
    )


@mcp.tool(
    title="Read the game's state",
    annotations=_READ_ONLY,
    structured_output=True,
)
def diag(server: bool = False, target: str | None = None) -> DiagOut:
    """Ask one side of the session what it currently sees, parsed into fields.

    Args:
        server: Read the dedicated server's view instead of the client's.
        target: Address a specific client by name.

    Returns counters as integers and the mod's absence markers as null, so a
    reading of 0 — a real measurement — cannot be confused with "no data".
    Asking both sides at the same moment is the only way to answer "the client
    reports no NPC", which one side alone cannot.
    """
    if _session is None:
        raise RuntimeError("no session — call `launch` first")

    fields = _session.diag(server=server, target=target)
    # side_of, not the raw line. The mod writes "client netmode=1"; handing that
    # back whole makes the field useless for comparison and invites callers to
    # substring-match it, which is the parsing this server exists to remove.
    return DiagOut(side=diag_mod.side_of(fields), fields=fields)


@mcp.tool(
    title="Photograph part of the frame",
    annotations=_READ_ONLY,
    structured_output=True,
)
def shot(region: str, target: str | None = None) -> ShotOut:
    """Capture a region of the game's own back buffer and return the PNG path.

    Args:
        region: topleft, topright, bottomleft, bottomright, or full. REQUIRED
            and deliberately without a default — the frame holds the player's
            character name, world name and any chat, so a request says which
            corner it wants.
        target: Address a specific client.

    This reads what the game rendered, not the screen, so no other window can
    appear in it — by construction rather than by luck. It also sees things the
    in-game capture camera cannot: dust and the interface layer.
    """
    if _session is None:
        raise RuntimeError("no session — call `launch` first")

    path = _session.shot(region, target=target)
    return ShotOut(path=str(path), region=region)


@mcp.tool(
    title="Read a side's log",
    annotations=_READ_ONLY,
    structured_output=True,
)
def logs(
    server: bool = False, contains: str | None = None, lines: int = 80
) -> dict[str, Any]:
    """Tail one side's tModLoader log, optionally filtered.

    Args:
        server: Read the server log rather than the client one.
        contains: Keep only lines containing this substring, case-insensitively.
        lines: How many trailing lines to return. Zero returns none; a negative
            count is refused rather than guessed at.

    Useful when a launch fails: the reason is usually in the log and not in
    anything the trigger protocol can reach, because the game never got far
    enough to poll.
    """
    if lines < 0:
        raise ValueError(f"lines={lines} is not a number of lines to return")

    cfg = _cfg()
    name = "server.log" if server else "client.log"
    path = cfg.tml_dir / "tModLoader-Logs" / name

    if not path.is_file():
        return {"path": str(path), "found": False, "lines": []}

    text = path.read_text(errors="replace").splitlines()
    if contains:
        needle = contains.lower()
        text = [ln for ln in text if needle in ln.lower()]

    # `text[-lines:]` looks like a tail and is one for every value but zero:
    # -0 is 0, so `text[-0:]` is the WHOLE log. Asking for no lines returned
    # every line there had ever been, as a successful answer of the right
    # shape - an agent spending its context on a log it did not ask for has
    # nothing to notice.
    kept = text[-lines:] if lines else []

    return {"path": str(path), "found": True, "lines": kept}


@mcp.tool(
    title="Stop the game session",
    annotations=_DESTRUCTIVE,
    structured_output=True,
)
def stop() -> StopOut:
    """Kill only the processes this session started.

    Surgical on purpose: a developer usually has their own game open, and a
    teardown that killed every tModLoader it could find would take it with them.
    """
    global _session

    if _session is None:
        return StopOut(killed_pids=[], note="no session was running")

    killed = session_mod.stop(_cfg(), _session)
    _session = None
    return StopOut(killed_pids=killed)


def main() -> None:
    mcp.run()


# `python -m tmodloader_mcp.server` must work, not just the console script — it
# is what a client config most often points at.
if __name__ == "__main__":
    main()


__all__ = ["COMMANDS", "TriggerError", "main", "mcp"]
