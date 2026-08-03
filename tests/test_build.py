"""Interpreting tModLoader's build output.

Pure, because the interesting cases are annoying to reproduce on demand: the
game being open, and warnings without errors.
"""

from __future__ import annotations

from tmodloader_mcp import build

CLEAN = """\
Reading properties: Biomancy
Building: Biomancy
Compiling Biomancy.dll
Compilation finished with 0 errors and 0 warnings
Packaging: Biomancy
"""

WARNINGS = "Compilation finished with 0 errors and 3 warnings\n"
ERRORS = """\
Compiling Biomancy.dll
CSC : error CS2001: Source file 'Foo.cs' could not be found.
Compilation finished with 1 errors and 0 warnings
"""

GAME_OPEN = """\
Reading properties: Biomancy
tModLoader: Mod Build error TML003: Please close tModLoader or disable the mod in-game to build mods directly.
"""


def test_a_clean_build_is_ok():
    got = build.interpret(CLEAN)
    assert got.ok
    assert got.errors == 0
    assert got.warnings == 0
    assert not got.game_was_open


def test_warnings_do_not_fail_a_build():
    got = build.interpret(WARNINGS)
    assert got.ok
    assert got.warnings == 3


def test_errors_fail_it():
    got = build.interpret(ERRORS)
    assert not got.ok
    assert got.errors == 1


def test_the_game_being_open_is_reported_as_itself():
    """THE ONE THIS MODULE EXISTS FOR.

    TML003 is completely recoverable and arrives looking like a compile failure.
    Reading it as a broken build sends you hunting a syntax error that is not
    there — which is exactly what happened before this was surfaced.
    """
    got = build.interpret(GAME_OPEN)
    assert not got.ok
    assert got.game_was_open
    assert "close" in got.summary.lower()
    assert "not a compile failure" in got.summary


def test_a_refusal_wins_over_a_success_line():
    """A build can print the success line AND still have been refused.

    No .tmod is written in that case, so reporting success would be a lie about
    the artifact — the same shape as a harness passing while nothing drew.
    """
    got = build.interpret(CLEAN + GAME_OPEN)
    assert got.game_was_open
    assert not got.ok


def test_output_with_no_verdict_line_is_not_silently_ok():
    """Positive control against defaulting to success.

    A build killed partway prints neither an error nor the success line. If the
    absence of bad news read as good news, a truncated build would pass.
    """
    got = build.interpret("Reading properties: Biomancy\n")
    assert not got.ok
    assert not got.game_was_open


def test_a_timeout_is_not_success():
    got = build.interpret("")
    assert not got.ok
