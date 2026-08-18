"""Copying the save aside, and putting it back.

REAL FILES, in a tmp_path. The whole point of this module is what happens to
bytes on a disk, and a test that mocked the copying would assert that the code
calls the functions the code calls.

THE PROPERTY THAT MATTERS MOST IS THE REFUSAL. Every entry point refuses while
tModLoader is running, because a copy taken from under a live game is neither
the state before nor the state after, and a restore is overwritten by the next
autosave - it appears to work and then undoes itself. `pids` is injected so
that "a game is running" is a fact a test can state, rather than something
that needs a game.

Every refusal here is paired with a POSITIVE CONTROL in the same test: the same
call succeeding when the refused condition is absent. Without it, a snapshot
function that raised unconditionally would pass every refusal test in the file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tmodloader_mcp import config as config_mod
from tmodloader_mcp import saves

WORLD = "TestWorld"

IDLE: set[int] = set()
RUNNING = {4242, 99}


def _no_pids(_cfg: config_mod.Config) -> set[int]:
    return IDLE


def _busy_pids(_cfg: config_mod.Config) -> set[int]:
    return RUNNING


def _cfg(root: Path, *, world: str | None = rf"C:\Worlds\{WORLD}.wld"):
    save = root / "tModLoader"
    (save / "Worlds").mkdir(parents=True, exist_ok=True)
    (save / "Players").mkdir(parents=True, exist_ok=True)
    return config_mod.Config(
        tml_dir=root / "game",
        save_dir=save,
        mod_source=root / "src",
        mod_source_win=r"C:\src",
        mod_name="Yours",
        world_win=world,
        taskkill=Path("/taskkill"),
        tasklist=Path("/tasklist"),
        powershell=Path("/powershell"),
    )


def _populate(cfg: config_mod.Config, *, world_body: bytes = b"world-v1") -> None:
    (cfg.save_dir / "Worlds" / f"{WORLD}.wld").write_bytes(world_body)
    (cfg.save_dir / "Worlds" / f"{WORLD}.twld").write_bytes(b"tmod-v1")
    (cfg.save_dir / "Players" / "n43n.plr").write_bytes(b"player-v1")
    (cfg.save_dir / "Players" / "n43n.tplr").write_bytes(b"tplayer-v1")


# ---- what gets copied ------------------------------------------------------


def test_a_snapshot_holds_the_world_and_the_players(tmp_path):
    cfg = _cfg(tmp_path)
    _populate(cfg)

    held = saves.take(cfg, "first", pids=_no_pids)

    assert set(held.files) == {
        f"Worlds/{WORLD}.wld",
        f"Worlds/{WORLD}.twld",
        "Players/n43n.plr",
        "Players/n43n.tplr",
    }
    assert held.size > 0


def test_the_snapshot_is_a_real_copy_rather_than_a_note_about_one(tmp_path):
    cfg = _cfg(tmp_path)
    _populate(cfg)
    saves.take(cfg, "first", pids=_no_pids)

    copied = saves.snapshot_root(cfg) / "first" / "Worlds" / f"{WORLD}.wld"
    assert copied.read_bytes() == b"world-v1"


def test_backups_are_left_alone(tmp_path):
    """`.bak` is the game's own safety net.

    Restoring a stale one beside a fresh save is worse than leaving it: the
    game would then hold two disagreeing ideas of the same world, and the
    older one is the one it falls back to.
    """
    cfg = _cfg(tmp_path)
    _populate(cfg)
    (cfg.save_dir / "Worlds" / f"{WORLD}.wld.bak").write_bytes(b"stale")
    (cfg.save_dir / "Players" / "n43n.plr.bak").write_bytes(b"stale")

    held = saves.take(cfg, "first", pids=_no_pids)

    assert not any(name.endswith(".bak") for name in held.files), held.files


def test_other_worlds_are_left_alone(tmp_path):
    """One world, not the directory. Measured 41MB against 3MB on a real
    install, and a snapshot nobody can afford is a snapshot nobody takes."""
    cfg = _cfg(tmp_path)
    _populate(cfg)
    (cfg.save_dir / "Worlds" / "SomeoneElses.wld").write_bytes(b"not mine")

    held = saves.take(cfg, "first", pids=_no_pids)

    assert not any("SomeoneElses" in name for name in held.files), held.files


def test_snapshots_do_not_live_where_the_game_will_find_them(tmp_path):
    """tModLoader scans its own directories. A folder of `.wld` copies under
    `Worlds/` becomes a pile of near-identical entries in the world list."""
    cfg = _cfg(tmp_path)
    _populate(cfg)
    saves.take(cfg, "first", pids=_no_pids)

    root = saves.snapshot_root(cfg).resolve()
    assert cfg.save_dir.resolve() not in root.parents
    assert root != cfg.save_dir.resolve()


def test_the_windows_world_path_is_read_as_windows_spells_it(tmp_path):
    """A backslash path through `Path(...).name` on Linux comes back whole,
    which would look for a world named after the entire path."""
    cfg = _cfg(tmp_path, world=r"C:\Users\someone\Worlds\TestWorld.wld")
    _populate(cfg)

    held = saves.take(cfg, "first", pids=_no_pids)

    assert f"Worlds/{WORLD}.wld" in held.files


def test_a_world_that_was_never_configured_is_refused(tmp_path):
    cfg = _cfg(tmp_path, world=None)

    with pytest.raises(saves.SaveError, match="TMODLOADER_WORLD_WIN"):
        saves.take(cfg, "first", pids=_no_pids)


def test_a_save_with_nothing_in_it_is_refused_rather_than_snapshotted_empty(tmp_path):
    """An empty snapshot restores cleanly and puts back nothing, which is the
    worst of the available outcomes: it reports success."""
    cfg = _cfg(tmp_path)

    with pytest.raises(saves.SaveError, match="nothing to snapshot"):
        saves.take(cfg, "first", pids=_no_pids)

    # POSITIVE CONTROL: with files present the same call succeeds, so the
    # refusal above is about emptiness rather than about being broken.
    _populate(cfg)
    assert saves.take(cfg, "first", pids=_no_pids).files


# ---- the refusal while a game is running -----------------------------------


def test_a_snapshot_is_refused_while_the_game_is_running(tmp_path):
    cfg = _cfg(tmp_path)
    _populate(cfg)

    with pytest.raises(saves.SaveError) as refused:
        saves.take(cfg, "first", pids=_busy_pids)

    assert "4242" in str(refused.value), "the refusal does not name what is running"

    # POSITIVE CONTROL: the identical call with no game running works.
    assert saves.take(cfg, "first", pids=_no_pids).files


def test_a_restore_is_refused_while_the_game_is_running(tmp_path):
    cfg = _cfg(tmp_path)
    _populate(cfg)
    saves.take(cfg, "first", pids=_no_pids)

    with pytest.raises(saves.SaveError) as refused:
        saves.restore(cfg, "first", pids=_busy_pids)

    assert "4242" in str(refused.value)

    # POSITIVE CONTROL, in the same test.
    assert saves.restore(cfg, "first", pids=_no_pids).files


# ---- labels ----------------------------------------------------------------


@pytest.mark.parametrize(
    "label",
    ["../escape", "a/b", "", ".hidden", "..", "with space", "x" * 65, "/abs"],
)
def test_a_label_that_could_climb_out_of_the_folder_is_refused(tmp_path, label):
    cfg = _cfg(tmp_path)
    _populate(cfg)

    with pytest.raises(saves.SaveError):
        saves.take(cfg, label, pids=_no_pids)


def test_the_undo_label_cannot_be_taken_by_hand(tmp_path):
    """It is the one copy a restore's undo depends on."""
    cfg = _cfg(tmp_path)
    _populate(cfg)

    with pytest.raises(saves.SaveError, match="reserved"):
        saves.take(cfg, saves.BEFORE_RESTORE, pids=_no_pids)


