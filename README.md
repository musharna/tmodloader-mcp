# tmodloader-mcp

Drive a running tModLoader instance from an agent: launch it, ask it questions,
photograph it, and read its state back as structured data.

Game engines have grown MCP servers — Unity, Unreal, Godot and Defold all have
one, so an assistant can see a real scene instead of guessing from a prompt.
tModLoader has not had one. This is that.

> **Status: alpha.** No path defaults to anybody's install any more — the two
> that named a person are required, the world is asked for rather than guessed,
> and no command list is kept here to be wrong about. The mod-side half is now
> yours too: [`responder/`](responder/) is a folder you copy into your mod and
> subclass, and CI compiles it with nothing of any mod's on the compile line.
>
> **Two sessions can share one save directory.** Two clients stopped
> overwriting each other's answers in 0.3.0; 0.4.0 closed the three ways they
> could still collide — captures landing in one wall-clock second, the lock
> that serialises them being bounded by a guess, and a dedicated server having
> no address to be told apart by. Every one of those was found by RUNNING it,
> which is the thing about this project most worth knowing: the suite passes
> against several of them. See [Phase 2](#phase-2--what-would-make-this-yours).

## Why an MCP server rather than a shell script

Most of what this does _could_ be a CLI, and where that is true it should stay
one — a stateless local binary does not need a protocol in front of it.

What earns the surface here is that a running game is not stateless:

| Tool              | What it buys over `bash`                                         |
| ----------------- | ---------------------------------------------------------------- |
| `launch` / `stop` | Session state across calls                                       |
| `join`            | A second client into a session that is already running           |
| `status`          | Asking whether a session exists without provoking an error       |
| `diag`            | Structured fields AND the records under them, not text to `sed`  |
| `wait_until`      | Waiting for a state on one budget, instead of sleeping a guess   |
| `trigger`         | The write → poll → timeout → clean-up loop, written once         |
| `shot`            | A path per call, a whole PNG behind it, refusals as refusals     |
| `read_capture`    | The picture itself, for an agent not on this machine             |
| `build_mod`       | Encodes tModLoader's refusal to build while the game is open     |
| `logs`            | Any log, filtered — including the run that already rotated away  |
| `heartbeat`       | WHICH silence — absent, stale, still loading, or not armed       |
| `inventory`       | The worlds, characters and mods `launch` needs and cannot check  |
| `prune_captures`  | Removing captures without a delete loose enough to reach a world |
| `log_since`       | Only what a log gained, and whether it rotated under you         |
| `log_watch`       | Blocking until a line appears, instead of a guessed sleep        |
| `api_search`      | What the INSTALLED tModLoader actually exposes, with signatures  |
| `restart`         | stop -> build -> launch in the one order that works              |

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

Those glue steps are where the hand-written version actually went wrong: a
`pkill` pattern that matched its own command line, a readiness check that passed
on a killed process's leftover heartbeat, and a capture that reported success
while the thing it photographed had never drawn.

That row promised a PNG check that did not exist, in any version — the file was
waited for and renamed, never opened. The claim was written down here as absent
rather than quietly corrected, because a README is read by people deciding what
they no longer have to check themselves, which makes an imagined guarantee worse
than an admitted gap.

**It exists now,** and it checks both ends rather than the header the old claim
described. A file appears when it is created rather than when it is finished, so
a capture large enough to be worth taking can be read mid-write — and a truncated
PNG has an entirely valid signature. So the trailer is what decides: bytes that
are not a picture are refused at once, and a picture that is still arriving is
waited for until the timeout. Neither is renamed into your captures.

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

## How it works

The game is asked by **writing a file it polls**, not by sending it input. No
synthetic keystrokes, no window focus, and nothing that can be fooled by another
window sitting on top of the game.

That last point is the reason for the design. OS-level screen capture was tried
first and returned a picture of Discord — a window in front of the game — while
passing every check available. Reading the game's own back buffer cannot contain
another window by construction, not by luck.

Captures name a **region** and have no default. The frame holds only the game,
but that still includes a character name, a world name and any chat on screen,
so a request says which corner it wants.

The mod side of that protocol — every filename, what each one contains, and
which failures it has to be able to express — is written down in
[`docs/MOD_CONTRACT.md`](docs/MOD_CONTRACT.md), and implemented in
[`responder/`](responder/). You can read the contract or vendor the folder; the
folder is the same document with a compiler checking it.

## Requirements

- Python 3.12+
- tModLoader installed
- A mod embedding the trigger-file responder — copy [`responder/`](responder/)
  into your mod's source tree and subclass `DevResponder`

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

`TMODLOADER_MOD_NAME` is the mod's **internal** name, which every artifact
filename is built from: `<modname>-diag-<token>.txt`, `<modname>-shot-<token>.png`,
lowercased, where `<token>` identifies which player's client wrote it — except
`<modname>-capture.trigger` and `<modname>-commands.txt`, which stay one name
shared by every client (see
[`docs/MOD_CONTRACT.md`](docs/MOD_CONTRACT.md#the-filenames) for why).
tModLoader takes the mod's name from the source folder, so it is derived from
`TMODLOADER_MOD_SOURCE` and only needs setting for a checkout whose folder is
named something other than the mod. Deriving it is also what keeps two mods
driven from one machine out of each other's trigger files — they share a save
directory.

`TMODLOADER_MOD_SOURCE_WIN` is the mod source as Windows sees it, which `-build`
needs because tModLoader compiles inside a Windows process with no `/mnt/c`. It
is **derived** from `TMODLOADER_MOD_SOURCE`, so setting that one is enough for a
source on a drive mount. Set it yourself only if your mod source lives outside
`/mnt/<drive>`, where there is no drive letter to translate to and the server
will ask for it by name.

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

**The usual cause is not a missing export — it is a client older than the
export.** A process's environment is a copy taken when it starts, and nothing
outside can add to it afterwards. So a long-lived parent — a daemon, an agent
host, a desktop session — hands every client it spawns the environment it had
on the day it started, however long ago that was, and adding the variables to
your profile today does not reach it. The symptom is a shell where
`env | grep TMODLOADER` prints all three sitting next to a server that sees
none of them. Restarting the client is not enough if the thing that spawned the
client is the stale one; restart that.

Nothing can be repaired from inside a running session: the value was gone before
the process started.

A project-scoped `.mcp.json` needs approving once — Claude Code will not run a
server a repository asked it to run without being told to. Start `claude` in
this directory and accept the prompt.

To drive the harness from the directory where you actually develop your mod,
copy the block into that project's `.mcp.json` and point `--directory` at this
checkout:

```json
"args": ["run", "--directory", "/path/to/tmodloader-mcp", "tmodloader-mcp"]
```

## Phase 2 — what would make this yours

1. ~~Artifact filenames are one mod's~~ — **done.** Every artifact name is
   derived from the mod's internal name, which tModLoader takes from the source
   folder. Nothing needs setting for the usual case; `TMODLOADER_MOD_NAME`
   exists for a checkout whose folder is named something else.
2. ~~Let the mod publish its own command list, and extract the responder~~ —
   **done, both halves.** `compose` takes the list the running side published
   and has no fallback, deliberately: a guess about which commands exist is
   exactly what this replaces, and a fallback would be that guess wearing a
   different name. And the responder itself now lives in
   [`responder/`](responder/) — a folder you copy into your mod and subclass,
   with its own test project compiling it against nothing of any mod's.
3. ~~Make `TMODLOADER_MOD_SOURCE` required and drop the machine-specific
   defaults~~ — **done.** The save directory and mod source are required, and
   the world is asked for rather than defaulted to one developer's self-test
   world. What keeps a default is what is not personal: Steam's install path
   and the Windows binaries under System32.
4. ~~A template mod~~ — **done, and built rather than described.**
   [`template/DevBridgeTemplate/`](template/) is a whole tModLoader mod: build
   files, an empty `Mod` class, a `DevResponder` subclass, and a byte-identical
   copy of `responder/`. Copy the folder into `ModSources/`, rename it, build.

   It exists because three claims here were argued rather than checked. The
   vendor test project compiles eight of the eleven files with nothing of any
   mod's on the line; the other three need Terraria, XNA and `ModSystem`, so
   until now NOTHING compiled the folder a consumer actually copies. This does,
   against the real tModLoader — 0 errors, 0 warnings, with a deliberate syntax
   error injected first to prove the vendored files were genuinely on the
   compile line and not quietly skipped. It is also the worked example of the
   documented subclass, which nothing had ever compiled either.

   The copy is checked in rather than synced at build time, because
   tModLoader's `-build` compiles the mod DIRECTORY and a file outside it is
   not on the compile line at all — a template saying "copy `responder/` in
   here" would not build as checked in. A test asserts the two directories are
   byte-identical, so drift is a red test on the commit that caused it.

5. ~~Per-player artifact naming~~ — **done, and run against two real clients.**
   A client's ANSWERS now carry a player token, so two developers sharing a
   save directory stop overwriting each other's heartbeat, diag dump, capture
   reply and shot drop box. Requests could already be addressed; answers could
   not be told apart, which is the half this closed.

   The two-client run is the part worth reading: it caught a regression the
   change itself introduced, a lost-update race nobody had predicted, and —
   once that race was fixed — a deeper limit it had been hiding. Both are
   closed now: every request carries the session's own player by default, so
   the **untargeted** coin flip is gone, and the trigger is **claimed rather
   than overwritten** — a second request waits for the slot instead of
   silently replacing what is in it. The trigger still holds **one request at
   a time**, not a queue; that is the mechanism now, not a limitation to work
   around. See
   [`docs/MOD_CONTRACT.md`](docs/MOD_CONTRACT.md#what-a-live-two-client-run-found).

6. ~~Two sessions sharing one save directory still collide in three places~~ —
   **done in 0.4.0, all three run rather than reasoned about.** Two `capture`
   requests inside one wall-clock second used to produce ONE picture and two
   callers each told it was theirs; they are serialised now, and the pre-fix
   control is the number worth quoting — 0 of 6 rounds passed, with six PNGs
   on disk for twelve requests, so half the pictures were not misattributed
   but lost. The lock doing that serialising carries its holder's own deadline
   instead of a 60-second guess that was wrong in both directions. And a
   **dedicated server is addressed by its port** (`diag@port7810`), which is
   also what names its answers — before that, two servers on one save
   directory were indistinguishable on disk, so the request went to whichever
   polled first and either session's cleanup could destroy the other's.

   What is NOT covered: two dedicated servers genuinely racing for one
   trigger. Each half of that mechanism was checked with one server and a
   hand-written trigger; running two needs a second world — which the template
   mod above is now the cheapest route to.

7. ~~Everything the harness can ask for only READS~~ — **done, and off by
   default.** `DevMutations` adds five verbs that change the world rather than
   observe it: `time`, `weather`, `spawn`, `give` and `teleport`. They are
   registered by nobody until a mod writes
   `DevMutations.RegisterInto(r, Report)`, which is the whole of the opt-in —
   so re-syncing the vendored folder can never hand a mod the power to spawn
   enemies into somebody's save.

   Each verb refuses the side that cannot do it and names the side that can.
   `time`, `weather` and `spawn` are refused on a multiplayer client, because
   the server owns the clock, the weather and the NPC array — a client that
   changed them would be corrected by the next world packet, so the change
   appears to work and then undoes itself, which is far worse to debug than a
   refusal. `give` and `teleport` are refused on a dedicated server, which runs
   the world without standing in it.

   The rules and the refusals live in a Terraria-free file so they are tested
   by running them; the Terraria half is a thin applier compiled by the
   template mod and **driven against a real game, 16 of 16**
   (`tests/live_mutations_check.py`). Every verb is verified by reading the
   state back out afterwards rather than by its own success report, and the
   multiplayer half is read from the side that did not make the change. The
   first run found two defects — both in what the diag MEASURED rather than in
   the verbs, and both invisible to any test that does not run a game.

The last thing that made this a tool for one mod is gone. The filenames, the
paths and the command list were already yours — this side holds no opinion about
any of them — and now the responder that answers them is a folder in this
repository rather than a class inside Biomancy. Biomancy runs on the vendored
copy, which is what keeps the claim honest: the reference implementation is a
consumer of this folder, not the owner of it.

## Licence

MIT.
