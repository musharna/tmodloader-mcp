"""The trigger protocol's pure parts: composing payloads and judging liveness."""

from __future__ import annotations

import re

import pytest

from tmodloader_mcp import server as server_mod
from tmodloader_mcp import triggers


def test_composes_the_four_shapes():
    assert triggers.compose("diag") == "diag"
    assert triggers.compose("shot", argument="bottomleft") == "shot:bottomleft"
    assert triggers.compose("diag", target="n43n") == "diag@n43n"
    assert (
        triggers.compose("shot", target="n43n", argument="topleft")
        == "shot:topleft@n43n"
    )


def test_an_unknown_command_is_refused_here_not_written_to_disk():
    """THE LOAD-BEARING CHECK.

    DevCommands.Parse treats an unknown word as Unknown and does nothing —
    correctly, so a misspelling cannot take a screenshot nobody asked for. But
    from this side "the game ignored it" and "the game is hung" are the same
    observation: no result file appears either way. Catching it here turns a
    timeout into a sentence.
    """
    with pytest.raises(triggers.TriggerError) as e:
        triggers.compose("creeep")

    assert "creeep" in str(e.value)
    assert "creep" in str(e.value)  # the valid set is named, not just rejected


def test_every_advertised_command_actually_composes():
    """Positive control for the check above.

    Without this, a COMMANDS set that had drifted to empty would make the
    rejection test pass while refusing everything.
    """
    for command in triggers.COMMANDS:
        assert triggers.compose(command) == command


def test_the_trigger_tool_advertises_every_command_there_is():
    """The tool docstring IS the surface an MCP caller reads, and it had drifted.

    It listed ten of the twelve — `place` and `killcreep` were missing — so a
    caller with no other source would have concluded they did not exist. That is
    the same drift the live-check contract test exists for, on the surface an
    agent actually reads, and it is invisible to every test that goes through
    `compose` instead of through the description.
    """
    described = server_mod.trigger.__doc__ or ""

    missing = [c for c in triggers.COMMANDS if not re.search(rf"\b{c}\b", described)]
    assert not missing

    # Positive control: a matcher that found everything would pass the above
    # while proving nothing.
    assert not re.search(r"\bcreeep\b", described)


def test_an_empty_half_is_an_error():
    """`shot:` names no region, and the mod calls that an error rather than
    defaulting — defaulting would capture a wider picture than was asked for.

    This was parametrized over `[("shot", ""), ("diag", None)]` with the body
    guarded by `if argument == ""`, so the second case entered no assertion at
    all: a parameter that could not fail, reported as a passing test.
    """
    with pytest.raises(triggers.TriggerError):
        triggers.compose("shot", argument="")

    assert triggers.compose("diag") == "diag"  # no argument at all is fine


def test_an_empty_target_is_an_error():
    """`diag@` addresses nobody; letting it through hands it to whoever polls
    first, which is the exact race addressing exists to remove."""
    with pytest.raises(triggers.TriggerError):
        triggers.compose("diag", target="")


def test_an_argument_the_game_will_never_read_is_refused():
    """The silent one, and the one that fires on ordinary input.

    `request.Argument` is read in exactly ONE place in the whole mod —
    `DevCapture.cs:386`, `TakeShot`. `Parse` hands `seed` an argument too, and
    then `SeedWherePlayerStands()` takes none and hardcodes Zombie/Bloom. So
    `ask("seed", argument="Jungle")` composes cleanly, is parsed cleanly, seeds
    something else entirely, and reports `SEED: ok`. Nothing anywhere says the
    specification was dropped.
    """
    with pytest.raises(triggers.TriggerError) as e:
        triggers.compose("seed", argument="Jungle")

    assert "seed" in str(e.value)
    assert "Jungle" in str(e.value)

    # The positive control belongs in the same test: a harness that refused
    # every argument would satisfy the assertion above and break the one
    # command that has an argument to give.
    assert triggers.compose("shot", argument="topleft") == "shot:topleft"


def test_a_payload_the_game_would_read_back_differently_is_refused():
    """`Parse` splits on the FIRST `@`, and the target is appended LAST.

    So an `@` inside the argument steals the target: `shot:top@left` is heard as
    a `shot` addressed to `left` with no region at all. Composed one way, read
    another, and the mismatch is invisible from this side — an addressed request
    no player answers produces no reply file, which is the same observation as a
    hung game.
    """
    with pytest.raises(triggers.TriggerError) as e:
        triggers.compose("shot", target="n43n", argument="top@left")

    assert "top@left" in str(e.value) or "n43n" in str(e.value)


