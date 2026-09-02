"""What the TOOLS expose, as opposed to what the code beneath them can do.

Every other test file here exercises a module directly. That left the thinnest
layer untested, and it was where things went missing: `diag.sections` was
implemented, tested, and called by nothing; `launch` quietly dropped two
arguments `session.launch` accepts. Both are invisible to a test that calls the
session and never the tool.
"""

from __future__ import annotations

import pytest

from tmodloader_mcp import diag as diag_mod
from tmodloader_mcp import server as server_mod

DUMP_FIELDS = {"side": "client netmode=1", "npcs": "active=2 mutated=1"}
DUMP_RECORDS = {
    "npcs": [
        "idx=0 type=37 name=Old Man mutated=0 mutation=None",
        "idx=1 type=22 name=Guide mutated=1 mutation=Bloom",
    ]
}


class FakeSession:
    """Records what the tool layer passed down, and answers plausibly."""

    def __init__(self):
        self.mode = "server_client"
        self.port = 7810
        self.player = "n43n"
        # `launch` resolves this and the session records it; a fake without it
        # would let `status` drop the world and still pass.
        self.world = r"C:\Worlds\Fake.wld"
        self.started = {4808, 42224}
        # `join` appends here and `status` reports it; a fake without it would
        # let the tool layer drop the list and still pass.
        self.joined: list[str] = ["tst2"]
        self.calls: list[tuple] = []

    def diag(self, *, server=False, target=None, timeout=60.0):
        self.calls.append(("diag", server, target, timeout))
        return diag_mod.Diag(fields=dict(DUMP_FIELDS), records=dict(DUMP_RECORDS))

    def ask(self, command, *, target=None, argument=None, server=False, timeout=60.0):
        self.calls.append(("ask", command, target, argument, server, timeout))
        from tmodloader_mcp.triggers import Reply

        return Reply(command=command, text="ok")

    def shot(self, region, *, target=None, timeout=60.0):
        self.calls.append(("shot", region, target, timeout))
        from pathlib import Path

        return Path(f"/save/biomancy-shot-001-{region}.png")


@pytest.fixture
def session(monkeypatch):
    fake = FakeSession()
    monkeypatch.setattr(server_mod, "_session", fake)
    return fake


@pytest.fixture
def no_session(monkeypatch):
    monkeypatch.setattr(server_mod, "_session", None)


# ---- the records that were parsed and thrown away ----------------------


def test_diag_returns_the_records_not_only_the_summary(session):
    """THE CAPABILITY THAT EXISTED AND WAS REACHABLE FROM NOTHING.

    `diag.sections` parses the indented per-record lines and has had tests since
    it was written. No tool ever called it, so `npcs: active=2 mutated=1` came
    back and the two lines saying WHICH TWO did not. A caller could learn that
    something was there and never what it was.
    """
    out = server_mod.diag()

    assert out["records"]["npcs"] == DUMP_RECORDS["npcs"]
    # And the summary is still there: records ADD to the scalars, not replace.
    assert out["fields"]["npcs"] == "active=2 mutated=1"
    assert out["side"] == "client"


# ---- arguments the tools dropped ---------------------------------------


def test_launch_passes_world_and_timeout_through(monkeypatch, no_session):
    """`session.launch` has taken both since the world-path bug; the TOOL took
    neither, so an agent could not choose a world or wait longer on a slow box.
    """
    seen = {}

    def fake_launch(cfg, mode, *, port, player, world, timeout):
        seen.update(mode=mode, port=port, player=player, world=world, timeout=timeout)
        return FakeSession()

    monkeypatch.setattr(server_mod.session_mod, "launch", fake_launch)
    monkeypatch.setattr(server_mod, "_cfg", lambda: object())

    server_mod.launch(world=r"C:\Worlds\Other.wld", timeout=900.0)

    assert seen["world"] == r"C:\Worlds\Other.wld"
    assert seen["timeout"] == 900.0


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (lambda: server_mod.trigger("diag", timeout=5.0), 5.0),
        (lambda: server_mod.diag(timeout=7.0), 7.0),
        (lambda: server_mod.shot("full", timeout=9.0), 9.0),
    ],
)
def test_a_timeout_given_to_a_tool_reaches_the_session(session, call, expected):
    """All three took one internally and exposed none, so a slow world had no
    remedy from the caller's side."""
    call()

    assert session.calls[-1][-1] == expected


