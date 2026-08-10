"""The server as a CLIENT sees it: started as a subprocess, spoken to over stdio.

THE LAYER EVERY OTHER TEST HERE SKIPS. The rest of this suite imports the module
and calls the tool functions, and `live_check.py` says it drives "the same code
paths the MCP tools use" - which is true and still not the protocol. A Python
call returns a dict and nobody checks it. Over MCP the same dict is validated
against a schema generated from the return annotation, and that is a different
question with a different answer.

It had a different answer. `status` was broken over the protocol in the state it
exists to report - no game running - and 197 tests were green the whole time:

    Error executing tool status: 4 validation errors for StatusOut
    mode / port / player / started_pids: Field required

`NotRequired` was spelled correctly. `from __future__ import annotations` made
every annotation a string, and `typing.TypedDict` computes __required_keys__ at
class creation without resolving them, so the optional fields were silently
promoted to required. Nothing that calls a function can see that.

Every READ-ONLY tool is driven here. `status` needs nothing; the other three go
through `_cfg()`, which was the reason they went untested - it wants a
tModLoader install, and a test that wants one is a test of the machine. But
`config.load` reads the environment for every path and `config.check` only
wants a few files to EXIST, so a directory tree with an empty `tModLoader.dll`
in it satisfies it completely. See `fake_install`. The tools that need a
running GAME - `build_mod`, `launch`, `trigger`, `commands`, `diag`, `shot`,
`stop` - are still uncovered at this layer, and a fake install cannot reach
them.

Built against a fixture rather than skipped without one, because a skipped test
reads exactly like a passing one in a CI log. This suite has already been
fooled by that once.
"""

import asyncio
import json
import shutil
import sys
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.exceptions import MCPError

from tmodloader_mcp import server as server_mod

EXPECTED_TOOLS = {
    "build_mod",
    "launch",
    "trigger",
    "commands",
    "diag",
    "shot",
    "captures",
    "read_capture",
    "status",
    "logs",
    "log_files",
    "heartbeat",
    "inventory",
    "stop",
}


#: The one capture the fake install holds. Its name has to match
#: `captures.capture_pattern(mod_name)` or the reader refuses it - which is the
#: point of the pattern, and makes this constant part of the test rather than
#: decoration.
FAKE_CAPTURE = "fakemod-shot-001-full.png"

#: Not a valid PNG. Nothing in this path decodes the image - the server hands
#: back bytes and the client base64s them - so a decodable file would only be
#: testing Pillow. What matters is that these EXACT bytes come back.
FAKE_PNG = b"\x89PNG\r\n\x1a\nnot really a png, and nothing here decodes it"


