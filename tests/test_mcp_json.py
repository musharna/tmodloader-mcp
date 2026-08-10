"""The shipped `.mcp.json`, checked the way the rest of this repo checks things.

A registration file is easy to break in ways nothing notices: it is not
imported, not linted, and not executed by the suite. The two failures worth
catching are a config that no longer parses, and one that has somebody's real
paths pasted into it — which is exactly what the required environment variables
were introduced to stop, arriving by a different door.
"""

from __future__ import annotations

import json
from pathlib import Path

MCP_JSON = Path(__file__).resolve().parent.parent / ".mcp.json"


def test_the_registration_file_exists_and_parses():
    """Seventeen tools are worth nothing if nothing can reach them.

    This is the whole reason the file was added: `claude mcp list` matched no
    server, there was no project config, and nothing in the user config either.
    """
    assert MCP_JSON.is_file(), f"no {MCP_JSON.name} - the server is unreachable"

    config = json.loads(MCP_JSON.read_text())
    assert "tmodloader" in config["mcpServers"]


def test_the_registration_runs_this_package():
    """A registration naming some other command would list and never work."""
    entry = json.loads(MCP_JSON.read_text())["mcpServers"]["tmodloader"]

    assert entry["command"] == "uv"
    assert "tmodloader-mcp" in entry["args"], entry["args"]


def test_the_registration_holds_no_real_paths():
    """GUARDS THE CLASS. The same rule as the config defaults, other door.

    Every path here has to be an `${ENVIRONMENT_VARIABLE}` reference. A real one
    would be one person's install shipped in everybody's checkout, and it would
    reintroduce precisely what making the variables required removed.

    Shape rather than username, for the same reason as `test_config.py`: naming
    the account would pass the moment a different one appeared, and would put
    that name into the repository in order to look for it.
    """
    entry = json.loads(MCP_JSON.read_text())["mcpServers"]["tmodloader"]
    env = entry.get("env", {})

    # Positive control: an empty env block would satisfy every assertion below
    # while telling the server nothing at all.
    assert env, "no env block - the required variables are not passed through"

    for name, value in env.items():
        assert value.startswith("${") and value.endswith("}"), (
            f"{name} is a literal ({value!r}), not an environment reference"
        )

    for text in [*env.values(), *entry["args"]]:
        assert "/Users/" not in text and "/home/" not in text, (
            f"per-account path in the shipped registration: {text!r}"
        )


def test_the_registration_passes_through_every_required_variable():
    """A missing one fails at launch, not at `mcp list`, which is far later."""
    from tmodloader_mcp import config

    env = json.loads(MCP_JSON.read_text())["mcpServers"]["tmodloader"]["env"]

    missing = sorted(set(config.REQUIRED) - set(env))
    assert not missing, f"the registration never passes {missing}"
