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

import contextlib
import math
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from . import commands as commands_mod
from . import inventory
from .commands import CommandSet
from .config import Config
from .diag import Diag
from .diag import parse as parse_diag
from .diag import sections as diag_sections
from .triggers import (
    Artifacts,
    Reply,
    TriggerError,
    artifacts_for,
    compose,
    heartbeat_is_live,
    parse,
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

#: The eight bytes every PNG begins with.
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: The twelve bytes every PNG ends with - an empty IEND chunk, whose CRC is
#: therefore a constant. BOTH ends are checked, and the second is the one that
#: matters: a file caught mid-write already has a perfectly valid signature, so
#: a check that read only the head would promote half a picture.
_PNG_TRAILER = b"\x00\x00\x00\x00IEND\xaeB`\x82"


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

#: How often a blocked claim retries for the trigger slot. Much shorter than
#: SETTLE_POLL: a slot is normally freed within one of the mod's poll ticks, so
#: this is the resolution at which "already gone" is noticed, not a settle.
CLAIM_POLL = 0.1

#: When a held capture lock stops being believable. A capture is bounded by
#: the mod's own settle window - `SettleTicks = 900`, about 15s at 60fps -
#: plus the round trip; the observed live capture took 1.2s. Four times the
#: WORST case, not the observed one, because the observed one was lucky.
CAPTURE_LOCK_STALE = 60.0

#: The most a stamp may ever delay a capture. Crossing one second boundary is
#: all the rule needs; anything longer means the stamp is wrong - a clock that
#: ran ahead, a hand-edited file - and a wrong stamp must cost a moment, not
#: an outage.
STAMP_WAIT_MAX = 1.0

#: Slop allowed past a holder's own recorded deadline before its lock may be
#: broken. SLOP, NOT SAFETY: it covers the holder's own `finally` - write the
#: stamp, unlink the lock - and the offset between the two clocks this rule
#: compares. It does NOT make an early break safe; that is what the deadline
#: itself is for.
#:
#: MEASURED 2026-08-15 on this DrvFs save directory, rather than guessed: a
#: file written at `time.time()` lands with an mtime 0.446-0.463s AHEAD of it,
#: five writes running, and two writes 10ms apart differ by 19ms. So the
#: resolution is fine, and the skew is one-directional and small - `age` reads
#: about half a second YOUNGER than the truth, which errs toward keeping a
#: lock rather than breaking it. Two seconds leaves roughly 4x headroom over
#: the skew actually observed.
CAPTURE_LOCK_GRACE = 2.0

#: The longest a holder's own recorded deadline is believed. Ten minutes is
#: far past any capture anybody has run - the slowest observed was 8s under
#: contention - and it is a BACKSTOP rather than the rule: without it a lock
#: claimed while the clock was running ahead records a budget nobody meant,
#: and nothing would ever break it. That trades this feature's bounded
#: failure, a collision, for an unbounded one: captures wedged for both
#: sessions until somebody deletes a file by hand. Capped for the same reason
#: STAMP_WAIT_MAX is capped, and the reasoning there is worth reading twice -
#: a wrong value in a shared file must cost a moment, not an outage.
CAPTURE_LOCK_MAX = 600.0


class SessionError(RuntimeError):
    """The game could not be launched, reached, or shut down."""


class SlotBusy(RuntimeError):
    """Another request already holds the name being claimed.

    Not an error the caller sees. Both callers catch it and wait, because a
    slot held by a request the game is about to consume is a normal,
    sub-second condition rather than a fault.

    Named for the SLOT rather than the trigger because there are two: the
    trigger a request is written to, and the capture lock that keeps two
    captures out of one wall-clock second.
    """


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


def _claim_atomically(path: Path, text: str) -> None:
    """Put `text` at `path` without `path` ever holding a fragment of it, and
    ONLY IF NOBODY ELSE HAS IT.

    The game POLLS the trigger path, so a write in place is readable by it
    half-finished, and a truncated command word is not an error on that side:
    `DevCommands.Parse` maps anything it does not recognise to Unknown and does
    nothing at all. The harness then waits out its timeout for a reply to a
    request that was thrown away, and reports a hang.

    Staged under a name nothing watches and linked into place, so the polled
    name only ever refers to a finished payload. The staging file is a sibling
    deliberately: a hard link only works within one filesystem, and the save
    directory is on /mnt/c while a temp dir would not be.

    THE STAGING NAME IS UNIQUE PER WRITE, and it has to be. It used to be
    `<path>.staging` - one name, derived from the polled path, which every
    client shares BY DESIGN. So two requests in flight at once shared one
    staging file: the last write won its contents, the first rename carried
    them, and the second rename raised `FileNotFoundError` having already lost
    its payload. One request silently replaced by another's - a lost update, in
    the one place this project exists to make unambiguous.

    Found by firing two captures at once, which is a thing this project could
    not produce until two clients could run at once. Per-write rather than
    per-session, because two threads driving one session collide identically
    and a per-player name would fix only the case that happened to be found.

    THE CLAIM IS THE POINT, and it is free. `os.replace` succeeded whatever was
    already there, so two sessions sharing this path meant the second silently
    replaced the first: measured as one capture answering in 1.2s while the
    other timed out at 120s having never had a request on disk. `os.link` is
    atomic in exactly the same way and refuses an occupied name, so exclusivity
    costs no second file to keep consistent with this one.
    """
    staged = path.with_name(f"{path.name}.{uuid.uuid4().hex[:12]}.staging")
    try:
        staged.write_text(text)
        try:
            os.link(staged, path)
        except FileExistsError as taken:
            raise SlotBusy(str(path)) from taken
    finally:
        # `os.replace` consumed the staging name; `os.link` leaves it as a
        # second name for the same inode, so this is now the ordinary path
        # rather than only the failure path.
        staged.unlink(missing_ok=True)


def _pending_payload(trigger: Path) -> str | None:
    """What the trigger holds, or None if there is nothing there.

    None means "no such file", and only that: a request some client is waiting
    on an answer to is exactly what it is not.

    READ THE WAY THE MOD READS, which is what `errors="replace"` is doing here
    and it is not error tolerance. `_is_ours_to_clear` treats an unreadable
    payload as this session's to delete, justified entirely by "the mod cannot
    read it either, so no client will ever consume it" - and the mod's
    `File.ReadAllText` does not have this side's failure mode. It never throws
    on bad UTF-8; it substitutes U+FFFD and parses what is left. Bytes that
    are invalid only inside the VERB therefore still yield a clean target over
    there. Giving up on them here would call another client's live request
    ours and delete it. Substituting the same way they do is what makes the
    two sides reach the same verdict on the same bytes.
    """
    try:
        return trigger.read_text(errors="replace")
    except OSError:
        return None


def _will_capture(payload: str) -> bool:
    """Whether this payload makes the game take a picture.

    Asked of `parse` rather than of the string, because the answer is the
    mod's and `parse` is this side's model of the mod's parser. Two rules
    come free that a string comparison would get wrong: a bare payload IS a
    capture (`DevResponder.cs:429` - the behaviour predates commands), and a
    malformed one is not, since `DevCommands.Parse` maps what it cannot read
    to Unknown and does nothing at all.
    """
    request = parse(payload)
    return request is not None and request.command == "capture"


class Broken(NamedTuple):
    """A lock this call removed: how old it was, and which rule said so."""

    age: float
    #: True when the holder's own recorded deadline had passed. False when the
    #: lock said nothing readable and `CAPTURE_LOCK_STALE` decided instead -
    #: a materially weaker claim, and the caller tells them apart in the note
    #: because "your picture waited on a guess" and "the last holder promised
    #: to be gone and was not" send a reader to different places.
    by_deadline: bool


def _break_stale_lock(lock: Path) -> Broken | None:
    """Remove a capture lock no live capture could still be holding, and say
    how old it was and why. `None` when nothing was broken.

    THE HOLDER'S OWN DEADLINE IS THE BOUND, and age is the fallback for a
    holder that did not record one. Age alone was a guess in both directions:
    it broke a live capture whose caller had legitimately asked for longer
    than 60s - reachable straight from `trigger`'s own advice about large
    worlds - and it made a dead capture with a five-second timeout wedge the
    other session for a full minute. A deadline the holder wrote itself is
    true by construction, because `ask` spends one budget across all three of
    its waits and cannot outlive it.

    Breaking wrongly costs a collision, which is exactly what shipped in
    0.3.0; breaking the TRIGGER claim wrongly destroys a request somebody is
    waiting on. That asymmetry is why this rule exists here and must not be
    carried over there.

    THE RETURN IS "DID THIS CALL REMOVE IT", not merely "was it stale". The
    holder may release between the stat and the unlink - a race this function
    WINS by doing nothing further - and that race's `FileNotFoundError` used
    to be swallowed the same way a permission failure is, with the age handed
    back regardless of which happened. That age reaches a caller as "a stale
    capture lock was broken to take this capture", and in that interleaving
    nothing here broke anything - the holder finished on its own. Saying so
    anyway would be a false claim about a judgement call nobody made. So the
    unlink's own outcome decides the answer, not just the stat that preceded
    it.
    """
    try:
        age = time.time() - lock.stat().st_mtime
    except OSError:
        return None

    deadline = _lock_deadline(lock)
    if deadline is None:
        expired = age > CAPTURE_LOCK_STALE
    else:
        # CAPPED AGAINST THE LOCK'S OWN AGE, for the reason STAMP_WAIT_MAX is
        # capped. The comparison itself needs no cap - both deadlines are
        # written by harness processes on one kernel, reading one clock. What
        # needs one is a deadline that is simply WRONG: claimed while the
        # clock ran ahead, or edited by hand. Uncapped, such a lock is
        # protected for as long as the error lasts and captures wedge for both
        # sessions until somebody deletes a file - trading this mechanism's
        # bounded failure, a collision, for an unbounded one. The cap is
        # anchored to the mtime rather than to now, because an anchor that
        # moves with the reader can always be outrun.
        expired = time.time() > (
            min(deadline, lock.stat().st_mtime + CAPTURE_LOCK_MAX) + CAPTURE_LOCK_GRACE
        )

    if not expired:
        return None

    try:
        lock.unlink()
    except OSError:
        # Gone already (the race above), or some other removal failure - either
        # way this call did not perform the break, so it does not get to claim
        # one. `_claim_capture`'s retry is unaffected: the lock is not held
        # either way, so retrying is correct on both branches of this except.
        return None
    return Broken(age=age, by_deadline=deadline is not None)


def _lock_payload(deadline: float) -> str:
    """What a capture lock holds: who took it, and when they promise to be gone.

    The pid is a breadcrumb - nothing reads it, and a developer staring at a
    wedged directory wants to know which process to look for. The deadline is
    load-bearing: it is what lets the NEXT session decide whether this holder
    can still be alive, instead of guessing from age.

    WALL CLOCK, not `time.monotonic()`, and the reason is the reader. A
    monotonic value means nothing to a process that did not start the clock,
    and the two other things anyone compares against this file - the stamp and
    the lock's own mtime - are already wall clock. One protocol file with two
    clocks in it is a trap for whoever reads it next.
    """
    return f"{os.getpid()}\n{deadline:.6f}"


def _lock_deadline(lock: Path) -> float | None:
    """When the holder said it would be gone, or None if it did not say.

    NEVER RAISES, for the same reason `_pending_payload` reads with
    `errors="replace"`: this file is read by a process that did not write it,
    and every way it can be wrong - absent, empty, truncated mid-write, hand
    edited, written by an older version that stored only a pid - has to arrive
    as "no deadline here" rather than as an exception on a path whose whole
    job is deciding whether somebody else is alive.

    A deadline that cannot be read is not an error, it is the OLD contract:
    the caller falls back to `CAPTURE_LOCK_STALE`.
    """
    try:
        lines = lock.read_text(errors="replace").splitlines()
    except OSError:
        return None

    if len(lines) < 2:
        return None

    try:
        deadline = float(lines[1])
    except ValueError:
        return None

    # A NaN compares false against everything, so it would neither break a
    # lock nor protect one - it would silently disable the bound. Infinity is
    # worse: it protects the lock forever. Both are reachable from `float()`
    # on text somebody wrote by hand, so neither counts as a deadline.
    if not math.isfinite(deadline):
        return None

    return deadline


def _write_stamp(stamp: Path, when: float) -> None:
    """Record when a capture's reply arrived, for whoever captures next.

    Best effort on purpose: a stamp that cannot be written costs a possible
    collision, and raising here would cost a capture that already succeeded.
    """
    with contextlib.suppress(OSError):
        stamp.write_text(f"{when:.6f}")


def _release_capture(lock: Path, stamp: Path, *, when: float) -> OSError | None:
    """Give up a held capture lock, and say so if the lock would not go.

    THE ORDER IS THE PROTOCOL, not a reading preference. The stamp is written
    BEFORE the lock is unlinked, and reversing the two statements is a silent
    hole rather than a slower path: the unlink frees the name, a session
    already polling for it claims it in the same breath, and `_stamp_wait`
    then reads whatever the PREVIOUS capture left behind - a second that
    passed long ago - so the boundary wait returns 0.0 and both captures land
    in one second. That is this feature's entire failure mode, reached through
    the release rather than through the claim, and nothing about how the two
    statements look says which order they belong in.

    THE UNLINK'S FAILURE IS RETURNED, NOT RAISED, because the only caller runs
    this from a `finally`. An OSError escaping from there REPLACES whatever
    the caller was about to receive: the reply to a capture that worked, or
    the exception explaining one that did not. Trading the answer for the
    housekeeping is the wrong way round in both directions - and the lock left
    behind is bounded anyway, since it carries its holder's own deadline and
    the next session breaks it on that.

    Not swallowed either. The caller folds it into the note, because a lock
    that will not go is the one thing on this path a human can act on: until
    its deadline passes, every capture from either session waits on it.
    """
    _write_stamp(stamp, when)
    try:
        lock.unlink(missing_ok=True)
    except OSError as stuck:
        return stuck
    return None


def _stamp_wait(stamp: Path, *, now: float) -> float:
    """How long to wait before capturing, so as not to land in the stamped
    second.

    `now` is passed in rather than read here so the boundary arithmetic can
    be tested without sleeping through it.

    ZERO IS THE DEFAULT ANSWER for everything doubtful - no stamp, an
    unreadable one, one that does not parse, one whose second has passed.
    This is an optimisation for a case that has to be observed to matter, and
    it is never a reason to refuse or to stall.
    """
    try:
        recorded = float(stamp.read_text(errors="replace").strip())
    except (OSError, ValueError):
        return 0.0

    remaining = (math.floor(recorded) + 1) - now
    if remaining <= 0:
        return 0.0
    return min(remaining, STAMP_WAIT_MAX)


def _is_ours_to_clear(payload: str | None, *, player: str | None) -> bool:
    """Whether a trigger holding `payload` is this session's to delete.

    THE SHARED SLOT IS NOT THIS SESSION'S PROPERTY. One trigger file is polled
    by every client sharing a save directory - deliberately, because seeing a
    request is how a client learns one is aimed at somebody else - so deleting
    it wholesale is the same lost update `_claim_atomically` exists to prevent,
    committed by the housekeeping rather than by the write. `launch` and `stop`
    both did exactly that, which meant one developer starting a game destroyed
    the other's in-flight request while their game was still polling for it.

    The rule is the mod's own, and stating it identically on both sides is the
    point: `DevResponder` leaves a request it is not addressed by exactly where
    it is, so the intended client finds it on its own next poll. This side
    leaves alone precisely what that client would still collect.

    AN UNREADABLE OR MALFORMED PAYLOAD IS OURS, and that is not a shortcut. The
    mod's parser will not get a target out of it either, so no client will ever
    consume it - and a request nobody can consume, sitting in a slot that holds
    one request, wedges that slot for everybody. Same for one carrying no
    address: it belongs to whoever polls first, and this session is a whoever.

    Compared case-insensitively, because the mod compares a target to its own
    name that way - `n43n` and `N43N` are one client, however it was typed.

    THE ADDRESS IS THE ONLY SIGNAL, which bounds this: two sessions each driving
    a DEDICATED SERVER out of one save directory write unaddressed requests to
    the server-side trigger and are indistinguishable here. Nothing on disk
    separates them; the client side, where two sessions actually is the
    supported arrangement, carries a player and does separate.
    """
    if payload is None:
        return True
    request = parse(payload)
    if request is None or request.target is None:
        return True
    return player is not None and request.target.casefold() == player.casefold()


def _release_trigger(cfg: Config, *, player: str | None) -> None:
    """Give up the shared trigger on both sides, where it is ours to give up.

    Called instead of unlinking it, by everything that used to unlink it. See
    `_is_ours_to_clear` for which of the two cases each side falls into.
    """
    for server in (False, True):
        trigger = cfg.artifact(cfg.artifacts.trigger, server=server)
        if _is_ours_to_clear(_pending_payload(trigger), player=player):
            trigger.unlink(missing_ok=True)


@dataclass
class Session:
    """One driven game. Holds the pids it started so teardown can be surgical."""

    cfg: Config
    mode: str
    port: int
    player: str
    #: The world this session actually loaded, RESOLVED - the caller's argument
    #: if they gave one, and `cfg.world_win` if they did not. Storing the raw
    #: argument would record `None` for the commonest case and lose which world
    #: is running, which is the thing worth knowing.
    #:
    #: Kept because `launch` took `world`, used it, and forgot it. `status`
    #: could therefore report the mode, port and player of a session while
    #: staying silent about the only field that says WHICH WORLD - and anything
    #: relaunching from a session's own parameters would quietly substitute the
    #: configured default for the world that was asked for.
    world: str | None = None
    started: set[int] = field(default_factory=set)

    # ---- artifacts -------------------------------------------------------

    def path(self, name: str, *, server: bool) -> Path:
        return self.cfg.artifact(name, server=server)

    @property
    def artifacts(self) -> Artifacts:
        """This session's names, carrying its player.

        `cfg.artifacts` has no player and stays the right answer for the
        dedicated server and for anything reading off disk without a session.
        """
        return artifacts_for(self.cfg.mod_name, self.player)

    def _names(self, server: bool, player: str | None = None) -> Artifacts:
        """Per-player names for the client, unsuffixed ones for the server.

        The dedicated server has no player. Handing it a token would rename
        files it writes under names nothing reads.

        `player` IS THE ADDRESSEE, and defaults to this session's own. That
        distinction is the whole of a live failure: an answer is written by the
        client the request NAMED, under ITS token, so the path to wait at is a
        function of who was addressed rather than of who asked. This used to
        read `self.player` unconditionally, so `diag(target='somebody else')`
        waited out its full timeout at a filename that client never touches -
        while the answer sat on disk beside it, correct and unread. The
        session's player is only the DEFAULT addressee.

        It worked before answers were per-player, and that is the uncomfortable
        part: every client wrote one shared reply file, so addressing worked
        BECAUSE answers were ambiguous. Removing the ambiguity is what broke it.

        A TARGET NAMING THIS SESSION'S OWN PLAYER RESOLVES TO ITS SPELLING,
        whatever case it was typed in. The mod compares a target to its own
        name case-insensitively and then writes under its OWN name, while the
        token's four hex characters are the MD5 of the ORIGINAL bytes - so
        `n43n` and `N43N` are one client and two filenames. Keying the wait to
        the target as typed would have made `diag(target='N43N')` time out
        against a client answering perfectly: the very failure this fix exists
        to remove, one case-fold away from it.

        For any OTHER player there is no canonical spelling to resolve to - the
        only account of that client's name is the client's own - so the target
        must match the character name exactly. Inherent rather than an
        oversight: the digest distinguishes names that differ ONLY by case,
        which is the point of digesting the original bytes.
        """
        if server:
            return self.cfg.artifacts

        if player is None or player.casefold() == self.player.casefold():
            player = self.player

        return artifacts_for(self.cfg.mod_name, player)

    def commands(self, *, server: bool = False) -> CommandSet:
        """What this side's mod says it serves.

        Read fresh rather than cached at launch. A mod reload republishes the
        list, and a set remembered from before one would describe a responder
        that no longer exists — the same staleness the heartbeat has, where the
        file outlives what wrote it. It is one small file; re-reading costs less
        than being wrong about it.

        Raises `CommandsMissing` when nothing published one, which is a
        different answer from a timeout: nothing is on its way.
        """
        return commands_mod.read(self.path(self.cfg.artifacts.commands, server=server))

    # ---- driving ---------------------------------------------------------

    def _busy_message(self, trigger: Path, timeout: float) -> str:
        """Why a claim gave up, describing ONLY what is observable.

        The caller's own request is fine and so is the game, so a timeout that
        described either would send them to check the two things that are not
        wrong.

        IT USED TO SAY "Another session's request is pending", WHICH IS AN
        OWNERSHIP CLAIM THIS SIDE NEVER CHECKED. Nothing validates that an
        addressed target names a live client, and the mod deliberately leaves a
        request it is not addressed by exactly where it is - so a typo'd
        `target`, or a client that has not loaded a character (its name is
        empty, so it matches no target), parks a request no client will ever
        consume. Every later call from BOTH sessions then failed with that
        sentence, which about the caller's own typo names the wrong culprit and
        sends them looking for a session that may not exist. Under `os.replace`
        the next request simply flattened it; the claim removed that accident,
        and this message is where the missing remedy is handed back.

        So: the payload, its age, and - when it is addressed to this session's
        own player - that it is theirs and how to be rid of it. That case is
        the self-inflicted one and the only one this side can be sure about.
        """
        try:
            pending = trigger.read_text().strip()
            age = time.time() - trigger.stat().st_mtime
            held = f"{pending!r}, {age:.0f}s old"
        except (OSError, UnicodeDecodeError):
            pending = None
            held = "a request that vanished while it was being read"

        request = parse(pending) if pending is not None else None
        addressed_to_me = (
            request is not None
            and request.target is not None
            and request.target.casefold() == self.player.casefold()
        )

        if addressed_to_me:
            tail = (
                f"It is addressed to this session's own player ({self.player!r}), "
                "so it is nobody else's to collect - and nothing has collected "
                "it, which means no client of that name is polling: either none "
                "is running, or one is running without a loaded character, whose "
                "name is empty and matches no target. A fresh `launch` clears a "
                "pending request addressed to this session's player; so does "
                "deleting the file."
            )
        else:
            tail = (
                "Whose request that is cannot be read off the trigger beyond the "
                "address it carries, so this session will not delete it - it may "
                "belong to another session still waiting for the answer, and "
                "deleting it would be the overwrite this claim exists to "
                "prevent. If the client it names is gone, remove the file by hand."
            )

        return f"the trigger at {trigger} still holds {held} after {timeout:g}s. {tail}"

    def _claimed_out_of_time_message(self, trigger: Path, timeout: float) -> str:
        """Why a claim that WON still has to fail: the game may still answer it.

        Deliberately not `_busy_message`. That one describes a request the
        caller never got - another session's, still in the way, safe to leave
        alone and safe to retry against immediately. This one describes a
        request the caller DID get: it is this session's own, it is sitting on
        the trigger the game polls, and the game will most likely serve it.
        What ran out was this call's own time to keep waiting, not the game's
        silence - so the fix is a longer timeout, not a check on the game or a
        deletion of anything.

        The trigger is left claimed rather than withdrawn - see `_claim` - so a
        retry issued right away may find this same request of its own still
        sitting there. That is the expected shape of a retry here, not a stall.
        """
        try:
            pending = trigger.read_text().strip()
            held = f"{pending!r}"
        except (OSError, UnicodeDecodeError):
            held = "its own request"

        return (
            f"the trigger at {trigger} now holds {held}, this session's own "
            f"claim, with none of the {timeout:g}s timeout left to wait for a "
            "reply. The game may still answer it - what ran out was this "
            "call's own time, not the game's silence. Retry with a longer "
            "timeout; a retry started right away may find this same request "
            "of its own still pending, which is expected rather than stuck."
        )

    def _claim(self, trigger: Path, payload: str, *, timeout: float) -> float:
        """Take the trigger slot, and return what is LEFT of `timeout`.

        Returning the remainder rather than swallowing it is the whole point:
        the caller asked for an answer within `timeout`, not for `timeout`
        waiting to ask plus `timeout` waiting to hear.

        A claim that only succeeds once the budget is essentially gone is
        still a WIN, not a loss - the trigger now holds the caller's own
        request rather than somebody else's. Handing that case `_busy_message`
        would tell the caller to go remove their own live request by hand, so
        it gets its own message and its own trigger is left exactly as claimed.
        """
        deadline = time.monotonic() + timeout
        while True:
            try:
                _claim_atomically(trigger, payload)
            except SlotBusy:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TriggerError(self._busy_message(trigger, timeout)) from None
                time.sleep(min(CLAIM_POLL, remaining))
                continue
            remaining = max(0.0, deadline - time.monotonic())
            if remaining < CLAIM_POLL:
                raise TriggerError(self._claimed_out_of_time_message(trigger, timeout))
            return remaining

    def _capture_busy_message(self, lock: Path, timeout: float) -> str:
        try:
            age = time.time() - lock.stat().st_mtime
            contention = (
                f"another session is capturing and holds {lock}, {age:.0f}s old"
            )
        except OSError:
            # No lock to describe. Either the holder released between the loop
            # giving up and this message being built, or this call had just
            # broken a stale one - and naming a holder that is demonstrably not
            # there is a guess of exactly the kind `_busy_message` refuses to
            # make about the trigger.
            contention = f"the capture lock at {lock} was contended for"

        return (
            f"{contention}, and the {timeout:g}s timeout ran out waiting for it. "
            "Captures are serialised because Terraria names the file it writes "
            "after the second it wrote it in, so two at once produce one picture "
            "and two callers told it is theirs. Retry with a longer timeout."
        )

    def _capture_out_of_time_message(self, timeout: float, waited: float) -> str:
        """Why a capture lock that WAS taken still has to fail.

        Deliberately not `_claimed_out_of_time_message`. That one describes a
        request the caller's own claim put on the trigger, which the game will
        most likely still serve - so it says "the game may still answer it"
        and leaves the file alone. Here nothing was ever asked: the budget
        went on the lock and the previous capture's second, the trigger was
        never written, and the lock is released on the way out. Reusing that
        message would promise an answer to a request that does not exist.
        """
        boundary = (
            f", {waited:.2f}s of it waiting out the previous capture's second"
            if waited
            else ""
        )
        return (
            f"the {timeout:g}s timeout went on taking the capture lock{boundary}, "
            "leaving nothing to ask the game with. NOTHING OF THIS REQUEST IS ON "
            "DISK: no trigger was written, no picture was taken, and the capture "
            "lock has been released. Retry with a longer timeout - a capture needs "
            "one big enough for the lock, up to a second of boundary wait, and the "
            "round trip after that."
        )

    def _claim_capture(
        self, lock: Path, stamp: Path, *, timeout: float
    ) -> tuple[float, str | None]:
        """Take the capture lock, wait out the previous capture's second, and
        return what is LEFT of `timeout` and a note for the caller.

        TAKEN BEFORE THE TRIGGER, always, and that ordering is the whole
        reason two locks are safe: a session waiting here is by construction
        not holding the trigger, so whoever does hold the trigger always
        finishes. Claiming them the other way round would let A hold the
        trigger while waiting for the lock B holds while waiting for the
        trigger.

        The stamp wait happens HERE rather than in the caller because it must
        happen under the lock - it is the previous holder's second being
        waited out, and a second claimant arriving mid-wait would otherwise
        skip it.

        RAISES WHEN THE SURVIVING BUDGET IS BELOW `CLAIM_POLL`, the same rule
        `_claim` applies to itself, because the remainder returned here is
        what funds the trigger claim that comes next. This paragraph used to
        argue the opposite - that a lock with no budget left "is released
        moments later by `ask`'s `finally` and leaves nothing behind". Both
        halves were false, and one session with a stamp and no contention at
        all was enough to show it: `ask` sets `held` from this function's
        RETURN, so a raise here reaches no `finally`; and a 0.0 remainder
        leaves plenty behind, because `_claim(timeout=0.0)` writes the
        request to the trigger and only THEN raises for having no time to
        wait on it. The caller got an error, the game got the request anyway,
        and the PNG was written with the lock already released - the
        unserialised capture this whole function exists to prevent.

        The stamp wait is charged AFTER the loop's last deadline check, which
        is why the check has to be repeated here rather than trusted from up
        there.

        THE ASYMMETRY WITH `_claim` IS REAL AND IS ABOUT THE SLOT, not about
        whether to raise. `_claim` leaves the trigger claimed on its way out,
        because a request already on disk is one the game may still serve and
        withdrawing it would be the lost update. This one unlinks the lock,
        because nothing was asked: a lock held for a request that does not
        exist blocks the other session for nothing.

        THE NOTE IS EARNED, NOT INFERRED FROM `SlotBusy`. Only
        `_break_stale_lock` returning an age means THIS call broke a lock; a
        `SlotBusy` that resolves any other way - the lock was never stale, or
        it vanished under a race `_break_stale_lock` recognises as somebody
        else's release - retries silently, same as before Task 8.
        """
        broke: Broken | None = None
        deadline = time.monotonic() + timeout

        # TWO CLOCKS, ON PURPOSE, and they measure different things. This
        # one is monotonic because it bounds THIS call's own waiting, where a
        # wall clock that steps sideways would hand out a wrong remainder.
        # The one written into the lock is wall clock because another process
        # reads it - see `_lock_payload`.
        #
        # It is `timeout` rather than the claim's share of it because the
        # holder's whole `ask` is bounded by one budget: this function returns
        # the remainder, `_claim` takes that, `_await_text` takes what is left.
        # So a lock taken now cannot outlive now + timeout, which is exactly
        # the promise the next session needs.
        expiry = time.time() + timeout
        while True:
            try:
                _claim_atomically(lock, _lock_payload(expiry))
            except SlotBusy:
                removed = _break_stale_lock(lock)
                if removed is not None:
                    broke = removed
                # EVERY cycle checks the deadline, including one that just
                # broke a lock. The break used to `continue` straight past
                # this: a lock somebody kept recreating stale would then loop
                # here for as long as they kept doing it, with the caller's
                # timeout never consulted again.
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TriggerError(
                        self._capture_busy_message(lock, timeout)
                    ) from None
                # No sleep after a break: that branch freed the name itself, so
                # it retries at once rather than sleeping on a lock it knows is
                # no longer there.
                if removed is None:
                    time.sleep(min(CLAIM_POLL, remaining))
                continue
            break

        # THE LOCK IS HELD FROM HERE, and every way out of this block has to
        # give it back. Released HERE rather than by `ask`'s `finally`, which
        # only runs for a lock this function RETURNED.
        #
        # The `except` is not decoration for the `raise` below it - that path
        # could unlink for itself. It is for the exits no statement in here
        # mentions: a KeyboardInterrupt or a signal handler's exception landing
        # inside the boundary sleep, which is where this function spends nearly
        # all of its wall clock. That is the one way the lock outlives its
        # session by DESIGN rather than by crash. A crash takes the process
        # with it and the next session's deadline check tidies up after it;
        # this leaves a live process holding a name it will never come back
        # for, and the caller who pressed Ctrl-C has no idea they now own a
        # file. `BaseException` for exactly that reason: `Exception` does not
        # catch the interrupt this exists for.
        #
        # No stamp on any of these paths, unlike `_release_capture`: nothing
        # reached the trigger, so there is no picture whose second the next
        # capture has to miss.
        try:
            wait = _stamp_wait(stamp, now=time.time())
            if wait:
                time.sleep(wait)

            remaining = max(0.0, deadline - time.monotonic())
            if remaining < CLAIM_POLL:
                # Nothing was written to the trigger, so the caller gets one
                # honest error instead of an error plus a capture nothing is
                # serialising.
                raise TriggerError(self._capture_out_of_time_message(timeout, wait))
        except BaseException:
            lock.unlink(missing_ok=True)
            raise

        # THE NOTE NAMES WHICH BOUND FIRED, because the two are worth very
        # different amounts to whoever reads it. A holder that recorded a
        # deadline and blew through it is a fact about that session; a lock
        # broken on age alone said nothing at all, and the 60s that decided it
        # is a guess about how long a capture can take. A reader chasing a
        # collision needs to know which of those they are looking at.
        note = (
            (
                f"a capture lock {broke.age:.0f}s old was broken to take this "
                "capture: its holder's own deadline had passed"
                if broke.by_deadline
                else f"a capture lock {broke.age:.0f}s old was broken to take "
                f"this capture: it recorded no deadline, so the "
                f"{CAPTURE_LOCK_STALE:g}s age bound decided it"
            )
            if broke is not None
            else None
        )
        return remaining, note

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

        The command is checked against WHAT THIS SIDE PUBLISHED, which is why
        the list is read here rather than passed in. A client and a dedicated
        server run different builds of nothing in particular but do answer
        different commands, and each publishes its own list — so asking the
        server for a client-only command is refused with the server's list
        rather than a client's.
        """
        # ADDRESSED BY DEFAULT, because unaddressed means "whoever polls
        # first". The mod accepts an untargeted request at every client, so
        # with two clients up the answer went to a race - measured as a
        # different client answering on each of two consecutive attempts. This
        # side knows its own player, so leaving that to chance was a choice.
        #
        # NOT for the dedicated server: it has no local player, so any target
        # matches nothing and it would fall silent for good.
        #
        # A client that has not yet loaded a character has an EMPTY name, so
        # it matches no target either and cannot be asked anything through
        # this path. `heartbeat` is how that client is reached instead - it
        # reads off disk and needs no cooperation from the game.
        if not server and target is None:
            target = self.player

        payload = compose(
            command,
            target=target,
            argument=argument,
            commands=self.commands(server=server),
        )

        trigger = self.path(self.cfg.artifacts.trigger, server=server)
        result = self.path(self._names(server, target).result, server=server)

        result.unlink(missing_ok=True)

        # SERIALISED, because Terraria names the file it writes after the
        # second it wrote it in and nothing downstream can undo that. Two
        # captures inside one second produce ONE picture and two callers each
        # told it is theirs - watched happen, live, with two clients.
        #
        # The lock comes BEFORE the trigger claim and that order is load
        # bearing: a session waiting here holds no trigger, so the trigger's
        # holder always finishes.
        # Client side only: `CaptureNow` refuses on a dedicated server, and a
        # server-side lock would be a DIFFERENT file (`-server` suffixed) that
        # serialises against nothing.
        capturing = _will_capture(payload) and not server
        lock = self.path(self.cfg.artifacts.capture_lock, server=server)
        stamp = self.path(self.cfg.artifacts.capture_stamp, server=server)

        # Set before the `try`: a non-capture request never claims the lock,
        # so nothing downstream ever assigns this, and `Reply` still needs a
        # value to pass.
        note = None
        held = False
        stuck = None
        try:
            if capturing:
                timeout, note = self._claim_capture(lock, stamp, timeout=timeout)
                held = True

            remaining = self._claim(trigger, payload, timeout=timeout)
            text = self._await_text(
                result, timeout=remaining, what=f"reply to {payload!r}"
            )
        finally:
            if held:
                # Stamped even on failure: Terraria may have written a PNG
                # whether or not the reply arrived, and a second nobody
                # recorded is a second the next capture will land in. The
                # stamp-then-unlink order is load bearing - see
                # `_release_capture`, which is where it is stated once rather
                # than being two adjacent lines here that look interchangeable.
                stuck = _release_capture(lock, stamp, when=time.time())

        if stuck is not None:
            # Reported rather than raised, and reported HERE rather than inside
            # the `finally`, where it would have replaced this reply. A caller
            # whose capture worked still needs to know their next one will
            # block, and this is the only thing on the whole path they can do
            # something about.
            failed = (
                f"this capture's lock could not be released ({stuck}) and is "
                f"still at {lock}. Captures from either session block on it "
                "until its recorded deadline passes, or until it is deleted"
            )
            note = f"{note}; {failed}" if note else failed

        return Reply(command=command, text=text.strip(), note=note)

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
        dump = self.path(self._names(server, target).diag, server=server)
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
        drop = self.path(self._names(False, target).shot, server=False)
        drop.unlink(missing_ok=True)

        reply = self.ask("shot", argument=region, target=target, timeout=timeout)
        if not reply.ok:
            raise TriggerError(f"the game refused a shot: {reply.text}")

        self._await_png(drop, timeout=timeout, what="shot PNG")

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

    def _await_png(self, path: Path, *, timeout: float, what: str) -> None:
        """Wait until the file is a WHOLE PNG, not until it exists.

        `shot` used to wait for existence and rename, and never opened the file.
        So whatever landed on that name was promoted into the capture directory
        and its path returned as a picture - and the caller found out one round
        trip later and somewhere else, where the error names their image reader
        rather than the capture that was never taken. The README recorded the
        absent guarantee rather than implying one; this is it arriving.

        Both ends are checked, and only the trailer answers the question that
        matters. A file appears when it is CREATED, so a capture big enough to
        be worth taking is big enough to be read mid-write - and a truncated PNG
        has an entirely valid signature. This is the race `_await_text` was
        written to close, reaching the one artifact nobody was reading.

        Incompleteness is WAITED OUT and a wrong signature is not. A short file
        is a writer still working, and refusing it on sight would turn a slow
        write into a failure. Bytes that are not a PNG at all will never become
        one, so waiting the full timeout on them buys nothing and costs the
        caller a minute before it says the obvious - the mod dropping a refusal
        on this name is exactly how that happens.
        """
        deadline = time.monotonic() + timeout
        self._await_file(path, timeout=timeout, what=what)

        while time.monotonic() < deadline:
            data = path.read_bytes()
            if data.startswith(_PNG_SIGNATURE):
                if data.endswith(_PNG_TRAILER):
                    return
            elif len(data) >= len(_PNG_SIGNATURE):
                raise TriggerError(
                    f"the {what} at {path} is not a PNG - it begins {data[:8]!r}. "
                    "Something wrote to the capture drop box that was not a "
                    "picture, so no capture was taken; the bytes are left there "
                    "to be read rather than renamed into the captures."
                )
            time.sleep(0.2)

        raise TriggerError(
            f"the {what} at {path} was still an incomplete PNG after "
            f"{timeout:.0f}s - it starts like one and has no end. The write "
            "never finished, so promoting it would hand back a truncated "
            "picture that opens as a broken one."
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


def _clear_stale_artifacts(cfg: Config, *, player: str | None) -> None:
    """A heartbeat or reply left by a previous run is what lets a readiness
    check pass against a dead process.

    Clears the unsuffixed names for both sides - `cfg.artifacts` has no
    player, and is the right answer for the dedicated server, which never has
    one - AND this session's own per-player names, when `player` is given. A
    per-player reply from a dead run under the SAME player name would
    otherwise survive into this one, which is the exact scenario the original
    comment describes and Task 2's per-player naming made reachable.

    THE TRIGGER IS EXCLUDED FROM BOTH LOOPS and released separately, because
    it is the one artifact shared with the OTHER session rather than merely
    with a previous run of this one. Deleting it here destroyed a request that
    another developer's game was still polling for - the lost update this
    branch removed from the write path, reintroduced by the cleanup. See
    `_release_trigger`, which deletes it only where it holds a request this
    session may take back. THE CAPTURE LOCK AND STAMP ARE EXCLUDED FOR THE
    SAME REASON - both are shared with the other session, not owned by a
    previous run of this one - plus one of their own: a lock left by a DEAD
    run is told apart from one a LIVE run is holding by its age, not by a
    launch's optimism, which is the only signal that can make that call (see
    Task 4).

    Other players' files are left alone, deliberately: they are not this
    session's to delete. That holds because of WHO reads them next: the
    `heartbeat` tool hands a human an `age_seconds` to judge, so a leftover
    file reads as not-live rather than as a phantom client. It does NOT hold
    for `_wait_ready`, which collapses a file to a single ready/not-ready bit
    and shows nobody its age - a leftover that is merely fresh enough would
    read as ready. `_wait_ready` is not exposed to that risk by leaving files
    alone here; it is protected by scoping its own candidates to the player
    THIS launch was given, so another player's leftover is simply not one of
    the names it looks at, however fresh it is.
    """
    shared = (
        cfg.artifacts.trigger,
        cfg.artifacts.capture_lock,
        cfg.artifacts.capture_stamp,
    )

    for name in cfg.artifacts.all:
        if name in shared:
            continue
        for server in (False, True):
            cfg.artifact(name, server=server).unlink(missing_ok=True)

    if player is not None:
        for name in artifacts_for(cfg.mod_name, player).all:
            # None of `shared` is per player, so this is the same shared names
            # again and the same exclusion applies. Skipped by name rather than
            # by trusting that, because a member of `shared` deciding to carry
            # a token one day must not silently reopen the hole.
            if name in shared:
                continue
            cfg.artifact(name, server=False).unlink(missing_ok=True)

    # The lock is excluded from both loops above because it may belong to a
    # LIVE session - but a launch is exactly when a lock left by a DEAD run
    # should go, and age is the one signal that tells the two apart. Both
    # sides, because either could be the one that crashed.
    for server in (False, True):
        _break_stale_lock(cfg.artifact(cfg.artifacts.capture_lock, server=server))

    _release_trigger(cfg, player=player)


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

    # EVERY request is now addressed, so a name `parse` cannot read back
    # intact fails on every call rather than only on targeted ones. Asked
    # once, here, where the answer names the character instead of arriving as
    # a refusal on a request the caller thought was simple.
    #
    # WHAT THIS ACTUALLY CATCHES: whitespace, not `@` or `:`. `player` is only
    # ever placed in the TARGET position, the payload's terminal field -
    # `parse` takes everything after the first `@` as the target VERBATIM and
    # never re-splits it, so a name holding `@` or `:` reads back unchanged.
    # Measured against both this module's `parse` and the mod's own
    # `DevCommands.Parse` (responder/DevCommands.cs), which use the identical
    # rule - a first hypothesis for this guard checked those two characters
    # directly and was wrong, because neither one actually breaks anything.
    # What `parse` DOES change is whitespace: both sides trim the target, so a
    # name with a leading or trailing space - or one that is empty or
    # whitespace-only - reads back shorter than it was typed, or as no name at
    # all. That is indistinguishable from an ordinary name in a log or a
    # config file, which is exactly why it is worth catching once here rather
    # than as a silent mystery on every request this session ever sends.
    heard = parse(f"diag@{player}")
    if heard is None or heard.target != player:
        raise SessionError(
            f"the character name {player!r} is not the name a request will "
            f"carry: a payload naming it reads back as {heard}, not as typed. "
            "`parse` trims whitespace from the target, so a name padded with "
            "spaces - or one that is empty or all whitespace - is addressed "
            "shorter than it was typed, or to nobody at all, on every request "
            "this session sends."
        )

    existing = _tml_pids(cfg)
    if existing:
        raise SessionError(
            f"tModLoader is already running (pids {sorted(existing)}). Close it, "
            "or stop the previous session - two instances share one save "
            "directory and would consume each other's trigger files."
        )

    # Resolved BEFORE the session is built, so the session records the world it
    # actually loads rather than the argument it was handed.
    world_arg = world or cfg.world_win
    if world_arg is None:
        # This used to default to one developer's self-test world by full path,
        # which on any other machine named a file that did not exist - and the
        # failure surfaced as a readiness timeout, blaming the heartbeat. The
        # worlds on THIS disk are a better answer than a guess about one.
        found = inventory.worlds(cfg.save_dir)
        listing = (
            "\n".join(f"  {w.name}: {w.path_win}" for w in found)
            if found
            else "  (none in this save directory)"
        )
        raise SessionError(
            "no world to load. Pass `world` as a WINDOWS path, or set "
            f"TMODLOADER_WORLD_WIN. Worlds here:\n{listing}"
        )

    problem = world_problem(world_arg)
    if problem:
        raise SessionError(problem)

    session = Session(cfg=cfg, mode=mode, port=port, player=player, world=world_arg)

    # Clear stale artifacts BEFORE launching - see _clear_stale_artifacts.
    _clear_stale_artifacts(cfg, player=player)

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

        _wait_ready(cfg, mode=mode, player=player, timeout=timeout)
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


def _wait_ready(cfg: Config, *, mode: str, player: str, timeout: float) -> None:
    """Block until the heartbeat says a world is live AND is recent.

    Both conditions, because they fail differently: a stale-but-ready heartbeat
    means the process died after loading, and a fresh-but-not-ready one means it
    is still loading. Checking only existence conflates them, which is exactly
    how a harness once sailed past three gates on a killed client's file.

    THE CLIENT HALF IS DISCOVERED, NOT NAMED - but only among the two names
    THIS launch's client can legitimately write. A client's heartbeat is
    `<mod>-hooks.txt` before it has a character and `<mod>-hooks-<token>.txt`
    from the moment one loads - and a world becomes ready at EXACTLY the
    moment a character exists, so a check pinned to the unsuffixed path can end
    up watching the file the mod has just stopped writing. The same failure
    reaches here from the other direction too: `-player <name> -join` can mean
    the client's very first heartbeat is already the per-player one, so the
    unsuffixed file may never exist at all. Either way a fixed path goes stale
    or empty on a game that is running perfectly, and `launch` times out on it.

    NOT a directory-wide `heartbeat.client_files` walk, though: that would also
    accept a DIFFERENT player's leftover heartbeat from a previous session.
    `_clear_stale_artifacts` deliberately does not delete other players'
    files (see its docstring) - so stop player A, launch player B inside
    HEARTBEAT_MAX_AGE of that stop, and A's `<mod>-hooks-<A-token>.txt` is
    still on disk, world-ready and fresh. A glob over every name would read
    it as B's client and return before B's client has loaded at all. So the
    candidate set is fixed to exactly the two names PLAYER could be writing
    under - discovered between those two, not among every name that exists.
    """
    server_hb = cfg.artifact(cfg.artifacts.heartbeat, server=True)
    per_player_name = artifacts_for(cfg.mod_name, player).heartbeat

    def _client_candidates() -> list[Path]:
        return [
            cfg.artifact(cfg.artifacts.heartbeat, server=False),
            cfg.artifact(per_player_name, server=False),
        ]

    def _any_ready(paths: list[Path]) -> bool:
        return any(
            heartbeat_is_live(p) and world_is_ready(p.read_text(errors="replace"))
            for p in paths
        )

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        server_ready = heartbeat_is_live(server_hb) and world_is_ready(
            server_hb.read_text(errors="replace")
        )
        client_ready = mode != "server_client" or _any_ready(_client_candidates())
        if server_ready and client_ready:
            # A short settle after world-ready: the mod refuses commands until a
            # world has been live a few seconds, because serving one at the
            # instant capture first becomes possible crashed the engine once.
            time.sleep(5)
            return
        time.sleep(2)

    missing = [server_hb.name] if not heartbeat_is_live(server_hb) else []

    # A client is "missing" when NO name it could be writing under - suffixed
    # or not - is live. That is a different question from whether one of the
    # names it found is world-ready in time, which is a legitimate still-
    # loading timeout rather than an absence.
    client_missing = False
    if mode == "server_client":
        # `_client_candidates()` always returns both names it computed,
        # whether or not either exists - unlike a glob, which only returns
        # what is actually on disk. "found" here means "exists", so the
        # not-found/went-stale distinction still asks the filesystem.
        found = [p for p in _client_candidates() if p.is_file()]
        live = [p for p in found if heartbeat_is_live(p)]
        client_missing = not live
        if not found:
            # Distinguished from a name that appeared and went stale: "never
            # wrote one" and "wrote one, then died" are different diagnoses,
            # and a check that can find nothing has to say which happened.
            missing.append("no client heartbeat of any name appeared")
        else:
            missing.extend(p.name for p in found if not heartbeat_is_live(p))

    # THE ADVICE HAS TO MATCH THE MODE THAT FAILED.
    #
    # This used to blame Steam unconditionally. In `server` mode there is no
    # client, so that sentence named a cause which could not apply - and it
    # cost a real debugging session: Steam genuinely WAS down, the advice fit
    # the client failure, so the identical server-mode failure was filed under
    # the same cause. Bringing Steam up fixed one and not the other, which is
    # the only reason the wrong attribution surfaced at all.
    if client_missing:
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

    # NOT an unconditional unlink. The trigger is shared with the other
    # session, and a teardown that deleted it took that session's in-flight
    # request with it - see `_release_trigger`.
    _release_trigger(cfg, player=session.player)

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
