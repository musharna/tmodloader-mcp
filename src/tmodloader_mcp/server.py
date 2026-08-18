"""MCP server over a running tModLoader instance.

Tools are synchronous. A game session is process-global state — there is one
game, one save directory, and one trigger file — so serialising calls on the
event loop is what stops two requests consuming each other's reply.
"""

# NO `from __future__ import annotations` HERE, DELIBERATELY.
#
# PEP 563 turns every annotation into a string, and `typing.TypedDict`
# computes __required_keys__ at class creation - from those strings. It does
# not resolve them, so `NotRequired[str]` is an unrecognised forward ref and
# the key is silently marked REQUIRED. Every optional field in this file was
# lost that way, and only the MCP layer noticed: pydantic validates these
# TypedDicts when a tool returns, so `status` raised four "Field required"
# errors whenever no game was running - the most common state there is.
#
# Invisible to every test here, because a test calls the function and gets a
# dict back; nothing validates it. Switching to typing_extensions does NOT
# fix it (measured on 3.13 - still zero optional keys). Removing this import
# does, and costs nothing: requires-python is >=3.12, where `str | None` and
# `list[int]` are native syntax.

from pathlib import Path
from typing import Any, TypedDict

# mcp 2.x renamed FastMCP to MCPServer and moved it out of mcp.server.fastmcp,
# which no longer exists. Same class, same decorator, same kwargs.
from mcp.server.mcpserver import Image, MCPServer
from mcp.types import ToolAnnotations

from . import api as api_mod
from . import build as build_mod_impl
from . import captures as captures_mod
from . import commands as commands_mod
from . import config as config_mod
from . import diag as diag_mod
from . import heartbeat as heartbeat_mod
from . import inventory as inventory_mod
from . import logs as logs_mod
from . import saves as saves_mod
from . import session as session_mod
from .triggers import TriggerError

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
    # The world RESOLVED - the argument if one was given, the configured
    # default otherwise. Reporting the argument would say `null` for the
    # commonest case and never name the world that actually loaded.
    world: str | None
    started_pids: list[int]


class LogSinceOut(TypedDict):
    lines: list[str]
    next_offset: int
    restarted: bool


class ApiMemberOut(TypedDict):
    path: str
    kind: str
    type: str


class ApiSearchOut(TypedDict):
    matches: list[ApiMemberOut]
    #: How many members the index holds, so a thin index is visible rather than
    #: reading as an API that does not have the thing you asked about.
    indexed: int
    truncated: bool


class SnapshotOut(TypedDict):
    label: str
    #: Epoch seconds. Absolute rather than an age, so two snapshots can be
    #: ordered against each other and against a log line.
    taken: float
    files: list[str]
    size: int


class SnapshotListOut(TypedDict):
    snapshots: list[SnapshotOut]
    #: Where they live, so somebody can delete them by hand without guessing.
    root: str


class RestoreOut(TypedDict):
    label: str
    files: list[str]
    size: int
    #: The snapshot holding what was overwritten, so the restore can be undone.
    undo: str | None


class LogWatchOut(TypedDict):
    matched: bool
    #: The matching lines from the poll that matched, or empty on a timeout.
    lines: list[str]
    next_offset: int
    restarted: bool
    elapsed: float
    polls: int


class RestartOut(TypedDict):
    """Flat on purpose.

    A nested `BuildOut | None` was the obvious shape and is the shape that
    broke `status`: an optional key the schema generator promotes to required,
    invisible to anything that calls the function directly. This tool needs a
    running game, so CI cannot drive it over the protocol and cannot catch that
    class of mistake here — which is a reason to avoid the risky shape, not to
    assume it would have been fine.
    """

    killed_pids: list[int]
    # Null when no build was asked for, which is different from a build that
    # ran and failed.
    built: bool | None
    build_summary: str | None
    mode: str
    port: int
    player: str
    world: str | None
    started_pids: list[int]


class ReplyOut(TypedDict):
    command: str
    ok: bool
    refused: bool
    text: str
    # Always present, never omitted - see `StatusOut`'s docstring for why an
    # optional key here would fail the round trip instead of merely being
    # absent. Null on every reply but a capture that broke a stale lock.
    note: str | None


class DiagOut(TypedDict):
    side: str
    fields: dict[str, Any]
    records: dict[str, list[str]]


class StatusOut(TypedDict):
    """Always the same shape; absence is a null, not a missing key.

    NotRequired was the obvious spelling and does not survive the round trip.
    The MCP layer fills a missing optional key with None when it serializes,
    then validates against a schema it generated saying that key is an array -
    so an absent field fails as "None is not of type 'array'" no matter which
    side is right. A key that is always present and sometimes null is
    representable in both.
    """

    running: bool
    mode: str | None
    port: int | None
    player: str | None
    # `launch` took a world, used it, and forgot it, so this could report the
    # mode, port and player of a session while staying silent about the only
    # field that says WHICH WORLD is loaded.
    world: str | None
    # Characters brought in by `join`, in arrival order - NOT including
    # `player`, which came up with `launch`. Empty rather than null when a
    # session is running with nobody joined: "none yet" and "no session" are
    # different answers and a caller acts on them differently.
    joined: list[str] | None
    started_pids: list[int] | None


class HeartbeatSideOut(TypedDict):
    """One side's heartbeat, with the questions kept apart deliberately.

    `present`, `live`, `world_ready` and `armed` are four booleans rather than
    one status string because they fail independently and each rules out a
    different fix. `launch` collapses them into a single readiness bit, which is
    correct for something that has to block on one — and is why its timeout
    message can only say that nothing happened.
    """

    side: str
    # Null for the dedicated server, and for a client that is up without a
    # character. Null is not "unknown" here — it is a state the caller can act
    # on, so it is reported rather than omitted.
    player: str | None
    present: bool
    live: bool
    # Null rather than absent - see StatusOut. Null also when the file is there
    # but cannot be stat'd: a fabricated 0.0 would read as the freshest
    # possible heartbeat, which is the opposite of what it would mean.
    age_seconds: float | None
    world_ready: bool
    armed: bool
    fields: dict[str, Any]
    diagnosis: str


