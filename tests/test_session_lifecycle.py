"""Lifecycle defects in the seam between this harness and a running game.

The first three were found by driving the real game rather than by reading
code, and were invisible to a suite that had never held a Windows process. The
rest are the same shape found by reading afterwards, once it was clear what
shape to look for: a file being written and read by two processes that never
agreed when it was finished, and a name whose uniqueness was guaranteed only
for as long as the object that generated it.
"""

from __future__ import annotations

import os
import struct
import time
import zlib
from pathlib import Path

import pytest

from tmodloader_mcp import session as session_mod
from tmodloader_mcp.session import Session
from tmodloader_mcp.triggers import (
    HEARTBEAT_MAX_AGE,
    Reply,
    TriggerError,
    artifacts_for,
)


def _client_heartbeat(path: Path, *, age: float = 0.0) -> None:
    """A REAL client heartbeat: world-ready, and genuinely `age` seconds old.

    Real files with real mtimes throughout, matching `test_heartbeat.py` -
    `os.utime` rather than a patched clock, because `heartbeat_is_live` reads
    `st_mtime` and a fake clock would test the fake.
    """
    path.write_text("dedServ: False\nworld-ready: True\n")
    if age:
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))


#: Comfortably past HEARTBEAT_MAX_AGE, so a backdated file is unambiguously
#: stale rather than merely old.
_WELL_PAST_MAX_AGE = HEARTBEAT_MAX_AGE + 30.0


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data))
    )


def _png(marker: bytes) -> bytes:
    """A REAL 1x1 PNG, carrying `marker` in a comment so two differ.

    Built rather than faked - signature, IHDR, a zlib-compressed IDAT, and IEND
    with correct CRCs - because a fixture assembled from whatever the check
    happens to look at today cannot disagree with the check tomorrow. These
    bytes open in an image viewer.
    """
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
        + _chunk(b"tEXt", b"Comment\x00" + marker)
        + _chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        + _chunk(b"IEND", b"")
    )


class FakeCfg:
    """The handful of Config fields these paths actually touch."""

    def __init__(self, root: Path, mod_name: str = "Biomancy"):
        self.root = root
        self.dotnet = Path("/fake/dotnet")
        self.tml_dir = Path("/fake/tml")
        self.taskkill = Path("/fake/taskkill")
        self.powershell = Path("/fake/powershell")
        self.world_win = r"C:\fake\World.wld"
        self.mod_name = mod_name
        # Derived here exactly as Config derives it, so these tests exercise the
        # real naming rule rather than a copy of one mod's filenames.
        self.artifacts = artifacts_for(mod_name)

    def artifact(self, name: str, *, server: bool) -> Path:
        return self.root / (f"server-{name}" if server else name)


def cfg_for(root: Path, mod_name: str = "Biomancy") -> FakeCfg:
    """A `FakeCfg` rooted at `root`, named for the callers that just want one."""
    return FakeCfg(root, mod_name)


class FakeWindows:
    """A process table a kill can actually change.

    The fake this replaces recorded which pids `taskkill` was aimed at and
    reported success for all of them - which is precisely the assumption the
    code under test was making, so it could not have disagreed with it. A
    double is only worth what it can contradict.

    `unkillable` is how a kill that does not take is expressed: taskkill still
    runs and still returns, and the process is still there afterwards.
    """

    def __init__(self, live: set[int], *, unkillable: frozenset[int] = frozenset()):
        self.live = set(live)
        self.unkillable = set(unkillable)
        self.aimed: list[int] = []

    def run(self, cmd, **kwargs):
        if "/PID" in cmd:
            pid = int(cmd[cmd.index("/PID") + 1])
            self.aimed.append(pid)
            if pid not in self.unkillable:
                self.live.discard(pid)

        class R:
            returncode = 0

        return R()

    def install(self, monkeypatch) -> None:
        # `_pid_table` is the one primitive every pid question routes through
        # now - `_tml_pids` and the stamp checks all derive from it, so a fake
        # patched one level up would leave the derived paths asking the real
        # machine. Empty stamps, deliberately: an unknown creation time
        # matches anything (see `_same_process`), which is the exact pre-stamp
        # behaviour these lifecycle tests pin.
        monkeypatch.setattr(
            session_mod, "_pid_table", lambda cfg: {pid: "" for pid in self.live}
        )
        monkeypatch.setattr(session_mod.subprocess, "run", self.run)
        # `time.sleep` is deliberately NOT patched here any more. It used to be,
        # to keep the settle poll from costing the suite anything, and it did
        # the opposite: the poll ends on `time.monotonic()`, so a no-op sleep
        # left the full ten seconds in place and removed the only thing yielding
        # during them. Tests that want a short settle now say so - see
        # TEST_SETTLE - which is a lever that actually moves the thing it names.


# ---- shot() ------------------------------------------------------------


def _session_that_captures(tmp_path, monkeypatch, payloads):
    """A Session whose `ask` writes the next payload where the game would."""
    cfg = FakeCfg(tmp_path)
    sess = Session(cfg=cfg, mode="server_client", port=1, player="n43n")
    queue = list(payloads)

    def fake_ask(self, command, *, argument=None, target=None, timeout=60.0):
        # The mod always writes ONE fixed filename. That is the whole bug: it
        # is not a thing this harness gets to choose, so the harness has to
        # stop handing that path back as if it were stable.
        #
        # The drop box is now PER PLAYER on the client side (`shot` reads
        # `self.artifacts.shot`, which carries `sess.player`), so the fixture
        # has to write where the session will actually look - `cfg.artifacts`
        # has no player and would silently miss it.
        cfg.artifact(sess.artifacts.shot, server=False).write_bytes(queue.pop(0))
        return Reply(command=command, text="ok")

    monkeypatch.setattr(Session, "ask", fake_ask)
    return sess


def test_two_captures_do_not_overwrite_each_other(tmp_path, monkeypatch):
    """THE REGRESSION THIS FILE EXISTS FOR.

    `shot()` returned the game's own fixed output path. Taking three regions in
    a row therefore returned the same path three times, and each capture
    clobbered the last - so a caller that took bottomleft, bottomright and full
    ended up holding three references to one file containing only `full`.

    Nothing failed. The calls all reported OK and returned a path that existed,
    which is why this survived a live run: the loss is silent, and the returned
    value looks correct in every log.
    """
    sess = _session_that_captures(
        tmp_path, monkeypatch, [_png(b"FIRST"), _png(b"SECOND")]
    )

    first = sess.shot("bottomleft")
    second = sess.shot("bottomright")

    assert first != second, (
        "both captures returned the same path, so the second overwrote the "
        "first and the caller cannot tell"
    )
    assert first.read_bytes() == _png(b"FIRST"), "the first capture was clobbered"
    assert second.read_bytes() == _png(b"SECOND")


def test_a_capture_survives_the_next_one_being_taken(tmp_path, monkeypatch):
    """The property a caller actually relies on, stated separately.

    The test above compares paths; this one holds a path across a later capture
    and reads it afterwards, which is what a caller collecting several regions
    before looking at any of them really does.
    """
    sess = _session_that_captures(
        tmp_path, monkeypatch, [_png(b"KEEP"), _png(b"LATER")]
    )

    kept = sess.shot("topleft")
    sess.shot("topright")

    assert kept.read_bytes() == _png(b"KEEP")


