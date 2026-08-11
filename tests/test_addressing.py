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

import struct
import zlib
from pathlib import Path

import pytest

from tmodloader_mcp import session as session_mod
from tmodloader_mcp.session import Session
from tmodloader_mcp.triggers import Reply, artifacts_for

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
    real = session_mod._write_atomically

    def answering(trigger: Path, payload: str) -> None:
        real(trigger, payload)
        path.write_text(text)

    monkeypatch.setattr(session_mod, "_write_atomically", answering)


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
    """THE LOST UPDATE, found by firing two `shot`s at once.

    `_write_atomically` staged at `<trigger>.staging`, and the trigger path is
    shared by every client BY DESIGN — so two sessions writing at once shared
    one staging file. The last write won its contents, the first rename carried
    them, and the second rename raised `FileNotFoundError`. One request was
    silently replaced by the other's payload, which is the failure this whole
    project exists to prevent, arriving from the other end.

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
