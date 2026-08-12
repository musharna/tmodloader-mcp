"""Where an ANSWER is looked for when the request was addressed to somebody else.

Found by running two clients against one server for the first time — the case
the per-player naming exists for, and one no unit test had ever been able to
express. `diag(target='tst2')` from a session whose player is `n43n` timed out
after 60 seconds while `tst2` answered correctly into its own files. The mod
was right; the harness was watching a path nothing writes.

The mechanism: the request became per-player and the reply path did not follow.
It appears at three sites, so a fix to one of them is a tripwire removal rather
than a mechanism removal — hence three tests and three positive controls.

On `master` this worked, and that is the uncomfortable part: every client wrote
one shared reply file, so addressing worked BECAUSE answers were ambiguous.
Removing the ambiguity is what broke it.
"""

from __future__ import annotations

import multiprocessing
import struct
import zlib
from pathlib import Path

import pytest

from tmodloader_mcp import session as session_mod
from tmodloader_mcp.session import Session
from tmodloader_mcp.triggers import Reply, TriggerError, artifacts_for

#: The session's own player, and somebody else entirely. Both are real names
#: run through the real token rule rather than hand-spelled filenames, so these
#: tests exercise the naming rule instead of a copy of one player's hashes.
SELF = "n43n"
OTHER = "tst2"


class FakeCfg:
    """The handful of Config fields these paths touch.

    Deliberately the same shape as `test_session_lifecycle.FakeCfg`: `artifact`
    prefixes rather than suffixes for the server side, which is enough to keep
    the two sides apart without pretending to be the real path builder.
    """

    def __init__(self, root: Path, mod_name: str = "Biomancy"):
        self.root = root
        self.mod_name = mod_name
        self.artifacts = artifacts_for(mod_name)

    def artifact(self, name: str, *, server: bool) -> Path:
        return self.root / (f"server-{name}" if server else name)


@pytest.fixture
def cfg(tmp_path) -> FakeCfg:
    return FakeCfg(tmp_path)


@pytest.fixture
def sess(cfg) -> Session:
    """A session belonging to SELF, which is the whole point: it will be asked
    about OTHER, and must not answer for itself by accident."""
    return Session(cfg=cfg, mode="server_client", port=1, player=SELF)


def _publish(cfg: FakeCfg, *, server: bool = False) -> None:
    """A responder's command list, which `ask` composes against.

    Required rather than optional: `compose` checks the verb against what the
    mod PUBLISHED and there is deliberately no fallback list, so without this
    every `ask` here would fail for a reason that has nothing to do with
    addressing.
    """
    cfg.artifact(cfg.artifacts.commands, server=server).write_text(
        "diag\tnoarg\tstate dump\nshot\targ\tone region of the frame\n"
    )


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data))
    )


def _png() -> bytes:
    """A REAL 1x1 PNG. `shot` opens what it is handed and checks the trailer,
    so a placeholder byte string would fail for the wrong reason."""
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        + _chunk(b"IEND", b"")
    )


def _answering_as(cfg: FakeCfg, monkeypatch, who: str, *, png: bool = False):
    """Make `ask` behave like the ADDRESSED client: write under ITS token.

    This is the contract's rule, verified live — a client writes its answers
    under its own player token, never under the requester's. The fixture obeys
    it, so a harness that looks anywhere else finds nothing, which is exactly
    what the live run saw.
    """
    names = artifacts_for(cfg.mod_name, who)

    def fake_ask(
        self, command, *, argument=None, target=None, server=False, timeout=60.0
    ):
        if png:
            cfg.artifact(names.shot, server=False).write_bytes(_png())
        else:
            cfg.artifact(names.diag, server=False).write_text("player: " + who + "\n")
        return Reply(command=command, text="ok")

    monkeypatch.setattr(Session, "ask", fake_ask)


