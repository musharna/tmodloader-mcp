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
