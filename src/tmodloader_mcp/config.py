"""Where tModLoader, the save directory and the mod source live.

Every path is an environment variable. THE THREE THAT NAMED A PERSON ARE NOW
REQUIRED — the save directory, the mod source and the world all used to default
to one developer's install, spelled out with their Windows username and their
mod's name.

That was right for phase 1 and is wrong for anything published. A default that
points somewhere plausible on the author's disk and nowhere on yours does not
fail: it resolves, `check` reports a missing directory you never mentioned, and
in the worst case it resolves to something that DOES exist and the server
drives an install you did not choose. A tool that silently operates on somebody
else's files is worse than one that asks for a path.

What keeps a default is what is not personal: tModLoader's Steam location, and
the Windows binaries under System32. Those are the same on every machine that
can run this at all, and requiring them would be ceremony rather than safety.

The mod source's WINDOWS name stays DERIVED rather than defaulted. Two
variables naming one directory can disagree, and a machine-specific default is
what made them disagree quietly: see `load`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .triggers import MOD_NAME, Artifacts, artifacts_for

#: Steam's own layout, identical on every machine with a default library. Not
#: personal, so it keeps a default; `TMODLOADER_DIR` overrides it for a library
#: on another drive.
DEFAULT_TML = "/mnt/c/Program Files (x86)/Steam/steamapps/common/tModLoader"

#: The variables with no default, because every plausible value names a person.
#: Reported together rather than one restart at a time.
REQUIRED = ("TMODLOADER_SAVE_DIR", "TMODLOADER_MOD_SOURCE")

#: What a caller additionally needs to `launch` WITHOUT naming a world. Kept
#: here rather than written out by each script that needs it: the live checks
#: both inherit their environment and set none of it, so this list is the thing
#: most likely to drift out of step with reality, and a copy per script is two
#: places to forget.
REQUIRED_TO_LAUNCH = (*REQUIRED, "TMODLOADER_WORLD_WIN")

#: A WSL mount of a Windows drive: `/mnt/c/...`. The lookahead keeps `/mnt/wsl`
#: out, where `wsl` is a directory rather than a drive letter.
_DRIVE_MOUNT = re.compile(r"^/mnt/(?P<drive>[A-Za-z])(?=/|$)")


def windows_path_for(path: Path) -> str | None:
    """What Windows calls this WSL path, or None if that cannot be worked out.

    tModLoader builds inside a WINDOWS process with no `/mnt/c`, so `-build`
    needs the Windows spelling of the mod source. Deriving it is what stops the
    two from disagreeing — see `load`.

    Only drive mounts are translated. `wslpath -w` will answer for any path, but
    for one outside `/mnt/<drive>` it answers with a `\\\\wsl.localhost\\` UNC
    path, and whether tModLoader can build from a UNC path is not something this
    repo has ever measured. None means "I do not know", which `check` turns into
    a request for the variable; inventing a UNC path here would be shipping an
    answer nobody verified.

    Not a call to `wslpath` for two reasons: it is a pure string operation on
    the paths that matter, and CI does not run under WSL, so a subprocess would
    make the one part with a test into the part that cannot be tested.
    """
    text = str(path)
    match = _DRIVE_MOUNT.match(text)
    if match is None:
        return None

    rest = text[match.end() :].lstrip("/").replace("/", "\\")
    return f"{match.group('drive').upper()}:\\{rest}"


DEFAULT_POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

DEFAULT_TASKKILL = "/mnt/c/Windows/System32/taskkill.exe"
DEFAULT_TASKLIST = "/mnt/c/Windows/System32/tasklist.exe"


class ConfigError(RuntimeError):
    """A path is missing or is not what it claims to be."""


@dataclass(frozen=True)
class Config:
    """Resolved paths. Frozen so a tool cannot mutate them mid-session."""

    tml_dir: Path
    save_dir: Path
    mod_source: Path
    #: None when `mod_source` is not on a Windows drive and nobody said what to
    #: call it instead. Unusable rather than wrong — `check` reports it, and
    #: `build` refuses rather than passing the word "None" to tModLoader.
    mod_source_win: str | None
    #: The mod's INTERNAL name, which is what every artifact filename is built
    #: from. tModLoader takes it from the source folder, so it defaults to that
    #: and rarely needs setting; a checkout whose folder is named something else
    #: is the case `TMODLOADER_MOD_NAME` exists for.
    mod_name: str
    #: The world a session loads, as WINDOWS sees it. tModLoader runs as a
    #: Windows process and cannot resolve a `/mnt/c` path, so this cannot be
    #: derived from `save_dir` — passing the WSL spelling makes the server fail
    #: to load a world, and the only symptom is a readiness timeout naming the
    #: wrong cause entirely.
    #:
    #: None when nobody said which world. `launch` then requires one and lists
    #: the worlds actually on this disk, which is a better answer than the
    #: default this used to carry: one developer's self-test world, by name.
    world_win: str | None
    taskkill: Path
    tasklist: Path
    powershell: Path
    #: Names of required variables that were not set. Carried on the config
    #: rather than raised from `load`, so `check` can report every problem in
    #: one pass — fixing paths one error message at a time is one restart each.
    #:
    #: Last, and defaulted, so a Config built by hand (every test fixture here)
    #: still constructs. A hand-built one has nothing unset by definition: the
    #: caller supplied the paths directly.
    unset: tuple[str, ...] = ()

    @property
    def artifacts(self) -> Artifacts:
        """The filenames this mod writes and this harness reads."""
        return artifacts_for(self.mod_name)

    @property
    def dotnet(self) -> Path:
        """tModLoader ships its own dotnet; use it rather than a system one."""
        return self.tml_dir / "dotnet" / "dotnet.exe"

    @property
    def tml_dll(self) -> Path:
        return self.tml_dir / "tModLoader.dll"

    def artifact(self, name: str, *, server: bool) -> Path:
        """A DevCapture artifact path for one side of a session.

        Both sides share one save directory, so the server's files are suffixed.
        This mirrors DevArtifacts.ForSide in the mod: if the two disagree about
        the naming, a harness reads the wrong side's answer and cannot tell.
        """
        if not server:
            return self.save_dir / name
        stem, dot, ext = name.rpartition(".")
        return self.save_dir / (f"{stem}-server{dot}{ext}" if dot else f"{name}-server")


def _setting(src, key: str, default: str) -> str:
    """One environment setting, treating an empty one as absent.

    `FOO=` is not the same as `FOO` unset, and `os.environ.get(key, default)`
    cannot tell them apart — it returns the empty string, and the default never
    applies. An empty `TMODLOADER_DIR` therefore became `Path("")`, which is
    `Path(".")`, and `check` reported that the WORKING DIRECTORY held no
    tModLoader.dll: a true sentence about a directory nobody meant.
    """
    return src.get(key, "").strip() or default


def load(env: dict[str, str] | None = None) -> Config:
    """Resolve configuration from the environment.

    Existence is checked HERE rather than at first use. A missing tModLoader
    directory should say so when the server starts, not sixty seconds into a
    launch that was never going to work.

    THE WINDOWS MOD SOURCE IS DERIVED, NOT DEFAULTED. It used to have a default
    of its own, which made a config that names two different mods representable
    and easy to reach: override `TMODLOADER_MOD_SOURCE` alone and the Windows
    path stayed pointing at this machine's Biomancy, so every tool here drove
    the caller's mod while `build_mod` compiled Biomancy — and reported success,
    because the build HAD succeeded. Two variables naming one directory only
    stay in step if one of them follows the other.
    """
    src = os.environ if env is None else env

    tml = Path(_setting(src, "TMODLOADER_DIR", DEFAULT_TML))
    # Not defaulted, and not raised either. An unset variable becomes `Path(".")`
    # and its NAME is recorded, so `check` can list every missing one at once
    # instead of stopping at the first. The recorded name is what makes that
    # distinguishable from someone genuinely configuring the working directory.
    save = Path(_setting(src, "TMODLOADER_SAVE_DIR", "."))
    mod = Path(_setting(src, "TMODLOADER_MOD_SOURCE", "."))
    unset = tuple(name for name in REQUIRED if not src.get(name, "").strip())

    return Config(
        tml_dir=tml,
        save_dir=save,
        mod_source=mod,
        unset=unset,
        mod_source_win=_setting(src, "TMODLOADER_MOD_SOURCE_WIN", "")
        or windows_path_for(mod),
        # tModLoader's internal name is the source folder's name, so that is the
        # default rather than a constant naming one mod.
        mod_name=_setting(src, "TMODLOADER_MOD_NAME", mod.name),
        # None rather than a default world. The one this carried was a specific
        # developer's self-test world by full path, so on anyone else's machine
        # it named a file that does not exist and the failure arrived as a
        # readiness timeout.
        world_win=_setting(src, "TMODLOADER_WORLD_WIN", "") or None,
        taskkill=Path(_setting(src, "TMODLOADER_TASKKILL", DEFAULT_TASKKILL)),
        tasklist=Path(_setting(src, "TMODLOADER_TASKLIST", DEFAULT_TASKLIST)),
        powershell=Path(_setting(src, "TMODLOADER_POWERSHELL", DEFAULT_POWERSHELL)),
    )


def check(cfg: Config) -> list[str]:
    """Problems that would make this config unusable, or an empty list.

    Returned rather than raised so a caller can report every problem at once.
    Fixing five paths one error message at a time is five restarts.
    """
    problems: list[str] = []

    # First, and returned ALONE. An unset variable resolved to `Path(".")`, so
    # every downstream check would also fire and complain about the working
    # directory - three true sentences about a directory nobody meant, burying
    # the one that says what to do.
    if cfg.unset:
        return [
            f"{name} is not set, and has no default because every plausible "
            "value names somebody's own install"
            for name in cfg.unset
        ]

    if not cfg.tml_dir.is_dir():
        problems.append(f"no tModLoader install at {cfg.tml_dir} (set TMODLOADER_DIR)")
    elif not cfg.tml_dll.is_file():
        problems.append(f"{cfg.tml_dir} exists but holds no tModLoader.dll")

    if not cfg.save_dir.is_dir():
        problems.append(
            f"no save directory at {cfg.save_dir} (set TMODLOADER_SAVE_DIR)"
        )

    if not cfg.mod_source.is_dir():
        problems.append(
            f"no mod source at {cfg.mod_source} (set TMODLOADER_MOD_SOURCE)"
        )
    elif not (cfg.mod_source / "build.txt").is_file():
        # build.txt is what makes a directory a tModLoader mod. Without it the
        # build fails with a much less obvious error.
        problems.append(f"{cfg.mod_source} has no build.txt, so it is not a mod source")

    if not MOD_NAME.match(cfg.mod_name):
        problems.append(
            f"TMODLOADER_MOD_NAME is {cfg.mod_name!r}, which cannot be a mod's "
            "internal name. It becomes the prefix of every artifact filename, "
            "so it must be letters and digits only - a separator would build a "
            "path rather than a name."
        )

    problems.extend(_windows_source_problems(cfg))

    return problems


def _windows_source_problems(cfg: Config) -> list[str]:
    """Whether the mod source's two names still describe one directory.

    Deriving the Windows path removes the case that actually happened — set one
    variable, inherit the other. It cannot remove the case where BOTH are set to
    different places, because that is a caller saying two contradictory things
    on purpose. So that one is reported rather than assumed away.
    """
    if cfg.mod_source_win is None:
        return [
            (
                f"cannot tell what {cfg.mod_source} is called from Windows: it "
                "is not under /mnt/<drive>, so set TMODLOADER_MOD_SOURCE_WIN. "
                "tModLoader builds inside a Windows process and cannot resolve "
                "a WSL path."
            )
        ]

    derived = windows_path_for(cfg.mod_source)
    if derived is None or _same_windows_path(derived, cfg.mod_source_win):
        return []

    return [
        (
            f"TMODLOADER_MOD_SOURCE_WIN is {cfg.mod_source_win}, but "
            f"{cfg.mod_source} is {derived} from Windows. Those name one "
            "directory and currently name two: every tool here would drive the "
            "first while `build_mod` compiled the second, and report success "
            "for doing it."
        )
    ]


def _same_windows_path(left: str, right: str) -> bool:
    """Windows path equality, near enough: case-insensitive, trailing `\\` moot."""
    return left.rstrip("\\").casefold() == right.rstrip("\\").casefold()