def _replies_when_triggered(monkeypatch, path: Path, text: str) -> None:
    """A client that answers WHEN THE TRIGGER LANDS, at `path`.

    The reply cannot simply be planted before the call: `ask` deletes the reply
    file it is about to wait for — deliberately, so a stale answer cannot be
    read as a fresh one — and would delete the plant along with it. So the
    fixture hangs off the trigger write, which is where a real game's answer
    comes from too.
    """
    real = session_mod._claim_atomically

    def answering(trigger: Path, payload: str) -> None:
        real(trigger, payload)
        path.write_text(text)

    monkeypatch.setattr(session_mod, "_claim_atomically", answering)


# ---- site 1: ask(), the reply file -------------------------------------


def test_a_reply_is_awaited_where_the_ADDRESSEE_writes_it(sess, cfg, monkeypatch):
    """THE DEFECT, at the site that produced the live timeout.

    `ask` computed the reply path from the session's player and ignored
    `target` entirely, so addressing anybody but yourself waited out the full
    timeout at a filename the addressee never touches — while the addressee's
    own answer sat on disk, correct, complete and unread.
    """
    _publish(cfg)
    _replies_when_triggered(
        monkeypatch,
        cfg.artifact(artifacts_for(cfg.mod_name, OTHER).result, server=False),
        "ok",
    )

    reply = sess.ask("diag", target=OTHER, timeout=1.0)

    assert reply.text == "ok", (
        "the answer was written under the addressee's token, where the contract "
        "says it goes, and the harness waited somewhere else"
    )


def test_a_reply_to_an_unaddressed_request_still_comes_to_this_session(
    sess, cfg, monkeypatch
):
    """POSITIVE CONTROL. A NEGATIVE RESULT NEEDS ONE.

    Keying the wait to the target could be got wrong in the other direction
    just as easily — an unaddressed request is for THIS session's player, and
    a fix that always used `target` would send the commonest call in the whole
    surface to a file named for nobody at all.
    """
    _publish(cfg)
    _replies_when_triggered(
        monkeypatch,
        cfg.artifact(artifacts_for(cfg.mod_name, SELF).result, server=False),
        "mine",
    )

    assert sess.ask("diag", timeout=1.0).text == "mine"


def test_a_server_reply_is_never_looked_for_under_a_player(sess, cfg, monkeypatch):
    """The dedicated server has no player, and handing it a token would name a
    file nothing writes. `target` must not change that: the server side stays
    unsuffixed however the request was addressed."""
    _publish(cfg, server=True)
    _replies_when_triggered(
        monkeypatch, cfg.artifact(cfg.artifacts.result, server=True), "server says"
    )

    assert (
        sess.ask("diag", server=True, target=OTHER, timeout=1.0).text == "server says"
    )


# ---- site 2: diag(), the dump file --------------------------------------


def test_a_diag_dump_is_read_where_the_ADDRESSEE_writes_it(sess, cfg, monkeypatch):
    """The same mechanism one layer up, which is why fixing `ask` alone is not
    enough. `diag` derives the DUMP path the same wrong way — so even with the
    reply arriving correctly, the dump is deleted and then awaited under the
    requester's name."""
    _answering_as(cfg, monkeypatch, OTHER)

    parsed = sess.diag(target=OTHER, timeout=1.0)

    assert parsed.fields.get("player") == OTHER


def test_an_unaddressed_diag_still_reads_this_sessions_dump(sess, cfg, monkeypatch):
    """POSITIVE CONTROL for the dump path."""
    _answering_as(cfg, monkeypatch, SELF)

    assert sess.diag(timeout=1.0).fields.get("player") == SELF


# ---- site 3: shot(), the drop box ---------------------------------------