def test_a_second_session_does_not_overwrite_the_first_ones_captures(
    tmp_path, monkeypatch
):
    """THE HALF OF THE OVERWRITE BUG THE FIRST FIX DID NOT REACH.

    Numbering the captures stopped them colliding WITHIN a session, but the
    number came from a counter on the Session, and a Session is short-lived
    while the directory it writes into is not. Launch a second one - which is
    the normal way to work, since a session ends when the game is stopped - and
    its first capture is `-001-` again, landing on top of the first session's.

    Same silent loss as before, wearing the fix as a disguise: the path returned
    is unique within the call that returned it, so nothing about it looks wrong.

    The counter was never the right source. A name has to be unique in the
    NAMESPACE it is written into, and that namespace is the save directory.
    """
    first = _session_that_captures(tmp_path, monkeypatch, [_png(b"SESSION-ONE")])
    kept = first.shot("full")

    second = _session_that_captures(tmp_path, monkeypatch, [_png(b"SESSION-TWO")])
    later = second.shot("full")

    assert kept != later, (
        "a fresh session restarted the numbering and reused a name already on "
        "disk from the previous one"
    )
    assert kept.read_bytes() == _png(b"SESSION-ONE"), (
        "the second session's first capture overwrote the first session's"
    )
    assert later.read_bytes() == _png(b"SESSION-TWO")


# ---- shot(), and whether what landed is a picture -----------------------


def test_a_complete_png_is_accepted(tmp_path, monkeypatch):
    """THE POSITIVE CONTROL for the two refusals below.

    Asserted in its own test rather than left implied by them, because a check
    that refuses everything passes every negative test ever written. This is the
    one that fails if the PNG check is too strict, and it holds the bytes a real
    capture is made of rather than a marker.
    """
    sess = _session_that_captures(tmp_path, monkeypatch, [_png(b"REAL")])

    kept = sess.shot("full")

    assert kept.read_bytes() == _png(b"REAL")
    assert kept.exists()


def test_a_reply_that_is_not_a_png_is_refused(tmp_path, monkeypatch):
    """`shot` reported success on a file it never opened.

    The drop file was waited for and renamed; not one byte was read. So anything
    landing on that name was promoted into the capture directory and its path
    handed back as a picture - and the caller's next move is to open it, one
    round trip later and somewhere else, where the error names the reader rather
    than the capture that was never taken.

    The README recorded this as an absent guarantee rather than quietly adding
    one, which is what makes it a known gap rather than a discovered bug.
    """
    sess = _session_that_captures(tmp_path, monkeypatch, [b"REFUSED: no back buffer"])

    with pytest.raises(TriggerError) as caught:
        sess.shot("full")

    assert "png" in str(caught.value).lower(), (
        "the failure has to name what was wrong with the file, or it reads as "
        "the game never answering"
    )


def test_a_png_still_being_written_is_not_returned(tmp_path, monkeypatch):
    """A file exists when it is CREATED, not when it is finished.

    The same race `_await_text` was written to close, arriving through the one
    artifact that was never read. A capture large enough to be worth taking is
    large enough to be caught mid-write, and a truncated PNG has a perfectly
    valid signature - so a check that looked only at the first eight bytes would
    promote half a picture and call it a capture.

    Waited for rather than refused on sight: the writer is expected to finish,
    and refusing the moment the file is short would turn a slow write into a
    failure. It fails only when the timeout runs out with the file still
    incomplete.
    """
    truncated = _png(b"HALF")[:-8]
    sess = _session_that_captures(tmp_path, monkeypatch, [truncated])

    with pytest.raises(TriggerError) as caught:
        sess.shot("full", timeout=1.0)

    assert "png" in str(caught.value).lower()


def test_a_refused_capture_is_not_left_in_the_capture_directory(tmp_path, monkeypatch):
    """A refusal must not still produce a numbered capture.

    Promoting the bad file and then raising would leave `captures` listing it,
    `read_capture` serving it, and the next capture numbered around it - so the
    failure would be reported once and the corrupt artifact would outlive it.
    """
    sess = _session_that_captures(tmp_path, monkeypatch, [b"not a picture"])

    with pytest.raises(TriggerError):
        sess.shot("full")

    numbered = list(tmp_path.glob("*-0*-full.png"))
    assert numbered == [], f"a refused capture was promoted anyway: {numbered}"


# ---- launch() ----------------------------------------------------------


def test_launch_clears_a_stale_per_player_reply(tmp_path):
    """The cleanup comment's own scenario, now reachable.

    A per-player diag left by a dead run is exactly what lets a readiness
    check pass against a process that is gone.
    """
    stale = tmp_path / "biomancy-diag-n43n-003f.txt"
    stale.write_text("from a previous run\n")
    fresh = tmp_path / "biomancy-diag.txt"
    fresh.write_text("also stale\n")

    session_mod._clear_stale_artifacts(cfg_for(tmp_path), player="n43n")

    assert not stale.exists()
    # Positive control: the unsuffixed form was being cleared before this
    # change and must still be, so a green result cannot mean "cleared
    # nothing".
    assert not fresh.exists()


def test_launch_leaves_another_players_stale_files_alone(tmp_path):
    """The other half of the same fix, stated as its own test.

    `client_files` reports a dead player's leftover heartbeat with an age, so
    it reads as not-live rather than as a phantom client - but only if this
    cleanup does not delete files that are not this session's to delete.

    FIX ROUND 2, IMPORTANT 2: the original version of this test asserted only
    that `someone_elses` survives, which a `_clear_stale_artifacts` that
    deleted NOTHING AT ALL - or was never called - would also pass. This
    plants THIS player's own stale file alongside the other player's, so the
    same test proves both halves: the file that must go is gone, and the file
    that must stay survives.
    """
    someone_elses = tmp_path / "biomancy-diag-big-bird-44a3.txt"
    someone_elses.write_text("a different player's dead run\n")

    # Positive control, in the SAME test: this player's own stale artifact
    # MUST be deleted by the same call.
    this_players_own = tmp_path / "biomancy-diag-n43n-003f.txt"
    this_players_own.write_text("this player's own dead run\n")

    session_mod._clear_stale_artifacts(cfg_for(tmp_path), player="n43n")

    assert someone_elses.exists(), (
        "a per-player artifact belonging to a DIFFERENT player was deleted - "
        "it is not this session's to clear"
    )
    assert not this_players_own.exists(), (
        "this player's own stale artifact survived the cleanup - a green "
        "result on the assertion above cannot mean the cleanup did nothing"
    )