@pytest.fixture
def fake_install(tmp_path):
    """A tree that satisfies `config.check` with no tModLoader anywhere in it.

    `check` asks whether a few paths exist and whether one of them holds a file
    called `tModLoader.dll`. It never opens it. So the whole install can be a
    directory and an empty file, and the read-only tools - which only list and
    read the save and log directories - cannot tell the difference.

    THE WINDOWS MOD SOURCE HAS TO BE SET EXPLICITLY. It is normally derived
    from the WSL path, and derivation only works under `/mnt/<drive>`; a
    `tmp_path` is not, so `check` would report that it cannot tell what Windows
    calls this directory. Setting it is what a non-WSL caller does anyway.

    Returns the environment the server subprocess needs.
    """
    tml = tmp_path / "tml"
    (tml / "tModLoader-Logs" / "Old").mkdir(parents=True)
    (tml / "tModLoader.dll").write_bytes(b"")
    (tml / "tModLoader-Logs" / "client.log").write_text("first line\nsecond line\n")
    (tml / "tModLoader-Logs" / "Old" / "run-1.zip").write_bytes(b"PK")

    save = tmp_path / "save"
    save.mkdir()
    (save / FAKE_CAPTURE).write_bytes(FAKE_PNG)
    # Something in the save directory that is NOT a capture, so `captures`
    # listing exactly one thing means the pattern filtered rather than that
    # there was only ever one file.
    (save / "fakemod-diag.txt").write_text("not a capture")

    # The three directories `inventory` reads, each with the decoy that a
    # too-loose listing would return: a world backup, a character backup, and a
    # mod that is enabled without being built here.
    (save / "Worlds").mkdir()
    (save / "Worlds" / "FakeWorld.wld").write_bytes(b"")
    (save / "Worlds" / "FakeWorld.wld.bak").write_bytes(b"")
    (save / "Players").mkdir()
    (save / "Players" / "n43n.plr").write_bytes(b"")
    (save / "Players" / "n43n.plr.bak").write_bytes(b"")
    (save / "Mods").mkdir()
    (save / "Mods" / "Fakemod.tmod").write_bytes(b"")
    (save / "Mods" / "enabled.json").write_text('["Fakemod", "Workshopped"]')

    mod = tmp_path / "modsrc"
    mod.mkdir()
    (mod / "build.txt").write_text("displayName = Fake\n")

    return {
        "TMODLOADER_DIR": str(tml),
        "TMODLOADER_SAVE_DIR": str(save),
        "TMODLOADER_MOD_SOURCE": str(mod),
        "TMODLOADER_MOD_SOURCE_WIN": r"C:\Fake\ModSources\modsrc",
        "TMODLOADER_MOD_NAME": "Fakemod",
    }


def _server_params(env=None) -> StdioServerParameters:
    # `-m` rather than the console script: the entry point is only on PATH once
    # the package is installed, and this has to run from a source checkout too.
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "tmodloader_mcp.server"],
        # Merged over the client's default inherited environment, not replacing
        # it - the subprocess still needs PATH to find its own interpreter.
        env=env,
    )