def test_a_shot_is_collected_from_the_ADDRESSEES_drop_box(sess, cfg, monkeypatch):
    """The third site. The drop box went per-player in this same change, so a
    shot addressed to another client is written where that client's token says
    and collected from where the requester's token says."""
    _answering_as(cfg, monkeypatch, OTHER, png=True)

    kept = sess.shot("full", target=OTHER, timeout=1.0)

    assert artifacts_for(cfg.mod_name, OTHER).shot.split(".")[0] in kept.name, (
        f"collected {kept.name}, which does not carry the addressee's token"
    )
    assert kept.read_bytes().endswith(b"IEND\xae\x42\x60\x82")


def test_an_unaddressed_shot_still_uses_this_sessions_drop_box(sess, cfg, monkeypatch):
    """POSITIVE CONTROL for the drop box."""
    _answering_as(cfg, monkeypatch, SELF, png=True)

    kept = sess.shot("full", timeout=1.0)

    assert artifacts_for(cfg.mod_name, SELF).shot.split(".")[0] in kept.name


# ---- the shared staging file -------------------------------------------


def test_two_requests_in_flight_do_not_share_one_staging_file(cfg, monkeypatch):
    """THE LOST UPDATE, found by firing two `shot`s at once - what this guards
    now that `_claim_atomically` refuses a taken slot instead of overwriting it.

    `_claim_atomically` staged at `<trigger>.staging`, and the trigger path is
    shared by every client BY DESIGN — so two sessions writing at once shared
    one staging file. Under the old `os.replace`, the last write won its
    contents, the first rename carried them, and the second rename raised
    `FileNotFoundError`: one request silently replaced by the other's payload.
    `os.link` closes that by refusing the second claim outright rather than by
    racing it, which is why this test now frees the slot itself between the
    two requests - see below - instead of letting a second stage-and-rename
    happen unopposed. What still has to hold, and what this test still checks,
    is the property underneath: no two writers may ever stage at the same path.

    Asserted as a property rather than by racing threads: two sessions ask for
    two different things, and no staging path may be written twice. A thread
    race reproduces it only sometimes, and a test that fails sometimes is not
    a test.
    """
    _publish(cfg)
    monkeypatch.setattr(Session, "_await_text", lambda self, p, **kw: "ok")

    staged: list[Path] = []
    real_write_text = Path.write_text

    def spy(self, *args, **kwargs):
        if ".staging" in Path(self).name:
            staged.append(Path(self))
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", spy)

    Session(cfg=cfg, mode="server_client", port=1, player=SELF).ask("diag")

    # THE MOD CONSUMES A REQUEST BEFORE THE NEXT ONE ARRIVES, and the trigger
    # is now CLAIMED rather than overwritten, so the second session can only
    # reach its staged write once the slot is free. Simulating that consumption
    # is more faithful to the protocol than the old version, which could stage
    # twice only because `os.replace` destroyed the first request.
    cfg.artifact(cfg.artifacts.trigger, server=False).unlink()

    Session(cfg=cfg, mode="server_client", port=1, player=OTHER).ask("diag")

    assert len(staged) == 2, "the test is not exercising two staged writes"
    assert staged[0] != staged[1], (
        f"both sessions staged at {staged[0]} — concurrent requests overwrite "
        "each other's payload before either reaches the game"
    )


def test_addressing_this_sessions_own_player_survives_a_different_case(
    sess, cfg, monkeypatch
):
    """A REGRESSION THE ADDRESSEE FIX INTRODUCED, caught reviewing it.

    The mod compares a target to its own name with `OrdinalIgnoreCase`, so it
    answers to `n43n`, `N43N` and `N43n` alike — and then writes under its OWN
    name. The token's four hex characters are the MD5 of the ORIGINAL bytes,
    deliberately, so those three spellings produce three DIFFERENT tokens.

    Before the addressee fix this worked by accident: the wait was pinned to
    the session's player whatever the target said. Keying it to the target
    would have made `diag(target='N43N')` from an `n43n` session time out
    against a client answering perfectly — the exact failure the fix removes,
    reintroduced one case-fold away from it.

    The session knows the canonical spelling of its own player. A target that
    names it, however typed, resolves to that spelling.
    """
    _publish(cfg)
    _replies_when_triggered(
        monkeypatch,
        cfg.artifact(artifacts_for(cfg.mod_name, SELF).result, server=False),
        "mine",
    )

    assert sess.ask("diag", target=SELF.upper(), timeout=1.0).text == "mine"