def test_launch_leaves_a_pending_request_addressed_to_another_player_alone(tmp_path):
    """FIX ROUND 3, IMPORTANT 1: the cleanup undid the claim it was shipped with.

    The trigger is the ONE artifact deliberately shared between sessions - it
    is how a client learns a request is aimed at somebody else - and it was in
    `cfg.artifacts.all`, so every launch deleted it. Developer A launching a
    game therefore destroyed developer B's claimed, in-flight request while
    B's game was still running and still polling for it: exactly the silent
    loss `os.link` was introduced to remove, arriving by a different door.

    The rule is the mod's own (`DevResponder`: a request it is not addressed
    by is left exactly where it is), so this deletes only what is this
    session's to delete.
    """
    cfg = cfg_for(tmp_path)
    trigger = cfg.artifact(cfg.artifacts.trigger, server=False)

    trigger.write_text("diag@big-bird")
    session_mod._clear_stale_artifacts(cfg, player="n43n")

    assert trigger.exists() and trigger.read_text() == "diag@big-bird", (
        "a launch deleted a request addressed to another player - that client "
        "is still polling for it and will now wait out its whole timeout"
    )

    # POSITIVE CONTROL, in the same test: a cleanup that had simply stopped
    # touching the trigger at all would pass the assertion above while
    # leaving this session's own dead request wedged in the shared slot
    # forever - which is the only escape hatch an unaddressable target has.
    trigger.write_text("diag@n43n")
    session_mod._clear_stale_artifacts(cfg, player="n43n")

    assert not trigger.exists(), (
        "a launch left behind a pending request addressed to its OWN player - "
        "nothing else will ever collect it, so the shared slot stays wedged"
    )

    # And the third case, for the same reason: a payload the mod's parser
    # cannot read is addressed to nobody and will be answered by nobody.
    trigger.write_text("@@@")
    session_mod._clear_stale_artifacts(cfg, player="n43n")

    assert not trigger.exists(), (
        "a malformed request survived a launch - no client can parse it, so "
        "leaving it in place wedges the slot on nobody's behalf"
    )


def test_launch_leaves_the_capture_lock_and_stamp_alone(tmp_path):
    """They are in `all` so the inventory guard still sees them, and skipped
    where `all` gets deleted - the trigger's arrangement, for the trigger's
    reason.

    A launch that deleted the lock would take it from a session that is
    capturing right now, which is the same lost update `_release_trigger`
    exists to prevent, arriving by a third door. A lock left by a DEAD run is
    distinguished by its age instead, not by a launch's optimism.
    """
    cfg = cfg_for(tmp_path)
    lock = cfg.artifact(cfg.artifacts.capture_lock, server=False)
    stamp = cfg.artifact(cfg.artifacts.capture_stamp, server=False)

    lock.write_text("another session, capturing right now")
    stamp.write_text("1000.0")

    session_mod._clear_stale_artifacts(cfg, player="n43n")

    assert lock.exists(), "a launch took the capture lock from a live session"
    assert stamp.exists()

    # POSITIVE CONTROL, same test: the cleanup still clears what it should,
    # so this cannot pass by `_clear_stale_artifacts` having stopped working.
    heartbeat = cfg.artifact(cfg.artifacts.heartbeat, server=False)
    heartbeat.write_text("from a previous run")
    session_mod._clear_stale_artifacts(cfg, player="n43n")
    assert not heartbeat.exists()


def test_launch_breaks_a_capture_lock_left_by_a_dead_run(tmp_path):
    """Task 2 stopped a launch deleting the lock unconditionally, which was
    right and left the dead-run case unhandled. Age is what separates them.
    """
    cfg = cfg_for(tmp_path)
    lock = cfg.artifact(cfg.artifacts.capture_lock, server=False)

    lock.write_text("from a run that died")
    old = time.time() - (session_mod.CAPTURE_LOCK_STALE + 5)
    os.utime(lock, (old, old))

    session_mod._clear_stale_artifacts(cfg, player="n43n")
    assert not lock.exists()

    # POSITIVE CONTROL: a fresh lock still survives a launch, which is the
    # invariant Task 2 established and this must not undo.
    lock.write_text("another session, capturing right now")
    session_mod._clear_stale_artifacts(cfg, player="n43n")
    assert lock.exists()


def test_a_pending_request_is_read_the_way_the_mod_reads_it(tmp_path):
    """The two sides must agree on what "unreadable" means, byte for byte.

    `_is_ours_to_clear` treats an unreadable payload as this session's, and
    that is sound ONLY while the mod agrees it is unreadable too - the whole
    justification is "no client will ever consume it". But the mod does not
    have this side's failure mode: `File.ReadAllText` never throws on bad
    UTF-8, it substitutes U+FFFD and parses what is left. So a payload with an
    invalid byte in its VERB and a clean target decodes to a perfectly good
    request over there, gets consumed by the client it names - and read here
    as unreadable, hence as ours, hence deleted out from under them.

    Nothing this harness writes can produce it (it writes UTF-8), which is why
    this went unnoticed; a request typed from a shell, PowerShell, or an editor
    with the wrong encoding can. Reading with `errors="replace"` is not error
    tolerance for its own sake - it is this side agreeing to make the same
    substitution the mod makes, so both reach the same verdict on the same
    bytes.
    """
    cfg = cfg_for(tmp_path)
    trigger = cfg.artifact(cfg.artifacts.trigger, server=False)

    # Invalid as UTF-8, but only in the verb: latin-1 "é" in "café". The mod
    # substitutes it and still parses the target "big-bird" out of the rest.
    trigger.write_bytes(b"shot:caf\xe9@big-bird")
    session_mod._clear_stale_artifacts(cfg, player="n43n")

    assert trigger.exists(), (
        "a launch deleted a request whose target the mod can still read - "
        "that client will consume it, or would have, and is now waiting on a "
        "request this side threw away for being undecodable to this side only"
    )

    # POSITIVE CONTROL, same test, same bad byte: substituting instead of
    # giving up must not turn into "never clear anything". A payload that
    # really is ours after substitution still goes.
    trigger.write_bytes(b"shot:caf\xe9@n43n")
    session_mod._clear_stale_artifacts(cfg, player="n43n")

    assert not trigger.exists(), (
        "a launch left its OWN request wedged in the shared slot because one "
        "byte of the verb was not UTF-8"
    )


def _fake_launch_world(monkeypatch, *, existing, after, ready_raises):
    """Wire launch()'s dependencies so no game is started.

    Backed by a real (fake) process table rather than a canned sequence of
    answers: spawning puts `after` in it and killing takes pids out of it, so
    what these tests read back is a consequence of what happened rather than a
    reply arranged in advance.
    """
    windows = FakeWindows(existing)
    windows.install(monkeypatch)
    monkeypatch.setattr(session_mod, "world_problem", lambda w: None)

    def fake_popen(*a, **k):
        # Whole set per spawn, not one pid each: how many processes a mode
        # starts is the code's business, and pairing them here would make this
        # helper break when that changes rather than when behaviour does.
        windows.live |= set(after)

    monkeypatch.setattr(session_mod.subprocess, "Popen", fake_popen)

    def fake_wait(cfg, *, mode, player, timeout, port=None):
        if ready_raises:
            raise session_mod.SessionError("no live heartbeat within 300s")

    monkeypatch.setattr(session_mod, "_wait_ready", fake_wait)
    return windows.aimed


def test_a_failed_launch_kills_what_it_spawned(monkeypatch):
    """THE LEAK.

    `launch` started the processes and then waited for readiness. When the wait
    failed it raised - and the processes it had just started stayed up, held by
    nobody. No Session ever reached the caller, so `stop()` answered "no session
    was running" and killed nothing. Three orphaned games in one afternoon,
    every one of them found by hand with tasklist.

    Whoever spawns owns the processes until it can hand them back.
    """
    killed = _fake_launch_world(
        monkeypatch, existing=set(), after={4808, 42224}, ready_raises=True
    )

    with pytest.raises(session_mod.SessionError):
        session_mod.launch(FakeCfg(Path("/tmp")), "server_client", port=1)

    assert sorted(killed) == [4808, 42224], (
        f"a failed launch left {sorted({4808, 42224} - set(killed))} running"
    )


