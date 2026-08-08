"""Finding a log, including one belonging to a run that has already ended.

tModLoader keeps its logs in `tModLoader-Logs/` and ZIPS THE PREVIOUS RUN'S
into `Old/` when a new one starts. That is the detail that matters here, and it
was missed: `logs` is documented as the tool to reach for when a launch fails,
and the normal thing to do after a failed launch is launch again — which rotates
the failed run's log into an archive and starts a fresh empty one. So the tool
answered with the retry's log at exactly the moment its own docstring promised
the failure's.

Measured on the real install rather than assumed: `client.log` opened at
01:10:32, the timestamp of this morning's live run, while the newest archive in
`Old/` was written at 16:18 the previous day — the run before it.
"""

from __future__ import annotations

import zipfile

import pytest

from tmodloader_mcp import logs as logs_mod


@pytest.fixture
def tml_dir(tmp_path):
    """An install whose log directory looks like the real one."""
    directory = tmp_path / "tModLoader-Logs"
    directory.mkdir()

    (directory / "client.log").write_text("current client run\nline two\n")
    (directory / "server.log").write_text("current server run\n")
    (directory / "Launch.log").write_text("launcher said this\n")
    (directory / "environment-client.log").write_text("env client\n")

    old = directory / "Old"
    old.mkdir()
    # Two archives, so "the newest" is a real choice rather than the only one.
    with zipfile.ZipFile(old / "2026-07-29-3.zip", "w") as z:
        z.writestr("client.log", "OLDER run\n")
    with zipfile.ZipFile(old / "2026-07-29-4.zip", "w") as z:
        z.writestr("client.log", "the run that failed\nreason here\n")
        z.writestr("environment-server.log", "env from that run\n")

    # Mtimes decide "newest", and writing order does not guarantee them.
    import os

    os.utime(old / "2026-07-29-3.zip", (1_000, 1_000))
    os.utime(old / "2026-07-29-4.zip", (2_000, 2_000))
    return tmp_path


def test_it_lists_the_logs_that_are_actually_there(tml_dir):
    """`logs` could name two of six. The set is read off disk rather than
    hardcoded, because which logs exist depends on what was run."""
    assert logs_mod.available(tml_dir) == [
        "Launch.log",
        "client.log",
        "environment-client.log",
        "server.log",
    ]


def test_it_reads_a_log_that_is_not_client_or_server(tml_dir):
    """Launch.log and the environment-*.log pair were unreachable, and a launch
    that dies before the game starts writes to those, not to client.log."""
    assert "launcher said this" in logs_mod.read(tml_dir, "Launch.log")


def test_the_previous_run_comes_from_the_newest_archive(tml_dir):
    """THE ONE THE TOOL EXISTS FOR.

    Launch fails, you launch again, and tModLoader zips the failed run's log
    away and starts an empty one. Reading `client.log` then answers about the
    retry — confidently, with a log of the right name that is the wrong run.
    """
    text = logs_mod.read(tml_dir, "client.log", previous=True)

    assert "the run that failed" in text
    assert "OLDER run" not in text, "it took an archive, but not the newest one"


def test_the_current_log_is_still_the_default(tml_dir):
    """Positive control. A reader that always reached for the archive would
    satisfy the test above and never show you the run you are watching."""
    assert "current client run" in logs_mod.read(tml_dir, "client.log")


def test_a_log_absent_from_the_archive_says_so(tml_dir):
    """The newest archive holds whatever that run wrote. A server-only run has
    no client.log in it, and "not in this archive" is a different answer from
    "no such log" — collapsing them would send you looking for the wrong thing.
    """
    with pytest.raises(logs_mod.LogError) as e:
        logs_mod.read(tml_dir, "Launch.log", previous=True)

    assert "archive" in str(e.value).lower()


def test_an_unknown_log_is_refused_with_the_real_list(tml_dir):
    with pytest.raises(logs_mod.LogError) as e:
        logs_mod.read(tml_dir, "nonsense.log")

    assert "client.log" in str(e.value)


def test_a_name_with_a_path_in_it_is_refused(tml_dir):
    """The name is joined to a directory and also looked up inside a zip. A
    traversal must not reach either — and the zip lookup is the one a
    filesystem check alone would miss."""
    for escape in ["../../secrets.log", "Old/2026-07-29-4.zip", "/etc/passwd"]:
        with pytest.raises(logs_mod.LogError):
            logs_mod.read(tml_dir, escape)


def test_no_archives_at_all_is_reported_not_guessed(tmp_path):
    directory = tmp_path / "tModLoader-Logs"
    directory.mkdir()
    (directory / "client.log").write_text("only run\n")

    with pytest.raises(logs_mod.LogError) as e:
        logs_mod.read(tmp_path, "client.log", previous=True)

    assert "no archived" in str(e.value).lower()
