<h1 align="center">tmodloader-mcp</h1>

<p align="center"><em>Drive a running tModLoader instance from an agent —
launch it, ask it questions, photograph it, and read its state back as
structured data.</em></p>

<p align="center">
  <a href="https://github.com/musharna/tmodloader-mcp/actions/workflows/tests.yml"><img src="https://github.com/musharna/tmodloader-mcp/actions/workflows/tests.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/tmodloader-mcp/"><img src="https://img.shields.io/pypi/v/tmodloader-mcp" alt="PyPI"></a>
  <img src="https://img.shields.io/badge/python-3.12%20%7C%203.13-blue" alt="Python 3.12 | 3.13">
  <img src="https://img.shields.io/badge/licence-MIT-green" alt="MIT">
</p>

<p align="center">
  <a href="#how-it-works">How it works</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#configuration">Configuration</a> ·
  <a href="#known-limits">Known limits</a>
</p>

Game engines have grown MCP servers — Unity, Unreal, Godot and Defold all have
one, so an assistant can see a real scene instead of guessing from a prompt.
tModLoader has not had one. This is that.

<p align="center">
  <img src="https://raw.githubusercontent.com/musharna/tmodloader-mcp/master/docs/shot-topright.png"
       alt="The top-right region of a running game: hearts, a minimap, two players on it"
       width="640">
</p>

<p align="center"><sub><em>A real answer, not a mock-up:
<code>shot:topright</code>, exactly as the tool returned it — read from the
game's own back buffer, so a window sitting in front of the game cannot be in
the picture. Why that matters is the first thing the next section
explains.</em></sub></p>