def test_a_second_snapshot_of_one_label_replaces_the_first(tmp_path):
    cfg = _cfg(tmp_path)
    _populate(cfg, world_body=b"world-v1")
    saves.take(cfg, "first", pids=_no_pids)

    _populate(cfg, world_body=b"world-v2")
    saves.take(cfg, "first", pids=_no_pids)

    copied = saves.snapshot_root(cfg) / "first" / "Worlds" / f"{WORLD}.wld"
    assert copied.read_bytes() == b"world-v2"


# ---- putting it back -------------------------------------------------------


def test_a_restore_puts_the_bytes_back(tmp_path):
    cfg = _cfg(tmp_path)
    _populate(cfg, world_body=b"world-v1")
    saves.take(cfg, "before", pids=_no_pids)

    _populate(cfg, world_body=b"world-RUINED")
    assert (cfg.save_dir / "Worlds" / f"{WORLD}.wld").read_bytes() == b"world-RUINED"

    saves.restore(cfg, "before", pids=_no_pids)

    assert (cfg.save_dir / "Worlds" / f"{WORLD}.wld").read_bytes() == b"world-v1"


def test_a_restore_saves_what_it_is_about_to_overwrite(tmp_path):
    """The caller may have named the wrong label. One copy makes that
    recoverable instead of final."""
    cfg = _cfg(tmp_path)
    _populate(cfg, world_body=b"world-v1")
    saves.take(cfg, "before", pids=_no_pids)

    _populate(cfg, world_body=b"world-current")
    put = saves.restore(cfg, "before", pids=_no_pids)

    assert put.undo == saves.BEFORE_RESTORE
    undone = saves.snapshot_root(cfg) / saves.BEFORE_RESTORE / "Worlds" / f"{WORLD}.wld"
    assert undone.read_bytes() == b"world-current"


