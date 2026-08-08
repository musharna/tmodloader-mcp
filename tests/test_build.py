"""Interpreting tModLoader's build output.

Pure, because the interesting cases are annoying to reproduce on demand: the
game being open, and warnings without errors.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from tmodloader_mcp import build
from tmodloader_mcp import config as config_mod

_CFG = config_mod.Config(
    tml_dir=Path("/mnt/c/tModLoader"),
    save_dir=Path("/mnt/c/save"),
    mod_source=Path("/mnt/c/Mods/Yours"),
    mod_source_win=r"C:\Mods\Yours",
    world_win=r"C:\Worlds\Test.wld",
    taskkill=Path("/mnt/c/Windows/System32/taskkill.exe"),
    tasklist=Path("/mnt/c/Windows/System32/tasklist.exe"),
    powershell=Path("/mnt/c/Windows/System32/powershell.exe"),
)

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


def test_output_that_never_reached_a_verdict_is_not_success():
    """Was called `test_a_timeout_is_not_success`, and never timed out.

    It passed an empty string to `interpret`, which is the no-output path, not
    the timeout path — `interpret` is never even called when a build times out.
    So the only test naming timeouts asserted nothing about them, and the wrong
    summary below sat under a green suite. A test's NAME is not its coverage.
    """
    got = build.interpret("")
    assert not got.ok


class _Timeout:
    """A `subprocess.run` that never comes back in time."""

    def __call__(self, *args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="tModLoader.dll -build", timeout=600.0)


def test_a_build_that_timed_out_says_so_instead_of_blaming_the_compiler(monkeypatch):
    """THE ONE THAT NAMED THE WRONG CAUSE.

    A timeout produced `ok=False, errors=0, warnings=0`, and `summary` — the
    field the MCP tool actually surfaces — rendered that as "build failed: 0
    error(s), 0 warning(s)". A ten-minute hang reported as a compile failure
    with nothing wrong in it, which is the confidently-wrong error this repo has
    now shipped twice: it gets believed, and it sends the reader to the code.
    """
    monkeypatch.setattr(build.subprocess, "run", _Timeout())

    got = build.build(_CFG, timeout=600.0)

    assert got.timed_out
    assert not got.ok
    assert "did not finish" in got.summary
    assert "error(s)" not in got.summary


def test_an_ordinary_failure_still_reads_as_one():
    """Positive control: the timeout wording must not swallow real failures."""
    got = build.interpret(ERRORS)
    assert not got.timed_out
    assert "1 error(s)" in got.summary


def test_a_source_windows_cannot_name_is_refused_before_launching_anything(
    monkeypatch,
):
    """`mod_source_win` is None when it could be neither derived nor given.

    Passing that to the command line would spell the word "None" as a path, and
    tModLoader would fail with an error about a directory nobody named.
    """
    ran = []
    monkeypatch.setattr(build.subprocess, "run", lambda *a, **k: ran.append(a))

    with pytest.raises(config_mod.ConfigError) as e:
        build.build(replace(_CFG, mod_source_win=None))

    assert "TMODLOADER_MOD_SOURCE_WIN" in str(e.value)
    assert ran == []  # refused before spawning, not after