def test_a_successful_launch_keeps_its_processes(monkeypatch):
    """POSITIVE CONTROL, and the one that stops the fix going too far.

    A teardown-on-failure that also fired on success would kill the game the
    caller just asked for, and the test above would still pass - it only knows
    that something was killed. This pins the other direction.
    """
    killed = _fake_launch_world(
        monkeypatch, existing=set(), after={4808}, ready_raises=False
    )

    sess = session_mod.launch(FakeCfg(Path("/tmp")), "server_client", port=1)

    assert killed == [], "a successful launch killed its own game"
    assert sess.started == {4808}, "the session did not record what it started"


def test_refusing_to_launch_over_a_running_game_kills_nothing(monkeypatch):
    """The other direction of the same worry, and why `stop()` is surgical: a
    developer usually has their own game open.

    `launch` refuses outright when tModLoader is already running, and that
    refusal happens BEFORE anything is spawned. The cleanup added for the leak
    must not reach it - otherwise the fix for an orphaned process becomes a fix
    that closes the game you were playing.
    """
    killed = _fake_launch_world(
        monkeypatch, existing={31337}, after={31337}, ready_raises=False
    )

    with pytest.raises(session_mod.SessionError) as e:
        session_mod.launch(FakeCfg(Path("/tmp")), "server_client", port=1)

    assert "already running" in str(e.value), (
        "it refused for the wrong reason - the already-running guard must come "
        "before any mode-specific refusal, or this stops testing what it says"
    )
    assert killed == [], "refusing to launch killed the game that was already up"


# ---- readiness failure advice ------------------------------------------


def _wait_ready_error(tmp_path, monkeypatch, mode):
    """Drive _wait_ready to its timeout with no heartbeats on disk."""
    cfg = FakeCfg(tmp_path)
    monkeypatch.setattr(session_mod, "heartbeat_is_live", lambda p: False)

    with pytest.raises(session_mod.SessionError) as e:
        session_mod._wait_ready(cfg, mode=mode, player="n43n", timeout=0.0)

    return str(e.value)


def test_server_mode_is_refused_because_it_cannot_ever_be_ready(monkeypatch):
    """MEASURED, THEN REFUSED - the same treatment singleplayer already gets.

    A dedicated server runs NO ModSystem update hooks until a client connects,
    so the mod never polls, never writes a heartbeat, and `launch`'s promise -
    "started AND able to answer" - cannot be met by a server on its own.

    Measured 2026-08-07 on one server process, changing only whether a client
    was attached: alone for 90s it wrote nothing anywhere on disk; 30s after a
    client joined THAT SAME PROCESS its heartbeat appeared, reporting
    `polls: 1` and `hooks-seen: PostUpdateEverything,PostUpdateWorld`.

    So this is refused up front rather than after a five-minute timeout, and
    the refusal carries the measurement - the previous version spent 300s
    rediscovering it and then guessed at why.
    """
    killed = _fake_launch_world(
        monkeypatch, existing=set(), after={4808}, ready_raises=False
    )

    with pytest.raises(session_mod.SessionError) as e:
        session_mod.launch(FakeCfg(Path("/tmp")), "server", port=1)

    message = str(e.value)
    assert "server_client" in message, "it should point at the mode that works"
    assert "client" in message, "it should say what is missing"
    assert killed == [], "it should refuse before starting anything, not clean up after"


def test_a_server_heartbeat_missing_under_server_client_does_not_blame_steam(
    tmp_path, monkeypatch
):
    """THE MISATTRIBUTION THAT COST A DEBUGGING SESSION, kept as a live case.

    The advice was once unconditional, so a run with no client involved was
    told to check its client's Steam login. Steam happened to be down, which
    made the wrong advice fit, and two different failures got filed under one
    cause.

    `server` mode is refused outright now, but this branch is still reachable
    and still matters: under `server_client`, the CLIENT can be up while the
    SERVER heartbeat is missing. Steam is not the explanation for that one.
    """
    cfg = FakeCfg(tmp_path)
    client_hb = cfg.artifact("biomancy-hooks.txt", server=False)
    # `_wait_ready` checks the two names THIS launch's client could
    # legitimately be writing under (unsuffixed, or this player's token), so
    # a client that is "up" has to be a real file on disk for it to find -
    # content is irrelevant since `world_is_ready` is monkeypatched below.
    client_hb.touch()
    monkeypatch.setattr(
        session_mod, "heartbeat_is_live", lambda p: p.name == client_hb.name
    )
    monkeypatch.setattr(session_mod, "world_is_ready", lambda text: False)

    with pytest.raises(session_mod.SessionError) as e:
        session_mod._wait_ready(cfg, mode="server_client", player="n43n", timeout=0.0)

    message = str(e.value)
    assert "Steam is NOT the likely cause" in message
    assert "join" in message, "it should say the server reports once a client joins"
    assert "No client is involved" not in message, (
        "a client IS involved here - it is up, and the SERVER is the missing "
        "one. The sentence was written for a mode that no longer reaches this."
    )


def test_client_mode_failure_still_blames_steam(tmp_path, monkeypatch):
    """POSITIVE CONTROL.

    Steam really is the usual cause when a client is involved - that advice was
    correct and load-bearing, and a fix that removed it everywhere would trade
    one misdiagnosis for another.
    """
    message = _wait_ready_error(tmp_path, monkeypatch, "server_client")

    assert "Steam" in message
    assert "NOT the likely cause" not in message


def test_wait_ready_follows_the_clients_heartbeat_wherever_it_moves(
    tmp_path, monkeypatch
):
    """FIX ROUND 1: `_wait_ready` watched ONE FIXED PATH for the client side.

    That path is the unsuffixed `<mod>-hooks.txt`, and Task 2's per-player
    naming means the mod stops writing it the moment a character loads -
    which is EXACTLY when `world-ready` turns True, the other half of what
    this function waits for. Pinned to the fixed path, the check would watch
    a file the mod had just stopped updating and time out on a game running
    perfectly.

    Two independent, real ways to reach that:

    1. The unsuffixed file appears first, live and world-ready - then the mod
       picks up a character and switches to a per-player name; the unsuffixed
       file ages past HEARTBEAT_MAX_AGE while the per-player one stays fresh.
    2. The client is launched already knowing its player (`-player <name>
       -join`), so the unsuffixed file NEVER EXISTS AT ALL - only the
       per-player name ever appears, from the very first heartbeat.

    All three cases below must be READY (the call must not raise). Real files
    with real mtimes throughout - see `_client_heartbeat`.
    """
    monkeypatch.setattr(session_mod.time, "sleep", lambda s: None)
    cfg = FakeCfg(tmp_path)
    server_hb = cfg.artifact(cfg.artifacts.heartbeat, server=True)
    _client_heartbeat(server_hb)

    unsuffixed = tmp_path / "biomancy-hooks.txt"
    per_player = tmp_path / "biomancy-hooks-n43n-003f.txt"

    # Case 1: unsuffixed alone, live and world-ready. TODAY'S BEHAVIOUR - must
    # not regress.
    _client_heartbeat(unsuffixed)
    session_mod._wait_ready(cfg, mode="server_client", player="n43n", timeout=1.0)

    # Case 2: the mod switches to a per-player name; the unsuffixed file goes
    # STALE (backdated past HEARTBEAT_MAX_AGE) rather than being deleted -
    # which is what actually happens on disk, since nothing rewrites it once
    # the mod stops touching it. Against the PRE-FIX code this is the failure:
    # it watches only `unsuffixed`, finds it stale, and never looks at
    # `per_player` at all.
    _client_heartbeat(per_player)
    _client_heartbeat(unsuffixed, age=_WELL_PAST_MAX_AGE)
    session_mod._wait_ready(cfg, mode="server_client", player="n43n", timeout=1.0)

    # Case 3: the unsuffixed file never existed - the client knew its player
    # from its very first tick. Against the pre-fix code this is the same
    # failure by a different route: the one path it watches was never written.
    unsuffixed.unlink()
    session_mod._wait_ready(cfg, mode="server_client", player="n43n", timeout=1.0)