def test_the_defaults_are_still_the_defaults(session):
    """Positive control: adding the parameter must not change what happens when
    nobody passes it."""
    server_mod.diag()

    assert session.calls[-1][-1] == 60.0


#: Every tool whose work is bounded by time, and the parameter that bounds it.
#: A list rather than three more one-off tests, because the defect this pins is
#: not "one tool forgot" — it is that the surface and the code beneath it drift
#: apart one function at a time.
TIMED_TOOLS = [
    ("build_mod", "timeout"),
    ("launch", "timeout"),
    ("trigger", "timeout"),
    ("diag", "timeout"),
    ("shot", "timeout"),
    ("stop", "settle"),
]


@pytest.mark.parametrize(("tool", "parameter"), TIMED_TOOLS)
def test_every_bounded_tool_lets_the_caller_set_its_bound(tool, parameter):
    """THE CHECK THAT WOULD HAVE CAUGHT THIS TWICE.

    `stop` grew its `settle` argument precisely because a bound nothing can set
    is a bound nothing can check — and then the TOOL still could not set it.
    `build_mod` took no arguments at all while `build()` had taken a timeout all
    along. Four other tools were fixed in between, one at a time, by noticing.

    Asking it of every timed tool at once is the difference between fixing the
    instances somebody spotted and pinning the class.
    """
    import inspect

    signature = inspect.signature(getattr(server_mod, tool))

    assert parameter in signature.parameters, (
        f"`{tool}` is bounded by time and the caller cannot say how long; "
        f"the function beneath it takes `{parameter}`"
    )


# ---- asking without breaking something ---------------------------------


def test_status_reports_no_session_without_raising(no_session):
    """THE QUESTION THAT HAD NO ANSWER.

    Seven tools and not one could say whether a session existed. An agent that
    lost track had to provoke an error to find out — `launch` fails when one
    exists, `diag` fails when one does not — so the cheapest question on the
    surface was the one you had to break something to ask.
    """
    assert server_mod.status() == {
        "running": False,
        "mode": None,
        "port": None,
        "player": None,
        "world": None,
        "joined": None,
        "started_pids": None,
    }


def test_every_status_key_is_present_whether_or_not_a_game_is_running(monkeypatch):
    """THE SHAPE IS THE CONTRACT, and it used to change underneath callers.

    These fields were `NotRequired`, so with no game running `status` returned
    `{"running": False}` and nothing else. As Python that reads fine and every
    test here passed. Over MCP it does not survive: the tool's output is
    validated against a schema generated from this TypedDict, and the missing
    keys came back as four "Field required" errors - so the cheapest read-only
    question on the surface was broken in the state it exists to report.

    Asserting the KEYS rather than the values, because the values legitimately
    differ and the shape must not.
    """
    keys = set(server_mod.StatusOut.__annotations__)

    monkeypatch.setattr(server_mod, "_session", None)
    idle = server_mod.status()

    monkeypatch.setattr(server_mod, "_session", FakeSession())
    live = server_mod.status()

    assert set(idle) == keys, "a stopped session must still fill every field"
    assert set(live) == keys, "a running session must fill every field"
    assert idle["running"] is False and live["running"] is True
    assert idle["started_pids"] is None and live["started_pids"] is not None


def test_status_describes_the_session_it_believes_in(session):
    out = server_mod.status()

    assert out["running"] is True
    assert out["mode"] == "server_client"
    assert out["port"] == 7810
    assert out["player"] == "n43n"
    assert out["started_pids"] == [4808, 42224]


def test_status_does_not_start_or_touch_anything(session):
    """It is annotated read-only, so it must behave that way: no trigger, no
    diag, nothing written. A status call that woke the game would be worse than
    no status call."""
    server_mod.status()

    assert session.calls == []


# ---- serialisation ----------------------------------------------------------


