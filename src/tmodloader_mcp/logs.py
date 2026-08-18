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

import hashlib
import os
import time
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

    # UTF-8 by name, matching `_from_archive`'s explicit decode: the default
    # is the locale's, and a log read differently on two machines is a diff
    # nobody can explain.
    return target.read_text(encoding="utf-8", errors="replace")


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


#: How much of a log's head the identity fingerprint covers. Enough to span
#: the run-specific opening lines (timestamps, versions) that make two runs'
#: heads differ; small enough that computing it per call costs nothing.
_FINGERPRINT_BYTES = 4096


def _fingerprint(handle) -> str:
    """This log's identity right now: `<bytes covered>:<hash>`.

    The covered LENGTH rides along because the head of a young log is still
    growing - hashing "the first 4KB" of a 100-byte file and comparing it
    against the hash of the same file at 5KB would call ordinary growth a
    rotation. Recording how many bytes the hash covers lets the comparison
    re-hash exactly that many, which an append-only file can never change.
    """
    handle.seek(0)
    head = handle.read(_FINGERPRINT_BYTES)
    return f"{len(head)}:{hashlib.md5(head).hexdigest()[:12]}"


def _same_log(handle, recorded: str) -> bool:
    """Whether this file is the one `recorded` was taken from.

    Anything unreadable about the recorded fingerprint - an old caller's
    absent one, a hand-mangled value - is NO INFORMATION, and no information
    must not invent a rotation: the offset-versus-size check still catches
    the shrinking case exactly as it always did.
    """
    head_text, _, digest = recorded.partition(":")
    try:
        covered = int(head_text)
    except ValueError:
        return True

    if covered <= 0 or not digest:
        return True

    handle.seek(0)
    head = handle.read(covered)
    if len(head) < covered:
        # The file cannot even hold the head it used to have.
        return False

    return hashlib.md5(head).hexdigest()[:12] == digest


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
    #: This log's identity, to hand back on the next call. What catches the
    #: rotation `offset > size` cannot: a new run that has already outgrown
    #: the old offset, whose head this call would otherwise read mid-stream
    #: as a quiet continuation - silently skipping the start of the new run.
    fingerprint: str = ""
    #: True when `limit` stopped the read short of the file's end. The caller
    #: resumes from `next_offset`; nothing was dropped.
    truncated: bool = False