def test_the_undo_can_actually_be_restored(tmp_path):
    """The undo snapshot is worth nothing if restoring it is refused."""
    cfg = _cfg(tmp_path)
    _populate(cfg, world_body=b"world-v1")
    saves.take(cfg, "before", pids=_no_pids)

    _populate(cfg, world_body=b"world-current")
    saves.restore(cfg, "before", pids=_no_pids)
    assert (cfg.save_dir / "Worlds" / f"{WORLD}.wld").read_bytes() == b"world-v1"

    saves.restore(cfg, saves.BEFORE_RESTORE, pids=_no_pids)
    assert (cfg.save_dir / "Worlds" / f"{WORLD}.wld").read_bytes() == b"world-current"


def test_restoring_a_label_that_does_not_exist_names_the_ones_that_do(tmp_path):
    cfg = _cfg(tmp_path)
    _populate(cfg)
    saves.take(cfg, "alpha", pids=_no_pids)
    saves.take(cfg, "beta", pids=_no_pids)

    with pytest.raises(saves.SaveError) as refused:
        saves.restore(cfg, "gamma", pids=_no_pids)

    assert "alpha" in str(refused.value) and "beta" in str(refused.value)


def test_a_snapshot_whose_manifest_is_unreadable_counts_as_absent(tmp_path):
    """Reported absent rather than empty, so a restore refuses instead of
    putting back nothing and reporting success."""
    cfg = _cfg(tmp_path)
    _populate(cfg)
    saves.take(cfg, "first", pids=_no_pids)
    (saves.snapshot_root(cfg) / "first" / saves.MANIFEST).write_text("{ not json")

    assert saves.read(cfg, "first") is None
    with pytest.raises(saves.SaveError):
        saves.restore(cfg, "first", pids=_no_pids)


def test_a_snapshot_emptied_behind_our_back_refuses_rather_than_reports_success(
    tmp_path,
):
    cfg = _cfg(tmp_path)
    _populate(cfg)
    saves.take(cfg, "first", pids=_no_pids)

    for stray in (saves.snapshot_root(cfg) / "first").rglob("*"):
        if stray.is_file() and stray.name != saves.MANIFEST:
            stray.unlink()

    with pytest.raises(saves.SaveError, match="none"):
        saves.restore(cfg, "first", pids=_no_pids)


# ---- listing and forgetting ------------------------------------------------


