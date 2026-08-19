# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

This file was backfilled at #12 from the merged history rather than kept from
the start, so entries within a release are grouped by what they do rather than
dated individually; the PR numbers are the audit trail. Everything up to 0.1.0
predates any tag, so the "breaking" notes below describe changes nobody could
have been depending on — they are recorded because the reasoning is worth
keeping, not because they broke a released API.

## [0.6.0] - 2026-08-17

Everything in this release comes out of one full audit of the repository —
four review passes (core concurrency, I/O, the C# responder, security), every
finding either fixed here or documented as an accepted residual where the fix
would cost more than the failure.

### Fixed — the two critical findings

- **The tool surface is now serialised by an explicit lock.** `server.py`'s
  header claimed synchronous tools serialise on the event loop; under mcp 2.x
  they run in worker threads beneath a concurrent dispatcher, so two calls
  from one batching client ran in real parallel — consuming each other's
  replies, double-launching into one save directory, clearing `_session`
  under each other. Every tool and resource now runs under `_ONE_AT_A_TIME`
  via `@_serialized`, and an ast-scan test refuses any future tool that
  skips it.
- **The mutation area cap survives 32-bit arithmetic.**
  `settile:0,0,65536,65536,0` computed an area of 2^32 — exactly zero as an
  int — sailed under `MaxArea`, and rewrote the entire world as dirt; every
  product past 2^31 went negative and passed the same way. Both cap sites
  now multiply in 64 bits, with the overflow boundary pinned in tests.

### Fixed — the responder (C#)

- **A throwing handler no longer crashes the game silently.** Dispatch wraps
  `command.Handler` in a catch that Reports the exception; before, a
  Terraria-level throw left the update hook with the trigger already
  consumed and no reply written, so the harness timed out against a dead
  game with nothing on disk saying why.
- **`settile` checks `TileLoader.TileCount`** the way `spawn` and `give`
  check their ceilings — load-bearing here because `PlaceTile` indexes
  arrays sized to that count, so an oversized id was an
  `IndexOutOfRangeException` rather than a refusal.
- **The pre-arm trigger clear checks addressing before deleting.** A second
  client's first settled poll used to destroy a request addressed to the
  client already in-world serving it.
- **Consumption is a `File.Move`, not a delete** — a delete succeeds silently
  on a file somebody else just deleted, so two overlapping polls could both
  consume one untargeted request and both dispatch.
- **An unreadable trigger is retried, not served.** A transient read failure
  used to fall through to `Parse(null)` — the legacy bare-trigger capture —
  consuming a request nobody read and photographing nobody's question. Three
  failed polls now clear it loudly instead.
- **`SyncTiles` sends the clamped rectangle, chunked to 100-tile squares.**
  Edge fills made the server serialise out-of-world tiles through 1.4's
  unchecked Tilemap indexer, and a legal 16384x1 fill exceeded packet 20's
  byte-sized dimensions.
- **`DevChat`'s recorder is unwound on unload** (via a small `ModSystem`, so
  `DevResponder` still never names an opt-in class). Left wrapped, it pinned
  the old load's assembly across Build+Reload and re-wrapped per reload.
- **`FrameShot` writes the PNG aside and moves it into place**, closing the
  torn-drop-box case a mid-write crash left behind; **`Settle()` throttles
  its recursive PNG listing** to every 30th tick; **`ListPlayers` reports
  tile Y from `Center`**, agreeing with `find` about where a body stands;
  **`settile` counts pre-existing tiles separately** from placed ones.

### Fixed — the harness (Python)

- **Replies are correlated to requests.** The reply file is per player, so a
  late reply to a timed-out request was returned as the answer to the next
  one — for `shot`, a wrong picture confidently labeled. Payloads now carry
  `#r-<hex>` when the responder publishes `# replies: tagged`, replies echo
  it as their first line, and a stable reply wearing another request's id is
  waited past. Gated on the published capability, so older vendored
  responders keep working untagged.
