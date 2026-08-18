"""Do the write verbs, the detail query and the snapshot bracket work?

WHAT NOTHING ELSE COVERS. The argument rules are unit-tested and the template
mod proves the appliers COMPILE. Only this proves that placing a tile changes
the world, that despawning removes an NPC, that `find` reads health off a live
entity, and that a snapshot taken before all of it puts the world back.

THE SNAPSHOT IS THE OUTER BRACKET, and it is checked rather than trusted:

    hash the world -> snapshot -> run everything -> stop
                   -> RUIN the world file on purpose -> restore -> hash again

The deliberate ruin is the positive control: a restore that copied nothing
would pass without it. It is done by this file rather than by the game because
the first version of this check asserted that the RUN had dirtied the world -
and that assertion failed, correctly. A session ended with `stop()` writes
neither the world nor the character file, because `stop` force-kills and a
killed Terraria saves nothing. Whether the game wrote anything is now REPORTED
rather than asserted, since demanding a change would pin a premise measured
false and demanding no change would pin an accident of force-killing.

Tiles are filled with type 0, which is DIRT. That is deliberate: `spawn` and
`give` refuse id 0 because it means "nothing" in their id spaces, and this is
the live half of the unit test asserting the tile space diverges.

Drives `template/DevBridgeTemplate`. Run it with:

    TMODLOADER_MOD_SOURCE="$TMODLOADER_SAVE_DIR/ModSources/DevBridgeTemplate" \
        uv run python tests/live_round2_check.py

Not collected by pytest (no `test_` prefix). Run it by hand, with no game
already running.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tmodloader_mcp import saves
from tmodloader_mcp.config import load
from tmodloader_mcp.session import Session, launch, stop
from tmodloader_mcp.triggers import TriggerError

PORT = 7810
PLAYER = "n43n"

LABEL = "round2-live"

#: Somewhere empty and out of the way: high above the surface, so a fill there
#: starts from nothing and the count afterwards is unambiguous.
SKY_X, SKY_Y, SKY_W, SKY_H = 2200, 100, 8, 8
SKY_AREA = SKY_W * SKY_H

#: Dirt. Zero on purpose - see the docstring.
DIRT = 0

NPC_ID = 1
SPAWN_COUNT = 6


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


def say(session: Session, command: str, *, server: bool = True) -> str:
    verb, _, argument = command.partition(":")
    reply = session.ask(verb, argument=argument or None, server=server, timeout=60.0)
    return reply.text


def tile_count(session: Session, type_id: int) -> int:
    """How many tiles of one type are in the sky rectangle."""
    said = say(session, f"tiles:{SKY_X},{SKY_Y},{SKY_W},{SKY_H}")
    hit = re.search(rf"id={type_id} count=(\d+)", said)
    return int(hit.group(1)) if hit else 0


def npc_count(session: Session, type_id: int) -> int:
    said = say(session, "entities:npc")
    hit = re.search(rf"id={type_id} count=(\d+)", said)
    return int(hit.group(1)) if hit else 0


def world_digest(cfg) -> str:
    """A fingerprint of the world file, or "" if it is not there."""
    stem = cfg.world_win.replace("\\", "/").rsplit("/", 1)[-1]
    path = cfg.save_dir / "Worlds" / stem
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def main() -> int:
    cfg = load()
    check = Checks()

    if cfg.mod_name.casefold() != "devbridgetemplate":
        note(
            "this drives the template mod and TMODLOADER_MOD_SOURCE names "
            f"{cfg.mod_name!r}. See this file's docstring."
        )
        return 2

    # 1. THE SNAPSHOT, taken with nothing running. This is also the live proof
    #    that `take` works against a real save directory rather than a tmp_path.
    before_digest = world_digest(cfg)
    note(f"world before: {before_digest}")

    held = saves.take(cfg, LABEL)
    check(
        len(held.files) >= 2 and held.size > 0,
        f"a snapshot was taken: {len(held.files)} file(s), {held.size} bytes",
    )

    session = None
    try:
        note(f"launching server_client on port {PORT} as {PLAYER}")
        session = launch(cfg, "server_client", port=PORT, player=PLAYER)
        note(f"up: pids {sorted(session.started)}")

        # 2. THE REFUSAL WHILE RUNNING, which is the property the whole module
        #    rests on and cannot be checked without a real game.
        try:
            saves.take(cfg, "should-not-happen")
            check(False, "a snapshot was taken while the game was running")
        except saves.SaveError as refused:
            check(
                "running" in str(refused),
                f"a snapshot is refused while the game runs: {str(refused)[:120]!r}",
            )

        served = set(session.commands().names)
        check(
            {"settile", "cleartile", "despawn", "find", "players"} <= served,
            f"the new verbs are served (serves {sorted(served)})",
        )

        # 3. PLACING TILES. Counted before and after, in a patch of sky that
        #    starts empty, so the number is the fill rather than the scenery.
        was = tile_count(session, DIRT)
        note(f"      dirt in the sky patch before: {was}")

        placed = say(session, f"settile:{SKY_X},{SKY_Y},{SKY_W},{SKY_H},{DIRT}")
        note(f"      settile said: {placed[:160]!r}")
        check(placed.startswith("OK"), f"the fill ran: {placed[:100]!r}")

        now = tile_count(session, DIRT)
        note(f"      dirt after: {now}")
        check(
            now - was >= SKY_AREA // 2,
            f"the tile query sees the fill: {was} -> {now} of {SKY_AREA} asked for",
        )

        # 4. TYPE 0 WENT IN AT ALL, which is the live half of the unit test
        #    saying the tile id space diverges from the NPC and item ones.
        check(
            now > 0,
            'tile type 0 was placed rather than refused as "nothing" - it is '
            "Dirt here, unlike id 0 for spawn and give",
        )

        # 5. CLEARING THEM AGAIN.
        cleared = say(session, f"cleartile:{SKY_X},{SKY_Y},{SKY_W},{SKY_H}")
        note(f"      cleartile said: {cleared[:160]!r}")
        check(cleared.startswith("OK"), f"the clear ran: {cleared[:100]!r}")

        after = tile_count(session, DIRT)
        check(
            after < now,
            f"the tiles are gone again: {now} -> {after}",
        )

        # 6. DESPAWN, by type. Spawned first so the count is ours.
        say(session, f"spawn:{NPC_ID},{SPAWN_COUNT}")
        spawned = npc_count(session, NPC_ID)
        note(f"      id={NPC_ID} after spawn: {spawned}")
        check(spawned >= SPAWN_COUNT // 2, f"NPCs were spawned to remove ({spawned})")

        removed = say(session, f"despawn:{NPC_ID}")
        note(f"      despawn said: {removed[:160]!r}")
        check(removed.startswith("OK"), f"the despawn ran: {removed[:100]!r}")
        check(
            npc_count(session, NPC_ID) == 0,
            f"every NPC of type {NPC_ID} is gone",
        )

        # 7. DESPAWN ALL SPARES TOWN NPCs. A town NPC is saved with the world
        #    and does not move back in on its own, so sweeping one away while
        #    clearing monsters is damage nobody notices until much later.
        townsfolk = say(session, "find:npc")
        town_ids = [int(m) for m in re.findall(r"id=(\d+)", townsfolk)]
        note(f"      npcs before despawn all: {sorted(set(town_ids))}")

        say(session, f"spawn:{NPC_ID},{SPAWN_COUNT}")
        swept = say(session, "despawn:all")
        note(f"      despawn all said: {swept[:160]!r}")
        check(
            swept.startswith("OK") and "spared" in swept,
            f"despawn all reports what it spared: {swept[:120]!r}",
        )
        check(
            npc_count(session, NPC_ID) == 0,
            "the spawned monsters were swept",
        )

        spared = re.search(r"spared (\d+) town", swept)
        if spared and int(spared.group(1)) > 0:
            left = say(session, "entities:npc")
            check(
                "id=" in left,
                f"a town NPC survived the sweep: {left.splitlines()[0]!r}",
            )
        else:
            note("      SKIP  this world has no town NPC to spare")

        # 8. FIND, which is the whole point of the detail query: state a count
        #    cannot carry.
        say(session, f"spawn:{NPC_ID},{SPAWN_COUNT}")
        found = say(session, f"find:npc,{NPC_ID}")
        note(f"      find said: {found[:300]!r}")
        check(found.startswith("OK"), f"the detail query answered: {found[:100]!r}")
        check(
            "life=" in found and "tile=" in found,
            f"it reports health and position, which `entities` cannot: {found[:200]!r}",
        )

        lives = re.findall(r"life=(\d+)/(\d+)", found)
        check(
            bool(lives) and all(int(mx) > 0 for _, mx in lives),
            f"every reported NPC has a real maximum health: {lives[:4]}",
        )

        # 9. PLAYERS, which `entities` deliberately does not answer.
        who = say(session, "players", server=True)
        note(f"      players said: {who[:200]!r}")
        check(who.startswith("OK"), f"the server lists players: {who[:100]!r}")
        check(
            PLAYER in who,
            f"the character that launched is in the list: {who[:200]!r}",
        )

        # 10. THE REFUSALS, live.
        for command, expect, why in (
            (
                f"settile:{SKY_X},{SKY_Y},1000,1000,{DIRT}",
                "limit",
                "a fill past the cap is refused, cap named",
            ),
            ("settile:10,20,4,5", "type", "a fill with no tile type is refused"),
            ("despawn:0", "nothing", "despawn id 0 is refused"),
            ("despawn:everything", "all", 'a word that is not "all" is refused'),
            ("find:mob", "npc", "an unknown kind is refused by listing the kinds"),
            ("find:npc,0", "nothing", "find id 0 is refused"),
        ):
            refused = say(session, command)
            check(
                refused.startswith("REFUSED") and expect in refused,
                f"{why}: {refused[:130]!r}",
            )

        # 11. THE SIDE RULE. All three writers change what the server owns.
        for verb in ("settile:10,20,1,1,0", "cleartile:10,20,1,1", "despawn:1"):
            client = say(session, verb, server=False)
            check(
                client.startswith("REFUSED") and "server" in client.lower(),
                f"a client is refused {verb.split(':')[0]!r}: {client[:110]!r}",
            )

    except (TriggerError, AssertionError) as failed:
        check(False, f"the run died: {failed}")
    finally:
        if session is not None:
            note("stopping")
            try:
                stop(cfg, session)
            except Exception as leak:  # noqa: BLE001 - reported, not swallowed
                note(f"teardown problem: {leak}")

    # 12. WHETHER THE GAME WROTE ANYTHING, reported rather than asserted.
    #
    #     This started out as an assertion that the run had dirtied the world,
    #     as the positive control for the restore below. It FAILED, and it was
    #     right to: a session ended with `stop()` writes neither the world nor
    #     the character file, because `stop` force-kills and a killed Terraria
    #     saves nothing. Measured twice, once here and once with a probe that
    #     did nothing but `settile` and `give`.
    #
    #     So it is reported as the fact it is. Asserting either way would be
    #     wrong: demanding a change asserts a premise measured false, and
    #     demanding no change would pin an accident of force-killing that no
    #     future `stop` is obliged to keep.
    ran_digest = world_digest(cfg)
    note(f"world after the run: {ran_digest}")
    note(
        "      the game "
        + (
            "WROTE the world (autosave or a graceful exit)"
            if ran_digest != before_digest
            else "did not write the world - stop() force-kills, so nothing saved"
        )
    )

    # 13. THE RESTORE, against a change this test makes ITSELF.
    #
    #     The control has to come from somewhere, and it can no longer come
    #     from the game. Corrupting the world file here is a real change to a
    #     real save directory, which is exactly what restore has to undo - and
    #     unlike the game's behaviour, it is guaranteed to have happened.
    world_path = (
        cfg.save_dir / "Worlds" / cfg.world_win.replace("\\", "/").rsplit("/", 1)[-1]
    )
    world_path.write_bytes(b"deliberately ruined by live_round2_check")
    ruined_digest = world_digest(cfg)
    check(
        ruined_digest != ran_digest,
        f"the world file was deliberately changed first ({ran_digest} -> "
        f"{ruined_digest}), so the restore below has something to undo",
    )

    put = saves.restore(cfg, LABEL)
    note(f"      restored {len(put.files)} file(s), undo={put.undo!r}")

    healed_digest = world_digest(cfg)
    note(f"world after restore: {healed_digest}")
    check(
        healed_digest == before_digest,
        f"the restore put the world back ({ruined_digest} -> {healed_digest}, "
        f"wanted {before_digest})",
    )

    # AND THE UNDO IS REAL: what the restore overwrote is recoverable.
    undone = saves.read(cfg, saves.BEFORE_RESTORE)
    check(
        undone is not None and undone.files,
        f"the overwritten state was kept as {put.undo!r}, so the restore "
        "itself could be undone",
    )

    saves.forget(cfg, LABEL)
    saves.forget(cfg, saves.BEFORE_RESTORE)

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
