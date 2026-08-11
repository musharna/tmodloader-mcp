"""Reading the mod's heartbeat, and saying WHICH KIND OF SILENCE it is.

WHY THIS IS A TOOL AND NOT AN IMPLEMENTATION DETAIL

`session.launch` already reads this file — it is how readiness is decided, and
it has been read here since before there was a parser for it. What it does with
the file is collapse it to one bit and throw the rest away: either a live
heartbeat reporting a live world appeared inside the timeout, or it did not.

That bit is the right thing for `launch` to block on and the wrong thing to
report, because a launch that fails is exactly when the discarded detail is the
answer. The caller gets `no live heartbeat within 300s` — which names the
symptom and none of the four different causes:

    absent                  nothing ever wrote one. The mod is not loaded, is
                            not enabled, or was built without the dev bridge.
    present but stale       the process wrote one and then died.
    live, world not ready   still loading. Wait longer; nothing is wrong.
    live, ready, not armed  loaded and answering, bridge not listening.

Those need four different actions, and they arrived as one sentence about a
timeout. Five minutes to learn that something is slow, when the file on disk
said `armed: False` the whole time.

Deliberately reads OFF DISK rather than through a session. A failed `launch`
raises, so there IS no session at the moment this is worth asking — a tool that
required one could never answer the question it exists for.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import diag
from .triggers import HEARTBEAT_MAX_AGE, PLAYER_TOKEN_GRAMMAR


def client_files(save_dir: Path, mod_prefix: str) -> list[Path]:
    """Every CLIENT heartbeat in this directory, sorted by name.

    Two forms are accepted and both are real:

    - `<mod>-hooks-<token>.txt` — a client with a character
    - `<mod>-hooks.txt` — a client that is up and has not got one yet

    `<mod>-hooks-server.txt` is excluded by construction rather than by a
    special case: `-server` is not a token, because a token ends in four hex
    characters and `server` does not. A looser glob would report the dedicated
    server as a client, which is the diagnosis this tool exists to keep apart.
    """
    pattern = re.compile(
        rf"^{re.escape(mod_prefix)}-hooks(-{PLAYER_TOKEN_GRAMMAR})?\.txt$"
    )
    if not save_dir.is_dir():
        return []
    return sorted(
        (e for e in save_dir.iterdir() if pattern.match(e.name)), key=lambda p: p.name
    )


def player_of(filename: str, mod_prefix: str) -> str | None:
    """The token in a client heartbeat's name, or None for the unsuffixed form.

    Returns the TOKEN, not the character name — the name cannot be recovered,
    the slug is lossy and the hash is one way. Callers that want a name have
    the session's; this is for telling two clients apart on disk.
    """
    stem = filename[len(mod_prefix) + len("-hooks") : -len(".txt")]
    return stem.lstrip("-") or None


@dataclass(frozen=True)
class Heartbeat:
    """One side's heartbeat, with the three questions kept apart.

    `present`, `live` and `world_ready` are separate fields rather than one
    status because they fail independently and each rules out a different fix.
    Collapsing them is what `launch` does internally and what makes its timeout
    message unactionable.
    """

    side: str
    present: bool
    live: bool
    age: float | None
    fields: dict[str, Any]

    @property
    def world_ready(self) -> bool:
        """Whether the CONTENTS claim a world is loaded.

        A bool because `diag.parse` now types it as one. It was a truthy
        `"False"` string until the parser learned this file's shapes.
        """
        return self.fields.get("world-ready") is True

    @property
    def armed(self) -> bool:
        """Whether the mod says its trigger bridge is listening."""
        return self.fields.get("armed") is True


def side_of(fields: dict[str, Any]) -> str:
    """Which side wrote this heartbeat, from the mod's own account of itself.

    `dedServ` rather than the filename. The name is this harness's convention
    and the field is the mod's statement, and where those two disagree the mod
    is right — the same reason `diag.side_of` reads the `side` line instead of
    inferring from netmode.
    """
    dedicated = fields.get("dedServ")
    if dedicated is True:
        return "server"
    if dedicated is False:
        return "client"
    return "unknown"


def read(
    path: Path, *, now: float | None = None, max_age: float = HEARTBEAT_MAX_AGE
) -> Heartbeat:
    """Read one heartbeat file, whether or not it exists.

    An absent file is a Heartbeat with `present=False`, not an exception. "No
    heartbeat" is the single most informative answer this can give — it is the
    difference between a mod that is slow and a mod that was never loaded — so
    it is returned as data rather than raised as a failure to answer.
    """
    if not path.is_file():
        return Heartbeat(side="unknown", present=False, live=False, age=None, fields={})

    try:
        age = (now if now is not None else time.time()) - path.stat().st_mtime
    except OSError:
        # The file existed a moment ago and does not now, or cannot be stat'd.
        # Report it as present-but-unreadable rather than inventing an age: a
        # made-up 0.0 would read as the freshest possible heartbeat.
        return Heartbeat(side="unknown", present=True, live=False, age=None, fields={})

    fields = diag.parse(path.read_text(errors="replace"))
    return Heartbeat(
        side=side_of(fields),
        present=True,
        live=age <= max_age,
        age=age,
        fields=fields,
    )


def diagnose(hb: Heartbeat) -> str:
    """One line naming which silence this is, and what to do about it.

    A convenience over the fields, never a replacement for them — the fields
    ride along in the same result. A summary that was the only output would put
    this module in the business of being believed rather than checked, and the
    whole reason it exists is that something upstream collapsed this file to a
    single bit.
    """
    if not hb.present:
        return (
            "no heartbeat on disk. The mod is not loaded, is not enabled in "
            "this install, or was built without the dev bridge. `build_mod` "
            "and the mod list say which."
        )
    if not hb.fields:
        return "heartbeat present but unreadable."
    if not hb.live:
        age = f"{hb.age:.0f}s" if hb.age is not None else "unknown"
        return (
            f"heartbeat is stale ({age} old). The file survives the process, "
            "so this is a game that ran and is no longer running."
        )
    if not hb.world_ready:
        return (
            "heartbeat is live but no world is loaded — still starting up. "
            "Nothing is wrong yet; wait, or raise the launch timeout."
        )
    if not hb.armed:
        return (
            "world is loaded but the trigger bridge is not armed, so commands "
            "will not be answered."
        )
    return "live, world loaded, bridge armed — this side can answer."