- **`log_watch` can no longer permanently miss its line.** `read_since`
  consumed unterminated fragments and advanced past them, splitting any line
  that straddled two polls where nothing could match it whole. Only whole
  lines are consumed now (with a stated exception for a single line longer
  than the byte cap).
- **Log rotation is detected by identity, not only by shrinkage.** A new run
  that outgrew the old offset was read mid-stream as a quiet continuation,
  silently skipping the head of the new run. `log_since`/`log_watch` carry a
  `fingerprint` of the log's head for this.
- **Re-taking a snapshot cannot destroy both copies.** `take` deleted the old
  snapshot before the swap; a transient `/mnt/c` lock failing the swap then
  took the staging copy down with it. The old copy is moved aside and
  restored on failure.
- **`restore`'s undo swallow is narrow.** It caught every `SaveError` — disk
  full included — and proceeded to overwrite the live save with no backup
  while `undo: null` claimed there was nothing to save. Only the new
  `NothingToSnapshot` is waved through.
- **`restore` removes files the snapshot does not hold** (within its scope),
  so a world snapshotted before its `.twld` existed no longer restores into
  a mismatched pair. Reported as `removed`, and covered by the undo.
- **`stop` pins kills to process creation time.** Windows recycles pids, so
  a session client that died on its own could hand its number to the
  developer's own game — which `stop` then killed. The CIM query now returns
  creation stamps, recorded at adoption and checked at teardown.
- **A broken process query is no longer an empty one.** `_tml_pids` returned
  `set()` when PowerShell failed, so `stop` aimed at nothing and reported
  success, releasing a session that left a running game owned by nobody.
  Every caller that acts on the answer now refuses instead; cleanup paths
  that are already unwinding a worse failure stay tolerant.
- **Breaking a stale capture lock takes the lock it judged.** Unlink-by-name
  could remove the fresh lock another session claimed after breaking the
  same stale one — the recovery path producing the very collision the lock
  prevents. The break is a rename, verified against the judged mtime, with
  a fresh lock put back via `os.link` (which refuses to clobber a newer
  claim). The same rename-verify shape now guards `_release_trigger`.
- **A capture claim that wins with no budget left withdraws its own
  request** instead of leaving an unserialised capture behind the released
  lock; `_break_stale_lock` also no longer crashes on its own documented
  race (the second `stat` is gone).
- **`_await_png` tolerates the writer's share lock** — DrvFs surfaces
  `FileShare.None` as EACCES mid-write, which is "still being written"
  wearing an exception, not a failure.
- **`shot` slugs the region in the kept filename** (`top-left` → `topleft`),
  so every capture it reports is one `captures`/`read_capture` can see.
- **`log_since` is bounded** (512KB per call, cut at a line boundary, with
  `truncated` and a resume point), the **API index cache is written via
  staging-and-rename** so a killed indexer cannot install a truncated index
  that passes the validity check forever, **`prune_captures` tolerates the
  other session pruning concurrently**, heartbeat reads survive the
  stat-then-read race, protocol files pin `encoding="utf-8"` on both ends,
  and `${VAR}/suffix` placeholders are diagnosed as unsubstituted rather
  than traveling as literal paths (`C:\Cash$Mod` stays a real path).

### Security / packaging

- **The sdist no longer ships `docs/superpowers/`** — internal planning
  notes carrying real usernames and machine paths rode the wholesale `docs`
  include into every built tarball. Never published (the package has not
  been uploaded anywhere), so the leak was latent; the include now names
  `docs/MOD_CONTRACT.md` alone. The `save_restore` manifest is also no
  longer trusted with paths: a tampered `files` entry that escapes the save
  directory reads as an untrustworthy snapshot, same as an unparseable one.

### Documented residuals (deliberate)

- The stamp-boundary wait assumes the WSL and Windows wall clocks share
  fractional-second alignment; a miss costs one collision, the bounded
  failure this mechanism already accepts.
- The pid diff still adopts anything that starts tModLoader during the
  launch window; the creation stamps pin `stop` to those processes but
  cannot pin the diff to our spawns.
