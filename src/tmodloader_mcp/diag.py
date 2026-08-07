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

#: A counter, recognised by the SHAPE OF ITS VALUE rather than by its name.
#: Parsed to int so a caller can compare rather than string-match, which is
#: where `"10" < "9"` bugs come from.
#:
#: This was a list of known counter NAMES until 2026-08-05, and that list drifted
#: the moment the mod renamed one: `creep-drawn` became `creep-converted` plus
#: `creep-census` in 0.8.0, nothing here was told, and both new counters parsed
#: as strings for a whole release. Silent in the worst way — `"0"` is truthy, so
#: a harness control asserting "creep exists" would pass on an empty world.
#:
#: Naming the counters put the burden on this file to keep pace with a mod that
#: gains diag lines faster than it can. The `parse` docstring already refuses
#: that bargain for DROPPING unknown keys; typing by shape refuses it here too.
#:
#: No redundant leading zero: a count is never written `007`, so a zero-padded
#: value is an identifier, and `int()` would silently renumber it.
_COUNTER = re.compile(r"^-?(?:0|[1-9][0-9]*)$")

#: Fields that stay text however numeric they look, because they NAME something
#: rather than count it. `int()` on an identifier is lossy and irreversible: an
#: all-decimal md5 would stop matching the file it names, and a player called
#: "43" would stop matching their own character.
#:
#: This list can drift too — but forgetting an identifier is LOUD (the value
#: changes shape, comparisons against the real string fail) where forgetting a
#: counter was silent. The residual failure mode is the visible one.
_TEXT_FIELDS = frozenset(
    {
        "version",
        "tmod-md5",
        "player",
        "strain-readout",
        "directive",
        "shot-path",
        "world-path",
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

        if key not in _TEXT_FIELDS and _COUNTER.match(value):
            out[key] = int(value)
            continue

        # Anything else stays exactly as written. A counter reading `unavailable`
        # is a real signal, not something to coerce to 0 — that would read as
        # "nothing happened" — and a composite like `creep-residue` carries its
        # meaning in the whole string.
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
