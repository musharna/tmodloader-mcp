"""Drive the real thing through the same code paths the MCP tools use."""

import os
import sys
import traceback
from pathlib import Path

from tmodloader_mcp import config, server

# BEFORE ANYTHING LAUNCHES. This script inherits the shell's environment and
# sets none of it, so when the machine-specific defaults were removed it began
# failing on its first call - and the world variable would not have failed
# until `launch`, minutes in, with a game process possibly already spawned.
#
# CI cannot catch this: the live scripts are deliberately not collected. The
# names come from `config.REQUIRED_TO_LAUNCH` so this and its sibling script
# cannot drift apart.
_unset = [n for n in config.REQUIRED_TO_LAUNCH if not os.environ.get(n, "").strip()]
if _unset:
    print("=== cannot run: required configuration is not exported ===")
    for _name in _unset:
        print(f"  {_name}")
    print("\nThese have no defaults - every plausible one named somebody's")
    print("own install. Export them and run this again.")
    sys.exit(2)


def step(name, fn):
    try:
        out = fn()
        print(f"  OK   {name}: {out}")
        return out
    except Exception as e:
        print(f"  FAIL {name}: {type(e).__name__}: {e}")
        traceback.print_exc(limit=1)
        raise


print(">>> singleplayer must be REFUSED, with a reason")
try:
    server.launch(mode="singleplayer")
    print("  FAIL: it launched something instead of refusing")
    sys.exit(1)
except Exception as e:  # noqa: BLE001 - the refusal's TYPE is not the contract
    # What is promised is that it refuses and says why. Narrowing this to
    # SessionError would let a different exception - an unresolvable path, a
    # missing dotnet - pass as if the refusal had worked.
    assert "headless" in str(e).lower(), f"refusal did not explain: {e}"
    print("  OK   refused and explained")

print(">>> build_mod")
b = step("build", lambda: server.build_mod())
assert b["ok"], f"build not ok: {b['summary']}"

print(">>> launch server_client")
step("launch", lambda: server.launch(mode="server_client", port=7812))

try:
    print(">>> diag (client)")
    d = step("diag", lambda: server.diag())
    f = d["fields"]
    print(
        f"       side={d['side']} version={f.get('version')} motes={f.get('ambient-motes')} creep-tiles={f.get('creep-tiles')}"
    )
    assert d["side"] in {"client", "singleplayer"}, d["side"]
    assert isinstance(f.get("ambient-motes"), int), "counter did not parse as int"

    print(">>> trigger creep (server-authoritative)")
    r = step("trigger", lambda: server.trigger("creep", server=True))
    assert r["ok"], r["text"]

    print(">>> diag again - creep should now exist")
    d2 = step("diag2", lambda: server.diag())
    f2 = d2["fields"]
    print(
        f"       creep-sources={f2.get('creep-sources')} tiles={f2.get('creep-tiles')} converted={f2.get('creep-converted')} census={f2.get('creep-census')}"
    )
    print(f"       creep-residue={f2.get('creep-residue')}")
    # `creep-drawn` was printed here until 2026-08-05, months after the mod
    # renamed it — so this line reported `drawn=None` on every run and nobody
    # noticed, because a live check that PRINTS is not a live check that ASSERTS.
    #
    # Type only, deliberately not `census > 0`: creep grows over ticks, and the
    # 2026-08-04 probe needed ~6s before the first non-zero sample. Asserting a
    # value here would be a timing race dressed up as a correctness check.
    assert isinstance(f2.get("creep-census"), int), "census did not parse as int"

    print(">>> two shots must not be the same file")
    # Taken twice on purpose. One capture proves nothing about the defect that
    # actually shipped: `shot` returned the mod's fixed output path, so every
    # call handed back the same name and each capture quietly replaced the last.
    # Every call reported OK and returned a path that existed. Only a SECOND
    # capture can show that.
    s1 = step("shot 1", lambda: server.shot("bottomleft"))
    s2 = step("shot 2", lambda: server.shot("topright"))
    assert s1["path"] != s2["path"], f"both captures returned {s1['path']}"

    for shot in (s1, s2):
        png = Path(shot["path"])
        assert png.is_file(), f"{png} was returned but is not on disk"
        assert png.stat().st_size > 0, f"{png} is on disk but empty"
        # The README claimed for a long time that the server did this. It never
        # did, and it still does not - but a LIVE check is where the claim can
        # be made true cheaply, against the real file the real game wrote.
        assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", f"{png} is not a PNG"

    print(">>> an unknown command must be refused BEFORE hitting disk")
    try:
        server.trigger("creeep")
        print("  FAIL: bad command was not refused")
        # Exits rather than reading on, the way the singleplayer check above
        # does. Printing FAIL and continuing left the script exiting 0 with the
        # word FAIL in its output: legible to whoever was watching, invisible to
        # anything that runs this and reads a status code. SystemExit is not an
        # Exception, so the handler below does not swallow it, and the outer
        # `finally` still stops the game.
        sys.exit(1)
    except Exception as e:  # noqa: BLE001 - any refusal beats reaching disk
        print(f"  OK   refused: {e}")
finally:
    print(">>> stop")
    print("  ", server.stop())
