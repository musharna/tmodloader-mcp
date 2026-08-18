"""Resolving configuration, and catching the ways it can be quietly wrong.

The expensive class here is not a missing path — that is loud. It is a config
that RESOLVES, passes every check, and drives the wrong thing.
"""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

from tmodloader_mcp import config

# Captured from real `wslpath -w` output on WSL2, because CI has no wslpath and
# a translation this file invented for itself would be checked against nothing.
#
#   /mnt/c/Users/someone/.../Biomancy  ->  C:\Users\someone\...\Biomancy
#   /mnt/c/Program Files (x86)/Steam ->  C:\Program Files (x86)\Steam
#   /home/someone                   ->  \\wsl.localhost\Ubuntu\home\mjarnold
REAL_TRANSLATIONS = [
    ("/mnt/c/Program Files (x86)/Steam", r"C:\Program Files (x86)\Steam"),
    (
        "/mnt/c/Users/someone/Documents/My Games/Terraria/tModLoader/ModSources/Biomancy",
        (
            r"C:\Users\someone\Documents\My Games\Terraria\tModLoader"
            r"\ModSources\Biomancy"
        ),
    ),
    ("/mnt/d/Mods/Yours", r"D:\Mods\Yours"),
]


@pytest.mark.parametrize(("wsl", "win"), REAL_TRANSLATIONS)
def test_a_drive_path_translates_the_way_wslpath_does(wsl, win):
    assert config.windows_path_for(Path(wsl)) == win


@pytest.mark.parametrize("wsl", ["/home/someone", "/mnt/wsl", "/", "relative/path"])
def test_a_path_with_no_drive_letter_is_not_guessed_at(wsl):
    """`wslpath` answers these with a \\\\wsl.localhost\\ UNC, and whether
    tModLoader can build from one is not something this repo has established.

    Returning None says "I do not know" and lets `check` ask for the variable.
    Inventing a UNC path here would be asserting an answer nobody measured.
    """
    assert config.windows_path_for(Path(wsl)) is None


def test_the_windows_mod_source_follows_the_wsl_one():
    """THE ONE THAT BUILDS SOMEBODY ELSE'S MOD.

    `TMODLOADER_MOD_SOURCE` and `TMODLOADER_MOD_SOURCE_WIN` name ONE directory.
    They used to default independently, so overriding the first and not the
    second left the Windows path pointing at this machine's Biomancy: every
    other tool drove the caller's mod while `build_mod` compiled Biomancy, and
    the build reported success because it HAD succeeded — at the wrong thing.
    """
    cfg = config.load({"TMODLOADER_MOD_SOURCE": "/mnt/c/Mods/Yours"})

    assert cfg.mod_source == Path("/mnt/c/Mods/Yours")
    assert cfg.mod_source_win == r"C:\Mods\Yours"
    assert "Biomancy" not in (cfg.mod_source_win or "")


def test_an_explicit_windows_path_still_wins():
    """Positive control for the derivation: a caller who sets it is obeyed.

    Without this, a `load` that ignored the variable entirely would satisfy the
    test above and break every install whose source is not on a drive letter.
    """
    cfg = config.load(
        {
            "TMODLOADER_MOD_SOURCE": "/home/somebody/MyMod",
            "TMODLOADER_MOD_SOURCE_WIN": r"\\wsl.localhost\Ubuntu\home\somebody\MyMod",
        }
    )

    assert cfg.mod_source_win == r"\\wsl.localhost\Ubuntu\home\somebody\MyMod"


def test_a_windows_path_that_names_a_different_directory_is_reported(tmp_path):
    """Set both, disagreeing — the mismatch derivation cannot remove.

    Deriving stops the common case (override one, inherit the other). It cannot
    stop a caller from setting both to different places, so that is checked
    rather than assumed away.
    """
    source = tmp_path / "MyMod"
    source.mkdir()
    (source / "build.txt").write_text("version = 1.0\n")

    cfg = config.load(
        {
            "TMODLOADER_DIR": str(tmp_path),
            "TMODLOADER_SAVE_DIR": str(tmp_path),
            "TMODLOADER_MOD_SOURCE": f"/mnt/c/{source.relative_to(source.anchor)}",
            "TMODLOADER_MOD_SOURCE_WIN": r"C:\Somewhere\Else",
        }
    )

    problems = config.check(cfg)
    assert any("Somewhere\\Else" in p for p in problems), problems
    assert any("TMODLOADER_MOD_SOURCE_WIN" in p for p in problems), problems