- The rename-verify put-backs (lock break, trigger release, late-claim
  withdrawal) each keep a microsecond-scale three-party window, priced in
  their docstrings.

Unreleased work folded into this release from between 0.5.0 and the audit:

### Fixed

- **Three broken anchors in `docs/MOD_CONTRACT.md`**, all pointing at the same
  heading and all missing one hyphen: `#modcapturetrigger--the-request` where
  GitHub generates `#mod-capturetrigger--the-request`. A broken anchor does not
  fail loudly — the link renders, the reader clicks, and the page does not
  move — so these sat there for weeks and were found only by writing a checker.

### Added

- **`tests/test_doc_links.py`** — every relative link and anchor in the
  published documentation, checked against the real files rather than a
  fixture. Includes the checker shown failing on both a bad anchor and a
  missing file, because a link checker that returns an empty list
  unconditionally passes every document ever written.

### Documentation

- **The README's "Phase 2" section is gone** — 105 lines of struck-through
  development diary, every item already recorded in this file. Replaced with
  33 lines of **Known limits**, which is what a reader deciding whether to
  adopt this actually needs: it has only run on one install, two dedicated
  servers racing for one trigger is unobserved, there is no escape hatch by
  design, and a stopped session saves nothing but must not be relied on to.
- The status blockquote no longer recites release notes. It says what the
  project is, and points at this file for what changed.
- **The README is now the PyPI page it was about to become.** An Install
  section (`uv tool install` / `pip install` / `uvx`, and that the responder
  is vendored from the repository, not installed from PyPI); every relative
  link made absolute, because PyPI renders the README verbatim and a relative
  link there is dead — and `test_doc_links.py` taught to map those absolute
  self-repo URLs back onto the working tree so they stay checked; CI and PyPI
  badges. Three registered tools the table omitted (`commands`, `captures`,
  `log_files`) are in it now; "`check` says so by name" no longer names a tool
  that does not exist; the paragraph beginning "That row" names the row
  (`shot`) instead of pointing thirty lines up; and the vendoring section says
  what 0.6.0's tagged replies buy and that older vendored copies keep working.

## [0.5.0] - 2026-08-17

The release where the world stops being read-only. `tiles` could count a world
nothing could change and `spawn` could make NPCs nothing could remove; `settile`,
`cleartile` and `despawn` close that, and `find` and `players` answer the state
a count cannot carry. `command` gives the escape hatch other harnesses spell as
`reflect_invoke` a form that stays published, typed and refusable — a mod's own
registered commands, which it already decided existed.

Two things here are worth more than the verbs. `api_search` reads the INSTALLED
assembly's own metadata, so a call is checked against the version on this disk
rather than against anybody's recollection of the API — it found that
`CommandLoader.HandleCommand` is not public, which is why `command` needed no
reflection. And `save_snapshot` / `save_restore` were built on a premise that
turned out to be false: measuring it showed a session ended by `stop()` writes
neither the world nor the character file, because `stop` force-kills. The
feature stayed, its justification changed, and the live check that caught the
error now reports the fact instead of asserting the guess.


### Added

- **`settile`, `cleartile` and `despawn` — the write half.** `tiles` could read
  a world nothing could write, and `spawn` could create NPCs nothing could
  remove. All three are `DevMutations` verbs, refused on a multiplayer client
  like every other thing the server owns. The fills are capped by the same
  `MaxArea` as the tile query, because placing pays per tile exactly as scanning
  does — unlike the entity query, whose rectangle only filters.

  Tile type **0 is accepted**, which is the one place the id rules diverge:
  `spawn` and `give` refuse 0 because it means "nothing" in their id spaces, and
  in the tile space it is Dirt. A test asserts both halves together so the
  contrast cannot rot.

  `despawn:all` **spares town NPCs.** They are saved with the world and do not
  move back in on their own, so sweeping one away while clearing a test's
  monsters is a change somebody notices days later and cannot undo. Naming a
  town NPC's id explicitly still removes it — that is a request rather than
  collateral.

