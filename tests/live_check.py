"""Drive the real thing through the same code paths the MCP tools use."""

import sys
import traceback
from tmodloader_mcp import server


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
except Exception as e:
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

    print(">>> shot bottomleft")
    s = step("shot", lambda: server.shot("bottomleft"))

    print(">>> an unknown command must be refused BEFORE hitting disk")
    try:
        server.trigger("creeep")
        print("  FAIL: bad command was not refused")
    except Exception as e:
        print(f"  OK   refused: {e}")
finally:
    print(">>> stop")
    print("  ", server.stop())
