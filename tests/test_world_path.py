"""The world argument, which is the one that cost a live run.

The first version passed a WSL path to a directory. tModLoader runs as a Windows
process, so it could not resolve /mnt/c; the server started, found no world, and
the only symptom was a readiness timeout blaming the heartbeat. Nothing in the
output said "wrong path" — the check that would have said so is this one.
"""

from __future__ import annotations

import pytest

from tmodloader_mcp.session import world_problem

WINDOWS_WORLD = r"C:\Users\a2b32\Documents\My Games\Terraria\tModLoader\Worlds\Test.wld"


def test_a_windows_world_file_is_accepted():
    """Positive control. Without it, "reject everything" passes every case below."""
    assert world_problem(WINDOWS_WORLD) is None


def test_a_wsl_path_is_refused_and_says_why():
    """THE ONE THAT ACTUALLY HAPPENED.

    Silent in the game: the process starts, resolves nothing, and times out
    five minutes later blaming the heartbeat.
    """
    problem = world_problem(
        "/mnt/c/Users/user/Documents/My Games/Terraria/tModLoader/Worlds"
    )
    assert problem is not None
    assert "windows" in problem.lower()
    assert "TMODLOADER_WORLD_WIN" in problem


def test_a_directory_is_refused_even_when_it_is_a_windows_path():
    """The second half of the same bug, and independent of the first.

    A correct Windows path that names a FOLDER fails identically — the server
    starts and never loads anything.
    """
    problem = world_problem(
        r"C:\Users\a2b32\Documents\My Games\Terraria\tModLoader\Worlds"
    )
    assert problem is not None
    assert ".wld" in problem


@pytest.mark.parametrize("world", [WINDOWS_WORLD, WINDOWS_WORLD.upper()])
def test_the_extension_check_is_case_insensitive(world):
    """Windows does not care about the case of .WLD, so neither may this."""
    assert world_problem(world) is None