def test_wait_ready_positive_control_no_live_client_heartbeat_at_all(
    tmp_path, monkeypatch
):
    """POSITIVE CONTROL for the test above.

    A `_wait_ready` that simply accepted anything - or that stopped checking
    the client side at all while chasing the fix above - would pass every
    case in that test. This pins the other direction: with no client
    heartbeat of any name, live or stale, readiness must NOT be satisfied.
    """
    monkeypatch.setattr(session_mod.time, "sleep", lambda s: None)
    cfg = FakeCfg(tmp_path)
    server_hb = cfg.artifact(cfg.artifacts.heartbeat, server=True)
    _client_heartbeat(server_hb)

    with pytest.raises(session_mod.SessionError) as e:
        session_mod._wait_ready(cfg, mode="server_client", player="n43n", timeout=0.0)

    assert "no client heartbeat of any name appeared" in str(e.value)


def test_wait_ready_ignores_another_players_leftover_heartbeat(tmp_path, monkeypatch):
    """FIX ROUND 2, IMPORTANT 1: a directory-wide walk re-opened the hole
    `_clear_stale_artifacts` exists to close.

    `_clear_stale_artifacts` deliberately leaves another player's files alone
    (see its docstring) - they are not this session's to delete, and the
    `heartbeat` tool that reads them shows a human their age. `_wait_ready`
    shows nobody an age: it collapses a file to one ready/not-ready bit, so a
    leftover from a DIFFERENT player's stopped session - fresh enough to still
    be "live" if the turnaround was under HEARTBEAT_MAX_AGE - must not be
    mistaken for THIS launch's client.
    """
    monkeypatch.setattr(session_mod.time, "sleep", lambda s: None)
    cfg = FakeCfg(tmp_path)
    server_hb = cfg.artifact(cfg.artifacts.heartbeat, server=True)
    _client_heartbeat(server_hb)

    # A DIFFERENT player's heartbeat: fresh, world-ready, left behind by a
    # session that already stopped. Nothing for THIS launch's player ("n43n")
    # exists yet.
    someone_elses = tmp_path / "biomancy-hooks-big-bird-44a3.txt"
    _client_heartbeat(someone_elses)

    with pytest.raises(session_mod.SessionError) as e:
        session_mod._wait_ready(cfg, mode="server_client", player="n43n", timeout=0.1)
    assert "no client heartbeat of any name appeared" in str(e.value), (
        "another player's leftover heartbeat was accepted as this launch's "
        "client - it is not one of the names this player could be writing"
    )

    # Positive control in the SAME test: THIS launch's own player heartbeat
    # appearing IS what satisfies readiness - a green result above cannot mean
    # the check rejects everything regardless of what is on disk.
    this_players = tmp_path / "biomancy-hooks-n43n-003f.txt"
    _client_heartbeat(this_players)
    session_mod._wait_ready(cfg, mode="server_client", player="n43n", timeout=1.0)


# ---- the two file races --------------------------------------------------


def test_the_trigger_file_is_never_written_where_the_game_is_watching(
    tmp_path, monkeypatch
):
    """A POLLED PATH MUST NOT BE A WRITE TARGET.

    The game finds out what to do by polling one filename, so anything written
    there in place is visible to it half-finished. A truncated word is not
    rejected - `DevCommands.Parse` maps anything it does not recognise to
    Unknown and does nothing - so the harness sits waiting for a reply to a
    request the game already discarded, and reports a hang.

    That is the failure this whole module keeps rediscovering: not an error,
    but an action that quietly did not happen. So the payload is completed
    under a name nothing is watching, and only then given the watched name, by
    a rename - which either has happened or has not.
    """
    cfg = FakeCfg(tmp_path)
    sess = Session(cfg=cfg, mode="server_client", port=1, player="n43n")

    trigger = cfg.artifact(cfg.artifacts.trigger, server=False)
    # `ask` composes against what the mod PUBLISHED, so a responder has to have
    # left a list behind - which a real one does at load, before any request.
    cfg.artifact(cfg.artifacts.commands, server=False).write_text(
        "diag\tnoarg\tstate dump\n"
    )
    # No game is going to answer, and `ask` deletes any reply it finds before
    # writing - deliberately, so a stale answer cannot be read as a fresh one.
    # So the reply is stubbed rather than planted: this test is about the write.
    monkeypatch.setattr(Session, "_await_text", lambda self, p, **kw: "ok")

    written: list[Path] = []
    real_write_text = Path.write_text

    def spy(self, *args, **kwargs):
        written.append(Path(self))
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", spy)

    sess.ask("diag")

    assert trigger not in written, (
        "the payload was written straight into the file the game polls, so the "
        "game can read it half-written"
    )
    assert written, "nothing was written at all - the test is not exercising ask()"
    assert trigger.read_text() == "diag@n43n", "the trigger never arrived intact"

    staged = written[-1]
    assert staged.parent == trigger.parent, (
        f"staged at {staged} - a rename onto {trigger} only stays atomic within "
        "one filesystem, and the save directory is on /mnt/c"
    )


def _reader_that_returns(monkeypatch, path: Path, chunks):
    """Make `path` read back each of `chunks` in turn - a write in progress."""
    remaining = list(chunks)
    real_read_text = Path.read_text

    def fake(self, *args, **kwargs):
        if Path(self) == path:
            return remaining.pop(0) if len(remaining) > 1 else remaining[0]
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake)


def test_a_reply_is_not_read_until_it_stops_changing(tmp_path, monkeypatch):
    """THE SECOND RACE, WHICH HAD A TIMER ON IT RATHER THAN A FIX.

    A file appears the moment it is created, not when it is finished, so the
    read was preceded by a fixed 0.4s sleep and a comment admitting exactly
    that. A sleep does not make the write complete - it makes it USUALLY
    complete, and a slower machine or a bigger dump moves the line.

    What makes it dangerous rather than merely flaky is that a short read is
    still a valid-looking answer: `Reply.ok` reads `PART` as success, and a
    truncated diag parses into fields with plausible values and missing keys.

    So wait for the content to stop changing instead of waiting a while and
    hoping.
    """
    sess = Session(cfg=FakeCfg(tmp_path), mode="server", port=1, player="n43n")

    reply = tmp_path / "reply.txt"
    reply.write_text("PART")
    _reader_that_returns(monkeypatch, reply, ["PART", "PARTIAL", "PARTIAL"])

    assert sess._await_text(reply, timeout=5.0, what="reply") == "PARTIAL"


