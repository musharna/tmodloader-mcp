"""Do the world-changing verbs actually change the world?

WHAT NOTHING ELSE COVERS. `DevMutationArgs` is unit-tested by running it, and
every argument rule and side rule in this feature lives there. `DevMutations`
is the other half - the file that calls `Main.StopRain()`, `NPC.NewNPC`,
`QuickSpawnItem`, `Teleport` and `NetMessage.SendData` - and it imports
Terraria, so it is on no test project's compile line. The template mod proves
it COMPILES. Only this proves it does anything.

THE PROOF IS A DIAG, NOT A REPLY. Every verb here reports "OK: ..." on its own
authority, which is exactly the evidence that cannot be trusted: a handler that
resolved its argument perfectly and then wrote to the wrong field would report
the same sentence. So each check reads the state back out of the game
afterwards, and where multiplayer is involved it reads it from the side that
did NOT make the change.

THE REFUSALS ARE HALF THE POINT and are checked the same way. A verb refused on
the wrong side must ALSO be shown working on the right one inside the same
check - otherwise a responder that refused everything would pass every negative
here and look correct.

Drives `template/DevBridgeTemplate`, not Biomancy, because that mod is the only
one in this repository that opts in. Run it with:

    TMODLOADER_MOD_SOURCE="$TMODLOADER_SAVE_DIR/ModSources/DevBridgeTemplate" \
        uv run python tests/live_mutations_check.py

Not collected by pytest (no `test_` prefix), same as the other live checks. Run
it by hand, with no game already running.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tmodloader_mcp.config import load
from tmodloader_mcp.session import Session, launch, stop
from tmodloader_mcp.triggers import TriggerError

PORT = 7810
PLAYER = "n43n"

#: A blue slime and a torch: two of the lowest ids in their spaces, both
#: harmless, both trivially countable afterwards.
NPC_ID = 1
ITEM_ID = 8
GIVE_STACK = 5
SPAWN_COUNT = 3


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


def refused(session: Session, command: str, *, server: bool) -> str:
    """Send a command that is expected to be refused, and return its text."""
    reply = session.ask(
        command.split(":")[0],
        argument=command.split(":", 1)[1] if ":" in command else None,
        server=server,
        timeout=60.0,
    )
    return reply.text


def main() -> int:
    cfg = load()
    check = Checks()

    if cfg.mod_name.casefold() != "devbridgetemplate":
        note(
            f"this drives the template mod and TMODLOADER_MOD_SOURCE names "
            f"{cfg.mod_name!r}. Point it at ModSources/DevBridgeTemplate - see "
            "this file's docstring."
        )
        return 2

    note(f"launching server_client on port {PORT} as {PLAYER}")
    session = launch(cfg, "server_client", port=PORT, player=PLAYER)
    note(f"up: pids {sorted(session.started)}")

    try:
        # 1. THE OPT-IN IS REAL, and this is the only place it can be observed.
        #    The verbs are in the mod's published command list because the
        #    template's RegisterCommands called RegisterInto - not because
        #    anything in responder/ registered them.
        for side in (False, True):
            names = set(session.commands(server=side).names)
            check(
                {"time", "weather", "spawn", "give", "teleport"} <= names,
                f"{'server' if side else 'client'} publishes all five verbs "
                f"(it serves {sorted(names)})",
            )

        # 2. TIME, on the side that owns the clock - and read back from the
        #    side that does not. That second half is the sync check: a server
        #    that changed its own clock and told nobody would pass a server-side
        #    diag and leave every client an hour behind.
        session.ask("time", argument="midnight", server=True, timeout=60.0)
        after = session.wait_until("day", "==", "false", server=True, timeout=60.0)
        check(after.matched, f"the server's clock says night (last={after.last!r})")

        seen_by_client = session.wait_until("day", "==", "false", timeout=90.0)
        check(
            seen_by_client.matched,
            f"the CLIENT sees the same night, so WorldData was sent "
            f"(last={seen_by_client.last!r})",
        )

        session.ask("time", argument="noon", server=True, timeout=60.0)
        back = session.wait_until("day", "==", "true", server=True, timeout=60.0)
        check(back.matched, "and back to day, so the verb is not one-way")

        # 3. THE SAME VERB ON THE WRONG SIDE. The control is check 2 above: the
        #    identical request to the server worked, so this is about the side.
        text = refused(session, "time:noon", server=False)
        check(
            text.startswith("REFUSED") and "server" in text.lower(),
            f"a client is refused the clock and told where to send it: {text!r}",
        )

        # 4. WEATHER, both directions, read back from the server.
        session.ask("weather", argument="rain", server=True, timeout=60.0)
        wet = session.wait_until("raining", "==", "true", server=True, timeout=60.0)
        check(wet.matched, f"it is raining (last={wet.last!r})")

        session.ask("weather", argument="clear", server=True, timeout=60.0)
        dry = session.wait_until("raining", "==", "false", server=True, timeout=60.0)
        check(dry.matched, "and clear again")

        # 5. SPAWN. Counted rather than trusted, and counted as a DELTA because
        #    a live world spawns and kills NPCs on its own - an absolute count
        #    would be a different number every run.
        before = session.diag(server=True, timeout=60.0).fields.get("npcs")
        note(f"      npcs before: {before!r}")
        session.ask(
            "spawn", argument=f"{NPC_ID},{SPAWN_COUNT}", server=True, timeout=60.0
        )
        grew = session.wait_until(
            "npcs", "changed", server=True, timeout=60.0, poll=1.0
        )
        check(
            grew.matched,
            f"the server's NPC line moved after a spawn (now {grew.last!r})",
        )

        text = refused(session, f"spawn:{NPC_ID}", server=False)
        check(
            text.startswith("REFUSED") and "server" in text.lower(),
            f"a client is refused a spawn: {text!r}",
        )

        # 6. GIVE, on the side that has a player, counted out of the inventory.
        #    QuickSpawnItem drops the item at the player's feet and it is picked
        #    up on a later tick, which is exactly what wait_until is for.
        #
        #    STACKS, NOT SLOTS. The first run of this counted occupied slots and
        #    read 25 before and 25 after a give that had worked: five torches
        #    joined a stack of torches the character was already carrying, which
        #    is the COMMON case for a stackable item rather than an edge one.
        before = session.diag(timeout=60.0)
        held = before.fields.get("inventory-stacks")
        note(
            f"      inventory: {held!r} items in "
            f"{before.fields.get('inventory-slots')!r} slots"
        )
        # The reply is printed whether or not it is needed. Its absence is the
        # only reason the first failure here was ambiguous: an applier that had
        # reported ERROR and one that had reported OK against an unmoved counter
        # were indistinguishable from outside.
        said = session.ask("give", argument=f"{ITEM_ID},{GIVE_STACK}", timeout=60.0)
        note(f"      give said: {said.text!r}")
        got = session.wait_until(
            "inventory-stacks", ">", str(held), timeout=60.0, poll=1.0
        )
        check(
            got.matched,
            f"the item reached the inventory ({held!r} -> {got.last!r})",
        )

        text = refused(session, f"give:{ITEM_ID}", server=True)
        check(
            text.startswith("REFUSED") and "client" in text.lower(),
            f"a dedicated server is refused a give and told to ask a client: {text!r}",
        )

        # 7. TELEPORT, verified by where the character actually is rather than
        #    by the reply. Sent to spawn, which the diag also reports, so the
        #    two lines can be compared without this script knowing the world.
        #
        #    `player-tile` reports the tile UNDER the character - centre
        #    horizontally, feet vertically - so it can be compared to a spawn
        #    point by equality. It reported `position`, the top-left corner of a
        #    three-tile-tall body, on the first run of this: a teleport that
        #    landed exactly read as one that had missed by three tiles.
        spawn = session.diag(timeout=60.0).fields.get("spawn")
        note(f"      world spawn: {spawn!r}")
        said = session.ask("teleport", argument="spawn", timeout=60.0)
        note(f"      teleport said: {said.text!r}")
        moved = session.wait_until(
            "player-tile", "==", str(spawn), timeout=60.0, poll=1.0
        )
        check(
            moved.matched,
            f"the character stands on the spawn tile ({spawn!r}, last={moved.last!r})",
        )

        text = refused(session, "teleport:spawn", server=True)
        check(
            text.startswith("REFUSED") and "client" in text.lower(),
            f"a dedicated server is refused a teleport: {text!r}",
        )

        # 8. THE ARGUMENT RULES, live. Unit tests already pin every one of
        #    these; what they cannot show is that the applier CALLS the resolver
        #    at all. A handler that skipped it would spawn id 0 and report OK.
        for argument, expect, why in (
            ("0", "nothing", "id 0 is refused as Terraria's spelling of nothing"),
            ("1,999", "50", "a count past the cap is refused, and the cap named"),
            ("banana", "whole", "a non-number is refused as what it is"),
        ):
            text = refused(session, f"spawn:{argument}", server=True)
            check(
                text.startswith("REFUSED") and expect in text,
                f"{why}: {text!r}",
            )

        text = refused(session, "time:teatime", server=True)
        check(
            text.startswith("REFUSED") and "noon" in text,
            f"an unknown time names the ones that exist: {text!r}",
        )

    except TriggerError as failed:
        check(False, f"the run died on a trigger error: {failed}")
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
