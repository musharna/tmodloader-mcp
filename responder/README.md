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
it. Take the `.cs` files — `tests/` is this repository's proof and is not yours
to carry.

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

Two files are excluded from that project: `FrameShot.cs` and `DevResponder.cs`
need Terraria, XNA and `ModSystem`, none of which exist on a build runner. They
are covered by a source-scan contract test plus a live run against a real game.
