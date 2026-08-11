# The mod-side contract

What a mod has to do to be driven by this harness. Everything here is
observable — it was written from `triggers.py`, `session.py` and the actual
files a running Biomancy install had on disk, not from an intention.

**[`responder/`](../responder/) is the reference implementation of this
document** — a folder you copy into your mod and subclass. Read this if you are
writing a responder yourself or want to know why a filename is what it is;
vendor the folder if you just want your mod driven. They are kept adjacent
deliberately: a contract with its implementation in the same repository is a
contract somebody has actually compiled.

This document came first, when the responder lived inside one mod's C# and was
the only thing standing between "works for Biomancy" and "works for yours". It
was the specification the extraction had to satisfy, which is why it is exact —
and it stays, because the folder answers "what do I copy" while this answers
"what is the protocol", and a vendored implementation is not a substitute for
being able to read the wire.

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

## The filenames

Every name is built from the mod's **internal name, lowercased**. tModLoader
takes that name from the source folder, so a mod in `ModSources/Biomancy`
writes `biomancy-*`.

Two clients can now be on one machine at once (see
[Two clients at once](#two-clients-at-once) below), and their **answers** have
to be told apart the same way their **requests** already could be. So a
client's per-reply files carry a **player token** — everything it writes back
_about_ its own player — while the files both clients poll or share stay
exactly one name:

| File                        | Written by  | When                                   | Per player?    |
| --------------------------- | ----------- | -------------------------------------- | -------------- |
| `<mod>-commands.txt`        | mod         | once, at load                          | no             |
| `<mod>-hooks.txt`           | mod         | continuously, until a character exists | no (see below) |
| `<mod>-hooks-<token>.txt`   | mod         | continuously, once a character exists  | **yes**        |
| `<mod>-capture.trigger`     | **harness** | per request                            | no             |
| `<mod>-capture-<token>.txt` | mod         | per request, in reply                  | **yes**        |
| `<mod>-diag-<token>.txt`    | mod         | when asked                             | **yes**        |
| `<mod>-shot-<token>.png`    | mod         | when asked                             | **yes**        |

The prefix must be letters and digits only. It lands in filenames and inside a
regular expression, so a separator would build a path rather than a name and a
dot would widen the pattern. The token sits **before the extension** — after
it, `biomancy-diag.txt-n43n-003f` stops being a text file to anything that
reads extensions.

### The player token

A character name is turned into a token by: lowercasing it, collapsing every
run of non-alphanumeric characters to a single `-`, trimming leading and
trailing `-`, then appending `-` plus the **first four hex characters of the
MD5 of the ORIGINAL name's UTF-8 bytes** — not the slug, the name as typed. The
hash is always appended, not only on collision: whether two names would clash
is a question neither side can answer alone, since the mod knows one player
and the harness knows one player, and a rule with a branch needs both sides to
agree about when to take it.

A name that slugs to nothing — pure punctuation — becomes `player-<digest>`
rather than a bare digest, because the bare form does not match the grammar
below and a token the grammar rejects is a file that discovery cannot see: a
client that vanishes from `heartbeat` rather than one that is reported wrong.

The result matches:

```
[a-z0-9][a-z0-9-]*-[0-9a-f]{4}
```

pinned as `PLAYER_TOKEN_GRAMMAR` in `triggers.py`, and imported everywhere
else a token has to be recognised rather than re-derived, so the boundary
against an adjacent capture index stays the one grammar instead of two
hand-written copies of it.

Three computed examples:

| Name       | Token           |
| ---------- | --------------- |
| `n43n`     | `n43n-003f`     |
| `Big Bird` | `big-bird-44a3` |
| `BigBird`  | `bigbird-ca4c`  |

`Big Bird` and `BigBird` land on different tokens even though they slug to
similar text, because the hash is taken from the ORIGINAL bytes — the
punctuation the slug throws away is still what the digest sees.

### What deliberately stays shared

`<mod>-capture.trigger` and `<mod>-commands.txt` are **not** per player, and
that is load-bearing rather than an oversight:

- **The trigger** is how one client learns a request is aimed at somebody
  else. `DevResponder` leaves a trigger it is not addressed by exactly where
  it is, unconsumed, so the intended client finds it on its own next poll. A
  per-player trigger would give each client its own copy of the request and
  delete the one part of addressing that already worked — the mod's whole
  addressing check depends on every client polling the **same** file and
  deciding for itself whether a request is its own.
- **The command list** describes the channel, not an answer on it. Both
  clients serve the same verbs from the same running mod, so a second copy
  would only be the first one repeated under a different name.

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

## The heartbeat

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

**A CLIENT writes `<mod>-hooks.txt` (no token) for as long as it has no local
character, and `<mod>-hooks-<token>.txt` from the moment one loads.** This is
not a legacy name kept around for compatibility — it is the one heartbeat form
that means "this client is up and has not got a character yet", and a world
becomes ready at exactly the moment a character exists, so `launch`'s
readiness wait watches BOTH names for exactly this reason: whichever one this
client is currently writing is the one that will flip to `world-ready: True`.
A dedicated server never has a player, so it never writes a tokened heartbeat
at all — only the `-server` suffix from
[Where the files live](#where-the-files-live) applies to it.

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

This is the one artifact that stays a **single shared name even with two
clients** — see [What deliberately stays shared](#what-deliberately-stays-shared)
for why an addressed trigger still has to be one file both clients poll.

## The reply

One line, written to `<mod>-capture-<token>.txt` — or, before this client has
a character, `<mod>-capture.txt`; the same fallback described for the
heartbeat applies to every answer file, not only it. The first word decides
how the reply is read:

```
SHOT: C:\Users\...\biomancy-shot-n43n-003f.png
ERROR something went wrong
REFUSED not while a boss is alive
```

`ERROR` and `REFUSED` are both failures, and they are **reported separately
from success rather than folded into it** — a refusal is the mod deliberately
saying no, and treating that as success is how a rejected action reads as a
completed one.

The harness waits until the file's contents **stop changing** rather than for a
fixed interval, and treats an empty file as still being written.

## The state dump

Written to `<mod>-diag-<token>.txt` (or `<mod>-diag.txt` before a character
exists — see [The reply](#the-reply)). `key: value` lines, with **indented
lines** under a key forming a list:

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

## The drop box

`shot`'s single fixed filename that each capture overwrites —
`<mod>-shot-<token>.png` (or `<mod>-shot.png` before a character exists). The
harness renames it to `<mod>-shot-<token>-<index>-<region>.png` after each
request, which is why three captures in a row do not all end up pointing at
one file, and why two clients each calling `shot` do not end up pointing at
each other's — the token makes the drop box itself per-client, not just the
history built from it.

This does **not** apply to the `capture` verb, which uses Terraria's own
capture camera rather than `shot`'s back-buffer read and writes into a
directory the mod does not name — see
[Two clients at once](#two-clients-at-once).

**Read the game's own back buffer, not the screen.** OS-level capture was tried
first here and returned a picture of a Discord window sitting in front of the
game, having passed every check available. A back-buffer read cannot contain
another window by construction rather than by luck — and that guarantee is the
reason this project exists.

## Two clients at once

The trigger payload can be **addressed** — `shot@n43n` asks one player by name,
and the mod is expected to ignore a request aimed at somebody else. That much
already worked before this section changed, and it is why `trigger` and `diag`
take a `target`. tModLoader will happily start a second client: it is launched
directly as `dotnet tModLoader.dll -join ...`, not through Steam, so nothing
stops a second process joining the same server.

What used not to work was two clients **answering** at once. The diagnosis
that used to live in this section is still the reason the naming above exists,
so it is kept rather than deleted: **everything a client wrote was namespaced
by side, not by player**. Two clients shared one `<mod>-hooks.txt`, one
`<mod>-capture.txt`, one `<mod>-diag.txt` and one `<mod>-shot.png`. Requests
could be told apart — the trigger carries `@name` — and answers could not: a
heartbeat could not say which client was alive, and two captures overwrote one
drop box. The fix is everything above this section: a client's ANSWERS now
carry its player token, on both sides of the contract at once.

**One name still deliberately survives unsuffixed on purpose, not as a gap:**
`<mod>-hooks.txt`. A client that is up but has no character yet writes it, and
a client that later gets a character writes `<mod>-hooks-<token>.txt` instead
— see [The heartbeat](#the-heartbeat). Both the trigger and the command list
stay shared too, for the reason given in
[What deliberately stays shared](#what-deliberately-stays-shared) — none of
these three are the limitation this section used to describe; they were never
part of the ambiguity that namespacing removes.

### What is still open

Per-player naming was checked against `Artifacts` and the responder's own
sources, not yet against two real clients running at once. Three behaviours
are **predicted from reading the code, not yet observed**, and a live
two-client run is expected to settle them:

- **A single client may appear TWICE in `heartbeat`.** Nothing deletes
  `<mod>-hooks.txt` when a client starts writing its tokened name, and
  `heartbeat` discovery matches both the tokened and the unsuffixed form — see
  [The heartbeat](#the-heartbeat). One client could plausibly show up as a
  live tokened entry AND a plain entry aging past the 45-second staleness
  window.
- **An UNTARGETED request may be answerable by either client.** The mod's
  addressing check accepts any client when a trigger names no `@player`, while
  a caller waiting on a reply is watching its own session's per-player answer
  file. Which client actually answers an unaddressed request with two clients
  running is not yet known.
- **The `capture` verb may report the wrong client's picture.** It uses
  Terraria's own capture camera, which writes into a directory the mod does
  not name, and the mod finds the result by picking the largest NEW `.png`
  under the shared save directory — see [The drop box](#the-drop-box) for why
  this is unrelated to `shot`'s per-player drop box. Per-player naming does
  nothing to fix this and may make it MORE likely to surface, since two
  clients capturing near-simultaneously is now something this project can
  actually produce.

This subsection is written to be replaced, not append to indefinitely: once a
live run has watched each of these happen or not, that observation belongs
here in place of the prediction, not beside it.

## What the harness clears

Before every launch it deletes the six unsuffixed names, both sides, and —
when a player is given — that launch's own per-player names. A heartbeat or a
command list left by a previous run is exactly what lets a readiness check
pass against a process that is no longer there. A DIFFERENT player's leftover
files are deliberately left alone: they are not this launch's to delete, and
`heartbeat` reports their age rather than treating their mere existence as
"live", so a stale one reads as stale rather than as a phantom client.