def test_a_source_that_cannot_be_translated_asks_for_the_variable(tmp_path):
    source = tmp_path / "MyMod"
    source.mkdir()
    (source / "build.txt").write_text("version = 1.0\n")

    cfg = config.load(
        {
            "TMODLOADER_DIR": str(tmp_path),
            "TMODLOADER_SAVE_DIR": str(tmp_path),
            "TMODLOADER_MOD_SOURCE": str(source),
        }
    )

    assert cfg.mod_source_win is None
    assert any("TMODLOADER_MOD_SOURCE_WIN" in p for p in config.check(cfg))


def test_an_exported_but_empty_variable_falls_back_to_the_default():
    """`FOO=` is not `FOO` unset, and `os.environ.get(k, default)` cannot tell.

    An empty `TMODLOADER_DIR` resolved to `Path("")`, which is `Path(".")` — the
    working directory. `check` then found a directory that exists and reported
    "<cwd> exists but holds no tModLoader.dll", sending the reader to look at
    entirely the wrong place.
    """
    cfg = config.load({"TMODLOADER_DIR": "", "TMODLOADER_SAVE_DIR": "   "})

    assert cfg.tml_dir == Path(config.DEFAULT_TML)
    # SAVE_DIR has no default any more - every plausible one named a person -
    # so a whitespace-only value is UNSET rather than falling back. Same rule,
    # different landing place: the variable is still not treated as configured.
    assert "TMODLOADER_SAVE_DIR" in cfg.unset


def windows_tools(tmp_path) -> dict[str, str]:
    """Stand-ins for Windows' own process tools, and the env naming them.

    `check` now verifies these exist, because the platform assumption used to
    be invisible: every default lives under `/mnt/c`, and a native Linux or
    macOS install failed much later with an OSError naming a path in System32.

    Every positive control here needs them, and they cannot be the real ones -
    CI runs on ubuntu, where `/mnt/c/Windows` does not exist, so a test relying
    on the defaults would pass on the author's machine and fail on the runner.
    """
    tools = tmp_path / "winbin"
    tools.mkdir(exist_ok=True)
    made = {}
    for variable, name in (
        ("TMODLOADER_TASKKILL", "taskkill.exe"),
        ("TMODLOADER_TASKLIST", "tasklist.exe"),
        ("TMODLOADER_POWERSHELL", "powershell.exe"),
    ):
        path = tools / name
        path.write_bytes(b"")
        made[variable] = str(path)
    return made


def test_a_usable_config_reports_nothing(tmp_path):
    """Positive control for `check` itself.

    Every test above asserts that some problem IS reported. A `check` that
    returned a complaint unconditionally would pass all of them.
    """
    (tmp_path / "tModLoader.dll").write_text("")
    source = tmp_path / "MyMod"
    source.mkdir()
    (source / "build.txt").write_text("version = 1.0\n")

    cfg = config.load(
        {
            "TMODLOADER_DIR": str(tmp_path),
            "TMODLOADER_SAVE_DIR": str(tmp_path),
            "TMODLOADER_MOD_SOURCE": str(source),
            "TMODLOADER_MOD_SOURCE_WIN": r"C:\Mods\MyMod",
            **windows_tools(tmp_path),
        }
    )

    assert config.check(cfg) == []


# --- the defaults that named a person ---------------------------------------


def test_the_required_variables_have_no_default_at_all():
    """Unset is reported as unset, not resolved to somewhere plausible.

    A default pointing at the author's disk does not fail on yours — it
    resolves. Best case `check` complains about a directory you never
    mentioned; worst case it EXISTS and the server drives an install you did
    not choose.
    """
    cfg = config.load({})

    assert set(cfg.unset) == set(config.REQUIRED)
    assert "TMODLOADER_SAVE_DIR" in cfg.unset
    assert "TMODLOADER_MOD_SOURCE" in cfg.unset


def test_unset_variables_are_reported_alone():
    """Otherwise the one message that says what to do is buried.

    An unset variable resolves to `Path(".")`, so every later check fires too
    and complains about the working directory — several true sentences about a
    directory nobody meant.
    """
    problems = config.check(config.load({}))

    assert len(problems) == len(config.REQUIRED), problems
    assert all("is not set" in p for p in problems), problems
    assert not any(str(Path.cwd()) in p for p in problems), problems


