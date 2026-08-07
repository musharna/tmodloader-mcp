"""The log tail, whose slice quietly did the opposite of what it was asked.

`logs` is the tool a caller reaches for when a launch failed, so it is read at
exactly the moment nobody has spare attention for a wrong answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tmodloader_mcp import server as server_mod


class FakeCfg:
    """Only the field `logs` touches."""

    def __init__(self, root: Path):
        self.tml_dir = root


@pytest.fixture
def logs_dir(tmp_path, monkeypatch):
    """A client.log holding twenty numbered lines."""
    directory = tmp_path / "tModLoader-Logs"
    directory.mkdir()
    (directory / "client.log").write_text(
        "\n".join(f"line {n}" for n in range(1, 21)) + "\n"
    )
    monkeypatch.setattr(server_mod, "_cfg", lambda: FakeCfg(tmp_path))
    return directory


def test_a_tail_of_zero_lines_is_no_lines(logs_dir):
    """THE BUG.

    The tail was `text[-lines:]`, and `-0` is not a negative index - it is `0`,
    so `text[-0:]` is the whole list. Ask for no lines and you get every line
    there has ever been.

    It reads as a wrong answer rather than an error, which is the part that
    matters: an agent that asked for a small tail and got a whole log has no
    signal that anything went wrong, just a much larger context than it meant
    to spend.
    """
    assert server_mod.logs(lines=0)["lines"] == []


def test_a_tail_returns_the_end_of_the_log_not_the_start(logs_dir):
    """POSITIVE CONTROL, and the property people actually want.

    Clamping the slice could be got wrong in the other direction just as
    easily - `text[:lines]` is the same length and the wrong end, and a tail
    that shows the first three lines of a launch failure shows the part where
    nothing had gone wrong yet.
    """
    assert server_mod.logs(lines=3)["lines"] == ["line 18", "line 19", "line 20"]


def test_asking_for_more_lines_than_exist_returns_what_there_is(logs_dir):
    assert len(server_mod.logs(lines=500)["lines"]) == 20


def test_a_negative_tail_is_refused_rather_than_silently_inverted(logs_dir):
    """A negative count used to drop lines off the FRONT.

    `text[-(-5):]` is `text[5:]`, so asking for -5 lines returned all but the
    first five - the opposite end, and nearly the whole log. There is no
    sensible reading of a negative tail, so it is refused with the reason
    rather than answered with something.
    """
    with pytest.raises(ValueError):
        server_mod.logs(lines=-5)


def test_a_missing_log_is_reported_as_missing(tmp_path, monkeypatch):
    """POSITIVE CONTROL for the empty answer above.

    `lines == []` now means two things - no lines asked for, and no log at all
    - so the caller needs `found` to tell them apart. A log that has never been
    written is the normal state of a fresh install, not an error.
    """
    monkeypatch.setattr(server_mod, "_cfg", lambda: FakeCfg(tmp_path))

    result = server_mod.logs()
    assert result["found"] is False
    assert result["lines"] == []