- **`find` and `players` — state a count cannot carry.** `entities` says the
  boss is there; it cannot say the boss is at a third of its health. `find:npc`
  or `find:npc,<id>` returns one line per entity with slot, position, name and
  the state that kind actually has — health for an NPC, stack for a dropped
  item, owner for a projectile — capped at 64 lines, with the number that
  MATCHED reported alongside so the cap is visible rather than looking like the
  whole answer.

  `players` is a separate verb because players have no type. Counting them the
  way `entities` counts would report "1 distinct type" for any number of
  people, which is true and useless.

- **`save_snapshot`, `save_restore`, `save_snapshots` — undoing a run.** Copies
  the configured world and every character aside, and puts them back. Refuses
  while tModLoader is running, naming the pids: a running game owns those files
  and writes them on its own schedule, so a copy taken then is mid-write and a
  restore is overwritten by the next save. `save_restore` saves what it is
  about to overwrite under `auto-before-restore`, so a restore aimed at the
  wrong label is recoverable rather than final.

  **The premise this was built on turned out to be wrong, and measuring it is
  the useful part.** It was justified by "runs quietly accumulate changes in the
  save". Measured: a session launched, told to `settile` and `give`, then ended
  with `stop()` wrote NEITHER the world file NOR the character file — both
  byte-identical afterwards, the world's mtime six days old. `stop` force-kills
  through taskkill and a killed Terraria saves nothing, so the ordinary short
  run was already safe, by accident rather than design.

  What is still unsafe, and what this is for: a run long enough for the server's
  own autosave; a graceful exit, now or after any future change to how `stop`
  works; and pointing `settile` at a world somebody cares about, where "probably
  nothing was saved" is not the assurance you want. The live check reports
  whether the game wrote anything rather than asserting it either way, and gets
  its positive control by ruining the world file itself.

- **`entities` — the other half of the world.** `tiles` was argued for on the
  grounds that a diag reports whatever a mod chose to count, so a mod that never
  thought to count something cannot be asked about it. That argument is about
  counting rather than about tiles, and it applies unchanged to what is moving
  around on top of them — which is why the earlier decision to skip an entity
  query as "already covered by diag records" was the inconsistent one.

  Takes a kind and no default — `entities:npc`, `entities:item`,
  `entities:projectile` — for the reason `shot` takes a region and no default:
  the three id spaces are separate, so a query answered about the wrong one
  comes back as a plausible list of numbers that means nothing. An unknown kind
  is refused by naming the three. A rectangle may follow the kind, in the same
  tile coordinates `tiles` uses.

  **Its rectangle is a filter, not a budget.** A tile query pays per tile, so an
  unbounded one walks five million of them and is capped. An entity query walks
  a fixed few hundred array slots whatever it is handed, so the same rectangle
  is free — it is therefore NOT capped, and a test asserts both halves together
  so the exemption cannot quietly become a deleted cap.

  Reports the display name beside each id, because three opaque id spaces are in
  play and `id=4 count=1` is a fact the reader then has to go look up. A name
  that fails to resolve is reported as itself rather than losing the whole count.

  Neither side is refused: a dedicated server owns these arrays and a
  multiplayer client holds synced copies, so asking both the same question is
  how a desync becomes visible rather than theoretical.

- **`command` — run the mod's own registered commands.** The biggest gap
  against the Minecraft and Unity MCP ecosystems was the escape hatch: every
  other verb here has to be ANTICIPATED, so a question nobody wrote a verb for
  costs a C# edit, a rebuild and a relaunch. Those servers answer it with
  `reflect_invoke` and `execute_code`, which buy unlimited reach and throw away
  the property this design rests on — that everything is published, typed and
  refusable.

  A mod's own `ModCommand`s are the middle. The mod decided they exist, named
  them and gave each a usage line, and most mods already have the debug commands
  somebody would otherwise be adding a verb for. Running one is not new power —
  it is the power a developer already has by typing into chat — and the set is
  ENUMERABLE, so an unknown name is refused by listing the ones that exist
  rather than by silence. `commandlist` asks for that list directly.

  It needed no reflection, which was not obvious: `CommandLoader.HandleCommand`
  is not public. `ModLoader.Mods`, `Mod.GetContent<ModCommand>()`,
  `ModCommand.Action` and `CommandLoader.Matches` all are, and together they are
  a complete public path — found with `api_search` rather than by grepping.
  Output is captured by handing the command a `CommandCaller` that writes
  replies down instead of to a screen, which is the interface tModLoader already
  asks for.