def test_a_configured_install_still_passes(tmp_path):
    """POSITIVE CONTROL. Without it a `check` that rejected everything passes."""
    tml = tmp_path / "tml"
    tml.mkdir()
    (tml / "tModLoader.dll").write_bytes(b"")
    source = tmp_path / "Mymod"
    source.mkdir()
    # build.txt is what makes a directory a tModLoader mod source at all.
    (source / "build.txt").write_text("displayName = Mymod\n")

    cfg = config.load(
        {
            "TMODLOADER_DIR": str(tml),
            "TMODLOADER_SAVE_DIR": str(tmp_path),
            "TMODLOADER_MOD_SOURCE": str(source),
            "TMODLOADER_MOD_SOURCE_WIN": r"C:\Mymod",
            **windows_tools(tmp_path),
        }
    )

    assert cfg.unset == ()
    assert config.check(cfg) == []


# --- the variable that arrives as its own name ------------------------------


PLACEHOLDER_ENV = {
    "TMODLOADER_SAVE_DIR": "${TMODLOADER_SAVE_DIR}",
    "TMODLOADER_MOD_SOURCE": "${TMODLOADER_MOD_SOURCE}",
}


def test_a_placeholder_is_absence_rather_than_a_path():
    """`${FOO}` is a substitution that did not happen, not a value.

    `.mcp.json` writes the placeholder on purpose, so that no checkout carries
    anybody's disk, and the CLIENT expands it against its own environment. A
    client started by a daemon or a desktop launcher inherits no interactive
    shell, substitutes nothing, and passes the text through — which is how this
    server is handed `${TMODLOADER_SAVE_DIR}` as though someone had chosen it.
    """
    cfg = config.load(PLACEHOLDER_ENV)

    assert cfg.unexpanded == (
        ("TMODLOADER_MOD_SOURCE", "${TMODLOADER_MOD_SOURCE}"),
        ("TMODLOADER_SAVE_DIR", "${TMODLOADER_SAVE_DIR}"),
    )
    # Not ALSO unset: one absence, reported once, by the list whose message
    # says the thing that fixes it.
    assert cfg.unset == ()


def test_a_placeholder_does_not_blame_the_variables_it_derives():
    """REGRESSION. Four problems, two naming variables nobody had set.

    A placeholder is a non-empty string, so it was never `unset` and the guard
    that exists to report absence ALONE never fired. `mod_name` is derived from
    the mod source and `mod_source_win` is translated from it, so both inherited
    the placeholder and complained under their OWN names — sending the reader to
    set `TMODLOADER_MOD_NAME` and `TMODLOADER_MOD_SOURCE_WIN`, neither of which
    was wrong and neither of which would have helped.
    """
    problems = config.check(config.load(PLACEHOLDER_ENV))

    # One problem, not one per variable: the remedy is a single action, and
    # repeating it twice rebuilds the wall of text the early return is for.
    assert len(problems) == 1, problems
    assert not any("TMODLOADER_MOD_NAME" in p for p in problems), problems
    assert not any("TMODLOADER_MOD_SOURCE_WIN" in p for p in problems), problems
    # Both names still appear - which ones failed is the part that differs.
    assert "TMODLOADER_SAVE_DIR" in problems[0], problems
    assert "TMODLOADER_MOD_SOURCE" in problems[0], problems
    # And it must not read as "you forgot to export it", which is what the
    # reader has already done. The distinguishing instruction is the restart.
    assert "restart the client" in problems[0], problems


def test_a_placeholder_never_reaches_the_game_as_a_world():
    """The expensive half. The message is cosmetic next to this.

    `world_win` is passed to tModLoader verbatim, and a world that does not
    exist does not fail loudly — the server never finishes loading and the only
    symptom is a readiness timeout blaming the heartbeat. Treating the
    placeholder as absent is what makes `launch` refuse and list the real
    worlds instead.
    """
    cfg = config.load(
        {**PLACEHOLDER_ENV, "TMODLOADER_WORLD_WIN": "${TMODLOADER_WORLD_WIN}"}
    )

    assert cfg.world_win is None


def test_a_placeholder_falls_back_where_there_is_a_default():
    """Absent means absent, so the non-personal defaults still apply."""
    cfg = config.load({**PLACEHOLDER_ENV, "TMODLOADER_DIR": "${TMODLOADER_DIR}"})

    assert cfg.tml_dir == Path(config.DEFAULT_TML)


