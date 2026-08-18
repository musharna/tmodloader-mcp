"""Reading a capture back, and refusing to read anything else.

`shot` returns a filesystem path, which is useless to an agent that is not on
this machine. Handing back the BYTES fixes that and introduces a worse problem
if it is done naively: a tool that reads an arbitrary path and returns its
contents is a file-exfiltration primitive with an MCP interface.

That is not hypothetical here. This project exists because OS-level screen
capture returned a picture of a Teams inbox and a Discord friend list. The whole
design is "the game can only hand back what the game rendered", and a reader
that will open any path on request throws that away from the other end.

So the tests that matter are the refusals, and each names what it is refusing.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tmodloader_mcp import captures

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def save_dir(tmp_path):
    """A save directory holding one capture and one thing that is not one."""
    (tmp_path / "biomancy-shot-n43n-003f-001-topleft.png").write_bytes(PNG)
    (tmp_path / "biomancy-diag.txt").write_text("side: client netmode=1\n")
    return tmp_path


def test_a_capture_is_returned_as_bytes(save_dir):
    """POSITIVE CONTROL, and the point of the exercise.

    Every other test here asserts a refusal. Without this one, a reader that
    refused everything unconditionally would pass the lot.
    """
    assert (
        captures.read(save_dir, "Biomancy", "biomancy-shot-n43n-003f-001-topleft.png")
        == PNG
    )


def test_the_listing_shows_captures_and_nothing_else(save_dir):
    """`biomancy-diag.txt` is in the same directory and is not a capture."""
    assert captures.available(save_dir, "Biomancy") == [
        "biomancy-shot-n43n-003f-001-topleft.png"
    ]


@pytest.mark.parametrize(
    "escape",
    [
        "../../../etc/passwd",
        "..\\..\\Windows\\System32\\config\\SAM",
        "subdir/../../outside.png",
    ],
)
def test_a_traversal_out_of_the_save_directory_is_refused(save_dir, escape):
    """Covered TWICE — and mutation is how I know, rather than assuming.

    Disabling EITHER guard alone leaves this test passing: the name shape
    rejects it first (a capture name cannot contain a separator), and the
    resolved-parent check would reject it regardless. Defence in depth, which
    means this test alone proves neither guard is alive. The two below are what
    pin them individually.
    """
    with pytest.raises(captures.CaptureError):
        captures.read(save_dir, "Biomancy", escape)


def test_an_absolute_path_is_refused(save_dir, tmp_path):
    """`Path(dir) / "/etc/passwd"` is `/etc/passwd` — joining does not contain
    an absolute path, it surrenders to it."""
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(PNG)

    with pytest.raises(captures.CaptureError):
        captures.read(save_dir, "Biomancy", str(outside))


def test_a_symlink_pointing_out_is_refused(save_dir, tmp_path):
    """THE ONE ONLY CONTAINMENT CATCHES — verified by mutation.

    The name is perfectly capture-shaped, it really does sit in the save
    directory, and it resolves somewhere else entirely. It contains no `..` for
    a string check to find. Disable the resolved-parent comparison and this is
    the ONLY test in the file that fails, which is what makes that comparison
    load-bearing rather than decorative.
    """
    secret = tmp_path.parent / "secret.png"
    secret.write_bytes(b"not yours")
    link = save_dir / "biomancy-shot-n43n-003f-002-full.png"
    link.symlink_to(secret)

    with pytest.raises(captures.CaptureError):
        captures.read(save_dir, "Biomancy", "biomancy-shot-n43n-003f-002-full.png")


def test_a_file_in_the_save_dir_that_is_not_a_capture_is_refused(save_dir):
    """THE ONE ONLY THE NAME CHECK CATCHES — also verified by mutation.

    Being in the right directory is not enough: the save directory holds diag
    dumps, heartbeats, trigger files and the world. Containment passes this
    happily, because the file IS inside the save directory. Disable the name
    check and this fails alone.
    """
    with pytest.raises(captures.CaptureError) as e:
        captures.read(save_dir, "Biomancy", "biomancy-diag.txt")

    assert "capture" in str(e.value).lower()


def test_a_capture_that_is_not_there_says_so_rather_than_pretending(save_dir):
    with pytest.raises(captures.CaptureError) as e:
        captures.read(save_dir, "Biomancy", "biomancy-shot-n43n-003f-404-full.png")

    assert "biomancy-shot-n43n-003f-404-full.png" in str(e.value)


def test_the_refusals_are_not_all_the_same_check(save_dir):
    """A guard that rejected everything for one reason would satisfy every test
    above while being wrong about why. Each refusal names its own cause."""
    reasons = set()
    for name in [
        "../outside.png",
        "biomancy-diag.txt",
        "biomancy-shot-n43n-003f-404-x.png",
    ]:
        try:
            captures.read(save_dir, "Biomancy", name)
        except captures.CaptureError as e:
            reasons.add(str(e))

    assert len(reasons) == 3, reasons


def test_reading_does_not_depend_on_the_save_dir_being_absolute(tmp_path, monkeypatch):
    """A relative cwd must not defeat the containment check."""
    (tmp_path / "biomancy-shot-n43n-003f-001-full.png").write_bytes(PNG)
    monkeypatch.chdir(tmp_path)

    assert (
        captures.read(Path("."), "Biomancy", "biomancy-shot-n43n-003f-001-full.png")
        == PNG
    )


# --- Pruning. This DELETES, in the user's save directory. -------------------


def _shot(save, index, region="full", mod="biomancy", age=0.0, token="n43n-003f"):
    """A capture on disk with a genuinely older mtime, not a patched clock."""
    p = save / f"{mod}-shot-{token}-{index:03d}-{region}.png"
    p.write_bytes(b"\x89PNG")
    if age:
        stamp = time.time() - age
        os.utime(p, (stamp, stamp))
    return p


def test_pruning_keeps_the_newest_and_removes_the_rest(tmp_path):
    for i, age in enumerate([500, 400, 300, 200, 100], start=1):
        _shot(tmp_path, i, age=age)

    removed = captures.prune(tmp_path, "Biomancy", keep=2)

    assert removed == [
        "biomancy-shot-n43n-003f-001-full.png",
        "biomancy-shot-n43n-003f-002-full.png",
        "biomancy-shot-n43n-003f-003-full.png",
    ]
    assert captures.available(tmp_path, "Biomancy") == [
        "biomancy-shot-n43n-003f-004-full.png",
        "biomancy-shot-n43n-003f-005-full.png",
    ]


def test_pruning_orders_by_mtime_not_by_the_index_in_the_name(tmp_path):
    """The index is OUR counter; the mtime is what the disk saw happen.

    Written so the two orders DISAGREE: the highest-numbered capture is the
    oldest file. An implementation sorting on the name keeps 003 and deletes
    the freshest one, which is the opposite of what was asked.
    """
    _shot(tmp_path, 1, age=10)
    _shot(tmp_path, 2, age=20)
    _shot(tmp_path, 3, age=900)

    removed = captures.prune(tmp_path, "Biomancy", keep=1)

    assert captures.available(tmp_path, "Biomancy") == [
        "biomancy-shot-n43n-003f-001-full.png"
    ]
    assert "biomancy-shot-n43n-003f-003-full.png" in removed


def test_pruning_never_touches_anything_that_is_not_a_capture(tmp_path):
    """THE ONE THAT MATTERS. This runs in a directory holding worlds.

    The save directory is not a cache — it holds the user's worlds, characters,
    diag dumps and heartbeats, plus any OTHER mod's captures. A prune that
    globbed loosely, or matched on `.png`, would delete someone's save file and
    report a tidy number.
    """
    (tmp_path / "Worlds").mkdir()
    (tmp_path / "Worlds" / "Long_Nooch.wld").write_bytes(b"world")
    (tmp_path / "biomancy-diag.txt").write_text("diag")
    (tmp_path / "biomancy-hooks.txt").write_text("heartbeat")
    (tmp_path / "screenshot.png").write_bytes(b"not ours")
    (tmp_path / "othermod-shot-001-full.png").write_bytes(b"another mod's")
    _shot(tmp_path, 1, age=100)
    _shot(tmp_path, 2, age=50)

    removed = captures.prune(tmp_path, "Biomancy", keep=0)

    assert removed == [
        "biomancy-shot-n43n-003f-001-full.png",
        "biomancy-shot-n43n-003f-002-full.png",
    ]
    # POSITIVE CONTROL: everything else is still there. Asserting only that the
    # captures went would pass on an implementation that deleted the lot.
    assert (tmp_path / "Worlds" / "Long_Nooch.wld").read_bytes() == b"world"
    assert (tmp_path / "biomancy-diag.txt").is_file()
    assert (tmp_path / "biomancy-hooks.txt").is_file()
    assert (tmp_path / "screenshot.png").is_file()
    assert (tmp_path / "othermod-shot-001-full.png").is_file()


def test_keeping_more_than_exist_deletes_nothing(tmp_path):
    _shot(tmp_path, 1)
    _shot(tmp_path, 2)

    assert captures.prune(tmp_path, "Biomancy", keep=10) == []
    assert len(captures.available(tmp_path, "Biomancy")) == 2


def test_a_negative_keep_is_refused_rather_than_treated_as_zero(tmp_path):
    """`found[:-(-1)]` is `found[:1]`, which silently deletes the wrong set.

    Negative slicing makes this a real hazard rather than a theoretical one: it
    does not crash, it deletes a plausible-looking number of files.
    """
    _shot(tmp_path, 1)

    with pytest.raises(captures.CaptureError, match="keep must be 0 or more"):
        captures.prune(tmp_path, "Biomancy", keep=-1)

    assert captures.available(tmp_path, "Biomancy") == [
        "biomancy-shot-n43n-003f-001-full.png"
    ]


def test_pruning_an_absent_directory_is_empty_not_a_crash(tmp_path):
    assert captures.prune(tmp_path / "nope", "Biomancy", keep=0) == []


def test_pruning_serves_one_mod_and_not_its_neighbour(tmp_path):
    """Two mods share one save directory, which is why the pattern takes a name."""
    _shot(tmp_path, 1, mod="biomancy")
    _shot(tmp_path, 1, mod="othermod")

    assert captures.prune(tmp_path, "Othermod", keep=0) == [
        "othermod-shot-n43n-003f-001-full.png"
    ]
    assert captures.available(tmp_path, "Biomancy") == [
        "biomancy-shot-n43n-003f-001-full.png"
    ]


def test_a_capture_shaped_symlink_out_of_the_directory_stops_the_prune(tmp_path):
    """The delete path gets the same containment the read path has.

    A symlink named exactly like a capture passes the name pattern and really
    does sit in the save directory; only resolving it shows it points somewhere
    else. `read` has refused this since it was written, and a `prune` that
    called `unlink` on whatever `iterdir` handed back would not.

    Nothing is deleted, because validation runs over the whole set before the
    first unlink — a refusal halfway through would leave some files gone, an
    exception raised, and no record of where it stopped.
    """
    outside = tmp_path.parent / "not-a-capture.png"
    outside.write_bytes(b"someone else's file")
    save = tmp_path / "save"
    save.mkdir()
    real = _shot(save, 1, age=100)
    (save / "biomancy-shot-n43n-003f-002-full.png").symlink_to(outside)

    with pytest.raises(captures.CaptureError, match="outside"):
        captures.prune(save, "Biomancy", keep=0)

    # POSITIVE CONTROL on both sides: the link's target survives, and so does
    # the legitimate capture that would otherwise have been deleted first.
    assert outside.read_bytes() == b"someone else's file"
    assert real.is_file(), "a refusal must not leave a half-finished prune"


from tmodloader_mcp.captures import capture_pattern


def test_a_per_player_capture_matches():
    assert capture_pattern("Biomancy").match("biomancy-shot-n43n-003f-001-full.png")


def test_the_token_cannot_swallow_the_index():
    """The grammar is pinned rather than open for exactly this.

    `[a-z0-9-]+` alone is greedy across dashes AND digits, so a token pattern
    that did not end in four hex could absorb `-001` and leave the region to
    stand in for the index. The four-hex tail is what makes the boundary
    findable.
    """
    m = capture_pattern("Biomancy").match(
        "biomancy-shot-big-bird-44a3-012-topright.png"
    )
    assert m

    # The positive control: a name that is NOT a valid token is refused, so
    # the test above cannot be passing because the pattern matches anything.
    assert not capture_pattern("Biomancy").match(
        "biomancy-shot-nothex-zzzz-012-topright.png"
    )


def test_an_unanchored_lookalike_is_still_refused():
    # The reason the docstring gives for anchoring both ends, re-asserted
    # against the new shape.
    assert not capture_pattern("Biomancy").match(
        "evil-biomancy-shot-n43n-003f-001-full.png"
    )
    assert not capture_pattern("Biomancy").match(
        "biomancy-shot-n43n-003f-001-full.png.exe"
    )


def test_another_mods_capture_is_not_ours():
    assert not capture_pattern("Biomancy").match("othermod-shot-n43n-003f-001-full.png")


def test_pruning_tolerates_a_file_the_other_session_deleted_first(
    tmp_path, monkeypatch
):
    """Two sessions share a save directory and prune without coordinating. A
    file vanishing between the listing and the unlink used to fail the whole
    prune three separate ways - the sort's stat, the pre-validation's
    existence check, and the unlink itself - each turning finished work into
    a spurious error."""
    doomed = _shot(tmp_path, 1, age=500)
    _shot(tmp_path, 2, age=400)
    _shot(tmp_path, 3, age=100)

    real_unlink = Path.unlink

    def other_pruner_wins(self, missing_ok=False):
        # The other session removes the oldest file just as this prune starts
        # acting - after the listing, before this unlink.
        if self == doomed and doomed.exists():
            real_unlink(doomed)
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", other_pruner_wins)

    removed = captures.prune(tmp_path, "Biomancy", keep=1)

    assert "biomancy-shot-n43n-003f-001-full.png" in removed
    assert captures.available(tmp_path, "Biomancy") == [
        "biomancy-shot-n43n-003f-003-full.png"
    ]


def test_a_vanished_file_does_not_weaken_the_containment_check(tmp_path):
    """`missing_ok` relaxes ONLY absence. A name that is not a capture, or
    one that escapes, is refused exactly as the read refuses it - a prune
    tolerant of races must not become tolerant of traversal."""
    with pytest.raises(captures.CaptureError, match="not a capture name"):
        captures.contained(tmp_path, "Biomancy", "../../escape.png", missing_ok=True)
