"""Resolving configuration, and catching the ways it can be quietly wrong.

The expensive class here is not a missing path — that is loud. It is a config
that RESOLVES, passes every check, and drives the wrong thing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tmodloader_mcp import config

# Captured from real `wslpath -w` output on WSL2, because CI has no wslpath and
# a translation this file invented for itself would be checked against nothing.
#
#   /mnt/c/Users/a2b32/.../Biomancy  ->  C:\Users\a2b32\...\Biomancy
#   /mnt/c/Program Files (x86)/Steam ->  C:\Program Files (x86)\Steam
#   /home/mjarnold                   ->  \\wsl.localhost\Ubuntu\home\mjarnold
REAL_TRANSLATIONS = [
    ("/mnt/c/Program Files (x86)/Steam", r"C:\Program Files (x86)\Steam"),
    (
        "/mnt/c/Users/a2b32/Documents/My Games/Terraria/tModLoader/ModSources/Biomancy",
        (
            r"C:\Users\a2b32\Documents\My Games\Terraria\tModLoader"
            r"\ModSources\Biomancy"
        ),
    ),
    ("/mnt/d/Mods/Yours", r"D:\Mods\Yours"),
]


@pytest.mark.parametrize(("wsl", "win"), REAL_TRANSLATIONS)
def test_a_drive_path_translates_the_way_wslpath_does(wsl, win):
    assert config.windows_path_for(Path(wsl)) == win


@pytest.mark.parametrize("wsl", ["/home/mjarnold", "/mnt/wsl", "/", "relative/path"])
def test_a_path_with_no_drive_letter_is_not_guessed_at(wsl):
    """`wslpath` answers these with a \\\\wsl.localhost\\ UNC, and whether
    tModLoader can build from one is not something this repo has established.

    Returning None says "I do not know" and lets `check` ask for the variable.
    Inventing a UNC path here would be asserting an answer nobody measured.
    """
    assert config.windows_path_for(Path(wsl)) is None


def test_the_windows_mod_source_follows_the_wsl_one():
    """THE ONE THAT BUILDS SOMEBODY ELSE'S MOD.

    `TMODLOADER_MOD_SOURCE` and `TMODLOADER_MOD_SOURCE_WIN` name ONE directory.
    They used to default independently, so overriding the first and not the
    second left the Windows path pointing at this machine's Biomancy: every
    other tool drove the caller's mod while `build_mod` compiled Biomancy, and
    the build reported success because it HAD succeeded — at the wrong thing.
    """
    cfg = config.load({"TMODLOADER_MOD_SOURCE": "/mnt/c/Mods/Yours"})

    assert cfg.mod_source == Path("/mnt/c/Mods/Yours")
    assert cfg.mod_source_win == r"C:\Mods\Yours"
    assert "Biomancy" not in (cfg.mod_source_win or "")


def test_an_explicit_windows_path_still_wins():
    """Positive control for the derivation: a caller who sets it is obeyed.

    Without this, a `load` that ignored the variable entirely would satisfy the
    test above and break every install whose source is not on a drive letter.
    """
    cfg = config.load(
        {
            "TMODLOADER_MOD_SOURCE": "/home/somebody/MyMod",
            "TMODLOADER_MOD_SOURCE_WIN": r"\\wsl.localhost\Ubuntu\home\somebody\MyMod",
        }
    )

    assert cfg.mod_source_win == r"\\wsl.localhost\Ubuntu\home\somebody\MyMod"


def test_a_windows_path_that_names_a_different_directory_is_reported(tmp_path):
    """Set both, disagreeing — the mismatch derivation cannot remove.

    Deriving stops the common case (override one, inherit the other). It cannot
    stop a caller from setting both to different places, so that is checked
    rather than assumed away.
    """
    source = tmp_path / "MyMod"
    source.mkdir()
    (source / "build.txt").write_text("version = 1.0\n")

    cfg = config.load(
        {
            "TMODLOADER_DIR": str(tmp_path),
            "TMODLOADER_SAVE_DIR": str(tmp_path),
            "TMODLOADER_MOD_SOURCE": f"/mnt/c/{source.relative_to(source.anchor)}",
            "TMODLOADER_MOD_SOURCE_WIN": r"C:\Somewhere\Else",
        }
    )

    problems = config.check(cfg)
    assert any("Somewhere\\Else" in p for p in problems), problems
    assert any("TMODLOADER_MOD_SOURCE_WIN" in p for p in problems), problems


def test_a_source_that_cannot_be_translated_asks_for_the_variable(tmp_path):
    source = tmp_path / "MyMod"
    source.mkdir()
    (source / "build.txt").write_text("version = 1.0\n")

    cfg = config.load(
        {
            "TMODLOADER_DIR": str(tmp_path),
            "TMODLOADER_SAVE_DIR": str(tmp_path),
            "TMODLOADER_MOD_SOURCE": str(source),
        }
    )

    assert cfg.mod_source_win is None
    assert any("TMODLOADER_MOD_SOURCE_WIN" in p for p in config.check(cfg))


def test_an_exported_but_empty_variable_falls_back_to_the_default():
    """`FOO=` is not `FOO` unset, and `os.environ.get(k, default)` cannot tell.

    An empty `TMODLOADER_DIR` resolved to `Path("")`, which is `Path(".")` — the
    working directory. `check` then found a directory that exists and reported
    "<cwd> exists but holds no tModLoader.dll", sending the reader to look at
    entirely the wrong place.
    """
    cfg = config.load({"TMODLOADER_DIR": "", "TMODLOADER_SAVE_DIR": "   "})

    assert cfg.tml_dir == Path(config.DEFAULT_TML)
    assert cfg.save_dir == Path(config.DEFAULT_SAVE)


def test_a_usable_config_reports_nothing(tmp_path):
    """Positive control for `check` itself.

    Every test above asserts that some problem IS reported. A `check` that
    returned a complaint unconditionally would pass all of them.
    """
    (tmp_path / "tModLoader.dll").write_text("")
    source = tmp_path / "MyMod"
    source.mkdir()
    (source / "build.txt").write_text("version = 1.0\n")

    cfg = config.load(
        {
            "TMODLOADER_DIR": str(tmp_path),
            "TMODLOADER_SAVE_DIR": str(tmp_path),
            "TMODLOADER_MOD_SOURCE": str(source),
            "TMODLOADER_MOD_SOURCE_WIN": r"C:\Mods\MyMod",
        }
    )

    assert config.check(cfg) == []