def test_snapshots_are_listed_newest_first(tmp_path):
    cfg = _cfg(tmp_path)
    _populate(cfg)

    saves.take(cfg, "older", pids=_no_pids)
    saves.take(cfg, "newer", pids=_no_pids)

    # Stamped rather than slept: the manifest is the record, and two copies of
    # a few KB can land inside one clock tick.
    manifest = saves.snapshot_root(cfg) / "older" / saves.MANIFEST
    held = json.loads(manifest.read_text())
    held["taken"] = held["taken"] - 3600
    manifest.write_text(json.dumps(held))

    assert [s.label for s in saves.listing(cfg)] == ["newer", "older"]


def test_a_partial_snapshot_is_never_listed(tmp_path):
    """Staged under a dotted name and swapped into place. A half-written
    snapshot restores cleanly and puts back a world missing its other half."""
    cfg = _cfg(tmp_path)
    _populate(cfg)
    saves.take(cfg, "first", pids=_no_pids)

    partial = saves.snapshot_root(cfg) / ".leftover.partial"
    partial.mkdir(parents=True, exist_ok=True)
    (partial / saves.MANIFEST).write_text(
        json.dumps({"label": "leftover", "taken": 0.0, "files": [], "size": 0})
    )

    assert [s.label for s in saves.listing(cfg)] == ["first"]


def test_forgetting_removes_it_and_says_what_went(tmp_path):
    cfg = _cfg(tmp_path)
    _populate(cfg)
    saves.take(cfg, "first", pids=_no_pids)

    gone = saves.forget(cfg, "first")

    assert gone is not None and gone.label == "first"
    assert saves.read(cfg, "first") is None
    assert saves.forget(cfg, "first") is None


# ---- re-taking cannot destroy both copies ----------------------------------


def test_a_failed_swap_keeps_the_old_snapshot(tmp_path, monkeypatch):
    """rmtree-then-replace had a hole exactly where this directory lives: a
    transient /mnt/c lock failing the replace AFTER the old copy was deleted,
    with the error handler then removing the staging copy too. "Take
    `pre-test` again" ended with no `pre-test` at all - old or new."""
    cfg = _cfg(tmp_path)
    _populate(cfg, world_body=b"old-state")
    saves.take(cfg, "pre-test", pids=_no_pids)

    _populate(cfg, world_body=b"new-state")

    real_replace = Path.replace
    failures = {"left": 1}

    def failing_swap(self, target):
        # The FIRST swap into the label's own directory fails and the lock
        # then clears - a momentary /mnt/c lock's actual shape. The put-back
        # of the aside copy is the beneficiary of the clearing, exactly as it
        # would be live; a lock that never clears leaves the old snapshot in
        # the aside directory, which is recoverable by hand and out of scope.
        if Path(target).name == "pre-test" and failures["left"] > 0:
            failures["left"] -= 1
            raise OSError("resource busy")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", failing_swap)
    with pytest.raises(saves.SaveError, match="pre-test"):
        saves.take(cfg, "pre-test", pids=_no_pids)
    monkeypatch.undo()

    held = saves.read(cfg, "pre-test")
    assert held is not None, "the failed re-take destroyed the old snapshot too"
    snapshot_world = saves.snapshot_root(cfg) / "pre-test" / "Worlds" / f"{WORLD}.wld"
    assert snapshot_world.read_bytes() == b"old-state", (
        "the surviving snapshot is not the one that existed before the failure"
    )


def test_retaking_a_label_replaces_it_cleanly(tmp_path):
    """POSITIVE CONTROL: the aside dance is invisible when nothing fails."""
    cfg = _cfg(tmp_path)
    _populate(cfg, world_body=b"first")
    saves.take(cfg, "again", pids=_no_pids)

    _populate(cfg, world_body=b"second")
    saves.take(cfg, "again", pids=_no_pids)

    snapshot_world = saves.snapshot_root(cfg) / "again" / "Worlds" / f"{WORLD}.wld"
    assert snapshot_world.read_bytes() == b"second"
    assert not (saves.snapshot_root(cfg) / ".again.replaced").exists()
    assert not (saves.snapshot_root(cfg) / ".again.partial").exists()


# ---- the undo swallow is narrow now -----------------------------------------