- **`chat` and `say` — hearing what the game said.** A screenshot shows that
  chat happened and cannot be read as text, and `diag` reports only what a mod
  chose to put in it. Neither reached `Main.NewText`, where most Terraria mods
  print the things a developer wants to see. It was the one channel this
  harness could not hear.

  The recorder wraps rather than hooks. `Main.chatMonitor` is a public field of
  a public interface and everything printed goes through it, so this installs a
  second implementation that writes each line down and forwards every call — no
  MonoMod detour, nothing a tModLoader update can silently change the shape of.
  Guarded against double wrapping, because a mod reload would otherwise build a
  chain of recorders each reporting the same line again. A dedicated server
  draws no chat and is refused rather than answered with an empty list: "nothing
  was said" and "this side cannot hear" are different answers.

- **`tiles` — counting what is actually in the world.** Terraria is a tile game
  and there was no tile query anywhere in the protocol. Counts BY TYPE rather
  than a grid of ids: a caller asking "did the placement work" wants to know
  that 40 of type 812 appeared, not to receive 16,000 numbers and count them.
  Area-capped and clamped to the world's edges, with the number actually looked
  at reported so a clamp is visible. In the base responder rather than behind an
  opt-in, because reading a world the harness can already photograph adds no
  power to anybody.

- **`api_search` — what the installed tModLoader actually exposes.** Writing
  mod-side code here has meant answering two questions over and over, both
  badly. "Does `Main.cloudAlpha` exist" was answered by GREPPING A 21MB DLL FOR
  A SUBSTRING, which finds the name and cannot say what owns it, whether it is
  a field or a method, or what type it is. "What does `QuickSpawnItem` take"
  was answered by writing the call and letting the compiler decide — exact, but
  only for code already written, and a build cycle per attempt.

  Neither answers the question you have BEFORE writing anything: what is there.
  `Main.maxRaining` is only findable if you already suspect the name.

  Read from the assembly's own METADATA by `tools/ApiIndex`, which never loads
  or runs it, so the answer cannot drift from the version installed — the
  failure mode of every wiki page and every model's recollection of an API.
  36,566 members across 2,240 types. Cached against the DLL's size and mtime,
  so a game update invalidates the index by construction rather than by
  anybody remembering.

  Ranked, because an unranked substring search over 36,000 members answers
  "rain" with `slimeRainKillCount` first and buries `raining` — a confident
  answer about the wrong member. Exact short name, then short name contains,
  then anywhere in the path, then the type; ties broken by path depth.

- **`log_watch` — block until a log line appears.** `log_since` answers "what
  has this log gained", which leaves a caller who is WAITING to write the loop
  themselves: read, check, sleep a guessed number, give up eventually. The
  offset is the mechanism — each poll resumes where the last stopped, so a line
  is matched exactly once, never missed in the gap between polls and never
  re-reported on the next. A watch that re-read from the top would match a line
  written before the wait began and call it news, which is how "wait for the
  crash" passes on the crash from the PREVIOUS run.

  `offset=0` includes history deliberately: "did the mod load" is a question
  about a line that is usually already there. Not matching returns rather than
  raises, carrying the resume point; a MISSING log still raises, because that
  is nobody having been asked.

