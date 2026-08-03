"""Diag parsing. Pure text in, dict out — no game required.

These exist because the shell version of this parsing got two things wrong in
practice, and both looked like passing checks at the time.
"""

from __future__ import annotations

import pytest

from tmodloader_mcp import diag

# A real capture, trimmed. Kept verbatim rather than synthesised: a parser
# tested only against text the test author wrote will handle exactly the shapes
# the author remembered.
SAMPLE = """\
version: 0.7.0
tmod-md5: d6f3a15efc991e64522d9476d0ff2164
side: client netmode=1
player: n43n
npcs: active=2 mutated=1
  idx=0 type=37 name=Old Man mutated=0 mutation=None apex=0 life=250/250 at=770,231
  idx=1 type=22 name=Guide mutated=0 mutation=None apex=0 life=250/250 at=2171,253
cue-particles: 0
ambient-motes: 346
strain-readout: Zombie in Surface
creep-sources: 1
creep-tiles: 48
creep-drawn: 36096
strains: N/A (never sent to clients)
directive: N/A (never sent to clients)
"""


def test_scalars_are_parsed():
    got = diag.parse(SAMPLE)
    assert got["version"] == "0.7.0"
    assert got["player"] == "n43n"
    assert got["strain-readout"] == "Zombie in Surface"


def test_counters_are_ints_not_strings():
    """String counters are where "10" < "9" comes from."""
    got = diag.parse(SAMPLE)
    assert got["ambient-motes"] == 346
    assert got["creep-drawn"] == 36096
    assert isinstance(got["creep-tiles"], int)


def test_absent_markers_become_none():
    """`NONE` must not be readable as a value.

    A readout showing the literal string "NONE" and a readout showing nothing
    are the same bytes on disk; only one of them is a strain.
    """
    got = diag.parse(
        SAMPLE.replace("strain-readout: Zombie in Surface", "strain-readout: NONE")
    )
    assert got["strain-readout"] is None

    # And the two N/A forms, which mean "this side structurally cannot answer".
    assert got["strains"] is None
    assert got["directive"] is None


def test_zero_is_a_value_not_an_absence():
    """THE CONTROL THAT MATTERS.

    A counter reading 0 is a real measurement — "nothing was drawn" — and must
    not collapse to None alongside the absent markers. Conflating them is how a
    harness reports "no data" when the data says the feature did nothing.
    """
    got = diag.parse(SAMPLE)
    assert got["cue-particles"] == 0
    assert got["cue-particles"] is not None


def test_values_containing_colons_survive():
    """Paths have colons; splitting on every colon truncates them."""
    got = diag.parse("shot-path: C:\\Users\\a2b32\\biomancy-shot.png\n")
    assert got["shot-path"] == "C:\\Users\\a2b32\\biomancy-shot.png"


def test_unknown_keys_are_kept():
    """The mod gains diag lines faster than this parser learns them.

    Dropping unrecognised keys would make a newly added counter invisible here,
    which is indistinguishable from the mod never emitting it.
    """
    got = diag.parse(SAMPLE + "some-future-counter: 12\n")
    assert got["some-future-counter"] == "12"


def test_crlf_is_not_part_of_the_value():
    """These files are written by a Windows process and read from WSL."""
    got = diag.parse("player: n43n\r\nambient-motes: 5\r\n")
    assert got["player"] == "n43n"
    assert got["ambient-motes"] == 5


def test_a_non_numeric_counter_is_not_coerced_to_zero():
    """Coercing would read as "nothing happened", hiding the real signal."""
    got = diag.parse("ambient-motes: unavailable\n")
    assert got["ambient-motes"] == "unavailable"


def test_sections_collect_indented_records():
    got = diag.sections(SAMPLE)
    assert len(got["npcs"]) == 2
    assert got["npcs"][0].startswith("idx=0")


def test_sections_does_not_invent_a_section_for_scalars():
    """Positive control for the section parser: a scalar-only diag has none."""
    assert diag.sections("version: 0.7.0\nplayer: n43n\n") == {}


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("side: client netmode=1", "client"),
        ("side: singleplayer netmode=0", "singleplayer"),
        ("side: server netmode=2", "server"),
    ],
)
def test_side_is_read_not_inferred(line, expected):
    """Read from the mod's own account rather than derived from netmode.

    Deriving would duplicate a rule this server does not own, and the two would
    drift the moment the mod changed it.
    """
    assert diag.side_of(diag.parse(line + "\n")) == expected


def test_side_of_a_diag_without_one_is_unknown_not_a_guess():
    assert diag.side_of({}) == "unknown"
