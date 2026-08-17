"""Waiting for a log line, rather than polling for one by hand.

WHY THIS EXISTS. `log_since` answers "what has this log gained", which makes a
caller who is WAITING for something write the loop themselves: read, check,
sleep a guessed number, read again, give up eventually. That is the same loop
`wait_until` removed for diag fields, and it is wrong in the same two
directions - too short and the check runs before the line is written, too long
and every run pays the worst case.

THE OFFSET IS THE WHOLE MECHANISM. Each poll resumes where the last one
stopped, so a line is matched exactly once: never missed in the gap between two
polls, and never re-reported on the next one. A watch that re-read the file
from the top each time would match a line written before the wait began and
call it new - which is the failure that makes a "wait for the crash" check pass
on the crash from the PREVIOUS run.

Real files with real appends throughout. The thing being tested is a read of a
file that another process is writing, and a fake that hands back strings cannot
disagree with the code about what a partial read looks like.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tmodloader_mcp import logs as logs_mod


def _tml(tmp_path: Path) -> Path:
    """A tModLoader directory with an empty log directory in it."""
    logs_mod.directory(tmp_path).mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = logs_mod.directory(tmp_path) / name
    path.write_text(text, encoding="utf-8")
    return path


def _append(tmp_path: Path, name: str, text: str) -> None:
    path = logs_mod.directory(tmp_path) / name
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def test_a_line_already_in_the_log_matches_on_the_first_poll(tmp_path):
    """The common case, and the reason `offset` is a parameter rather than
    always the end: "did the mod load" is a question about a line that is
    usually already there."""
    tml = _tml(tmp_path)
    _write(tml, "client.log", "starting\nMod Biomancy loaded\nready\n")

    got = logs_mod.watch_for(tml, "client.log", contains="Biomancy loaded")

    assert got.matched
    assert got.polls == 1, "a line already present cost more than one read"
    assert got.lines == ["Mod Biomancy loaded"]


def test_a_line_written_after_the_watch_starts_is_caught(tmp_path):
    """The point of the tool. The append happens between polls, which is
    exactly where a hand-written loop drops it."""
    tml = _tml(tmp_path)
    _write(tml, "client.log", "starting\n")

    appended = {"done": False}
    real_sleep = time.sleep

    def append_then_sleep(seconds: float) -> None:
        if not appended["done"]:
            _append(tml, "client.log", "the thing happened\n")
            appended["done"] = True
        real_sleep(0)

    original = logs_mod.time.sleep
    logs_mod.time.sleep = append_then_sleep
    try:
        got = logs_mod.watch_for(
            tml, "client.log", contains="the thing", timeout=5.0, poll=0.01
        )
    finally:
        logs_mod.time.sleep = original

    assert got.matched
    assert got.polls >= 2, "it matched before the line could have been written"
    assert got.lines == ["the thing happened"]


def test_a_line_is_never_matched_twice_because_the_offset_advances(tmp_path):
    """THE MECHANISM. A watch that re-read from the top would match a line
    written before it started and call it new - which is how "wait for the
    crash" passes on the crash from the previous run."""
    tml = _tml(tmp_path)
    _write(tml, "client.log", "boom\n")

    first = logs_mod.watch_for(tml, "client.log", contains="boom")
    assert first.matched

    # Resuming from where the first watch stopped, the same line is gone.
    again = logs_mod.watch_for(
        tml,
        "client.log",
        contains="boom",
        offset=first.next_offset,
        timeout=0.2,
        poll=0.05,
    )
    assert not again.matched, (
        "the same line matched twice, so a caller resuming from next_offset "
        "sees history as if it were news"
    )

    # POSITIVE CONTROL, same test: a NEW occurrence after that offset does
    # match, so the offset is advancing rather than the watch being broken.
    _append(tml, "client.log", "boom\n")
    third = logs_mod.watch_for(
        tml, "client.log", contains="boom", offset=first.next_offset
    )
    assert third.matched


