# A mod you can clone and build

`DevBridgeTemplate/` is a complete tModLoader mod that answers this harness. It
is the smallest thing that can be launched, asked for a diag, photographed and
driven — and it is checked in as a mod rather than as instructions, because a
template nobody can build proves nothing.

## Use it

```sh
cp -r template/DevBridgeTemplate "$TMODLOADER_SAVE_DIR/ModSources/MyMod"
# then rename the folder, build.txt's displayName, and the namespace
```

The FOLDER NAME is the mod's internal name, and everything else follows from
it: tModLoader takes the mod's name from the folder, the responder derives its
artifact filenames from that name, and `DevBridgeGate` looks for a
`ModSources/<that name>/` directory to decide whether this is a developed
install or a played one. Rename the folder and the whole chain moves with it.

Build it the way you would build any mod — `build_mod` here, or tModLoader's own
`-build`. Verified on 2026-08-17: 0 errors, 0 warnings, with a deliberate syntax
error in `DevBridge/DevMutations.cs` first confirming that the vendored folder is
genuinely on the compile line rather than quietly skipped.

## What is in it

| File                   | What it is                                                            |
| ---------------------- | --------------------------------------------------------------------- |
| `build.txt`            | tModLoader's manifest. Yours will differ.                             |
| `DevBridgeTemplate.cs` | The `Mod` class, deliberately empty.                                  |
| `TemplateResponder.cs` | **The whole of what a mod has to write.**                             |
| `DevBridge/`           | A byte-identical copy of [`responder/`](../responder/). Never edited. |

`TemplateResponder.cs` is the file to read. Two members: `CollectDiag`, which is
what your mod knows that is worth reading back, and `RegisterCommands`, which is
optional and here turns on the world-changing verbs.

## Why the copy is checked in

tModLoader's `-build` compiles the mod DIRECTORY, so a file outside it is not on
the compile line at all. A template that said "copy `responder/` in here" would
not compile as checked in, and could not be built by CI, by this harness, or by
anybody evaluating it.

A checked-in copy rots, so `tests/test_template_mod.py` asserts the two
directories are byte-identical and prints the one-line `cp` that fixes them.
Drift is a red test on the commit that caused it rather than a mod that stopped
compiling six months ago. Re-sync with:

```sh
cp responder/*.cs responder/SHA256SUMS template/DevBridgeTemplate/DevBridge/
```

## What building it proves

Three things this repository previously argued rather than checked:

1. **The vendored folder compiles as vendored.** `responder/tests/` compiles
   eight of the eleven files with nothing of any mod's on the line — the other
   three need Terraria, XNA and `ModSystem`. This compiles all eleven against a
   real tModLoader, which is the only proof that the folder a consumer copies is
   a folder that builds.
2. **The documented subclass is the subclass that works.**
   [`responder/README.md`](../responder/README.md) shows a `CollectDiag` and a
   `RegisterCommands`. Nothing checked that the snippet compiled; this is that
   snippet, grown just enough to run.
3. **The opt-in line is real.** `DevMutations.RegisterInto(r, Report)` is the
   only place in this repository that turns the world-changing verbs on, so it
   is both the worked example and the only thing a live run can drive them from.