class HeartbeatOut(TypedDict):
    """Every client, plus the server.

    A LIST rather than one `client` key, because the old shape could not
    express two clients at once and silently reported whichever had written
    last. That was the shared-file bug wearing a different hat: two clients,
    one answer, no way to tell.
    """

    clients: list[HeartbeatSideOut]
    server: HeartbeatSideOut


class WorldOut(TypedDict):
    name: str
    # Null rather than absent - see StatusOut. Null means this path is not
    # under /mnt/<drive> and there is no drive letter to translate to, which
    # `config.windows_path_for` refuses to guess at rather than invent a UNC
    # spelling nobody has measured against tModLoader.
    path_win: str | None


class ModOut(TypedDict):
    name: str
    enabled: bool
    built_here: bool


class InventoryOut(TypedDict):
    worlds: list[WorldOut]
    players: list[str]
    mods: list[ModOut]


class PruneOut(TypedDict):
    removed: list[str]
    remaining: list[str]


class ShotOut(TypedDict):
    path: str
    region: str


class StopOut(TypedDict):
    killed_pids: list[int]
    # Null rather than absent - see StatusOut.
    note: str | None


class CommandOut(TypedDict):
    name: str
    takes_argument: bool
    summary: str


class CommandsOut(TypedDict):
    responder: bool
    commands: list[CommandOut]
    # Null rather than absent - see StatusOut.
    note: str | None


