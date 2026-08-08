# tmodloader-mcp

Drive a running tModLoader instance from an agent: launch it, ask it questions,
photograph it, and read its state back as structured data.

Game engines have grown MCP servers — Unity, Unreal, Godot and Defold all have
one, so an assistant can see a real scene instead of guessing from a prompt.
tModLoader has not had one. This is that.

> **Status: alpha, phase 1.** The paths default to one specific Biomancy
> install, and the mod-side half is not yet extracted into a package other mods
> can depend on. It is genuinely useful for that mod today and not yet useful
> for yours. See [Phase 2](#phase-2--what-would-make-this-yours).

## Why an MCP server rather than a shell script

Most of what this does _could_ be a CLI, and where that is true it should stay
one — a stateless local binary does not need a protocol in front of it.

What earns the surface here is that a running game is not stateless:

| Tool              | What it buys over `bash`                                     |
| ----------------- | ------------------------------------------------------------ |
| `launch` / `stop` | Session state across calls                                   |
| `diag`            | Structured fields, not text you `sed` and misparse           |
| `trigger`         | The write → poll → timeout → clean-up loop, written once     |
| `shot`            | A capture path per call, and refusals reported as refusals   |
| `build_mod`       | Encodes tModLoader's refusal to build while the game is open |
| `logs`            | Either side's log, filtered                                  |

Those glue steps are where the hand-written version actually went wrong: a
`pkill` pattern that matched its own command line, a readiness check that passed
on a killed process's leftover heartbeat, and a capture that reported success
while the thing it photographed had never drawn.

That row used to promise a PNG header check. There was never one, in any
version — the file is waited for and renamed, never opened. It is written down
here rather than quietly corrected because a README is read by people deciding
what they no longer have to check themselves, which makes an imagined guarantee
worse than an absent one.

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

## Requirements

- Python 3.12+
- tModLoader installed
- A mod embedding the trigger-file responder (currently Biomancy's
  `DevCapture` / `FrameShot` / `DiagReport`)

## Configuration

Every path is an environment variable with a default:

| Variable                    | Meaning                                      |
| --------------------------- | -------------------------------------------- |
| `TMODLOADER_DIR`            | tModLoader install                           |
| `TMODLOADER_SAVE_DIR`       | Save directory the mod writes artifacts into |
| `TMODLOADER_MOD_SOURCE`     | Mod source directory (WSL path)              |
| `TMODLOADER_MOD_SOURCE_WIN` | Usually leave unset — see below              |

`TMODLOADER_MOD_SOURCE_WIN` is the mod source as Windows sees it, which `-build`
needs because tModLoader compiles inside a Windows process with no `/mnt/c`. It
is **derived** from `TMODLOADER_MOD_SOURCE`, so setting that one is enough for a
source on a drive mount. Set it yourself only if your mod source lives outside
`/mnt/<drive>`, where there is no drive letter to translate to and the server
will ask for it by name.

The two describe one directory. If you set both to different places the server
refuses to start and says so, rather than driving one and building the other.

## Phase 2 — what would make this yours

1. Extract the mod-side responder into a package any mod can depend on, rather
   than requiring Biomancy's copy.
2. Make `TMODLOADER_MOD_SOURCE` required and drop the machine-specific defaults.
3. A template mod and documentation.

Until then this is honestly a tool for one mod that happens to be built to
generalise.

## Licence

MIT.
