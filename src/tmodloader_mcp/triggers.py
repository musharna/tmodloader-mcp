"""The trigger-file protocol: ask a running game something, wait for its answer.

HOW IT WORKS, AND WHY IT IS A FILE

The game is asked by WRITING A FILE it polls, not by sending it input. No
synthetic keystrokes, no window focus, and — the reason it exists — nothing that
can be fooled by another window sitting on top of the game. OS-level capture was
tried first and returned a picture of Discord.

Both sides of a session share one save directory, so the server's artifacts are
suffixed. A request can be ADDRESSED (`diag@n43n`) to name one player among
several clients sharing a trigger file. This harness addresses every request to
its own player by default — see `Session.ask` — so writing an address here is
how one session reaches a client OTHER than its own.

The waiting is here rather than in each caller because getting it wrong is easy
and quiet: the shell version of this loop was hand-written per harness, and the
mistakes were a `pkill` pattern that matched its own command line and a poll
that read a heartbeat left behind by a process that had already been killed.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .commands import CommandSet

#: A mod name that can be a filename prefix. Letters and digits only, which is
#: what tModLoader internal names are: the prefix lands in filenames and inside
#: the capture-matching pattern, so a separator would build a path instead of a
#: name and a dot would widen the pattern.
MOD_NAME = re.compile(r"^[A-Za-z0-9]+$")

#: A slug plus four hex of MD5. Pinned here as a constant because
#: `captures.capture_pattern` has to embed the SAME grammar to stay
#: unambiguous against the three-digit index that follows it, and two
#: hand-written copies of a regex are two regexes.
PLAYER_TOKEN_GRAMMAR = r"[a-z0-9][a-z0-9-]*-[0-9a-f]{4}"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def player_token(name: str | None) -> str | None:
    """A character name as a filename fragment, or None if there is no name.

    Lowercase, runs of non-alphanumerics collapsed to one `-`, trimmed, plus
    four hex of the MD5 of the ORIGINAL bytes.

    The hash is always present rather than only on collision. Adding it only
    when two names clash needs both sides to agree about WHEN, and they cannot
    see each other's players — the mod knows one name, the harness knows one
    name. A rule with one branch is a rule that cannot disagree.

    MD5 is a filename disambiguator, not a security boundary: nothing here
    depends on it being hard to collide deliberately.
    """
    if not name or not name.strip():
        return None
    slug = _NON_ALNUM.sub("-", name.lower()).strip("-")
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()[:4]
    # A name of pure punctuation slugs to "". `player` rather than nothing,
    # because the bare digest does not match PLAYER_TOKEN_GRAMMAR - and a token
    # the grammar rejects is a file `captures` and heartbeat discovery cannot
    # see, which is a silent disappearance rather than an error.
    return f"{slug or 'player'}-{digest}"


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
    #: The client's player token, or None for the dedicated server and for a
    #: client that has not got a character yet. See MOD_CONTRACT.md: an
    #: unsuffixed heartbeat is not a legacy name, it MEANS "up, no character".
    player: str | None = None

    def _named(self, stem: str, ext: str) -> str:
        """`<prefix>-<stem>[-<player>].<ext>` — token before the extension.

        After, and `biomancy-diag.txt-n43n-003f` stops being a text file to
        anything that reads extensions.
        """
        token = f"-{self.player}" if self.player else ""
        return f"{self.prefix}-{stem}{token}.{ext}"

    @property
    def trigger(self) -> str:
        # NOT per player, deliberately. The trigger is how one client learns a
        # request is aimed at somebody else - `DevResponder` leaves a trigger
        # it is not addressed by exactly where it is, so the intended client
        # finds it on its own next poll. Per-player triggers would delete the
        # one part of multi-client that already worked.
        return f"{self.prefix}-capture.trigger"

    @property
    def result(self) -> str:
        return self._named("capture", "txt")

    @property
    def diag(self) -> str:
        return self._named("diag", "txt")

    @property
    def heartbeat(self) -> str:
        return self._named("hooks", "txt")

    @property
    def shot(self) -> str:
        return self._named("shot", "png")

    @property
    def commands(self) -> str:
        """What the mod says it serves, written at load rather than on request.

        The only one of these that is not a reply. The rest are a channel; this
        describes the channel, which is why the mod writes it unasked — see
        `commands.py` for why reading it replaced believing a copy of it.
        """
        return f"{self.prefix}-commands.txt"

    @property
    def all(self) -> tuple[str, ...]:
        """Every artifact, for the callers that clear them between runs.

        `commands` belongs here for the same reason the heartbeat does, and it
        is not housekeeping: a list left by a previous run would say "responder
        present" about a build that may no longer have one. Cleared first, its
        reappearance proves the mod loaded in THIS run.
        """
        return (
            self.trigger,
            self.result,
            self.diag,
            self.heartbeat,
            self.shot,
            self.commands,
        )


def artifacts_for(mod_name: str, player: str | None = None) -> Artifacts:
    """The artifact names one mod writes and this harness reads.

    `player` is a RAW character name; the token rule is applied here so no
    caller has to remember to apply it, and so a caller cannot pass an
    already-tokenised name and get it tokenised twice.
    """
    return Artifacts(prefix=mod_name.lower(), player=player_token(player))


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
    cannot parse at all — which the mod calls malformed.

    A MODEL of the mod's parser, kept here so this side can check that what it
    wrote means what it meant. The rules it mirrors, in order: the whole string
    is trimmed; an empty one is `capture`, because a bare `touch` of the trigger
    meant that before commands existed; the FIRST `@` splits off the target; the
    FIRST `:` in what remains splits off the argument; both are trimmed again; an
    empty half on either split is malformed; the command word is lowercased while
    the target keeps its case, being a player name shown back to a human.

    IT NO LONGER JUDGES WHETHER THE COMMAND EXISTS, and that is a correction
    rather than a relaxation. It used to return None for a word outside a
    hardcoded list, which stopped matching the mod the day the mod's own parser
    stopped deciding that too: a registry now answers it at dispatch, so `Parse`
    hands back the word as written and an unserved verb is refused with a
    message naming what IS served. A model that still rejected here would report
    "unparseable" for a payload the game parses perfectly and simply declines —
    two different answers, and the second is the one a caller can act on.
    Validity is asked of the published set, in `compose`.

    Being a model is the limit worth stating plainly: this catches a payload
    this module would read back differently from how it built it. It cannot
    catch the mod changing its GRAMMAR — only its vocabulary is published.
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

    return Request(text.lower(), target, argument)


def compose(
    command: str,
    target: str | None = None,
    argument: str | None = None,
    *,
    commands: CommandSet,
) -> str:
    """Build a trigger payload: `cmd`, `cmd:arg`, `cmd@who`, `cmd:arg@who`.

    Validated here so a typo is refused before it reaches a game that would
    simply not recognise it. The mod refuses an unserved verb rather than
    falling back to a capture, deliberately — but a refusal costs a round trip
    and only arrives if a game is running to give it.

    `commands` IS THE MOD'S OWN LIST, and is required rather than defaulted.
    This function used to check the word against a hardcoded twelve kept in this
    file, which made this side's idea of the mod a thing that could be wrong: a
    command the mod added was refused here as unknown, and one it removed was
    composed here and answered by nobody. There is deliberately no fallback
    list. A guess about which commands exist is exactly what this replaces, and
    a fallback would be that guess wearing a different name — when no list has
    been published, the honest answer is that no responder is running, which
    `commands.read` says in those words.

    THE COMMAND WORD WAS THE ONLY PART EVER CHECKED. Everything after it was
    pasted between delimiters and trusted, which left the two ways of being
    misheard that the checks below exist to prevent:

    An argument nothing will read. The mod parses one for every command and only
    some read it, answering with success having dropped the rest — so which is
    which is published per command, and it is asked here rather than assumed.

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
    known = commands.get(command)
    if known is None:
        raise TriggerError(
            f"the running mod does not serve {command!r} - it serves "
            f"{list(commands.names)}"
        )

    if argument is not None and not argument:
        # "shot:" names no region, and the mod treats that as an error rather
        # than defaulting — defaulting would capture a wider picture than asked.
        raise TriggerError(f"{command!r} was given an empty argument")

    if target is not None and not target:
        raise TriggerError(f"{command!r} was addressed to nobody")

    if argument is not None and not known.takes_argument:
        raise TriggerError(
            f"{command!r} takes no argument, so {argument!r} would be refused by "
            f"the mod rather than acted on - of what it serves, only "
            f"{list(commands.taking_an_argument)} reads one"
        )

    if argument is None and known.takes_argument:
        raise TriggerError(
            f"{command!r} needs an argument, as {command}:<argument>"
            + (f" - {known.summary}" if known.summary else "")
        )

    # The mod's own spelling, not the caller's. Resolution is case-insensitive
    # on both sides, so `DIAG` names a real command — but the round trip below
    # compares against what the game HEARS, which is lowercased, and a caller's
    # casing would fail that comparison as though the payload were malformed.
    payload = known.name
    if argument is not None:
        payload = f"{payload}:{argument}"
    if target is not None:
        payload = f"{payload}@{target}"

    meant = Request(known.name, target, argument)
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