@mcp.tool(
    title="Build the mod",
    annotations=_MUTATES,
    structured_output=True,
)
def build_mod(timeout: float = 600.0) -> BuildOut:
    """Compile the configured mod source into a .tmod.

    Args:
        timeout: Seconds to wait for the compile. A large mod on a slow machine
            can outlast the default, and a build that runs out of time says so
            rather than reporting a compile failure with no errors in it.

    tModLoader REFUSES to build while the game is open, and says so with an
    error that otherwise reads like a compile failure. That case is reported as
    itself, with the fix, rather than as a broken build — which is the
    difference between closing the game and hunting a syntax error that is not
    there.

    Success is read from the output, not the exit code, which is not reliable
    here.
    """
    result = build_mod_impl.build(_cfg(), timeout=timeout)
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
    mode: str = "server_client",
    port: int = 7810,
    player: str = "n43n",
    world: str | None = None,
    timeout: float = 300.0,
) -> LaunchOut:
    """Start tModLoader and wait until it can actually answer.

    Args:
        mode: "server_client" — a server plus one joined client. It is the only
            mode there is, and the only way to observe what a CLIENT sees, which
            is where most sync bugs live. The other two are refused because the
            engine cannot satisfy them: "singleplayer" has no headless entry
            point, and "server" alone never ticks, so the mod never polls and
            never answers.
        port: Server port. Change it only if something else holds the default.
        player: Character name for the client. Must already exist — `-player`
            does not create one, and a duplicate name is kicked.
        world: WINDOWS path to a `.wld`, overriding TMODLOADER_WORLD_WIN. A WSL
            path is refused rather than tried: tModLoader runs as a Windows
            process, cannot resolve /mnt/c, and the only symptom is a readiness
            timeout blaming the heartbeat.
        timeout: Seconds to wait for readiness. Raise it on a slow machine or a
            large world — the default assumes neither.

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

    _session = session_mod.launch(
        _cfg(), mode, port=port, player=player, world=world, timeout=timeout
    )
    return LaunchOut(
        mode=_session.mode,
        port=_session.port,
        player=_session.player,
        world=_session.world,
        started_pids=sorted(_session.started),
    )


class JoinOut(TypedDict):
    player: str
    # Everyone joined so far, this call included, in arrival order. The
    # session's own `player` is not in it - that client came up with `launch`.
    joined: list[str]
    started_pids: list[int]


@mcp.tool(
    title="Join a second client to the running session",
    annotations=_MUTATES,
    structured_output=True,
)
def join(player: str, timeout: float = 300.0) -> JoinOut:
    """Bring another character into the session that is already running.

    Args:
        player: Character name. Must already exist — `-player` does not create
            one — and must not be one this session already has, in any casing.
        timeout: Seconds to wait for that client to report a live, world-ready
            heartbeat of its own.

    The protocol has supported several clients since answers became per-player;
    the LIFECYCLE supported one, so the arrangement that work exists to make
    safe could only be reached by spawning a game by hand. This is that, with
    the waiting done properly.

    It waits for THIS client, not for a process. A new pid says something
    started — not that a character loaded, that the join was accepted, or that
    a world is under it. And it watches only that player's own tokened
    heartbeat: the unsuffixed `<mod>-hooks.txt` is a shared slot holding
    whichever client booted last, so accepting it would return against the
    heartbeat of the game that was already here.

    Address the new client by name — `diag(target=...)`, `shot(target=...)` —
    which already works, because addressing was never the half that was
    missing. `stop` takes it down with everything else the session started.
    """
    global _session

    if _session is None:
        raise RuntimeError("no session — call `launch` first")

    _session = session_mod.join(_cfg(), _session, player, timeout=timeout)
    return JoinOut(
        player=player,
        joined=list(_session.joined),
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
    timeout: float = 60.0,
) -> ReplyOut:
    """Ask the running game to do something, and return what it said.

    Args:
        command: One of the dev commands THIS mod serves — call `commands` to
            see them, since they are the running mod's rather than a list kept
            here. A word it does not serve is refused before anything is
            written to disk: a game that does not recognise one does nothing,
            and from outside that is indistinguishable from a hang.
        target: Address the request to one player by name. Every request is
            already addressed to this session's own player by default, so pass
            this only to ask a DIFFERENT client — one another session drives on
            the same machine.
        argument: Only some commands read one — `commands` says which. Passing
            one to a command that takes none is refused here, because the mod
            would refuse it too and that costs a round trip.
        server: Send to the dedicated server rather than the client. Some
            commands are server-authoritative and refuse on a client, and each
            side publishes its own list.
        timeout: Seconds to wait for the game's reply. A command that does real
            work on a large world can outlast the default, and for `capture`
            that is now safe at any value: the capture lock records the
            deadline this argument implies, so another session waits it out
            instead of guessing from the lock's age. Raising it no longer
            trades a slow capture against a collision.

    `refused` is reported separately from `ok`: a refusal is the mod
    deliberately saying no, and treating it as success is how a rejected action
    reads as a completed one.

    `note` is usually null and is the one field with no other way to reach you:
    something that happened on the way to this reply which the reply itself
    cannot show. Currently it says a capture lock was broken to take this
    picture — captures are serialised across sessions sharing a save directory,
    and this one's holder was judged gone. It names WHICH rule judged it: the
    holder's own recorded deadline had passed, or the lock recorded no deadline
    and the 60s age bound decided instead. The second is the weaker claim — a
    guess about how long a capture can take — and worth knowing you are reading.
    """
    if _session is None:
        raise RuntimeError("no session — call `launch` first")

    reply = _session.ask(
        command, target=target, argument=argument, server=server, timeout=timeout
    )
    return ReplyOut(
        command=reply.command,
        ok=reply.ok,
        refused=reply.refused,
        text=reply.text,
        note=reply.note,
    )


@mcp.tool(
    title="What the running mod serves",
    annotations=_READ_ONLY,
    structured_output=True,
)
def commands(server: bool = False) -> CommandsOut:
    """What this side's mod says it serves, read from the mod itself.

    The list is published by the responder when it loads, not assembled here.
    This harness used to carry its own copy of one mod's twelve commands and its
    own belief about which read an argument — facts that belonged to running C#
    and drifted the moment either side changed alone.

    `responder` IS THE USEFUL FIELD when something is wrong. False means no list
    was published: the mod is not loaded, or is a build with the dev bridge
    compiled out. That is a different answer from a game still starting, and it
    used to arrive as a readiness timeout, which names the wrong thing entirely —
    it reads as slow rather than as never going to answer.

    A list that exists but cannot be read is an ERROR rather than `responder:
    false`, because it means a responder IS running and this side cannot
    understand it — a version mismatch, which needs a human rather than a wait.

    Args:
        server: Ask the dedicated server rather than the client. Each side
            publishes its own list, and they are not always the same.
    """
    if _session is None:
        raise RuntimeError("no session — call `launch` first")

    try:
        published = _session.commands(server=server)
    except commands_mod.CommandsMissing as absent:
        return CommandsOut(responder=False, commands=[], note=str(absent))

    return CommandsOut(
        responder=True,
        commands=[
            CommandOut(name=c.name, takes_argument=c.takes_argument, summary=c.summary)
            for c in published.commands
        ],
        note=None,
    )


@mcp.tool(
    title="Read the game's state",
    annotations=_READ_ONLY,
    structured_output=True,
)
def diag(
    server: bool = False, target: str | None = None, timeout: float = 60.0
) -> DiagOut:
    """Ask one side of the session what it currently sees, parsed.

    Args:
        server: Read the dedicated server's view instead of the client's.
        target: Address a specific client by name.
        timeout: Seconds for the WHOLE call — the reply and then the dump it
            promises, out of one budget rather than one each. A large world
            takes longer.

    Returns counters as integers and the mod's absence markers as null, so a
    reading of 0 — a real measurement — cannot be confused with "no data".
    Asking both sides at the same moment is the only way to answer "the client
    reports no NPC", which one side alone cannot.

    `records` carries the indented list bodies the scalars only summarise:
    `fields["npcs"]` says `active=6 mutated=1`, and `records["npcs"]` says which
    six. Those lines were parsed and discarded until now, so a caller could see
    that something was there and never what it was.
    """
    if _session is None:
        raise RuntimeError("no session — call `launch` first")

    dump = _session.diag(server=server, target=target, timeout=timeout)
    # side_of, not the raw line. The mod writes "client netmode=1"; handing that
    # back whole makes the field useless for comparison and invites callers to
    # substring-match it, which is the parsing this server exists to remove.
    return DiagOut(
        side=diag_mod.side_of(dump.fields),
        fields=dump.fields,
        records=dump.records,
    )


class WaitOut(TypedDict):
    matched: bool
    #: The last reading taken, whether or not it matched. Null both when the
    #: field genuinely read as absent and when no poll completed - the two are
    #: told apart by `polls`, and conflating them in the type would be worse
    #: than either.
    last: Any
    polls: int
    elapsed: float
    # Always present, never omitted - see `StatusOut`. Null unless the last
    # poll was cut off by the timeout rather than answered.
    note: str | None


@mcp.tool(
    title="Wait until the game reaches a state",
    annotations=_READ_ONLY,
    structured_output=True,
)
def wait_until(
    field: str,
    op: str,
    value: str | None = None,
    server: bool = False,
    target: str | None = None,
    timeout: float = 60.0,
    poll: float = 2.0,
) -> WaitOut:
    """Poll `diag` until one of its fields satisfies a comparison.

    Args:
        field: A TOP-LEVEL diag field, exactly as `diag` reports it - `vats`,
            `items`, `world-ready`. Not a path into one: `diag` splits
            `key: value` and stops, so `npcs` is the whole string
            `active=4 mutated=0` and there is no `npcs.active`.
        op: One of `==`, `!=`, `<`, `<=`, `>`, `>=`, `contains`, `changed`.
        value: What to compare against, as text - it is converted to whatever
            type the field actually reads as. Omitted for `changed`, which
            baselines on its first reading.
        server: Watch the dedicated server's view instead of the client's.
        target: Watch a specific client by name.
        timeout: Seconds for the WHOLE call, spent across every poll rather
            than granted to each.
        poll: Seconds between polls.

    Use this instead of sleeping and taking a diag. A guessed sleep is wrong in
    both directions, and the short one is dangerous: the check reads the state
    BEFORE the thing happened, which looks exactly like the feature being
    broken.

    THE COMPARISON IS TYPED. `diag` returns counters as ints and the
    heartbeat's flags as bools; `world-ready == true` compares as a boolean and
    `items >= 10` as a number, so neither `"10" < "9"` nor the truthiness of
    `"False"` can come back here.

    IT REFUSES WHAT CAN NEVER COME TRUE rather than waiting it out. An unknown
    field names the fields that do exist; ordering a composite string says what
    the value actually is. Both used to be spellable and would have reported a
    timeout - blaming a game that was answering perfectly.

    Not matching is an ANSWER, not an error: it returns `matched: false` with
    the last reading it took, so a wait that expected nothing to happen is as
    expressible as one that expected something to.
    """
    if _session is None:
        raise RuntimeError("no session — call `launch` first")

    got = _session.wait_until(
        field,
        op,
        value,
        server=server,
        target=target,
        timeout=timeout,
        poll=poll,
    )
    return WaitOut(
        matched=got.matched,
        last=got.last,
        polls=got.polls,
        elapsed=got.elapsed,
        note=got.note,
    )


@mcp.tool(
    title="Photograph part of the frame",
    annotations=_READ_ONLY,
    structured_output=True,
)
def shot(region: str, target: str | None = None, timeout: float = 60.0) -> ShotOut:
    """Capture a region of the game's own back buffer and return the PNG path.

    Args:
        region: topleft, topright, bottomleft, bottomright, or full. REQUIRED
            and deliberately without a default — the frame holds the player's
            character name, world name and any chat, so a request says which
            corner it wants.
        target: Address a specific client.
        timeout: Seconds for the WHOLE call — the reply and then the PNG,
            out of one budget rather than one each.

    This reads what the game rendered, not the screen, so no other window can
    appear in it — by construction rather than by luck. It also sees things the
    in-game capture camera cannot: dust and the interface layer.

    The path comes back only once the bytes behind it are a WHOLE PNG. A file
    exists from the moment it is created rather than the moment it is finished,
    so waiting on the name alone would hand back half a picture as readily as a
    whole one — and anything else that landed on that name as readily as either.
    """
    if _session is None:
        raise RuntimeError("no session — call `launch` first")

    path = _session.shot(region, target=target, timeout=timeout)
    return ShotOut(path=str(path), region=region)


@mcp.tool(
    title="List the captures on disk",
    annotations=_READ_ONLY,
    structured_output=True,
)
def captures() -> dict[str, list[str]]:
    """Every capture in the save directory, newest last.

    Names, not paths — a path handed out is a path that can come back changed,
    and `read_capture` deliberately accepts only a name.
    """
    cfg = _cfg()
    return {"captures": captures_mod.available(cfg.save_dir, cfg.mod_name)}


@mcp.tool(
    title="Read a capture back as an image",
    annotations=_READ_ONLY,
)
def read_capture(name: str) -> Image:
    """Return one capture's PNG as image content.

    `shot` answers with a filesystem path, which is worth nothing to an agent
    that is not running on this machine. This is how the picture itself gets
    back, and it is a SEPARATE call on purpose: a full-frame PNG is tens of
    kilobytes before base64, so a caller that only wanted to know the capture
    succeeded should not be made to pay for the pixels.

    Args:
        name: A capture filename from `captures`, e.g.
            `<mod>-shot-<token>-001-topleft.png`. A NAME, never a path — see
            `captures.read` for why the containment is structural.
    """
    cfg = _cfg()
    return Image(data=captures_mod.read(cfg.save_dir, cfg.mod_name, name), format="png")


@mcp.tool(
    title="Delete old captures",
    annotations=_DESTRUCTIVE,
    structured_output=True,
)
def prune_captures(keep: int) -> PruneOut:
    """Delete all but the newest `keep` captures, and say which went.

    Captures accumulated forever. `shot` writes one per call and nothing ever
    removed them, so an agent photographing in a loop grew the SAVE DIRECTORY
    without bound — the folder holding the worlds and characters, which is not
    a cache and not somewhere to leave litter.

    Args:
        keep: How many of the newest captures to keep. REQUIRED and
            deliberately without a default, the same way `shot` requires a
            region: this deletes files, and a destructive tool that runs with
            no arguments is one that gets called by accident. `keep=0` removes
            all of them, which is a real request and has to be spelled out.

    Only files matching THIS mod's capture pattern are touched, and each is
    re-checked to resolve to a direct child of the save directory — the same
    containment `read_capture` uses, because a delete that listed and removed
    through different rules would be looser than the read beside it. The whole
    set is validated before anything is unlinked, so a refusal costs nothing
    rather than leaving a half-finished prune.

    Newest is decided by MTIME, not by the index in the filename: the index is
    this harness's counter and the timestamp is the disk's account of what
    happened.
    """
    cfg = _cfg()
    removed = captures_mod.prune(cfg.save_dir, cfg.mod_name, keep=keep)
    return PruneOut(
        removed=removed,
        remaining=captures_mod.available(cfg.save_dir, cfg.mod_name),
    )


@mcp.resource(
    "capture://{name}",
    title="A captured frame",
    mime_type="image/png",
)
def capture_resource(name: str) -> bytes:
    """The same capture, addressable as a resource.

    The server was tools-only, which is a poor fit for something that is
    plainly an artifact rather than an action. Same reader, same refusals — a
    second surface, not a second implementation, because two paths to one file
    is how one of them ends up with a weaker check.
    """
    cfg = _cfg()
    return captures_mod.read(cfg.save_dir, cfg.mod_name, name)


@mcp.tool(
    title="Is a session running?",
    annotations=_READ_ONLY,
    structured_output=True,
)
def status() -> StatusOut:
    """Whether a session is running, and what it is.

    The only read-only way to ask. Without it an agent that lost track had to
    provoke an error to find out — `launch` fails when one exists, `diag` fails
    when one does not — so the cheapest question on the surface was the one
    that had to be asked by breaking something.

    Reports what this server BELIEVES it started. It does not re-query the
    process table, so it cannot tell you a game was closed from outside; `stop`
    is what verifies against reality.
    """
    if _session is None:
        return StatusOut(
            running=False,
            mode=None,
            port=None,
            player=None,
            world=None,
            joined=None,
            started_pids=None,
        )

    return StatusOut(
        running=True,
        mode=_session.mode,
        port=_session.port,
        player=_session.player,
        world=_session.world,
        joined=list(_session.joined),
        started_pids=sorted(_session.started),
    )


@mcp.tool(
    title="Read a side's log",
    annotations=_READ_ONLY,
    structured_output=True,
)
def logs(
    name: str = "client.log",
    previous: bool = False,
    contains: str | None = None,
    lines: int = 80,
) -> dict[str, Any]:
    """Tail one of tModLoader's logs, optionally filtered.

    Args:
        name: Which log — see `log_files` for what this install actually has.
            `client.log` and `server.log` are the game; `Launch.log` and the
            `environment-*.log` pair are written by the launcher, which is where
            a run that died BEFORE the game started says why.
        previous: Read the run BEFORE this one. tModLoader zips the previous
            run's logs into `Old/` when a new run starts, so after a failed
            launch and a retry the failure is in an archive and the live log
            belongs to the retry.
        contains: Keep only lines containing this substring, case-insensitively.
            Applied to the WHOLE log before the tail, so this returns the last N
            MATCHING lines rather than the matches among the last N.
        lines: How many trailing lines to return. Zero returns none; a negative
            count is refused rather than guessed at.

    Useful when a launch fails: the reason is usually in a log and not in
    anything the trigger protocol can reach, because the game never got far
    enough to poll. Which is also why `previous` exists — the obvious thing to
    do after a failed launch is launch again, and that rotates the evidence.
    """
    cfg = _cfg()

    try:
        text = logs_mod.read(cfg.tml_dir, name, previous=previous)
    except logs_mod.LogMissing as absent:
        # Absence is reported, not raised: a log that has never been written is
        # the normal state of a fresh install. A bad NAME is a different thing
        # and is deliberately NOT caught here — it stays loud, because it is a
        # caller mistake rather than a fact about the install.
        return {
            "name": name,
            "previous": previous,
            "found": False,
            "lines": [],
            "note": str(absent),
        }

    return {
        "name": name,
        "previous": previous,
        "found": True,
        "lines": logs_mod.tail(text, contains=contains, lines=lines),
    }


@mcp.tool(
    title="List the logs this install has",
    annotations=_READ_ONLY,
    structured_output=True,
)
def log_files() -> dict[str, Any]:
    """Which logs exist right now, and how many earlier runs are archived.

    Read off disk rather than listed as a constant: which logs exist depends on
    what was run, and a server-only session writes no `client.log` at all.
    """
    cfg = _cfg()
    return {
        "logs": logs_mod.available(cfg.tml_dir),
        "archived_runs": len(logs_mod.archives(cfg.tml_dir)),
    }


@mcp.tool(
    title="What this install has",
    annotations=_READ_ONLY,
    structured_output=True,
)
def inventory() -> InventoryOut:
    """The worlds, characters and mods on this machine.

    `launch` states two preconditions and could check neither. `player` must
    already exist — it does not create one, and a duplicate is kicked — and
    `world` wants a WINDOWS path the caller had to know in advance. Both are
    facts about directories sitting right there, and until now the only way to
    learn either was to launch and read the failure: a kick for the wrong
    character, and a readiness timeout blaming the heartbeat for the wrong
    world. Each world's `path_win` is the exact string `launch(world=...)`
    wants.

    The mods answer something else. `commands` reports `responder: false` for
    three different situations — the mod is not built, or it is built and
    switched off, or it is on and was compiled without the dev bridge — and
    `enabled` plus `built_here` separate the first two.

    THOSE TWO ARE NOT ONE FACT. A mod can be enabled and have no `.tmod` here,
    because a workshop mod is installed from somewhere else entirely; on the
    install this was written against, `CheatSheet` is exactly that. Collapsing
    them into `installed` would report it missing and send someone rebuilding a
    mod that was never the problem.
    """
    cfg = _cfg()
    return InventoryOut(
        worlds=[
            WorldOut(name=w.name, path_win=w.path_win)
            for w in inventory_mod.worlds(cfg.save_dir)
        ],
        players=inventory_mod.players(cfg.save_dir),
        mods=[
            ModOut(name=m.name, enabled=m.enabled, built_here=m.built_here)
            for m in inventory_mod.mods(cfg.save_dir)
        ],
    )


@mcp.tool(
    title="Copy the save aside",
    annotations=_MUTATES,
    structured_output=True,
)
def save_snapshot(label: str) -> SnapshotOut:
    """Copy this world and its characters aside, so a run can be undone.

    WHAT THIS IS FOR. The mutating verbs write to a real install and none of
    that FAILS — it accumulates. Enemy NPCs do not survive a reload, so `spawn`
    looks harmless, but `give` writes the character file, `time` and `weather`
    live in the world, and `settile` changes it for good. The damage is
    invisible when it is done and shows up later as a measurement nobody
    doubts.

    Take one before a run that mutates, and `save_restore` after it.

    REFUSES WHILE THE GAME IS RUNNING, naming the pids. A running tModLoader
    owns these files and writes them out on its own schedule, so a copy taken
    now is mid-write. Stop the session first.

    Copies the configured world's `.wld` and `.twld` and every `.plr`/`.tplr` —
    not the whole Worlds directory, which measured 41MB against 3MB for one
    world, and not `.bak` files, which are the game's own safety net.
    """
    cfg = _cfg()
    held = saves_mod.take(cfg, label)
    return SnapshotOut(
        label=held.label,
        taken=held.taken,
        files=list(held.files),
        size=held.size,
    )


@mcp.tool(
    title="Put a saved copy back",
    annotations=_DESTRUCTIVE,
    structured_output=True,
)
def save_restore(label: str) -> RestoreOut:
    """Overwrite the world and characters with a snapshot.

    This DESTROYS what is on disk now, so it saves that first: the state being
    overwritten is copied to `auto-before-restore` and returned as `undo`,
    which `save_restore` accepts like any other label. A restore aimed at the
    wrong snapshot is therefore recoverable rather than final.

    Refuses while the game is running, and refuses a label that does not exist
    by listing the ones that do.
    """
    cfg = _cfg()
    put = saves_mod.restore(cfg, label)
    return RestoreOut(
        label=put.label,
        files=list(put.files),
        size=put.size,
        undo=put.undo,
    )


@mcp.tool(
    title="List the saved copies",
    annotations=_READ_ONLY,
    structured_output=True,
)
def save_snapshots() -> SnapshotListOut:
    """Every snapshot on this machine, newest first, with its age in seconds.

    A snapshot whose manifest cannot be read is omitted rather than listed as
    empty, because `save_restore` refuses it for the same reason: putting back
    nothing and reporting success is the worst available outcome.
    """
    cfg = _cfg()
    held = saves_mod.listing(cfg)
    return SnapshotListOut(
        snapshots=[
            SnapshotOut(label=s.label, taken=s.taken, files=list(s.files), size=s.size)
            for s in held
        ],
        root=str(saves_mod.snapshot_root(cfg)),
    )


@mcp.tool(
    title="Read the mod's heartbeat",
    annotations=_READ_ONLY,
    structured_output=True,
)
def heartbeat() -> HeartbeatOut:
    """Why the game is not answering, for both sides at once.

    `launch` already reads this file to decide readiness and keeps one bit of
    it. When a launch SUCCEEDS that is all anyone needs. When it fails, the
    discarded detail is the entire answer, and what comes back instead is `no
    live heartbeat within 300s` — which names the symptom and none of the four
    causes:

    - **absent** — nothing ever wrote one. The mod is not loaded, is not
      enabled in this install, or was built without the dev bridge. For
      clients this is an EMPTY `clients` list, not an entry saying so.
    - **stale** — a game ran and is no longer running. The file outlives the
      process, so this is indistinguishable from live to anything that only
      checks whether it exists.
    - **live, no world** — still loading. Nothing is wrong; wait longer.
    - **live, world, not armed** — loaded and ticking, bridge not listening.

    `clients` is a LIST because two clients can share one save directory and
    the old single-client shape reported whichever wrote last. Expect an entry
    per client — and one more: an entry with `player: null` is the untokened
    heartbeat every client writes before its character loads, which nothing
    deletes and each new client overwrites. Treat it as a slot rather than as
    a client. A real client is the one carrying a token and an advancing
    `polls`.

    Reads OFF DISK and needs no session, deliberately: a failed `launch` raises
    without storing one, so a tool that required a session could never answer
    the question it exists for. It is also the only tool here that is useful
    when nothing else is.

    Both sides are returned together because "the client is silent and the
    server is fine" is a different diagnosis from both being silent, and asking
    one at a time cannot see the difference.
    """
    cfg = _cfg()

    def _entry(path: Path, player: str | None) -> HeartbeatSideOut:
        hb = heartbeat_mod.read(path)
        return HeartbeatSideOut(
            # `hb.side` is the mod's own account of itself and can disagree
            # with the file this was read from. Where they differ the mod is
            # right, so it is reported rather than the key it was filed under.
            side=hb.side,
            player=player,
            present=hb.present,
            live=hb.live,
            age_seconds=hb.age,
            world_ready=hb.world_ready,
            armed=hb.armed,
            fields=hb.fields,
            diagnosis=heartbeat_mod.diagnose(hb),
        )

    prefix = cfg.artifacts.prefix
    clients = [
        _entry(path, heartbeat_mod.player_of(path.name, prefix))
        for path in heartbeat_mod.client_files(cfg.save_dir, prefix)
    ]
    return HeartbeatOut(
        clients=clients,
        server=_entry(cfg.artifact(cfg.artifacts.heartbeat, server=True), None),
    )


@mcp.tool(
    title="Read what a log has gained",
    annotations=_READ_ONLY,
    structured_output=True,
)
def log_since(name: str, offset: int = 0, contains: str | None = None) -> LogSinceOut:
    """Only what a log has gained since you last looked.

    Args:
        name: A log filename from `log_files`.
        offset: The `next_offset` from your previous call, or 0 to start at the
            beginning. BYTES, not lines — a line count is not a resume point,
            because the number of lines you have read is not where the file
            continues.
        contains: Case-insensitive filter, applied to the new lines only.

    NOT A LIVE TAIL, and it cannot be one. Tools here are synchronous and a
    game session is process-global state, so a `launch` blocking for five
    minutes is not something another call watches from the side. What this
    buys is the read between calls: `logs` re-reads a file that grows all run,
    and this returns the new part.

    `restarted` is the field to check. tModLoader ZIPS the previous run's logs
    and starts fresh, so an offset from a run that has since rotated points
    past the end of a now-shorter file. Reading there would report an empty log
    forever, which looks exactly like a quiet game rather than like a log that
    restarted underneath you. When that happens the read begins again at zero
    and says so, because handing back the whole file is only correct if the
    caller is told why.
    """
    cfg = _cfg()
    since = logs_mod.read_since(cfg.tml_dir, name, offset=offset)

    # The cap is however many lines arrived, which is no cap at all - reused
    # rather than reimplemented so the `contains` filter behaves identically to
    # `logs`. Truncating here would drop the middle of a burst while still
    # advancing the offset past it: log lost, with a resume point claiming
    # otherwise. `or 1` only covers the empty read, where either value returns
    # nothing.
    new_lines = since.text.splitlines()
    return LogSinceOut(
        lines=logs_mod.tail(since.text, contains=contains, lines=len(new_lines) or 1),
        next_offset=since.next_offset,
        restarted=since.restarted,
    )


@mcp.tool(
    title="Search the tModLoader API surface",
    annotations=_READ_ONLY,
    structured_output=True,
)
def api_search(query: str, kind: str | None = None, limit: int = 40) -> ApiSearchOut:
    """Find a type, field, property or method in the INSTALLED tModLoader.

    Args:
        query: Part of a name, or a type. `cloudAlpha`, `rain`, `QuickSpawnItem`,
            `IEntitySource`. Case-insensitive.
        kind: Narrow to one of type, field, property, method.
        limit: How many matches to return, best first.

    ANSWERS THE QUESTION YOU HAVE BEFORE YOU WRITE ANYTHING. A compile tells you
    exactly whether the call you already wrote is right; it cannot tell you what
    is there. `Main.maxRaining` is only findable if you already suspect the name.

    READ FROM THE ASSEMBLY'S OWN METADATA, so it cannot drift from the version
    installed — which is the failure mode of every wiki page and every model's
    recollection of an API. It carries no prose, because it is not documentation:
    it is the public surface, with signatures.

    The index is built once per tModLoader version and cached against the DLL it
    came from, so a game update invalidates it by construction rather than by
    anybody remembering to. The first call after an update pays a few seconds.

    Needs a .NET SDK, because the indexer is a small C# tool — it reads metadata
    without loading or running the game assembly. Without one this refuses and
    says so, rather than answering from a stale or absent index.
    """
    cfg = _cfg()
    members = api_mod.parse(api_mod.ensure_index(cfg).read_text())
    found = api_mod.search(members, query, kind=kind, limit=limit)

    return ApiSearchOut(
        matches=[ApiMemberOut(path=m.path, kind=m.kind, type=m.type) for m in found],
        indexed=len(members),
        truncated=len(found) == limit,
    )


@mcp.tool(
    title="Wait for a line to appear in a log",
    annotations=_READ_ONLY,
    structured_output=True,
)
def log_watch(
    name: str,
    contains: str,
    offset: int = 0,
    timeout: float = 60.0,
    poll: float = 1.0,
) -> LogWatchOut:
    """Block until a log line matches, instead of polling `log_since` by hand.

    Args:
        name: A log filename from `log_files`.
        contains: Case-insensitive text to wait for. REQUIRED — without one
            this matches the first line written and is `log_since` wearing a
            longer name.
        offset: Where to start reading. 0 includes the log's HISTORY, which is
            usually what you want ("did the mod load" is a question about a
            line that is already there). Pass a previous call's `next_offset`
            to watch only what comes after it.
        timeout: Seconds for the WHOLE call, spent across every poll.
        poll: Seconds between reads.

    THE OFFSET IS THE MECHANISM. Each poll resumes where the last stopped, so a
    line is matched exactly once — never missed in the gap between two polls,
    and never re-reported on the next. A watch that re-read the file from the
    top would match a line written before the wait began and call it news,
    which is how "wait for the crash" passes on the crash from the PREVIOUS run.

    Not matching is an ANSWER, not an error: it returns `matched: false` with
    the resume point, so "nothing was logged for 30s" is as expressible as
    waiting for something. A MISSING log still raises, because that is nobody
    having been asked rather than a line failing to arrive.

    `restarted` means the log rotated during the wait — tModLoader zips the
    previous run's logs and starts fresh, so your offset stopped meaning
    anything and the lines you are holding came out of a different file.
    """
    cfg = _cfg()
    got = logs_mod.watch_for(
        cfg.tml_dir, name, contains=contains, offset=offset, timeout=timeout, poll=poll
    )
    return LogWatchOut(
        matched=got.matched,
        lines=got.lines,
        next_offset=got.next_offset,
        restarted=got.restarted,
        elapsed=got.elapsed,
        polls=got.polls,
    )


@mcp.tool(
    title="Rebuild and relaunch the session",
    annotations=_MUTATES,
    structured_output=True,
)
def restart(
    build: bool = True, timeout: float = 300.0, build_timeout: float = 600.0
) -> RestartOut:
    """Stop, rebuild, and start again with the session's own settings.

    Args:
        build: Compile the mod between stopping and starting. On by default,
            because picking up a code change is the reason this loop exists.
        timeout: Seconds to wait for readiness on the relaunch.
        build_timeout: Seconds to allow the compile.

    THE ORDER IS THE POINT. tModLoader REFUSES to build while the game is open
    and reports it with an error that reads like a compile failure, so
    stop-then-build-then-launch is not a preference — building first sends you
    hunting a syntax error that is not there. Three separate calls let a caller
    get that order wrong; this one cannot.

    The mode, port, player and WORLD come from the running session rather than
    from arguments or defaults. That last one is why `Session` had to start
    recording the world it resolved: a relaunch that fell back to the
    configured default would quietly load a different world than the one being
    tested, and report success.

    Needs a running session, because a session is where those settings live.
    With nothing running there is nothing to reuse — call `launch`.
    """
    global _session

    if _session is None:
        raise RuntimeError(
            "no session to restart — `restart` reuses the running session's "
            "mode, port, player and world, and there is none. Call `launch`."
        )

    mode, port, player, world = (
        _session.mode,
        _session.port,
        _session.player,
        _session.world,
    )
    cfg = _cfg()

    killed = session_mod.stop(cfg, _session)
    _session = None

    built: bool | None = None
    summary: str | None = None
    if build:
        result = build_mod_impl.build(cfg, timeout=build_timeout)
        built, summary = result.ok, result.summary
        if not result.ok:
            # Deliberately NOT relaunching a mod that did not compile. The game
            # would start, load the previous .tmod, and answer normally - so the
            # session would look healthy while testing the code you just failed
            # to build, which is worse than not starting at all.
            raise RuntimeError(
                f"build failed, so nothing was relaunched: {result.summary}"
            )

    _session = session_mod.launch(
        cfg, mode, port=port, player=player, world=world, timeout=timeout
    )
    return RestartOut(
        killed_pids=killed,
        built=built,
        build_summary=summary,
        mode=_session.mode,
        port=_session.port,
        player=_session.player,
        world=_session.world,
        started_pids=sorted(_session.started),
    )


@mcp.tool(
    title="Stop the game session",
    annotations=_DESTRUCTIVE,
    structured_output=True,
)
def stop(settle: float = session_mod.KILL_SETTLE) -> StopOut:
    """Kill only the processes this session started, and confirm they are gone.

    Args:
        settle: Seconds a killed process may take to leave the process table
            before it counts as a survivor. `/F` returns before Windows has
            caught up, so verifying too eagerly reports a successful teardown as
            a refused one — raise this on a loaded machine rather than lower it.

    Surgical on purpose: a developer usually has their own game open, and a
    teardown that killed every tModLoader it could find would take it with them.

    `killed_pids` are pids VERIFIED to have left the process table, not pids a
    kill was aimed at. If any survive, this FAILS rather than answering with a
    shorter list — and the session is deliberately kept, so calling `stop`
    again retries exactly those pids. Releasing it would leave a running game
    that nothing owns, which is how the next `launch` ends up refusing to start
    over a process nobody remembers starting.
    """
    global _session

    if _session is None:
        return StopOut(killed_pids=[], note="no session was running")

    # Not in a `finally`: a survivor has to stay owned, so the session is
    # released only once stop() has confirmed there is nothing left to own.
    killed = session_mod.stop(_cfg(), _session, settle=settle)
    _session = None
    return StopOut(killed_pids=killed, note=None)


def _reading(label: str, produce) -> str:
    """One live reading for a prompt, or the reason there isn't one.

    A prompt that diagnoses a broken install is READ WHEN THINGS ARE BROKEN, so
    the config being unusable is a likely state rather than an exceptional one.
    The failure is put in the text instead of raised: a diagnostic that refuses
    to render because something is wrong has failed at the one moment it was
    for. The exception text is included verbatim rather than summarised, which
    is the difference between reporting the fault and hiding it.
    """
    try:
        return f"{label}: {produce()}"
    except Exception as exc:  # noqa: BLE001 - the reason IS the diagnostic
        return f"{label}: could not be read - {type(exc).__name__}: {exc}"


@mcp.prompt(
    title="Why is the mod not answering?",
    description="The four silences, with this install's current readings.",
)
def diagnose_silence() -> str:
    """Walk the decision tree with live readings already taken.

    The tree exists across four docstrings — `commands` explains `responder:
    false`, `heartbeat` names the four silences, `inventory` splits "not built"
    from "switched off", and `launch` says why a timeout blames the wrong
    thing. Correct in each place and assembled nowhere, so following it meant
    reading four tools' documentation and knowing to.
    """
    cfg_error = None
    try:
        cfg = _cfg()
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        cfg, cfg_error = None, f"{type(exc).__name__}: {exc}"

    if cfg is None:
        readings = (
            f"Nothing could be read: the configuration is unusable.\n\n{cfg_error}\n\n"
            "That IS the diagnosis - fix the paths before looking at the mod."
        )
    else:
        # A WALK, not a single read: once a client has a character it writes a
        # per-player name, so reading only the unsuffixed file reported
        # "client heartbeat: absent" in the one tool whose entire job is
        # explaining why the game is silent. `client_files` finds every
        # client's heartbeat, per-player or not-yet-a-player, the same way
        # `heartbeat` does.
        prefix = cfg.artifacts.prefix
        found = heartbeat_mod.client_files(cfg.save_dir, prefix)
        if found:
            client_lines = []
            for path in found:
                player = heartbeat_mod.player_of(path.name, prefix)
                label = f"client heartbeat ({player})" if player else "client heartbeat"
                client_lines.append(
                    f"{label}: {heartbeat_mod.diagnose(heartbeat_mod.read(path))}"
                )
        else:
            client_lines = [
                (
                    "client heartbeat: none found - no client, live or stale, has "
                    "ever written one"
                )
            ]
        server = heartbeat_mod.read(cfg.artifact(cfg.artifacts.heartbeat, server=True))
        readings = "\n".join(
            [
                f"mod under test: {cfg.mod_name}",
                *client_lines,
                f"server heartbeat: {heartbeat_mod.diagnose(server)}",
                _reading(
                    "mods installed",
                    lambda: [
                        f"{m.name} (enabled={m.enabled}, built_here={m.built_here})"
                        for m in inventory_mod.mods(cfg.save_dir)
                    ],
                ),
                _reading("logs present", lambda: logs_mod.available(cfg.tml_dir)),
            ]
        )

    return f"""\
