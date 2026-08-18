"""Searching the API surface, and the ranking that makes it an answer.

The search is pure text in, members out, so almost all of this runs against a
fixture rather than against a 21MB game assembly. The one test that does use
the real thing is marked and skipped where there is no tModLoader — it is the
real-execution check, and it exists because a fixture index cannot disagree
with the indexer about what an index looks like.

RANKING IS THE POINT, not matching. An unranked substring search over 36,000
members answers "rain" with `slimeRainKillCount` first and buries `raining`
somewhere below the fold, which is a worse answer than none: it is confidently
about the wrong member.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tmodloader_mcp import api

#: A slice of the real index, in the real format. The lines are copied from a
#: run against tModLoader.dll rather than invented, so the format cannot be
#: wrong here and right there.
_QUICK_SPAWN = (
    "Terraria.Player.QuickSpawnItem(Terraria.DataStructures.IEntitySource source, "
    "System.Int32 item, System.Int32 stack)\tmethod\tSystem.Int32"
)

#: DELIBERATELY IN THE WRONG ORDER. Two of the ranking tests below passed
#: against a version of `search` that did no sorting at all, because this tuple
#: happened to list the right answer first and the fixture was doing the work.
#: Every entry a ranking test cares about is now listed AFTER something that
#: would beat it on insertion order, so a search that returns the input order
#: returns the wrong answer.
_LINES = (
    "Terraria.Main.oldMaxRaining\tfield\tSystem.Single",
    "Terraria.Main.maxRaining\tfield\tSystem.Single",
    "Terraria.Main.slimeRainKillCount\tfield\tSystem.Int32",
    "Terraria.Main.raining\tfield\tSystem.Boolean",
    "Terraria.Main\ttype\tclass",
    "Terraria.Main.maxRain\tfield\tSystem.Int32",
    "Terraria.Main.cloudAlpha\tfield\tSystem.Single",
    "Terraria.Main.StopRain()\tmethod\tSystem.Void",
    _QUICK_SPAWN,
    # THE PAIR THAT PINS RANK ABOVE DEPTH. `Happening` is this member's exact
    # short name and its path is the LONGEST here; `HappeningSoon` merely
    # contains it and sits on a much shorter path. A search for "Happening"
    # that ranked by path length would answer with the shorter, wrong one - and
    # that is precisely the version the first draft of these tests could not
    # tell apart from the right one.
    "Terraria.Main.HappeningSoon\tfield\tSystem.Boolean",
    "Terraria.GameContent.Events.Sandstorm.Happening\tfield\tSystem.Boolean",
    "malformed line with no tabs",
    "",
)

INDEX = "\n".join(_LINES)


@pytest.fixture
def members() -> list[api.Member]:
    return api.parse(INDEX)


def test_a_malformed_line_is_skipped_rather_than_fatal(members):
    """An index is thousands of lines from a separate program. One unreadable
    line is not a reason to refuse every question about the rest."""
    # Derived from the fixture rather than written as a number, so adding a
    # line for some other test does not turn this into a failure about
    # arithmetic. It went 10 -> 11 exactly that way once.
    well_formed = sum(1 for line in _LINES if line.count("\t") == 2)

    assert len(members) == well_formed
    assert well_formed < len(_LINES), "the fixture has no malformed line to skip"
    assert all(m.path for m in members)


def test_the_short_name_is_what_a_human_would_type(members):
    """Ranking is built on it, so it has to survive both a namespace and a
    parameter list."""
    by_path = {m.path: m for m in members}

    assert by_path["Terraria.Main.cloudAlpha"].name == "cloudAlpha"
    assert by_path["Terraria.Main.StopRain()"].name == "StopRain"
    assert by_path[_QUICK_SPAWN.split("\t")[0]].name == "QuickSpawnItem"


def test_an_exact_name_wins_over_everything_that_merely_contains_it(members):
    """THE RANKING TEST. `raining` is a real field and also a substring of
    `maxRaining` and `oldMaxRaining`. Unranked, the answer depends on index
    order, which is the assembly's business and not the caller's."""
    found = api.search(members, "raining")

    assert found[0].path == "Terraria.Main.raining", (
        f"the exact field was not first: {[m.path for m in found[:4]]}"
    )


def test_an_exact_name_beats_a_shorter_path_that_merely_contains_it(members):
    """RANK ABOVE DEPTH, and the test above cannot tell the difference.

    `raining` is both the exact match AND the shortest path, so it comes first
    under either rule - which is why disabling the exact-match rank entirely
    left that test green. Here the exact match sits on the LONGEST path in the
    fixture and the substring match on a much shorter one, so only the intended
    order produces the intended answer.
    """
    found = api.search(members, "Happening")

    assert found[0].path == "Terraria.GameContent.Events.Sandstorm.Happening", (
        "a shorter path beat an exact name, so depth is being weighed above "
        f"rank: {[m.path for m in found[:3]]}"
    )
    assert found[1].path == "Terraria.Main.HappeningSoon"


def test_a_search_that_matches_nothing_exactly_still_finds_the_family(members):
    """ "rain" is nobody's exact name and is the obvious thing to type when you
    do not know what you are looking for - which is the case this exists for."""
    found = api.search(members, "rain")
    paths = [m.path for m in found]

    assert "Terraria.Main.raining" in paths
    assert "Terraria.Main.StopRain()" in paths
    assert "Terraria.Main.slimeRainKillCount" in paths


def test_a_shallower_path_sorts_before_a_deeper_one_at_the_same_rank(members):
    """`Terraria.Main.X` before `Terraria.GameContent.Events.Y`. The shallow
    one is nearly always the one being asked about, and length is the only
    signal available without knowing the game."""
    found = api.search(members, "System.Boolean")
    paths = [m.path for m in found]

    assert paths.index("Terraria.Main.raining") < paths.index(
        "Terraria.GameContent.Events.Sandstorm.Happening"
    )


def test_matching_the_type_finds_members_by_what_they_hold(members):
    """ "which fields are floats" is a real question when you are about to
    assign one, and it is answerable from the third column."""
    found = api.search(members, "System.Single")

    assert {m.path for m in found} >= {
        "Terraria.Main.maxRaining",
        "Terraria.Main.cloudAlpha",
    }


def test_a_kind_filter_narrows_to_one_column(members):
    """ "is StopRain a method or a field" is answered by asking for one."""
    methods = api.search(members, "rain", kind="method")

    assert [m.path for m in methods] == ["Terraria.Main.StopRain()"]

    # POSITIVE CONTROL, same test: without the filter the fields come back too,
    # so the filter is narrowing rather than the search being broken.
    assert len(api.search(members, "rain")) > 1


def test_an_unknown_kind_is_refused_rather_than_matching_nothing(members):
    """Silently returning nothing for `kind="fields"` reads exactly like an API
    that does not have the thing you asked about."""
    with pytest.raises(ValueError, match="field"):
        api.search(members, "rain", kind="fields")


def test_an_empty_query_is_refused(members):
    """It matches all thirty-six thousand members, which is the index rather
    than an answer."""
    for query in ("", "   "):
        with pytest.raises(ValueError, match="something to look for"):
            api.search(members, query)


def test_the_limit_is_honoured(members):
    assert len(api.search(members, "rain", limit=2)) == 2


def test_the_search_is_case_insensitive_like_every_other_filter(members):
    assert api.search(members, "CLOUDALPHA")[0].path == "Terraria.Main.cloudAlpha"


# ---- the cache key ---------------------------------------------------------


class _Cfg:
    def __init__(self, dll: Path) -> None:
        self.tml_dll = dll


def test_the_cache_is_keyed_to_the_assembly_it_describes(tmp_path, monkeypatch):
    """A tModLoader update must invalidate the index BY CONSTRUCTION.

    An index cached under a fixed name would answer questions about the version
    you used to have, and would keep doing it forever - a confident answer of
    the right shape about the wrong game, which is the failure mode this whole
    repository keeps paying for.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    dll = tmp_path / "tModLoader.dll"
    dll.write_bytes(b"pretend this is an assembly")
    before = api.index_path_for(_Cfg(dll))

    # A new version: different size, and a different mtime.
    dll.write_bytes(b"pretend this is a NEWER assembly, and longer")
    after = api.index_path_for(_Cfg(dll))

    assert before != after, (
        "the cache key did not move when the assembly did, so an update would "
        "be answered out of the old index forever"
    )


