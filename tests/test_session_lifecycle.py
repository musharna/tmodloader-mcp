"""Two lifecycle defects found by driving the real game, not by reading code.

Both were invisible to the suite because both live in the seam between this
harness and a running Windows process, and the suite had never held one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tmodloader_mcp import session as session_mod
from tmodloader_mcp.session import Session
from tmodloader_mcp.triggers import Reply, SHOT_NAME


class FakeCfg:
    """The handful of Config fields these paths actually touch."""

    def __init__(self, root: Path):
        self.root = root
        self.dotnet = Path("/fake/dotnet")
        self.tml_dir = Path("/fake/tml")
        self.taskkill = Path("/fake/taskkill")
        self.powershell = Path("/fake/powershell")
        self.world_win = r"C:\fake\World.wld"

    def artifact(self, name: str, *, server: bool) -> Path:
        return self.root / (f"server-{name}" if server else name)


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
        cfg.artifact(SHOT_NAME, server=False).write_bytes(queue.pop(0))
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
    sess = _session_that_captures(tmp_path, monkeypatch, [b"FIRST", b"SECOND"])

    first = sess.shot("bottomleft")
    second = sess.shot("bottomright")

    assert first != second, (
        "both captures returned the same path, so the second overwrote the "
        "first and the caller cannot tell"
    )
    assert first.read_bytes() == b"FIRST", "the first capture was clobbered"
    assert second.read_bytes() == b"SECOND"


def test_a_capture_survives_the_next_one_being_taken(tmp_path, monkeypatch):
    """The property a caller actually relies on, stated separately.

    The test above compares paths; this one holds a path across a later capture
    and reads it afterwards, which is what a caller collecting several regions
    before looking at any of them really does.
    """
    sess = _session_that_captures(tmp_path, monkeypatch, [b"KEEP", b"LATER"])

    kept = sess.shot("topleft")
    sess.shot("topright")

    assert kept.read_bytes() == b"KEEP"


# ---- launch() ----------------------------------------------------------


def _fake_launch_world(monkeypatch, *, existing, after, ready_raises):
    """Wire launch()'s dependencies so no game is started."""
    cfg_pids = iter([existing] + [after] * 10)
    monkeypatch.setattr(session_mod, "_tml_pids", lambda cfg: next(cfg_pids))
    monkeypatch.setattr(session_mod, "world_problem", lambda w: None)
    monkeypatch.setattr(session_mod.subprocess, "Popen", lambda *a, **k: None)

    killed: list[int] = []

    def fake_run(cmd, **kwargs):
        # stop() shells out to taskkill; record what it aimed at.
        if "/PID" in cmd:
            killed.append(int(cmd[cmd.index("/PID") + 1]))

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(session_mod.subprocess, "run", fake_run)

    def fake_wait(cfg, *, mode, timeout):
        if ready_raises:
            raise session_mod.SessionError("no live heartbeat within 300s")

    monkeypatch.setattr(session_mod, "_wait_ready", fake_wait)
    return killed


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

    sess = session_mod.launch(FakeCfg(Path("/tmp")), "server", port=1)

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
        session_mod.launch(FakeCfg(Path("/tmp")), "server", port=1)

    assert "already running" in str(e.value)
    assert killed == [], "refusing to launch killed the game that was already up"


# ---- readiness failure advice ------------------------------------------


def _wait_ready_error(tmp_path, monkeypatch, mode):
    """Drive _wait_ready to its timeout with no heartbeats on disk."""
    cfg = FakeCfg(tmp_path)
    monkeypatch.setattr(session_mod, "heartbeat_is_live", lambda p: False)

    with pytest.raises(session_mod.SessionError) as e:
        session_mod._wait_ready(cfg, mode=mode, timeout=0.0)

    return str(e.value)


def test_server_mode_failure_does_not_blame_steam(tmp_path, monkeypatch):
    """THE MISATTRIBUTION THAT COST A DEBUGGING SESSION.

    The advice was unconditional, so a `server` run - which starts no client at
    all - was told to check that its client's Steam login was working. Steam
    happened to be down at the time, which made the wrong advice fit, and both
    modes got filed under one cause. Only bringing Steam up, and watching
    `server` fail identically, exposed it.

    An error that names a cause the mode cannot have is worse than one that
    names none: it is confidently wrong, and it gets believed.
    """
    message = _wait_ready_error(tmp_path, monkeypatch, "server")

    assert "Steam is NOT the likely cause" in message
    assert "server_client" in message, "it should point at the mode that works"


def test_client_mode_failure_still_blames_steam(tmp_path, monkeypatch):
    """POSITIVE CONTROL.

    Steam really is the usual cause when a client is involved - that advice was
    correct and load-bearing, and a fix that removed it everywhere would trade
    one misdiagnosis for another.
    """
    message = _wait_ready_error(tmp_path, monkeypatch, "server_client")

    assert "Steam" in message
    assert "NOT the likely cause" not in message
