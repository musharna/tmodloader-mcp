"""Do the command bridge, chat and the tile query actually work?

WHAT NOTHING ELSE COVERS. All three touch Terraria types, so none of them is on
the vendor test project's compile line. The unit tests cover the argument rules;
the template mod proves they COMPILE. Only this proves they do anything.

THE THREE THINGS BEING CHECKED ARE DIFFERENT IN KIND:

  command   runs somebody ELSE'S code - a ModCommand registered by whichever
            mods happen to be loaded - so the check is about dispatch, capture
            and refusal rather than about any particular command's behaviour.
  chat      is a round trip: say something, then hear it. Either half alone
            proves nothing, because a recorder that captured nothing and a
            speaker that said nothing look identical from outside.
  tiles     is the only verb here that can be checked against a fact the game
            already told us - the world's own spawn point is on solid ground,
            so a query there that finds no tiles at all is wrong.

Drives `template/DevBridgeTemplate`, the only mod in this repository that opts
in. Run it with:

    TMODLOADER_MOD_SOURCE="$TMODLOADER_SAVE_DIR/ModSources/DevBridgeTemplate" \
        uv run python tests/live_bridge_check.py

Not collected by pytest (no `test_` prefix). Run it by hand, with no game
already running.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tmodloader_mcp.config import load
from tmodloader_mcp.session import Session, launch, stop
from tmodloader_mcp.triggers import TriggerError

PORT = 7810
PLAYER = "n43n"

#: Said into chat and then listened for. Distinctive so a match cannot be some
#: other line that happened to be printed at the same moment.
PHRASE = "devbridge-live-check-marker"


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


def say(session: Session, command: str, *, server: bool = False) -> str:
    verb, _, argument = command.partition(":")
    reply = session.ask(verb, argument=argument or None, server=server, timeout=60.0)
    return reply.text


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
        # 1. THE VERBS ARE SERVED AT ALL, which is the opt-in having run. The
        #    base class registers none of these.
        served = set(session.commands().names)
        check(
            {"command", "commandlist", "chat", "say", "tiles"} <= served,
            f"the client serves the new verbs (it serves {sorted(served)})",
        )

        # 2. THE COMMAND BRIDGE SEES OTHER MODS' COMMANDS. The template
        #    registers no ModCommands of its own, so anything listed here came
        #    from another mod entirely - which is the point of enumerating
        #    ModLoader.Mods rather than this mod's own content.
        listed = say(session, "commandlist")
        note(f"      commandlist said: {listed[:400]!r}")
        check(
            listed.startswith("OK"),
            f"the bridge can enumerate this install's commands: {listed[:80]!r}",
        )

        names = [
            line.strip().split(" ")[0]
            for line in listed.splitlines()[1:]
            if line.strip()
        ]
        check(
            len(names) > 0,
            f"at least one ModCommand is registered somewhere ({names[:8]})",
        )

        # 3. AN UNKNOWN COMMAND NAMES WHAT EXISTS. A misspelling is the
        #    commonest reason a command does nothing, and the list is right
        #    there in the process being asked.
        unknown = say(session, "command:definitelynotacommandanywhere")
        check(
            unknown.startswith("REFUSED")
            and "definitelynotacommandanywhere" in unknown,
            f"an unknown command is refused by name: {unknown[:140]!r}",
        )
        if names:
            check(
                names[0] in unknown,
                "the refusal lists the commands that DO exist, which is what "
                f"makes it actionable: {unknown[:200]!r}",
            )

        # 4. RUNNING ONE, and capturing what it printed. `help` is tModLoader's
        #    own and prints a lot, which makes it the safest real command to
        #    drive: it changes nothing and its output is unmistakable.
        if any(n.casefold() == "help" for n in names):
            helped = say(session, "command:help")
            note(f"      command:help said: {helped[:200]!r}")
            check(
                helped.startswith("OK") and "printed" in helped,
                f"a real command ran and its output was captured: {helped[:120]!r}",
            )
        else:
            note("      SKIP  no `help` command registered on this install")

        # 5. CHAT, AS A ROUND TRIP. Saying and hearing are checked together
        #    because either alone proves nothing: a recorder that captured
        #    nothing and a speaker that said nothing look identical.
        before = say(session, "chat")
        note(f"      chat before: {before[:120]!r}")
        check(
            before.startswith("OK"),
            f"the client is listening to chat: {before[:120]!r}",
        )

        spoken = say(session, f"say:{PHRASE}")
        check(spoken.startswith("OK"), f"the client said something: {spoken!r}")

        # A multiplayer client's line goes to the server and comes back, so it
        # is not in the log the instant the reply arrives.
        heard = ""
        for _ in range(10):
            heard = say(session, "chat")
            if PHRASE in heard:
                break
            time.sleep(1.0)

        check(
            PHRASE in heard,
            f"the line came back and was recorded: {heard[-200:]!r}",
        )

        # 6. THE SIDE RULE FOR CHAT. A dedicated server draws none, and
        #    answering "no chat" there would be a confident lie rather than a
        #    refusal.
        server_chat = say(session, "chat", server=True)
        check(
            server_chat.startswith("REFUSED") and "client" in server_chat.lower(),
            f"a dedicated server refuses to read chat: {server_chat[:140]!r}",
        )

        # 7. TILES, against a fact the game already told us. The world's spawn
        #    point is on solid ground, so a query there that finds nothing is
        #    wrong rather than merely empty.
        spawn = session.diag(timeout=60.0).fields.get("spawn")
        note(f"      world spawn: {spawn!r}")
        sx, sy = (int(part) for part in str(spawn).split(","))

        around = say(session, f"tiles:{sx - 8},{sy},16,16", server=True)
        note(f"      tiles said: {around[:300]!r}")
        check(
            around.startswith("OK") and "distinct type" in around,
            f"the tile query answered: {around[:120]!r}",
        )
        check(
            "id=" in around,
            "the ground under the world spawn holds at least one tile type - "
            f"finding none there means the query is not reading the world: {around[:200]!r}",
        )

        # 8. ITS REFUSALS, which are the half a unit test cannot prove is wired.
        for argument, expect, why in (
            ("10,20,4", "rectangle", "a three-part argument is refused"),
            ("10,20,0,5", "no tiles", "a rectangle with no area is refused"),
            ("0,0,1000,1000", "limit", "an area past the cap is refused, cap named"),
        ):
            refused = say(session, f"tiles:{argument}", server=True)
            check(
                refused.startswith("REFUSED") and expect in refused,
                f"{why}: {refused[:140]!r}",
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
