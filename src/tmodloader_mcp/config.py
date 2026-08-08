"""Where tModLoader, the save directory and the mod source live.

Every path is overridable by environment variable, and most have a default that
points at this machine's Biomancy install. That is deliberate for phase 1: the
server has to be useful for the mod it was extracted from before it is useful
for anybody else's, and a server that refuses to start until five variables are
exported is a server nobody runs.

The exception is the mod source's WINDOWS name, which is DERIVED from its WSL
one rather than defaulted. Two variables naming one directory can disagree, and
a machine-specific default is what made them disagree quietly: see `load`.

Phase 2 makes MOD_SOURCE required and drops the Biomancy defaults, because a
public tool that silently drives somebody else's install is worse than one that
asks.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .triggers import MOD_NAME, Artifacts, artifacts_for

#: Defaults for the machine this was extracted from.
DEFAULT_TML = "/mnt/c/Program Files (x86)/Steam/steamapps/common/tModLoader"
DEFAULT_SAVE = "/mnt/c/Users/a2b32/Documents/My Games/Terraria/tModLoader"
DEFAULT_MOD_SOURCE = (
    "/mnt/c/Users/a2b32/Documents/My Games/Terraria/tModLoader/ModSources/Biomancy"
)

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


#: The world a session loads, as WINDOWS sees it. tModLoader runs as a Windows
#: process and cannot resolve a /mnt/c path, so this cannot be derived from
#: save_dir - passing the WSL path makes the server fail to load a world and the
#: only symptom is a readiness timeout that names the wrong cause entirely.
DEFAULT_WORLD_WIN = (
    r"C:\Users\a2b32\Documents\My Games\Terraria\tModLoader\Worlds"
    r"\BiomancySelfTest.wld"
)

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
    world_win: str
    taskkill: Path
    tasklist: Path
    powershell: Path

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
    save = Path(_setting(src, "TMODLOADER_SAVE_DIR", DEFAULT_SAVE))
    mod = Path(_setting(src, "TMODLOADER_MOD_SOURCE", DEFAULT_MOD_SOURCE))

    return Config(
        tml_dir=tml,
        save_dir=save,
        mod_source=mod,
        mod_source_win=_setting(src, "TMODLOADER_MOD_SOURCE_WIN", "")
        or windows_path_for(mod),
        # tModLoader's internal name is the source folder's name, so that is the
        # default rather than a constant naming one mod.
        mod_name=_setting(src, "TMODLOADER_MOD_NAME", mod.name),
        world_win=_setting(src, "TMODLOADER_WORLD_WIN", DEFAULT_WORLD_WIN),
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
