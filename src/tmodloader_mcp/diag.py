"""Turn a DevCapture diag dump into structured fields.

WHY THIS IS THE POINT OF THE SERVER

A diag is a flat text file. Driving it from a shell means a `sed` per field,
every time, and every one is a chance to misread. Two real mistakes came from
exactly that: a harness whose control demanded `ambient-motes` be 0 when the
counter is CUMULATIVE and could never return to zero, and a capture that passed
while the thing it photographed had never drawn, because "a file appeared" was
the only thing being checked.

Structured output does not prevent a wrong assertion. It does remove the class
where the assertion was right and the parse was wrong.

Terraria-free and process-free on purpose: everything here is pure text in,
dict out, so it is testable without a game.
"""

from __future__ import annotations

import re
from typing import Any

#: `key: value`, one per line. Values may contain colons (paths do), so the
#: split is on the FIRST colon only.
_LINE = re.compile(r"^(?P<key>[a-z0-9][a-z0-9-]*): ?(?P<value>.*)$")

#: Lines the mod emits as "this side has none", which must not be mistaken for
#: a real value. `NONE` is an empty readout; the N/A forms are a side that is
#: structurally unable to answer, which is different from an empty answer.
_ABSENT = {"NONE", "N/A (never sent to clients)", "N/A (no local player)"}

#: Fields that are counters. Parsed to int so a caller can compare rather than
#: string-match, which is where `"10" < "9"` bugs come from.
_INT_FIELDS = frozenset(
    {
        "netmode",
        "cue-particles",
        "ambient-motes",
        "creep-sources",
        "creep-tiles",
        "creep-drawn",
    }
)


def parse(text: str) -> dict[str, Any]:
    """Parse a diag dump into a dict.

    Unknown keys are kept as strings rather than dropped. The mod gains diag
    lines faster than this server can learn about them, and a parser that
    silently discards what it does not recognise would make a new counter
    invisible to every tool here — which is the same failure as not emitting it.

    Absent markers become None, so `result["strain-readout"] is None` means
    "nothing showing" and cannot be confused with the literal string "NONE".
    """
    out: dict[str, Any] = {}

    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        if not line or line.startswith(" "):
            # Indented lines are continuations of a list section (npcs, items,
            # strains). Those are handled by `sections`, not here.
            continue

        m = _LINE.match(line)
        if m is None:
            continue

        key = m.group("key")
        value = m.group("value").strip()

        if value in _ABSENT:
            out[key] = None
            continue

        if key in _INT_FIELDS:
            try:
                out[key] = int(value)
            except ValueError:
                # A counter that is not a number is a real signal, not something
                # to coerce to 0 — that would read as "nothing happened".
                out[key] = value
            continue

        out[key] = value

    return out


def sections(text: str) -> dict[str, list[str]]:
    """The indented list bodies, keyed by the header line that introduced them.

    `npcs: active=6 mutated=1` followed by indented `idx=...` lines becomes
    `{"npcs": ["idx=...", ...]}`. Kept separate from `parse` because these are
    lists of records rather than scalars, and a caller usually wants one or the
    other, not both flattened together.
    """
    out: dict[str, list[str]] = {}
    current: str | None = None

    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not raw.strip():
            continue

        if raw.startswith(" ") or raw.startswith("\t"):
            if current is not None:
                out.setdefault(current, []).append(raw.strip())
            continue

        m = _LINE.match(raw.rstrip())
        current = m.group("key") if m else None

    return out


def side_of(parsed: dict[str, Any]) -> str:
    """Which side produced this diag: "server", "client" or "singleplayer".

    Read from the `side` line rather than inferred from netmode, because the mod
    writes both and they are the mod's own account of itself. Inferring would
    put this server in the business of duplicating a rule it does not own.
    """
    raw = parsed.get("side")
    if not isinstance(raw, str):
        return "unknown"

    # The line is "singleplayer netmode=0" / "client netmode=1" / "server ...".
    return raw.split()[0] if raw.split() else "unknown"
