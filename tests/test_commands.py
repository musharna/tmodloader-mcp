"""Reading the list the mod publishes, and telling absence from nonsense."""

from __future__ import annotations

import pytest

from tmodloader_mcp import commands as commands_mod

#: THE REAL 975 BYTES a real Biomancy wrote, captured 2026-08-08.
#:
#: Not transcribed from the C# — transcribed FROM THE ARTIFACT. The whole point
#: of this module is that the harness stops holding its own idea of the mod, and
#: a fixture written here to suit the parser would put the same mistake back one
#: layer down: it would prove the reader reads what this file imagines, which is
#: exactly what the hardcoded list proved for years.
#:
#: So it was produced rather than composed. A dedicated server was launched
#: headlessly against BiomancySelfTest.wld with both candidate files deleted
#: first, and this is what appeared, byte for byte.
#:
#: What that run also established, by what did NOT appear: no heartbeat. An
#: empty server never ticks, so `biomancy-hooks-server.txt` was never written —
#: while the list was, because publishing happens at Load(). That is the case
#: this whole change is for, measured rather than argued: the state where
#: readiness times out is a state where the command list is already on disk.
PUBLISHED = (
    "# commands served by this responder, written at load\n"
    "# name\targ|noarg\tsummary\n"
    "capture\tnoarg\tSave a PNG of the whole frame via Terraria's own capture camera.\n"
    "diag\tnoarg\tWrite this side's state dump, from a live session.\n"
    "shot\targ\tSave a PNG of one region of the frame, from the back buffer "
    "(topleft, topright, bottomleft, bottomright, full).\n"
    "mutate\tnoarg\tPlant a mutated NPC in a world that is already ticking.\n"
    "vat\tnoarg\tRestart gestation in the first vat found, server-side.\n"
    "creature\tnoarg\tRelease an assembled creature into a live world.\n"
    "kill\tnoarg\tKill every mutated enemy, server-side, so their loot drops.\n"
    "strains\tnoarg\tWrite the strain report, as the Field Notebook does.\n"
    "seed\tnoarg\tSeed a strain in the biome a connected player is standing in.\n"
    "creep\tnoarg\tRegister a creep source where a connected player is standing.\n"
    "place\tnoarg\tStamp a solid patch of a creep tile type into the world.\n"
    "killcreep\tnoarg\tRemove every creep source, so the converter puts the "
    "terrain back.\n"
)

#: What this harness used to hardcode. Kept ONLY as the check below.
WAS_HARDCODED = (
    "capture",
    "diag",
    "mutate",
    "vat",
    "creature",
    "kill",
    "strains",
    "seed",
    "creep",
    "place",
    "killcreep",
    "shot",
)


def test_reads_what_the_mod_actually_writes():
    """The contract, against bytes a real mod really wrote."""
    served = commands_mod.parse_published(PUBLISHED)

    assert len(served) == 12
    assert served.taking_an_argument == ("shot",)
    assert served.names[0] == "capture"
    assert served.names[-1] == "killcreep"


def test_the_published_list_agrees_with_what_was_hardcoded():
    """The migration's one-time proof, and the reason to trust the swap.

    Reading the list instead of keeping one is only an improvement if the thing
    read back is the thing that was there. If the published set differed from
    the twelve this harness carried, every existing script would have changed
    behaviour silently at the moment of the swap — some command refused that used
    to work, or accepted that used to be caught.

    It does not differ, and that is a measurement: these twelve names and the one
    argument-taker came off a real run, and match the old constant exactly. What
    changes is not the answer but where it comes from.
    """
    served = commands_mod.parse_published(PUBLISHED)

    assert sorted(served.names) == sorted(WAS_HARDCODED)
    assert served.taking_an_argument == ("shot",)


def test_the_summary_survives_whole():
    """A summary carrying its own punctuation, including the parenthesised region
    list, must not be truncated at the first thing that looks like a delimiter."""
    served = commands_mod.parse_published(PUBLISHED)

    assert served.get("shot").summary.endswith("bottomright, full).")
    assert "back buffer" in served.get("shot").summary


def test_headers_and_blank_lines_are_not_commands():
    served = commands_mod.parse_published(
        "# a comment\n\n   \ndiag\tnoarg\tstate\n\n# another\n"
    )

    assert served.names == ("diag",)


def test_resolution_ignores_case_both_ways():
    served = commands_mod.parse_published("DIAG\tnoarg\tstate\n")

    # The mod lowercases at registration; this side must not depend on that
    # having happened, and must answer a caller who shouts.
    assert served.names == ("diag",)
    assert "diag" in served
    assert served.get("DiAg").name == "diag"


def test_a_command_that_is_absent_is_absent():
    served = commands_mod.parse_published(PUBLISHED)

    # A plausible verb this mod does not serve, which is the case that matters:
    # another mod's command, or one this build compiled out.
    assert served.get("teleport") is None
    assert "teleport" not in served

    # Positive control: the lookup is not simply failing.
    assert served.get("diag") is not None