def test_a_finished_reply_is_returned_without_a_second_thought(tmp_path, monkeypatch):
    """POSITIVE CONTROL.

    Waiting for stability must not turn every read into a wait: a reply that is
    already complete - the overwhelmingly common case - has to come straight
    back, or every call in a session pays for a race that is not happening.
    """
    sess = Session(cfg=FakeCfg(tmp_path), mode="server", port=1, player="n43n")

    reply = tmp_path / "reply.txt"
    reply.write_text("DONE")

    started = time.monotonic()
    assert sess._await_text(reply, timeout=5.0, what="reply") == "DONE"
    assert time.monotonic() - started < 2.0, "a settled file should not be waited on"


def test_an_empty_file_is_a_write_in_progress_not_an_empty_answer(
    tmp_path, monkeypatch
):
    """The stability check's own edge, and the one that would undo it.

    A writer that has created the file but put nothing in it yet reads back ""
    twice in a row, which is "stable" by the letter of the rule and a torn read
    by any other measure. The mod has no command that answers with nothing, so
    emptiness here means not-yet rather than nothing.
    """
    sess = Session(cfg=FakeCfg(tmp_path), mode="server", port=1, player="n43n")

    reply = tmp_path / "reply.txt"
    reply.write_text("")
    _reader_that_returns(monkeypatch, reply, ["", "", "OK", "OK"])

    assert sess._await_text(reply, timeout=5.0, what="reply") == "OK"


def test_a_reply_that_never_settles_times_out_rather_than_hanging(
    tmp_path, monkeypatch
):
    """The bound on the loop above, and the reason it is a bound and not a hang.

    A file that keeps changing means the writer is stuck or looping, and there
    is no reading of it that is safe to hand back. Failing after the timeout
    says so; waiting forever says nothing at all, from a tool whose callers
    have no way to interrupt it.
    """
    sess = Session(cfg=FakeCfg(tmp_path), mode="server", port=1, player="n43n")

    reply = tmp_path / "reply.txt"
    reply.write_text("0")

    counter = iter(range(10_000))
    real_read_text = Path.read_text

    def never_settles(self, *args, **kwargs):
        if Path(self) == reply:
            return str(next(counter))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", never_settles)

    with pytest.raises(TriggerError):
        sess._await_text(reply, timeout=1.0, what="reply")


# ---- stop() ------------------------------------------------------------


#: What these tests pass for `settle`. Small because nothing here is really
#: waiting on Windows, and explicit because the alternative - reaching into the
#: clock or the sleep - is what went wrong before. See SETTLE in the module docs.
TEST_SETTLE = 0.05


def _stopping(monkeypatch, *, started, live, unkillable=frozenset()):
    """A session that believes it started `started`, over a table holding `live`."""
    windows = FakeWindows(live, unkillable=unkillable)
    windows.install(monkeypatch)

    session = Session(
        cfg=FakeCfg(Path("/tmp")), mode="server_client", port=1, player="n43n"
    )
    session.started = set(started)
    return windows, session


def test_stop_waits_for_the_settle_it_was_given_and_not_a_constant(monkeypatch):
    """THE ONE NOTHING WAS ASKING.

    Every other timed operation here takes its bound as an argument - `ask`,
    `launch`, `build`, `_await_text`. `stop` alone read a module constant, so a
    test could not shorten it and reached for the only lever left: patching
    `time.sleep` to a no-op.

    That lever moves nothing. The loop ends on `time.monotonic()`, so removing
    the sleep does not shorten the wait - it removes the only thing yielding
    during it, turning a twenty-iteration poll into a ten-second spin at full
    CPU. Twice, for twenty of the suite's twenty-three seconds, under a comment
    claiming it made the wait cost nothing.

    A bound nothing can set is a bound nothing can check.
    """
    _, session = _stopping(monkeypatch, started={4808}, live={4808}, unkillable={4808})

    started_at = time.monotonic()
    with pytest.raises(session_mod.SessionError):
        session_mod.stop(session.cfg, session, settle=TEST_SETTLE)
    elapsed = time.monotonic() - started_at

    # Generous against a loaded CI box, and still two orders of magnitude below
    # the ten-second constant this used to wait regardless of what it was told.
    assert elapsed < 1.0, (
        f"stop took {elapsed:.1f}s for a {TEST_SETTLE}s settle - it is waiting "
        "on something other than the bound it was given"
    )


def test_stop_does_not_report_killing_something_that_is_still_running(monkeypatch):
    """ISSUING A KILL IS NOT EVIDENCE THAT ANYTHING DIED.

    `stop` ran taskkill and then appended the pid to its result, and the two
    were never connected: the exit code is deliberately ignored - a pid that
    died between the listing and the kill exits non-zero, and that is the
    outcome we wanted - but the same ignoring absorbs the opposite case, a
    taskkill that is refused while the process carries on.

    What comes back then is the worst kind of wrong: a list of pids, of the
    right shape, that reads as a completed teardown. The failure surfaces later
    and somewhere else, as the NEXT launch refusing to start over a game
    nobody remembers leaving open.

    The fix is not a better reading of taskkill's exit code. It is to ask the
    process table, which is the thing the claim has to be true about - the same
    move as numbering captures from the directory rather than from a counter.
    """
    windows, session = _stopping(
        monkeypatch, started={4808}, live={4808}, unkillable={4808}
    )

    with pytest.raises(session_mod.SessionError) as e:
        session_mod.stop(session.cfg, session, settle=TEST_SETTLE)

    assert windows.aimed == [4808], "it never even tried to kill it"
    assert "4808" in str(e.value), (
        "the survivor has to be named - the pid is the only thing that makes "
        "it findable by hand"
    )


def test_a_process_that_outlived_its_kill_stays_owned(monkeypatch):
    """THE OTHER HALF, and the one that turns a bad report into an orphan.

    `stop` cleared `started` unconditionally, so a process that survived stopped
    being anybody's - and `server.py` drops the session on the way out, which
    is what makes it unreachable rather than merely mis-reported. That is the
    same orphan this module already paid for once on the launch path, arriving
    by the opposite route: there the process was never owned, here ownership
    was given up while the process was still alive.
    """
    _, session = _stopping(monkeypatch, started={4808}, live={4808}, unkillable={4808})

    with pytest.raises(session_mod.SessionError):
        session_mod.stop(session.cfg, session, settle=TEST_SETTLE)

    assert session.started == {4808}, (
        "a surviving process lost its owner, so nothing can be asked to kill it again"
    )


def test_stop_reports_the_processes_that_actually_died(monkeypatch):
    """POSITIVE CONTROL. Without it, a `stop` that reported nothing and raised
    every time would satisfy both tests above."""
    windows, session = _stopping(monkeypatch, started={4808, 42224}, live={4808, 42224})

    killed = session_mod.stop(session.cfg, session, settle=TEST_SETTLE)

    assert sorted(killed) == [4808, 42224]
    assert session.started == set(), "nothing survived, so nothing is still owned"
    assert windows.live == set()


def test_a_slow_exit_is_not_mistaken_for_a_refused_kill(monkeypatch):
    """THE FALSE ALARM THE SETTLE POLL EXISTS TO PREVENT.

    Verification that asked the process table once, immediately, would pass
    every test above and be wrong in the common case: /F asks Windows to end
    the process and returns before the table has caught up, so a perfectly
    successful teardown would report a survivor and refuse to release its
    session. A check that cries wolf about the one thing it was added to be
    trusted on is worse than no check.

    Here the pid ignores the kill and then leaves on its own a moment later,
    which is what a normal shutdown looks like from outside.
    """
    _, session = _stopping(monkeypatch, started={4808}, live={4808}, unkillable={4808})
    tables = iter([{4808: ""}, {4808: ""}])
    monkeypatch.setattr(session_mod, "_pid_table", lambda cfg: next(tables, {}))

    killed = session_mod.stop(session.cfg, session, settle=TEST_SETTLE)

    assert killed == [4808], "a process that did leave was not reported as killed"
    assert session.started == set()