# ---- the claim itself ----------------------------------------------------


def test_a_claim_refuses_a_slot_that_is_already_taken(cfg):
    """THE LOST UPDATE, at the primitive that allowed it.

    `os.replace` succeeds unconditionally, so the second of two concurrent
    requests overwrote the first and its caller waited out a full timeout with
    nothing on disk to explain why. `os.link` is atomic in exactly the same way
    and additionally refuses an occupied name.

    The positive control is in the same test deliberately: a claim that refused
    EVERYTHING would pass the first assertion while making the tool useless.
    """
    trigger = cfg.artifact(cfg.artifacts.trigger, server=False)

    session_mod._claim_atomically(trigger, "diag@n43n")
    assert trigger.read_text() == "diag@n43n", "the free slot was not claimed"

    with pytest.raises(session_mod.SlotBusy):
        session_mod._claim_atomically(trigger, "diag@tst2")

    assert trigger.read_text() == "diag@n43n", (
        "the pending request was overwritten - which is the entire defect"
    )


def test_a_claim_leaves_no_staging_file_behind(cfg):
    """`os.replace` CONSUMED the staging name; `os.link` does not - it leaves a
    second name for the same inode. Forgetting the unlink would litter the
    directory the game reads with one file per request.

    Checked after both a successful claim and a refused one, because the
    refused path is the one that returns early.
    """
    trigger = cfg.artifact(cfg.artifacts.trigger, server=False)

    session_mod._claim_atomically(trigger, "diag@n43n")
    assert not list(cfg.root.glob("*.staging")), "staging file left after a claim"

    with pytest.raises(session_mod.SlotBusy):
        session_mod._claim_atomically(trigger, "diag@tst2")
    assert not list(cfg.root.glob("*.staging")), "staging file left after a refusal"


def test_a_claim_never_writes_into_the_polled_path(cfg, monkeypatch):
    """The guarantee that must SURVIVE the change: the game polls the trigger,
    so a payload written there in place is readable half-finished, and a
    truncated command word is not an error on that side - it parses as unknown
    and is discarded, and the harness then waits out its timeout for a reply to
    a request the game threw away.
    """
    trigger = cfg.artifact(cfg.artifacts.trigger, server=False)
    written: list[Path] = []
    real_write_text = Path.write_text

    def spy(self, *args, **kwargs):
        written.append(Path(self))
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", spy)
    session_mod._claim_atomically(trigger, "diag@n43n")

    assert written, "nothing was written - the test is not exercising the claim"
    assert trigger not in written, (
        "the payload was written straight into the file the game polls"
    )
    assert written[-1].parent == trigger.parent, (
        "staged outside the trigger's directory - a link only works within one "
        "filesystem, and the save directory is on /mnt/c"
    )


def test_a_blocked_claim_waits_for_the_slot_rather_than_failing(sess, cfg, monkeypatch):
    """Contention is NORMAL and sub-second, not a fault.

    Two developers on one machine will collide routinely, and the slot frees
    within one of the mod's poll ticks. Failing on contact would turn an
    invisible wait into a visible error for a condition that resolves itself.

    The slot is freed from inside the sleep, which is where a real consumption
    would land, and makes the test deterministic instead of racing a thread.
    """
    _publish(cfg)
    trigger = cfg.artifact(cfg.artifacts.trigger, server=False)
    session_mod._claim_atomically(trigger, "diag@somebody-else")

    def freeing_sleep(_seconds):
        trigger.unlink(missing_ok=True)

    monkeypatch.setattr(session_mod.time, "sleep", freeing_sleep)
    _replies_when_triggered(
        monkeypatch,
        cfg.artifact(artifacts_for(cfg.mod_name, SELF).result, server=False),
        "mine",
    )

    assert sess.ask("diag", timeout=5.0).text == "mine"


