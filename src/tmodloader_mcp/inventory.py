"""What this install actually HAS: worlds, characters, and mods.

WHY THIS EXISTS

`launch` states two preconditions and can check neither of them.

    player  "Must already exist - `-player` does not create one, and a
             duplicate name is kicked."
    world   a WINDOWS path to a `.wld`, which the caller has to already know.

Both are facts about a directory sitting right there. `Players/` and `Worlds/`
are listable, and until now nothing here listed them — so the only way to learn
either was to launch and read the failure, which for a wrong player name is a
kick and for a wrong world path is a readiness timeout blaming the heartbeat.

This is the argument that produced `log_files`, which reads which logs exist
off disk rather than naming them as a constant, applied to the other two
directories the same reasoning covers.

THE THIRD ONE IS THE MOD LIST, AND IT IS THE POINT

`commands` answers `responder: false` when nothing published a command list.
That is one string for three different fixes: the mod is not built, or it is
built and not enabled, or it is enabled and was compiled without the dev
bridge. `session.py` says as much in a message - "check that a world is loaded
and the mod is enabled" - and gave the caller no way to check either.

ENABLED AND BUILT-HERE ARE DIFFERENT FACTS, deliberately reported as two
booleans rather than collapsed into "installed". On the install this was
written against, `enabled.json` lists `Biomancy` and `CheatSheet` while `Mods/`
holds only `Biomancy.tmod` — because a workshop mod is enabled from somewhere
else entirely. A single `installed` flag would call CheatSheet missing, which
is both wrong and the kind of wrong that sends someone rebuilding a mod that
was never the problem.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .config import windows_path_for


class InventoryError(RuntimeError):
    """Something on disk is here but unreadable, which is not the same as absent."""


@dataclass(frozen=True)
class World:
    """A world, with the spelling `launch` actually wants.

    `path_win` is None off a drive mount rather than guessed - the same rule
    `config.windows_path_for` follows, for the same reason: an invented UNC
    path is an answer nobody measured.
    """

    name: str
    path_win: str | None


@dataclass(frozen=True)
class Mod:
    name: str
    enabled: bool
    built_here: bool


def worlds(save_dir: Path) -> list[World]:
    """Every world in the install, newest first.

    `*.wld` and nothing else. The directory also holds `.twld` companions,
    `.bak`/`.bak2` copies and a `Backups/` folder, and a looser pattern would
    offer `The_Miasma_of_Grief.wld.bak` as something to launch.
    """
    folder = save_dir / "Worlds"
    if not folder.is_dir():
        return []

    found = sorted(folder.glob("*.wld"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [World(name=p.stem, path_win=windows_path_for(p)) for p in found]


def players(save_dir: Path) -> list[str]:
    """Every character name that exists, which is the set `launch` will accept."""
    folder = save_dir / "Players"
    if not folder.is_dir():
        return []

    return sorted(p.stem for p in folder.glob("*.plr"))


def mods(save_dir: Path) -> list[Mod]:
    """What is enabled, what is built here, and the union of the two.

    A missing `enabled.json` is an empty list: a fresh install has none, and
    that is a real state rather than a fault. A `enabled.json` that EXISTS and
    will not parse is an error, because it means something wrote a mod list
    this cannot read - which needs a human, not a default. That is the same
    line `commands` draws between `responder: false` and a raise.
    """
    folder = save_dir / "Mods"
    if not folder.is_dir():
        return []

    enabled: set[str] = set()
    manifest = folder / "enabled.json"
    if manifest.is_file():
        try:
            parsed = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            # OSError covers a file deleted or locked between the check and
            # the read; either way the answer is the same as unparseable JSON:
            # a mod list exists that this cannot read, which needs a human.
            raise InventoryError(
                f"{manifest} exists and is not valid JSON: {exc}"
            ) from exc
        if not isinstance(parsed, list):
            raise InventoryError(
                f"{manifest} is {type(parsed).__name__}, expected a list"
            )
        enabled = {str(name) for name in parsed}

    built = {p.stem for p in folder.glob("*.tmod")}

    return [
        Mod(name=name, enabled=name in enabled, built_here=name in built)
        for name in sorted(enabled | built)
    ]