def test_a_watch_that_finds_nothing_reports_where_to_resume(tmp_path):
    """A timeout that did not hand back an offset would force the caller to
    start again from zero, which is where the already-seen lines are."""
    tml = _tml(tmp_path)
    _write(tml, "client.log", "nothing interesting\n")

    got = logs_mod.watch_for(
        tml, "client.log", contains="interesting thing", timeout=0.3, poll=0.05
    )

    assert not got.matched
    assert got.lines == []
    assert got.next_offset == len("nothing interesting\n"), (
        "the caller is told it timed out and not where the log had got to"
    )


def test_a_log_that_rotated_under_the_watch_says_so(tmp_path):
    """tModLoader zips the previous run's logs and starts fresh. An offset from
    before that points past the end of a now-shorter file, and reading there
    would report an empty log forever - which looks exactly like a quiet game.
    """
    tml = _tml(tmp_path)
    _write(tml, "client.log", "a long first run with plenty of output\n")
    far_past_the_end = 500

    got = logs_mod.watch_for(
        tml, "client.log", contains="first run", offset=far_past_the_end
    )

    assert got.matched, "the rotated log was never re-read from the top"
    assert got.restarted, (
        "the caller is handed lines from the beginning of a file without being "
        "told why they are seeing them again"
    )


def test_the_whole_watch_spends_one_budget(tmp_path):
    """One budget across every poll, the rule `wait_until` and `_left_of`
    already follow. A watch that slept its full poll interval after the
    deadline would overrun the timeout it was given.

    THE POLL IS LONGER THAN THE BUDGET, deliberately, and this test did not
    survive its own mutation until it was. Against `poll=0.2` and a 0.3s
    timeout, sleeping the full interval regardless overruns by 0.1s - inside
    any threshold loose enough not to be flaky on a busy machine, so the bug
    lived. A one-second poll turns the same bug into a 0.7s overrun, and
    `poll=1.0` is the DEFAULT, so this is the ordinary call rather than a
    contrived one.
    """
    tml = _tml(tmp_path)
    _write(tml, "client.log", "quiet\n")

    began = time.monotonic()
    got = logs_mod.watch_for(tml, "client.log", contains="never", timeout=0.3, poll=1.0)
    took = time.monotonic() - began

    assert not got.matched
    assert took < 0.6, f"the watch took {took:.3f}s against a 0.3s budget"


def test_it_sleeps_between_polls_rather_than_spinning(tmp_path):
    """A busy loop would pass every test above while reading the file as fast
    as the disk allows."""
    tml = _tml(tmp_path)
    _write(tml, "client.log", "quiet\n")

    got = logs_mod.watch_for(tml, "client.log", contains="never", timeout=0.3, poll=0.1)

    assert got.polls <= 5, f"{got.polls} polls in 0.3s - it is spinning"


def test_a_watch_with_no_needle_is_refused(tmp_path):
    """Without one it matches the first line written and is `log_since` wearing
    a longer name - and, worse, reports a match for any output at all."""
    tml = _tml(tmp_path)
    _write(tml, "client.log", "anything\n")

    for needle in ("", None):
        with pytest.raises(ValueError, match="what to wait for"):
            logs_mod.watch_for(tml, "client.log", contains=needle)


def test_the_needle_is_case_insensitive_like_every_other_filter(tmp_path):
    """`tail` lowercases both sides, and two spellings of one rule is how they
    drift apart."""
    tml = _tml(tmp_path)
    _write(tml, "client.log", "Mod Biomancy Loaded\n")

    assert logs_mod.watch_for(tml, "client.log", contains="biomancy loaded").matched


def test_a_missing_log_is_reported_as_itself_not_waited_out(tmp_path):
    """Waiting out the full budget on a log that does not exist reports a
    timeout, which sends the reader to look at a game that is running fine."""
    tml = _tml(tmp_path)

    with pytest.raises(logs_mod.LogMissing):
        logs_mod.watch_for(tml, "client.log", contains="anything", timeout=5.0)