- **Verbs that change the world, and are off until a mod asks.**
  `responder/DevMutations.cs` adds `time`, `weather`, `spawn`, `give` and
  `teleport`. Everything in `responder/` until now READ — `capture`, `diag` and
  `shot` observe a world and write files outside it — so these could not arrive
  by upgrading a vendored folder: a mod re-syncing must not silently gain the
  power to spawn enemies into somebody's save. Nothing registers them until a
  mod writes `DevMutations.RegisterInto(r, Report)`, and `Report` being
  `protected` on the base class makes "only a mod can turn these on" a rule the
  compiler enforces rather than a convention. `DevBridgeGate` still applies
  underneath: a played install runs none of it.

  Each verb refuses the side that cannot do it and names the side that can.
  `time`, `weather` and `spawn` are refused on a multiplayer client — the
  server owns the clock, the weather and the NPC array, and a client that
  changed them would be corrected by the next world packet, so the change would
  appear to work and then undo itself. `give` and `teleport` are refused on a
  dedicated server, which runs the world without standing in it. Singleplayer
  does everything, being both sides at once.

  Split into two files so the half worth testing is testable.
  `DevMutationArgs.cs` imports nothing but `System` and holds every argument
  rule and every refusal message; `DevMutations.cs` imports Terraria and is the
  thin applier. Left as one file, the refusals would have sat on the wrong side
  of the vendor project's compile line — and a verb that spawns the wrong thing
  is caught the first time somebody looks at the world, where a refusal naming
  the wrong side is never caught at all. `time` resolves to a FRACTION of a
  phase rather than to ticks for the same reason: 54000 is a fact about the
  game, and this file cannot see the game.

  Two safety rules are enforced rather than trusted: a count is capped, because
  `spawn:1,10000` is one keystroke from `spawn:1,1000` and would fill the NPC
  array; and id `0` is refused specifically, because it parses, is in range,
  means "nothing" in both of Terraria's id spaces, and is therefore the one
  argument that would report success against a world it did not change.

  **Run against a real game, 16 of 16** (`tests/live_mutations_check.py`, two
  clients and a dedicated server on one world). Every verb is verified by
  reading the state back out afterwards rather than by its own success report,
  and the multiplayer half is read from the side that did NOT make the change:
  `time:midnight` on the server arrives at the client, which is `WorldData`
  going out. Every refusal is checked live too, each next to the same request
  succeeding on the right side — a responder that refused everything would pass
  the negatives alone.

  The first run found two defects, both in the measurements rather than the
  verbs, and both of a kind only a live run produces. `give` reported OK while
  the occupied-slot count sat at 25 before and after: five torches had merged
  into a stack of torches the character already carried, which is the COMMON
  case for a stackable item. The diag now counts stacks as well as slots, and
  the same give reads 80177 -> 80182. And `teleport` landed three tiles above
  where it was asked to: `player-tile` was reporting `position`, the top-left
  corner of a three-tile-tall body, so a teleport that landed exactly read as
  one that had missed. It now reports the tile UNDER the character — centre
  horizontally, feet vertically — and `teleport:spawn` lands on `2101,252`
  when the world's spawn is `2101,252`.

  The teleport applier was corrected in the same pass: it placed the character
  half a body to the right of the named tile, because a tile is a point and a
  body is not. It now offsets by half the width as vanilla's own spawn does.

- **A template mod that compiles.** `template/DevBridgeTemplate/` is a whole
  tModLoader mod — build files, an empty `Mod` class, a `DevResponder`
  subclass, and a byte-identical copy of `responder/`. This closes README Phase
  2 item 4, which had been "mostly answered rather than done" since the
  responder was extracted.

  It was built rather than described, and that is the point. The vendor test
  project compiles eight of the eleven files with nothing of any mod's on the
  line; the other three need Terraria, XNA and `ModSystem`, so nothing had ever
  compiled the folder a consumer actually copies. This does, against the real
  tModLoader: 0 errors, 0 warnings — with a deliberate syntax error injected
  into the vendored `DevMutations.cs` first, which failed the build at the
  expected line and proved the copy was genuinely on the compile line rather
  than quietly skipped.

  The copy is checked in rather than synced at build time because tModLoader's
  `-build` compiles the mod DIRECTORY, so a template saying "copy `responder/`
  in here" would not build as checked in — and a template nobody can build
  proves none of this. A checked-in copy rots, so a test asserts the two
  directories are byte-identical and prints the one-line `cp` that fixes them.