def test_a_claim_that_never_clears_names_what_is_holding_it(sess, cfg, monkeypatch):
    """A NEGATIVE RESULT NEEDS A POSITIVE CONTROL, and the control here is the
    test above: the wait must end, but only when the slot genuinely never frees.

    The message has to name the pending request, because the caller's own
    request is fine and the game is fine - neither of the things they would
    otherwise go and check.
    """
    _publish(cfg)
    trigger = cfg.artifact(cfg.artifacts.trigger, server=False)
    session_mod._claim_atomically(trigger, "shot:full@somebody-else")

    monkeypatch.setattr(session_mod.time, "sleep", lambda _s: None)

    with pytest.raises(TriggerError) as refused:
        sess.ask("diag", timeout=0.2)

    message = str(refused.value)
    assert "somebody-else" in message, f"does not name what is holding it: {message}"
    assert trigger.read_text() == "shot:full@somebody-else", (
        "the blocked claim deleted another session's request, which is the "
        "overwrite this whole change removes"
    )


def test_a_blocked_claim_does_not_assert_an_ownership_it_cannot_know(
    sess, cfg, monkeypatch
):
    """FIX ROUND 3, IMPORTANT 2: the message named a culprit it never checked.

    Nothing validates that an addressed target names a LIVE client, and the
    mod leaves a request it is not addressed by exactly where it is - so one
    typo'd `target`, or a client that has not loaded a character, parks a
    request no client will ever consume. Every later call from BOTH sessions
    then failed with "Another session's request is pending", which about a
    caller's own typo is simply false, and sends them to look at a session
    that may not exist.

    What is observable is the payload and its age. That is what it may say.
    """
    _publish(cfg)
    trigger = cfg.artifact(cfg.artifacts.trigger, server=False)
    session_mod._claim_atomically(trigger, "diag@nobody-by-that-name")

    monkeypatch.setattr(session_mod.time, "sleep", lambda _s: None)

    with pytest.raises(TriggerError) as refused:
        sess.ask("diag", timeout=0.2)

    message = str(refused.value)
    assert "Another session's request is pending" not in message, (
        f"asserts whose request it is, which nothing here checked: {message}"
    )
    # POSITIVE CONTROL, in the same test: a message that had gone vague to
    # satisfy the assertion above would be worse than the one it replaced.
    # What IS observable still has to be in it.
    assert "nobody-by-that-name" in message, (
        f"does not name the payload that is actually in the way: {message}"
    )


def test_a_blocked_claim_on_this_sessions_own_request_names_the_remedy(
    sess, cfg, monkeypatch
):
    """The self-inflicted half, which the caller can actually act on.

    A request addressed to THIS session's own player that no client has taken
    means no client of that name is polling - the commonest cause being a
    client that has not loaded a character, whose name is empty and matches no
    target. Saying "another session's" about that is the wrong owner AND the
    wrong remedy.
    """
    _publish(cfg)
    trigger = cfg.artifact(cfg.artifacts.trigger, server=False)
    session_mod._claim_atomically(trigger, f"diag@{SELF}")

    monkeypatch.setattr(session_mod.time, "sleep", lambda _s: None)

    with pytest.raises(TriggerError) as refused:
        sess.ask("diag", timeout=0.2)

    message = str(refused.value)
    assert "this session's own player" in message, (
        f"does not say the pending request is the caller's own: {message}"
    )
    assert "`launch`" in message, (
        f"does not name the remedy - a fresh launch clears this session's own "
        f"pending request, and nothing else in the message tells them so: "
        f"{message}"
    )
    assert "may belong to another session" not in message, (
        f"hedges about an owner it can see is the caller themselves: {message}"
    )


