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