- **`join` — a second client into a session that is already running.** The
  protocol has supported several clients since answers became per-player:
  every request carries an address, every answer carries a token. The
  LIFECYCLE supported exactly one, so the arrangement two whole releases exist
  to make safe was unreachable from this harness — the only way to get a
  second client was to spawn a game by hand, which is what
  `tests/live_capture_check.py` did. That function is now deleted and the live
  check calls this instead, which is the point of moving it.

  It waits for a CLIENT, not for a process. The lifted version watched for a
  new tModLoader pid and called it done; a pid says something started, not
  that a character loaded, that the join was accepted, or that a world is
  under it.

  And it watches only that player's own tokened heartbeat. The unsuffixed
  `<mod>-hooks.txt` is a shared slot rather than a client — every client that
  boots writes it on the way through the menu and nothing deletes it — so with
  a client already running it holds THAT client's record, and accepting it
  would let the call return against the heartbeat of the game that was already
  here. This is the one the test suite missed on the first pass: written with
  a zero timeout, the wait loop never ran, and the test passed just as happily
  against a version that accepted the shared slot.

  A duplicate character is refused, case-insensitively. Two clients under one
  name share a player token, so their heartbeat, reply, diag and shot are one
  file each between them — the collision per-player naming removed, reachable
  again through the tool that adds clients. `stop` needs no knowledge of any
  of this: the pids go into `session.started`, which is already exactly what
  it kills. `status` gained `joined`.

- **`wait_until` — waiting for a state instead of sleeping a guess.** Every
  caller driving this server had written the same loop by hand: send a
  trigger, sleep a number the author picked, take a diag, check a field, give
  up or go round again. A guessed sleep is wrong in both directions, and the
  short one is the dangerous one — the check reads the state BEFORE the thing
  happened, which is indistinguishable from the feature being broken.

  The comparison is TYPED, which is most of the value. `diag` already returns
  counters as ints and the heartbeat's flags as bools, precisely because
  `"10" < "9"` and the truthiness of `"False"` were real bugs; a wait that
  string-matched them would have reintroduced both at the last possible
  moment, in the one place nothing downstream can catch it. `world-ready ==
true` compares as a boolean and `items >= 10` as a number.

  It REFUSES what can never come true rather than waiting it out. An unknown
  field names the fields that do exist; ordering a composite string says what
  the value actually is — `npcs` is the whole line `active=4 mutated=0`, there
  is no `npcs.active`, and `contains` is how that line is waited on. Both were
  spellable and would have reported a timeout, which blames a game that was
  answering perfectly. An ABSENCE is deliberately not in that category: a
  field reading `NONE` may gain a value on the next poll, so it is a
  non-match rather than a refusal.

  Not matching is an answer, not an error: it returns `matched: false` with
  the last reading it took, so a caller is never sent to take another diag
  against a state that has since moved on. One budget across every poll, the
  same rule `diag` and `shot` already follow within a single call.

## [0.4.0] - 2026-08-16

The release where two sessions stop colliding on the one thing neither can see
the other doing — and where the dedicated server, the only side that could
never be addressed, finally gets a name.

0.3.0 stopped two clients erasing each other's answers and left three ways they
could still collide. Terraria stamps a capture's filename to the second before
the mod is involved, so two captures inside one second produced ONE picture and
two callers each told it was theirs. The lock that now serialises them was
bounded by a 60-second guess that was wrong in both directions — it broke a
live capture whose caller had legitimately asked for longer, and it wedged the
other session for a full minute over a dead one. And a dedicated server had no
address at all, so two of them sharing a save directory were indistinguishable
on disk: the request went to whichever polled first, both wrote their answers
to one set of filenames, and either session's cleanup could destroy the other's.

