"""Launching, driving and tearing down a tModLoader session.

WHAT MAKES THIS FIDDLY

tModLoader runs as a WINDOWS process even when driven from WSL, so processes are
listed and killed through `tasklist.exe` / `taskkill.exe` rather than POSIX
signals. And a teardown must kill ONLY what this session started: a developer
usually has their own game open, and a harness that killed every tModLoader it
could find would take that with it.

The pid diff is how that is decided. It is not elegant, and the alternative —
trusting a launched handle — does not survive the launcher being a shell that
exits immediately.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .diag import Diag
from .diag import parse as parse_diag
from .diag import sections as diag_sections
from .triggers import (
    DIAG_NAME,
    HEARTBEAT_NAME,
    RESULT_NAME,
    SHOT_NAME,
    TRIGGER_NAME,
    Reply,
    TriggerError,
    compose,
    heartbeat_is_live,
    world_is_ready,
)

#: PowerShell that returns one pid per line for every tModLoader process.
#:
#: NOT a tasklist name grep. tModLoader runs as `dotnet.exe`, so a grep for
#: "tmodloader" in the process NAME matches nothing at all - which is silent and
#: much worse than noisy: the already-running guard never fires, and teardown
#: computes an empty set and reports killing nothing AS SUCCESS, leaving orphans
#: that hold a lock on the .tmod and break the next build.
#:
#: Nor is it a grep for `dotnet.exe`, which matches Roslyn's VBCSCompiler idling
#: after any dotnet build and would report the game running while it is closed.
#: The COMMAND LINE is the only thing that distinguishes them, and only CIM
#: exposes it. Both of these are lessons the shell harness had already learned
#: and written down; this reimplemented them wrongly before reading it.
_PID_QUERY = (
    "Get-CimInstance Win32_Process -Filter \"Name='dotnet.exe'\" | "
    "Where-Object { $_.CommandLine -like '*tModLoader.dll*' } | "
    "ForEach-Object { $_.ProcessId }"
)


def parse_pids(text: str) -> set[int]:
    """Pull pids out of the query's output.

    Split out so the parsing is testable without Windows. An empty result is
    ambiguous by nature - no games running, or a query that broke - so callers
    that need to tell those apart must have a positive control, exactly as the
    shell harness does.
    """
    pids: set[int] = set()
    for line in text.replace("\r", "").split("\n"):
        stripped = line.strip()
        if stripped.isdigit():
            pids.add(int(stripped))
    return pids


#: Terraria has no headless singleplayer entry point. Measured, not assumed:
#: `-join -player <name> -skipselect` lands at the MAIN MENU under both
#: -savedirectory and -tmlsavedirectory, with and without a Worlds folder, and
#: regardless of which character is used.
NO_HEADLESS_SINGLEPLAYER = (
    "tModLoader cannot start a singleplayer world headlessly - `-join -player "
    "-skipselect` lands at the main menu, which is measured rather than assumed. "
    "Load a world yourself, then every other tool here will drive it: the mod "
    "polls its trigger file the same way in singleplayer as in multiplayer. "
    "This is refused rather than silently launched as something else, because a "
    "harness that quietly tested the wrong netmode is how a singleplayer-only "
    "bug shipped here before."
)

#: An EMPTY dedicated server does not tick, so the mod never polls and never
#: answers. Measured 2026-08-07 on ONE server process, changing only whether a
#: client was attached to it:
#:
#:   alone, 90s          -> no artifact anywhere on disk, while tModLoader's own
#:                          log showed the mod loaded and the world open
#:   client joins, +30s  -> the SAME process wrote its heartbeat, reporting
#:                          `polls: 1` and
#:                          `hooks-seen: PostUpdateEverything,PostUpdateWorld`
#:
#: So it is not that the mod is broken on a server. The server runs no update
#: hooks at all until somebody connects, and a mode whose readiness can never
#: arrive is worth refusing rather than waiting five minutes for.
NO_EMPTY_SERVER = (
    "a dedicated server alone never becomes ready, so this mode is refused "
    "rather than left to time out. An empty server runs no update hooks, so the "
    "mod never polls and never answers - measured by attaching a client to a "
    "server that had been silent for 90s, whose heartbeat then appeared within "
    "30s. Use `server_client`, which joins one for you. If you want a server to "
    "join yourself, start it outside this tool: what cannot be honoured here is "
    "the promise that `launch` returns something able to answer."
)


#: How long a killed process may take to leave the process table, in seconds.
#: Long enough that a slow exit is not mistaken for a refused kill, short
#: enough that a genuinely stuck process is reported while the caller is still
#: paying attention.
KILL_SETTLE = 10.0

#: How often the settle poll asks the process table again. Capped by whatever
#: is left of the settle, so a caller passing a bound shorter than this gets the
#: bound it asked for rather than one poll's worth of overshoot.
SETTLE_POLL = 0.5


class SessionError(RuntimeError):
    """The game could not be launched, reached, or shut down."""


def _tml_pids(cfg: Config) -> set[int]:
    """Every tModLoader pid Windows currently reports."""
    try:
        out = subprocess.run(
            [str(cfg.powershell), "-NoProfile", "-Command", _PID_QUERY],
            capture_output=True,
            text=True,
            timeout=60,
            # An empty pid list is a legitimate answer (no game running), so a
            # non-zero exit is parsed rather than raised.
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return set()

    return parse_pids(out)


def _next_capture_index(drop: Path) -> int:
    """The next free capture number in the directory the drop box lives in.

    Asked of the filesystem because the filesystem is what the answer has to be
    true about. Anything held in memory is scoped to one session and cannot
    know what an earlier one already wrote there.
    """
    pattern = re.compile(rf"{re.escape(drop.stem)}-(\d+)-.*")

    highest = 0
    for existing in drop.parent.glob(f"{drop.stem}-*{drop.suffix}"):
        found = pattern.fullmatch(existing.stem)
        if found:
            highest = max(highest, int(found.group(1)))

    return highest + 1


def _write_atomically(path: Path, text: str) -> None:
    """Put `text` at `path` without `path` ever holding a fragment of it.

    The game POLLS the trigger path, so a write in place is readable by it
    half-finished, and a truncated command word is not an error on that side:
    `DevCommands.Parse` maps anything it does not recognise to Unknown and does
    nothing at all. The harness then waits out its timeout for a reply to a
    request that was thrown away, and reports a hang.

    Staged under a name nothing watches and renamed into place, so the polled
    name only ever refers to a finished payload. The staging file is a sibling
    deliberately: a rename is only a rename within one filesystem, and the save
    directory is on /mnt/c while a temp dir would not be.
    """
    staged = path.with_name(f"{path.name}.staging")
    staged.write_text(text)
    os.replace(staged, path)


@dataclass
class Session:
    """One driven game. Holds the pids it started so teardown can be surgical."""

    cfg: Config
    mode: str
    port: int
    player: str
    started: set[int] = field(default_factory=set)

    # ---- artifacts -------------------------------------------------------

    def path(self, name: str, *, server: bool) -> Path:
        return self.cfg.artifact(name, server=server)

    # ---- driving ---------------------------------------------------------

    def ask(
        self,
        command: str,
        *,
        target: str | None = None,
        argument: str | None = None,
        server: bool = False,
        timeout: float = 60.0,
    ) -> Reply:
        """Fire a trigger and wait for the reply file.

        The result file is REMOVED FIRST. Without that, a reply left over from a
        previous request is indistinguishable from a fresh one, and the caller
        reads a stale answer as a current one.
        """
        payload = compose(command, target=target, argument=argument)

        trigger = self.path(TRIGGER_NAME, server=server)
        result = self.path(RESULT_NAME, server=server)

        result.unlink(missing_ok=True)
        _write_atomically(trigger, payload)

        text = self._await_text(result, timeout=timeout, what=f"reply to {payload!r}")
        return Reply(command=command, text=text.strip())

    def diag(
        self, *, server: bool = False, target: str | None = None, timeout: float = 60.0
    ) -> Diag:
        """Ask a side for its state and return it PARSED, both halves.

        The diag file is removed before asking for the same reason the reply file
        is: an old dump reads exactly like a new one.

        BOTH halves, because the dump has two and only one used to survive. The
        scalars said `npcs: active=6 mutated=1`; the indented per-NPC lines under
        them said which six, and were parsed and thrown away — so a caller could
        learn that six existed and never what any of them was.
        """
        dump = self.path(DIAG_NAME, server=server)
        dump.unlink(missing_ok=True)

        reply = self.ask("diag", target=target, server=server, timeout=timeout)
        if not reply.ok:
            raise TriggerError(f"the game refused a diag: {reply.text}")

        text = self._await_text(dump, timeout=timeout, what="diag dump")
        return Diag(fields=parse_diag(text), records=diag_sections(text))

    def shot(
        self, region: str, *, target: str | None = None, timeout: float = 60.0
    ) -> Path:
        """Capture a region of the frame and return the PNG path.

        Region is REQUIRED and has no default - see the README. The reply is
        checked before the file is waited for, so a refusal (bad region name, a
        dedicated server with no back buffer) is reported as itself rather than
        as a timeout.

        THE RETURNED PATH IS UNIQUE PER CAPTURE, and it has to be. The mod
        writes one fixed filename, so this used to hand that same path back
        every time: three regions in a row returned three references to one
        file, each capture silently overwriting the last, and the caller ended
        up holding only whichever it took last. Nothing failed - every call
        reported OK and returned a path that existed - which is exactly why it
        survived a live run and was only caught by opening the images.

        So the fixed name is treated as what it is: a drop box the game writes
        into, which this moves out of before the next capture can land on it.

        The number comes from the DIRECTORY, not from a counter on this object.
        A counter fixed the collision within one session and reintroduced it
        between two: a session ends when the game stops, the save directory
        does not, and a second session began numbering at 001 again and landed
        on the first one's captures. Same silent loss, wearing the fix as a
        disguise - the path handed back was unique among the calls that made
        it, which is not the property anybody needed.
        """
        drop = self.path(SHOT_NAME, server=False)
        drop.unlink(missing_ok=True)

        reply = self.ask("shot", argument=region, target=target, timeout=timeout)
        if not reply.ok:
            raise TriggerError(f"the game refused a shot: {reply.text}")

        self._await_file(drop, timeout=timeout, what="shot PNG")

        # Numbered first so a listing sorts into capture order, and the region
        # kept so a directory of these is readable without a log beside it.
        index = _next_capture_index(drop)
        kept = drop.with_name(f"{drop.stem}-{index:03d}-{region}{drop.suffix}")
        drop.replace(kept)
        return kept

    # ---- waiting ---------------------------------------------------------

    def _await_file(self, path: Path, *, timeout: float, what: str) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.is_file():
                return
            time.sleep(0.5)

        raise TriggerError(
            f"no {what} within {timeout:.0f}s at {path}. The game may not be "
            f"polling - check that a world is loaded and the mod is enabled."
        )

    def _await_text(self, path: Path, *, timeout: float, what: str) -> str:
        """Read a file once it has STOPPED CHANGING, not once it exists.

        A file appears when it is created, not when it is written, so this used
        to sleep 0.4s first - a race with a timer on it. The timer does not make
        the write finish; it makes it usually finish, and "usually" moves with
        the machine and the size of the dump.

        A short read is not a visible failure either, which is what makes it
        worth a loop rather than a longer sleep: `Reply.ok` reads a truncated
        `REFUSED...` as success, and half a diag parses into believable fields
        with some keys simply absent.

        An empty file is treated as still being written rather than as an empty
        answer. The mod has no command that legitimately replies with nothing,
        so emptiness here means the writer got as far as creating the file - and
        returning "" would be the same torn read wearing different clothes.
        """
        deadline = time.monotonic() + timeout
        self._await_file(path, timeout=timeout, what=what)

        previous = path.read_text(errors="replace")
        while time.monotonic() < deadline:
            time.sleep(0.2)
            current = path.read_text(errors="replace")
            if current == previous and current != "":
                return current
            previous = current

        raise TriggerError(
            f"the {what} at {path} was still being written after {timeout:.0f}s "
            "(its contents kept changing, or it stayed empty). Nothing there is "
            "safe to read as an answer."
        )


def world_problem(world: str) -> str | None:
    """Why this world argument will not work, or None.

    Checked BEFORE launching because the failure is otherwise silent and
    misattributed: tModLoader runs as a WINDOWS process, so a /mnt/c path is
    simply not found, the server never loads a world, and the only symptom is a
    readiness timeout that blames the heartbeat. Five minutes to learn the wrong
    thing.
    """
    if world.startswith("/"):
        return (
            f"world path {world!r} is a WSL path. tModLoader runs as a Windows "
            "process and cannot resolve /mnt/c - pass a Windows path such as "
            r"C:\\Users\\you\\Documents\\My Games\\Terraria\\tModLoader"
            r"\\Worlds\\Yours.wld (set TMODLOADER_WORLD_WIN)."
        )

    if not world.lower().endswith(".wld"):
        return (
            f"world path {world!r} does not name a .wld file. A directory is not "
            "a world: the server starts, finds nothing to load, and never "
            "reports a ready heartbeat."
        )

    return None


def launch(
    cfg: Config,
    mode: str,
    *,
    port: int = 7810,
    player: str = "n43n",
    world: str | None = None,
    timeout: float = 300.0,
) -> Session:
    """Start a game and wait until it is actually ready to answer.

    `mode` is "server_client". Both other modes are refused, each because the
    engine cannot satisfy what this function promises - see
    NO_HEADLESS_SINGLEPLAYER and NO_EMPTY_SERVER.
    """
    if mode == "singleplayer":
        raise SessionError(NO_HEADLESS_SINGLEPLAYER)

    if mode == "server":
        raise SessionError(NO_EMPTY_SERVER)

    if mode not in {"server", "server_client"}:
        raise SessionError(
            f"unknown mode {mode!r} - expected 'server' or 'server_client'"
        )

    existing = _tml_pids(cfg)
    if existing:
        raise SessionError(
            f"tModLoader is already running (pids {sorted(existing)}). Close it, "
            "or stop the previous session - two instances share one save "
            "directory and would consume each other's trigger files."
        )

    session = Session(cfg=cfg, mode=mode, port=port, player=player)

    # Clear stale artifacts BEFORE launching. A heartbeat or reply left by a
    # previous run is what lets a readiness check pass against a dead process.
    for name in (TRIGGER_NAME, RESULT_NAME, DIAG_NAME, HEARTBEAT_NAME, SHOT_NAME):
        for server in (False, True):
            cfg.artifact(name, server=server).unlink(missing_ok=True)

    world_arg = world or cfg.world_win
    problem = world_problem(world_arg)
    if problem:
        raise SessionError(problem)

    server_cmd = [
        str(cfg.dotnet),
        "tModLoader.dll",
        "-server",
        "-world",
        world_arg,
        "-players",
        "4",
        "-port",
        str(port),
        "-noupnp",
        "-lang",
        "en-US",
    ]
    # THE OWNERSHIP BLOCK OPENS AT THE FIRST SPAWN, NOT AT THE FIRST WAIT.
    #
    # It used to start below, after both processes were already running. The
    # window that left is small and entirely real: a client that cannot be
    # started leaves the server up, and so does a KeyboardInterrupt arriving
    # between the two - the very interrupt the comment below cites as the case
    # that matters most. A leak the block was written to close, one statement
    # above where the block began.
    try:
        subprocess.Popen(
            server_cmd,
            cwd=str(cfg.tml_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )

        if mode == "server_client":
            client_cmd = [
                str(cfg.dotnet),
                "tModLoader.dll",
                "-join",
                "127.0.0.1",
                "-port",
                str(port),
                "-player",
                player,
                "-skipselect",
            ]
            subprocess.Popen(
                client_cmd,
                cwd=str(cfg.tml_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )

        _wait_ready(cfg, mode=mode, timeout=timeout)
    except BaseException as failure:
        # WHOEVER SPAWNS OWNS THEM UNTIL IT CAN HAND THEM BACK.
        #
        # This used to raise straight out. The processes it had just started
        # stayed up held by nobody: `launch` never returned, so no Session ever
        # reached the caller, and `stop()` answered "no session was running" and
        # killed nothing. The orphan then poisoned the NEXT launch, which refuses
        # to start over a running game - so one readiness timeout cost two
        # failures and a manual hunt through tasklist.
        #
        # BaseException rather than Exception: a KeyboardInterrupt or a timeout
        # during a five-minute wait is exactly when this matters most, and those
        # do not derive from Exception.
        session.started = _tml_pids(cfg) - existing
        try:
            stop(cfg, session)
        except SessionError as leak:
            # ATTACHED TO THE ORIGINAL FAILURE, NOT RAISED OVER IT. Why the
            # launch failed is what the caller has to act on; that a process
            # also survived the cleanup is something they need told, not
            # something that should replace it. Raising here would swap a
            # readiness timeout - with its mode-specific advice - for a message
            # about pids, and the reason the game never came up would be gone
            # from the top of the traceback.
            failure.add_note(str(leak))
        raise

    session.started = _tml_pids(cfg) - existing
    return session


def _wait_ready(cfg: Config, *, mode: str, timeout: float) -> None:
    """Block until the heartbeat says a world is live AND is recent.

    Both conditions, because they fail differently: a stale-but-ready heartbeat
    means the process died after loading, and a fresh-but-not-ready one means it
    is still loading. Checking only existence conflates them, which is exactly
    how a harness once sailed past three gates on a killed client's file.
    """
    server_hb = cfg.artifact(HEARTBEAT_NAME, server=True)
    client_hb = cfg.artifact(HEARTBEAT_NAME, server=False)
    wanted = [server_hb] + ([client_hb] if mode == "server_client" else [])

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(
            heartbeat_is_live(p) and world_is_ready(p.read_text(errors="replace"))
            for p in wanted
        ):
            # A short settle after world-ready: the mod refuses commands until a
            # world has been live a few seconds, because serving one at the
            # instant capture first becomes possible crashed the engine once.
            time.sleep(5)
            return
        time.sleep(2)

    missing = [p.name for p in wanted if not heartbeat_is_live(p)]

    # THE ADVICE HAS TO MATCH THE MODE THAT FAILED.
    #
    # This used to blame Steam unconditionally. In `server` mode there is no
    # client, so that sentence named a cause which could not apply - and it
    # cost a real debugging session: Steam genuinely WAS down, the advice fit
    # the client failure, so the identical server-mode failure was filed under
    # the same cause. Bringing Steam up fixed one and not the other, which is
    # the only reason the wrong attribution surfaced at all.
    if client_hb.name in missing:
        hint = (
            "The client requires Steam to be running and logged in - check that "
            "first, it is the usual cause."
        )
    else:
        # THE CLIENT IS UP AND THE SERVER IS NOT - which is a different failure
        # from the one above, and used to be described with a sentence written
        # for `server` mode: "no client is involved in this mode". One IS
        # involved here; it is the half that worked.
        #
        # A server only starts ticking once somebody connects (NO_EMPTY_SERVER),
        # so the thing to suspect is the JOIN, not the server's startup: it can
        # be listening, with the world open, and still be silent because nothing
        # reached it.
        hint = (
            "The client is up but the server is not reporting. Steam is NOT the "
            "likely cause - a server stays silent until a client actually joins "
            "it, so suspect the join rather than the server's startup: a wrong "
            "port, a refused connection, or a character name that was kicked. "
            "The server can be listening with the world loaded and still never "
            "answer, because nothing has connected to make it tick."
        )

    raise SessionError(
        f"no live heartbeat within {timeout:.0f}s (missing or stale: {missing}). "
        f"The game may have failed to start - check the logs. {hint}"
    )


def stop(
    cfg: Config, session: Session | None, *, settle: float = KILL_SETTLE
) -> list[int]:
    """Kill only the processes this session started. Returns the pids VERIFIED gone.

    `settle` is how long a killed process may take to leave the process table.
    It is an ARGUMENT because every other timed thing here takes one - `ask`,
    `launch`, `build`, `_await_text` - and this was the exception. A bound
    nothing can set is a bound nothing can check: the tests could not shorten
    it, so they reached for the only lever left and patched `time.sleep` to a
    no-op, which shortens nothing. The loop ends on `time.monotonic()`, so
    removing the sleep left the full wait in place and took away the only thing
    yielding during it - two tests spinning at full CPU for ten seconds each.

    Surgical on purpose. A developer usually has their own game open, and a
    teardown that killed every tModLoader it could find would take it with them.

    ISSUING A KILL IS NOT EVIDENCE THAT ANYTHING DIED. This used to append each
    pid to its result as soon as taskkill returned. The exit code is ignored on
    purpose - a pid that died between the listing and the kill exits non-zero,
    and that is the outcome we wanted - but the same ignoring absorbed the
    opposite case, a kill that is refused while the process carries on. What
    came back was a list of the right shape that read as a completed teardown,
    and the mistake surfaced later and elsewhere: as the NEXT launch refusing
    to start over a game nobody remembered leaving open.

    So the result is read back off the process table, which is the thing the
    claim has to be true about - the same move as numbering captures from the
    directory rather than from a counter. Anything still there stays in
    `session.started`, because a survivor that has lost its owner is exactly
    the orphan the launch path already paid for once, arriving by the opposite
    route: there it was never owned, here ownership was given up while the
    process was still alive.
    """
    if session is None:
        return []

    live = _tml_pids(cfg)
    aimed = sorted(session.started & live)

    for pid in aimed:
        try:
            subprocess.run(
                [str(cfg.taskkill), "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=30,
                # See the docstring: the exit code cannot distinguish the two
                # failures that matter, so nothing is concluded from it.
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            # Not skipped past - a kill that could not even be issued leaves a
            # live process, and the check below is what will say so.
            continue

    # Terminating is not instantaneous: /F asks Windows to end the process and
    # returns before the table has caught up. Verifying immediately would
    # report a successful kill as a survivor, which is a false alarm about the
    # one thing this function now exists to be trusted on.
    #
    # The poll never outlasts what is left of the settle, so the loop cannot
    # overshoot its own bound and cannot spin: every iteration either sleeps or
    # ends.
    deadline = time.monotonic() + settle
    survivors = set(aimed) & _tml_pids(cfg)
    while survivors:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(SETTLE_POLL, remaining))
        survivors = set(aimed) & _tml_pids(cfg)

    for name in (TRIGGER_NAME,):
        for server in (False, True):
            cfg.artifact(name, server=server).unlink(missing_ok=True)

    session.started = survivors

    if survivors:
        raise SessionError(
            f"taskkill was issued for {aimed}, but {sorted(survivors)} is still "
            f"running {settle:.0f}s later. Those pids remain owned by this "
            "session, so calling `stop` again will retry them - but if they "
            "survive that too, end them yourself: a leftover game holds the "
            ".tmod against the next build and makes the next launch refuse to "
            "start over a process nobody remembers starting."
        )

    return aimed
