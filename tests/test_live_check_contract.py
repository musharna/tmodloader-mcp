"""Keep `live_check.py` honest without needing a game to find out.

`live_check.py` is a script, not a collected test - deliberately, because it
drives a real running game. The cost of that is nobody notices when it rots:
it references the API by name, and a rename leaves it syntactically perfect and
wrong, until someone runs it with a world loaded and reads the failure.

That has already happened once here, and the script says so in its own comment:
it printed `creep-drawn` for months after the mod renamed that field, reporting
`None` on every run.

This does not run the script. It reads what the script SAYS it will call, and
checks those names still resolve - the part of a live check that does not need
anything to be alive.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
from pathlib import Path

import pytest

from tmodloader_mcp import server as server_mod

LIVE_CHECK = Path(__file__).parent / "live_check.py"
PROTOCOL_CHECK = Path(__file__).parent / "live_protocol_check.py"

#: Every script here that drives a real game and is therefore NOT collected.
#: A list rather than one constant, because the defect this file exists to
#: prevent is a script rotting unwatched - and a second unwatched script was
#: added the moment there was a second way to drive the game. Guards that name
#: one file stop guarding the moment a sibling appears.
LIVE_SCRIPTS = [LIVE_CHECK, PROTOCOL_CHECK]
SCRIPT_IDS = [p.name for p in LIVE_SCRIPTS]


def _server_attributes(source: str) -> set[str]:
    """Every `server.<name>` the source refers to."""
    tree = ast.parse(source)

    found = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "server"
        ):
            found.add(node.attr)

    return found


def test_the_extractor_finds_what_it_is_looking_for():
    """POSITIVE CONTROL for the reader itself.

    The test below passes if `_server_attributes` returns an EMPTY set - an
    extractor that quietly found nothing would report a clean bill of health
    for a script full of dead calls. So pin that it reads a real one.
    """
    found = _server_attributes("server.launch(mode='x')\nserver.stop()\n")

    assert found == {"launch", "stop"}


def test_the_extractor_does_not_confuse_other_objects_for_the_server():
    """The other way it could be uselessly quiet: over-matching.

    A reader that collected every attribute access anywhere would flag
    `result.ok` and `path.name` as missing server functions, and a check that
    cries wolf gets deleted.
    """
    found = _server_attributes("other.launch()\nd['fields'].get('x')\nserver.diag()\n")

    assert found == {"diag"}


def test_every_server_call_in_live_check_still_exists():
    """THE DRIFT GUARD.

    A name here that no longer exists means the live check is broken in a way
    that only shows up with a game running - the most expensive place to find
    out, and the one place nobody is watching.
    """
    assert LIVE_CHECK.is_file(), f"{LIVE_CHECK} is gone"

    used = _server_attributes(LIVE_CHECK.read_text())
    assert used, "no server calls found - the extractor or the script changed shape"

    missing = sorted(name for name in used if not hasattr(server_mod, name))
    assert not missing, (
        f"live_check.py calls {missing}, which no longer exist on the server "
        "module. It would fail only when run against a live game."
    )


def _strings_in(call: ast.Call) -> list[str]:
    """Every literal string in a call's arguments, f-strings included."""
    out = []
    for arg in call.args:
        for node in ast.walk(arg):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.append(node.value)
    return out


def _announces_failure(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Name)
        and stmt.value.func.id == "print"
        and any("FAIL" in s for s in _strings_in(stmt.value))
    )


def _stops_the_run(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.Raise):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        func = stmt.value.func
        return isinstance(func, ast.Attribute) and func.attr == "exit"
    return False


def _fail_branches_that_do_not_stop(source: str) -> list[int]:
    """Lines that print FAIL and then let the script carry on regardless."""
    bad: list[int] = []

    for node in ast.walk(ast.parse(source)):
        blocks = [
            getattr(node, field, None) for field in ("body", "orelse", "finalbody")
        ]
        for block in blocks:
            if not isinstance(block, list):
                continue
            for i, stmt in enumerate(block):
                if _announces_failure(stmt) and not any(
                    _stops_the_run(later) for later in block[i + 1 :]
                ):
                    bad.append(stmt.lineno)

    return sorted(bad)


