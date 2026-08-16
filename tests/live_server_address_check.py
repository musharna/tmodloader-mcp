"""Does a dedicated server actually answer to its port?

The whole server-address change is inert unless the mod can read `-port` off
its own command line, and NOTHING in either test suite can reach that. The
Python half tests what the harness composes. The C# half tests
`DevArtifacts.ServerAddress` against a command line handed to it. `DevResponder.cs`
- where the two meet, and the only place `Environment.GetCommandLineArgs()` is
ever called - is on no compile line at all, covered by a source scan that can
see the spelling of a line and not its behaviour. This script is the only thing
that runs it.

ONE SERVER IS ENOUGH, and that is worth saying because the defect is about two.
The negative half - a server must leave a request addressed to a DIFFERENT port
exactly where it is - is observable with one server and a hand-written trigger,
because that IS the mod's rule: a request you are not addressed by is not yours
to consume, and not consuming it is how the intended recipient still finds it.
A second server would need a second world, and there is one.

Not collected by pytest (no `test_` prefix), same as `live_check.py` and
`live_capture_check.py`. Run it by hand, with no game already running.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tmodloader_mcp.config import load
from tmodloader_mcp.session import launch, stop
from tmodloader_mcp.triggers import artifacts_for_server

PORT = 7810
PLAYER = "n43n"
ELSEWHERE = 9999


def note(line: str) -> None:
    print(line, flush=True)


def main() -> int:
    cfg = load()
    names = artifacts_for_server(cfg.mod_name, PORT)
    failures: list[str] = []

    def check(ok: bool, what: str) -> None:
        note(f"{'PASS' if ok else 'FAIL'}  {what}")
        if not ok:
            failures.append(what)

    note(f"launching server_client on port {PORT} as {PLAYER}")
    session = launch(cfg, "server_client", port=PORT, player=PLAYER)
    note(f"up: pids {sorted(session.started)}")

    try:
        # 1. THE HEARTBEAT IS THE FIRST PROOF and it needs no request at all.
        #    A server that could not read its own `-port` writes the unsuffixed
        #    name; one that could writes this. `launch` accepts either, so
        #    getting here proves only that SOMETHING answered - this is what
        #    says which.
        addressed_hb = cfg.artifact(names.heartbeat, server=True)
        plain_hb = cfg.artifact(cfg.artifacts.heartbeat, server=True)
        check(
            addressed_hb.is_file(),
            f"the server writes its heartbeat to {addressed_hb.name} "
            f"(so it read -port off its own command line)",
        )
        if not addressed_hb.is_file() and plain_hb.is_file():
            note(
                f"      it wrote {plain_hb.name} instead - that is the "
                "no-address fallback, so the mod is a copy behind or the "
                "command line has no readable -port"
            )

        # 2. A REQUEST ADDRESSED TO THIS SERVER IS SERVED. `diag(server=True)`
        #    composes `diag@port7810` now; before this change it composed a
        #    bare `diag`, so a pass here is the addressing working end to end
        #    rather than the old untargeted path still being taken.
        dump = cfg.artifact(names.diag, server=True)
        dump.unlink(missing_ok=True)
        parsed = session.diag(server=True, timeout=60.0)
        check(bool(parsed.fields), f"the server answered diag@port{PORT}")
        check(
            dump.is_file(),
            f"the dump landed at {dump.name} (per-port, not the shared name)",
        )
        note(f"      side={parsed.fields.get('side') or parsed.fields}")

        # 3. THE NEGATIVE, which is the whole point of an address. A request
        #    for a DIFFERENT port must be left exactly where it is - both
        #    unconsumed and unanswered. Written straight to the trigger rather
        #    than through `ask`, because `ask` will not compose an address this
        #    session does not own, and that refusal is not what is under test.
        trigger = cfg.artifact(cfg.artifacts.trigger, server=True)
        trigger.unlink(missing_ok=True)
        dump.unlink(missing_ok=True)
        trigger.write_text(f"diag@port{ELSEWHERE}")
        note(f"wrote diag@port{ELSEWHERE} to {trigger.name}; watching 20s")
        time.sleep(20)

        check(
            trigger.is_file(),
            f"a request for port{ELSEWHERE} was NOT consumed by the port{PORT} "
            "server (so the other server would still find it)",
        )
        check(
            not dump.is_file(),
            f"and was NOT answered into {dump.name}",
        )

        # 4. POSITIVE CONTROL for 3, by the same route. Without it, "the
        #    trigger is still there" also passes against a server that has
        #    stopped polling entirely - which is the failure mode this whole
        #    project exists to tell apart from a refusal.
        trigger.unlink(missing_ok=True)
        dump.unlink(missing_ok=True)
        trigger.write_text(f"diag@port{PORT}")
        note(f"control: wrote diag@port{PORT} the same way; watching 20s")
        deadline = time.time() + 20
        while time.time() < deadline and trigger.is_file():
            time.sleep(1)

        check(
            not trigger.exists(),
            f"the same request addressed to port{PORT} WAS consumed "
            "(so the server is polling, and 3 is a refusal not a silence)",
        )
        check(dump.is_file(), f"and WAS answered into {dump.name}")

    finally:
        note("stopping")
        stop(cfg, session)

    note("")
    if failures:
        note(f"FAILED {len(failures)}:")
        for line in failures:
            note(f"  - {line}")
        return 1

    note("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
