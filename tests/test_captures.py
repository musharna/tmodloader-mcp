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

from pathlib import Path

import pytest

from tmodloader_mcp import captures

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def save_dir(tmp_path):
    """A save directory holding one capture and one thing that is not one."""
    (tmp_path / "biomancy-shot-001-topleft.png").write_bytes(PNG)
    (tmp_path / "biomancy-diag.txt").write_text("side: client netmode=1\n")
    return tmp_path


def test_a_capture_is_returned_as_bytes(save_dir):
    """POSITIVE CONTROL, and the point of the exercise.

    Every other test here asserts a refusal. Without this one, a reader that
    refused everything unconditionally would pass the lot.
    """
    assert captures.read(save_dir, "biomancy-shot-001-topleft.png") == PNG


def test_the_listing_shows_captures_and_nothing_else(save_dir):
    """`biomancy-diag.txt` is in the same directory and is not a capture."""
    assert captures.available(save_dir) == ["biomancy-shot-001-topleft.png"]


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
        captures.read(save_dir, escape)


def test_an_absolute_path_is_refused(save_dir, tmp_path):
    """`Path(dir) / "/etc/passwd"` is `/etc/passwd` — joining does not contain
    an absolute path, it surrenders to it."""
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(PNG)

    with pytest.raises(captures.CaptureError):
        captures.read(save_dir, str(outside))


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
    link = save_dir / "biomancy-shot-002-full.png"
    link.symlink_to(secret)

    with pytest.raises(captures.CaptureError):
        captures.read(save_dir, "biomancy-shot-002-full.png")


def test_a_file_in_the_save_dir_that_is_not_a_capture_is_refused(save_dir):
    """THE ONE ONLY THE NAME CHECK CATCHES — also verified by mutation.

    Being in the right directory is not enough: the save directory holds diag
    dumps, heartbeats, trigger files and the world. Containment passes this
    happily, because the file IS inside the save directory. Disable the name
    check and this fails alone.
    """
    with pytest.raises(captures.CaptureError) as e:
        captures.read(save_dir, "biomancy-diag.txt")

    assert "capture" in str(e.value).lower()


def test_a_capture_that_is_not_there_says_so_rather_than_pretending(save_dir):
    with pytest.raises(captures.CaptureError) as e:
        captures.read(save_dir, "biomancy-shot-404-full.png")

    assert "biomancy-shot-404-full.png" in str(e.value)


def test_the_refusals_are_not_all_the_same_check(save_dir):
    """A guard that rejected everything for one reason would satisfy every test
    above while being wrong about why. Each refusal names its own cause."""
    reasons = set()
    for name in ["../outside.png", "biomancy-diag.txt", "biomancy-shot-404-x.png"]:
        try:
            captures.read(save_dir, name)
        except captures.CaptureError as e:
            reasons.add(str(e))

    assert len(reasons) == 3, reasons


def test_reading_does_not_depend_on_the_save_dir_being_absolute(tmp_path, monkeypatch):
    """A relative cwd must not defeat the containment check."""
    (tmp_path / "biomancy-shot-001-full.png").write_bytes(PNG)
    monkeypatch.chdir(tmp_path)

    assert captures.read(Path("."), "biomancy-shot-001-full.png") == PNG