def test_the_claim_wait_spends_the_callers_timeout_not_a_second_budget(
    sess, cfg, monkeypatch
):
    """A caller passing timeout=N asked for an ANSWER within N seconds.

    Giving the claim its own N and the reply another N would mean a caller
    blocked for the whole budget then waits the whole budget again - and the
    error they finally get names a silent game, when the game was never
    involved.
    """
    _publish(cfg)
    trigger = cfg.artifact(cfg.artifacts.trigger, server=False)
    session_mod._claim_atomically(trigger, "diag@somebody-else")

    slept: list[float] = []

    def counting_sleep(seconds):
        slept.append(seconds)
        if len(slept) >= 3:
            trigger.unlink(missing_ok=True)

    monkeypatch.setattr(session_mod.time, "sleep", counting_sleep)

    seen: list[float] = []
    real_await = Session._await_text

    def spy(self, path, *, timeout, what):
        seen.append(timeout)
        return real_await(self, path, timeout=timeout, what=what)

    monkeypatch.setattr(Session, "_await_text", spy)
    _replies_when_triggered(
        monkeypatch,
        cfg.artifact(artifacts_for(cfg.mod_name, SELF).result, server=False),
        "mine",
    )

    sess.ask("diag", timeout=5.0)

    assert seen, "the reply was never awaited"
    assert seen[0] < 5.0, (
        f"the reply got the full {seen[0]}s after the claim had already spent "
        "part of the budget - that is two budgets, not one"
    )


