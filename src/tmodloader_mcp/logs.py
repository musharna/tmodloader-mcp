"""Finding tModLoader's logs, including the ones a finished run left behind.

WHY THIS IS NOT `(tml_dir / "client.log").read_text()`

tModLoader keeps its logs in `tModLoader-Logs/` and ZIPS THE PREVIOUS RUN'S into
`Old/` when a new run starts. That single fact is what this module exists for,
and missing it made the `logs` tool answer the wrong question at exactly the
moment it was documented to answer the right one:

    launch fails  ->  you launch again  ->  the failed run's log is now a zip,
                                            and client.log is the retry's, empty

So the tool reported on the run you were not asking about, with a log of the
right name. Nothing looked wrong. That is the same shape as every other defect
this repo has paid for: a confident answer of the correct type.

Measured on the real install rather than assumed: `client.log` opened at
01:10:32, the timestamp of a live run, while the newest archive in `Old/` was
written 16:18 the previous day - the run before it.

There are also six log files, not two. `Launch.log` and the `environment-*.log`
pair are where a launch that dies BEFORE the game starts writes, which is
precisely the failure `logs` is reached for, and none of them were addressable.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

#: Where tModLoader keeps them, relative to the install.
LOG_DIR = "tModLoader-Logs"

#: Where it moves the previous run's logs, as a zip per run.
ARCHIVE_DIR = "Old"


class LogError(RuntimeError):
    """The named log cannot be served, and the message says which rule."""


class LogMissing(LogError):
    """The name was fine; there is simply nothing there yet.

    Separate from its parent because the two deserve different treatment at the
    surface. A log that has never been written is the NORMAL state of a fresh
    install - the game has not run - and reporting that as an error makes an
    ordinary situation look broken. A bad NAME is a caller mistake, and stays
    loud. The old tool had this right and said so; keeping the distinction is
    what lets the new one keep saying it.
    """


def directory(tml_dir: Path) -> Path:
    return tml_dir / LOG_DIR


def available(tml_dir: Path) -> list[str]:
    """Every log file present right now, sorted, names only.

    Read off disk rather than listed as a constant: which logs exist depends on
    what was run. A server-only session writes no `client.log` at all, and a
    hardcoded set would offer one that was never there.
    """
    root = directory(tml_dir)
    if not root.is_dir():
        return []

    return sorted(entry.name for entry in root.iterdir() if entry.suffix == ".log")


def archives(tml_dir: Path) -> list[Path]:
    """Rotated runs, oldest first. The last is the run before this one."""
    old = directory(tml_dir) / ARCHIVE_DIR
    if not old.is_dir():
        return []

    return sorted(old.glob("*.zip"), key=lambda p: p.stat().st_mtime)


def read(tml_dir: Path, name: str, *, previous: bool = False) -> str:
    """One log's text, from the current run or the one before it.

    `name` is a filename and never a path. It is joined to a directory AND
    looked up inside a zip archive, and only one of those two is protected by
    the filesystem - a `..` inside a zip member name is checked by nothing at
    all, so the guard has to be on the name itself.
    """
    if name != Path(name).name or not name.endswith(".log"):
        raise LogError(
            f"{name!r} is not a log filename. Pass a name from `available`, "
            "such as client.log - never a path."
        )

    if previous:
        return _from_archive(tml_dir, name)

    target = directory(tml_dir) / name
    if not target.is_file():
        raise LogMissing(
            f"there is no {name!r} in {directory(tml_dir)}. Present: "
            f"{available(tml_dir)}"
        )

    return target.read_text(errors="replace")


def _from_archive(tml_dir: Path, name: str) -> str:
    """The named log out of the most recent rotated run."""
    found = archives(tml_dir)
    if not found:
        raise LogMissing(
            f"no archived runs in {directory(tml_dir) / ARCHIVE_DIR}, so there "
            "is no previous run to read. This is the first run since the logs "
            "were last cleared."
        )

    newest = found[-1]
    with zipfile.ZipFile(newest) as archive:
        held = archive.namelist()
        if name not in held:
            # A different answer from "no such log": that run may simply not
            # have had this side. A server-only run holds no client.log, and
            # conflating the two sends the reader looking for the wrong thing.
            raise LogMissing(
                f"{name!r} is not in the newest archive {newest.name}, which "
                f"holds {sorted(held)}. That run may not have had this side."
            )

        return archive.read(name).decode("utf-8", errors="replace")


def tail(text: str, *, contains: str | None = None, lines: int = 80) -> list[str]:
    """The last `lines` lines, optionally only those matching `contains`.

    Filtering happens BEFORE the tail, and the order is the whole value of the
    filter: filter-then-tail gives the last N matching lines, while
    tail-then-filter gives the matches among the last N - usually none, since
    the interesting line in a failure is near the top and the tail is shutdown
    noise.
    """
    if lines < 0:
        # ValueError rather than LogError: nothing is wrong with the log, the
        # ARGUMENT is wrong. Keeping the type the old tool raised also means the
        # test that pins this did not have to be edited to keep passing, which
        # is the kind of churn that hides a real behaviour change.
        raise ValueError(f"lines={lines} is not a number of lines to return")

    kept = text.splitlines()
    if contains:
        needle = contains.lower()
        kept = [line for line in kept if needle in line.lower()]

    # `kept[-lines:]` is a tail for every value but zero: -0 is 0, so it would
    # return the WHOLE log to a caller who asked for none of it.
    return kept[-lines:] if lines else []


@dataclass(frozen=True)
class Since:
    """New log text, and where to resume.

    Named fields rather than a tuple. `(text, offset, restarted)` reads the same
    as any other order at the call site, and this codebase has already paid for
    a positional seam once.
    """

    text: str
    next_offset: int
    restarted: bool


def read_since(tml_dir: Path, name: str, *, offset: int) -> Since:
    """Whatever was appended to a log after `offset` bytes, and the next offset.

    NOT A LIVE TAIL, and cannot be one. Tools here are synchronous and the game
    session is process-global, so a `launch` blocking for five minutes is not
    something another call can watch from the side. What this buys is the read
    BETWEEN calls: ask again later with the offset you were given and get only
    what is new, instead of re-reading a log that grows all run.

    BYTES, not lines or characters. A line count is not a resume point — lines
    are appended and the count of what you have already seen is not where the
    file continues — and a character offset is not a seek position in a file
    that is decoded with replacements.

    `restarted` is the case that matters. tModLoader ZIPS the previous run's
    logs and starts fresh, so an offset from a run that has since rotated points
    past the end of a file that is now shorter. Seeking there would report an
    empty log forever, which reads exactly like a quiet game. When the file is
    shorter than the offset the read starts from zero and says so, because "here
    is the whole thing again" is only correct if the caller is told why.
    """
    if offset < 0:
        raise ValueError(f"offset={offset} is not a position in a file")

    if name != Path(name).name or not name.endswith(".log"):
        raise LogError(
            f"{name!r} is not a log filename. Pass a name from `available`, "
            "such as client.log - never a path."
        )

    target = directory(tml_dir) / name
    if not target.is_file():
        raise LogMissing(
            f"there is no {name!r} in {directory(tml_dir)}. Present: "
            f"{available(tml_dir)}"
        )

    size = target.stat().st_size
    restarted = offset > size
    start = 0 if restarted else offset

    with target.open("rb") as handle:
        handle.seek(start)
        chunk = handle.read()

    # Decoded with replacement rather than strictly: a byte offset can land in
    # the middle of a multi-byte character, and one mangled glyph at a chunk
    # boundary is a far better outcome than refusing to return the log.
    return Since(
        text=chunk.decode("utf-8", errors="replace"),
        next_offset=start + len(chunk),
        restarted=restarted,
    )
