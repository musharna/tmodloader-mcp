"""Two dedicated servers racing for ONE trigger - the last unobserved scenario.

A server is addressed by its port and answers under per-port names, and every
half of that mechanism was checked with one server and a hand-written trigger
(live_server_address_check.py). What one server cannot show is the race: two
servers polling the SAME trigger file, where the wrong one may claim a request
first and must put it back untouched for the right one to find. This runs the
race for real.

Pair A comes up through the harness. Pair B is spawned BY HAND with the same
command lines the harness uses, because `launch` rightly refuses while any
tModLoader pid exists - the refusal is correct, so this check goes around it
rather than weakening it. Both servers load the same mod from the shared Mods
directory, so both poll the same `-server` trigger. Each round writes one
request addressed to one port and requires three things at once: the addressed
server answered into ITS per-port dump, the other server's dump stayed absent,
and the trigger was consumed. A wrong-server claim that was put back correctly
still passes - that is the protocol working; only a lost or misrouted request
fails.

Run it with the template mod's source and a SECOND world's name on argv - a
world pair A is not using, listed by `inventory`:

    TMODLOADER_MOD_SOURCE="$TMODLOADER_SAVE_DIR/ModSources/DevBridgeTemplate" \
        uv run python tests/live_race_check.py Long_Nooch

Not collected by pytest (no `test_` prefix), same as the other live checks. Run
it by hand, with no game already running. First observed passing 6/6 on
2026-08-18.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tmodloader_mcp import inventory
from tmodloader_mcp.config import load
from tmodloader_mcp.session import _tml_pids, launch, stop
from tmodloader_mcp.triggers import artifacts_for_server

PORT_A, PORT_B = 7810, 7811
PLAYER_A, PLAYER_B = "n43n", "tst2"
ROUNDS = [PORT_A, PORT_B, PORT_B, PORT_A, PORT_A, PORT_B]


def note(line: str) -> None:
    print(line, flush=True)


def second_world(cfg, wanted: str) -> str:
    """The Windows path of the world named on argv, from the real directory."""
    found = inventory.worlds(cfg.save_dir)
    for world in found:
        if world.name.casefold() == wanted.casefold() and world.path_win:
            return world.path_win
    names = ", ".join(w.name for w in found) or "(none)"
    raise SystemExit(f"no world called {wanted!r} here. Worlds: {names}")


def main() -> int:
    if len(sys.argv) != 2:
        note("usage: live_race_check.py <second-world-name>")
        return 2

    cfg = load()
    world_b = second_world(cfg, sys.argv[1])
    names_a = artifacts_for_server(cfg.mod_name, PORT_A)
    names_b = artifacts_for_server(cfg.mod_name, PORT_B)

    note(f"launching pair A on port {PORT_A} (harness)")
    session = launch(cfg, "server_client", port=PORT_A, player=PLAYER_A)
    note(f"A up: pids {sorted(session.started)}")

    failures: list[str] = []
    try:
        spawn = {
            "cwd": str(cfg.tml_dir),
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
        }
        note(f"spawning pair B on port {PORT_B} by hand ({sys.argv[1]})")
        subprocess.Popen(
            [
                str(cfg.dotnet),
                "tModLoader.dll",
                "-server",
                "-world",
                world_b,
                "-players",
                "4",
                "-port",
                str(PORT_B),
                "-noupnp",
                "-lang",
                "en-US",
            ],
            **spawn,
        )
        time.sleep(20)  # let B's server bind before its client dials it
        subprocess.Popen(
            [
                str(cfg.dotnet),
                "tModLoader.dll",
                "-join",
                "127.0.0.1",
                "-port",
                str(PORT_B),
                "-player",
                PLAYER_B,
                "-skipselect",
            ],
            **spawn,
        )

        # B's server only polls once its client is attached - an empty server
        # runs no update hooks. Its per-port heartbeat appearing proves both
        # that it polls and that it read `-port` off its own command line.
        hb_b = cfg.artifact(names_b.heartbeat, server=True)
        hb_b.unlink(missing_ok=True)
        deadline = time.time() + 420
        while time.time() < deadline and not hb_b.is_file():
            time.sleep(3)
        if not hb_b.is_file():
            note("FAIL  server B heartbeat never appeared")
            return 1
        note(f"PASS  server B polls and knows its address ({hb_b.name})")

        trigger = cfg.artifact(cfg.artifacts.trigger, server=True)
        dumps = {
            PORT_A: cfg.artifact(names_a.diag, server=True),
            PORT_B: cfg.artifact(names_b.diag, server=True),
        }

        for i, target in enumerate(ROUNDS, 1):
            want = dumps[target]
            other = dumps[PORT_A if target == PORT_B else PORT_B]
            trigger.unlink(missing_ok=True)
            want.unlink(missing_ok=True)
            other.unlink(missing_ok=True)
            trigger.write_text(f"diag@port{target}")

            end = time.time() + 45
            while time.time() < end and not want.is_file():
                time.sleep(0.5)
            time.sleep(2)  # give a misroute the chance to show itself

            answered = want.is_file()
            misrouted = other.is_file()
            consumed = not trigger.exists()
            ok = answered and not misrouted and consumed
            note(
                f"{'PASS' if ok else 'FAIL'}  round {i}: diag@port{target} -> "
                f"answered={answered} wrong_server_answered={misrouted} "
                f"consumed={consumed}"
            )
            if not ok:
                failures.append(f"round {i} target {target}")
            time.sleep(2)

        note("all rounds pass" if not failures else f"failed: {failures}")
    finally:
        # Pair B is not the session's, so `stop` will not touch it. Everything
        # the harness does not own dies here, by pid, before the session does.
        note("killing pair B (everything the harness does not own)")
        for pid in sorted(set(_tml_pids(cfg)) - set(session.started)):
            done = subprocess.run(
                ["taskkill.exe", "/PID", str(pid), "/F"],
                capture_output=True,
                text=True,
                check=False,
            )
            note(f"  pid {pid}: {(done.stdout or done.stderr).strip()}")
        note("stopping pair A")
        stop(cfg, session)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
