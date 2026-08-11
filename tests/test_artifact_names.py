"""The filenames the mod and this harness have to agree on, for ANY mod.

They were five constants spelling `biomancy-`, which is how a tool for one mod
stays a tool for one mod. They are now derived from the mod's internal name,
and the first test is that the derivation reproduces the existing names exactly
— because those are not a preference, they are a contract with running C# that
was validated against a live game this morning.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tmodloader_mcp import captures, config
from tmodloader_mcp.triggers import artifacts_for

# Verbatim from the mod's own constants in DevCapture.cs / DevArtifacts.cs, and
# from the files this harness read off disk during the 2026-08-08 live run.
BIOMANCY_TODAY = {
    "trigger": "biomancy-capture.trigger",
    "result": "biomancy-capture.txt",
    "diag": "biomancy-diag.txt",
    "heartbeat": "biomancy-hooks.txt",
    "shot": "biomancy-shot.png",
}


def test_the_derivation_reproduces_biomancys_names_exactly():
    """THE COMPATIBILITY TEST, and the reason the rule is what it is.

    tModLoader's internal mod name is its source folder name, so lowercasing it
    yields `biomancy-` for Biomancy. Any other rule would have been a rename of
    files that a shipped, running mod already writes — and the harness would
    have sat waiting for a trigger nobody reads.
    """
    art = artifacts_for("Biomancy")

    assert art.trigger == BIOMANCY_TODAY["trigger"]
    assert art.result == BIOMANCY_TODAY["result"]
    assert art.diag == BIOMANCY_TODAY["diag"]
    assert art.heartbeat == BIOMANCY_TODAY["heartbeat"]
    assert art.shot == BIOMANCY_TODAY["shot"]


def test_another_mod_gets_its_own_names():
    """The point of the exercise. Without this, the test above is satisfied by
    hardcoding `biomancy-` and calling it derived."""
    art = artifacts_for("MyCoolMod")

    assert art.trigger == "mycoolmod-capture.trigger"
    assert art.shot == "mycoolmod-shot.png"
    assert "biomancy" not in art.diag


def test_two_mods_never_collide():
    """Both sides share one save directory. Two mods driven from one machine
    must not consume each other's triggers — the same reason the server's
    artifacts are suffixed."""
    a = artifacts_for("Biomancy")
    b = artifacts_for("Otherwise")

    assert {a.trigger, a.result, a.diag, a.heartbeat, a.shot}.isdisjoint(
        {b.trigger, b.result, b.diag, b.heartbeat, b.shot}
    )


def test_the_mod_name_comes_from_the_source_folder_by_default(tmp_path):
    """tModLoader's internal name IS the folder name, so nothing needs setting
    for the common case."""
    source = tmp_path / "Biomancy"
    source.mkdir()

    cfg = config.load({"TMODLOADER_MOD_SOURCE": str(source)})

    assert cfg.mod_name == "Biomancy"
    assert cfg.artifacts.trigger == "biomancy-capture.trigger"


def test_the_mod_name_can_be_overridden(tmp_path):
    """For a source folder whose name is not the internal name."""
    source = tmp_path / "src-checkout"
    source.mkdir()

    cfg = config.load(
        {"TMODLOADER_MOD_SOURCE": str(source), "TMODLOADER_MOD_NAME": "RealName"}
    )

    assert cfg.mod_name == "RealName"
    assert cfg.artifacts.shot == "realname-shot.png"


@pytest.mark.parametrize("bad", ["", "   ", "has space", "has/slash", "dot.dot"])
def test_a_name_that_would_not_be_a_mod_name_is_refused(bad, tmp_path):
    """The prefix lands in filenames and in a regex. A name with a separator in
    it would build a path rather than a filename, and one with a `.` would make
    the capture pattern match more than it should."""
    source = tmp_path / "Src"
    source.mkdir()

    # SAVE_DIR supplied because `check` reports unset REQUIRED variables alone
    # and returns - otherwise this would assert on the wrong problem entirely.
    cfg = config.load(
        {
            "TMODLOADER_MOD_SOURCE": str(source),
            "TMODLOADER_SAVE_DIR": str(tmp_path),
            "TMODLOADER_MOD_NAME": bad,
        }
    )
    problems = config.check(cfg)

    if bad.strip():
        assert any("TMODLOADER_MOD_NAME" in p for p in problems), problems
    else:
        # Blank falls back to the folder name rather than being an error.
        assert cfg.mod_name == "Src"


# ---- captures follow the same prefix ------------------------------------


def test_capture_reading_follows_the_mod_name(tmp_path):
    """`captures` matched `^biomancy-shot-...` too, so another mod's captures
    were invisible to the reader that exists to serve them."""
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    (tmp_path / "mycoolmod-shot-001-full.png").write_bytes(png)

    assert captures.available(tmp_path, "MyCoolMod") == ["mycoolmod-shot-001-full.png"]
    assert captures.read(tmp_path, "MyCoolMod", "mycoolmod-shot-001-full.png") == png


def test_one_mods_capture_is_not_served_to_another(tmp_path):
    """Two mods, one save directory. The containment rule now has a second
    axis, and it is the same rule: serve only what this mod wrote."""
    (tmp_path / "biomancy-shot-001-full.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    with pytest.raises(captures.CaptureError):
        captures.read(Path(tmp_path), "MyCoolMod", "biomancy-shot-001-full.png")


# ---- answers carry the player, requests do not ---------------------------


def test_answers_carry_the_player_and_requests_do_not():
    a = artifacts_for("Biomancy", player="n43n")
    # Requests are SHARED and addressed - the trigger is how a client is told
    # the request is not for it, so per-player triggers would remove the very
    # mechanism that already works.
    assert a.trigger == "biomancy-capture.trigger"
    # The verb set is the mod's and identical for every client.
    assert a.commands == "biomancy-commands.txt"

    assert a.diag == "biomancy-diag-n43n-003f.txt"
    assert a.heartbeat == "biomancy-hooks-n43n-003f.txt"
    assert a.result == "biomancy-capture-n43n-003f.txt"
    assert a.shot == "biomancy-shot-n43n-003f.png"


def test_the_token_goes_before_the_extension_not_after():
    # A name ending `.txt-n43n-003f` is not a text file to anything that reads
    # extensions, including the harness's own capture reader.
    a = artifacts_for("Biomancy", player="n43n")
    for name in (a.diag, a.heartbeat, a.result):
        assert name.endswith(".txt")
    assert a.shot.endswith(".png")


def test_no_player_reproduces_todays_names_exactly():
    # The dedicated server has no player, and a client has none before a
    # character exists. Both keep the old names, so this is also the proof that
    # the change is backward compatible where it has to be.
    a = artifacts_for("Biomancy")
    assert a.trigger == "biomancy-capture.trigger"
    assert a.result == "biomancy-capture.txt"
    assert a.diag == "biomancy-diag.txt"
    assert a.heartbeat == "biomancy-hooks.txt"
    assert a.shot == "biomancy-shot.png"
    assert a.commands == "biomancy-commands.txt"


def test_all_covers_every_artifact_including_the_per_player_ones():
    # `all` is what clears artifacts between runs. A name missing from it is a
    # stale file that survives a run and reads as this run's answer.
    a = artifacts_for("Biomancy", player="n43n")
    assert set(a.all) == {a.trigger, a.result, a.diag, a.heartbeat, a.shot, a.commands}
    assert len(a.all) == 6