def test_a_missing_assembly_does_not_crash_the_cache_key(tmp_path, monkeypatch):
    """`ensure_index` refuses a missing DLL with a sentence naming the setting.
    Computing where the cache WOULD be must not throw before it gets there."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    path = api.index_path_for(_Cfg(tmp_path / "nothing-here.dll"))
    assert path.name.endswith(".txt")

    with pytest.raises(api.ApiError, match="TMODLOADER_DIR"):
        api.ensure_index(_Cfg(tmp_path / "nothing-here.dll"))


# ---- the real thing, once --------------------------------------------------


def _real_install() -> Path | None:
    """The installed tModLoader.dll, or None on a machine without one.

    Blind `except Exception` on purpose, and narrowly scoped: this is a SKIP
    CONDITION, evaluated at collection time on machines that may have no
    configuration at all. Anything `config.load` can raise here means the same
    thing - there is no install to test against - and letting one of them
    escape would fail collection for the whole file rather than skip one test.
    """
    from tmodloader_mcp import config

    try:
        cfg = config.load()
    except Exception:  # noqa: BLE001 - a skip condition, see above
        return None
    return cfg.tml_dll if cfg.tml_dll.is_file() else None


def _has_dotnet() -> bool:
    """Asked the way the CODE asks it, not the way `shutil.which` does.

    This skipped on the machine that has a working SDK, because the SDK lives
    at ~/.dotnet/dotnet and is not on PATH — so the only test here that runs
    the real indexer was silently not running. A skip condition stricter than
    the requirement it guards is worse than no test: it reads green.
    """
    try:
        api._dotnet()
    except api.ApiError:
        return False
    return True


@pytest.mark.skipif(
    _real_install() is None or not _has_dotnet(),
    reason="needs a tModLoader install and a dotnet SDK",
)
def test_the_index_built_from_the_real_assembly_answers_a_real_question():
    """THE REAL-EXECUTION CHECK. Every test above runs on a fixture, and a
    fixture cannot disagree with the indexer about what an index looks like.

    The questions are the ones this session actually had while writing
    `DevMutations`, answered then by grepping a 21MB DLL for substrings.
    """
    from tmodloader_mcp import config

    cfg = config.load()
    index = api.ensure_index(cfg)
    members = api.parse(index.read_text())

    assert len(members) > 10_000, f"only {len(members)} members - the index is thin"

    top = api.search(members, "cloudAlpha")
    assert top and top[0].path == "Terraria.Main.cloudAlpha"
    assert top[0].kind == "field"

    spawn = api.search(members, "QuickSpawnItem", kind="method")
    assert spawn, "the method every `give` call goes through is not in the index"
    assert "IEntitySource" in spawn[0].path, (
        f"the signature carries no parameters: {spawn[0].path}"
    )


# ---- the cache write is atomic ----------------------------------------------


class _StagedCfg:
    def __init__(self, root: Path):
        self.tml_dll = root / "tModLoader.dll"


def test_a_killed_indexer_cannot_install_a_truncated_cache(tmp_path, monkeypatch):
    """The cache's validity check is `is_file and size > 0`, which a file
    killed mid-write passes forever - and `parse` skips only the torn line,
    so `api_search` answered "not found" about every member in the missing
    tail: the misleading absence this module exists to prevent, installed
    permanently. The index is therefore written to a staging name and
    renamed; a build that dies leaves NOTHING at the cached path."""
    root = tmp_path / "install"
    root.mkdir()
    (root / "tModLoader.dll").write_bytes(b"not really a dll")
    cfg = _StagedCfg(root)

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(api, "_dotnet", lambda: "/fake/dotnet")

    tool = Path(api.__file__).resolve().parent.parent.parent / "tools" / "ApiIndex"
    built = tool / "bin" / "Debug" / "net8.0" / "ApiIndex.dll"

    def dies_mid_write(command, *, timeout, what):
        if what == "building the API indexer":
            return
        # The indexer writes half its output and is killed - `finally` does
        # not run on SIGTERM, which is how the truncated file survives.
        Path(command[-1]).write_text("Terraria.Main\ttype\tclass\n[torn")
        raise api.ApiError(f"{what} was killed mid-write")

    monkeypatch.setattr(api, "_run", dies_mid_write)
    if built.is_file():
        # Force the build step to be "already built" or not - either way the
        # index step below is the one that dies.
        pass

    cached = api.index_path_for(cfg)
    with pytest.raises(api.ApiError, match="killed mid-write"):
        api.ensure_index(cfg)

    assert not cached.exists(), "a torn index landed at the cached path"
    assert not list(cached.parent.glob("*.partial")), "the staging file leaked"


def test_a_successful_index_lands_whole_at_the_cached_path(tmp_path, monkeypatch):
    """POSITIVE CONTROL: the staging dance is invisible when the indexer
    finishes."""
    root = tmp_path / "install"
    root.mkdir()
    (root / "tModLoader.dll").write_bytes(b"not really a dll")
    cfg = _StagedCfg(root)

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(api, "_dotnet", lambda: "/fake/dotnet")

    def writes_everything(command, *, timeout, what):
        if what == "indexing the API":
            Path(command[-1]).write_text("Terraria.Main\ttype\tclass\n")

    monkeypatch.setattr(api, "_run", writes_everything)

    cached = api.ensure_index(cfg)

    assert cached.read_text() == "Terraria.Main\ttype\tclass\n"
    assert not list(cached.parent.glob("*.partial"))
