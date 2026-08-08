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
from pathlib import Path

import pytest

from tmodloader_mcp import server as server_mod

LIVE_CHECK = Path(__file__).parent / "live_check.py"


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


def test_no_handler_swallows_the_exits_the_checks_depend_on():
    """Guards the premise the fix above rests on.

    Failing a check is spelled `sys.exit(1)`, which raises SystemExit — and
    SystemExit is deliberately NOT an Exception, so the `except Exception`
    handlers wrapped around these probes let it through. A bare `except:` or an
    `except BaseException:` would catch it instead, and the script would go back
    to printing FAIL and exiting 0 with nothing to show for it.

    That is a one-word edit away at any time, and it would leave every test here
    passing, because the AST check above only asks whether an exit is WRITTEN.
    """
    assert _handlers_catching_everything(LIVE_CHECK.read_text()) == []


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


def test_live_check_is_still_valid_python():
    """Cheapest possible smoke test, and it has caught nothing yet.

    Kept anyway because the failure it guards - a script that cannot even parse
    - is indistinguishable from "the game refused" when you are reading its
    output at the end of a five-minute launch.
    """
    try:
        ast.parse(LIVE_CHECK.read_text())
    except SyntaxError as e:
        pytest.fail(f"live_check.py does not parse: {e}")