def test_a_claim_that_succeeds_on_its_last_tick_still_fails_as_a_claim(
    sess, cfg, monkeypatch
):
    """A claim can SUCCEED and still have nothing left to wait with.

    `_claim` used to return whatever was left of `timeout` unconditionally, so
    a claim that only went through on the final poll returned ~0s of budget
    for the reply. `_await_text(timeout=0)` then failed on its first check and
    raised a message about the GAME not polling - which was never asked, and
    was never what was slow. The budget was spent entirely by contention for
    the slot, and the failure has to say so.

    A first fix reused `_busy_message` here, which is wrong in a different way:
    by the time the floor fires the claim has ALREADY SUCCEEDED, so the
    trigger holds this session's own request, not somebody else's. Telling the
    caller "another session's request is pending... remove the file by hand"
    describes a request that is neither - it is theirs, it is live, and
    deleting it would destroy the very thing they just asked for. The message
    under test here has to say the opposite: the claim went through, the game
    may still answer it, and the remedy is a longer timeout - not a deletion.

    The clock is faked rather than raced: `time.monotonic` and `time.sleep`
    are both replaced with a hand-advanced counter, so the claim succeeds at a
    deterministic instant with less than one `CLAIM_POLL` of budget left,
    rather than hoping real wall-clock timing lands there.
    """
    _publish(cfg)
    trigger = cfg.artifact(cfg.artifacts.trigger, server=False)
    session_mod._claim_atomically(trigger, "diag@somebody-else")

    clock = [0.0]
    calls = [0]

    def advancing_sleep(seconds):
        calls[0] += 1
        clock[0] += seconds
        if calls[0] >= 2:
            trigger.unlink(missing_ok=True)

    monkeypatch.setattr(session_mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(session_mod.time, "sleep", advancing_sleep)

    with pytest.raises(TriggerError) as refused:
        sess.ask("diag", timeout=0.25)

    message = str(refused.value)
    assert "polling" not in message, (
        f"blamed the game for a budget the claim spent: {message}"
    )
    assert "Another session's request is pending" not in message, (
        f"describes the caller's own live claim as somebody else's: {message}"
    )
    assert "remove the file by hand" not in message, (
        f"told the caller to delete a request that is their own and still "
        f"live: {message}"
    )
    assert "may still answer" in message, (
        f"does not say the claim went through and the game may still serve "
        f"it: {message}"
    )
    assert "retry" in message.lower(), (
        f"does not point the caller at the actual remedy - a longer timeout: {message}"
    )


def _claim_worker(trigger: str, payload: str, result_path: str) -> None:
    """Multiprocessing target: attempt one real claim, record the outcome.

    Module-level so it can be sent to a child process (`multiprocessing`
    pickles the target by qualified name), and so it works the same way under
    both `fork` and `spawn`.
    """
    from tmodloader_mcp import session as worker_session_mod

    try:
        worker_session_mod._claim_atomically(Path(trigger), payload)
    except worker_session_mod.SlotBusy:
        Path(result_path).write_text("busy")
    else:
        Path(result_path).write_text("ok")


def test_a_claim_under_real_process_contention_admits_exactly_one(tmp_path):
    """Real kernel-level exclusion, not a mocked clock.

    Every other test in this module fakes `time.sleep` and reasons about
    `_claim_atomically` from a single process, which never actually exercises
    the guarantee the whole design rests on: several processes racing
    `os.link` onto the same name, with the KERNEL - not this codebase -
    deciding which one wins. This runs several real OS processes against one
    trigger path and checks that exactly one wins and every other gets
    `SlotBusy`.

    `tmp_path` here is a real (if ordinary) filesystem, not the production
    save directory, which is DrvFs under WSL2 - `os.link`'s atomicity there
    was separately confirmed to hold when `_claim_atomically` was written.

    The payload left on disk is checked against the recorded winner in the
    same test, so a harness bug that let every worker report failure (and
    thus vacuously pass a "not more than one winner" check) could not pass
    this one: nothing would be on disk to match against.
    """
    trigger = tmp_path / "biomancy-capture.trigger"
    workers = 8
    procs: list[multiprocessing.Process] = []
    result_paths: list[Path] = []

    for i in range(workers):
        result_path = tmp_path / f"result-{i}.txt"
        result_paths.append(result_path)
        proc = multiprocessing.Process(
            target=_claim_worker,
            args=(str(trigger), f"diag@worker-{i}", str(result_path)),
        )
        procs.append(proc)

    try:
        for proc in procs:
            proc.start()
        for proc in procs:
            proc.join(timeout=10)

        assert all(not proc.is_alive() for proc in procs), "a worker never finished"
    finally:
        # A hung or crashed child must not outlive a failing run - terminate
        # whatever is still alive rather than leaking a background process.
        for proc in procs:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=5)

    outcomes = [p.read_text() for p in result_paths]
    assert outcomes.count("ok") == 1, f"expected exactly one winner, got {outcomes}"
    assert outcomes.count("busy") == workers - 1, (
        f"expected every other worker refused, got {outcomes}"
    )

    winner = outcomes.index("ok")
    assert trigger.read_text() == f"diag@worker-{winner}", (
        "the payload on disk does not belong to the recorded winner - a run "
        "where every worker failed could not have produced this either"
    )
    assert not list(tmp_path.glob("*.staging")), "a staging file was left behind"


def test_an_unaddressed_request_is_addressed_to_this_sessions_player(
    sess, cfg, monkeypatch
):
    """THE COIN FLIP, removed at its source.

    `IsFor` returns true for EVERY client when a payload names no `@player`, so
    with two clients up whichever polled first deleted the shared trigger and
    answered. Measured: run twice in a row, a different client answered each
    time. The harness knows its own session's player, so leaving the payload
    unaddressed delegated the choice to a race.
    """
    _publish(cfg)
    seen: list[str] = []
    real = session_mod._claim_atomically

    def spy(path, text):
        seen.append(text)
        return real(path, text)

    monkeypatch.setattr(session_mod, "_claim_atomically", spy)
    _replies_when_triggered(
        monkeypatch,
        cfg.artifact(artifacts_for(cfg.mod_name, SELF).result, server=False),
        "mine",
    )

    sess.ask("diag", timeout=1.0)

    assert seen == [f"diag@{SELF}"], f"composed {seen}, not an addressed payload"