> **Status: alpha** — it has only ever run on one install; see
> [Known limits](#known-limits) before adopting it. Nothing defaults to
> anybody's install, the mod-side half is a folder you vendor
> ([`responder/`](https://github.com/musharna/tmodloader-mcp/tree/master/responder)),
> and CI compiles it with nothing of any mod's on the compile line.

## Quick start

```sh
uv tool install tmodloader-mcp    # or: pip install tmodloader-mcp

# the two paths with no default — every plausible default names somebody's install
export TMODLOADER_SAVE_DIR="/mnt/c/Users/<you>/Documents/My Games/Terraria/tModLoader"
export TMODLOADER_MOD_SOURCE="$TMODLOADER_SAVE_DIR/ModSources/<YourMod>"
```

Then:

1. **Vendor the responder** — copy
   [`responder/`](https://github.com/musharna/tmodloader-mcp/tree/master/responder)
   into your mod's source tree and subclass `DevResponder`. It is a folder of
   C#, not a PyPI dependency, because it compiles inside **your** mod.
2. **Point your MCP client at the server** — the block in
   [Using it from Claude Code](#using-it-from-claude-code).
3. **Ask the game something** — `launch`, then `diag`. Or open with the
   `start_a_session` prompt, which lists the worlds and characters that
   actually exist here, the two preconditions `launch` cannot check.

This needs **WSL2 driving a Windows tModLoader** — [Requirements](#requirements)
says why before anything else does.

## How it works

The game is asked by **writing a file it polls**, not by sending it input:

```text
┌─────────────────────────────────────────────────────┐
│  an agent — Claude Code, or anything speaking MCP   │
└──────────────────────┬──────────────────────────────┘
                       │  tools · prompts · capture:// resources
┌──────────────────────▼──────────────────────────────┐
│  tmodloader-mcp — this package (Python, WSL2)       │
└──────────────────────┬──────────────────────────────┘
                       │  writes  <mod>-capture.trigger
                       │  reads   <mod>-diag-<token>.txt,
                       │          <mod>-shot-<token>.png, ...
┌──────────────────────▼──────────────────────────────┐
│  the tModLoader save directory                      │
│  plain files on disk — no socket, no keystrokes     │
└──────────────────────▲──────────────────────────────┘
                       │  polls every few frames, writes
                       │  each answer next to its trigger
┌──────────────────────┴──────────────────────────────┐
│  DevResponder — C# you vendor into YOUR mod,        │
│  inside tModLoader, a real game, on Windows         │
└─────────────────────────────────────────────────────┘
```

No synthetic keystrokes, no window focus, and nothing that can be fooled by
another window sitting on top of the game. That last point is the reason for
the design: OS-level screen capture was tried first and returned a picture of
Discord — a window in front of the game — while passing every check available.
Reading the game's own back buffer cannot contain another window by
construction, not by luck.

Captures name a **region** and have no default. The frame holds only the game,
but that still includes a character name, a world name and any chat on screen,
so a request says which corner it wants.

The mod side of that protocol — every filename, what each one contains, and
which failures it has to be able to express — is written down in
[`docs/MOD_CONTRACT.md`](https://github.com/musharna/tmodloader-mcp/blob/master/docs/MOD_CONTRACT.md),
and implemented in
[`responder/`](https://github.com/musharna/tmodloader-mcp/tree/master/responder).
You can read the contract or vendor the folder; the folder is the same document
with a compiler checking it.

Since 0.6.0 replies are **tagged**: a request may carry a short id the
responder echoes back as its answer's first line, so a late answer can never be
mistaken for the next request's. A vendored copy older than that keeps working
— the harness sends the tag only to a responder that advertises taking it —
but re-vendoring buys the correlation.

## What the mod side answers

These are verbs, driven through the `trigger` tool, not separate MCP tools.

**The reads come with the base class** — every consumer serves them, and
vendoring an upgrade can never hand your mod a power it did not have before:

| Verb               | What it answers                                         |
| ------------------ | ------------------------------------------------------- |
| `capture` / `shot` | a photograph of the frame, by named region              |
| `diag`             | whatever your mod chose to report                       |
| `tiles`            | tile-type counts in a rectangle                         |
| `entities`         | NPC, item or projectile counts, filterable by rectangle |
| `find`             | one line per entity: id, position, health               |
| `players`          | who is connected                                        |

**The writes are opt-ins**, each one line you write in `RegisterCommands`:

| Opt-in             | Verbs it adds                                                              |
| ------------------ | -------------------------------------------------------------------------- |
| `DevMutations`     | `time` `weather` `spawn` `give` `teleport` `settile` `cleartile` `despawn` |
| `DevCommandBridge` | `command` `commandlist` — runs any mod's own registered `ModCommand`s      |
| `DevChat`          | `chat` `say`                                                               |

One line each is deliberately the whole mechanism: not a setting, not a marker
file, not an
environment variable, because each of those can be switched on somewhere other
than the source somebody will read when they ask why an NPC appeared in their
world. [`responder/README.md`](https://github.com/musharna/tmodloader-mcp/blob/master/responder/README.md)
has the detail, including why `DevCommandBridge` is the answer to "what about
an escape hatch" and why there is no `reflect_invoke` here.

## The tool surface

Most of what this does _could_ be a CLI, and where that is true it should stay
one — a stateless local binary does not need a protocol in front of it. What
earns the surface here is that a running game is not stateless:

**A session**

| Tool              | What it buys over `bash`                                        |
| ----------------- | --------------------------------------------------------------- |
| `launch` / `stop` | Session state across calls                                      |
| `join`            | A second client into a session that is already running          |
| `status`          | Asking whether a session exists without provoking an error      |
| `restart`         | stop → build → launch in the one order that works               |
| `inventory`       | The worlds, characters and mods `launch` needs and cannot check |

**Asking the game**

| Tool         | What it buys over `bash`                                        |
| ------------ | --------------------------------------------------------------- |
| `trigger`    | The write → poll → timeout → clean-up loop, written once        |
| `commands`   | What the mod says it serves, read from the mod, not a copy here |
| `diag`       | Structured fields AND the records under them, not text to `sed` |
| `wait_until` | Waiting for a state on one budget, instead of sleeping a guess  |
| `heartbeat`  | WHICH silence — absent, stale, still loading, or not armed      |

**Pictures**

| Tool             | What it buys over `bash`                                          |
| ---------------- | ----------------------------------------------------------------- |
| `shot`           | A path per call, a whole PNG behind it, refusals as refusals      |
| `captures`       | Which captures exist, as names — a reader that takes no paths     |
| `read_capture`   | The picture itself, for an agent not on this machine              |
| `prune_captures` | Removing captures without a delete loose enough to reach a world  |

**Logs**

| Tool        | What it buys over `bash`                                        |
| ----------- | --------------------------------------------------------------- |
| `logs`      | Any log, filtered — including the run that already rotated away |
| `log_files` | Which logs exist right now, and how many old runs are archived  |
| `log_since` | Only what a log gained, and whether it rotated under you        |
| `log_watch` | Blocking until a line appears, instead of a guessed sleep       |

**Building, the API, and the saves**

| Tool             | What it buys over `bash`                                          |
| ---------------- | ----------------------------------------------------------------- |
| `build_mod`      | Encodes tModLoader's refusal to build while the game is open      |
| `api_search`     | What the INSTALLED tModLoader actually exposes, with signatures   |
| `save_snapshot`  | Copying the world and characters aside before a run mutates them  |
| `save_restore`   | Putting them back, saving what it overwrote so it can be undone   |
| `save_snapshots` | Which copies exist, newest first                                  |

Those glue steps are where the hand-written version actually went wrong: a
`pkill` pattern that matched its own command line, a readiness check that
passed on a killed process's leftover heartbeat, and — for one stretch — a
`shot` that promised "a whole PNG behind it" while no PNG check existed in any
version: the file was waited for and renamed, never opened. The check exists
now, and it checks the end that decides: a PNG's signature is valid on a
truncated file, so the trailer is what is read — bytes that are not a picture
are refused at once, and a picture still arriving is waited for. The gap was
recorded here as absent rather than quietly corrected, because a README is
read by people deciding what they no longer have to check themselves.

Two **prompts** ship with it. `diagnose_silence` walks the four reasons the mod
might not answer — with this install's heartbeat, mod list and logs already
read, rather than as prose you apply yourself. `start_a_session` lists the
worlds and characters that actually exist here, which are the two preconditions
`launch` states and cannot check. Both render the failure into the text when
the configuration is unusable, because a diagnostic that refuses to render has
failed at the one moment it was for.

Captures are also addressable as `capture://{name}` resources. Both surfaces go
through one reader that accepts a **name, never a path**, and serves only
capture-shaped files whose resolved parent is the save directory — a reader that
opened whatever it was handed would be the leak this project exists to prevent,
arriving from the other end.

## What it cannot do

**There is no headless singleplayer.** Terraria has no entry point for it —
`-join -player -skipselect` lands at the main menu, measured rather than
assumed. `launch("singleplayer")` refuses and says so instead of launching
something else and letting you believe otherwise. Singleplayer testing needs a
human to load a world; the other tools then drive it normally.

That matters more than it sounds: a bug that only appeared in singleplayer
shipped once precisely because every harness ran server-plus-client.

**There is no bare dedicated server either.** An empty server runs no update
hooks, so the mod never polls and never answers — measured on one server
process, changing only whether a client was attached: silent for 90s alone, and
its heartbeat appeared within 30s of a client joining that same process,
reporting `polls: 1`. `launch("server")` refuses for the same reason
singleplayer does — what it promises is a game that can answer, and a server on
its own never becomes one. Start a server outside this tool if you want one to
join yourself.

## Requirements

**WSL2 on Windows, driving a Windows tModLoader.** This is the one requirement
worth reading before the others, because it is not a preference — sessions are
listed and killed through Windows' own `tasklist.exe` and `taskkill.exe`, and
`build_mod` hands tModLoader a Windows path because it builds inside a Windows
process. A native Linux or macOS tModLoader cannot be driven by this as it
stands. The configuration check every tool runs first says so by name rather
than failing later on a missing file in System32; if you are on WSL and those
tools live somewhere unusual, set
`TMODLOADER_TASKLIST`, `TMODLOADER_TASKKILL` and `TMODLOADER_POWERSHELL`.

- Python 3.12+
- tModLoader installed (1.4.4.9 is what this is tested against)
- A mod embedding the trigger-file responder — copy
  [`responder/`](https://github.com/musharna/tmodloader-mcp/tree/master/responder)
  into your mod's source tree and subclass `DevResponder`
- A .NET SDK, for `api_search` only — the index is built by a small C# tool.
  Everything else works without one.

## Install

```sh
uv tool install tmodloader-mcp    # or: pip install tmodloader-mcp
```

Or install nothing and let `uvx tmodloader-mcp` resolve it per run — the
client configuration below shows both shapes.

The package is the Python half only. The responder is not on PyPI and could
not usefully be — it is C# that compiles inside **your** mod — so it comes
from vendoring
[`responder/`](https://github.com/musharna/tmodloader-mcp/tree/master/responder)
out of the repository, however you obtained the package.

## Configuration

Every path is an environment variable. **Two are required**, because every
plausible default for them names somebody's own install:

| Variable                    | Meaning                                         |
| --------------------------- | ----------------------------------------------- |
| `TMODLOADER_SAVE_DIR`       | **Required.** Where the mod writes artifacts    |
| `TMODLOADER_MOD_SOURCE`     | **Required.** Mod source directory (WSL path)   |
| `TMODLOADER_DIR`            | tModLoader install; defaults to Steam's layout  |
| `TMODLOADER_WORLD_WIN`      | Default world, as Windows spells it — see below |
| `TMODLOADER_MOD_SOURCE_WIN` | Usually leave unset — see below                 |
| `TMODLOADER_MOD_NAME`       | Usually leave unset — see below                 |

```sh
export TMODLOADER_SAVE_DIR="/mnt/c/Users/<you>/Documents/My Games/Terraria/tModLoader"
export TMODLOADER_MOD_SOURCE="$TMODLOADER_SAVE_DIR/ModSources/<YourMod>"
```

The required two have no default rather than a plausible one on purpose. A
default pointing at the author's disk does not fail on yours — it resolves, and
in the worst case it resolves to something that exists, so the server drives an
install you never chose. Both unset variables are reported together, so this
costs one restart and not two.

`TMODLOADER_WORLD_WIN` is the world `launch` loads when you do not pass one. It
has no default either; with neither set, `launch` refuses and **lists the worlds
actually in your save directory**, with the Windows paths it wants. `inventory`
answers the same question without launching anything.

<details>
<summary><code>TMODLOADER_MOD_NAME</code> — when the derived name is wrong,
and what the name keeps apart</summary>

`TMODLOADER_MOD_NAME` is the mod's **internal** name, which every artifact
filename is built from: `<modname>-diag-<token>.txt`, `<modname>-shot-<token>.png`,
lowercased, where `<token>` identifies which player's client wrote it — except
`<modname>-capture.trigger` and `<modname>-commands.txt`, which stay one name
shared by every client (see
[`docs/MOD_CONTRACT.md`](https://github.com/musharna/tmodloader-mcp/blob/master/docs/MOD_CONTRACT.md#the-filenames)
for why).
tModLoader takes the mod's name from the source folder, so it is derived from
`TMODLOADER_MOD_SOURCE` and only needs setting for a checkout whose folder is
named something other than the mod. Deriving it is also what keeps two mods
driven from one machine out of each other's trigger files — they share a save
directory.

</details>

<details>
<summary><code>TMODLOADER_MOD_SOURCE_WIN</code> — only for a mod source living
outside <code>/mnt/&lt;drive&gt;</code></summary>

`TMODLOADER_MOD_SOURCE_WIN` is the mod source as Windows sees it, which `-build`
needs because tModLoader compiles inside a Windows process with no `/mnt/c`. It
is **derived** from `TMODLOADER_MOD_SOURCE`, so setting that one is enough for a
source on a drive mount. Set it yourself only if your mod source lives outside
`/mnt/<drive>`, where there is no drive letter to translate to and the server
will ask for it by name.

</details>

The two describe one directory. If you set both to different places the server
refuses to start and says so, rather than driving one and building the other.

## Using it from Claude Code

A `.mcp.json` ships with the repository, so a session started in this directory
finds the server:

```json
{
  "mcpServers": {
    "tmodloader": {
      "command": "uv",
      "args": ["run", "tmodloader-mcp"],
      "env": {
        "TMODLOADER_SAVE_DIR": "${TMODLOADER_SAVE_DIR}",
        "TMODLOADER_MOD_SOURCE": "${TMODLOADER_MOD_SOURCE}"
      }
    }
  }
}
```

The two paths are **read from your environment rather than written down**. A
committed config with real paths in it would put back exactly what the required
variables took out, and it would be one person's paths in everybody's checkout.
Export them first; `claude mcp list` names any that are missing.

**Export them where the client is launched, not only in an interactive shell.**
The substitution is the _client's_, against its own environment, so a client
that never sourced your shell profile has nothing to substitute and passes
`${TMODLOADER_SAVE_DIR}` through as text. The server treats a value that is
still its own name as absent and says so by that name, rather than reporting
four problems about the variables derived from it.

<details>
<summary><strong>If the server reports a variable missing that you know you
exported</strong> — the usual cause is a client older than the export</summary>

A process's environment is a copy taken when it starts, and nothing
outside can add to it afterwards. So a long-lived parent — a daemon, an agent
host, a desktop session — hands every client it spawns the environment it had
on the day it started, however long ago that was, and adding the variables to
your profile today does not reach it. The symptom is a shell where
`env | grep TMODLOADER` prints all three sitting next to a server that sees
none of them. Restarting the client is not enough if the thing that spawned the
client is the stale one; restart that.

Nothing can be repaired from inside a running session: the value was gone before
the process started.

</details>

A project-scoped `.mcp.json` needs approving once — Claude Code will not run a
server a repository asked it to run without being told to. Start `claude` in
this directory and accept the prompt.

To drive the harness from the directory where you actually develop your mod,
copy the block into that project's `.mcp.json` and point `--directory` at this
checkout:

```json
"args": ["run", "--directory", "/path/to/tmodloader-mcp", "tmodloader-mcp"]
```

Or, with the package from PyPI, no checkout at all:

```json
"command": "uvx",
"args": ["tmodloader-mcp"]
```

The repository's own `.mcp.json` stays on `uv run` deliberately — inside this
checkout you want the code in front of you, not the release behind it.

## Known limits

Everything below is a fact about this repository rather than a plan. The
history it replaced — seven rounds of "what would make this yours", every item
struck through — now lives in
[`CHANGELOG.md`](https://github.com/musharna/tmodloader-mcp/blob/master/CHANGELOG.md),
which is where a changelog belongs.

**The thing most worth knowing about this project is that its hardest bugs
were found by RUNNING it.** Two clients overwriting each other's answers, a
capture lock bounded by a guess, a dedicated server with no address to be told
apart by, and — most recently — a save-snapshot feature whose entire premise
turned out to be false when somebody finally measured it. The unit suite
passed against every one of those. The changelog is the record.

**It has only ever run on one install.** One machine, one tModLoader
(1.4.4.9), one world, one character. Every live check in `tests/` drives a real
game rather than a mock, which is the strongest evidence this project has — and
it is still evidence from a single configuration. That is what the alpha
classifier is for, and the first thing an outside user is likely to find is
something install-specific.

**Two dedicated servers racing for one trigger is unobserved.** A server is
addressed by its port and answers under that name, and each half of that
mechanism was checked with one server and a hand-written trigger. Running two
at once needs a second world;
[`template/`](https://github.com/musharna/tmodloader-mcp/tree/master/template)
is the cheapest route to one.

**There is no escape hatch, deliberately.** Other harnesses ship
`reflect_invoke` or `execute_code`. `command` is the answer here: it runs a
mod's OWN registered `ModCommand`s, which the mod already decided existed,
named, and gave a usage line. That keeps every reachable action published,
typed and refusable. Arbitrary evaluation would buy unlimited reach and throw
that away, so a question nobody wrote a verb for still costs an edit, a
rebuild and a relaunch.

**A stopped session saves nothing.** `stop` force-kills, so a run that changes
the world usually leaves no trace on disk — measured, not assumed. Do not rely
on that: a run long enough to autosave, or a graceful exit, does write. Take a
`save_snapshot` before anything that mutates a world you care about.

## Licence

MIT.