def test_a_failed_undo_refuses_the_restore_instead_of_proceeding_without_one(
    tmp_path, monkeypatch
):
    """The `except SaveError` around the automatic undo caught disk-full and
    an unwritable snapshot root along with "nothing to snapshot" - so the one
    situation where a caller most wants a refusal overwrote the live save
    with no backup, while `undo: None` claimed there was nothing to back up."""
    cfg = _cfg(tmp_path)
    _populate(cfg)
    saves.take(cfg, "known-good", pids=_no_pids)

    real_take = saves.take

    def disk_full(cfg_, label, **kwargs):
        if label == saves.BEFORE_RESTORE:
            raise saves.SaveError("could not take the snapshot: disk full")
        return real_take(cfg_, label, **kwargs)

    monkeypatch.setattr(saves, "take", disk_full)

    with pytest.raises(saves.SaveError, match="disk full"):
        saves.restore(cfg, "known-good", pids=_no_pids)


def test_an_empty_install_still_restores_with_undo_none(tmp_path):
    """POSITIVE CONTROL: the case the swallow exists for still works - a
    first restore against an empty save has nothing to undo TO, and that is
    not a reason to refuse."""
    cfg = _cfg(tmp_path)
    _populate(cfg)
    saves.take(cfg, "seed", pids=_no_pids)

    for path in (cfg.save_dir / "Worlds").iterdir():
        path.unlink()
    for path in (cfg.save_dir / "Players").iterdir():
        path.unlink()

    put = saves.restore(cfg, "seed", pids=_no_pids)

    assert put.undo is None
    assert (cfg.save_dir / "Worlds" / f"{WORLD}.wld").is_file()


# ---- restore removes what the snapshot does not hold ------------------------


def test_restore_removes_files_created_after_the_snapshot(tmp_path):
    """Copy-only restore left a `.twld` written after the snapshot beside the
    restored `.wld` - the mismatched-pair state this module's own staging
    comment calls worse than no snapshot."""
    cfg = _cfg(tmp_path)
    (cfg.save_dir / "Worlds" / f"{WORLD}.wld").write_bytes(b"world-only")
    saves.take(cfg, "before-twld", pids=_no_pids)

    (cfg.save_dir / "Worlds" / f"{WORLD}.twld").write_bytes(b"newer-tmod-half")

    put = saves.restore(cfg, "before-twld", pids=_no_pids)

    assert f"Worlds/{WORLD}.twld" in put.removed
    assert not (cfg.save_dir / "Worlds" / f"{WORLD}.twld").exists(), (
        "the restored world kept a tModLoader half from a different state"
    )
    # And the removal is as reversible as the overwrite: the undo holds it.
    undone = saves.restore(cfg, put.undo, pids=_no_pids)
    assert (cfg.save_dir / "Worlds" / f"{WORLD}.twld").read_bytes() == (
        b"newer-tmod-half"
    ), f"the undo {undone.label!r} did not bring the removed file back"


# ---- the manifest is not trusted with paths ---------------------------------


def test_a_manifest_naming_a_path_outside_the_save_is_not_a_snapshot(tmp_path):
    """`restore` joins manifest names to save_dir and COPIES there, so a
    tampered `../../x` would write outside the save directory. The same
    policy as an unreadable manifest: reported as absent, so a restore
    refuses rather than acting on it."""
    cfg = _cfg(tmp_path)
    _populate(cfg)
    saves.take(cfg, "tampered", pids=_no_pids)

    manifest = saves.snapshot_root(cfg) / "tampered" / saves.MANIFEST
    held = json.loads(manifest.read_text())
    for bad in ("../../escape.wld", "/etc/escape", "C:\\escape", "Worlds\\..\\..\\x"):
        held["files"] = [bad]
        manifest.write_text(json.dumps(held))
        assert saves.read(cfg, "tampered") is None, f"{bad!r} was trusted"

    # POSITIVE CONTROL: the untampered shape still reads.
    held["files"] = [f"Worlds/{WORLD}.wld"]
    manifest.write_text(json.dumps(held))
    assert saves.read(cfg, "tampered") is not None