The mod is not answering. Work out WHICH silence this is before changing
anything — the four have four different fixes, and three of them are not a
code change.

CURRENT READINGS

{readings}

THE TREE

1. No heartbeat on disk at all. Nothing ever wrote one, so the responder is
   not running. Check `inventory`: a mod with `built_here: false` was never
   compiled here, and one with `enabled: false` is switched off in the mod
   list. If it is both built and enabled, it was compiled without the dev
   bridge — the responder is a source-level thing, not a runtime flag.

2. A heartbeat that is STALE. The file outlives the process, so this is a
   game that ran and is no longer running, and it looks identical to a live
   one to anything that only checks the file exists. `logs` on the previous
   run (`previous=true`) is where the crash is, not the current log — a new
   run zips the old one away.

3. Live, but no world loaded. Nothing is wrong yet; it is still starting.
   Wait, or raise the `launch` timeout. Do not rebuild.

4. Live, world loaded, bridge NOT ARMED. The mod is running and its trigger
   responder is not listening. This is the only one of the four that is
   really a mod-side bug.

WHAT NOT TO CONCLUDE

`commands` reporting `responder: false` does not mean the mod is broken —
it means no command list was published, which is 1, 2 and 4 all at once. A
readiness TIMEOUT from `launch` does not mean the machine is slow: it is
whatever the heartbeat says above, reported as a wait.
"""


@mcp.prompt(
    title="Start a session on this machine",
    description="The worlds and characters that actually exist here.",
)
def start_a_session() -> str:
    """`launch` has two preconditions it cannot check. This shows them met.

    Reads the install rather than describing one, because "pass a world path"
    is advice and a list of the three worlds on this disk is an answer.
    """
    try:
        cfg = _cfg()
        worlds = inventory_mod.worlds(cfg.save_dir)
        players = inventory_mod.players(cfg.save_dir)
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        return (
            "This install cannot be read, so there is nothing to launch yet:\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            "Fix the configured paths first — every one is an environment "
            "variable, and the error above names the one that is wrong."
        )

    listing = "\n".join(
        f"  - {w.name}: {w.path_win or 'NO WINDOWS PATH'}" for w in worlds
    )
    return f"""\
Start a driven session on this machine.

WORLDS HERE (pass `world` as the Windows path, which is what tModLoader needs
— it runs as a Windows process and cannot resolve a /mnt path):

{listing or "  (none — a world has to be created in the game first)"}

CHARACTERS HERE: {", ".join(players) or "(none — `launch` cannot create one)"}

`player` must be one of those names. `-player` does not create a character,
and a duplicate name is kicked rather than joined.

THE ONLY MODE IS `server_client`. Singleplayer has no headless entry point and
an empty dedicated server never ticks, so the mod never polls and never
answers; both are refused with the measurement rather than attempted.

Then: `build_mod` if the code changed, `launch`, and `commands` to see what
this mod actually serves. If nothing answers, use the `diagnose_silence`
prompt rather than guessing — a readiness timeout names the wrong cause.
"""


def main() -> None:
    mcp.run()


# `python -m tmodloader_mcp.server` must work, not just the console script — it
# is what a client config most often points at.
if __name__ == "__main__":
    main()


__all__ = ["TriggerError", "main", "mcp"]
