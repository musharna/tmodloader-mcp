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
from plr_fixture import PLR_KEY, encrypt_block, plr_bytes

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
        # Encrypted for real: a listing that never opens the file passes on an
        # empty one, and an empty `.plr` is not a character.
        (players / f"{name}.plr").write_bytes(plr_bytes(name))
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
    assert [(p.file, p.name) for p in inventory.players(save)] == [
        ("n43n", "n43n"),
        ("tst2", "tst2"),
    ]


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


# ---- what is INSIDE a .plr --------------------------------------------------
#
# `launch(player=...)` passes `-player <name>`, and tModLoader matches the name
# stored inside the encrypted file. Measured on a real install, `Betula.plr`
# holds `Betula_Calamity`. The files below are encrypted by `plr_fixture`,
# which shares no code with the reader.

FIPS197_KEY = bytes(range(16))
FIPS197_PLAIN = bytes.fromhex("00112233445566778899aabbccddeeff")
FIPS197_CIPHER = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")


def test_the_block_cipher_matches_the_published_vector():
    """FIPS-197 Appendix C.1, both directions.

    The reader's decryption and the test-side encryption are pinned to the
    same published vector, so the round-trip tests below cannot pass on two
    implementations that are wrong in the same way.
    """
    assert inventory.aes128_cbc_decrypt(FIPS197_KEY, bytes(16), FIPS197_CIPHER) == (
        FIPS197_PLAIN
    )
    assert encrypt_block(FIPS197_KEY, FIPS197_PLAIN) == FIPS197_CIPHER


def test_the_name_is_read_from_inside_the_file_not_the_file_name(save):
    """THE BUG: a stem-listing offered `Betula`, which `launch` cannot use.

    The real install's `Betula.plr` holds a character named `Betula_Calamity`.
    `name` is what `-player` matches; `file` is kept beside it so a caller can
    still find the file.
    """
    (save / "Players" / "Betula.plr").write_bytes(plr_bytes("Betula_Calamity"))

    got = {p.file: p for p in inventory.players(save)}

    assert got["Betula"].name == "Betula_Calamity"
    assert got["Betula"].error is None
    # And the files whose names do agree still read as themselves.
    assert got["n43n"].name == "n43n"


@pytest.mark.parametrize(
    ("byte", "label"),
    [(0, "classic"), (1, "mediumcore"), (2, "hardcore"), (3, "journey"), (9, None)],
)
def test_difficulty_is_read_from_the_byte_after_the_name(tmp_path, byte, label):
    """A journey character cannot join a non-journey world, so it is reported.

    An unknown byte is None rather than a guess; the name is still right.
    """
    (tmp_path / "Players").mkdir()
    (tmp_path / "Players" / "x.plr").write_bytes(plr_bytes("Someone", byte))

    [got] = inventory.players(tmp_path)
    assert (got.name, got.difficulty, got.error) == ("Someone", label, None)


def test_names_beyond_ascii_and_beyond_a_one_byte_length_decode(tmp_path):
    """The real install has `Betula-瑟银withDarkrai`. UTF-8, length-prefixed.

    A 130-byte name needs a two-byte 7-bit length; a reader that takes the
    first byte as the length reads 2 bytes of it and calls that the name.
    """
    long_name = "n" * 130
    (tmp_path / "Players").mkdir()
    (tmp_path / "Players" / "a.plr").write_bytes(plr_bytes("Betula-瑟银withHu0", 3))
    (tmp_path / "Players" / "b.plr").write_bytes(plr_bytes(long_name))

    got = {p.file: p for p in inventory.players(tmp_path)}
    assert got["a"].name == "Betula-瑟银withHu0"
    assert got["a"].difficulty == "journey"
    assert got["b"].name == long_name


def test_an_unreadable_character_is_listed_with_why_not_dropped(tmp_path):
    """Absent and unreadable need different fixes, so both are reported.

    Each of these is a way a `.plr` can be there and not be a character this
    reader understands: empty, not encrypted, encrypted with another key,
    another kind of Terraria file, a header cut off inside the name, and a
    directory where the file should be (a real OSError, not a mock).
    """
    players = tmp_path / "Players"
    players.mkdir()
    (players / "empty.plr").write_bytes(b"")
    (players / "plain.plr").write_bytes(b"relogic" * 80)
    (players / "otherkey.plr").write_bytes(plr_bytes("Ghost", key=b"0123456789abcdef"))
    (players / "aworld.plr").write_bytes(plr_bytes("Ghost", file_type=2))
    (players / "cut.plr").write_bytes(plr_bytes("n" * 30)[:32])
    (players / "folder.plr").mkdir()
    # POSITIVE CONTROL, in the same directory: the reader is not simply broken,
    # so the errors above are about those files.
    (players / "good.plr").write_bytes(plr_bytes("Good"))

    got = {p.file: p for p in inventory.players(tmp_path)}

    assert set(got) == {"empty", "plain", "otherkey", "aworld", "cut", "folder", "good"}
    for stem in ("empty", "plain", "otherkey", "aworld", "cut", "folder"):
        assert got[stem].name is None, f"{stem}.plr was given a name"
        assert got[stem].difficulty is None
        assert got[stem].error and f"{stem}.plr" in got[stem].error, got[stem]
    assert "magic" in got["otherkey"].error
    assert "magic" in got["aworld"].error
    assert "does not fit" in got["cut"].error
    assert "cannot read" in got["folder"].error
    assert (got["good"].name, got["good"].error) == ("Good", None)


def test_the_reader_uses_terrarias_key(tmp_path):
    """The key is a constant of the game, not of this module: pin its bytes."""
    assert PLR_KEY == b"h\x003\x00y\x00_\x00g\x00U\x00y\x00Z\x00"
    (tmp_path / "Players").mkdir()
    (tmp_path / "Players" / "k.plr").write_bytes(plr_bytes("Keyed", key=PLR_KEY))
    assert inventory.players(tmp_path)[0].name == "Keyed"