Each was found by running it rather than by reading it. The capture collision
was reproduced against a pre-fix checkout — 0 of 6 rounds passed, with six PNGs
on disk for twelve requests, so half the pictures were not misattributed but
lost — and the server's address was checked against a rebuilt mod, 7 of 7, with
the refusal proved to be a refusal rather than a silence.

### Added

- **`tests/live_server_address_check.py` — the server's address, run where the
  mod actually reads it.** The change is inert unless the mod can read `-port`
  off its own command line, and nothing in either suite reaches that: the
  Python half tests what the harness composes, the C# half tests
  `ServerAddress` against a command line handed to it, and `DevResponder.cs` —
  where they meet, and the only caller of `Environment.GetCommandLineArgs()` —
  is on no compile line at all.

  Run 2026-08-16 against a rebuilt Biomancy, 7/7. The heartbeat filename is
  the first proof and needs no request: `launch` accepts either name, so
  getting that far says only that something answered, and
  `biomancy-hooks-port7810-server.txt` says which. `side=server netmode=2` off
  the dump confirms the answer came from the dedicated server rather than the
  client.

  ONE SERVER IS ENOUGH even though the defect is about two: the negative — a
  request for `port9999` left unconsumed and unanswered — is the mod's own
  leave-what-is-not-yours rule, and a second server would need a second world
  where there is one. The pair is ordered so the refusal is a refusal:
  without the control that follows it, "the trigger is still there" passes
  just as well against a server that stopped polling.

- **A dedicated server has an address: its port.** It had none — no
  `Main.LocalPlayer`, so `IsFor` refused every target — and what followed was
  that every request on the server side of the trigger went out untargeted.
  Two sessions each driving their own server out of one save directory were
  then indistinguishable on disk in all three ways that matter: the request
  went to whichever server polled first, both wrote their answers to one set
  of filenames, and either session's `launch` or `stop` could delete a request
  the other was still waiting on. The same race per-player naming removed for
  clients, on the axis nothing had covered.

  `port7810` is both the target a request carries (`diag@port7810`) and the
  token naming the answers (`<mod>-diag-port7810-server.txt`) — one string,
  because two spellings of one identity is one more place for the two
  languages to disagree. The port because it is the one value both sides
  already hold with no handshake: the harness passes `-port`, and the mod
  reads the same argument back off its own command line. Read from the command
  line rather than from Terraria's networking state so the rule stays inside
  the part of `responder/` that compiles and is tested without Terraria on the
  line.

  **Breaking for the mod side**: a responder must be re-vendored to be
  addressable. An old copy still works, degraded to the old ambiguity rather
  than broken — it writes the unsuffixed server names, which `launch` still
  accepts, and answers untargeted requests as it always did.

  This also moved where the side suffix sits relative to the token, from
  `-server-<token>` to `<token>-server`, matching the order the harness has
  always composed in. No existing filename changed — the order is only
  observable when both apply, and until now nothing had both. A test had stood
  since the per-player work recording that divergence and saying in as many
  words that giving a server a token could not be done safely without fixing
  it first.

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

### Known

- **Two dedicated servers genuinely racing for one trigger has not been
  observed.** The mechanism is verified — a server derives its address from
  its own `-port`, answers only what names it, and leaves what does not where
  the other would find it — but each half was checked with ONE server and a
  hand-written trigger. Running two needs a second world; this install has
  one, and two dedicated servers on a single `.wld` is a corruption risk not
  worth a test.

- **A responder vendored before this release is not addressable.** It writes
  the unsuffixed server names and answers untargeted requests exactly as it
  always did, and `launch` accepts either heartbeat, so it degrades rather
  than breaks — but two such servers stay indistinguishable. Re-copy
  `responder/` and rebuild to get the address; `SHA256SUMS` is what tells you
  a copy is behind, and it did on this one.

- **A capture whose reply times out releases its lock while Terraria may
  still be writing the PNG.** Carried unchanged from 0.3.0 and independent of
  the deadline work: the lock is released in a `finally`, and the mod has the
  request whether or not the answer came back.

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
