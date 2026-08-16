# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

This file was backfilled at #12 from the merged history rather than kept from
the start, so entries within a release are grouped by what they do rather than
dated individually; the PR numbers are the audit trail. Everything up to 0.1.0
predates any tag, so the "breaking" notes below describe changes nobody could
have been depending on — they are recorded because the reasoning is worth
keeping, not because they broke a released API.

## [Unreleased]

### Fixed

- **`diag` and `shot` spend ONE budget across both of their waits.** Each
  issues a request and then waits for a second file — the state dump, the drop
  box — and each handed the caller's whole `timeout` to both, so a call asked
  to take at most 60s could legitimately take 120. The tool text was already
  the honest version: `shot`'s `timeout` read "seconds to wait for the reply
  and the PNG", which described neither wait alone and nothing at all
  together. A budget the reply consumes now fails naming the BUDGET, rather
  than passing a zero down to a wait whose own error ("the game may not be
  polling — check that a world is loaded") blames a game that just answered.

- **Releasing a capture lock is a protocol, and three parts of it were
  unstated.** The stamp is written BEFORE the lock is unlinked, and reversing
  the two still passes every test — what changes is invisible from one
  session: the unlink frees the name, a session already polling takes it in
  that instant, and reads the second the PREVIOUS capture left behind, so both
  PNGs land together. That is this feature's whole failure mode, reached
  through the release rather than the claim. The unlink also sat unguarded in
  a `finally`, where an exception does not join what is in flight but REPLACES
  it: a capture that took its picture and got its reply came back as a
  `PermissionError` about a lock file, and one that timed out lost the error
  explaining why. It is reported through the reply's `note` now instead. And
  the lock is given back on the interrupt path — the holder spends nearly all
  its wall clock asleep on the second boundary, which is the one way the lock
  outlives its session with the session still alive.

- **The capture lock's staleness bound is the holder's own deadline, not a
  60-second guess.** Until now the only signal was the lock's mtime, and it
  was wrong in both directions. A capture given `timeout=120` — which the
  `trigger` tool's own advice about large worlds encourages — was still LIVE
  with a lock past 60s, so a second session broke it and captured into the
  very window the lock exists to keep clear: the collision this whole
  mechanism removes, reached back through a supported parameter with no error
  and no warning. The same guess ran the other way too, leaving a dead capture
  whose caller asked for five seconds to wedge the other session for a full
  minute.

  The lock already carried content nobody read — a pid. It now carries the
  holder's deadline on a second line, and that deadline is true BY
  CONSTRUCTION rather than by assumption: `ask` spends one budget across the
  lock claim, the boundary wait, the trigger claim and the reply wait, so a
  lock taken now cannot outlive now plus the caller's `timeout`. Wall clock
  rather than monotonic, because the reader is a different process and the two
  other things compared against this file — the stamp and the mtime — are
  already wall clock; one protocol file with two clocks in it is a trap for
  whoever reads it next.

  `CAPTURE_LOCK_STALE` survives as the fallback for a lock that says nothing
  readable: an older version's bare pid, a write caught half-finished, a hand
  edit. The parse never raises, and never accepts `nan` or `inf` as a promise
  — a NaN compares false against everything and would silently disable the
  bound, while an infinity would protect the lock forever. A new
  `CAPTURE_LOCK_GRACE` (~2s) covers the holder's own release and DrvFs
  timestamp granularity; it is documented as slop, not as safety.

  A deadline is believed for at most `CAPTURE_LOCK_MAX` (10 minutes) past the
  claim it belongs to — the same guard `STAMP_WAIT_MAX` already puts on the
  stamp. Without it, a lock claimed while the clock ran ahead records a budget
  nobody meant and is protected for as long as the error lasts, wedging
  captures for both sessions until somebody deletes a file: this mechanism's
  bounded failure traded for an unbounded one. The ceiling is anchored to the
  lock's mtime rather than to the reader's clock, since an anchor that moves
  with the reader can always be outrun.

  The reply's `note` now names WHICH bound fired. "The last holder promised to
  be gone and was not" and "your picture waited on a guess" send a reader to
  different places, and the old wording claimed the first while often meaning
  the second.

  Unchanged and still stated: a capture whose reply times out releases its
  lock while Terraria may still be writing the PNG. That residual is
  independent of the bound.

### Added

- **`tests/live_capture_check.py` — the collision, reproduced and closed
  against the real capture camera.** No test in `tests/` could reach this
  defect: Terraria names the PNG after the second it started writing it,
  before the mod is involved, so the collision only exists where Terraria
  does. Everything else here drives fakes or bare files and passes just as
  happily with the serialisation deleted.

  It launches a server and client, joins a second client, and fires `capture`
  from two separate OS processes through a barrier — processes rather than
  threads, because the lock under test is a filesystem claim BETWEEN sessions
  and two threads in one interpreter share state the real case does not.

  A pass proves nothing by itself, which is why the file documents its own
  negative control: point `PYTHONPATH` at a pre-fix checkout and run it again.
  Measured 2026-08-14 against `b2041a3` (0.3.0, serialisation absent), 0/6
  rounds passed — both callers named one file every time, and SIX PNGs existed
  on disk for TWELVE requests. Half the pictures were not misattributed but
  overwritten and lost. Serialised, on the same clients minutes apart: 3/3,
  one file per request.

  It also settled why a pre-fix run is FASTER (1.2s against 5-8s). The PNG's
  name is stamped when the capture starts and the write finishes seconds later
  — `Capture ... 23_43_59.png` landed at 23:44:06 — so before the fix the
  loser's `CaptureFind` returned the winner's file the moment it appeared. The
  slower serialised number is the honest one: it is a session waiting for a
  picture that is actually its own. The stamp is therefore conservative by a
  wider margin than its design assumed, recording a reply that arrives after
  the write completes, long after the name was chosen.

### Fixed

- **Two clients capturing in the same wall-clock second no longer collide.**
  Terraria's own capture camera names the output PNG and stamps that name to
  the second before the mod ever sees it, so two captures inside one second
  produced ONE file and both callers were told it was theirs — shipped as
  Known in 0.3.0 after a live two-client run reproduced it. Neither addressing
  nor namespacing could have fixed this: each client lists the captures
  directory into its own `_before` snapshot, so each sees that single written
  file as new, and there is no name here the harness or the mod chooses.

  The fix is a second claim, harness-side and taken BEFORE the trigger:
  `<mod>-capture.lock`, shared like the trigger and carrying no player token
  on purpose. It is claimed with the same `os.link` primitive the trigger
  uses, and taking it first is what makes deadlock impossible — a session
  waiting on the lock holds no trigger, so the trigger's holder always
  finishes. It is released in a `finally`, so a timeout or a refusal cannot
  wedge captures for both sessions.

  Serialising the requests is not sufficient on its own — a capture that
  finishes at `18:12:01.05` and one whose reply lands at `18:12:01.95` never
  overlap and would still collide. On release the holder writes
  `<mod>-capture.stamp` with the time its reply arrived and returns
  immediately; the NEXT claimant waits out whatever remains of that second,
  capped at one second (`STAMP_WAIT_MAX`). The cost lands on the contender
  rather than on a session working alone, which pays nothing.

  A lock older than `CAPTURE_LOCK_STALE` (60s — four times the mod's own
  ~15s settle window) is assumed to have no live capture behind it, so it is
  broken, and the caller is told via a new optional `note` field on the
  reply — breaking is a judgement made from age alone, worth saying out loud.
  Breaking wrongly costs only a collision, which is 0.3.0's shipped behaviour;
  the same rule is deliberately NOT applied to the trigger claim, where
  breaking wrongly would destroy somebody's in-flight request.

  The assumption holds only while the round trip fits inside
  `CAPTURE_LOCK_STALE`. The lock's mtime is set once, at claim time, and never
  refreshed while it is held; a caller may pass `ask` a `timeout` above 60s,
  and a capture legitimately spending most of it on contention can still be
  LIVE with a lock older than the bound — a second session then breaks it and
  captures into the same window this whole feature exists to close, through a
  supported parameter, with no error or warning. Not fixed here; see
  [`docs/MOD_CONTRACT.md`](docs/MOD_CONTRACT.md#two-clients-at-once).

  Client side only: the mod refuses `capture` on a dedicated server, so a
  server-side lock would serialise against nothing. The lock and stamp are
  both in `Artifacts.all` and excluded from the launch-time clear the same way
  the trigger is — see
  [`docs/MOD_CONTRACT.md`](docs/MOD_CONTRACT.md#what-the-harness-clears).

  One residual is stated rather than removed: a capture whose reply times out
  releases the lock in its `finally` while Terraria may still be writing the
  PNG. Holding the lock until a reply that may never come would wedge captures
  for both sessions on every timed-out request — a certain outage traded
  against a rare collision.

- **A capture whose whole budget went on the boundary wait no longer leaves a
  request on the trigger.** Found in review of the work above and reproduced
  with ONE session and no contention: a stamp from a capture that had just
  finished, then `trigger("capture", timeout=0.4)`. `_claim_capture` charged
  the boundary sleep after its last look at the deadline and handed back a
  `0.0` remainder anyway; `_claim` then took that as its budget, wrote the
  request to the trigger, and raised for having no time to wait on it. The
  `finally` released the capture lock with that request still sitting there.

  The mod deletes a trigger before dispatching it, so the game went on to
  serve the request and Terraria wrote a PNG WITH NO LOCK HELD — another
  session could take the freed lock, wait out a stamp written before that PNG
  existed, and land in the same second. The collision this release removes,
  reached through the mechanism that removes it.

  `_claim_capture` now applies to its own return the rule `_claim` already
  applied to itself: with less than `CLAIM_POLL` left, raise rather than hand
  on a remainder that cannot fund the next step. It releases the lock on that
  path itself, since `ask` only knows to release what this call RETURNED, and
  it reports in its own words — the borrowed message promised that "the game
  may still answer it", which named a request that was never written. Two
  neighbours in the same loop went with it: a stale-lock break skipped the
  deadline check every other cycle performs, and the contention message named
  a holder on a path where the lock had just been unlinked.

## [0.3.0] - 2026-08-12

The release where two people can drive one game directory at once. 0.2.0 gave
another mod the responder; this one stops two sessions using it from erasing
each other. Both halves of that were needed and neither is much use alone: a
client's ANSWERS had to stop sharing one filename, and the shared trigger had
to stop being a slot anyone could overwrite.

Verified with two real tModLoader clients rather than argued from the code —
which is how the sharpest defects here were found, several of them regressions
this work introduced and none of them visible to a suite that only ever ran
one client.

### Added

- **`responder/SHA256SUMS` — a fingerprint that ships with the folder.** The
  vendored copies in other people's mods are invisible to this repository, so
  nothing here could tell a mod that its copy had gone stale. This is not
  hypothetical: 0.2.0 shipped after two comments were edited upstream and had to
  be re-copied into the reference implementation BY HAND, which is exactly the
  step that eventually gets forgotten.

  One small file answers both questions a vendored copy raises —
  `sha256sum -c SHA256SUMS` says whether anybody edited the copy, and diffing it
  against upstream's says whether the copy is behind, without fetching nine
  sources to find out.

  Four tests keep the manifest true, because a manifest somebody updated once is
  worse than none: it would describe an older folder than the one beside it and
  report "in sync" to a consumer who was not. The coverage check is separate
  from the content check on purpose — a file ADDED upstream and omitted from the
  manifest leaves every recorded hash still matching, so a content-only check
  passes while consumers never learn the file exists. Both mutations were run
  and seen to fail the right test with the right message.

- **Per-player artifact naming — a client's ANSWERS carry a token.** Two
  clients on one machine could already have their REQUESTS told apart (a
  trigger can be addressed `shot@n43n`), but everything a client wrote back
  was namespaced by side only, so two clients shared one heartbeat, one diag
  dump, one capture reply and one shot drop box — see
  [`docs/MOD_CONTRACT.md`](docs/MOD_CONTRACT.md#two-clients-at-once) for the
  diagnosis this replaces.

  A character name is now turned into a token — lowercased, non-alphanumeric
  runs collapsed to one `-`, trimmed, plus `-` and the first four hex
  characters of the MD5 of the ORIGINAL name's bytes — matching
  `[a-z0-9][a-z0-9-]*-[0-9a-f]{4}` (`PLAYER_TOKEN_GRAMMAR` in `triggers.py`).
  A client's heartbeat, diag reply, capture reply and shot drop box are all
  suffixed with it once a character exists.

  What deliberately did **not** gain a token: `<mod>-capture.trigger` and
  `<mod>-commands.txt` stay one shared name each. The trigger's addressing
  only works because every client polls the SAME file and decides for itself
  whether a request is its own — a per-player trigger would give each client
  its own copy and remove the one part of two-client addressing that already
  worked. The command list describes the channel both clients share, not an
  answer on it.

### Changed

- **`heartbeat`'s shape can express two clients now, not one.** The single
  `client` key became a `clients` LIST, and each `HeartbeatSideOut` gained a
  `player` field. The old shape could not express two clients at once and
  silently reported whichever had written last — the same shared-file
  ambiguity per-player naming removes above, wearing a different hat, so
  shimming the old key would have kept the ambiguity while hiding that it was
  still there. **Breaking:** `HeartbeatOut.client` is gone; callers read
  `HeartbeatOut.clients`, a list.

### Fixed

Both of these were found by running two clients against one server for the
first time — the case per-player naming exists for, and one no test in this
repository had ever been able to express.

- **An answer is now awaited where the ADDRESSEE writes it.**
  `diag(target='tst2')` from a session whose player was `n43n` timed out after
  60 seconds while `tst2` answered correctly into its own files: the request
  was addressed and the reply path was not. This was a regression introduced
  by per-player naming itself, and an uncomfortable one — before it, every
  client wrote one shared reply file, so addressing worked BECAUSE answers
  were ambiguous, and removing the ambiguity broke it at the same stroke.

  The wrong coupling lived at three sites — the reply file, the diag dump and
  the shot drop box each derived their path from the session's player — so
  correcting only the one that failed would have left `diag` hanging on the
  dump and `shot` on the drop box. `Session._names` now takes the addressee,
  defaulting to the session's own player: an answer is written by the client
  the request named, under its token, so the path is a function of who was
  addressed rather than of who asked.

- **Two requests in flight no longer share one staging file.** The trigger is
  written atomically — staged beside the polled path and linked into place —
  but the staging name was derived FROM that shared path, so two concurrent
  writers shared it. The write was still a _rename_ when this was found: the
  last write won the contents, the first rename carried them away, and the
  second rename raised `FileNotFoundError` having already lost its payload —
  one request silently replaced by another's, in the one place this project
  exists to make unambiguous. The staging name is now unique per write, and a
  `finally` clears it on every write — not only a failed one — so nothing is
  left behind in the directory the game reads.

- **Two sessions can now issue requests at the same time.** The trigger is
  claimed rather than written: `os.link` is atomic exactly as `os.replace` was
  and additionally refuses an occupied name, so a second request waits for the
  slot instead of silently replacing what is in it. Measured before the fix as
  one capture answering in 1.2s while the other timed out at 120s having never
  had a request on disk.

  A blocked claim never deletes the request in its way — that would be the same
  overwrite under a friendlier name — so a trigger held by a client that will
  never consume it is reported, naming the pending request and its age. It does
  not name an OWNER: nothing validates that an address belongs to a live
  client, so a caller's own typo'd `target` used to be reported as "another
  session's request", which is the wrong culprit and the wrong remedy.

  `launch` and `stop` no longer delete the trigger unconditionally either. That
  was the same lost update arriving through the housekeeping — one developer
  starting a game destroyed the other's in-flight request while their game was
  still polling for it. Both now release it only where it holds a request
  addressed to their own player, unaddressed, or unparseable, which is also the
  only way a request nobody can consume ever leaves the shared slot.

- **An untargeted request is no longer a coin flip.** Every client request now
  carries the session's own player. The mod accepts an unaddressed request at
  any client, so with two clients up the answer went to whichever polled
  first — a different one on each of two consecutive attempts.

  **Behaviour change:** a client that has not yet loaded a character has an
  empty name, matches no target, and can no longer be asked anything. Use
  `heartbeat`, which reads off disk and needs no cooperation from the game.

- **A save directory that cannot support an exclusive claim is refused at
  startup**, naming the filesystem's reason, rather than falling back to the
  write that loses requests.

- **A pending request is now read the way the mod reads it.** The decision to
  delete a request out of the shared trigger treats an unreadable payload as
  this session's own, justified entirely by "the mod cannot read it either, so
  nobody will ever consume it". But `File.ReadAllText` has no such failure
  mode — it substitutes U+FFFD and parses on — so bytes invalid only inside the
  VERB still yield a clean target over there. This side gave up on them and
  called another client's live request its own. Reading with `errors="replace"`
  is this side making the same substitution, so both reach the same verdict on
  the same bytes; nothing this harness writes can produce such a payload, but a
  request typed from a shell or an editor with the wrong encoding can.

### Known

- **Two simultaneous `capture` requests can collide on one filename.** Once
  requests could actually be concurrent, a live two-client run produced it: two
  captures inside the same wall-clock second both answered with the identical
  path — `PNG: C:\Users\...\Captures\Capture 2026-08-11 18_12_01.png` from both
  `n43n` and `tst2` — and only one file existed on disk at that timestamp.
  Terraria's own capture camera names the file, stamped to the second, into a
  directory the mod does not control; `CaptureFind.PickNew` reports whichever
  new `.png` it finds, with no way to know which client's request produced it.
  `shot` does not share this failure — its drop box is a name the mod itself
  picks and suffixes per player. See
  [`docs/MOD_CONTRACT.md`](docs/MOD_CONTRACT.md#two-clients-at-once). This is
  mod-side (`responder/`) and out of scope for this repository's harness.

## [0.2.0] - 2026-08-10

The release where the mod-side half stopped being Biomancy's. 0.1.0 could tell
another mod what to implement; this one hands it the implementation.

### Added

- **The mod-side half is now yours: `responder/`.** The responder that answers
  every trigger used to live inside Biomancy, so what this project could offer
  another mod was a document to implement. It is now a folder you copy into your
  mod's source tree and subclass — nine `.cs` files, one abstract class, two
  overrides.

  It is source rather than a package because tModLoader has no dependency
  mechanism for compile-time C#; a `modReference` links a built `.tmod`, which
  is a different thing. Vendoring is not a shortcut around packaging here, it is
  what packaging would look like.

  **This was an extraction, not a rewrite.** The DevBridge layer had already
  been built to separate the harness protocol from Biomancy's verbs — the parse,
  the registry, the artifact naming and the capture geometry were generic
  before anything moved. What was not separable was the dispatch: the generic
  half and the mod's half were one enum and one switch, so neither could be
  lifted without the other coming along. `DevCommandRegistry` is what replaced
  that switch, and it is the reason the base class's three verbs and a mod's own
  can be published as one ordered list from two different classes.

  **What the compile line proves that a source scan cannot.** Reading the files
  and finding no `using Biomancy` is an argument. `responder/tests/` compiles
  them with nothing of any mod's on the compile line, on a CI runner with no
  tModLoader installed, on every push — so vendorability is checked by the
  compiler rather than asserted by a reviewer. Two files sit outside that
  project because they need Terraria, XNA and `ModSystem`, which no build runner
  has: they are covered by a source-scan contract test and by a live run against
  a real game, where the extracted responder answered on both the client and the
  dedicated server with its three verbs leading the published list.

  Biomancy now runs on the vendored copy rather than its own, which is what
  keeps the claim honest — the reference implementation is a consumer of this
  folder, not its owner. Its `DevCapture` went from 1442 lines to 678.

  **Not done: per-player artifact naming.** Two developers on one machine share
  a save directory, so they share every artifact filename. Designed in the same
  spec and deliberately left to its own change, so that a failure in the
  extraction had one candidate cause rather than two.

- **`docs/MOD_CONTRACT.md` — the mod-side protocol, written down.** Every
  filename, where it lives, what it contains, and which failures it has to be
  able to express. It existed only as C# inside one mod, which is what made
  "extract the responder" a job nobody could start: there was nothing to
  extract _to_. Written from `triggers.py`, `session.py` and the artifacts a
  running install actually had on disk rather than from intention, and pinned
  by tests in BOTH directions — one fails if the protocol gains a file the
  document does not mention, the other if the document describes a file nothing
  writes. Neither is sufficient alone: a spec can name every real artifact and
  several imagined ones. (#35)

### Fixed

- **`shot` reported success on a file it never opened.** The drop file was
  waited for, renamed, and its path handed back as a picture — not one byte was
  read. So anything that landed on that name was promoted into the captures, and
  the caller found out one round trip later and somewhere else, where the error
  names their image reader rather than the capture that was never taken.

  The README had recorded this as an absent guarantee rather than quietly
  implying one, which is what made it a known gap instead of a discovered bug.
  It is closed now, and it checks BOTH ends rather than the header that old
  claim described. A file exists from the moment it is created rather than the
  moment it is finished, so a capture big enough to be worth taking is big
  enough to be read mid-write — and a truncated PNG has an entirely valid
  signature. The trailer is what decides.

  The two failures are handled differently on purpose: bytes that are not a PNG
  will never become one, so they are refused at once rather than costing a
  minute of timeout before saying the obvious — the mod dropping a refusal on
  that name is exactly how it happens. A picture still arriving is waited out,
  because refusing a short file on sight would turn a slow write into a failure.
  Neither is renamed into your captures, so a refusal cannot leave a corrupt
  artifact behind for `captures` to list and `read_capture` to serve.

- **The README claimed a coupling that had already been removed.** The status
  banner and Phase 2 both said `trigger` validates against Biomancy's command
  set. It does not, and has not since the harness started reading the list the
  running side publishes: `compose` requires that list and has no fallback,
  deliberately, because a guess about which commands exist is the thing it
  replaced and a fallback would be that guess under another name. Half of Phase
  2 item 2 was therefore already done and recorded as outstanding.

  Corrected in the direction that costs something: a README understating what
  works sends a reader away from a tool that would have suited them, which is
  the same class of error as one overstating it, and the only reason it feels
  safer is that nobody files a bug about it.

- **A path that never arrived was treated as a path.** `.mcp.json` passes the
  two required directories as `${TMODLOADER_SAVE_DIR}` and
  `${TMODLOADER_MOD_SOURCE}` on purpose, so that no checkout carries anybody's
  disk — and the MCP _client_ expands them against _its own_ environment. A
  client started by a daemon or a desktop launcher inherits no interactive
  shell, substitutes nothing, and hands the server the text. Measured rather
  than reasoned about: two live sessions, one `.mcp.json`, and only the one
  whose client had the variables got real paths.

  Read literally that text is a non-empty string, so it was configuration as
  far as anything here could tell. `check` reported **four** problems, two of
  them naming `TMODLOADER_MOD_NAME` and `TMODLOADER_MOD_SOURCE_WIN` — variables
  the reader had never set, derived from the one that never arrived. The
  message that says what to do was the one thing missing.

  The message was the cheap half. `world_win` took the placeholder the same
  way, so `${TMODLOADER_WORLD_WIN}` would have reached tModLoader as a world to
  load and come back as a readiness timeout blaming the heartbeat — the exact
  class of failure the required-variables work was done to remove, arriving
  through the variable that was supposed to fix it.

  Fixed where every variable is read rather than per-variable, so a placeholder
  in one nobody has thought about is absent too: it falls back to the default
  where there is one, and is reported as its own kind of absence where there is
  not, with the instruction that differs from "you forgot to export it" — which
  is what the reader has already done. Covered through the protocol as well as
  at the unit, because the confusing part is that the server _starts_,
  advertises every tool, and fails only when one is called.

## [0.1.0] - 2026-08-09

First tagged release. The surface is 17 tools, 2 prompts and 1 resource,
covered by 280 tests.

**What is verified, and how.** Everything reachable without a running game is
driven over the PROTOCOL in CI on Python 3.12 and 3.13 — not by calling the
tool functions, which is a different question with a different answer, and the
one that let `status` stay broken through 197 green tests. The 8 tools that
need tModLoader actually running (`build_mod`, `launch`, `trigger`, `commands`,
`diag`, `shot`, `restart`, `stop`) are covered by `tests/live_protocol_check.py`,
run by hand on a machine with the game.

**What that leaves — since closed.** At the time of tagging the live check had
not been re-run since `restart` landed and the configuration became required.
It has now been run against 0.1.0's code and passes end to end, including
`restart` keeping the session's world across a relaunch and the mod answering
again afterwards. It found no defect in the released package; it found two in
the live script itself, fixed in #36 after the tag.

**Still not yours.** The mod-side responder lives inside Biomancy rather than
in a package your mod can vendor, so `trigger` validates against Biomancy's
command set. See Phase 2 in the README.

### Fixed

- **Removing the defaults broke both live scripts, and nothing could have said
  so.** They inherit the shell's environment and set none of it, so once the
  paths became required an unconfigured run died on its first tool call — and
  `TMODLOADER_WORLD_WIN` would not have failed until `launch`, minutes in, with
  a game process possibly already spawned and nothing holding its pids. CI
  cannot catch this: the live scripts are deliberately not collected. Both now
  preflight before anything starts, naming EVERY missing variable rather than
  the first. Verified by running both with the configuration stripped: exit 2,
  all three named, and zero game processes started. (#33)
- The variable list lives in `config.REQUIRED_TO_LAUNCH` rather than being
  written out by each script. Two scripts needing one answer is two places to
  forget, which this repo has already done once — a drift guard written for one
  live script, and a sibling added a commit later that it did not cover. The
  contract test now runs BOTH for real with the configuration removed, which
  works on a bare CI runner because the preflight fires before anything needs
  an install. (#33)

### Added

- **A `.mcp.json`, so the server can actually be reached.** Seventeen working
  tools and two prompts, and nothing could call any of them: `claude mcp list`
  returned no match, there was no project config, and nothing in the user
  config either. The paths in it are `${TMODLOADER_SAVE_DIR}` and
  `${TMODLOADER_MOD_SOURCE}` rather than real ones — a committed config holding
  real paths would put back exactly what #31 removed, and would be one person's
  install in everybody's checkout. Verified by starting the server through the
  registered command: 17 tools, 2 prompts, and `inventory` returning this
  machine's three worlds. (#32)

### Changed

- **BREAKING: the three defaults that named a person are gone.**
  `TMODLOADER_SAVE_DIR` and `TMODLOADER_MOD_SOURCE` are now REQUIRED, and
  `TMODLOADER_WORLD_WIN` has no default. All three used to point at one
  developer's install, spelled out with their Windows username and their mod's
  name. That was right for phase 1 and wrong for anything published: a default
  aimed at the author's disk does not fail on yours, it RESOLVES — best case
  `check` complains about a directory you never mentioned, worst case it exists
  and the server drives an install you did not choose. Unset variables are
  reported together and ALONE, because an unset one resolves to `Path(".")` and
  every later check would otherwise fire too, burying the message that says
  what to do under true sentences about the working directory. (#31)
- `launch` with no world configured and none passed now LISTS THE WORLDS in the
  save directory, with the Windows paths it wants. The old default was one
  developer's self-test world by full path, so on any other machine it named a
  file that did not exist — and the failure arrived as a readiness timeout
  blaming the heartbeat, which names the wrong thing entirely. **Breaking:**
  `Config.world_win` is `str | None`. (#31)
- What KEEPS a default is what is not personal: tModLoader's Steam location and
  the Windows binaries under System32 are the same on every machine that can run
  this at all, and requiring them would be ceremony rather than safety. A test
  guards the CLASS rather than the removed constants — it fails on any
  `DEFAULT_*` under `/Users/` or `/home/`, whoever the account belongs to, since
  checking for the specific username would pass the moment a different one
  appeared and would put that username back in the repository to do it. (#31)

- **The command list is read from the mod, not kept here.** This harness held
  its own copy of one mod's twelve verbs and its own belief about which of them
  read an argument — facts owned by running C#, in a file the mod could not see.
  A mod that added a command got no support here until somebody edited Python;
  one that removed a command left this side composing triggers nothing would
  answer. The responder now publishes `<prefix>-commands.txt` at load and
  `compose` is checked against it. There is deliberately **no fallback list**: a
  guess about which commands exist is exactly what this removes, and a fallback
  would be that guess under another name. **Breaking:** `triggers.COMMANDS` is
  gone and `compose` takes a required `commands` keyword. (#19)
- New `commands` tool — what the running mod serves, with `responder: false`
  when nothing published a list. That is a distinct answer from a game still
  starting, and it used to arrive as a readiness timeout, which names the wrong
  thing entirely: it reads as slow rather than as never going to answer. A list
  that exists but cannot be parsed stays an error, because it means a responder
  IS running and this side cannot understand it. (#19)
- `triggers.parse` models the mod's GRAMMAR only. It used to return None for a
  word outside the hardcoded list, which stopped matching the mod when the mod's
  own parser stopped judging vocabulary too — a registry now answers that at
  dispatch. Reporting "unparseable" for a payload the game parses perfectly and
  simply declines conflated two different answers, and only one of them tells a
  caller what to do. (#19)
- `compose` refuses a command that NEEDS an argument and was given none. Only
  the opposite case was checked, so `shot` with no region reached the game and
  came back refused — a round trip to learn what the published list already
  says. (#19)
- The command list is cleared with the other artifacts before a launch, for the
  same reason the heartbeat is: one left by a previous run would say "responder
  present" about a build that may no longer have one. (#19)
- `build_mod` accepts `timeout` and `stop` accepts `settle`, finishing what
  #14 started. Both bounds existed underneath and neither was reachable from the
  surface — `stop` grew `settle` in #11 _because_ a bound nothing can set is a
  bound nothing can check, and the tool still could not set it. A test now asks
  it of every timed tool at once, rather than of the ones somebody noticed. (#18)
- **`logs` can reach every log, and the run that already rotated away.** It read
  `client.log` or `server.log` and nothing else, of six log files — `Launch.log`
  and the `environment-*.log` pair are where a run that dies BEFORE the game
  starts writes, which is the failure `logs` is reached for. Worse, tModLoader
  zips the previous run's logs into `Old/` when a new run starts: after a failed
  launch and a retry, the failure is in an archive and the live log belongs to
  the retry, so the tool answered about the wrong run with a log of the right
  name. `logs(name=..., previous=...)` addresses both. Verified on the real
  install: the live `client.log` opens at 01:10:32 and the newest of 20 archives
  is the run before it. **Breaking:** the `server: bool` parameter is replaced by
  `name`. (#17)
- New `log_files` tool — which logs this install actually has, and how many
  earlier runs are archived. Read off disk, since a server-only session writes
  no `client.log` at all. (#17)
- **Artifact filenames are no longer one mod's.** The five names this harness
  and the mod must agree on are derived from the mod's internal name rather than
  spelling `biomancy-` as constants. tModLoader takes that name from the source
  folder, so the rule reproduces Biomancy's existing filenames exactly — which
  was the requirement, not a nicety: they are a contract with running C#, and a
  prettier scheme would have renamed files the mod still writes. Two mods driven
  from one machine now also stay out of each other's triggers and captures,
  which matters because they share a save directory. `TMODLOADER_MOD_NAME`
  overrides it for a checkout whose folder is named something else. (#16)
- `captures.available()` and `captures.read()` take the mod name, so one mod is
  never served another's captures. (#16)

### Added

- **The server had no prompts at all; now it has two.** `diagnose_silence`
  walks the four reasons the mod might not answer, and `start_a_session` lists
  the worlds and characters that exist on this machine. Both READ THE INSTALL
  rather than reciting prose — the diagnostic arrives with the heartbeat, mod
  list and log inventory already taken, and the session prompt with the actual
  Windows world paths. The decision tree existed correctly across four separate
  docstrings and was assembled nowhere, so following it required reading four
  tools' documentation and knowing to. (#30)
- Both prompts render a failure INTO the text instead of raising it. A
  diagnostic is read when things are broken, so an unusable configuration is a
  likely state rather than an exceptional one — and it is the diagnosis. The
  exception text is included verbatim rather than summarised, which is the
  difference between reporting the fault and hiding it. (#30)
- **`launch` took a world, used it, and forgot it.** `Session` recorded the
  mode, port and player and not the world, so `status` could describe a running
  session while staying silent about the only field saying WHICH WORLD was
  loaded — and anything relaunching from a session's own settings would
  substitute the configured default for the world under test and report
  success. `Session.world` now holds the RESOLVED world (the argument if given,
  `cfg.world_win` otherwise; storing the raw argument would record `null` for
  the commonest case), and both `status` and `launch` report it. **Breaking:**
  `StatusOut` and `LaunchOut` gain a `world` key. (#29)
- **New `restart` tool — stop, rebuild, relaunch, in the one order that works.**
  tModLoader REFUSES to build while the game is open and says so with an error
  that reads like a compile failure, so building before stopping sends you
  hunting a syntax error that is not there. Three separate calls let a caller
  get that order wrong; one cannot. Mode, port, player and world come from the
  running session rather than from defaults — which is what `Session.world`
  above had to exist for. A failed build deliberately does NOT relaunch: the
  game would start, load the previous `.tmod` and answer normally, so the
  session would look healthy while testing the code that just failed to
  compile. (#29)
- **New `log_since` tool — only what a log has gained.** `logs` re-reads a file
  that grows all run. This takes a byte offset and returns the new part plus
  the next offset. Bytes rather than lines because a line count is not a resume
  point, and rather than characters because a character offset is not a seek
  position in a file decoded with replacements. `restarted` is the field that
  matters: tModLoader zips the previous run's logs and starts fresh, so an
  offset from a rotated run points past the end of a now-shorter file, and
  reading there reports an empty log forever — which looks exactly like a quiet
  game. It is deliberately NOT a live tail and says so; tools here are
  synchronous, so nothing watches a 300s `launch` from the side. (#29)
- **New `prune_captures` tool — captures accumulated forever.** `shot` writes
  one per call and nothing ever removed them, so an agent photographing in a
  loop grew the SAVE DIRECTORY without bound: the folder holding the worlds and
  characters, which is not a cache. `keep` is REQUIRED and has no default, the
  same way `shot` requires a region — this deletes files, and a destructive
  tool that runs with no arguments is one that gets called by accident. (#28)
- `captures.contained()` — the containment check `read` always had, extracted
  so `prune` uses the identical one. This module's own header says two paths to
  one file is how one of them ends up weaker, and the newer caller here is the
  one that DELETES. A capture-shaped symlink pointing out of the save directory
  passes the name pattern and really is in the directory; only resolving it
  shows otherwise, and a prune that unlinked whatever `iterdir` returned would
  not have noticed. (#28)
- `prune` validates the whole doomed set BEFORE unlinking anything, so a
  refusal costs nothing instead of leaving some files gone, an exception
  raised, and no record of where it stopped. Ordering is by MTIME rather than
  by the index in the filename: the index is this harness's counter, the
  timestamp is the disk's account of what happened. (#28)
- **New `inventory` tool — the worlds, characters and mods this install has.**
  `launch` states two preconditions and could check neither: `player` must
  already exist (it does not create one, and a duplicate is kicked) and `world`
  wants a WINDOWS path the caller had to know in advance. Both are facts about
  directories sitting right there, and the only way to learn either was to
  launch and read the failure — a kick for the wrong character, a readiness
  timeout blaming the heartbeat for the wrong world. Each world now reports the
  exact `path_win` string `launch(world=...)` wants. Same argument that produced
  `log_files`, applied to the other two directories it covers. (#27)
- `inventory` reports `enabled` and `built_here` as SEPARATE booleans, which
  splits `commands`' single `responder: false` into "not built" and "built and
  switched off". They are not one fact: a mod can be enabled with no `.tmod`
  here, because a workshop mod is installed from somewhere else — on the
  install this was written against, `enabled.json` lists `Biomancy` and
  `CheatSheet` while `Mods/` holds only `Biomancy.tmod`. An `installed` flag
  would call CheatSheet missing and send someone rebuilding a mod that was
  never the problem. (#27)
- `inventory.mods` raises on an `enabled.json` that exists and will not parse,
  and returns an empty list when there is none. A fresh install has no
  manifest, which is a state; a manifest something wrote and this cannot read
  is a fault needing a human. The same line `commands` draws between
  `responder: false` and an error. (#27)
- **New `heartbeat` tool — WHICH silence, not just that there was one.**
  `launch` has always read `<mod>-hooks.txt` to decide readiness and kept one
  bit of it. That is the right thing to block on and the wrong thing to report:
  a failed launch answers `no live heartbeat within 300s`, which names the
  symptom and none of the four causes. Absent means nothing ever wrote one — the
  mod is not loaded, not enabled, or built without the dev bridge. Stale means a
  game ran and died. Live without a world means it is still loading and nothing
  is wrong yet. Live and ready but not armed means the bridge is not listening.
  Four different actions, previously delivered as one sentence about a timeout.
  Reads off disk and needs no session deliberately: a failed `launch` raises
  without storing one, so a tool that required a session could never answer the
  question it exists for. Both sides come back together, because "the client is
  silent and the server is fine" is a different diagnosis from both being
  silent, and asking one at a time cannot see the difference. (#26)
- `diag.parse` types booleans and accepts camelCase keys, because the heartbeat
  shares its grammar and broke both assumptions. The key pattern was
  `[a-z0-9][a-z0-9-]*` — every diag key ever, and neither `gameMenu` nor
  `dedServ`, so the field naming the side that wrote the file matched nothing
  and was dropped in silence. And six of the heartbeat's eleven fields are
  booleans arriving as C#'s `bool.ToString()`, which left as text are all
  TRUTHY: `if hb["armed"]` passed on a game that was not. Verified a no-op for
  the diag dump against the real artifact — 0 camelCase keys, 0 `True`/`False`
  values — and pinned by a test so a future diag field of either shape fails
  loudly rather than changing what an existing caller receives. (#26)
- `read_capture` tool and a `capture://{name}` resource — a capture's PNG comes
  back as image content, so an agent that is not on this machine can finally see
  what it photographed. `shot` still answers with a path and stays cheap: a
  full-frame PNG is tens of kilobytes before base64, and a caller who only
  wanted to know the capture succeeded should not pay for pixels. (#15)
- `captures` tool — the capture filenames on disk, names rather than paths. (#15)
- `captures.read()` — the one reader behind both surfaces. Containment is
  structural: a NAME never a path, the resolved parent compared against the
  resolved save directory (which catches `..`, absolute paths and symlinks as
  one case), and a capture-shaped name required, because the save directory
  also holds diag dumps, heartbeats and the world. A reader that opens whatever
  path it is handed is a file-exfiltration primitive with an MCP interface —
  the same leak this project exists to prevent, arriving from the other end. (#15)
- `status` tool — whether a session is running, and what it is. Read-only, and
  the only way to ask without provoking an error: `launch` fails when a session
  exists and `diag` fails when one does not, so the cheapest question on the
  surface previously had to be asked by breaking something. (#14)
- `diag` now returns `records` beside `fields` — the indented per-record lines
  the scalars only summarise. `fields["npcs"]` says `active=6 mutated=1`;
  `records["npcs"]` says which six. `diag.sections()` had parsed them since it
  was written and no tool ever called it, so they were parsed and dropped. (#14)
- `launch` accepts `world` and `timeout`; `trigger`, `diag` and `shot` accept
  `timeout`. All were supported underneath and exposed by none of them, so an
  agent could neither choose a world nor wait longer on a slow machine. (#14)
- `triggers.parse()` — reads a trigger payload back the way the mod's
  `DevCommands.Parse` will, so `compose()` can refuse anything that would not
  survive the round trip. (#8)
- `config.windows_path_for()` — translates a `/mnt/<drive>` path to its Windows
  spelling, and returns `None` rather than guessing for anything else. (#9)
- `BuildResult.timed_out` — a build that ran out of time is a state the error
  and warning counts cannot express. (#9)
- `stop(settle=...)` — how long a killed process may take to leave the process
  table, as an argument rather than a module constant. (#11)
- Continuous integration: ruff and pytest on every pull request and every push
  to master. (#5)
- CI additionally gates formatting, runs the suite on Python 3.12 **and** 3.13,
  and caps the job at ten minutes. (#12)

### Changed

- `launch("server")` is refused up front. An empty dedicated server runs no
  update hooks, so the mod never polls and never answers — measured on one
  process, changing only whether a client was attached. It cannot meet the
  readiness contract at any timeout, so it fails immediately with the
  measurement instead of after five minutes with a guess. (#6)
- `stop()` returns pids **verified** to have left the process table, not pids a
  kill was aimed at. If any survive it raises, and deliberately keeps the
  session so the pids stay owned and a second call retries exactly them. (#7)
- `trigger` refuses an argument for the eleven commands whose argument the mod
  parses and then discards; only `shot` reads one. (#8)
- `TMODLOADER_MOD_SOURCE_WIN` is derived from `TMODLOADER_MOD_SOURCE` instead of
  defaulting independently. Setting both to different directories is now
  reported at startup rather than silently building one mod while driving
  another. `Config.mod_source_win` is consequently `str | None`. (#9)
- An environment variable set to the empty string is treated as unset, so the
  default applies. (#9)

### Fixed

- Diag counters are typed by the shape of their value rather than matched
  against a list of known names, so a counter the mod renames or adds still
  parses as a number. (#2)
- `shot()` returns a unique path per capture. It used to hand back the mod's one
  fixed output filename, so three regions in a row returned three references to
  a single file that each capture had overwritten — every call reporting OK and
  returning a path that existed. (#3)
- A failed `launch` no longer leaks the processes it started; ownership now
  begins at the first spawn rather than at the first wait, so an interrupt
  between the two spawns cannot orphan the server either. (#3, #7)
- Readiness failures no longer blame Steam in modes that start no client. (#3)
- The trigger file is staged and renamed rather than written in place, so the
  game cannot poll a half-written payload and map it to Unknown. (#4)
- A reply is read once its contents stop changing rather than after a fixed
  sleep, and an empty file counts as still being written. (#4)
- `logs(lines=0)` returns no lines. `text[-0:]` is the whole list, so asking for
  none returned the entire log as a successful answer of the right shape. (#4)
- A build that times out says so instead of reporting `0 error(s)`, which read
  as a compile failure with nothing wrong in it. (#9)
- `tests/live_check.py` exits non-zero when a check fails. It printed `FAIL` and
  carried on, so the run reported success anyway. (#10)
- The test suite no longer busy-waits: two tests spun at full CPU for ten
  seconds each because the fake patched `time.sleep` on a loop bounded by
  `time.monotonic()`. Suite runtime 23.0s → 3.4s. (#11)
- `place` and `killcreep` are accepted by the trigger allowlist. (#1)

### Documentation

- The README's promise of a PNG header check was removed rather than
  implemented: no version ever performed one, and the file is waited for and
  renamed but never opened. Recorded as an absent guarantee, because an
  imagined one is worse. (#4)
- The `trigger` tool description lists all twelve commands; it advertised ten,
  so `place` and `killcreep` read as though they did not exist. (#8)
