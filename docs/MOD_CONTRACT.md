# The mod-side contract

What a mod has to do to be driven by this harness. Everything here is
observable — it was written from `triggers.py`, `session.py` and the actual
files a running Biomancy install had on disk, not from an intention.

This exists because the responder currently lives inside one mod's C#. Until it
is extracted into something you can vendor (Phase 2), this document is the only
thing standing between "works for Biomancy" and "works for yours". It is also
the specification that extraction has to satisfy, so it is worth being exact.

## Where the files live

All of them sit directly in the **save directory** — the same folder that holds
`Worlds/`, `Players/` and `Mods/`. Not a subfolder, because the mod writes them
from a context where the save path is the thing it reliably knows.

Both sides of a session share that one directory, so the **server's copies are
suffixed**: the suffix goes before the extension, and a name with no extension
gets it appended.

```
biomancy-hooks.txt          <- client
biomancy-hooks-server.txt   <- server
```

Get that rule wrong in either direction and a harness reads the other side's
answer without being able to tell.

## The six filenames

Every name is built from the mod's **internal name, lowercased**. tModLoader
takes that name from the source folder, so a mod in `ModSources/Biomancy`
writes `biomancy-*`.

| File                    | Written by  | When                        |
| ----------------------- | ----------- | --------------------------- |
| `<mod>-commands.txt`    | mod         | once, at load               |
| `<mod>-hooks.txt`       | mod         | continuously, while ticking |
| `<mod>-capture.trigger` | **harness** | per request                 |
| `<mod>-capture.txt`     | mod         | per request, in reply       |
| `<mod>-diag.txt`        | mod         | when asked                  |
| `<mod>-shot.png`        | mod         | when asked                  |

The prefix must be letters and digits only. It lands in filenames and inside a
regular expression, so a separator would build a path rather than a name and a
dot would widen the pattern.

## `<mod>-commands.txt` — what you serve

Written **at load, unasked**. Tab-separated, `#` comments ignored:

```
# commands served by this responder, written at load
# name	arg|noarg	summary
shot	arg	Save a PNG of one region of the frame, from the back buffer.
diag	noarg	Write this side's state dump, from a live session.
```

The middle column says whether that command **reads** an argument. It is not
decoration: the harness refuses an argument for a command that would discard
it, and refuses a missing one for a command that needs it, so a caller learns
from the list instead of from a round trip.

Each side publishes its own list, because server-authoritative commands are not
the same set as client ones.

**This file's absence is meaningful.** The harness clears it before launching,
so its reappearance proves the mod loaded _in this run_. Nothing published
means no responder is running — a different answer from a game still starting,
and the harness reports it as such rather than waiting out a timeout.

## `<mod>-hooks.txt` — the heartbeat

Written repeatedly while the mod ticks. `key: value`, one per line:

```
hooks-seen: PostUpdateEverything,PostUpdateInput,UpdateUI
gameMenu: False
dedServ: False
savepath: C:\Users\...\tModLoader
trigger-path: C:\Users\...\biomancy-capture.trigger
trigger-exists: False
world-ready: True
capture-ready: True
armed: True
polls: 194
written: 20:19:07Z
```

The harness treats a heartbeat as live only if it was written within **45
seconds**, and separately asks whether `world-ready` is true. Both, because
they fail differently: stale-but-ready means the process died, fresh-but-not-
ready means it is still loading, and one boolean cannot say which.

Four fields carry most of the diagnostic weight:

- `dedServ` — which side wrote this. The harness trusts this over the filename.
- `world-ready` — a world is loaded and the mod can act on it.
- `armed` — the trigger responder is listening. A mod that is loaded and
  ticking with `armed: False` will never answer, and that is a distinct fault.
- `polls` — a counter that must advance. It is how "running" is told from
  "running and stuck".

Booleans are written as C# `bool.ToString()` gives them: `True` / `False`.

## `<mod>-capture.trigger` — the request

The **harness** writes this; the mod polls for it, acts, and deletes it. The
payload is one line:

```
shot                 command alone
shot:topleft         command with an argument
shot@n43n            addressed to one player
shot:topleft@n43n    both
```

Two constraints that are not optional:

**The harness writes it atomically** — staged under another name and renamed
into place — because a polled file can otherwise be read half-written, and a
truncated command word is not an error on your side. It parses as unknown, you
do nothing, and the harness waits out its timeout for a reply to a request that
was thrown away. A hang, reported as a hang, caused by a partial write.

**A command you do not serve must be refused, not guessed at.** Falling back to
some default action makes a typo look like a success.

## `<mod>-capture.txt` — the reply

One line. The first word decides how it is read:

```
SHOT: C:\Users\...\biomancy-shot.png
ERROR something went wrong
REFUSED not while a boss is alive
```

`ERROR` and `REFUSED` are both failures, and they are **reported separately
from success rather than folded into it** — a refusal is the mod deliberately
saying no, and treating that as success is how a rejected action reads as a
completed one.

The harness waits until the file's contents **stop changing** rather than for a
fixed interval, and treats an empty file as still being written.

## `<mod>-diag.txt` — the state dump

`key: value` lines, with **indented lines** under a key forming a list:

```
version: 0.8.1
side: client netmode=1
npcs: active=2 mutated=1
  idx=0 type=37 name=Old Man life=250/250 at=770,231
  idx=1 type=22 name=Guide life=250/250 at=2171,253
creep-tiles: 47
strains: N/A (never sent to clients)
```

Three rules the parser depends on:

- **Counters are recognised by the shape of the value, not by name.** Write a
  plain integer and it arrives as an integer, whatever you called the field. A
  new counter needs no change here.
- **"Nothing to report" has its own spellings**: `NONE`, `N/A (...)`. They
  become null, so a genuine reading of `0` cannot be confused with no data.
- **A key may repeat its meaning in a composite value** (`creep-residue: 0=35
2=12`) and it stays a string. Only bare integers become numbers.

## `<mod>-shot.png`

A single fixed filename that each capture overwrites. The harness renames it to
`<mod>-shot-<index>-<region>.png` after each request, which is why three
captures in a row do not all end up pointing at one file.

**Read the game's own back buffer, not the screen.** OS-level capture was tried
first here and returned a picture of a Discord window sitting in front of the
game, having passed every check available. A back-buffer read cannot contain
another window by construction rather than by luck — and that guarantee is the
reason this project exists.

## What the harness clears

Before every launch it deletes all six. A heartbeat or a command list left by
a previous run is exactly what lets a readiness check pass against a process
that is no longer there.
