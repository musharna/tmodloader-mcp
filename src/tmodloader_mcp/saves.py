"""Copy the save aside, and put it back.

WHY THIS EXISTS, stated more carefully than it first was. The claim this module
was built on — that ordinary runs quietly accumulate changes in the save — is
NOT TRUE as the harness stops a game today, and it was worth measuring rather
than assuming.

MEASURED 2026-08-17: a session launched, told to place tiles and to `give` an
item, then ended with `stop()`, wrote NEITHER the world file NOR the character
file. Both were byte-identical afterwards, and the world's mtime was six days
old. `stop` force-kills through taskkill, and a killed Terraria saves nothing.
So the ordinary short run is already safe, by accident rather than by design.

What is NOT safe, and is what this is for:

  - A LONG run. A dedicated server autosaves on its own schedule, so a session
    that outlives that interval writes the world without being asked.
  - A GRACEFUL exit, now or after any future change to how `stop` works. The
    safety above is a property of force-killing, which is an implementation
    detail nobody promised to keep.
  - Pointing the destructive verbs at a world somebody CARES about. `settile`
    and `cleartile` are the first verbs here that can ruin a build, and
    "probably nothing was saved" is not the assurance you want then.

The failure class is still the one worth naming: damage that is invisible when
it happens and only shows up as a later measurement nobody doubts. What changed
is the honest scope of it.

HARNESS-SIDE ON PURPOSE. The mod cannot do this. A running game holds the world
in memory and writes it out on its own schedule, so a save copied from inside
the process is a copy of something mid-flight. Copying files while nothing has
them open is the only version of this that is honest, which is why every entry
point here refuses while a tModLoader process exists.

WHAT IS COPIED: the configured world's `.wld` and `.twld`, and every `.plr` and
`.tplr`. Not `.bak` files - those are the game's own safety net and restoring a
stale one alongside a fresh save is worse than leaving it. Not the whole Worlds
directory either: that measured 41MB here against 3MB for one world, and a
snapshot nobody can afford to take is a snapshot nobody takes.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import Config

#: What a label may be. Deliberately narrow: a label becomes a DIRECTORY NAME,
#: so anything that could climb out of the snapshot root - a slash, a `..`, a
#: leading dot - is refused rather than sanitised. Sanitising silently maps two
#: labels onto one directory, and the second `take` would overwrite the first.
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: Taken automatically by `restore` before it overwrites anything, so a restore
#: is itself reversible. Reserved: a caller may not take this name, or the
#: undo would be the thing that destroyed the state it was meant to undo.
BEFORE_RESTORE = "auto-before-restore"

_RESERVED = (BEFORE_RESTORE,)

MANIFEST = "snapshot.json"


class SaveError(RuntimeError):
    """A snapshot could not be taken or put back, and why."""


class NothingToSnapshot(SaveError):
    """The save directory holds no world or player files to copy.

    A SUBCLASS because one caller must tell it apart: `restore` swallows this
    when taking its automatic undo - an empty install has nothing to undo TO,
    and refusing the restore over that would be wrong - and used to swallow
    its parent instead, which also covered disk-full and an unwritable
    snapshot root. The restore then overwrote the live save with no backup
    while `undo: None` claimed there had been nothing to back up: the one
    situation where a caller most wants a refusal, answered with a silent
    loss of the safety copy.
    """


@dataclass(frozen=True)
class Snapshot:
    """One saved copy, and what is in it."""

    label: str
    #: Epoch seconds, from the machine that took it.
    taken: float
    #: Names relative to the snapshot directory, in the order they were copied.
    files: tuple[str, ...]
    size: int

    @property
    def age(self) -> float:
        return max(0.0, time.time() - self.taken)


@dataclass(frozen=True)
class Restored:
    """What a restore put back."""

    label: str
    files: tuple[str, ...]
    size: int
    #: The label of the automatic snapshot taken before overwriting, so the
    #: restore can itself be undone. None only when there was nothing to save.
    undo: str | None
    #: Files in the snapshot's SCOPE that existed on disk and not in the
    #: snapshot, deleted so the restored state is the recorded state - see
    #: `restore` for the mismatched-pair failure this closes. They are in the
    #: undo snapshot, so removal is as reversible as the overwrite beside it.
    removed: tuple[str, ...] = ()


def snapshot_root(cfg: Config) -> Path:
    """Where snapshots live: beside the harness's own state, not in the game.

    NOT inside `save_dir`. tModLoader scans its own directories, and a folder
    full of `.wld` copies under `Worlds/` would show up in the game's world
    list as a pile of near-identical entries somebody then has to tell apart.
    """
    return cfg.save_dir.parent / ".tmodloader-mcp-snapshots"


def _world_stem(cfg: Config) -> str:
    """The configured world's name, with no extension and no directory.

    `world_win` is a WINDOWS path, so the separator is a backslash and
    `Path(...).name` on Linux would return the whole string unchanged - the
    kind of quiet wrong answer that produces a snapshot of nothing.
    """
    if not cfg.world_win:
        raise SaveError(
            "no world is configured, so there is nothing to snapshot. Set "
            "TMODLOADER_WORLD_WIN to the world this session drives."
        )

    name = cfg.world_win.replace("\\", "/").rsplit("/", 1)[-1]
    stem = name[: -len(".wld")] if name.casefold().endswith(".wld") else name
    if not stem:
        raise SaveError(
            f"TMODLOADER_WORLD_WIN does not name a world: {cfg.world_win!r}"
        )
    return stem


def _sources(cfg: Config) -> list[tuple[Path, str]]:
    """Every file worth copying, as (path on disk, name inside the snapshot).

    Returned even when a file does not exist yet - the caller reports what it
    actually found, so a world that has never been saved is visible as an
    absence rather than as a snapshot that silently holds less than it claims.
    """
    stem = _world_stem(cfg)
    worlds = cfg.save_dir / "Worlds"
    players = cfg.save_dir / "Players"

    found: list[tuple[Path, str]] = []
    for suffix in (".wld", ".twld"):
        found.append((worlds / f"{stem}{suffix}", f"Worlds/{stem}{suffix}"))

    if players.is_dir():
        for path in sorted(players.iterdir()):
            if path.is_file() and path.suffix in (".plr", ".tplr"):
                found.append((path, f"Players/{path.name}"))

    return found


def _check_label(label: str, *, reserved_ok: bool = False) -> str:
    if not _LABEL.match(label or ""):
        raise SaveError(
            f"{label!r} is not a usable label. Letters, digits, dot, dash and "
            "underscore, starting with a letter or digit, up to 64 characters "
            "- a label becomes a directory name, so anything that could climb "
            "out of the snapshot folder is refused rather than rewritten."
        )

    if label in _RESERVED and not reserved_ok:
        raise SaveError(
            f"{label!r} is reserved: `restore` writes it automatically so that "
            "a restore can be undone. Taking it by hand would overwrite the "
            "one copy that undo depends on."
        )

    return label


def _check_idle(cfg: Config, doing: str, pids: Callable[[Config], set[int]]) -> None:
    """Refuse while a game is running, naming what is running.

    A running tModLoader holds the world in memory and writes it out on its
    own schedule. Copying then yields a file that is neither the state before
    nor the state after; restoring then is overwritten the next time the game
    saves, so it appears to work and then undoes itself - the same failure the
    mutating verbs refuse a client for.
    """
    live = pids(cfg)
    if live:
        raise SaveError(
            f"cannot {doing} while tModLoader is running (pids "
            f"{sorted(live)}). A running game owns these files: a copy taken "
            "now is mid-write, and a restore is overwritten by the next save. "
            "Stop the session first."
        )


def _default_pids(cfg: Config) -> set[int]:
    # Imported here rather than at module scope: session imports a good deal of
    # the package, and this module is also used by tests that want neither.
    from .session import _tml_pids

    return _tml_pids(cfg)


def take(
    cfg: Config,
    label: str,
    *,
    pids: Callable[[Config], set[int]] = _default_pids,
    reserved_ok: bool = False,
) -> Snapshot:
    """Copy the save aside under `label`, replacing any snapshot of that name."""
    _check_label(label, reserved_ok=reserved_ok)
    _check_idle(cfg, f"take the snapshot {label!r}", pids)

    into = snapshot_root(cfg) / label
    staging = snapshot_root(cfg) / f".{label}.partial"

    # Staged, then swapped. A snapshot half-written when something goes wrong
    # is worse than no snapshot: it restores cleanly and puts back a world
    # missing its tModLoader half.
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    size = 0
    try:
        for source, name in _sources(cfg):
            if not source.is_file():
                continue
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(name)
            size += target.stat().st_size

        if not copied:
            raise NothingToSnapshot(
                f"nothing to snapshot: no world or player files were found "
                f"under {cfg.save_dir}. Expected "
                f"Worlds/{_world_stem(cfg)}.wld."
            )

        taken = time.time()
        (staging / MANIFEST).write_text(
            json.dumps(
                {"label": label, "taken": taken, "files": copied, "size": size},
                indent=2,
            )
            + "\n"
        )

        # THE OLD SNAPSHOT IS MOVED ASIDE, NOT DELETED, until the new one is
        # in place. rmtree-then-replace had a hole exactly where this
        # directory lives: `replace` can fail on a transient /mnt/c lock (the
        # failure mode config.py documents as real here), and the error
        # handler below then also removed the staging copy - so re-taking a
        # snapshot destroyed BOTH the old one and the new one, and "take
        # `pre-test` again" ended with no `pre-test` at all. A rename is one
        # directory entry either way; the aside copy is restored on failure
        # and only deleted once the swap has actually happened.
        aside = snapshot_root(cfg) / f".{label}.replaced"
        shutil.rmtree(aside, ignore_errors=True)
        had_previous = into.is_dir()
        if had_previous:
            into.replace(aside)

        try:
            staging.replace(into)
        except OSError:
            if had_previous:
                with contextlib.suppress(OSError):
                    aside.replace(into)
            raise

        shutil.rmtree(aside, ignore_errors=True)
    except SaveError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except OSError as failed:
        shutil.rmtree(staging, ignore_errors=True)
        raise SaveError(f"could not take the snapshot {label!r}: {failed}") from failed

    return Snapshot(label=label, taken=taken, files=tuple(copied), size=size)


def _name_is_contained(name: str) -> bool:
    """Whether a manifest file name stays inside the directories it is joined
    to. `take` only ever writes `Worlds/x` and `Players/x`, so anything that
    could climb - a leading separator, a drive letter, a `..` segment, a
    backslash that Windows would read as one - is a manifest somebody edited,
    not one this module wrote."""
    if not name or name.startswith(("/", "\\")) or "\\" in name:
        return False
    if re.match(r"^[A-Za-z]:", name):
        return False
    return ".." not in name.split("/")


def read(cfg: Config, label: str) -> Snapshot | None:
    """One snapshot as recorded, or None if there is no such label."""
    manifest = snapshot_root(cfg) / label / MANIFEST
    if not manifest.is_file():
        return None

    try:
        held = json.loads(manifest.read_text(encoding="utf-8"))
        found = Snapshot(
            label=held["label"],
            taken=float(held["taken"]),
            files=tuple(str(name) for name in held["files"]),
            size=int(held["size"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        # A manifest that cannot be read means a snapshot that cannot be
        # trusted. Reported as absent rather than as an empty one, so a
        # restore refuses instead of putting back nothing.
        return None

    # A name that escapes its directory is the same untrustworthiness in a
    # sharper shape: `restore` joins these to save_dir and COPIES there, so a
    # tampered `../../x` would write outside the save. `captures` refuses this
    # class structurally, and a reader with a weaker rule than the writer
    # beside it is how that guarantee erodes.
    if not all(_name_is_contained(name) for name in found.files):
        return None

    return found


def listing(cfg: Config) -> tuple[Snapshot, ...]:
    """Every readable snapshot, newest first."""
    root = snapshot_root(cfg)
    if not root.is_dir():
        return ()

    held = []
    for path in root.iterdir():
        if path.is_dir() and not path.name.startswith("."):
            found = read(cfg, path.name)
            if found is not None:
                held.append(found)

    return tuple(sorted(held, key=lambda s: s.taken, reverse=True))


def restore(
    cfg: Config,
    label: str,
    *,
    pids: Callable[[Config], set[int]] = _default_pids,
) -> Restored:
    """Put `label` back, after saving what is there now so this can be undone."""
    _check_label(label, reserved_ok=True)
    _check_idle(cfg, f"restore the snapshot {label!r}", pids)

    held = read(cfg, label)
    if held is None:
        names = [s.label for s in listing(cfg)]
        raise SaveError(
            f"there is no snapshot called {label!r}. "
            + (f"There is: {', '.join(names)}." if names else "There are none yet.")
        )

    # UNDO FIRST. This is about to overwrite a real save, and the caller may
    # have meant a different label. Taking the current state under a reserved
    # name costs one copy and makes the mistake recoverable instead of final.
    undo: str | None = None
    if label != BEFORE_RESTORE:
        try:
            undo = take(cfg, BEFORE_RESTORE, pids=pids, reserved_ok=True).label
        except NothingToSnapshot:
            # Nothing on disk to save (a first run against an empty install) is
            # not a reason to refuse the restore - there is simply nothing to
            # undo to, and the caller is told so by `undo` staying None.
            #
            # ONLY that case. This caught every SaveError, which also covers
            # disk-full and an unwritable snapshot root - and swallowing those
            # overwrote the live save with no backup while `undo: None`
            # claimed there had been nothing to back up. A failed safety copy
            # refuses the restore; a genuinely absent one waves it through.
            undo = None

    from_dir = snapshot_root(cfg) / label
    put: list[str] = []
    size = 0
    try:
        for name in held.files:
            source = from_dir / name
            if not source.is_file():
                continue
            target = cfg.save_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            put.append(name)
            size += target.stat().st_size
    except OSError as failed:
        raise SaveError(
            f"the restore of {label!r} failed partway through ({failed}). "
            + (
                f"What was there before this started is in {undo!r}."
                if undo
                else "Nothing was saved beforehand."
            )
        ) from failed

    if not put:
        raise SaveError(
            f"the snapshot {label!r} lists {len(held.files)} file(s) but none "
            "of them are on disk. The snapshot directory has been emptied."
        )

    # FILES THE SNAPSHOT DOES NOT HOLD ARE REMOVED - within its scope, which
    # is exactly the set `take` would copy today. Copy-only restore left
    # anything created SINCE the snapshot in place, so restoring a world
    # snapshotted before tModLoader wrote its `.twld` put the old `.wld` back
    # beside the newer `.twld` - precisely the mismatched-pair state the
    # staging comment above calls worse than no snapshot. Removal happens
    # AFTER the copies and only after `put` is known non-empty, and everything
    # deleted here is in the undo snapshot taken above, so it undoes with the
    # rest.
    removed: list[str] = []
    recorded = set(held.files)
    for source, name in _sources(cfg):
        if name in recorded or not source.is_file():
            continue
        try:
            source.unlink()
        except OSError as failed:
            raise SaveError(
                f"the restore of {label!r} put its files back but could not "
                f"remove {name}, which the snapshot does not hold ({failed}). "
                f"The save now mixes two states"
                + (f"; what was there before is in {undo!r}." if undo else ".")
            ) from failed
        removed.append(name)

    return Restored(
        label=label, files=tuple(put), size=size, undo=undo, removed=tuple(removed)
    )


def forget(cfg: Config, label: str) -> Snapshot | None:
    """Delete one snapshot. Returns what was deleted, or None if absent."""
    _check_label(label, reserved_ok=True)
    held = read(cfg, label)
    if held is None:
        return None

    shutil.rmtree(snapshot_root(cfg) / label, ignore_errors=True)
    return held
