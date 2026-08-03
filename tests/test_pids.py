"""Process identification — the defect a live run found.

The first version grepped `tasklist` for "tmodloader" in the process NAME.
tModLoader runs as `dotnet.exe`, so it matched nothing, ever. That is silent and
far worse than noisy: the already-running guard never fired, and teardown
computed an empty set and reported killing nothing AS SUCCESS — leaving orphaned
processes holding a lock on the .tmod, which then broke the next build with an
error that looked like a compile failure.

Only real execution could find that. These tests cover what can be checked
without Windows: the parsing, and the shape of the query itself.
"""

from __future__ import annotations

from tmodloader_mcp import session


def test_pids_are_parsed_from_one_per_line_output():
    assert session.parse_pids("38552\n43876\n") == {38552, 43876}


def test_carriage_returns_do_not_break_it():
    """The output comes from a Windows process read in WSL."""
    assert session.parse_pids("38552\r\n43876\r\n") == {38552, 43876}


def test_blank_and_noise_lines_are_ignored():
    """PowerShell emits a trailing newline, and warnings can precede output."""
    assert session.parse_pids("\nWARNING: something\n38552\n\n") == {38552}


def test_no_processes_parses_to_an_empty_set_not_an_error():
    """Empty is a legitimate answer — no game is running."""
    assert session.parse_pids("") == set()


def test_the_query_filters_on_the_command_line_not_the_process_name():
    """THE BUG, pinned.

    Two ways to get this wrong, and the query must avoid both:

    - Matching the process NAME for "tmodloader" finds nothing, because the
      process is dotnet.exe. Silent, and everything downstream degrades quietly.
    - Matching dotnet.exe ALONE finds Roslyn's VBCSCompiler, which idles after
      any dotnet build, and reports the game running while it is closed.

    The command line is the only thing that separates them.
    """
    q = session._PID_QUERY

    assert "CommandLine" in q, "must filter on the command line"
    assert "tModLoader.dll" in q, "must look for the game's entry assembly"
    assert "dotnet.exe" in q, "the process is dotnet.exe, not tModLoader.exe"

    # And it must not be a bare name match, which is the VBCSCompiler trap.
    assert "-like" in q


def test_the_query_asks_cim_because_only_cim_exposes_the_command_line():
    """tasklist cannot report a command line, which is why this is not tasklist."""
    assert "Get-CimInstance" in session._PID_QUERY
