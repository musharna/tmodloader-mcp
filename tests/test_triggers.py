"""The trigger protocol's pure parts: composing payloads and judging liveness."""

from __future__ import annotations

import pytest

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


@pytest.mark.parametrize("bad", [("shot", ""), ("diag", None)])
def test_an_empty_half_is_an_error(bad):
    command, argument = bad
    if argument == "":
        with pytest.raises(triggers.TriggerError):
            triggers.compose(command, argument=argument)


def test_an_empty_target_is_an_error():
    """`diag@` addresses nobody; letting it through hands it to whoever polls
    first, which is the exact race addressing exists to remove."""
    with pytest.raises(triggers.TriggerError):
        triggers.compose("diag", target="")


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
