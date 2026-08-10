# Extracting the responder, and naming answers per player

**Status:** approved design, not yet planned.
**Repos touched:** `tmodloader-mcp` (host) and `Biomancy` (first consumer).

## Why

`README.md` has said since the first release that the mod-side half is not
yours: the responder lives inside Biomancy rather than in anything another mod
can depend on. `docs/MOD_CONTRACT.md` removed the excuse — the protocol is
written down, file by file — but a contract with no reference implementation is
a document, not a dependency. Anyone installing this harness today has nothing
to point it at.

Two things land together, because they are one edit to one contract:

1. The generic half of the responder becomes a folder a mod vendors.
2. A client's ANSWERS carry the player who wrote them, so two clients can be
   driven at once.

The second is folded in deliberately. `MOD_CONTRACT.md` records it as a known
limitation and says two clients seeing each other "is the case that most
deserves testing and currently cannot be" — and it is a rename of the same files
this extraction is already moving. Split across two passes, the naming rule gets
rewritten twice and the second rewrite lands after third parties may have
vendored the first.

## What the extraction actually is

Less than the README implies. Biomancy's `Common/DevBridge/` was already written
for this, and says so: `DevCommandRegistry.cs:46` explains that the closed enum
became a registry precisely so "the generic half of this responder could not be
vendored into another mod without carrying Biomancy's gameplay verbs." Artifact
names already derive from `Mod.Name`. `FrameShot` already takes the name from
its caller. `DevBridgeGate` already keeps the whole thing out of played
installs.

What is still tangled is one file. `Common/Diagnostics/DevCapture.cs` is 1442
lines in which the generic `ModSystem` — poll on four update hooks, arm, parse,
dispatch, write the heartbeat, publish the command list, serve `capture`, `diag`
and `shot` — shares a class with nine Biomancy gameplay verbs and a Biomancy
state dump. `BuildCommands` at line 118 already draws the line in a comment.

So: split one class, move eight files, pick a neutral namespace.

## Decisions

| Decision              | Choice                                                                   | Rejected, and why                                                                                                                                                                                              |
| --------------------- | ------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| How a mod consumes it | A vendored source folder it copies into its own `ModSources/<Mod>/` tree | A tModLoader library mod via `modReferences` would be a hard RUNTIME dependency every player of a consuming mod must install and enable — for a bridge whose whole design is to be absent from played installs |
| Where it lives        | `tmodloader-mcp/responder/`, beside `docs/MOD_CONTRACT.md`               | A separate repo puts the contract and the code implementing it on opposite sides of a boundary, which is the drift this extraction exists to end                                                               |
| Shape of the seam     | Abstract `DevResponder : ModSystem` with two overridable members         | Composition would make the consumer forward four update hooks by hand, and a forgotten forward is silent — silence being the exact failure this bridge diagnoses                                               |
| Diag                  | Owned by the base, body supplied by the mod                              | Leaving `diag` as an ordinary mod-registered verb lets a mod compile, load, publish a command list and still fail `MOD_CONTRACT`, with nothing saying so                                                       |
| Per-player naming     | This pass                                                                | See above                                                                                                                                                                                                      |

## 1. `responder/` — what ships

A directory of C# in this repo. tModLoader compiles every `.cs` under a mod's
source folder, so vendoring is a copy: no package manager, no workshop entry,
nothing a player installs.

**Namespace `TModLoaderMcp.DevBridge`**, chosen so a consumer never runs a
find-replace. A C# namespace need not match the mod's name, and one that says
`Biomancy` in somebody else's mod is the same class of mistake as an artifact
file that does.

Moved from `Biomancy/Common/DevBridge/`, otherwise unchanged except the
namespace line:

| file                    | lines | what it is                                      |
| ----------------------- | ----- | ----------------------------------------------- |
| `DevArtifacts.cs`       | 91    | the filename rule, per mod and per side         |
| `DevBridgeGate.cs`      | 73    | developed-install vs played-install             |
| `DevCommandRegistry.cs` | 200   | the verb set as data, and `Publish()`           |
| `DevCommands.cs`        | 159   | payload grammar: `name`, `name:arg`, `name@who` |
| `ShotRegion.cs`         | 109   | the five region names, no default               |
| `FrameShot.cs`          | 164   | back-buffer read                                |
| `CaptureBounds.cs`      | 61    | region to rectangle                             |
| `CaptureFind.cs`        | 54    | locating what the capture camera wrote          |

