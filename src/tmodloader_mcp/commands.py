"""What the mod says it serves, read from the mod rather than believed.

This module exists to delete a copy. The harness used to hold its own list of
one mod's twelve commands, and its own belief about which of them read an
argument, in a file the mod could not see. Every one of those facts belonged to
running C#, and the two drifted the moment either side changed alone: a mod that
added a command got no support here until somebody edited Python, and one that
removed a command left this side composing triggers nothing would answer.

The mod now writes the list at load. It is the only artifact here that is not a
reply to a request — the others are a channel, and this describes the channel,
which is why it arrives without being asked for. A harness has to know what it
may ask before it can ask anything.

ABSENCE IS AN ANSWER, and a specific one. No list means no responder: either the
mod is not loaded, or it is a build with the dev bridge compiled out. That used
to surface as a readiness timeout, which names the wrong thing entirely — it
reads as a game too slow to start rather than a game that was never going to
answer.

The file is cleared before a launch along with the other artifacts, and that is
load-bearing rather than tidiness. A list left behind by a previous run says
"responder present" about a build that may no longer have one — the same trap
the heartbeat has, where existence outlives the process that wrote it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class CommandsError(RuntimeError):
    """The command list could not be read or made sense of."""


class CommandsMissing(CommandsError):
    """No list on disk: nothing in the game published one.

    Separate from a malformed list because they mean opposite things. Missing is
    "no responder is running"; malformed is "a responder is running and this
    side cannot read what it said", which is a version mismatch and needs a
    human rather than a retry.
    """


@dataclass(frozen=True)
class Command:
    """One verb the mod serves."""

    name: str
    takes_argument: bool
    summary: str = ""


@dataclass(frozen=True)
class CommandSet:
    """The verbs a running mod publishes, in the order it registered them."""

    commands: tuple[Command, ...]

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name.lower() in self._by_name

    def get(self, name: str) -> Command | None:
        return self._by_name.get(name.lower())

    @property
    def _by_name(self) -> dict[str, Command]:
        return {c.name: c for c in self.commands}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.commands)

    @property
    def taking_an_argument(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.commands if c.takes_argument)

    def __len__(self) -> int:
        return len(self.commands)


def parse_published(text: str) -> CommandSet:
    """Read the mod's published list.

    One command per line, tab separated: name, `arg` or `noarg`, then a summary
    that may itself contain tabs and is taken whole. Blank lines and `#` comments
    are skipped.

    A line this cannot read is an ERROR rather than a skip. Skipping would drop a
    command silently and the only symptom would be this side refusing to compose
    a trigger the game would have answered perfectly — a failure that points at
    the wrong half of the system.
    """
    found: list[Command] = []
    seen: set[str] = set()

    for number, raw in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        parts = raw.split("\t")
        if len(parts) < 2:
            raise CommandsError(
                f"line {number} of the published command list is not tab "
                f"separated, so it names no command: {raw!r}"
            )

        name = parts[0].strip().lower()
        flag = parts[1].strip().lower()
        summary = parts[2].strip() if len(parts) > 2 else ""

        if not name:
            raise CommandsError(f"line {number} names an empty command: {raw!r}")

        if flag not in ("arg", "noarg"):
            # Guessing either way is worse than refusing: read as `noarg`, a
            # command that needs an argument becomes uncallable; read as `arg`,
            # one that ignores it starts accepting a word it will drop.
            raise CommandsError(
                f"line {number} says {flag!r} for {name!r}, which is neither "
                f"'arg' nor 'noarg', so whether it reads an argument is unknown"
            )

        if name in seen:
            raise CommandsError(
                f"{name!r} is published twice; the list does not say which "
                f"handler the game will actually run"
            )

        seen.add(name)
        found.append(Command(name=name, takes_argument=flag == "arg", summary=summary))

    return CommandSet(commands=tuple(found))


def read(path: Path) -> CommandSet:
    """Read the list one side published, or say plainly that none exists."""
    if not path.is_file():
        raise CommandsMissing(
            f"no command list at {path}. The mod publishes one when it loads, so "
            f"either no game is running, the mod is not installed in the game "
            f"that is, or it is a build with the dev responder compiled out. "
            f"This is not a timeout - nothing is on its way."
        )

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise CommandsError(f"could not read {path}: {e}") from e

    published = parse_published(text)

    if not published:
        # A responder that serves nothing is a responder that loaded and
        # registered nothing - which is a real state, and a different one from
        # having no responder at all.
        raise CommandsError(
            f"{path} publishes no commands. A responder wrote it, so the mod is "
            f"loaded, but it registered nothing to serve."
        )

    return published