def test_a_real_path_that_merely_contains_a_dollar_is_left_alone(tmp_path):
    """POSITIVE CONTROL. A rule this broad could eat legitimate directories.

    Only a WHOLE value that is nothing but one reference counts. A directory
    genuinely named with a `$` in it is a path somebody chose, and refusing it
    would be this check inventing a problem of its own.
    """
    tml = tmp_path / "tml"
    tml.mkdir()
    (tml / "tModLoader.dll").write_bytes(b"")
    source = tmp_path / "Cash$Mod"
    source.mkdir()
    (source / "build.txt").write_text("displayName = CashMod\n")

    cfg = config.load(
        {
            "TMODLOADER_DIR": str(tml),
            "TMODLOADER_SAVE_DIR": str(tmp_path),
            "TMODLOADER_MOD_SOURCE": str(source),
            "TMODLOADER_MOD_SOURCE_WIN": r"C:\Cash$Mod",
            "TMODLOADER_MOD_NAME": "CashMod",
            **windows_tools(tmp_path),
        }
    )

    assert cfg.unexpanded == ()
    assert cfg.mod_source == source
    assert config.check(cfg) == []


def test_no_world_is_configured_by_default():
    """It used to be one developer's self-test world, by full path.

    On any other machine that named a file which does not exist, and the
    failure arrived as a readiness timeout blaming the heartbeat.
    """
    assert config.load({}).world_win is None


def test_no_default_points_inside_somebody_s_home_directory():
    """GUARDS THE CLASS, not the three constants that were removed.

    Checking for the specific username that used to be here would pass the
    moment a different one appeared — and would put that username back into the
    repository in order to do it. This asserts on the SHAPE instead: a default
    under `/Users/` or `/home/` is per-account by construction, whoever the
    account belongs to.
    """
    defaults = {
        name: value
        for name, value in vars(config).items()
        if name.startswith("DEFAULT_") and isinstance(value, str)
    }

    # Positive control: a scan that finds nothing to check proves nothing.
    assert defaults, "no DEFAULT_* constants found - this test stopped looking"

    personal = {
        name: value
        for name, value in defaults.items()
        if "/Users/" in value or "/home/" in value
    }
    assert not personal, f"per-account default(s): {personal}"


def test_a_save_directory_that_cannot_claim_is_reported(tmp_path, monkeypatch):
    """A SILENT FALLBACK WOULD REBUILD THE DEFECT UNDER A NICER NAME.

    The claim's exclusivity is `os.link`'s, and `os.link` is not available on
    every filesystem. Falling back to `os.replace` there would mean two
    sessions silently overwriting each other again - and the developer would
    meet it months later as a lost request rather than today as a message.

    Forced to fail rather than found failing: no filesystem is guaranteed to
    refuse links on any given machine, so a test that waited to find one would
    pass everywhere by not running.
    """
    config._claim_support.cache_clear()

    def refuses(src, dst):
        raise OSError(errno.EPERM, "operation not permitted")

    monkeypatch.setattr(config.os, "link", refuses)

    problem = config._claim_support(str(tmp_path))

    assert problem is not None, "an unusable save directory was reported as fine"
    assert "operation not permitted" in problem, (
        f"does not say why it cannot claim: {problem}"
    )


def test_a_save_directory_that_can_claim_is_silent(tmp_path):
    """POSITIVE CONTROL. A probe that reported EVERY directory unusable would
    satisfy the test above and stop the server starting anywhere at all."""
    config._claim_support.cache_clear()

    assert config._claim_support(str(tmp_path)) is None


def test_the_claim_probe_leaves_nothing_behind(tmp_path):
    """It writes into the SAVE DIRECTORY - the one holding the user's worlds
    and characters. Two files per call would accumulate there forever, and
    `check` runs on every tool call."""
    config._claim_support.cache_clear()
    config._claim_support(str(tmp_path))

    assert list(tmp_path.iterdir()) == [], (
        f"probe litter left in the save directory: {list(tmp_path.iterdir())}"
    )