@pytest.mark.parametrize(
    "line",
    [
        "diag\n",  # no tab at all: names no flag
        "diag noarg state\n",  # spaces, not tabs
        "\tnoarg\tstate\n",  # empty name
    ],
)
def test_a_line_that_names_no_command_is_an_error_not_a_skip(line):
    """Skipping would drop a command SILENTLY.

    The only symptom would be this side refusing to compose a trigger the game
    would have answered perfectly — a failure that points at the wrong half of
    the system, and the hardest kind to chase because both halves look right.
    """
    with pytest.raises(commands_mod.CommandsError):
        commands_mod.parse_published(line)

    # Positive control: a well-formed line in the same shape is accepted.
    assert commands_mod.parse_published("diag\tnoarg\tstate\n").names == ("diag",)


@pytest.mark.parametrize("flag", ["yes", "true", "argument", "noargs", ""])
def test_an_unreadable_argument_flag_is_an_error(flag):
    """Guessing is worse than refusing, in both directions.

    Read as `noarg`, a command that needs an argument becomes uncallable from
    here. Read as `arg`, one that ignores it starts accepting a word the mod
    will refuse. Neither failure names itself.
    """
    with pytest.raises(commands_mod.CommandsError) as e:
        commands_mod.parse_published(f"diag\t{flag}\tstate\n")

    assert "diag" in str(e.value)


def test_the_flag_is_read_both_ways():
    """Positive control for the check above: both legal values are accepted, and
    they mean opposite things. A parser that rejected everything, or that read
    every flag as False, would satisfy the rejection test."""
    served = commands_mod.parse_published("diag\tnoarg\tstate\nshot\targ\tregion\n")

    assert served.get("diag").takes_argument is False
    assert served.get("shot").takes_argument is True


@pytest.mark.parametrize("flag", ["ARG", "arg ", " Arg\t"])
def test_case_and_padding_around_the_flag_are_noise(flag):
    """Deliberate, and worth pinning rather than leaving to chance.

    The producer writes a bare lowercase word today. Refusing a padded or
    shouted one would make this reader brittle against a formatting change on
    the other side of a repository boundary that means nothing — while the
    unreadable values above stay refused, because those change the MEANING.
    """
    served = commands_mod.parse_published(f"shot\t{flag}\tregion\n")

    assert served.get("shot").takes_argument is True


def test_a_command_published_twice_is_an_error():
    """The list would not say which handler the game will run.

    The mod refuses a duplicate registration, so a published duplicate means the
    two sides disagree about what a responder even is — which is worth stopping
    on rather than picking one.
    """
    with pytest.raises(commands_mod.CommandsError) as e:
        commands_mod.parse_published("diag\tnoarg\tone\ndiag\targ\ttwo\n")

    assert "diag" in str(e.value)


def test_a_missing_list_says_no_responder_rather_than_no_answer_yet(tmp_path):
    """THE DISTINCTION THIS MODULE EXISTS FOR.

    An absent list is not a slow one. It means the mod is not loaded, or is a
    build with the dev bridge compiled out — and that used to surface as a
    readiness timeout, which reads as a game too slow to start rather than a
    game that was never going to answer.
    """
    with pytest.raises(commands_mod.CommandsMissing) as e:
        commands_mod.read(tmp_path / "biomancy-commands.txt")

    said = str(e.value)
    assert "not a timeout" in said
    assert "biomancy-commands.txt" in said


def test_a_missing_list_is_distinguishable_in_type_not_just_in_wording(tmp_path):
    """A caller has to be able to branch on it.

    The `commands` tool reports `responder: false` for absence and raises for a
    list it cannot read, so the two cannot share one exception type — and
    `CommandsMissing` subclasses `CommandsError`, which means catching the
    general one FIRST would swallow the specific one.
    """
    path = tmp_path / "biomancy-commands.txt"

    with pytest.raises(commands_mod.CommandsMissing):
        commands_mod.read(path)

    path.write_text("diag noarg state\n")  # spaces: readable file, unreadable list
    with pytest.raises(commands_mod.CommandsError) as e:
        commands_mod.read(path)

    assert not isinstance(e.value, commands_mod.CommandsMissing), (
        "a malformed list read as 'no responder', so a version mismatch would "
        "be reported as an absent mod and waited out rather than fixed"
    )


def test_a_list_with_no_commands_is_not_the_same_as_no_list(tmp_path):
    """A responder that registered nothing DID load. Reporting that as absence
    would send someone looking for a mod that is right there."""
    path = tmp_path / "biomancy-commands.txt"
    path.write_text("# commands served by this responder, written at load\n")

    with pytest.raises(commands_mod.CommandsError) as e:
        commands_mod.read(path)

    assert not isinstance(e.value, commands_mod.CommandsMissing)
    assert "registered nothing" in str(e.value)


def test_reads_a_real_file_off_disk(tmp_path):
    """Positive control for every refusal above: the happy path works.

    Byte for byte what the mod wrote, through the same entry point the session
    uses — so this exercises the file read, not just the string parser.
    """
    path = tmp_path / "biomancy-commands.txt"
    path.write_text(PUBLISHED, encoding="utf-8")

    served = commands_mod.read(path)

    assert sorted(served.names) == sorted(WAS_HARDCODED)
    assert served.get("shot").takes_argument is True
