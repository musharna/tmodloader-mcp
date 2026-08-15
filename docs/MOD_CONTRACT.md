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

**CASE IS PART OF THAT, AND ADDRESSING IS NOT.** `n43n` and `N43N` slug the
same and digest differently — `n43n-003f` against `n43n-b6ff` — while the
addressing check in [the trigger](#modcapturetrigger--the-request) compares
names case-insensitively. So a client answers a target it does not spell the
same way, and then writes under the spelling it does. **Address a client by
its character name exactly**; the harness can only resolve this for its own
session's player, whose spelling it was given. Both halves are deliberate: the
digest distinguishes names that differ only by case, and a mod refusing
`@N43N` from a developer typing quickly would be its own kind of wrong.

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
- **`<mod>-capture.lock` and `<mod>-capture.stamp`** are shared the same way,
  and for the same reason — but they are not part of this protocol at all.
  The mod never writes or reads either; they exist purely between two of this
  harness's own sessions, to keep one from capturing in the second the other
  already claimed. See [What the harness clears](#what-the-harness-clears)
  for what they do and why.

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

**Read the untokened client heartbeat as a slot, not as a client.** Nothing
deletes it when its writer moves to a tokened name, and every client that
boots writes it again on the way through the menu — so with two clients it
holds ONE record, belonging to whoever started last, frozen at whatever it
said then. Measured: `polls: 1`, unchanged, still reading `live` for 45
seconds after the client it came from had been in the world for a minute. Use
`dedServ`, `polls` and the token to decide what is really running; see
[What a live two-client run found](#what-a-live-two-client-run-found).

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

**The harness writes it atomically** — staged under another name and _linked_
into place — because a polled file can otherwise be read half-written, and a
truncated command word is not an error on your side. It parses as unknown, you
do nothing, and the harness waits out its timeout for a reply to a request that
was thrown away. A hang, reported as a hang, caused by a partial write. The
link is also the claim: it succeeds only when the name is free, so the same
operation that makes the write atomic is what refuses to overwrite a pending
request — see the rule below. The staging name is removed once the link lands,
whether it wins the name or not.

**A command you do not serve must be refused, not guessed at.** Falling back to
some default action makes a typo look like a success.

This is the one artifact that stays a **single shared name even with two
clients** — see [What deliberately stays shared](#what-deliberately-stays-shared)
for why an addressed trigger still has to be one file both clients poll.

**One name means one slot: it holds a single request, not a queue.** So the
harness CLAIMS it rather than writing it — an exclusive create (`os.link`, or
`O_CREAT|O_EXCL`) that fails when the name is taken, retried until the slot
frees or the caller's timeout is spent. Two rules follow, and both are
load-bearing:

- **A claim that finds the slot occupied must not delete what is there.** That
  request may belong to another session, and deleting it is the overwrite this
  rule exists to prevent, wearing a friendlier name. Report it instead — naming
  the pending payload and its age, since neither the caller's own request nor
  the game is what is wrong.

  **Report only what the trigger shows.** The address it carries is the only
  evidence of who is waiting on it, and nothing validates that an address names
  a live client — so a message asserting "another session's request is pending"
  is a guess, and about a caller's own typo'd `target` it is a wrong guess that
  sends them hunting a session that does not exist. A payload addressed to the
  reporting session's OWN player is the one case that is certain, and it comes
  with a remedy: see [What the harness clears](#what-the-harness-clears).

- **The claim and the reply share one budget.** A caller asking for an answer
  within N seconds did not ask for N waiting to ask plus N waiting to hear, so
  the claim returns what is left of the timeout and a claim that consumed the
  whole budget fails as a claim rather than reporting a game that was never
  involved.

  Scoped to ONE request, which is what this rule can promise: a tool that
  issues a request and then waits for a SECOND file — the state dump, the drop
  box — spends its timeout on the trigger-plus-reply round trip and then waits
  up to that timeout again for the file, so an end-to-end call can take roughly
  twice what it was given. Documented rather than tightened, because the second
  wait is for a file the mod writes after answering and the reply is not
  evidence it has landed.

  `capture` spends that same one budget on more than the trigger-plus-reply
  round trip: it must first take `<mod>-capture.lock` (see
  [Two clients at once](#two-clients-at-once)), then possibly wait out
  whatever remains of the previous capture's second — capped at one second,
  `STAMP_WAIT_MAX` — before it ever claims the trigger. None of that doubles
  the budget the way the second-file case above does; it is still the ONE
  timeout the caller gave, spent across three phases instead of one.

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

### What a live two-client run found

Two clients, `n43n` and `tst2`, against one server — the case this whole
section is about, run for the first time on 2026-08-11. What follows is
**observed**, and replaces the three predictions that used to stand here.

**Addressing works, and each client answers as itself.** A `shot` addressed to
each in turn produced two distinct files with two distinct tokens and two
distinct sets of bytes, and neither client consumed the other's request. That
last part is earned rather than lucky: the responder checks addressing BEFORE
deleting the trigger, so a client that is not the addressee leaves the file
where its owner will find it. Keep that ordering if you write your own.

**A single client does appear twice, and the second entry is a phantom.** It
was predicted as a tokened entry beside a plain one decaying past the
45-second window. What actually happens is worse: the plain entry is a
_frozen_ main-menu observation — `polls: 1`, `gameMenu: True`,
`world-ready: False` — abandoned the moment the client got a character. So for
45 seconds `heartbeat` reports a second client that is "still starting up",
and after that a dead one, and neither ever existed. It is also **shared**:
the second client to boot overwrites it, so N clients produce ONE phantom,
belonging to whoever started most recently.

**An untargeted request was a coin flip, and this harness no longer sends one.**
`IsFor` returns true for every client when no `@player` is given, so all of them
qualify and whichever polls first deletes the shared trigger and answers. Run
twice in a row, a different client answered each time. **The mod's behaviour is
unchanged and is still the rule above** — a client must accept an unaddressed
request, because another harness may still send one. What changed is on this
side: every client request now carries the session's own player, so the race is
unreachable from here. A client that has not yet loaded a character has an empty
name, matches no target, and is therefore not askable; `heartbeat` answers for
it instead, off disk, without the game's cooperation.

**The trigger holds one request, and it is CLAIMED rather than written.** One
name is one slot, so a second request arriving before the first is read cannot
also be there. It used to overwrite it: both payloads reached disk intact, the
second replaced the first, and the loser's caller waited out its full timeout
with nothing on disk to explain why — measured as one capture answering in 1.2s
while the other timed out at 120s. A harness must now take the slot with an
exclusive create that fails when it is occupied, and wait for it to free rather
than replacing what is there.

**`capture` used to collide, and this run watched it happen before the fix.**
With the trigger claimed rather than overwritten, two simultaneous captures
both answered — 1.2s and 1.3s, neither timing out — but both `n43n` and
`tst2` reported the same picture: `PNG: C:\Users\...\Captures\Capture
2026-08-11 18_12_01.png`, and only one file existed on disk at that
timestamp. `capture` finds its result the way [The drop box](#the-drop-box)
already says: Terraria's own capture camera writes into a directory the mod
does not name, so `Begin()` lists that directory before queueing
`QuickScreenshot()` and `Settle()` lists it again once the wait is over;
`CaptureFind.PickNew` reports whichever `.png` is both new and, if more than
one is, largest. Nothing in that comparison looks at which client asked — it
cannot, because Terraria's camera does not tell the mod who it was writing
for, only a path. Two clients capturing inside the same wall-clock second
therefore contended for one filename, since that camera stamps its own name
to the second: whichever client's poll ran after the write saw exactly one
new file and reported it as its own, and the other client was told about a
picture it did not take, with nothing on disk to say so.

**Neither addressing nor namespacing could have fixed this.** Each client
lists the captures directory into its own `_before` snapshot, so each sees
that single written file as new relative to itself, and there is no name
here that the harness or the mod chooses — `capture`'s filename is Terraria's
alone, stamped to the second before the mod ever sees it. `shot` does not
have this failure mode, and the reason is the same one upstream of anything a
token could fix: its drop box is a name the mod itself picks
(`AnswerName(Names.Shot)`, suffixed per player — see
[The drop box](#the-drop-box)), while `capture`'s filename is not.

**What stops it is a second claim, harness-side, taken BEFORE the trigger.**
`<mod>-capture.lock` is shared like the trigger and carries no player token —
being shared is the point, since what it serialises is one save directory's
worth of Terraria captures, not one player's. It is claimed with the same
`os.link` primitive the trigger already uses (`_claim_atomically`): atomic,
and it refuses an occupied name rather than overwriting it. Taking it before
the trigger claim, always, is what makes deadlock impossible — a session
waiting on the lock is by construction not holding the trigger, so whoever
does hold the trigger always finishes and releases both. It is released in a
`finally`, so a timeout or a refusal cannot wedge captures for either session.

Serialising the requests is not sufficient by itself: a capture that finishes
at `18:12:01.05` and one whose reply lands at `18:12:01.95` never overlap and
would still collide. So on release the holder writes `<mod>-capture.stamp` —
the time its reply arrived, no player token, shared like the lock — and
returns immediately; the NEXT claimant waits out whatever is left of that
second, capped at one second (`STAMP_WAIT_MAX`), before it captures. The cost
of the boundary lands on the contender rather than on a session working
alone, which pays nothing.

**The lock says when its holder will be gone, and that is the bound.** It
carries two lines — the holder's pid, then the wall-clock moment its whole
`ask` runs out:

```
17244
1755230489.271833
```

Once that moment passes (plus `CAPTURE_LOCK_GRACE`, ~2s of slop for the
holder's own release and for the offset between the two clocks involved) the
lock may be broken. The deadline is true by
construction rather than by assumption: `ask` spends ONE budget across the
lock claim, the boundary wait, the trigger claim and the reply wait, so a
lock taken now cannot outlive now plus the caller's `timeout`.

Wall clock rather than monotonic, deliberately — the reader is a different
process, and the two other things anyone compares against this file, the
stamp and the lock's own mtime, are already wall clock.

`CAPTURE_LOCK_STALE` (60s — four times the mod's own ~15s settle window)
survives as the fallback for a lock that says nothing readable: one written
by an older version carrying a bare pid, one caught mid-write, one edited by
hand. The parse never raises and never accepts `nan` or `inf` as a promise —
a NaN compares false against everything and would silently disable the bound,
and an infinity would protect the lock forever.

**A deadline is believed for at most `CAPTURE_LOCK_MAX` (10 minutes) past the
claim it belongs to**, which is the same guard `STAMP_WAIT_MAX` puts on the
stamp and exists for the same reason. A lock claimed while the clock ran
ahead records a budget nobody meant; honouring it literally would protect
that lock for as long as the error lasted, wedging captures for BOTH sessions
until somebody deleted a file by hand. That trades this mechanism's bounded
failure — a collision — for an unbounded one, which is a bad trade at any
odds. The ceiling is anchored to the lock's mtime rather than to the reader's
clock, because an anchor that moves with the reader can always be outrun.

**This replaced a bound that was a guess in both directions.** Until
2026-08-15 the rule was age alone, so a capture given `timeout=120` — which
the `trigger` tool's own advice about large worlds encourages — was still
LIVE with a lock past 60s, and a second session broke it and captured into
the very window the lock exists to keep clear, through a supported parameter
with no error and no warning. The same guess ran the other way too: a dead
capture whose caller asked for five seconds wedged the other session for a
full minute.

The caller is told via a `note` field on the reply, and the note NAMES WHICH
BOUND FIRED, because the two are worth different amounts. A holder that
recorded a deadline and blew through it is a fact about that session; a lock
broken on age alone said nothing at all, and 60s is a guess about how long a
capture can take. Breaking wrongly here costs only a collision, which is
exactly 0.3.0's shipped behaviour — this does NOT carry over to the trigger
claim, where breaking wrongly destroys a request somebody is waiting on.
Client side only: the mod refuses `capture` on a dedicated server, so a
server-side lock would serialise against nothing.

**A stated residual: a capture that times out releases its lock while a PNG
may still be on its way.** The lock is released in a `finally`, so when the
wait for the reply runs out the release happens then — but the game has the
request, the mod deletes a trigger before dispatching it, and Terraria may
write the picture seconds later with the lock already gone. Another session
can take the freed lock in that window and land in the same second after all.
This is deliberate and it is the safe side of the trade: holding a lock until
a reply that may never come would wedge captures for both sessions on every
timed-out request, which is a certain outage traded against a rare collision.
The stamp is written on that path too, so the next claimant still misses the
second the timeout happened in — it just cannot know about a write that had
not happened yet. This case is reached whenever the reply wait runs out,
whatever timeout the caller gave — see [the shared-budget
rule](#modcapturetrigger--the-request): `capture` spends that ONE timeout
across the lock claim, the boundary wait, the trigger claim and the reply
wait, so contention on the lock alone can consume most of even a generous
timeout before the reply wait ever starts. Worked case: `ask("capture",
timeout=60)` while another session holds the lock for 56s reaches the trigger
claim with roughly 4s left; if the reply is not in by then, the `finally`
still releases the lock while the game may still be about to write the PNG —
this same case, from a caller who gave four times the timeout of the caller
who got there in 4s flat. A LONGER timeout does not exempt a caller from this
residual, and can make it worse: the lock stays claimed for longer while the
extra time is spent on contention the caller cannot see. The timed-out caller
is told nothing was confirmed, which is already true of any timeout here.

Distinct from that, and not a residual: a capture whose budget is used up
BEFORE anything is asked — by waiting for the lock, or by waiting out the
previous capture's second — fails with the lock released and NOTHING on the
trigger. There is no picture in flight in that case, and the error says so in
those words.

Whether the trigger should become a queue is a real question and a separate
one. [What deliberately stays shared](#what-deliberately-stays-shared) argues
for one trigger from ADDRESSING — every client must be able to see a request
to know it is not theirs — and that argument is sound and says nothing about
two requests at the same instant.

## What the harness clears

Before every launch it deletes the five unsuffixed game-written names, both
sides, and — when a player is given — that launch's own per-player names. A
heartbeat or a command list left by a previous run is exactly what lets a
readiness check pass against a process that is no longer there. A DIFFERENT
player's leftover files are deliberately left alone: they are not this launch's
to delete, and `heartbeat` reports their age rather than treating their mere
existence as "live", so a stale one reads as stale rather than as a phantom
client.

**The trigger is not one of them.** It is one of three names shared with the
OTHER session rather than merely with a previous run of this one — the trigger,
`<mod>-capture.lock` and `<mod>-capture.stamp`, all three excluded from the
clear for that reason and the other two covered below. Deleting the trigger on
the way in or out is the same overwrite the
[claim rule](#modcapturetrigger--the-request) exists to prevent, committed by
the housekeeping instead of by the write. `launch` and `stop` both used to do
it unconditionally, which meant one developer starting a game destroyed the
other's in-flight request while their game was still polling for it.

So both RELEASE it rather than delete it: the file goes only when what it holds
is that session's to take back — a request addressed to that session's own
player, one carrying no address at all, or one no parser can read. A request
addressed to anybody else stays exactly where it is, which is the same rule the
mod itself follows when it declines to consume a request it is not addressed
by. An unreadable or unaddressed payload counts as the session's own because
nothing will ever collect it either way, and a slot that holds one request
cannot afford to hold it forever.

That release is also the only way an UNCONSUMABLE request leaves the slot.
Nothing validates that a target names a live client, so a typo — or a client
that has not loaded a character, whose name is empty and matches no target —
parks a request no client will ever take. A fresh `launch` from the session
that addressed it clears it; deleting the file by hand always works.

**`<mod>-capture.lock` and `<mod>-capture.stamp` are excluded the same way,
for the trigger's reason.** Both are shared with the OTHER session rather than
with a previous run of this one — the lock while a capture is in flight, the
stamp for a short while after — so an unconditional launch-time delete would
take the lock out from under a session capturing right now: the same lost
update the trigger exclusion exists to prevent, arriving through a second
file. A lock a DEAD run left behind is told from one a LIVE session holds by
the deadline the holder wrote into it — falling back to age for a lock that
recorded none — and a launch makes that call: after the two clears above, and
for both sides, it breaks the capture lock if and only if that rule says the
holder is gone. Nothing else about a launch is allowed to touch either file.
Neither file is ever written or read by the mod — both are held entirely
between two of this harness's OWN sessions, to keep them from capturing in the
same wall-clock second, before either one writes a trigger.