def test_stop_still_leaves_alone_a_game_it_did_not_start(monkeypatch):
    """The surgical property, restated against the new verification.

    A teardown that verified by clearing the table - or that widened its aim to
    make sure - would pass the tests above and take the developer's own game
    with it.
    """
    windows, session = _stopping(monkeypatch, started={4808}, live={4808, 31337})

    session_mod.stop(session.cfg, session, settle=TEST_SETTLE)

    assert windows.aimed == [4808], "it aimed at a process it had not started"
    assert 31337 in windows.live, "it killed a game that was not its to kill"


def test_stop_leaves_a_pending_request_addressed_to_another_player_alone(
    tmp_path, monkeypatch
):
    """The same defect on the teardown path, and stated separately because it
    is a separate deletion: `stop` unlinked the trigger unconditionally.

    Less destructive than the launch case only because the stopping session's
    own game is on its way out - the OTHER developer's game is not, and its
    request vanishing from under it is the identical loss.
    """
    windows = FakeWindows(set())
    windows.install(monkeypatch)
    cfg = cfg_for(tmp_path)
    session = Session(cfg=cfg, mode="server_client", port=1, player="n43n")
    trigger = cfg.artifact(cfg.artifacts.trigger, server=False)

    trigger.write_text("shot:full@big-bird")
    session_mod.stop(cfg, session, settle=TEST_SETTLE)

    assert trigger.exists() and trigger.read_text() == "shot:full@big-bird", (
        "stop deleted a request addressed to another player - the client it "
        "names is still running and still polling for it"
    )

    # POSITIVE CONTROL, in the same test: stop must still clear what IS its
    # own, or a teardown leaves its own dead request holding the shared slot
    # against the next session.
    trigger.write_text("shot:full@n43n")
    session_mod.stop(cfg, session, settle=TEST_SETTLE)

    assert not trigger.exists(), (
        "stop left its own session's pending request behind - the game that "
        "would have answered it is the one being killed"
    )


def test_a_spawn_that_fails_halfway_does_not_leak_the_half_that_started(monkeypatch):
    """THE OWNERSHIP WINDOW OPENED TOO LATE.

    `launch` spawned the server, spawned the client, and only THEN entered the
    try that owns them. Anything raising in between - a client that cannot be
    started, or the KeyboardInterrupt that comment says matters most - left the
    server running and held by nobody, which is the exact leak the try was
    added to close, one statement above where it starts.

    Whoever spawns owns them from the first spawn, not from the first wait.
    """
    windows = FakeWindows(set())
    windows.install(monkeypatch)
    monkeypatch.setattr(session_mod, "world_problem", lambda w: None)
    monkeypatch.setattr(session_mod, "_wait_ready", lambda cfg, *, mode, timeout: None)

    def half_a_spawn(cmd, **kwargs):
        if "-join" in cmd:
            raise OSError("the client could not be started")
        windows.live.add(4808)

    monkeypatch.setattr(session_mod.subprocess, "Popen", half_a_spawn)

    with pytest.raises(OSError):
        session_mod.launch(FakeCfg(Path("/tmp")), "server_client", port=1)

    assert windows.aimed == [4808], (
        "the server it had already started was left running when the client "
        "failed to start"
    )
    assert 4808 not in windows.live


# ---- join(): a second client into a running session ------------------------


def _joinable(monkeypatch, tmp_path, *, spawns, ready_for=None, delay_ready=0):
    """Wire `join`'s dependencies. Returns (cfg, session, windows).

    `spawns` is what appears in the process table when a client is started;
    `ready_for` is the player whose TOKENED heartbeat the fake mod eventually
    writes. `delay_ready` withholds it for that many readiness polls, which is
    how a test makes the wait actually wait.

    Backed by the same fake process table `launch`'s tests use, so a kill here
    changes what a later read sees rather than being recorded and believed.
    """
    cfg = cfg_for(tmp_path)
    windows = FakeWindows({4808, 5150})  # a server and a client, already up
    windows.install(monkeypatch)

    session = Session(
        cfg=cfg,
        mode="server_client",
        port=7810,
        player="n43n",
        world=r"C:\fake\World.wld",
        started={4808, 5150},
    )

    polls = {"n": 0}

    def fake_popen(*a, **k):
        windows.live |= set(spawns)

    monkeypatch.setattr(session_mod.subprocess, "Popen", fake_popen)

    real_ready = session_mod._client_is_ready

    def gated(cfg_, player):
        if player == ready_for:
            polls["n"] += 1
            if polls["n"] > delay_ready:
                _client_heartbeat(
                    cfg_.artifact(
                        artifacts_for(cfg_.mod_name, player).heartbeat, server=False
                    )
                )
        return real_ready(cfg_, player)

    monkeypatch.setattr(session_mod, "_client_is_ready", gated)
    monkeypatch.setattr(session_mod.time, "sleep", lambda s: None)
    return cfg, session, windows


def test_a_joined_client_is_waited_for_and_its_pids_recorded(monkeypatch, tmp_path):
    """The happy path, and the two things that make it teardown-safe.

    The pid matters more than it looks: `stop` kills exactly what a session
    started, so a client this harness spawned and did not record is a game
    left running that nothing owns.
    """
    cfg, session, _ = _joinable(monkeypatch, tmp_path, spawns={6100}, ready_for="tst2")

    back = session_mod.join(cfg, session, "tst2", timeout=30.0)

    assert back is session
    assert session.joined == ["tst2"]
    assert 6100 in session.started, (
        "the joined client's pid never reached the session, so `stop` will "
        "leave it running and nothing else knows it exists"
    )


def test_a_join_waits_for_THIS_clients_heartbeat_not_the_shared_one(
    monkeypatch, tmp_path
):
    """THE DEFECT in the code this was lifted from, one layer deeper.

    `live_capture_check.py` waited for a new PID and called it done. Waiting
    for a heartbeat instead is better but not sufficient: `_wait_ready`
    accepts EITHER the unsuffixed `<mod>-hooks.txt` or the tokened name for
    the launch client, and the unsuffixed one is a SLOT rather than a client -
    every client that boots writes it on the way through the menu and nothing
    deletes it, so with a client already running it holds that client's
    record, frozen.

    So a join that accepted it would return against the heartbeat of the game
    that was ALREADY HERE, before the joining client existed at all. Planted
    live and world-ready below, exactly as the running client would have left
    it.

    THE TIMEOUT HAS TO BE NON-ZERO, and this test did not survive its own
    mutation until it was. With `timeout=0.0` the wait loop never runs, the
    raise comes from the loop being SKIPPED, and `_client_is_ready` is never
    consulted at all - so the test passed just as happily against a version
    that accepted the shared slot. A tripwire nothing walks through.
    """
    cfg, session, _ = _joinable(monkeypatch, tmp_path, spawns={6100}, ready_for=None)

    # The already-running client's shared-slot record: live, world-ready, and
    # nothing to do with the client being joined. Nothing ever writes tst2's
    # own heartbeat here, so this file is the ONLY thing on disk that could
    # satisfy the wait.
    _client_heartbeat(cfg.artifact(cfg.artifacts.heartbeat, server=False))

    with pytest.raises(session_mod.SessionError, match="world-ready heartbeat"):
        session_mod.join(cfg, session, "tst2", timeout=0.3)

    # POSITIVE CONTROL, same test: with tst2's OWN heartbeat present the very
    # same predicate says ready, so the refusal above is about WHICH file was
    # watched and not about the wait being unsatisfiable.
    _client_heartbeat(
        cfg.artifact(artifacts_for(cfg.mod_name, "tst2").heartbeat, server=False)
    )
    assert session_mod._client_is_ready(cfg, "tst2")


