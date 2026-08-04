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

import time
from dataclasses import dataclass
from pathlib import Path

TRIGGER_NAME = "biomancy-capture.trigger"
RESULT_NAME = "biomancy-capture.txt"
DIAG_NAME = "biomancy-diag.txt"
HEARTBEAT_NAME = "biomancy-hooks.txt"
SHOT_NAME = "biomancy-shot.png"

#: Commands the mod's DevCapture understands. Listed so an unknown one is
#: refused HERE, with the valid set, rather than written to disk and silently
#: ignored by a game that has no arm for it — which reads as a hang.
COMMANDS = frozenset(
    {
        "capture",
        "diag",
        "mutate",
        "vat",
        "creature",
        "kill",
        "strains",
        "seed",
        "creep",
        "place",
        "killcreep",
        "shot",
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


def compose(
    command: str, target: str | None = None, argument: str | None = None
) -> str:
    """Build a trigger payload: `cmd`, `cmd:arg`, `cmd@who`, `cmd:arg@who`.

    Validated here so a typo is refused before it reaches a game that would
    simply not recognise it. `DevCommands.Parse` treats an unknown word as
    Unknown rather than falling back to a capture, deliberately — but it does so
    silently from our side, and a silent no-op is indistinguishable from a hang.
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

    payload = command
    if argument is not None:
        payload = f"{payload}:{argument}"
    if target is not None:
        payload = f"{payload}@{target}"

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