def test_the_check_is_the_grammar_and_not_a_list_of_bad_characters():
    """The discriminator between the two candidate fixes.

    Rejecting `@` in an argument would pass the test above and be wrong twice: a
    colon is equally a delimiter yet survives intact (the mod splits on the
    FIRST one and keeps the rest), while whitespace breaks nothing syntactically
    and still changes what the game does, because `Parse` trims every field.
    Only reading the payload back the way the game will can tell those apart.
    """
    assert triggers.compose("shot", argument="a:b") == "shot:a:b"

    with pytest.raises(triggers.TriggerError):
        triggers.compose("shot", argument=" topleft ")


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("diag", triggers.Request("diag", None, None)),
        ("shot:topleft", triggers.Request("shot", None, "topleft")),
        ("diag@n43n", triggers.Request("diag", "n43n", None)),
        ("shot:topleft@n43n", triggers.Request("shot", "n43n", "topleft")),
        ("  diag@n43n  ", triggers.Request("diag", "n43n", None)),
        ("DIAG", triggers.Request("diag", None, None)),  # matched case-insensitively
        ("diag@a@b", triggers.Request("diag", "a@b", None)),  # first `@` only
        ("", triggers.Request("capture", None, None)),  # a bare `touch`, historically
        ("diag@", None),
        ("shot:", None),
        ("@n43n", None),
        ("creeep", None),
    ],
)
def test_parse_mirrors_the_mods_own_grammar(payload, expected):
    """Checked against `DevCommands.Parse`, case by case.

    This is a MODEL of the mod's parser, not the parser itself, and the round
    trip in `compose` is only as good as the model: it catches a payload this
    file would read back differently, not one the mod has since changed its mind
    about. Which is why the cases are spelled out here rather than asserted in
    the abstract — they are the thing that has to be re-checked if the mod's
    grammar moves.
    """
    assert triggers.parse(payload) == expected


def test_a_missing_heartbeat_is_not_live(tmp_path):
    assert not triggers.heartbeat_is_live(tmp_path / "nothing.txt")


def test_a_stale_heartbeat_is_not_live(tmp_path):
    """THE ONE THAT COST A DEBUGGING SESSION.

    A heartbeat file outlives the process that wrote it. A harness once passed
    three readiness gates on a killed client's leftover file, then failed with a
    timeout that named the wrong cause entirely.
    """
    hb = tmp_path / "biomancy-hooks.txt"
    hb.write_text("world-ready: True\n")

    fresh = hb.stat().st_mtime
    assert triggers.heartbeat_is_live(hb, now=fresh)
    assert not triggers.heartbeat_is_live(
        hb, now=fresh + triggers.HEARTBEAT_MAX_AGE + 1
    )


def test_world_ready_reads_the_contents():
    assert triggers.world_is_ready("world-ready: True\npolls: 12\n")
    assert not triggers.world_is_ready("world-ready: False\npolls: 12\n")


def test_world_ready_is_false_when_the_line_is_absent():
    """Absence is not readiness. A heartbeat from a game still loading has no
    such line, and defaulting to true would drive a world that is not there."""
    assert not triggers.world_is_ready("polls: 3\ndedServ: False\n")


def test_freshness_and_readiness_are_separate_questions():
    """They fail differently: stale-but-ready means the game died, and
    fresh-but-not-ready means it is still loading. One boolean loses which."""
    assert triggers.world_is_ready("world-ready: True\n")  # says nothing about age


@pytest.mark.parametrize(
    ("text", "ok", "refused"),
    [
        ("CREEP: ok - planted at 500,500", True, False),
        ("SEED: ok - Zombie/Surface (Bloom)", True, False),
        ("REFUSED: seeding is server-authoritative", False, True),
        ("ERROR: the strain report threw: ...", False, False),
        ("IGNORED: a trigger was already on disk", False, False),
    ],
)
def test_reply_reads_the_mods_own_reporting_convention(text, ok, refused):
    """A refusal is the mod deliberately saying no, and must not read as success.

    Treating REFUSED as ok is how a refused tumour placement reads as a placed
    one — the harness then asserts against a world that never changed.
    """
    reply = triggers.Reply(command="creep", text=text)
    assert reply.ok is ok
    assert reply.refused is refused
