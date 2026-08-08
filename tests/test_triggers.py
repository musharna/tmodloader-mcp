"""The trigger protocol's pure parts: composing payloads and judging liveness."""

from __future__ import annotations

import pytest

from tmodloader_mcp import commands as commands_mod
from tmodloader_mcp import server as server_mod
from tmodloader_mcp import triggers

#: What a running Biomancy publishes, as `compose` now receives it.
#:
#: A FIXTURE rather than a constant this module believes in. It used to be
#: `triggers.COMMANDS`, a hardcoded copy of one mod's verbs that the real mod
#: could not see — and these tests then checked that copy against itself, which
#: is why none of them ever caught it drifting. Here it stands in for one
#: specific mod's answer, and nothing in `src/` knows it exists.
SERVED = commands_mod.CommandSet(
    commands=(
        commands_mod.Command("capture", False, "whole frame"),
        commands_mod.Command("diag", False, "state dump"),
        commands_mod.Command("shot", True, "one region"),
        commands_mod.Command("mutate", False, "plant a mutated NPC"),
        commands_mod.Command("seed", False, "seed a strain"),
        commands_mod.Command("creep", False, "register a creep source"),
        commands_mod.Command("killcreep", False, "remove every creep source"),
    )
)


def compose(command, **kw):
    """`triggers.compose` against the fixture set, so each test says one thing."""
    return triggers.compose(command, commands=SERVED, **kw)


def test_composes_the_four_shapes():
    assert compose("diag") == "diag"
    assert compose("shot", argument="bottomleft") == "shot:bottomleft"
    assert compose("diag", target="n43n") == "diag@n43n"
    assert compose("shot", target="n43n", argument="topleft") == "shot:topleft@n43n"


def test_a_command_the_mod_does_not_serve_is_refused_here():
    """THE LOAD-BEARING CHECK.

    The mod refuses a verb it does not serve rather than falling back to a
    capture — correctly, so a misspelling cannot take a screenshot nobody asked
    for. But that refusal costs a round trip and only arrives if a game is
    running to give it. Catching it here turns a wait into a sentence.
    """
    with pytest.raises(triggers.TriggerError) as e:
        compose("creeep")

    assert "creeep" in str(e.value)
    assert "creep" in str(e.value)  # what IS served is named, not just the refusal


def test_every_published_command_actually_composes():
    """Positive control for the check above.

    Without it, a published set that had arrived empty would make the rejection
    test pass while refusing everything.
    """
    for command in SERVED.names:
        argument = "x" if SERVED.get(command).takes_argument else None
        expected = f"{command}:x" if argument else command
        assert compose(command, argument=argument) == expected


def test_a_command_this_mod_lacks_is_refused_even_though_another_mod_has_it():
    """The point of reading the list rather than keeping one.

    `vat` and `place` are real Biomancy commands and are absent from this
    fixture, standing in for a mod that does not serve them. A harness holding
    its own list would compose them happily and wait for an answer nobody was
    ever going to write.
    """
    for absent in ("vat", "place", "strains"):
        with pytest.raises(triggers.TriggerError) as e:
            compose(absent)

        assert absent in str(e.value)

    # Positive control: this set is not simply refusing everything.
    assert compose("creep") == "creep"


def test_the_trigger_tool_points_at_the_published_list():
    """The tool docstring IS the surface an MCP caller reads, and it held a copy.

    It spelled out twelve commands, and had already drifted once — `place` and
    `killcreep` were missing, so a caller with no other source would have
    concluded they did not exist. The old test for that compared the docstring
    against `triggers.COMMANDS`: two copies of one mod's list agreeing with each
    other, neither of them the mod.

    Both copies are gone, so the drift is unrepresentable and there is nothing
    left to compare. What is worth holding is that the surface still tells a
    caller where the real answer lives.
    """
    described = server_mod.trigger.__doc__ or ""

    assert "`commands`" in described

    # It must not have quietly grown a list again.
    assert "killcreep" not in described


