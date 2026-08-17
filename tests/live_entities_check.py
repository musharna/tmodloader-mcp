"""Does the entity query actually read the world?

WHAT NOTHING ELSE COVERS. `DevMutationArgs.TryResolveEntityQuery` imports only
System, so the unit tests already decide what an argument may say. The applier
imports Terraria and cannot be on the vendor test project's compile line: the
template mod proves it COMPILES, and only this proves it counts anything.

THE HARD PART IS NOT GETTING A NUMBER BACK, it is knowing the number is real.
A query that returned "0 active" for every kind would pass any check that only
asked whether the reply started with OK. Three properties are checked instead,
none of which a stubbed-out counter could satisfy:

  three arrays   npc, item and projectile report DIFFERENT slot counts, which
                 they cannot if the switch is falling through to one array.
  a delta        NPCs are spawned and the count for that exact type must rise
                 by most of what was spawned. Counted as a delta because a
                 live world spawns and kills things on its own, and by MOST
                 rather than ALL because a slime that spawns can also die.
  a rectangle    the same array, asked twice: once around the tile the game
                 itself said the spawn happened at, once at the far corner of
                 the world. One must find them and the other must not - which
                 a filter that ignored its rectangle could not do.

The uncapped rectangle is checked against the tile query REFUSING the same
rectangle in the same run, because "entities accepted it" only means something
if the cap it is exempt from is demonstrably still there.

Drives `template/DevBridgeTemplate`, the only mod in this repository that opts
in to the mutations this needs. Run it with:

    TMODLOADER_MOD_SOURCE="$TMODLOADER_SAVE_DIR/ModSources/DevBridgeTemplate" \
        uv run python tests/live_entities_check.py

Not collected by pytest (no `test_` prefix). Run it by hand, with no game
already running.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tmodloader_mcp.config import load
from tmodloader_mcp.session import Session, launch, stop
from tmodloader_mcp.triggers import TriggerError

PORT = 7810
PLAYER = "n43n"

#: A blue slime, the same one the mutation check uses: lowest id in its space,
#: harmless, and already known to spawn on this install.
NPC_ID = 1

#: Deliberately far more than the natural spawn rate. The delta is asserted at
#: half of it, so neither a handful of slimes wandering in nor a handful of the
#: spawned ones dying can decide the result.
SPAWN_COUNT = 20
LEAST_DELTA = SPAWN_COUNT // 2

#: Bigger than DevMutationArgs.MaxArea, so `tiles` must refuse it and
#: `entities` must not.
WIDE = "0,0,1000,1000"

_HEADER = re.compile(
    r"looked at (\d+) (\w+) slot\(s\).*?, (\d+) active, (\d+) distinct"
)
_ROW = re.compile(r"id=(\d+) count=(\d+) name=(.*)")


def note(line: str) -> None:
    print(line, flush=True)


class Checks:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def __call__(self, ok: bool, what: str) -> bool:
        note(f"{'PASS' if ok else 'FAIL'}  {what}")
        if not ok:
            self.failures.append(what)
        return ok


class Counted:
    """One `entities` reply, read back apart.

    Raises rather than returning zeroes when the reply does not parse: a
    malformed reply and an empty world are the two things this whole file
    exists to tell apart, and silently reporting the first as the second is
    exactly the false pass it is guarding against.
    """

    def __init__(self, reply: str) -> None:
        self.text = reply
        head = _HEADER.search(reply)
        if head is None:
            raise AssertionError(f"not an entities reply: {reply[:200]!r}")

        self.looked = int(head.group(1))
        self.kind = head.group(2)
        self.active = int(head.group(3))
        self.distinct = int(head.group(4))

        self.counts: dict[int, int] = {}
        self.names: dict[int, str] = {}
        for line in reply.splitlines()[1:]:
            row = _ROW.search(line)
            if row is not None:
                self.counts[int(row.group(1))] = int(row.group(2))
                self.names[int(row.group(1))] = row.group(3).strip()

    def of(self, type_id: int) -> int:
        return self.counts.get(type_id, 0)


def say(session: Session, command: str, *, server: bool = False) -> str:
    verb, _, argument = command.partition(":")
    reply = session.ask(verb, argument=argument or None, server=server, timeout=60.0)
    return reply.text


def count(session: Session, argument: str, *, server: bool = True) -> Counted:
    return Counted(say(session, f"entities:{argument}", server=server))


def main() -> int:
    cfg = load()
    check = Checks()

    if cfg.mod_name.casefold() != "devbridgetemplate":
        note(
            "this drives the template mod and TMODLOADER_MOD_SOURCE names "
            f"{cfg.mod_name!r}. See this file's docstring."
        )
        return 2

    note(f"launching server_client on port {PORT} as {PLAYER}")
    session = launch(cfg, "server_client", port=PORT, player=PLAYER)
    note(f"up: pids {sorted(session.started)}")

    try:
        # 1. THE VERB IS SERVED, which for this one means the BASE class
        #    registered it - the template opts in to three classes, and none of
        #    them is where this lives.
        served = set(session.commands().names)
        check("entities" in served, f"the verb is served (serves {sorted(served)})")

        # 2. THREE KINDS, THREE ARRAYS. The slot counts must differ from each
        #    other: a switch that fell through, or three cases pointing at
        #    Main.npc, would report one number three times and satisfy every
        #    other check in this file.
        npcs = count(session, "npc")
        items = count(session, "item")
        shots = count(session, "projectile")

        note(
            f"      slots: npc={npcs.looked} item={items.looked} "
            f"projectile={shots.looked}"
        )
        check(
            len({npcs.looked, items.looked, shots.looked}) == 3,
            "the three kinds report three different slot counts, so they are "
            f"three arrays: npc={npcs.looked} item={items.looked} "
            f"projectile={shots.looked}",
        )
        check(
            npcs.kind == "npc" and items.kind == "item" and shots.kind == "projectile",
            "each reply names the kind it answered about",
        )

        # 3. BOTH SIDES ANSWER. The design says neither is refused, because a
        #    server owns these arrays and a client holds synced copies.
        on_client = count(session, "npc", server=False)
        check(
            on_client.text.startswith("OK"),
            f"a multiplayer client answers too: {on_client.text[:90]!r}",
        )

        # 4. THE DELTA. Spawned at the world spawn tile, which the reply names
        #    - so the rectangle below is anchored to what the game said rather
        #    than to what this file assumed.
        before = count(session, "npc")
        note(f"      id={NPC_ID} before: {before.of(NPC_ID)}")

        spawned = say(session, f"spawn:{NPC_ID},{SPAWN_COUNT}", server=True)
        note(f"      spawn said: {spawned[:160]!r}")
        check(spawned.startswith("OK"), f"the spawn ran: {spawned[:90]!r}")

        at = re.search(r"at tile (\d+),(\d+)", spawned)
        check(at is not None, f"the spawn named the tile it used: {spawned[:120]!r}")

        after = count(session, "npc")
        grew = after.of(NPC_ID) - before.of(NPC_ID)
        note(
            f"      id={NPC_ID} after: {after.of(NPC_ID)} (delta {grew}), "
            f"name={after.names.get(NPC_ID)!r}"
        )
        check(
            grew >= LEAST_DELTA,
            f"the count for the spawned type rose by {grew}, at least "
            f"{LEAST_DELTA} of the {SPAWN_COUNT} spawned",
        )

        # 5. THE NAME, which is the half of the reply a bare id cannot check.
        #    Not pinned to a string - this asserts the lookup RAN, and prints
        #    what it said so a wrong id space is visible to a reader.
        named = after.names.get(NPC_ID, "")
        check(
            bool(named) and not named.startswith("(no name"),
            f"the type was named rather than left as a bare id: {named!r}",
        )

        # 6. THE RECTANGLE, asked twice of the same array. Around the tile the
        #    spawn reported, and at the far corner of the world.
        if at is not None:
            sx, sy = int(at.group(1)), int(at.group(2))
            near = count(session, f"npc,{max(0, sx - 40)},{max(0, sy - 40)},80,80")
            note(f"      near the spawn tile: {near.text.splitlines()[0]!r}")
            check(
                near.of(NPC_ID) >= LEAST_DELTA,
                f"a rectangle around tile {sx},{sy} finds the spawned NPCs "
                f"({near.of(NPC_ID)} of them)",
            )

            far = count(session, "npc,0,0,1,1")
            check(
                far.active == 0,
                "a single tile at the far corner of the world finds nothing, so "
                f"the rectangle is a filter rather than decoration: {far.active}",
            )

            # POSITIVE CONTROL for the line above: the same query with no
            # rectangle still finds them. Without this, a filter that dropped
            # EVERYTHING would pass the far-corner check.
            check(
                after.of(NPC_ID) >= LEAST_DELTA,
                "and the unfiltered query still finds them, so the empty "
                "rectangle above means the filter worked rather than that the "
                "query broke",
            )

        # 7. THE ASYMMETRY, live and with its control. An entity query takes a
        #    rectangle the tile query refuses, because the tile query pays per
        #    tile and this one does not.
        wide = say(session, f"entities:npc,{WIDE}", server=True)
        check(
            wide.startswith("OK"),
            f"a rectangle past the tile cap is fine for entities: {wide[:90]!r}",
        )

        capped = say(session, f"tiles:{WIDE}", server=True)
        check(
            capped.startswith("REFUSED") and "limit" in capped,
            "and the tile query still refuses that same rectangle, so the "
            f"exemption above is scoped rather than deleted: {capped[:120]!r}",
        )

        # 8. THE REFUSALS, which are the half a unit test cannot prove is wired
        #    to the verb the game actually serves.
        for argument, expect, why in (
            ("mob", "npc", "an unknown kind is refused by listing the kinds"),
            ("npc,10,20", "rectangle", "half a rectangle is refused"),
            ("npc,10,20,0,5", "no tiles", "a rectangle with no area is refused"),
        ):
            refused = say(session, f"entities:{argument}", server=True)
            check(
                refused.startswith("REFUSED") and expect in refused,
                f"{why}: {refused[:140]!r}",
            )

    except (TriggerError, AssertionError) as failed:
        check(False, f"the run died: {failed}")
    finally:
        note("stopping")
        try:
            stop(cfg, session)
        except Exception as leak:  # noqa: BLE001 - reported, not swallowed
            note(f"teardown problem: {leak}")

    note("")
    if check.failures:
        note(f"{len(check.failures)} FAILED:")
        for line in check.failures:
            note(f"  - {line}")
        return 1

    note("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
