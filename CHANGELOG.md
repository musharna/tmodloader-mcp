# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

**Nothing has been released yet.** There are no tags, and the `0.1.0` in
`pyproject.toml` has never been published, so every entry below sits under
Unreleased — including the ones that would be breaking changes if anyone were
depending on them. This file was backfilled at #12 from the merged history
rather than kept from the start, so the entries are grouped by what they do
rather than dated individually; the PR numbers are the audit trail.

## [Unreleased]

### Changed

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