def test_an_empty_half_is_an_error():
    """`shot:` names no region, and the mod calls that an error rather than
    defaulting — defaulting would capture a wider picture than was asked for.

    This was parametrized over `[("shot", ""), ("diag", None)]` with the body
    guarded by `if argument == ""`, so the second case entered no assertion at
    all: a parameter that could not fail, reported as a passing test.
    """
    with pytest.raises(triggers.TriggerError):
        compose("shot", argument="")

    assert compose("diag") == "diag"  # no argument at all is fine


def test_an_empty_target_is_an_error():
    """`diag@` addresses nobody; letting it through hands it to whoever polls
    first, which is the exact race addressing exists to remove."""
    with pytest.raises(triggers.TriggerError):
        compose("diag", target="")


def test_an_argument_the_game_will_never_read_is_refused():
    """The silent one, and the one that fires on ordinary input.

    Only some commands read an argument, and the mod used to PARSE one for every
    command and drop it: `ask("seed", argument="Jungle")` composed cleanly, was
    parsed cleanly, seeded something else entirely and reported success. Nothing
    anywhere said the specification had been dropped.

    Which command reads one is now published per command rather than believed
    here, so this check asks the set instead of a constant.
    """
    with pytest.raises(triggers.TriggerError) as e:
        compose("seed", argument="Jungle")

    assert "seed" in str(e.value)
    assert "Jungle" in str(e.value)

    # The positive control belongs in the same test: a harness that refused
    # every argument would satisfy the assertion above and break the commands
    # that have an argument to give.
    assert compose("shot", argument="topleft") == "shot:topleft"


def test_a_command_that_needs_an_argument_is_refused_without_one():
    """The other half, and it was never checked.

    `shot` with no region reached the game, which refused it — a round trip to
    learn something the published list already says. Composing it is the error.
    """
    with pytest.raises(triggers.TriggerError) as e:
        compose("shot")

    assert "shot" in str(e.value)

    # Positive control: the same command with a region composes.
    assert compose("shot", argument="topleft") == "shot:topleft"


def test_a_payload_the_game_would_read_back_differently_is_refused():
    """`Parse` splits on the FIRST `@`, and the target is appended LAST.

    So an `@` inside the argument steals the target: `shot:top@left` is heard as
    a `shot` addressed to `left` with no region at all. Composed one way, read
    another, and the mismatch is invisible from this side — an addressed request
    no player answers produces no reply file, which is the same observation as a
    hung game.
    """
    with pytest.raises(triggers.TriggerError) as e:
        compose("shot", target="n43n", argument="top@left")

    assert "top@left" in str(e.value) or "n43n" in str(e.value)


def test_the_check_is_the_grammar_and_not_a_list_of_bad_characters():
    """The discriminator between the two candidate fixes.

    Rejecting `@` in an argument would pass the test above and be wrong twice: a
    colon is equally a delimiter yet survives intact (the mod splits on the
    FIRST one and keeps the rest), while whitespace breaks nothing syntactically
    and still changes what the game does, because `Parse` trims every field.
    Only reading the payload back the way the game will can tell those apart.
    """
    assert compose("shot", argument="a:b") == "shot:a:b"

    with pytest.raises(triggers.TriggerError):
        compose("shot", argument=" topleft ")


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
        # A word nothing serves is NOT malformed. It used to come back None,
        # from a hardcoded list this side kept — which stopped matching the mod
        # the day the mod's parser stopped judging vocabulary too. The game
        # parses this perfectly and declines it; those are different answers,
        # and only the second names what to do about it.
        ("creeep", triggers.Request("creeep", None, None)),
        ("mutate", triggers.Request("mutate", None, None)),
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

    It models the GRAMMAR only. Vocabulary is published by the mod and checked in
    `compose`, so the two limits are now different: the grammar can still drift
    unnoticed, the command set cannot.
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
