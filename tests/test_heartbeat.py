"""Heartbeat reading: which silence, not just whether there was one.

Real files on disk with real mtimes throughout — `os.utime` rather than a
patched clock, because the staleness rule reads `st_mtime` and a fake clock
would test the fake. The one thing mocked is nothing.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tmodloader_mcp import heartbeat

# Copied verbatim off the install on 2026-08-09 (`biomancy-hooks.txt`), not
# composed from memory. A parser tested only against text its author wrote
# handles exactly the shapes its author remembered.
CLIENT = """\
hooks-seen: PostUpdateEverything,PostUpdateInput,UpdateUI
gameMenu: False
dedServ: False
savepath: C:\\Users\\someone\\Documents\\My Games\\Terraria\\tModLoader
trigger-path: C:\\Users\\someone\\Documents\\My Games\\Terraria\\tModLoader\\biomancy-capture.trigger
trigger-exists: False
world-ready: True
capture-ready: True
armed: True
polls: 194
written: 20:19:07Z
"""

# The same run's server side. `dedServ: True` and `capture-ready: False` — a
# server cannot photograph anything, which is a real difference between the
# sides and not an error.
SERVER = """\
hooks-seen: PostUpdateEverything,PostUpdateWorld
gameMenu: False
dedServ: True
savepath: C:\\Users\\someone\\Documents\\My Games\\Terraria\\tModLoader
trigger-exists: False
world-ready: True
capture-ready: False
armed: True
polls: 120
written: 20:19:04Z
"""


def _write(tmp_path, text, *, age=0.0, name="hooks.txt"):
    """A heartbeat file that is genuinely `age` seconds old."""
    p = tmp_path / name
    p.write_text(text)
    if age:
        stamp = time.time() - age
        os.utime(p, (stamp, stamp))
    return p


def test_an_absent_heartbeat_is_an_answer_not_an_error():
    """THE MOST INFORMATIVE READING THIS CAN PRODUCE.

    No file means nothing ever wrote one: the mod is not loaded, not enabled,
    or built without the dev bridge. Raising here would turn the clearest
    diagnosis available into a failure to produce one.
    """
    hb = heartbeat.read(Path("/nonexistent/hooks.txt"))
    assert hb.present is False
    assert hb.live is False
    assert hb.age is None
    assert hb.fields == {}
    assert "not loaded" in heartbeat.diagnose(hb)


def test_a_stale_heartbeat_is_distinguished_from_a_live_one(tmp_path):
    """The file SURVIVES THE PROCESS.

    Existence is not liveness — a dead game leaves a heartbeat that reads
    exactly like a running one to anything that only asks whether it is there.
    """
    fresh = heartbeat.read(_write(tmp_path, CLIENT, name="fresh.txt"))
    stale = heartbeat.read(_write(tmp_path, CLIENT, age=600, name="stale.txt"))

    assert fresh.live is True
    assert stale.live is False
    # POSITIVE CONTROL: the stale one is fully present and parsed. If this
    # asserted only `live is False`, a read that returned nothing at all would
    # pass it while telling the caller nothing.
    assert stale.present is True
    assert stale.fields["polls"] == 194
    assert stale.age is not None and stale.age > 500
    assert "stale" in heartbeat.diagnose(stale)


def test_the_side_comes_from_the_mod_not_the_filename(tmp_path):
    """`dedServ` is the mod's statement; the filename is our convention.

    Written to deliberately MISMATCHING names, because a `side_of` that read
    the path would pass a test where the two agree and be wrong in the only
    case that distinguishes them.
    """
    client = heartbeat.read(_write(tmp_path, CLIENT, name="hooks-server.txt"))
    server = heartbeat.read(_write(tmp_path, SERVER, name="hooks.txt"))

    assert client.side == "client"
    assert server.side == "server"


def test_the_booleans_are_booleans(tmp_path):
    """Pins the parser change. `"False"` is truthy; six fields here are bools."""
    hb = heartbeat.read(_write(tmp_path, CLIENT))
    assert hb.fields["armed"] is True
    assert hb.fields["trigger-exists"] is False
    assert hb.fields["gameMenu"] is False
    assert hb.world_ready is True
    assert hb.armed is True


def test_capture_ready_differs_between_the_sides(tmp_path):
    """A server cannot photograph anything. Real difference, not an error."""
    client = heartbeat.read(_write(tmp_path, CLIENT, name="c.txt"))
    server = heartbeat.read(_write(tmp_path, SERVER, name="s.txt"))
    assert client.fields["capture-ready"] is True
    assert server.fields["capture-ready"] is False


@pytest.mark.parametrize(
    "edit,expected",
    [
        ("world-ready: True", "can answer"),
        ("world-ready: False", "still starting up"),
        ("armed: False", "not armed"),
    ],
)
def test_diagnose_names_each_silence_separately(tmp_path, edit, expected):
    """Four causes that need four different actions.

    They all arrived as one sentence about a timeout, which is why the fields
    are reported and not just consulted.
    """
    key = edit.split(":")[0]
    text = "\n".join(
        edit if line.startswith(f"{key}:") else line
        for line in CLIENT.strip().split("\n")
    )
    hb = heartbeat.read(_write(tmp_path, text + "\n"))
    assert expected in heartbeat.diagnose(hb)


def test_an_unreadable_heartbeat_does_not_invent_a_fresh_one(tmp_path):
    """A made-up age of 0.0 would read as the freshest possible heartbeat.

    The failure is forced by pointing the reader at a DIRECTORY, which exists
    and is not a file — `is_file()` says no, so this lands on the absent path
    rather than pretending to a reading it never took.
    """
    d = tmp_path / "hooks.txt"
    d.mkdir()
    hb = heartbeat.read(d)
    assert hb.present is False
    assert hb.age is None


def test_it_finds_one_file_per_client(tmp_path):
    (tmp_path / "biomancy-hooks-n43n-003f.txt").write_text("side: client\n")
    (tmp_path / "biomancy-hooks-big-bird-44a3.txt").write_text("side: client\n")
    found = heartbeat.client_files(tmp_path, "biomancy")
    assert sorted(p.name for p in found) == [
        "biomancy-hooks-big-bird-44a3.txt",
        "biomancy-hooks-n43n-003f.txt",
    ]


def test_an_unsuffixed_heartbeat_is_a_client_with_no_character_yet(tmp_path):
    """The one place the old name survives, carrying a new meaning.

    `launch` tells "live, no world" from "absent" by reading a heartbeat
    written BEFORE a world is up — and before a world is up there is no
    LocalPlayer to build a token from. Dropping this file would make the
    harness blind at exactly the moment the heartbeat matters most.
    """
    (tmp_path / "biomancy-hooks.txt").write_text("side: client\n")
    found = heartbeat.client_files(tmp_path, "biomancy")
    assert [p.name for p in found] == ["biomancy-hooks.txt"]


def test_the_servers_file_is_not_mistaken_for_a_clients(tmp_path):
    # `-server` is a side suffix, not a player token, and a glob loose enough
    # to catch it would report the dedicated server as a client.
    (tmp_path / "biomancy-hooks-server.txt").write_text("side: server\n")
    (tmp_path / "biomancy-hooks-n43n-003f.txt").write_text("side: client\n")
    found = heartbeat.client_files(tmp_path, "biomancy")
    # Positive control in the same test: the real client IS found, so this
    # cannot pass by finding nothing at all.
    assert [p.name for p in found] == ["biomancy-hooks-n43n-003f.txt"]


def test_another_mods_heartbeat_is_not_ours(tmp_path):
    (tmp_path / "othermod-hooks-n43n-003f.txt").write_text("side: client\n")
    assert heartbeat.client_files(tmp_path, "biomancy") == []


def test_a_file_deleted_between_the_stat_and_the_read_is_present_but_unreadable(
    tmp_path, monkeypatch
):
    """Another launch's stale-artifact clear can take the unsuffixed slot in
    exactly this window. The stat's guard already named the right answer for
    a file that stops cooperating mid-read; the read used to escape as a raw
    FileNotFoundError out of a five-minute launch instead of giving it."""
    path = tmp_path / "biomancy-hooks.txt"
    path.write_text("dedServ: False\nworld-ready: True\n")

    real_read = Path.read_text

    def vanishes(self, *args, **kwargs):
        if self == path:
            raise FileNotFoundError(path)
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", vanishes)

    hb = heartbeat.read(path)

    assert hb.present and not hb.live and hb.fields == {}