def test_the_reader_spots_a_failure_that_goes_unpunished():
    """POSITIVE CONTROL. A reader that found nothing would bless every script."""
    bad = _fail_branches_that_do_not_stop(
        "try:\n"
        "    thing()\n"
        "    print('  FAIL: it worked when it should not have')\n"
        "except Exception:\n"
        "    pass\n"
    )

    assert bad == [3]


@pytest.mark.parametrize(
    "after",
    ["    sys.exit(1)\n", "    raise AssertionError('no')\n"],
)
def test_a_failure_that_does_stop_is_not_flagged(after):
    """The other way it could be useless: flagging the branches that are fine.

    Both spellings the script already uses count — `sys.exit` after the message,
    and `raise` after it inside `step`.
    """
    source = "if bad:\n    print('  FAIL: nope')\n" + after

    assert _fail_branches_that_do_not_stop(source) == []


def test_every_failure_the_live_check_prints_also_fails_the_run():
    """THE ONE THAT WAS ACTUALLY BROKEN.

    `server.trigger("creeep")` succeeding means the unknown-command guard is
    gone — the check most worth having, since the whole reason to refuse a bad
    command here is that the game answers a typo with silence. It printed
    `FAIL`, fell through to the teardown, and the script exited 0.

    A live check is read by whoever ran it AND by whatever ran it. Printing the
    word FAIL only serves the first. This is the same class as the teardown that
    reported kills it never made: the announcement was not wired to the outcome.
    """
    unpunished = _fail_branches_that_do_not_stop(LIVE_CHECK.read_text())

    assert not unpunished, (
        f"live_check.py prints FAIL at line(s) {unpunished} and then carries on, "
        "so the run reports success anyway."
    )


def _handlers_catching_everything(source: str) -> list[int]:
    """Lines of `except:` or `except BaseException:` — handlers that eat exits."""
    caught = []

    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ExceptHandler):
            continue
        bare = node.type is None
        broad = isinstance(node.type, ast.Name) and node.type.id == "BaseException"
        if bare or broad:
            caught.append(node.lineno)

    return sorted(caught)


@pytest.mark.parametrize("script", LIVE_SCRIPTS, ids=SCRIPT_IDS)
def test_no_handler_swallows_the_exits_the_checks_depend_on(script):
    """Guards the premise the fix above rests on.

    Failing a check is spelled `sys.exit(1)`, which raises SystemExit — and
    SystemExit is deliberately NOT an Exception, so the `except Exception`
    handlers wrapped around these probes let it through. A bare `except:` or an
    `except BaseException:` would catch it instead, and the script would go back
    to printing FAIL and exiting 0 with nothing to show for it.

    That is a one-word edit away at any time, and it would leave every test here
    passing, because the AST check above only asks whether an exit is WRITTEN.
    """
    assert _handlers_catching_everything(script.read_text()) == []


def test_that_reader_can_actually_see_one():
    """Positive control: pin that both spellings are recognised."""
    assert _handlers_catching_everything(
        "try:\n    x()\nexcept BaseException:\n    pass\n"
    ) == [3]
    assert _handlers_catching_everything("try:\n    x()\nexcept:\n    pass\n") == [3]
    assert (
        _handlers_catching_everything("try:\n    x()\nexcept ValueError:\n    p\n")
        == []
    )


@pytest.mark.parametrize("script", LIVE_SCRIPTS, ids=SCRIPT_IDS)
def test_the_live_scripts_are_still_valid_python(script):
    """Cheapest possible smoke test, and it has caught nothing yet.

    Kept anyway because the failure it guards - a script that cannot even parse
    - is indistinguishable from "the game refused" when you are reading its
    output at the end of a five-minute launch.
    """
    try:
        ast.parse(script.read_text())
    except SyntaxError as e:
        pytest.fail(f"{script.name} does not parse: {e}")


# ---- the protocol check names its tools as STRINGS -----------------------


