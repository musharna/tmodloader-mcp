"""The trigger-file protocol: ask a running game something, wait for its answer.

HOW IT WORKS, AND WHY IT IS A FILE

The game is asked by WRITING A FILE it polls, not by sending it input. No
synthetic keystrokes, no window focus, and — the reason it exists — nothing that
can be fooled by another window sitting on top of the game. OS-level capture was
tried first and returned a picture of Discord.

Both sides of a session share one save directory, so the server's artifacts are
suffixed. A request may also be ADDRESSED (`diag@n43n`), because two clients on
one machine share a trigger file and would otherwise race for it — whichever
polled first would consume a request meant for the other and answer as the wrong
player.

The waiting is here rather than in each caller because getting it wrong is easy
and quiet: the shell version of this loop was hand-written per harness, and the
mistakes were a `pkill` pattern that matched its own command line and a poll
that read a heartbeat left behind by a process that had already been killed.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

#: A mod name that can be a filename prefix. Letters and digits only, which is
#: what tModLoader internal names are: the prefix lands in filenames and inside
#: the capture-matching pattern, so a separator would build a path instead of a
#: name and a dot would widen the pattern.
MOD_NAME = re.compile(r"^[A-Za-z0-9]+$")


@dataclass(frozen=True)
class Artifacts:
    """The five filenames this harness and the mod have to agree on.

    These were constants spelling `biomancy-`, which is how a tool for one mod
    stays a tool for one mod. They are DERIVED from the mod's internal name
    instead, and the rule was chosen so the derivation reproduces Biomancy's
    existing names exactly: tModLoader's internal name is the source folder
    name, and lowercasing it gives `biomancy-`.

    That mattered more than elegance. These names are a contract with C# that
    is already running; a prettier scheme would have renamed files the mod
    still writes, and the harness would have waited on a trigger nobody reads.
    """

    prefix: str

    @property
    def trigger(self) -> str:
        return f"{self.prefix}-capture.trigger"

    @property
    def result(self) -> str:
        return f"{self.prefix}-capture.txt"

    @property
    def diag(self) -> str:
        return f"{self.prefix}-diag.txt"

    @property
    def heartbeat(self) -> str:
        return f"{self.prefix}-hooks.txt"

    @property
    def shot(self) -> str:
        return f"{self.prefix}-shot.png"

    @property
    def all(self) -> tuple[str, ...]:
        """Every artifact, for the callers that clear them between runs."""
        return (self.trigger, self.result, self.diag, self.heartbeat, self.shot)


def artifacts_for(mod_name: str) -> Artifacts:
    """The artifact names one mod writes and this harness reads."""
    return Artifacts(prefix=mod_name.lower())


#: Commands the mod's DevCapture understands, and whether it READS an argument.
#: Listed so an unknown one is refused HERE, with the valid set, rather than
#: written to disk and silently ignored by a game that has no arm for it —
#: which reads as a hang.
#:
#: The flag is the second half of that same guarantee, and it is one command
#: wide: `request.Argument` is consumed in exactly one place in the whole mod,
#: `DevCapture.cs:386`, where `TakeShot` names a region. `DevCommands.Parse`
#: hands `seed` an argument as well, and `SeedWherePlayerStands()` then takes
#: none and hardcodes Zombie/Bloom — so an argument given to anything but `shot`
#: is parsed, dropped, and answered with success. It is recorded per command
#: rather than as a set of exceptions so that adding a command means saying
#: which it is, instead of getting an answer by default.
COMMANDS: Mapping[str, bool] = MappingProxyType(
    {
        "capture": False,
        "diag": False,
        "mutate": False,
        "vat": False,
        "creature": False,
        "kill": False,
        "strains": False,
        "seed": False,
        "creep": False,
        "place": False,
        "killcreep": False,
        "shot": True,
    }
)

#: How stale a heartbeat may be and still count as a live game, in seconds.
#: A heartbeat file outlives the process that wrote it, so existence alone
#: cannot distinguish "running" from "was running" — a harness once sailed past
#: three readiness checks on a killed client's leftover file and then failed
#: with a timeout that named the wrong thing.
HEARTBEAT_MAX_AGE = 45.0


class TriggerError(RuntimeError):
    """The request could not be made, or its answer never came."""


@dataclass(frozen=True)
class Reply:
    """What the game said, and whether it counts as success.

    `ok` is decided by the mod's own reporting convention: a reply beginning
    `ERROR` or `REFUSED` is a failure it is telling us about deliberately, and
    treating either as success is how a refused placement reads as a placement.
    """

    command: str
    text: str

    @property
    def ok(self) -> bool:
        return not self.text.startswith(("ERROR", "REFUSED", "IGNORED"))

    @property
    def refused(self) -> bool:
        """Distinguished from an error: the mod understood and said no."""
        return self.text.startswith("REFUSED")


@dataclass(frozen=True)
class Request:
    """A payload as the GAME will understand it, rather than as it was meant."""

    command: str
    target: str | None = None
    argument: str | None = None


def parse(payload: str) -> Request | None:
    """Read a payload back the way `DevCommands.Parse` will, or None for one it
    cannot parse — which the mod calls Unknown and answers by doing nothing.

    A MODEL of the mod's parser, kept here so this side can check that what it
    wrote means what it meant. The rules it mirrors, in order: the whole string
    is trimmed; an empty one is Capture, because a bare `touch` of the trigger
    meant that before commands existed; the FIRST `@` splits off the target; the
    FIRST `:` in what remains splits off the argument; both are trimmed again; an
    empty half on either split is Unknown; the command is matched
    case-insensitively while the target keeps its case, being a player name that
    gets shown back to a human.

    Being a model is the limit worth stating plainly: this catches a payload
    this module would read back differently from how it built it. It cannot
    catch the mod changing its grammar. Nothing on this side can, short of
    asking a running game — which is the cost that composing carefully exists to
    avoid paying.
    """
    text = payload.strip()
    if not text:
        return Request("capture")

    target = None
    at = text.find("@")
    if at >= 0:
        target = text[at + 1 :].strip()
        text = text[:at].strip()
        if not target or not text:
            return None

    argument = None
    colon = text.find(":")
    if colon >= 0:
        argument = text[colon + 1 :].strip()
        text = text[:colon].strip()
        if not argument or not text:
            return None

    command = text.lower()
    if command not in COMMANDS:
        return None

    return Request(command, target, argument)


def compose(
    command: str, target: str | None = None, argument: str | None = None
) -> str:
    """Build a trigger payload: `cmd`, `cmd:arg`, `cmd@who`, `cmd:arg@who`.

    Validated here so a typo is refused before it reaches a game that would
    simply not recognise it. `DevCommands.Parse` treats an unknown word as
    Unknown rather than falling back to a capture, deliberately — but it does so
    silently from our side, and a silent no-op is indistinguishable from a hang.

    THE COMMAND WORD WAS THE ONLY PART EVER CHECKED. Everything after it was
    pasted between delimiters and trusted, which left the two ways of being
    misheard that the check above exists to prevent:

    An argument nothing will read. `shot` is the only command whose argument the
    mod consumes, and the other eleven answer with success while dropping it —
    see the note on COMMANDS.

    An argument that reparses. `Parse` splits on the FIRST `@`, and the target
    is appended LAST, so an `@` inside an argument steals it: `shot:top@left`
    is heard as a `shot` addressed to `left` naming no region at all. Addressed
    to nobody, it is answered by nobody, and a request no client claims leaves
    no reply file — the same observation as a hung game, which is the failure
    this whole module is arranged around not producing.

    The second is checked by READING THE PAYLOAD BACK, not by refusing the
    characters that happen to break it today. A blacklist would have to reject
    `@` while permitting `:` (which survives, the mod splitting on the first one
    and keeping the rest) and would never think to reject a leading space
    (which breaks nothing syntactically and still changes what the game does,
    because `Parse` trims every field). The grammar knows all three; a list of
    bad characters knows whichever ones have bitten so far.
    """
    if command not in COMMANDS:
        raise TriggerError(
            f"unknown command {command!r} - expected one of {sorted(COMMANDS)}"
        )

    if argument is not None and not argument:
        # "shot:" names no region, and the mod treats that as an error rather
        # than defaulting — defaulting would capture a wider picture than asked.
        raise TriggerError(f"{command!r} was given an empty argument")

    if target is not None and not target:
        raise TriggerError(f"{command!r} was addressed to nobody")

    if argument is not None and not COMMANDS[command]:
        raise TriggerError(
            f"{command!r} takes no argument, so {argument!r} would be parsed and "
            f"then dropped, and the game would report success having never read "
            f"it - only {sorted(c for c, takes in COMMANDS.items() if takes)} "
            f"does anything with one"
        )

    payload = command
    if argument is not None:
        payload = f"{payload}:{argument}"
    if target is not None:
        payload = f"{payload}@{target}"

    meant = Request(command, target, argument)
    heard = parse(payload)
    if heard != meant:
        raise TriggerError(
            f"{payload!r} would not be read back as it was meant: the game hears "
            f"{heard}, not {meant}. Delimiters (`:` before an argument, `@` before "
            f"a target) and surrounding whitespace are the parts that cannot "
            f"survive being inside a field"
        )

    return payload


def heartbeat_is_live(
    path: Path, *, now: float | None = None, max_age: float = HEARTBEAT_MAX_AGE
) -> bool:
    """Whether a heartbeat file was written recently enough to trust.

    Existence is not enough. The file survives the process, so a stale one reads
    exactly like a live one to any check that only asks whether it is there.
    """
    if not path.is_file():
        return False

    stamp = now if now is not None else time.time()
    try:
        return (stamp - path.stat().st_mtime) <= max_age
    except OSError:
        return False


def world_is_ready(text: str) -> bool:
    """Whether a heartbeat's CONTENTS say a world is loaded.

    Separate from freshness because they fail differently: a stale-but-ready
    heartbeat means the game died, and a fresh-but-not-ready one means it is
    still loading. Collapsing them into one boolean loses which.
    """
    for line in text.replace("\r", "").split("\n"):
        if line.strip().lower().startswith("world-ready:"):
            return line.split(":", 1)[1].strip().lower() == "true"
    return False