async def _talk(call, env=None):
    async with (
        stdio_client(_server_params(env)) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        return await call(session)


def _run(call, env=None):
    return asyncio.run(_talk(call, env))


def _structured(result):
    """The tool's output as the PROTOCOL validated it.

    `structured_content` is the thing generated from the return annotation, so
    it is what a schema mistake shows up in. Reading the text block instead
    would be re-parsing JSON the server already parsed and checked.
    """
    assert not result.is_error, "".join(
        c.text for c in result.content if hasattr(c, "text")
    )
    assert result.structured_content is not None, (
        "tool declared structured_output and returned none"
    )
    return result.structured_content


needs_subprocess = pytest.mark.skipif(
    shutil.which(sys.executable) is None, reason="no interpreter to spawn"
)


@needs_subprocess
def test_the_server_starts_and_advertises_every_tool():
    """A tool that fails to register is invisible, not loud.

    The decorator runs at import; a tool whose signature the schema generator
    cannot handle drops out of the listing and everything else still works.
    Counting them is the only thing that notices.
    """

    async def call(session):
        return (await session.list_tools()).tools

    tools = _run(call)
    names = {t.name for t in tools}

    assert names == EXPECTED_TOOLS, (
        f"missing {EXPECTED_TOOLS - names}, extra {names - EXPECTED_TOOLS}"
    )
    assert all(t.description for t in tools), (
        "a tool with no description is unusable to a model"
    )


@needs_subprocess
def test_status_survives_the_round_trip_with_no_game_running():
    """THE ONE THAT WAS BROKEN, in the state it exists to report.

    Not `status() == {...}` - that is the assertion that passed throughout. The
    output has to come back THROUGH the protocol, where it is validated against
    the generated schema.
    """

    async def call(session):
        return await session.call_tool("status", {})

    result = _run(call)

    assert not result.is_error, "".join(
        c.text for c in result.content if hasattr(c, "text")
    )

    text = "".join(c.text for c in result.content if hasattr(c, "text"))
    parsed = json.loads(text)

    # The shape is the contract: every field present, absence expressed as null.
    assert set(parsed) == set(server_mod.StatusOut.__annotations__)
    assert parsed["running"] is False
    assert parsed["started_pids"] is None


def test_output_annotations_are_resolved_types_not_forward_refs():
    """The defect underneath, named where it lives rather than by symptom.

    `from __future__ import annotations` leaves these as ForwardRef, and a
    ForwardRef is what TypedDict cannot see NotRequired through - which is how
    every optional field became required with nothing complaining.

    The first version of this guard tested `isinstance(a, str)` and passed
    happily against the defect, because PEP 563 stores ForwardRef objects
    rather than bare strings. A predicate that cannot observe the thing it
    guards is not a guard; this one was rewritten after watching it fail to
    fail.
    """
    for cls in (server_mod.StatusOut, server_mod.StopOut, server_mod.CommandsOut):
        annotations = cls.__annotations__
        unresolved = [
            name
            for name, a in annotations.items()
            if isinstance(a, str) or type(a).__name__ == "ForwardRef"
        ]
        assert not unresolved, (
            f"{cls.__name__} has unresolved annotations {unresolved}, so "
            "TypedDict cannot see optionality through them - is "
            "`from __future__ import annotations` back at the top of server.py?"
        )


# ---- the read-only tools, against an install that is not one --------------


@needs_subprocess
def test_captures_lists_only_this_mod_s_captures(fake_install):
    """Two things at once: the round trip, and that the filter is the filter.

    The save directory holds diag dumps and heartbeats beside the captures, so
    a listing that returned everything would look identical to a correct one
    whenever a capture happened to be the only file there. The fixture puts a
    non-capture next to it for that reason.
    """

    async def call(session):
        return await session.call_tool("captures", {})

    out = _structured(_run(call, fake_install))

    assert out == {"captures": [FAKE_CAPTURE]}


@needs_subprocess
def test_log_files_reads_the_install_rather_than_a_constant(fake_install):
    """Which logs exist is a fact about disk, and the archive count with it.

    A server-only session writes no `client.log`; the fixture writes one log
    and archives one run, so a hardcoded answer and a read one differ.
    """

    async def call(session):
        return await session.call_tool("log_files", {})

    out = _structured(_run(call, fake_install))

    assert out == {"logs": ["client.log"], "archived_runs": 1}


@needs_subprocess
def test_logs_survives_both_of_the_shapes_it_returns(fake_install):
    """THE SAME CLASS OF DEFECT THAT BROKE `status`, in the one other tool with
    a key set that depends on which branch ran.

    `logs` returns `note` when the log is absent and omits it when it is not.
    That is exactly the shape `status` had - and `status` could not survive the
    round trip - so asking for a log that exists and one that does not, over the
    protocol, is what tells the two cases apart. Absence being NORMAL here is
    the whole reason the second call is not an error.
    """

    async def call(session):
        found = await session.call_tool("logs", {"name": "client.log", "lines": 5})
        missing = await session.call_tool("logs", {"name": "server.log"})
        return found, missing

    found, missing = (_structured(r) for r in _run(call, fake_install))

    assert found["found"] is True
    assert found["lines"] == ["first line", "second line"]

    assert missing["found"] is False
    assert missing["lines"] == []
    assert missing["note"], "an absent log has to say so, not just return nothing"


@needs_subprocess
def test_read_capture_returns_the_bytes_and_still_refuses_a_path(fake_install):
    """The containment, asserted THROUGH the surface, with its own control.

    The refusal half of this test passes against a server that refuses
    everything - including the capture it is supposed to serve - so a broken
    reader and a safe one look the same. The positive control is in the same
    test on purpose: the legitimate name has to come back with the exact bytes
    in the same run that the traversal is rejected.

    `..` is only one spelling. The module compares RESOLVED parents rather than
    scanning for it, and the name filter refuses this one before that even
    matters - which is the layering the docstring in `captures.py` describes.
    """
    import base64

    async def call(session):
        good = await session.call_tool("read_capture", {"name": FAKE_CAPTURE})
        bad = await session.call_tool("read_capture", {"name": "../../../etc/passwd"})
        return good, bad

    good, bad = _run(call, fake_install)

    # Positive control: the thing this tool exists to do still works.
    assert not good.is_error, "".join(
        c.text for c in good.content if hasattr(c, "text")
    )
    images = [c for c in good.content if getattr(c, "type", None) == "image"]
    assert len(images) == 1, f"expected one image, got {good.content}"
    assert base64.b64decode(images[0].data) == FAKE_PNG

    # And the refusal, which only means something alongside the control above.
    assert bad.is_error, "a path outside the save directory was served"
    assert "capture name" in "".join(c.text for c in bad.content if hasattr(c, "text"))


# ---- the same file, reached the other way --------------------------------


@needs_subprocess
def test_the_capture_resource_is_as_contained_as_the_tool(fake_install):
    """`capture://` reads the same file through a SECOND surface.

    `captures.py` says why that is the interesting case: "two paths to one file
    is how one of them ends up with a weaker check". The resource and the tool
    share `captures.read`, and sharing it is the claim being tested here - a
    resource that grew its own reader is exactly what this would catch.

    THE REFUSAL THAT DISCRIMINATES IS THE NEIGHBOUR, NOT THE TRAVERSAL. The
    fixture keeps `fakemod-diag.txt` beside the capture: a file that really
    exists, really is in the save directory, and really is not a capture. Only
    the NAME check refuses it, so deleting that check serves it. `..` cannot
    do the same job here - measured, it never reaches the reader at all, since
    `capture://../../../etc/passwd` fails to match the URI template and comes
    back as "Unknown resource". That is a refusal by ROUTING, and a routing
    refusal would keep passing even if the reader were removed entirely.

    The client also sees only a generic "Error creating resource from template"
    - the reason stays server-side - so there is no message to assert on. That
    makes the positive control the load-bearing half rather than a courtesy.
    """
    import base64

    async def call(session):
        templates = await session.list_resource_templates()
        good = await session.read_resource(f"capture://{FAKE_CAPTURE}")

        refused = []
        for uri in (
            "capture://fakemod-diag.txt",  # real file, real directory, not a capture
            f"capture://{FAKE_CAPTURE}.exe",  # the suffix an unanchored regex accepts
            "capture://../../../etc/passwd",  # refused by routing - see docstring
        ):
            try:
                await session.read_resource(uri)
                refused.append((uri, None))
            except MCPError as denied:
                refused.append((uri, str(denied)))
        return templates, good, refused

    templates, good, refused = _run(call, fake_install)

    assert "capture://{name}" in [
        t.uri_template for t in templates.resource_templates
    ], "a resource nobody can list is a resource nobody can find"

    # Positive control: the capture itself still comes back, and comes back WHOLE.
    blob = good.contents[0]
    assert blob.mime_type == "image/png"
    assert base64.b64decode(blob.blob) == FAKE_PNG

    served = [uri for uri, denied in refused if denied is None]
    assert not served, f"the resource served {served}"


@needs_subprocess
def test_heartbeat_answers_with_no_game_and_no_session(fake_install):
    """The tool exists FOR the case where nothing else works, so it is tested there.

    THIS TEST EXISTS BECAUSE ADVERTISED IS NOT WORKING. `heartbeat` shipped for
    a few minutes with its module import stripped by the formatter — added
    before its first use, removed as unused, and the name only appears inside
    the function body, so it was a NameError at CALL time and not at import.
    The registry listed it, the drift guard counted it, 213 of 214 tests passed,
    and the tool could not run. That is the `status` bug's exact shape: the
    surface was right and nothing called through it.

    Both sides absent is the single most informative reading this can give — it
    is the difference between a mod that is slow and one that was never loaded —
    so it is a value here, not an error.
    """

    async def call(session):
        return await session.call_tool("heartbeat", {})

    out = _structured(_run(call, fake_install))

    for side in ("client", "server"):
        assert out[side]["present"] is False, f"{side} invented a heartbeat"
        assert out[side]["live"] is False
        assert out[side]["age_seconds"] is None
        assert out[side]["fields"] == {}
        assert "not loaded" in out[side]["diagnosis"]


@needs_subprocess
def test_heartbeat_reads_a_real_one_and_types_its_booleans(fake_install):
    """POSITIVE CONTROL for the test above, which asserts only absences.

    A reader that returned `present: False` unconditionally — or that crashed
    and was reported as absent — would pass that test completely. This writes an
    actual heartbeat into the fake install's save directory and reads it back
    through the protocol, so the schema validates the mixed-type `fields` dict
    on the way out.

    The booleans are checked with `is`, not truthiness: they arrived as the
    strings `"True"`/`"False"` until the parser learned this file's shapes, and
    `"False"` is truthy, so `assert out["armed"]` passed on a game that was not.
    """
    save = Path(fake_install["TMODLOADER_SAVE_DIR"])
    (save / "fakemod-hooks.txt").write_text(
        "gameMenu: False\n"
        "dedServ: False\n"
        "trigger-exists: False\n"
        "world-ready: True\n"
        "capture-ready: True\n"
        "armed: True\n"
        "polls: 194\n"
    )

    async def call(session):
        return await session.call_tool("heartbeat", {})

    out = _structured(_run(call, fake_install))

    client = out["client"]
    assert client["present"] is True
    assert client["live"] is True
    assert client["side"] == "client", "side comes from dedServ, not the filename"
    assert client["world_ready"] is True
    assert client["armed"] is True
    assert client["fields"]["polls"] == 194
    assert client["fields"]["gameMenu"] is False
    assert "can answer" in client["diagnosis"]

    # And the side that genuinely has no file still reports absent, so the
    # reader is answering per-side rather than returning one answer twice.
    assert out["server"]["present"] is False


@needs_subprocess
def test_inventory_answers_the_two_preconditions_launch_could_not_check(fake_install):
    """`launch` demands an existing character and a Windows world path.

    Neither was answerable from this surface, so the only way to learn either
    was to launch and read the failure — a kick for a wrong character, and a
    readiness timeout blaming the heartbeat for a wrong world path.

    The decoys matter as much as the entries: the fixture holds a `.wld.bak`
    beside the world and a `.plr.bak` beside the character, so a listing that
    returned two of each would be a listing that filtered nothing.
    """

    async def call(session):
        return await session.call_tool("inventory", {})

    out = _structured(_run(call, fake_install))

    assert [w["name"] for w in out["worlds"]] == ["FakeWorld"]
    assert out["players"] == ["n43n"]


@needs_subprocess
def test_inventory_keeps_enabled_and_built_here_apart(fake_install):
    """A mod can be enabled with no `.tmod` here — a workshop mod is elsewhere.

    `Workshopped` is in `enabled.json` and has no file. Collapsing these into
    one `installed` flag would report it missing, which is how someone ends up
    rebuilding a mod that was never the problem. This is also the split that
    turns `commands`' single `responder: false` into "not built" versus "built
    and switched off".
    """

    async def call(session):
        return await session.call_tool("inventory", {})

    mods = {m["name"]: m for m in _structured(_run(call, fake_install))["mods"]}

    assert mods["Fakemod"] == {
        "name": "Fakemod",
        "enabled": True,
        "built_here": True,
    }
    assert mods["Workshopped"] == {
        "name": "Workshopped",
        "enabled": True,
        "built_here": False,
    }


@needs_subprocess
def test_a_world_path_survives_the_round_trip_as_a_nullable_string(fake_install):
    """`path_win` is `str | None`, and null is the answer off a drive mount.

    A nullable field is exactly what broke `status` — an optional key the
    schema generator marked required, invisible to every test that called the
    function directly. The fixture lives in a `tmp_path`, which is not under
    `/mnt/<drive>`, so this is the null branch going through validation.
    """

    async def call(session):
        return await session.call_tool("inventory", {})

    world = _structured(_run(call, fake_install))["worlds"][0]

    assert "path_win" in world, "the optional key did not survive the round trip"
    assert world["path_win"] is None