def read_since(
    tml_dir: Path,
    name: str,
    *,
    offset: int,
    fingerprint: str | None = None,
    limit: int | None = None,
) -> Since:
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

    `fingerprint` closes the rotation the size check cannot see: a new run
    that has already OUTGROWN the old offset. Without it the read starts at
    byte `offset` of the new file - mid-stream, `restarted=False` - and the
    head of the new run, which holds exactly the "did the mod load" lines a
    watcher is usually for, is silently skipped. Pass the previous call's
    back; None is the old contract and gets the old (size-only) check.

    ONLY WHOLE LINES ARE CONSUMED. The file is read to its end mid-write, so
    the final line is as likely as not a fragment - and returning it while
    advancing `next_offset` past it split every line that straddled a poll
    across two chunks, where nothing could ever match it whole: a `watch_for`
    needle landing there timed out reporting that a line on disk never
    happened. An unterminated tail is left for the next call, when its
    newline has arrived. One deliberate exception: a single line longer than
    `limit` passes through mid-line, because withholding it would make no
    call ever progress past it.

    `limit` bounds the BYTES one call returns - a resume point is what makes
    a bound safe, since the remainder is not dropped but waiting. Without one
    the whole remaining log comes back, which for `offset=0` on a long
    session is tens of MB in one answer.
    """
    if offset < 0:
        raise ValueError(f"offset={offset} is not a position in a file")

    if limit is not None and limit <= 0:
        raise ValueError(f"limit={limit} reads nothing, which is not a read")

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

    with target.open("rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        restarted = offset > size or (
            fingerprint is not None and not _same_log(handle, fingerprint)
        )
        start = 0 if restarted else offset

        remaining = max(0, size - start)
        budget = remaining if limit is None else min(limit, remaining)
        truncated = budget < remaining

        handle.seek(start)
        chunk = handle.read(budget)
        current = _fingerprint(handle)

    cut = chunk.rfind(b"\n")
    if cut >= 0:
        chunk = chunk[: cut + 1]
    elif not truncated:
        # No newline and no cap in the way: the whole chunk is one
        # unterminated fragment, still being written. Next call, whole line.
        chunk = b""
    # else: one line longer than the cap - the stated exception above.

    # Decoded with replacement rather than strictly: a byte offset can land in
    # the middle of a multi-byte character, and one mangled glyph at a chunk
    # boundary is a far better outcome than refusing to return the log.
    return Since(
        text=chunk.decode("utf-8", errors="replace"),
        next_offset=start + len(chunk),
        restarted=restarted,
        fingerprint=current,
        truncated=truncated,
    )


@dataclass(frozen=True)
class Watched:
    """What a `watch_for` saw, whether or not the line ever arrived.

    `next_offset` is here for the same reason `Since` has one: a watch that
    timed out without handing back a resume point forces the caller to start
    again from zero, which is precisely where the lines they have already seen
    are.
    """

    matched: bool
    lines: list[str]
    next_offset: int
    #: True if the log rotated at ANY point during the wait, not only on the
    #: last poll. The caller's original offset means nothing afterwards, and
    #: lines read after it came out of a different file than the ones before.
    restarted: bool
    elapsed: float
    polls: int
    #: The log's identity at the last poll, for resuming - `Since` has one for
    #: the same reason, and a watch that timed out is resumed exactly like a
    #: read that returned.
    fingerprint: str = ""


def watch_for(
    tml_dir: Path,
    name: str,
    *,
    contains: str | None,
    offset: int = 0,
    fingerprint: str | None = None,
    timeout: float = 60.0,
    poll: float = 1.0,
) -> Watched:
    """Block until a log line matches, or the budget runs out.

    WHAT THIS REPLACES: `log_since` answers "what has this log gained", which
    leaves a caller who is WAITING for something to write the loop themselves -
    read, check, sleep a guessed number, read again, give up eventually. That
    is the loop `Session.wait_until` removed for diag fields, wrong in the same
    two directions, and the short one is the dangerous one: the check runs
    before the line is written and reports that the thing never happened.

    THE OFFSET IS THE MECHANISM. Each poll resumes where the last stopped, so a
    line is matched exactly once - never missed in the gap between two polls,
    and never re-reported on the next. A watch that re-read from the top would
    match a line written before the wait began and call it news, which is how
    "wait for the crash" passes on the crash from the PREVIOUS run.

    `offset=0` therefore INCLUDES the log's history, deliberately: "did the mod
    load" is a question about a line that is usually already there. Pass a
    `next_offset` from an earlier call to watch only what comes after it.

    A TIMEOUT RETURNS RATHER THAN RAISES, carrying the resume point. A missing
    log still raises - that is not a line failing to arrive, it is nobody
    having been asked, and waiting it out would report a timeout against a game
    that is running perfectly.

    ONE BUDGET ACROSS EVERY POLL, the rule `_left_of` and `wait_until` follow.
    """
    if not contains:
        raise ValueError(
            "watch_for needs `contains` to say what to wait for. Without one it "
            "matches the first line written and is `read_since` wearing a "
            "longer name"
        )

    began = time.monotonic()
    deadline = began + timeout
    polls = 0
    restarted = False

    while True:
        polls += 1
        # Read BEFORE checking the clock, so a zero budget still gets one look.
        # A watch that never read at all would report "not found" about a file
        # it had not opened.
        #
        # The fingerprint rides from poll to poll, which is what lets a
        # rotation DURING the wait be seen even when the new run outgrows the
        # old offset between two polls - the case the size check alone misses.
        since = read_since(tml_dir, name, offset=offset, fingerprint=fingerprint)
        restarted = restarted or since.restarted
        offset = since.next_offset
        fingerprint = since.fingerprint

        found = tail(
            since.text, contains=contains, lines=len(since.text.splitlines()) or 1
        )
        if found:
            return Watched(
                matched=True,
                lines=found,
                next_offset=offset,
                restarted=restarted,
                elapsed=time.monotonic() - began,
                polls=polls,
                fingerprint=fingerprint,
            )

        left = deadline - time.monotonic()
        if left <= 0:
            break
        time.sleep(min(poll, left))

    return Watched(
        matched=False,
        lines=[],
        next_offset=offset,
        restarted=restarted,
        elapsed=time.monotonic() - began,
        polls=polls,
        fingerprint=fingerprint or "",
    )