**`ArtifactHash.cs` does not move.** Its only caller is
`DiagCollector.cs:354`, identifying a built `.tmod` for Biomancy's own
self-test. It is not part of the trigger protocol, and shipping it would ship
one mod's habits as everybody's.

One new file, `DevResponder.cs`, holding the generic half lifted out of
`DevCapture.cs`: `IsLoadingEnabled` and the gate, `Load`/`Unload`, the four
update hooks and `Tick`, the world-settle and arming logic, the addressee rule,
`Dispatch`, `WriteHeartbeatIfStale`, `PublishCommands`, `Observe`, `PathFor`,
`Report`, and the three generic verbs (`CaptureNow`, `WriteDiag`, `TakeShot`,
plus `Begin`/`Settle`/`SettleShot`/`List`/`ViewRectProblem`).

And `responder/README.md`: copy the folder, subclass, override two members.

## 2. The seam

```csharp
public abstract class DevResponder : ModSystem
{
    // Registered BEFORE RegisterCommands, so the published list always leads
    // with the three the harness relies on.
    //   capture  - whole frame, via Terraria's capture camera
    //   diag     - this side's state, body from CollectDiag()
    //   shot:<r> - one region, from the back buffer

    /// This mod's own verbs. Base class registers nothing after this.
    protected virtual void RegisterCommands(DevCommandRegistry r) { }

    /// The body of <mod>-diag-<player>.txt. The base owns the file, the write,
    /// and the failure report; only the CONTENT is the mod's.
    protected abstract string CollectDiag();
}
```

`CollectDiag` is abstract rather than virtual-with-a-default. A default would be
an empty dump that satisfies the compiler and fails the contract, which is the
failure mode this whole channel exists to make loud.

Biomancy's `DevCapture` becomes a subclass carrying its nine gameplay verbs
(`mutate`, `vat`, `creature`, `kill`, `strains`, `seed`, `creep`, `place`,
`killcreep`) and `CollectDiag() => DiagReport.Format(DiagCollector.Collect(Mod))`.
`Common/Diagnostics/` stays in Biomancy: `DiagVat`, `DiagStrain` and
`DiagResidue` are one mod's schema, not a generic one.

## 3. Per-player answers

### What already works

The REQUEST half needs no change. `DevCapture.cs:553` already implements the
rule — a client that is not the addressee leaves the trigger exactly where it
is, silently, so the intended client finds it on its own next poll. The contract
doc's own diagnosis is right: requests can be told apart and answers cannot.

### The rule

Trigger files stay shared and addressed. Only answers gain the player.

| file                    | today    | after                                                             |
| ----------------------- | -------- | ----------------------------------------------------------------- |
| `<mod>-capture.trigger` | per side | unchanged                                                         |
| `<mod>-commands.txt`    | per side | unchanged — the verb set is the mod's, identical for every client |
| `<mod>-hooks.txt`       | per side | `<mod>-hooks-<player>.txt`                                        |
| `<mod>-capture.txt`     | per side | `<mod>-capture-<player>.txt`                                      |
| `<mod>-diag.txt`        | per side | `<mod>-diag-<player>.txt`                                         |
| `<mod>-shot.png`        | per side | `<mod>-shot-<player>.png`                                         |

A dedicated server has no player and keeps the existing `-server` suffix
unchanged. The two axes compose the way `DevArtifacts` already describes: the
mod prefix keeps two MODS apart, the side suffix keeps two SIDES apart, and the
player token now keeps two CLIENTS apart.

### Turning a character name into a filename

Terraria character names allow spaces and punctuation, so the raw name is not a
filename. Lowercase-and-strip alone is not enough either: `Big Bird` and
`BigBird` would collapse to one token and land back in the shared-file bug this
pass exists to remove.

**The rule:** lowercase; every run of non-alphanumeric characters becomes a
single `-`; leading and trailing `-` trimmed; then four hex characters of the
MD5 of the ORIGINAL name's UTF-8 bytes, appended after a `-`.

These three are COMPUTED rather than illustrative, and are the first rows of the
cross-language table under Testing:

```
n43n      -> n43n-003f
Big Bird  -> big-bird-44a3
BigBird   -> bigbird-ca4c
```

The hash is always present, not only on collision, so the rule is one branch on
both sides rather than two that must agree about when to disambiguate. It costs
five characters on every artifact name forever, and buys a collision the harness
could not otherwise detect — two clients would simply share a file again, which
is the failure being fixed, arriving silently through the fix.

