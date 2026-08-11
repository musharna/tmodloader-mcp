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

import contextlib
import os
import re
from dataclasses import dataclass
from functools import lru_cache
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

#: A variable reference that nothing ever substituted: `${FOO}` or `$FOO`, whole
#: and alone. `.mcp.json` passes the two required paths through as `${NAME}` so
#: that nobody's checkout carries anybody's disk, and the client expands them
#: against ITS OWN environment — so a client launched without them exports the
#: placeholder verbatim rather than failing.
_UNEXPANDED = re.compile(r"^\$(?:\{[A-Za-z_]\w*\}|[A-Za-z_]\w*)$")


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
    #: `(name, literal)` for every variable whose value is a reference nobody
    #: substituted. Kept apart from `unset` because the two need DIFFERENT
    #: instructions: an unset variable is fixed by exporting it, while a
    #: placeholder means the export probably already exists and the process that
    #: launched this server could not see it. Told to export it, the reader does
    #: exactly what they have already done and gets the same failure back.
    unexpanded: tuple[tuple[str, str], ...] = ()

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


def _is_unexpanded(value: str) -> bool:
    """Whether a value is a variable reference nobody substituted."""
    return _UNEXPANDED.match(value) is not None


def _setting(src, key: str, default: str) -> str:
    """One environment setting, treating an empty or UNSUBSTITUTED one as absent.

    `FOO=` is not the same as `FOO` unset, and `os.environ.get(key, default)`
    cannot tell them apart — it returns the empty string, and the default never
    applies. An empty `TMODLOADER_DIR` therefore became `Path("")`, which is
    `Path(".")`, and `check` reported that the WORKING DIRECTORY held no
    tModLoader.dll: a true sentence about a directory nobody meant.

    `FOO=${FOO}` is the same absence wearing a value's clothes, and it is the
    one this server is actually handed: `.mcp.json` writes `${TMODLOADER_SAVE_DIR}`
    so that no checkout carries anybody's disk, and a client that was launched
    without the variable substitutes nothing and passes the text through. Taking
    it literally is what let a path that never arrived travel on as though it
    had — see `load`.

    THIS IS THE ONE PLACE EVERY VARIABLE IS READ, which is why the check lives
    here. Rejecting the placeholder per-variable would leave every variable
    nobody thought about still carrying it.
    """
    value = src.get(key, "").strip()
    return default if _is_unexpanded(value) else (value or default)


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

    A VALUE THAT IS STILL `${ITS_OWN_NAME}` COUNTS AS ABSENT, and is recorded
    separately so `check` can say which absence it is. This is the state a
    background client actually hands the server: `.mcp.json` deliberately writes
    the placeholder, the client expands it against its own environment, and one
    launched without the variable substitutes nothing. Read literally, the text
    is a non-empty string, so it was configuration as far as everything here
    could tell — `${TMODLOADER_WORLD_WIN}` would have reached tModLoader as a
    world to load, and come back as a readiness timeout naming the heartbeat.
    """
    src = os.environ if env is None else env

    # Every TMODLOADER_* name rather than the ones read below: a variable this
    # module does not know about still tells the reader their substitution is
    # broken, and that is the fact worth reporting.
    unexpanded = tuple(
        (name, value.strip())
        for name, value in sorted(src.items())
        if name.startswith("TMODLOADER_") and _is_unexpanded(value.strip())
    )

    tml = Path(_setting(src, "TMODLOADER_DIR", DEFAULT_TML))
    # Not defaulted, and not raised either. An unset variable becomes `Path(".")`
    # and its NAME is recorded, so `check` can list every missing one at once
    # instead of stopping at the first. The recorded name is what makes that
    # distinguishable from someone genuinely configuring the working directory.
    save = Path(_setting(src, "TMODLOADER_SAVE_DIR", "."))
    mod = Path(_setting(src, "TMODLOADER_MOD_SOURCE", "."))
    # A placeholder is a non-empty string, so it does not land here — which is
    # what we want: it is absent, but `unexpanded` says so with the instruction
    # that actually fixes it. The two lists never name the same variable twice.
    unset = tuple(name for name in REQUIRED if not src.get(name, "").strip())

    return Config(
        tml_dir=tml,
        save_dir=save,
        mod_source=mod,
        unset=unset,
        unexpanded=unexpanded,
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


class _ProbeWriteFailed(OSError):
    """The claim probe could not even WRITE into `save_dir`.

    Deliberately RAISED rather than returned. `_claim_support` is
    `lru_cache`d, and `lru_cache` only memoises a RETURN - never a raised
    exception - so raising here is what stops a full disk or a momentary
    antivirus lock on a `/mnt/c` path from being pinned as a permanent
    verdict about the directory's LINKING support, which is a different,
    much more stable, property. `check` catches this and reports it without
    it ever reaching the cache.
    """


def _cannot_claim(save_dir: str, reason: str) -> str:
    """The shared "this directory cannot isolate two sessions" message.

    Names the remedy, unlike its first draft: a reader who does not own the
    directory (a network share, a container mount) needs to be told to point
    `TMODLOADER_SAVE_DIR` elsewhere, not just informed that something failed.
    """
    return (
        f"{save_dir} cannot support an exclusive claim on the trigger file "
        f"({reason}). Two sessions sharing this directory would overwrite "
        "each other's requests, and this refuses rather than shipping that "
        "silently. Set TMODLOADER_SAVE_DIR to a directory on a filesystem "
        "that supports exclusive hard links."
    )


@lru_cache(maxsize=8)
def _claim_support(save_dir: str) -> str | None:
    """None if `save_dir` enforces exclusivity when linking, else why not.

    The harness takes the trigger by linking onto it, which is atomic AND
    refuses an occupied name - that refusal is what stops two sessions
    overwriting each other's requests. `os.link` is not available on every
    filesystem, so a directory that cannot provide it cannot provide isolation,
    and this says so rather than falling back to the write that loses requests.

    PROVES REFUSAL, NOT JUST PRESENCE. A probe that only checked whether
    `os.link` succeeded once would pass a filesystem whose `link` silently
    overwrites an occupied name - exactly the failure mode the harness
    depends on `link` NOT having. So this links onto a fresh name, then
    links onto that SAME name again and requires the second call to raise
    `FileExistsError`. A filesystem that allows the second link does not
    enforce exclusivity and is reported as unusable even though `os.link`
    "worked" both times.

    CACHED PER DIRECTORY, but the cached verdict is about LINKING ONLY.
    `check` runs from `_cfg` on every tool call, so an uncached probe would
    put two `/mnt/c` writes on the hot path for a question whose answer
    cannot change while the server runs - true of link support, but NOT of
    a failure to even write the probe file (a full disk, a momentary
    antivirus lock), which is transient in a way link support is not. That
    case is raised as `_ProbeWriteFailed` instead of returned, so it is
    never memoised: the next call tries again rather than replaying today's
    failure for the rest of the process.

    Keyed by `str` rather than `Path` so the cache key is hashable and stable.
    """
    stem = f".tmodloader-mcp-claimprobe-{os.getpid()}"
    source = Path(save_dir) / stem
    target = Path(save_dir) / f"{stem}.claim"
    try:
        source.write_text("probe")
    except OSError as failed:
        raise _ProbeWriteFailed(failed.errno, failed.strerror) from failed

    try:
        try:
            os.link(source, target)
        except FileExistsError:
            # The target already existed, which is the refusal being tested
            # for - so linking works here. A leftover from a killed process,
            # not a fault.
            return None
        except OSError as refused:
            return _cannot_claim(save_dir, refused.strerror or str(refused))

        # The target now exists because the link above created it. Linking
        # onto it a SECOND time is the actual property under test: a
        # filesystem whose `link` refuses only sometimes, or never, would
        # pass the first link and still be unsafe to depend on.
        try:
            os.link(source, target)
        except FileExistsError:
            return None
        except OSError as refused:
            return _cannot_claim(save_dir, refused.strerror or str(refused))
        else:
            return (
                f"{save_dir} lets `os.link` land on an occupied name instead "
                "of refusing it. Two sessions sharing this directory would "
                "overwrite each other's requests, and this refuses rather "
                "than shipping that silently. Set TMODLOADER_SAVE_DIR to a "
                "directory on a filesystem that supports exclusive hard "
                "links."
            )
    finally:
        # Both wrapped rather than left to `missing_ok=True` alone: that only
        # suppresses "already gone", not a live directory that stopped being
        # writable mid-probe. A `PermissionError` here must not replace the
        # diagnosis above with an unhandled exception from `finally`.
        with contextlib.suppress(OSError):
            target.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            source.unlink(missing_ok=True)


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
    #
    # A placeholder is the same disease with a different vector, and it got past
    # this guard for exactly one reason: `${TMODLOADER_SAVE_DIR}` is not empty,
    # so it was never `unset`. What the reader got instead was four problems, of
    # which two named TMODLOADER_MOD_NAME and TMODLOADER_MOD_SOURCE_WIN -
    # variables they had never set, derived from the one that never arrived.
    if cfg.unexpanded or cfg.unset:
        return [
            *_unexpanded_problem(cfg),
            *(
                f"{name} is not set, and has no default because every plausible "
                "value names somebody's own install"
                for name in cfg.unset
            ),
        ]

    if not cfg.tml_dir.is_dir():
        problems.append(f"no tModLoader install at {cfg.tml_dir} (set TMODLOADER_DIR)")
    elif not cfg.tml_dll.is_file():
        problems.append(f"{cfg.tml_dir} exists but holds no tModLoader.dll")

    if not cfg.save_dir.is_dir():
        problems.append(
            f"no save directory at {cfg.save_dir} (set TMODLOADER_SAVE_DIR)"
        )
    else:
        try:
            unclaimable = _claim_support(str(cfg.save_dir))
        except _ProbeWriteFailed as failed:
            # Not memoised - see `_claim_support` - so this is re-tried on
            # every call rather than repeating today's answer forever.
            problems.append(
                f"cannot write into {cfg.save_dir} to test whether it can "
                "support an exclusive claim on the trigger file "
                f"({failed.strerror}). This may be transient - a full disk, "
                "a momentary lock - so it is reported rather than assumed "
                "permanent; the next call tries again."
            )
        else:
            if unclaimable:
                problems.append(unclaimable)

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


def _unexpanded_problem(cfg: Config) -> list[str]:
    """The unsubstituted variables as ONE problem, or none.

    One entry however many variables there are, because the remedy is a single
    action and repeating it per variable rebuilds the wall of text this whole
    early return exists to avoid. The names still all appear: which ones failed
    to substitute is the part that differs, and the paragraph after it is not.
    """
    if not cfg.unexpanded:
        return []

    named = ", ".join(
        f"{name} is still the text {text}" for name, text in cfg.unexpanded
    )
    return [
        (
            f"{named}. Whatever started this server never substituted the "
            "reference, and nothing here can recover the value - it was gone "
            "before the process began. `.mcp.json` writes ${...} deliberately, "
            "so that no checkout carries anybody's disk, and the MCP CLIENT is "
            "what expands it, against its own environment. Export these where "
            "the client is launched rather than only in an interactive shell - "
            "a client started by a daemon or a desktop launcher inherits "
            "neither - and then restart the client."
        )
    ]


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
