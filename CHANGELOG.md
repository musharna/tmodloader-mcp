# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

**Nothing has been released yet.** There are no tags, and the `0.1.0` in
`pyproject.toml` has never been published, so every entry below sits under
Unreleased — including the ones that would be breaking changes if anyone were
depending on them. This file was backfilled at #12 from the merged history
rather than kept from the start, so the entries are grouped by what they do
rather than dated individually; the PR numbers are the audit trail.

## [Unreleased]

### Added

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