def test_every_tool_and_resource_is_wrapped_by_the_lock():
    """THE PREMISE THE WHOLE SURFACE RESTS ON, now enforced rather than
    assumed. The module header used to claim synchronous tools serialise on
    the event loop; under mcp 2.x they run in worker threads under a
    concurrent dispatcher, so two calls from one batching client ran in real
    parallel and consumed each other's replies. The `_serialized` decorator
    is the fix, and this scan is what stops the NEXT tool being added without
    it - which would be invisible to every other test here.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(server_mod))
    unlocked = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue

        names = []
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            names.append(ast.unparse(target))

        wrapped = any(name in ("mcp.tool", "mcp.resource") for name in names)
        if wrapped and "_serialized" not in names:
            unlocked.append(node.name)

    assert not unlocked, (
        f"{unlocked} reach process-global state without `@_serialized` - two "
        "concurrent calls there are the torn-reply bug back again"
    )


def test_the_lock_wrapper_preserves_what_the_schema_generator_reads():
    """`wraps` carries signature, annotations and docstring across - pinned
    because the SDK builds each tool's schema from exactly these, and a bare
    wrapper would silently flatten every tool to (*args, **kwargs)."""
    import inspect

    tool = server_mod.log_since

    assert tool.__doc__ and "log has gained" in tool.__doc__
    assert "offset" in inspect.signature(tool).parameters
    assert inspect.signature(tool).return_annotation is not inspect.Signature.empty


# ---- refusals reaching the model --------------------------------------------


def test_every_tool_surfaces_its_refusals():
    """The companion to the lock scan, and it exists for the same reason.

    mcp 2.1 replaced the message of any exception that is not a ToolError with
    `Error executing tool <name>`, so three tools that had spent their whole
    life telling the caller what to do instead said nothing. `_surfaces_refusals`
    converts at the boundary; this scan is what stops the NEXT tool being added
    without it, which no other test here would notice - a masked refusal is
    still a failed call, so the tool "works".

    The resource is deliberately not in scope: ToolError is the tool channel,
    and `capture_resource` refuses through the resource one.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(server_mod))
    unsurfaced = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue

        names = []
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            names.append(ast.unparse(target))

        if "mcp.tool" in names and "_surfaces_refusals" not in names:
            unsurfaced.append(node.name)

    assert not unsurfaced, (
        f"{unsurfaced} refuse without `@_surfaces_refusals` - since mcp 2.1 the "
        "caller gets `Error executing tool <name>` and the instruction in the "
        "refusal never leaves the server log"
    )


def test_every_error_this_package_raises_is_one_the_boundary_recognises():
    """`_REFUSALS` is `(RuntimeError, ValueError)` because that is the
    convention every module here already follows, not because those two cover
    today's classes by luck. Pinning the convention is what keeps the pair
    covering an open set: a new module inventing `WidgetError(Exception)` would
    be masked at the boundary and this is the only place that would say so.

    A private class is exempt, because a leading underscore is this package's
    own mark for "not part of the surface" - `config._ProbeWriteFailed` is
    raised past an `lru_cache` and caught two functions later, and never gets
    near a tool. The exemption is not taken on trust: a private exception has
    to be caught in the module that defines it, or it is treated as one that
    escapes and the convention applies to it after all.
    """
    import ast
    import importlib
    import inspect
    import pkgutil

    import tmodloader_mcp

    stray = []
    for mod in pkgutil.iter_modules(tmodloader_mcp.__path__):
        module = importlib.import_module(f"tmodloader_mcp.{mod.name}")
        caught = {
            ast.unparse(handler.type)
            for handler in ast.walk(ast.parse(inspect.getsource(module)))
            if isinstance(handler, ast.ExceptHandler) and handler.type is not None
        }

        for name, obj in vars(module).items():
            if not inspect.isclass(obj) or not issubclass(obj, BaseException):
                continue
            if obj.__module__ != module.__name__:
                continue  # imported from elsewhere, counted where it is defined
            if issubclass(obj, server_mod._REFUSALS):
                continue
            if name.startswith("_") and name in caught:
                continue  # internal control flow, handled where it is raised
            stray.append(f"{module.__name__}.{name}")

    assert not stray, (
        f"{stray} do not subclass RuntimeError or ValueError, so the tool "
        "boundary reads them as crashes and hides their message"
    )


def test_a_real_bug_is_still_masked_rather_than_read_as_a_refusal():
    """The negative half, with the positive control beside it in the same test.

    Surfacing refusals must not become surfacing everything: `Error executing
    tool X` is the right answer for a TypeError, because that message is a
    stack trace's worth of internals the caller can do nothing with. If this
    ever fails while the test above passes, the tuple has been widened to
    `Exception` and the boundary no longer distinguishes the two.
    """
    from mcp.server.mcpserver.exceptions import ToolError

    @server_mod._surfaces_refusals
    def refuses():
        raise RuntimeError("no session - call `launch` first")

    @server_mod._surfaces_refusals
    def crashes():
        raise TypeError("unsupported operand type(s)")

    with pytest.raises(ToolError) as refusal:
        refuses()
    assert "call `launch` first" in str(refusal.value)

    with pytest.raises(TypeError):
        crashes()