Both sides compute it independently from a name they each already hold: the mod
from the existing `LocalPlayerName` (`DevCapture.cs:1413`, which reads
`Main.LocalPlayer` and already returns null on a dedicated server or a null or
empty name), the harness from `Session.player`.

### When there is no player yet

`LocalPlayerName` is null before a character exists — at the main menu, and
during loading. The heartbeat is written from `Tick`, which runs then too, and
that is not incidental: `launch` distinguishes "live, no world" from "absent"
precisely by reading a heartbeat written before a world is up. So a per-player
heartbeat name is unwritable at exactly the moment the heartbeat matters most.

**Rule:** a client with no local player name yet writes the unsuffixed
`<mod>-hooks.txt`, as today; once a name exists, every artifact including the
heartbeat uses the player token. The harness globs both forms, and reads an
unsuffixed heartbeat as what it is — a client that is up and has not got a
character yet, which is the "live, no world" state `heartbeat` already reports.

This is the one place the old name survives, and it survives carrying a meaning
it did not have before. It must be spelled out in `MOD_CONTRACT.md` rather than
left as a fallback an implementer discovers, because a responder that skipped it
would look correct until somebody watched a launch.

### Harness changes

- `triggers.Artifacts` and `artifacts_for` take a player and gain the token
  rule; `Session.path` threads it through. `Session` already holds `player`, so
  `trigger`, `diag` and `shot` need no new arguments.
- `captures.capture_pattern` gains the token. It currently reads
  `^{mod}-shot-\d{3}-[a-z]+\.png$` and becomes
  `^{mod}-shot-{token}-\d{3}-[a-z]+\.png$`. The token grammar must be pinned to
  `[a-z0-9-]+-[0-9a-f]{4}` rather than left open, or the pattern stops being
  unambiguous against the three-digit index that follows it. Anchoring at both
  ends stays load-bearing for the reason the docstring already gives.
- `server.heartbeat` changes shape. It reads off disk with no session, so it
  globs `<mod>-hooks-*.txt` AND the unsuffixed `<mod>-hooks.txt`, and returns a
  LIST of client entries — one per player, plus at most one player-less entry
  for a client that has not got a character yet. This is a breaking change to
  `HeartbeatOut`, and it makes the tool answer a question it currently cannot:
  which clients are alive. The server entry is unchanged.
- `docs/MOD_CONTRACT.md`: the naming rule, the token grammar, and the
  "Known limitation: one client per side" section, which this removes.

## Testing

Three layers, because the interesting failures are at the boundary.

**Unit, both sides.** The token rule gets the same table of names in C# and in
Python, asserted to produce identical tokens — a rule computed independently on
two sides is a rule that can disagree, and the table is the only thing that
would catch it. Registry, gate, payload grammar and region resolution keep
Biomancy's existing tests, moved with the code.

**Contract, structural.** `tests/test_mod_contract_doc.py` already fails in both
directions — if the protocol gains a file the document does not mention, or the
document describes a file nothing writes. It must be extended to the new names
rather than left matching the old ones, or it will pass against a contract
nobody implements.

**Real execution.** The extraction is only proven by Biomancy running on the
vendored copy: build, launch, `commands`, `diag` both sides, `shot`, and a
capture read back. Two clients is the case that has never been runnable —
launch a second client against the same server and assert each writes its own
heartbeat, diag and shot, with neither consuming the other's trigger.

Per the standing rule: every new test is run against the pre-change state first
and seen to fail for its stated reason, and each negative assertion carries a
positive control in the same test.

## Order

1. `responder/` created in `tmodloader-mcp` — the eight files moved, namespace
   changed, `DevResponder.cs` written, tests moved.
2. Biomancy vendors it and `DevCapture` becomes a subclass. Nothing renames yet;
   this step proves the split alone, against a live game.
3. Per-player naming, both sides and the contract doc together.
4. Two-client live run — the case that could not be tested before.
5. `README.md` Phase 2 item 2 closed; item 4 (template mod) reassessed, since a
   vendorable folder with a README may be most of what it asked for.

Steps 2 and 3 are separate on purpose. Folding them together means a live
failure has two candidate causes, and the whole point of this bridge is not
having to guess which.

## Out of scope

- Publishing to PyPI and making the repo public. Both are one command and belong
  after there is something to point at.
- A template mod, until step 5 says whether one is still needed.
- Anything about singleplayer or a bare dedicated server. Both are measured
  engine limits, already refused with the measurement.
