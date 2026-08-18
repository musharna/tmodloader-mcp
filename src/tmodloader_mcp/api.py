"""Searching the tModLoader API surface without a browser or a guess.

WHY THIS EXISTS. Writing mod-side code here has meant answering two questions
over and over: does this member exist, and what does it take. Both have been
answered badly. "Does `Main.cloudAlpha` exist" was answered by GREPPING A 21MB
DLL FOR A SUBSTRING, which finds the name and cannot say what owns it, whether
it is a field or a method, or what type it is. "What does `QuickSpawnItem`
take" was answered by writing the call and letting the compiler decide - exact,
but only for code already written, and a build cycle per attempt.

Neither answers the question you have BEFORE writing anything: what is there.
`Main.maxRaining` is only findable if you already suspect the name.

WHAT THIS IS NOT: documentation. It is the assembly's own public surface, read
from its metadata, so it cannot drift from the version installed - which is the
failure mode of every wiki page and every model's recollection. It carries no
prose, because it is not trying to explain anything.

The index is built by `tools/ApiIndex`, which reads metadata WITHOUT loading or
running the assembly, and cached against the DLL it came from so a tModLoader
update invalidates it by construction rather than by anybody remembering.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Config

#: One line per member: `path`, `kind`, `type`. Tab separated because the paths
#: contain every other punctuation character a signature can hold.
_FIELDS = 3

#: What a caller may filter by, which is the second column of the index.
KINDS = ("type", "field", "property", "method")


class ApiError(RuntimeError):
    """The index cannot be built or read, and the message says what to do."""


@dataclass(frozen=True)
class Member:
    """One line of the index, split.

    `path` is what is searched and what a caller reads back — the fully
    qualified name, with a method's parameters attached. Namespaces are kept:
    `Terraria.Main` and a mod's own `Main` are different types, and an index
    that called both "Main" would confidently answer about the wrong one.
    """

    path: str
    kind: str
    type: str

    @property
    def name(self) -> str:
        """The short name, without the namespace or the parameter list.

        What a human means when they say "cloudAlpha". Used for ranking, so an
        exact hit on the thing somebody typed sorts above a signature that
        merely mentions it somewhere.
        """
        head = self.path.split("(", 1)[0]
        return head.rsplit(".", 1)[-1]


def parse(text: str) -> list[Member]:
    """Split an index into members, skipping anything malformed.

    Skipped rather than raised on: an index is thousands of lines produced by a
    separate program, and one unreadable line is not a reason to refuse every
    question about the other thirty-six thousand.
    """
    out: list[Member] = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) != _FIELDS:
            continue
        out.append(Member(path=parts[0], kind=parts[1], type=parts[2]))
    return out


def search(
    members: list[Member], query: str, *, kind: str | None = None, limit: int = 40
) -> list[Member]:
    """Members matching `query`, best first.

    RANKED, because an unranked substring search over 36,000 members answers
    "rain" with `slimeRainKillCount` before `raining` and buries the thing
    somebody asked for. The order is: the short name IS the query, then the
    short name contains it, then it appears anywhere else — a match inside a
    parameter list is a real match and a worse one.

    Case-insensitive throughout, matching every other filter here.
    """
    if not query or not query.strip():
        raise ValueError(
            "search needs something to look for. An empty query matches all "
            "36,000 members, which is the index rather than an answer"
        )

    if kind is not None and kind not in KINDS:
        raise ValueError(f"{kind!r} is not one of {', '.join(KINDS)}")

    needle = query.strip().lower()
    scored: list[tuple[int, int, str, Member]] = []

    for member in members:
        if kind is not None and member.kind != kind:
            continue

        name = member.name.lower()
        path = member.path.lower()

        if name == needle:
            rank = 0
        elif needle in name:
            rank = 1
        elif needle in path:
            rank = 2
        elif needle in member.type.lower():
            rank = 3
        else:
            continue

        # Shorter paths first within a rank: `Terraria.Main.raining` before
        # `Terraria.GameContent.Something.rainingHelper`, because the shallow
        # one is nearly always the one being asked about.
        scored.append((rank, len(member.path), member.path, member))

    scored.sort(key=lambda row: (row[0], row[1], row[2]))
    return [row[3] for row in scored[:limit]]


def index_path_for(cfg: Config) -> Path:
    """Where this install's index is cached, keyed by the DLL it describes.

    Size and mtime rather than a hash: the point is to notice a tModLoader
    update, and reading 21MB to answer a question about a cache is a cost paid
    on every call to avoid one paid on the rare occasion the game changes.

    Under the user's cache directory rather than in the install or the
    repository. The install is somebody else's, and a generated file of tens of
    thousands of lines does not belong in version control.
    """
    dll = cfg.tml_dll
    stamp = dll.stat() if dll.is_file() else None
    key = f"{stamp.st_size}-{int(stamp.st_mtime)}" if stamp else "absent"

    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "tmodloader-mcp" / f"api-{key}.txt"


def _dotnet() -> str:
    """The SDK used to build and run the indexer, or a refusal naming the need.

    NOT `cfg.dotnet`, which is tModLoader's own bundled runtime: that is a
    Windows executable for running the game, and this is a build of a small
    Linux console tool. Different tool, same word.
    """
    for candidate in ("dotnet", str(Path.home() / ".dotnet" / "dotnet")):
        found = shutil.which(candidate) or (
            candidate if Path(candidate).is_file() else None
        )
        if found:
            return found

    raise ApiError(
        "no dotnet SDK on PATH, and the API index is built by a small C# tool "
        "(tools/ApiIndex). Install the .NET 8 SDK, or answer the question with "
        "a build instead - the compiler is exact about signatures."
    )


def ensure_index(cfg: Config, *, timeout: float = 300.0) -> Path:
    """The cached index for this install, building it if it is not there.

    Built once per tModLoader version and then read from disk. The build is a
    `dotnet build` plus a metadata read of the game assembly, which is seconds
    rather than minutes, but it is not something to do on every question.
    """
    cached = index_path_for(cfg)
    if cached.is_file() and cached.stat().st_size > 0:
        return cached

    dll = cfg.tml_dll
    if not dll.is_file():
        raise ApiError(
            f"there is no tModLoader.dll at {dll}, so there is no API to index. "
            "Check TMODLOADER_DIR."
        )

    tool = Path(__file__).resolve().parent.parent.parent / "tools" / "ApiIndex"
    if not (tool / "ApiIndex.csproj").is_file():
        raise ApiError(f"the indexer is missing from {tool}")

    dotnet = _dotnet()
    cached.parent.mkdir(parents=True, exist_ok=True)

    built = tool / "bin" / "Debug" / "net8.0" / "ApiIndex.dll"
    if not built.is_file():
        _run(
            [dotnet, "build", str(tool), "-v", "q", "--nologo"],
            timeout=timeout,
            what="building the API indexer",
        )

    # INDEXED UNDER A STAGING NAME AND RENAMED, because the validity check
    # above is `is_file and size > 0` - which a file killed mid-write passes
    # forever. A SIGTERM landing during the one build this cache ever gets
    # (and `finally` does not run on SIGTERM here, measured) left a truncated
    # index that every later call served: `parse` skips the one torn line, so
    # api_search answered "not found" about every member in the missing tail -
    # the misleading absence this module exists to prevent, installed
    # permanently by the process meant to prevent it.
    staging = cached.with_name(f"{cached.name}.{os.getpid()}.partial")
    try:
        _run(
            [dotnet, str(built), str(dll), str(staging)],
            timeout=timeout,
            what="indexing the API",
        )

        if not staging.is_file() or staging.stat().st_size == 0:
            raise ApiError(
                f"the indexer reported success and wrote nothing to {staging}, "
                "so there is no index to search"
            )

        staging.replace(cached)
    finally:
        staging.unlink(missing_ok=True)

    return cached


def _run(command: list[str], *, timeout: float, what: str) -> None:
    """Run a step, and fail LOUD with what it actually said.

    `check=False` plus an explicit raise rather than `check=True`: a
    CalledProcessError carries the return code and hides the output, and the
    output is the only part anybody can act on.
    """
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        raise ApiError(f"{what} did not finish within {timeout:.0f}s") from None
    except OSError as broken:
        raise ApiError(f"{what} could not start: {broken}") from None

    if proc.returncode != 0:
        raise ApiError(
            f"{what} failed ({proc.returncode}):\n"
            f"{(proc.stdout + proc.stderr).strip()[-2000:]}"
        )