def test_the_server_side_is_never_addressed(sess, cfg, monkeypatch):
    """POSITIVE CONTROL, and a real hazard.

    A dedicated server has no local player, so `IsFor` returns FALSE for any
    non-empty target - addressing it would mean it never answers anything
    again. It also writes a `-server` suffixed trigger, so it was never
    contended and has nothing to gain here.
    """
    _publish(cfg, server=True)
    seen: list[str] = []
    real = session_mod._claim_atomically

    def spy(path, text):
        seen.append(text)
        return real(path, text)

    monkeypatch.setattr(session_mod, "_claim_atomically", spy)
    _replies_when_triggered(
        monkeypatch, cfg.artifact(cfg.artifacts.result, server=True), "server says"
    )

    sess.ask("diag", server=True, timeout=1.0)

    assert seen == ["diag"], f"the server was addressed: {seen}"


def test_launch_refuses_a_character_name_padded_with_whitespace(monkeypatch):
    """`parse` TRIMS the target, so a name padded with whitespace is not the
    name a request will actually carry.

    `@` and `:` were the first hypothesis for what this guard needed to catch,
    and they were wrong - measured, not assumed: `player` is only ever placed
    in the payload's TERMINAL field, and `parse` takes everything after the
    first `@` verbatim with no further splitting, so those two characters
    survive the round trip intact, in both this module's `parse` and the
    mod's own `DevCommands.Parse`. What `parse` actually changes is
    whitespace: a leading or trailing space reads back trimmed, which looks
    identical to a valid name in any log or config - exactly why catching it
    once at launch beats failing on every request this session ever sends.

    The positive control is in the same test deliberately: a guard that
    refused every name would pass the assertion below while making `launch`
    unusable for anybody.
    """
    cfg = FakeCfg(Path("/nonexistent"))
    monkeypatch.setattr(session_mod, "_tml_pids", lambda c: set())

    # An ordinary name has nothing for `parse` to trim, so the guard must let
    # it through - what stops `launch` here is `FakeCfg` lacking the rest of a
    # real `Config` (`world_win` and friends), not the character name.
    with pytest.raises(AttributeError):
        session_mod.launch(cfg, "server_client", player=SELF)

    with pytest.raises(session_mod.SessionError) as refused:
        session_mod.launch(cfg, "server_client", player="n43n ")

    assert "n43n " in str(refused.value), (
        f"refused without naming the character: {refused.value}"
    )


def test_what_counts_as_a_capture_is_asked_of_the_parser():
    """The lock has to cover what the MOD will do, not what the caller typed.

    `DevResponder.cs:428`: an unreadable or bare payload is "the historical
    bare-trigger behaviour, a capture". Matching the literal string "capture"
    would miss it. Matching the parser cannot.
    """
    assert session_mod._will_capture("capture")
    assert session_mod._will_capture("capture@n43n")
    assert session_mod._will_capture("  CAPTURE  ")  # trimmed and lowercased

    # The bare payload. `compose` will not let this session EMIT one - every
    # command is validated against the published set - so this is the
    # predicate being right rather than a path being reachable. It is written
    # down because the rule belongs to the mod, and the mod can be driven by
    # something other than this harness.
    assert session_mod._will_capture("")
    assert session_mod._will_capture("   ")

    # POSITIVE CONTROL: the predicate must also say NO, or a lock taken
    # around every request would pass every assertion above while
    # serialising the entire protocol.
    assert not session_mod._will_capture("diag")
    assert not session_mod._will_capture("shot:full@n43n")

    # Malformed is not a capture: `DevCommands.Parse` maps what it cannot
    # read to Unknown and does nothing, so nothing is written and nothing
    # collides.
    assert not session_mod._will_capture("capture@")
    assert not session_mod._will_capture("@n43n")
