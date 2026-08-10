"""Diag parsing. Pure text in, dict out — no game required.

These exist because the shell version of this parsing got two things wrong in
practice, and both looked like passing checks at the time.
"""

from __future__ import annotations

import pytest

from tmodloader_mcp import diag

# Line-for-line the shape `DiagReport.Format` emits, with the creep values taken
# from a real 0.8.1 capture (the `probe_verify` run of 2026-08-04). Not composed
# from memory: a parser tested only against text the test author wrote will
# handle exactly the shapes the author remembered.
SAMPLE = """\
version: 0.8.1
tmod-md5: d6f3a15efc991e64522d9476d0ff2164
side: client netmode=1
player: n43n
npcs: active=2 mutated=1
  idx=0 type=37 name=Old Man mutated=0 mutation=None apex=0 life=250/250 at=770,231
  idx=1 type=22 name=Guide mutated=0 mutation=None apex=0 life=250/250 at=2171,253
cue-particles: 0
ambient-motes: 346
creep-sources: 1
creep-tiles: 47
creep-converted: 56
creep-census: 52
creep-residue: 0=35 2=12 706=5 85=2 3=1
strain-readout: Zombie in Surface
strains: N/A (never sent to clients)
directive: N/A (never sent to clients)
"""


def test_scalars_are_parsed():
    got = diag.parse(SAMPLE)
    assert got["version"] == "0.8.1"
    assert got["player"] == "n43n"
    assert got["strain-readout"] == "Zombie in Surface"


def test_counters_are_ints_not_strings():
    """String counters are where "10" < "9" comes from."""
    got = diag.parse(SAMPLE)
    assert got["ambient-motes"] == 346
    assert got["creep-converted"] == 56
    assert got["creep-census"] == 52
    assert isinstance(got["creep-tiles"], int)


def test_a_counter_this_parser_has_never_heard_of_is_still_a_number():
    """THE REGRESSION THIS FILE EXISTS FOR.

    `creep-drawn` was renamed to `creep-converted`/`creep-census` in the mod's
    0.8.0. Nothing here knew, so both new counters parsed as strings for a whole
    release — visible in the 2026-08-04 probe logs as `"creep-census": "0"`
    beside an honest `"creep-sources": 0`. Nothing failed; `"0"` is truthy, so a
    control asserting "creep exists" would have passed on an empty world.

    The old design named its counters, so a rename silently demoted one. Typing
    on the VALUE's shape instead means the mod can add or rename counters
    without this parser being told.
    """
    got = diag.parse("counter-invented-after-this-test-was-written: 12\n")
    assert got["counter-invented-after-this-test-was-written"] == 12


def test_a_composite_value_is_not_mistaken_for_a_number():
    """Negative control for shape-typing: not every creep line is a count.

    `creep-residue` is a per-type tally, and the whole point of it is reading
    which tile types are listed. Collapsing it to a number would destroy exactly
    the signal it was built to carry.
    """
    got = diag.parse(SAMPLE)
    assert got["creep-residue"] == "0=35 2=12 706=5 85=2 3=1"


@pytest.mark.parametrize(
    ("line", "key", "expected"),
    [
        # An md5 is 32 hex digits; roughly one in ten million is all-decimal.
        (
            "tmod-md5: 06730015689102266131458830061455",
            "tmod-md5",
            "06730015689102266131458830061455",
        ),
        ("version: 1", "version", "1"),
        ("player: 43", "player", "43"),
    ],
)
def test_an_identifier_that_looks_numeric_stays_text(line, key, expected):
    """THE HAZARD SHAPE-TYPING INTRODUCES, controlled.

    Typing by shape means any all-digit value would become an int, and for an
    identifier that is lossy and irreversible — `int()` eats leading zeros, so
    an md5 would no longer match the file it names. These fields are text
    whatever they happen to look like.
    """
    assert diag.parse(line + "\n")[key] == expected


def test_a_leading_zero_means_identifier_not_count():
    """A count is never written `007`. Something zero-padded is an id.

    This is the structural half of the guard above: it protects ids nobody
    thought to register, which is the failure mode the named-counter list had.
    """
    got = diag.parse("some-padded-id: 007\n")
    assert got["some-padded-id"] == "007"


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

    Deliberately a non-numeric value: retention is a separate property from
    typing, and testing them through one assertion means a typing change reads
    as a retention failure.
    """
    got = diag.parse(SAMPLE + "some-future-readout: Zombie in Cavern\n")
    assert got["some-future-readout"] == "Zombie in Cavern"


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


# --- The heartbeat shares this grammar, and broke two assumptions in it -------
#
# `<mod>-hooks.txt` is the same `key: value` format, so it reuses this parser
# rather than growing a second one — two parsers for one grammar is how one of
# them ends up weaker. But the real file (read off the install 2026-08-09) uses
# two shapes the diag dump never does, and BOTH failed silently here.

HEARTBEAT_SAMPLE = """\
hooks-seen: PostUpdateEverything,PostUpdateInput,UpdateUI
gameMenu: False
dedServ: False
savepath: C:\\Users\\a2b32\\Documents\\My Games\\Terraria\\tModLoader
trigger-exists: False
world-ready: True
capture-ready: True
armed: True
polls: 194
written: 20:19:07Z
"""


def test_a_camelcase_key_is_kept_rather_than_dropped():
    """The key pattern encoded ONE artifact's naming convention.

    `[a-z0-9][a-z0-9-]*` matches every diag key, and neither `gameMenu` nor
    `dedServ` — so the two fields that say WHICH SIDE wrote the heartbeat, and
    whether it is sitting on the main menu, matched nothing and were dropped
    without a word. A parser that silently discards what it does not recognise
    makes the field invisible to every tool here, which is the same failure as
    the mod never emitting it.
    """
    got = diag.parse(HEARTBEAT_SAMPLE)
    assert "gameMenu" in got, "camelCase key was dropped by the key pattern"
    assert "dedServ" in got


def test_booleans_are_typed_rather_than_left_as_truthy_strings():
    """`"False"` IS TRUTHY. Six of the heartbeat's eleven fields are booleans.

    Left as strings, `if hb["armed"]` and `if hb["world-ready"]` are both true
    on a game that is neither — the exact shape of the `"0"` bug this file was
    written for, in a different artifact.
    """
    got = diag.parse(HEARTBEAT_SAMPLE)
    assert got["armed"] is True
    assert got["gameMenu"] is False
    assert got["trigger-exists"] is False
    assert got["world-ready"] is True


def test_typing_booleans_did_not_change_the_diag_dump():
    """POSITIVE CONTROL for the two changes above.

    Widening the key pattern and adding a value shape could only be a no-op for
    diag if the diag dump contains neither — verified against the real
    `biomancy-diag.txt` on 2026-08-09 (0 camelCase keys, 0 True/False values).
    This pins that, so a future diag field of either shape fails HERE, loudly,
    rather than changing what an existing caller receives.
    """
    got = diag.parse(SAMPLE)
    assert got["version"] == "0.8.1"
    assert got["ambient-motes"] == 346
    assert got["strains"] is None
    assert not any(isinstance(v, bool) for v in got.values())


def test_a_counter_is_still_a_counter_beside_booleans():
    """Bool typing must not swallow the ints — `polls` is the liveness counter."""
    got = diag.parse(HEARTBEAT_SAMPLE)
    assert got["polls"] == 194
    assert not isinstance(got["polls"], bool)
