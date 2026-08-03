"""Where tModLoader, the save directory and the mod source live.

Every path is overridable by environment variable and every one has a default
that points at this machine's Biomancy install. That is deliberate for phase 1:
the server has to be useful for the mod it was extracted from before it is
useful for anybody else's, and a server that refuses to start until five
variables are exported is a server nobody runs.

Phase 2 makes MOD_SOURCE required and drops the Biomancy defaults, because a
public tool that silently drives somebody else's install is worse than one that
asks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Defaults for the machine this was extracted from.
DEFAULT_TML = "/mnt/c/Program Files (x86)/Steam/steamapps/common/tModLoader"
DEFAULT_SAVE = "/mnt/c/Users/user/Documents/My Games/Terraria/tModLoader"
DEFAULT_MOD_SOURCE = (
    "/mnt/c/Users/user/Documents/My Games/Terraria/tModLoader/ModSources/Biomancy"
)

#: The Windows-side path tModLoader itself wants for -build. It cannot take the
#: WSL path: the build runs inside a Windows process, which has no /mnt/c.
DEFAULT_MOD_SOURCE_WIN = (
    r"C:\Users\a2b32\Documents\My Games\Terraria\tModLoader\ModSources\Biomancy"
)

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
    mod_source_win: str
    taskkill: Path
    tasklist: Path

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


def load(env: dict[str, str] | None = None) -> Config:
    """Resolve configuration from the environment.

    Existence is checked HERE rather than at first use. A missing tModLoader
    directory should say so when the server starts, not sixty seconds into a
    launch that was never going to work.
    """
    src = os.environ if env is None else env

    tml = Path(src.get("TMODLOADER_DIR", DEFAULT_TML))
    save = Path(src.get("TMODLOADER_SAVE_DIR", DEFAULT_SAVE))
    mod = Path(src.get("TMODLOADER_MOD_SOURCE", DEFAULT_MOD_SOURCE))
    mod_win = src.get("TMODLOADER_MOD_SOURCE_WIN", DEFAULT_MOD_SOURCE_WIN)

    return Config(
        tml_dir=tml,
        save_dir=save,
        mod_source=mod,
        mod_source_win=mod_win,
        taskkill=Path(src.get("TMODLOADER_TASKKILL", DEFAULT_TASKKILL)),
        tasklist=Path(src.get("TMODLOADER_TASKLIST", DEFAULT_TASKLIST)),
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

    return problems