def _tools_called(source: str) -> set[str]:
    """Every tool name passed to `session.call_tool(...)` as a literal.

    The protocol script cannot be checked the way `live_check.py` is. It never
    touches `server.<name>`; it sends `call_tool("diag", {})` down a pipe. So a
    renamed tool leaves it syntactically perfect, passing every other test here,
    and wrong - failing only against a live game, which is the same expensive
    place `creep-drawn` hid for months.
    """
    found = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # The RECEIVER matters, not just the method name. Checking only
        # `.attr == "call_tool"` matched `other.call_tool("nope")` too - caught
        # by the over-match control below, which is the entire reason it exists.
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "call_tool"
            and isinstance(func.value, ast.Name)
            and func.value.id == "session"
        ):
            continue
        if (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found.add(node.args[0].value)
    return found


def test_the_tool_reader_finds_what_it_is_looking_for():
    """POSITIVE CONTROL. An extractor returning nothing blesses every script."""
    found = _tools_called(
        'await session.call_tool("diag", {})\nx = session.call_tool("stop", {})\n'
    )

    assert found == {"diag", "stop"}


def test_the_tool_reader_ignores_other_calls_and_computed_names():
    """The other way it could be useless: over-matching.

    `session.read_resource(...)` and `session.list_tools()` are not tool calls,
    and a name built at runtime is not a literal this can check - reporting it
    as missing would be crying wolf about the one case that is legitimately
    unverifiable here.
    """
    found = _tools_called(
        'session.read_resource("capture://x.png")\n'
        "session.list_tools()\n"
        "session.call_tool(name, {})\n"
        'other.call_tool("nope", {})\n'
    )

    assert found == set()


def test_every_tool_the_protocol_check_calls_is_actually_registered():
    """THE DRIFT GUARD, in the spelling this script actually uses.

    Compared against the server's OWN registry rather than a list written down
    here. A copy of the tool names would drift from the server exactly the way
    the script does, and then agree with it - two wrong things matching is what
    a copy buys you. This is the same reason `commands` learns from the mod
    instead of shipping a list of them.
    """
    called = _tools_called(PROTOCOL_CHECK.read_text())
    assert called, (
        "no call_tool names found - the extractor or the script changed shape"
    )

    registered = {t.name for t in asyncio.run(server_mod.mcp.list_tools())}
    unknown = sorted(called - registered)

    assert not unknown, (
        f"live_protocol_check.py calls {unknown}, which the server does not "
        f"register. It would fail only against a live game. Registered: "
        f"{sorted(registered)}"
    )


def test_the_protocol_check_drives_every_tool_that_needs_a_game():
    """The point of the script, pinned so it cannot quietly shrink.

    These seven are unreachable from `test_mcp_protocol.py` - a fake install
    cannot launch anything - so this script is the ONLY protocol-level evidence
    they work. A tool dropped from the journey would leave that with no cover
    and nothing would say so.
    """
    needs_a_game = {
        "build_mod",
        "launch",
        "commands",
        "trigger",
        "diag",
        "shot",
        "stop",
    }

    missing = sorted(needs_a_game - _tools_called(PROTOCOL_CHECK.read_text()))

    assert not missing, f"nothing drives {missing} over the protocol any more"


# ---- the reporting funnel the whole script depends on --------------------


def _protocol_module():
    """Import the live script by path, without running its journey.

    `tests/` is not a package, so there is no `from . import` to use. Loading it
    by path also states the truth: it is a script, and importing it is only safe
    because the run is behind `if __name__ == "__main__"`. It used to launch
    Terraria at module scope, which is precisely why none of its reporting could
    be checked by anything cheap.
    """
    spec = importlib.util.spec_from_file_location("_lpc", PROTOCOL_CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_passing_check_does_not_print_its_failure_explanation(capsys):
    """THE ONE THAT WAS ACTUALLY BROKEN, in the new script.

    `detail` is written as the explanation of a failure. Printed on success too,
    it produced `OK   shot 1 is a PNG: bad magic` - a passing run that reads as
    a failing one, which is worse than useless in a log somebody skims once.
    """
    lpc = _protocol_module()

    lpc.failures.clear()
    assert lpc.check("it works", True, "THIS MUST NOT APPEAR") is True

    out = capsys.readouterr().out
    assert "OK" in out
    assert "THIS MUST NOT APPEAR" not in out
    assert lpc.failures == [], "a passing check recorded a failure"


def test_a_failing_check_prints_its_reason_and_records_it(capsys):
    """Positive control for the above: suppressing detail everywhere would also
    pass that test, and would throw away the only thing a failure carries."""
    lpc = _protocol_module()

    lpc.failures.clear()
    assert lpc.check("it broke", False, "the reason") is False

    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "the reason" in out
    assert lpc.failures == ["it broke: the reason"]
    lpc.failures.clear()
