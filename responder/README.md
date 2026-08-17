# The mod-side half

This folder is the responder that [`docs/MOD_CONTRACT.md`](../docs/MOD_CONTRACT.md)
describes. Copy it into your mod's source tree, subclass one class, and the
harness can drive your mod.

It is source, not a package. tModLoader has no dependency mechanism for
compile-time C# — a `modReference` links a built `.tmod`, which is a different
thing — so vendoring the files is not a shortcut around packaging, it is what
packaging would look like here.

## Four things

**1. Copy the folder** into your mod's source tree. Anywhere tModLoader compiles
is fine; `Common/DevBridge/vendored/` is where the reference implementation puts
it. Take the `.cs` files and `SHA256SUMS` — `tests/` is this repository's proof
and is not yours to carry.

**2. Subclass `DevResponder`.** It is a `ModSystem`, so tModLoader finds and
loads it with no registration on your part.

**3. Override `CollectDiag`** — what your mod knows that is worth reading back.

**4. Override `RegisterCommands`** if you want verbs of your own. Optional; the
base class already answers `capture`, `diag` and `shot`.

```csharp
using TModLoaderMcp.DevBridge;

public class MyDevResponder : DevResponder
{
    protected override string CollectDiag() =>
        "world: " + (Terraria.Main.worldName ?? "none") + "\n";

    protected override void RegisterCommands(DevCommandRegistry r) =>
        r.Register("spawnboss", takesArgument: true, "Spawn a boss by name",
            req => Report("spawned " + req.Argument));
}
```

`Report` is `protected static` on the base class — it is how a handler answers,
and every handler needs it.

## Verbs that change the world, which are off

Everything above READS. `capture`, `diag` and `shot` observe a world and write
files outside it. `DevMutations` is five verbs that change the one you are
sitting in — `time`, `weather`, `spawn`, `give`, `teleport` — and it is
registered by nobody until you write this:

```csharp
protected override void RegisterCommands(DevCommandRegistry r) =>
    DevMutations.RegisterInto(r, Report);
```

That line is the whole of the opt-in. Not a setting, not a marker file, not an
environment variable — each of those can be switched on somewhere other than the
source somebody will read when they ask why an NPC appeared in their world. It
also means re-syncing this folder can never give your mod a power it did not
have before, which is the property that makes vendoring an upgrade safe.

`DevBridgeGate` still applies underneath: a played install runs none of it,
whatever you register.

Each verb refuses the side that cannot do it and names the side that can.
`time`, `weather` and `spawn` are refused on a multiplayer **client** — the
server owns the clock, the weather and the NPC array, and a client that changed
them would be corrected by the next world packet, so the change would appear to
work and then undo itself. `give` and `teleport` are refused on a **dedicated
server**, which runs the world without standing in it. Singleplayer does
everything, being both sides at once.

The rules live in `DevMutationArgs.cs`, which imports nothing but `System` and
is therefore tested by running it; `DevMutations.cs` imports Terraria and is the
thin applier. If you want verbs of your own with the same property, that split
is the one to copy.

## The heartbeat before you have a character

**Keep writing the unsuffixed `<mod>-hooks.txt` heartbeat until a local player
exists.** `DevResponder` already does this for you — `AnswerPathFor` falls back
to the plain name whenever there is no local character, which is exactly the
state of a client sitting at the menu or mid-join. This is not a fallback to
rediscover by reading the source; it is spelled out here because getting it
wrong looks like nothing at all until somebody watches a launch fail.

The reason it matters: [`docs/MOD_CONTRACT.md`](../docs/MOD_CONTRACT.md#the-heartbeat)
has the harness wait on BOTH the unsuffixed heartbeat and this client's
per-player one, because a world becomes ready at exactly the moment a
character loads — the same tick the heartbeat's name changes out from under
whichever file the harness was watching. A responder that stopped writing the
unsuffixed name before a character existed — say, by writing only the tokened
form and leaving it absent until then — would produce no heartbeat at all
during exactly the window `launch` is waiting through, and the failure would
read as a hung game rather than as a naming bug in the responder.

## The one thing that is easy to get wrong

**`CollectDiag` must produce the grammar in
[`docs/MOD_CONTRACT.md`](../docs/MOD_CONTRACT.md),** because the harness parses
it rather than passing it through. `key: value` lines become typed fields;
indented lines under a key become that key's records. Get the shape wrong and
`diag` will not fail — it will return fewer fields than you wrote, which is the
harder failure to notice.

## It does not run on a player's machine

`DevBridgeGate` checks for `ModSources/<YourMod>/` beside the `Mods/` folder the
game loads from. A developer building the mod has that directory by definition —
it is what `-build` is pointed at. Somebody who subscribed to the finished mod
has no reason to own a folder named after your mod's internals.

This is deliberately not `#if DEBUG`: tModLoader's `-build` does not define
`DEBUG` (measured — a conditional `#warning` reported FNA as the only symbol
defined at all), so a compile-time flag would strip the responder out of the
_developer's_ build too, and the only symptom would be a harness waiting on a
game that was never going to answer. The deeper reason no compile-time flag can
work: the `.tmod` a player loads is the same artifact the developer builds,
identical bytes. What differs is the install around them, and that is what this
reads.

## Checking your copy

Copy `SHA256SUMS` along with the `.cs` files. It is a fingerprint of the folder
at the moment you took it, and it answers the two questions a vendored copy
eventually raises.

**"Has anyone edited my copy?"** — offline, no network:

```sh
cd path/to/your/vendored/copy && sha256sum -c SHA256SUMS
```

A mismatch means somebody patched the copy instead of upstream. That patch will
be destroyed the next time you re-sync, so it wants to go upstream before then.

**"Am I behind upstream?"** — one small file to compare rather than nine
sources:

```sh
curl -s https://raw.githubusercontent.com/musharna/tmodloader-mcp/master/responder/SHA256SUMS \
  | diff - SHA256SUMS && echo "in sync"
```

Upstream CI keeps `SHA256SUMS` honest: a test regenerates the hashes and fails
if they disagree with the sources, so the manifest cannot quietly describe an
older version of the folder than the one sitting beside it.

Re-syncing is a copy, not a merge — these files are meant to be byte-identical
to upstream, which is the property both checks rest on.

## Why this was hard to extract, since the shape now looks obvious

The generic half and Biomancy's half used to be one enum and one switch. The
generic half could not be lifted out without the specific half coming along —
which is why this is an extraction with a base class and a registry rather than
a file move. `DevCommandRegistry` exists so a mod's verbs and the responder's
own three can live in separate classes and still be published as one ordered
list.

## What proves this folder is yours and not Biomancy's

`responder/tests/` compiles these files with **nothing of any mod's on the
compile line**, and CI runs it on a machine with no tModLoader installed. That
is a stronger claim than reading the source and seeing no `using Biomancy` — it
is checked by the compiler on every push.

Three files are excluded from that project: `FrameShot.cs`, `DevResponder.cs`
and `DevMutations.cs` need Terraria, XNA and `ModSystem`, none of which exist on
a build runner. They are covered by source-scan contract tests plus a live run
against a real game — and by
[`template/`](../template/), a whole mod that compiles all eleven files against
a real tModLoader. That template is also the worked example of everything on
this page: it is the subclass, the diag grammar and the opt-in line, in a mod
you can build.