def test_the_claim_probe_is_not_repeated_on_every_call(tmp_path, monkeypatch):
    """`check` runs from `_cfg` on EVERY tool call (server.py:68-73), so an
    uncached probe would put two /mnt/c writes on the hot path to answer a
    question whose answer cannot change while the server runs.

    Asserted as "no MORE calls after the first", not a fixed total: a single
    probe now links twice on purpose (see
    test_a_save_directory_whose_link_never_refuses_is_reported), so the
    count that must not grow is whatever the first call cost, not a
    hardcoded 1.
    """
    config._claim_support.cache_clear()
    calls: list[tuple] = []
    real = config.os.link

    def counting(src, dst):
        calls.append((src, dst))
        return real(src, dst)

    monkeypatch.setattr(config.os, "link", counting)

    config._claim_support(str(tmp_path))
    after_first_call = len(calls)
    config._claim_support(str(tmp_path))
    config._claim_support(str(tmp_path))

    assert after_first_call > 0, "the probe never linked at all"
    assert len(calls) == after_first_call, (
        f"probed {len(calls) - after_first_call} more time(s) after the "
        "first call for one directory"
    )


def test_a_save_directory_whose_link_never_refuses_is_reported(tmp_path, monkeypatch):
    """A FILESYSTEM WHOSE `os.link` SILENTLY OVERWRITES AN OCCUPIED NAME PASSES
    A PROBE THAT ONLY CHECKS LINKING EXISTS - precisely the failure mode the
    harness depends on `link` NOT having: the mod claims the trigger by
    linking onto it, and the refusal on an occupied name is the entire
    mechanism stopping two sessions from overwriting each other's requests.

    A permissive fake that always "succeeds" therefore has to be reported
    unusable, not silent, even though `os.link` never raised.
    """
    config._claim_support.cache_clear()

    def permissive(src, dst):
        Path(dst).write_text("clobbered")

    monkeypatch.setattr(config.os, "link", permissive)

    problem = config._claim_support(str(tmp_path))

    assert problem is not None, (
        "a filesystem whose os.link does not refuse an occupied name was "
        "reported as fine"
    )


def test_a_probe_cleanup_failure_does_not_discard_the_diagnosis(tmp_path, monkeypatch):
    """REPRODUCES A LEFTOVER PROBE FILE FROM A KILLED PROCESS PLUS A SAVE
    DIRECTORY THAT STOPS BEING WRITABLE MID-PROBE: `write_text` succeeds,
    `os.link` fails and returns a diagnosis, and then cleanup's OWN `unlink`
    fails too. The unlink failure must not replace the diagnosis with a raw
    exception - `_cfg` only turns a `RuntimeError` from `check` into a
    readable message, so anything else would reach the caller as a traceback
    instead of "configuration is unusable".
    """
    config._claim_support.cache_clear()

    def refuses_link(src, dst):
        raise OSError(errno.EACCES, "permission denied")

    def refuses_unlink(self, missing_ok=False):
        raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr(config.os, "link", refuses_link)
    monkeypatch.setattr(Path, "unlink", refuses_unlink)

    problem = config._claim_support(str(tmp_path))

    assert problem is not None, "the diagnosis was discarded"
    assert "permission denied" in problem


