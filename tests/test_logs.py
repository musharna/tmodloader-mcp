"""The log tail, whose slice quietly did the opposite of what it was asked.

`logs` is the tool a caller reaches for when a launch failed, so it is read at
exactly the moment nobody has spare attention for a wrong answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tmodloader_mcp import logs
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


def test_the_filter_runs_over_the_whole_log_not_just_the_tail(logs_dir):
    """THE ORDER, which had no test at all and is the whole value of `contains`.

    Filtering and tailing do not commute. Filter first and you get the last N
    MATCHING lines; tail first and you get the matches among the last N, which
    for a launch failure is usually none - the interesting line is near the top
    and the tail is the shutdown noise after it.

    `line 2` falls outside a three-line tail of the twenty-line fixture only
    because the fixture is small; in a real log the gap is thousands of lines.
    An implementation that tailed first would return `["line 20"]` here and read
    as though `line 2` had never been written.
    """
    found = server_mod.logs(contains="line 2", lines=3)["lines"]

    assert found == ["line 2", "line 20"], (
        "the filter did not see the whole log - `line 2` is early enough to "
        "fall outside any tail taken before filtering"
    )


def test_the_filter_is_case_insensitive_as_documented(logs_dir):
    """Positive control for the filter, and the documented behaviour.

    Without it, a `contains` that quietly matched nothing would satisfy any
    assertion about what it excludes.
    """
    assert server_mod.logs(contains="LINE 7")["lines"] == ["line 7"]


def test_a_filter_matching_nothing_returns_nothing_rather_than_everything(logs_dir):
    """The other direction, and the one a falsy check gets wrong.

    `if contains:` skips filtering for an empty string, which is right - asking
    to keep lines containing nothing is asking for the whole log. But a filter
    that matches no lines has to return none of them rather than fall back to
    unfiltered, which is the same shape as the `-0` tail bug above.
    """
    assert server_mod.logs(contains="no such line")["lines"] == []
    assert server_mod.logs(contains="")["lines"] != []


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


# --- Incremental reads. Offsets, and the rotation that invalidates them. -----


def _logdir(tmp_path):
    d = tmp_path / logs.LOG_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_reading_from_zero_returns_everything_and_a_resume_point(tmp_path):
    (_logdir(tmp_path) / "client.log").write_text("one\ntwo\n")

    got = logs.read_since(tmp_path, "client.log", offset=0)

    assert got.text == "one\ntwo\n"
    assert got.next_offset == 8
    assert got.restarted is False


def test_reading_again_returns_only_what_was_appended(tmp_path):
    """The whole point: a log that grows all run, read once each time."""
    path = _logdir(tmp_path) / "client.log"
    path.write_text("one\ntwo\n")

    first = logs.read_since(tmp_path, "client.log", offset=0)
    with path.open("a") as handle:
        handle.write("three\n")
    second = logs.read_since(tmp_path, "client.log", offset=first.next_offset)

    assert second.text == "three\n"
    assert second.restarted is False


def test_nothing_new_is_an_empty_string_at_the_same_offset(tmp_path):
    path = _logdir(tmp_path) / "client.log"
    path.write_text("one\n")

    first = logs.read_since(tmp_path, "client.log", offset=0)
    again = logs.read_since(tmp_path, "client.log", offset=first.next_offset)

    assert again.text == ""
    assert again.next_offset == first.next_offset
    assert again.restarted is False


def test_a_rotated_log_is_re_read_from_the_start_and_says_so(tmp_path):
    """THE CASE THAT MATTERS. tModLoader zips the old run and starts fresh.

    An offset from the previous run points past the end of a file that is now
    shorter. Seeking there returns nothing, forever — which reads exactly like a
    quiet game rather than like a log that restarted underneath you.
    """
    path = _logdir(tmp_path) / "client.log"
    path.write_text("a long first run with plenty of output\n")
    first = logs.read_since(tmp_path, "client.log", offset=0)

    # The retry: same name, much shorter.
    path.write_text("new run\n")
    second = logs.read_since(tmp_path, "client.log", offset=first.next_offset)

    assert second.restarted is True
    assert second.text == "new run\n"
    assert second.next_offset == 8


def test_offsets_are_bytes_so_a_resume_point_survives_wide_characters(tmp_path):
    """A character offset is not a seek position. The log holds mod names."""
    path = _logdir(tmp_path) / "client.log"
    path.write_text("piña 🌱\n", encoding="utf-8")

    first = logs.read_since(tmp_path, "client.log", offset=0)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("after\n")
    second = logs.read_since(tmp_path, "client.log", offset=first.next_offset)

    assert first.next_offset == len("piña 🌱\n".encode())
    assert second.text == "after\n", "a character offset would have desynced here"


def test_a_negative_offset_is_refused(tmp_path):
    (_logdir(tmp_path) / "client.log").write_text("one\n")

    with pytest.raises(ValueError, match="not a position"):
        logs.read_since(tmp_path, "client.log", offset=-1)


def test_read_since_refuses_a_path_the_same_way_read_does(tmp_path):
    """Same guard, because a second reader is a second chance to be looser."""
    _logdir(tmp_path)

    with pytest.raises(logs.LogError):
        logs.read_since(tmp_path, "../../etc/passwd", offset=0)

    with pytest.raises(logs.LogError):
        logs.read_since(tmp_path, "notalog.txt", offset=0)


def test_read_since_on_a_missing_log_is_LogMissing_not_LogError(tmp_path):
    """A log that has never been written is the normal state of a fresh install."""
    _logdir(tmp_path)

    with pytest.raises(logs.LogMissing):
        logs.read_since(tmp_path, "client.log", offset=0)