def test_a_join_refuses_a_character_already_in_the_session(monkeypatch, tmp_path):
    """Two clients under one name share a player token, so their heartbeat,
    reply, diag and shot are one file each between them - the collision
    per-player naming removed, reachable again through the tool that adds
    clients."""
    cfg, session, _ = _joinable(monkeypatch, tmp_path, spawns={6100}, ready_for="tst2")

    with pytest.raises(session_mod.SessionError, match="already in this session"):
        session_mod.join(cfg, session, "n43n", timeout=30.0)

    # Case-insensitively: the mod compares a target to its own name that way,
    # so these are one client to the game and two player tokens to the disk -
    # the worst of both.
    with pytest.raises(session_mod.SessionError, match="already in this session"):
        session_mod.join(cfg, session, "N43N", timeout=30.0)

    # POSITIVE CONTROL, same test: a different name is accepted, so the guard
    # is a guard and not a refusal to join anybody.
    session_mod.join(cfg, session, "tst2", timeout=30.0)
    assert session.joined == ["tst2"]

    # And now THAT one is taken too.
    with pytest.raises(session_mod.SessionError, match="already in this session"):
        session_mod.join(cfg, session, "tst2", timeout=30.0)


@pytest.mark.parametrize("name", [" n43n", "n43n ", "", "   "])
def test_a_join_refuses_a_name_launch_would_refuse(monkeypatch, tmp_path, name):
    """ONE RULE, asked of both doors. `launch` has always refused a name
    `parse` cannot read back intact; a `join` that did not would let the same
    unaddressable character in through the other entrance, and every request
    to it would fail for a reason naming the request rather than the name."""
    cfg, session, _ = _joinable(monkeypatch, tmp_path, spawns={6100}, ready_for=name)

    with pytest.raises(session_mod.SessionError, match="not the name a request"):
        session_mod.join(cfg, session, name, timeout=30.0)


def test_a_join_that_never_becomes_ready_kills_only_what_it_started(
    monkeypatch, tmp_path
):
    """WHOEVER SPAWNS OWNS THEM UNTIL IT CAN HAND THEM BACK - `launch`'s rule,
    and a join needs it for the same reason: this function never returns, so
    the session never learns the pid, and a client left up is held by nobody.

    The part that is only true here: it must kill what THIS call started and
    nothing else. The session already has a server and a client running, and
    a failed join that took those down would turn a client that would not
    start into a session that is gone.
    """
    cfg, session, windows = _joinable(
        monkeypatch, tmp_path, spawns={6100}, ready_for=None
    )

    # Non-zero, so the wait actually runs and fails rather than being skipped
    # - see the shared-slot test above for what a 0.0 here hides.
    with pytest.raises(session_mod.SessionError, match="world-ready heartbeat"):
        session_mod.join(cfg, session, "tst2", timeout=0.3)

    assert windows.aimed == [6100], (
        f"taskkill was aimed at {windows.aimed} - a failed join must reach the "
        "client it started and nothing else"
    )
    assert 4808 in windows.live and 5150 in windows.live, (
        "a failed join took down the server and client that were already "
        "running, turning a client that would not start into no session at all"
    )
    assert session.joined == [], "a join that failed was recorded as having happened"


def test_a_join_refuses_a_session_with_no_server(monkeypatch, tmp_path):
    """There is nothing to join. Refused by name rather than left to a
    readiness timeout, which would blame the client."""
    cfg, session, _ = _joinable(monkeypatch, tmp_path, spawns={6100}, ready_for="tst2")
    session.mode = "server"

    with pytest.raises(session_mod.SessionError, match="no server for a client"):
        session_mod.join(cfg, session, "tst2", timeout=30.0)


# ---- the positive control the process query now carries ---------------------


def test_a_broken_process_query_fails_the_stop_instead_of_faking_success(
    monkeypatch,
):
    """THE SILENT NO-OP. `_tml_pids` returned an empty set when the QUERY
    broke, and an empty set is also the answer for "no games" - so `stop`
    aimed at nothing, killed nothing, returned [] as success, and the server
    released a session that left a running game owned by nobody. The next
    `launch` then refused over a process nobody remembered starting."""
    _, session = _stopping(monkeypatch, started={4808}, live={4808})
    monkeypatch.setattr(session_mod, "_pid_table", lambda cfg: None)

    with pytest.raises(session_mod.SessionError, match="query failed"):
        session_mod.stop(session.cfg, session, settle=TEST_SETTLE)

    assert session.started == {4808}, (
        "the session let go of a pid it never verified - the orphan, back again"
    )


def test_a_recycled_pid_is_left_alone_and_leaves_the_session(monkeypatch):
    """Windows reuses pids aggressively. A session client that died on its own
    can hand its number to the developer's own hand-started game, and a kill
    matched on the number alone took it - the one outcome the module docstring
    promises against. The creation stamp is what tells the two apart."""
    windows, session = _stopping(monkeypatch, started={4808}, live={4808})
    session.stamps = {4808: "1000"}
    monkeypatch.setattr(
        session_mod, "_pid_table", lambda cfg: {4808: "2000"} if windows.live else {}
    )

    killed = session_mod.stop(session.cfg, session, settle=TEST_SETTLE)

    assert windows.aimed == [], "taskkill was aimed at somebody else's process"
    assert killed == [], "nothing of this session's was alive to kill"
    assert session.started == set(), (
        "a pid whose process is DEAD stayed owned - `stop` would retry it forever"
    )


def test_a_matching_stamp_still_kills_normally(monkeypatch):
    """POSITIVE CONTROL for the two above: with the stamp agreeing, the kill
    is exactly what it always was."""
    windows, session = _stopping(monkeypatch, started={4808}, live={4808})
    session.stamps = {4808: "1000"}
    monkeypatch.setattr(
        session_mod,
        "_pid_table",
        lambda cfg: {pid: "1000" for pid in windows.live},
    )

    killed = session_mod.stop(session.cfg, session, settle=TEST_SETTLE)

    assert killed == [4808]
    assert session.started == set()


def test_a_region_spelled_with_separators_still_yields_a_findable_capture(
    tmp_path, monkeypatch
):
    """The mod accepts `top-left` as a spelling of `topleft`, but the capture
    pattern reads the filename's trailing field as `[a-z]+` - so a file named
    with the RAW spelling was a capture `captures`/`read_capture` could never
    see again: taken, reported, and invisible. The filename now carries the
    slug the mod resolved."""
    from tmodloader_mcp import captures as captures_mod

    sess = _session_that_captures(tmp_path, monkeypatch, [_png(b"CORNER")])

    kept = sess.shot("top-left")

    assert kept.name.endswith("-topleft.png"), kept.name
    assert kept.name in captures_mod.available(tmp_path, "Biomancy"), (
        "the capture exists and the listing cannot see it"
    )