def test_a_write_failure_is_not_cached_as_a_permanent_verdict(tmp_path, monkeypatch):
    """ENOSPC, OR A MOMENTARY ANTIVIRUS LOCK ON A `/mnt/c` PATH, IS TRANSIENT
    IN A WAY LINK SUPPORT IS NOT. Caching it would pin "cannot support an
    exclusive claim" for the life of the process even after the disk has
    space again, so a write failure must be reported without being
    memoised: the very next call has to retry rather than replay today's
    failure.
    """
    config._claim_support.cache_clear()

    attempts = 0
    real_write_text = Path.write_text

    def flaky_write(self, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError(errno.ENOSPC, "no space left on device")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", flaky_write)

    with pytest.raises(OSError):
        config._claim_support(str(tmp_path))

    assert config._claim_support(str(tmp_path)) is None, (
        "the second call did not retry - a transient write failure was "
        "cached as a permanent verdict"
    )
    assert attempts == 2, f"the write was only attempted {attempts} time(s)"


def test_the_claim_probe_is_keyed_per_directory(tmp_path, monkeypatch):
    """A GLOBAL PROBE-ONCE-EVER MEMO WOULD SATISFY THE EXISTING CACHE TEST BY
    ACCIDENT: it only ever calls `_claim_support` with one directory, so a
    cache keyed on nothing (rather than on `save_dir`) would still show
    `os.link` called exactly once. This proves a SECOND, DIFFERENT directory
    is probed independently rather than reusing the first directory's
    verdict.
    """
    config._claim_support.cache_clear()
    other = tmp_path / "other"
    other.mkdir()
    calls: list[tuple] = []
    real = config.os.link

    def counting(src, dst):
        calls.append((src, dst))
        return real(src, dst)

    monkeypatch.setattr(config.os, "link", counting)

    config._claim_support(str(tmp_path))
    after_first_dir = len(calls)
    config._claim_support(str(other))

    assert len(calls) > after_first_dir, (
        "a second, different directory was not probed at all - the cache is "
        "keyed globally rather than per directory"
    )


def test_the_unclaimable_message_names_a_remedy(tmp_path, monkeypatch):
    """UNLIKE ITS SIBLINGS ("...(set TMODLOADER_DIR)"), the message this
    reports named only the problem, not what a reader could actually do
    about it."""
    config._claim_support.cache_clear()

    def refuses(src, dst):
        raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr(config.os, "link", refuses)

    problem = config._claim_support(str(tmp_path))

    assert "TMODLOADER_SAVE_DIR" in problem, f"names no remedy: {problem}"


# --- the platform, which used to be assumed silently ------------------------


def _install(tmp_path) -> dict[str, str]:
    """A configuration that is fine except for whatever a test then breaks."""
    tml = tmp_path / "tml"
    tml.mkdir(exist_ok=True)
    (tml / "tModLoader.dll").write_bytes(b"")
    source = tmp_path / "Mymod"
    source.mkdir(exist_ok=True)
    (source / "build.txt").write_text("displayName = Mymod\n")
    return {
        "TMODLOADER_DIR": str(tml),
        "TMODLOADER_SAVE_DIR": str(tmp_path),
        "TMODLOADER_MOD_SOURCE": str(source),
        "TMODLOADER_MOD_SOURCE_WIN": r"C:\Mymod",
    }


def test_a_machine_without_windows_process_tools_is_told_why(tmp_path):
    """The platform assumption, made visible at `check` time.

    Every default path lives under `/mnt/c`, and sessions are listed and killed
    through `tasklist.exe` and `taskkill.exe`. Nothing said so. `check` passed
    on a native Linux install and the failure arrived much later as an OSError
    naming a path in System32 - from a tool the reader had never mentioned and
    could not connect to anything they had set.
    """
    env = _install(tmp_path)
    env.update(
        {
            "TMODLOADER_TASKKILL": str(tmp_path / "nowhere" / "taskkill.exe"),
            "TMODLOADER_TASKLIST": str(tmp_path / "nowhere" / "tasklist.exe"),
            "TMODLOADER_POWERSHELL": str(tmp_path / "nowhere" / "powershell.exe"),
        }
    )

    problems = config.check(config.load(env))

    platform = [p for p in problems if "WSL2" in p]
    assert len(platform) == 1, (
        f"expected exactly one problem about the platform, got {problems}"
    )

    # It has to name the platform rather than only the missing file. "no such
    # file: taskkill.exe" sends somebody looking for a download.
    assert "WINDOWS tModLoader" in platform[0]
    assert "Linux" in platform[0] and "macOS" in platform[0]

    # And all three, so a reader fixing one at a time does not restart twice.
    for variable in (
        "TMODLOADER_TASKKILL",
        "TMODLOADER_TASKLIST",
        "TMODLOADER_POWERSHELL",
    ):
        assert variable in platform[0], f"{variable} is not named"


def test_one_missing_tool_is_still_one_problem(tmp_path):
    """One problem however many are missing: the remedy is a single fact about
    the machine, and repeating it three times buries it."""
    env = _install(tmp_path)
    env.update(windows_tools(tmp_path))
    env["TMODLOADER_POWERSHELL"] = str(tmp_path / "gone" / "powershell.exe")

    problems = [p for p in config.check(config.load(env)) if "WSL2" in p]

    assert len(problems) == 1
    assert "TMODLOADER_POWERSHELL" in problems[0]
    # The two that ARE there are not named as missing.
    assert "TMODLOADER_TASKKILL" not in problems[0]


def test_the_platform_check_is_silent_when_the_tools_are_there(tmp_path):
    """POSITIVE CONTROL. Without it a check that complained unconditionally
    would pass both tests above."""
    env = _install(tmp_path)
    env.update(windows_tools(tmp_path))

    assert config.check(config.load(env)) == []
