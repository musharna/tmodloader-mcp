"""Drive the real thing through the same code paths the MCP tools use."""
import sys, traceback
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
    print(f"       side={d['side']} version={f.get('version')} motes={f.get('ambient-motes')} creep-tiles={f.get('creep-tiles')}")
    assert d["side"] in {"client", "singleplayer"}, d["side"]
    assert isinstance(f.get("ambient-motes"), int), "counter did not parse as int"

    print(">>> trigger creep (server-authoritative)")
    r = step("trigger", lambda: server.trigger("creep", server=True))
    assert r["ok"], r["text"]

    print(">>> diag again - creep should now exist")
    d2 = step("diag2", lambda: server.diag())
    print(f"       creep-sources={d2['fields'].get('creep-sources')} tiles={d2['fields'].get('creep-tiles')} drawn={d2['fields'].get('creep-drawn')}")

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
