"""Listing the install. Real directories in tmp_path, no game and no mocks.

The fixture mirrors the REAL save directory read on 2026-08-09, decoys and
all — `.twld` companions, `.bak`/`.bak2` copies, a `Backups/` folder, and a
mod that is enabled without being built here. Every one of those is a shape a
too-loose implementation would return, so leaving them out would make the
tests agree with a broken listing.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from tmodloader_mcp import inventory


@pytest.fixture
def save(tmp_path):
    worlds = tmp_path / "Worlds"
    worlds.mkdir()
    for name in ("BiomancySelfTest", "Long_Nooch", "The_Miasma_of_Grief"):
        (worlds / f"{name}.wld").write_bytes(b"")
        # The companion tModLoader file, and the backups. None of these is a
        # world you can launch, and all three end in something world-shaped.
        (worlds / f"{name}.twld").write_bytes(b"")
        (worlds / f"{name}.wld.bak").write_bytes(b"")
        (worlds / f"{name}.twld.bak").write_bytes(b"")
    (worlds / "The_Miasma_of_Grief.wld.bak2").write_bytes(b"")
    (worlds / "Backups").mkdir()

    players = tmp_path / "Players"
    players.mkdir()
    for name in ("n43n", "tst2"):
        (players / f"{name}.plr").write_bytes(b"")
        (players / f"{name}.tplr").write_bytes(b"")
        (players / f"{name}.plr.bak").write_bytes(b"")
        # tModLoader keeps a per-character DIRECTORY beside the .plr.
        (players / name).mkdir()
    (players / "Backups").mkdir()

    mods = tmp_path / "Mods"
    mods.mkdir()
    (mods / "Biomancy.tmod").write_bytes(b"")
    # Enabled but NOT built here - a workshop mod lives somewhere else. This is
    # the real state of the install this was written against.
    (mods / "enabled.json").write_text(json.dumps(["Biomancy", "CheatSheet"]))

    return tmp_path


def test_worlds_are_worlds_and_not_their_backups(save):
    """A looser pattern offers `The_Miasma_of_Grief.wld.bak` as launchable.

    Thirteen files in that directory, three of which are worlds.
    """
    names = [w.name for w in inventory.worlds(save)]
    assert sorted(names) == [
        "BiomancySelfTest",
        "Long_Nooch",
        "The_Miasma_of_Grief",
    ]


def test_worlds_are_newest_first(save):
    """The one you were just working in is the one you want offered first."""
    old = save / "Worlds" / "Long_Nooch.wld"
    stamp = time.time() - 90_000
    os.utime(old, (stamp, stamp))

    assert inventory.worlds(save)[-1].name == "Long_Nooch"


def test_a_world_off_a_drive_mount_reports_no_windows_path(save):
    """None means "I do not know", which is the honest answer off `/mnt/<drive>`.

    `launch` needs the WINDOWS spelling because tModLoader runs as a Windows
    process. Inventing a UNC path here would ship a spelling nobody has
    measured — `config.windows_path_for` refuses that and so does this. The
    translation itself is tested in `test_world_path.py`.
    """
    assert all(w.path_win is None for w in inventory.worlds(save)), (
        "a tmp_path is not under /mnt/<drive>, so nothing here is translatable"
    )


def test_players_are_characters_not_backups_or_folders(save):
    """Ten entries in `Players/`, two of which are characters `launch` accepts."""
    assert inventory.players(save) == ["n43n", "tst2"]


def test_enabled_and_built_here_are_different_facts(save):
    """THE DISTINCTION THIS TOOL EXISTS FOR.

    CheatSheet is enabled and has no `.tmod` in `Mods/`, because a workshop mod
    is installed from somewhere else. Collapsing these into one `installed`
    flag would report it missing, and send someone rebuilding a mod that was
    never the problem.
    """
    got = {m.name: m for m in inventory.mods(save)}

    assert got["Biomancy"].enabled is True
    assert got["Biomancy"].built_here is True
    assert got["CheatSheet"].enabled is True
    assert got["CheatSheet"].built_here is False


def test_a_mod_built_here_but_not_enabled_is_still_listed(save):
    """The `responder: false` case worth naming: it compiled and is switched off.

    A list of only the enabled ones cannot express this, and it is the state
    `build_mod` leaves behind on a first build.
    """
    (save / "Mods" / "Fakemod.tmod").write_bytes(b"")

    got = {m.name: m for m in inventory.mods(save)}
    assert got["Fakemod"].built_here is True
    assert got["Fakemod"].enabled is False


def test_a_missing_manifest_is_an_empty_list_not_a_failure(save):
    """A fresh install has no `enabled.json`. That is a state, not a fault."""
    (save / "Mods" / "enabled.json").unlink()

    got = {m.name: m for m in inventory.mods(save)}
    assert got["Biomancy"].built_here is True
    assert got["Biomancy"].enabled is False


def test_a_manifest_that_exists_and_will_not_parse_is_an_error(save):
    """Absent is a state; CORRUPT is a fault, and they need different actions.

    The same line `commands` draws: nothing published a list is `responder:
    false`, and a list that exists and cannot be read is a raise, because it
    means something wrote a file this cannot understand.
    """
    manifest = save / "Mods" / "enabled.json"
    manifest.write_text("[[[not json")

    with pytest.raises(inventory.InventoryError, match="not valid JSON"):
        inventory.mods(save)

    # POSITIVE CONTROL, in the same test: the reader is not simply broken.
    # Without this, an implementation that raised on every manifest would pass.
    manifest.write_text(json.dumps(["Biomancy"]))
    assert [m.name for m in inventory.mods(save)] == ["Biomancy"]


def test_a_manifest_of_the_wrong_shape_is_an_error(save):
    """`{"Biomancy": true}` is valid JSON and not a mod list."""
    (save / "Mods" / "enabled.json").write_text(json.dumps({"Biomancy": True}))

    with pytest.raises(inventory.InventoryError, match="expected a list"):
        inventory.mods(save)


def test_an_absent_directory_is_empty_rather_than_a_crash(tmp_path):
    """A save directory with none of the three subfolders still answers."""
    assert inventory.worlds(tmp_path) == []
    assert inventory.players(tmp_path) == []
    assert inventory.mods(tmp_path) == []
