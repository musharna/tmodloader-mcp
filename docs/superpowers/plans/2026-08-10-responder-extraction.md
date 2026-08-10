# Responder Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the generic half of Biomancy's dev responder into a `responder/`
folder in this repo that any mod can vendor, and prove it by making Biomancy run
on the vendored copy.

**Architecture:** Eight C# files move out of `Biomancy/Common/DevBridge/` into
`tmodloader-mcp/responder/` under a neutral namespace. A new abstract
`DevResponder : ModSystem` holds the generic half currently tangled into
Biomancy's 1442-line `DevCapture.cs`; a consuming mod subclasses it and
overrides two members. A .NET test project in this repo compiles the folder
with nothing of Biomancy's on the compile line, which is the compile-level
proof of vendorability that a source scan alone cannot give.

**Tech Stack:** C# / .NET 8 (xunit), Python 3.12+ (pytest), GitHub Actions.

## Global Constraints

- Namespace for every file in `responder/` is `TModLoaderMcp.DevBridge`. Exact,
  no variants.
- .NET 8. Pin with a `global.json` (`version: 8.0.0`, `rollForward: latestMinor`)
  — without one, `dotnet` takes the highest SDK on the machine and CI compiles
  with a different toolchain than local runs. Biomancy learned this; do not
  rediscover it.
- **THERE IS NO `dotnet` IN WSL.** `which dotnet` fails. The only SDK on this
  machine is the Windows one, and it is the 8.0.423 that Biomancy's `global.json`
  comment already refers to. Every local invocation in this plan uses:

  ```bash
  DOTNET="/mnt/c/Program Files/dotnet/dotnet.exe"
  ```

  Verified this session: `dotnet.exe` restores and builds a WSL-native path
  through `\\wsl.localhost\Ubuntu\...`, so `responder/` living under `/home` is
  not an obstacle. Expect UNC paths in its output; that is normal, not a fault.
  The CI job is unaffected — an ubuntu runner has a real `dotnet` and keeps the
  bare command.

- `ArtifactHash.cs` does NOT move. Its only caller is `DiagCollector.cs:354`,
  which is Biomancy's.
- No test may require a running game. `tests/live_check.py` is this repo's home
  for anything that does; Task 6 is a manual live run, not a collected test.
- Every new test is run against the pre-change state first and seen to FAIL for
  its stated reason. Every negative assertion (`no offenders`, `refused`) ships
  with a positive control in the same test proving the scan/harness works.
- `git commit` at the end of every task. Never leave a task half-committed.

---

### Task 1: A test project that compiles the vendored folder

The compile-level proof. `DevBridgeBoundaryTests.cs:24` says one "would need a
second project — which is worth building the day the bridge is actually
vendored." This is that project.

**Files:**

- Create: `responder/DevArtifacts.cs`, `responder/DevBridgeGate.cs`,
  `responder/DevCommandRegistry.cs`, `responder/DevCommands.cs`,
  `responder/ShotRegion.cs`, `responder/CaptureBounds.cs`,
  `responder/CaptureFind.cs`
- Create: `responder/tests/Responder.Tests.csproj`
- Create: `responder/tests/DevCommandsTests.cs`,
  `responder/tests/DevCommandRegistryTests.cs`,
  `responder/tests/DevBridgeGateTests.cs`,
  `responder/tests/DevArtifactsTests.cs`,
  `responder/tests/DevArtifactNamesTests.cs`,
  `responder/tests/ShotRegionTests.cs`,
  `responder/tests/CaptureBoundsTests.cs`,
  `responder/tests/CaptureFindTests.cs`
- Create: `global.json`

**Interfaces:**

- Consumes: nothing.
- Produces: the namespace `TModLoaderMcp.DevBridge` containing `DevArtifactNames`
  (ctor `DevArtifactNames(string modName)`; properties `Prefix`, `Trigger`,
  `Result`, `Diag`, `Heartbeat`, `Shot`, `Commands`), `DevArtifacts.ForSide(string
name, bool dedicatedServer)`, `DevBridgeGate.EnabledFor(string savePath, string
modName)` / `.SourcePathFor` / `.Explain`, `DevCommandRegistry` (`Register(string
name, bool takesArgument, string summary, DevCommandHandler handler)`,
  `TryResolve`, `Count`, `Entries`, `Names`, `Publish()`), `DevCommands.Parse(string
raw)` returning `DevRequest` (`Verb`, `Argument`, `Target`, `IsMalformed`,
  `IsFor(string)`), `ShotRegion` (`TryResolve`, `Names`), `CaptureBounds`,
  `CaptureFind`. Every later task uses these names.

Source of every file: `/mnt/c/Users/a2b32/Documents/My Games/Terraria/tModLoader/ModSources/Biomancy`.
Referred to below as `$BIOMANCY`.

- [ ] **Step 1: Copy the seven headless files and change one line each**

```bash
BIOMANCY="/mnt/c/Users/a2b32/Documents/My Games/Terraria/tModLoader/ModSources/Biomancy"
mkdir -p responder/tests
for f in DevArtifacts DevBridgeGate DevCommandRegistry DevCommands ShotRegion CaptureBounds CaptureFind; do
  cp "$BIOMANCY/Common/DevBridge/$f.cs" "responder/$f.cs"
done
# The ONLY edit. Namespace, not content.
sed -i 's/^namespace Biomancy\.Common\.DevBridge$/namespace TModLoaderMcp.DevBridge/' responder/*.cs
grep -c "namespace TModLoaderMcp.DevBridge" responder/*.cs
```

Expected: `1` for all seven files. `FrameShot.cs` is deliberately NOT here — it
imports Terraria and XNA and cannot compile headlessly. Task 2 handles it.

Do not reformat, retab or reflow these files. They carry long comments that are
the reason the code is the shape it is, and a reformat makes the diff
unreviewable for zero gain.

- [ ] **Step 2: Pin the SDK**

Create `global.json`:

```json
{
  "$comment": "setup-dotnet INSTALLS an SDK; it does not SELECT one. Without this, `dotnet` takes the highest SDK on the machine, so CI would compile net8.0 with the runner's preinstalled SDK 10 while every local run used 8.0.x - two toolchains, one green check. rollForward latestMinor keeps any 8.x usable.",
  "sdk": {
    "version": "8.0.0",
    "rollForward": "latestMinor"
  }
}
```

- [ ] **Step 3: Write the test project**

Create `responder/tests/Responder.Tests.csproj`. `EnableDefaultCompileItems` is
false and every file is listed, deliberately: an implicit glob would silently
pick up a file somebody drops in, and the point of this project is that its
compile line contains NOTHING but the vendorable folder.

```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>disable</Nullable>
    <IsPackable>false</IsPackable>
    <EnableDefaultCompileItems>false</EnableDefaultCompileItems>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.11.1" />
    <PackageReference Include="xunit" Version="2.9.2" />
    <PackageReference Include="xunit.runner.visualstudio" Version="2.8.2" />
  </ItemGroup>
  <ItemGroup>
    <!-- THE PROOF. Nothing of any mod's appears on this list. FrameShot is
         absent because it imports Terraria and XNA, which are not on a build
         machine; it is covered by the source scan in DevBridgeBoundaryTests
         instead, and that limit is stated there. -->
    <Compile Include="../DevArtifacts.cs" />
    <Compile Include="../DevBridgeGate.cs" />
    <Compile Include="../DevCommandRegistry.cs" />
    <Compile Include="../DevCommands.cs" />
    <Compile Include="../ShotRegion.cs" />
    <Compile Include="../CaptureBounds.cs" />
    <Compile Include="../CaptureFind.cs" />
  </ItemGroup>
  <ItemGroup>
    <Compile Include="DevCommandsTests.cs" />
    <Compile Include="DevCommandRegistryTests.cs" />
    <Compile Include="DevBridgeGateTests.cs" />
    <Compile Include="DevArtifactsTests.cs" />
    <Compile Include="DevArtifactNamesTests.cs" />
    <Compile Include="ShotRegionTests.cs" />
    <Compile Include="CaptureBoundsTests.cs" />
    <Compile Include="CaptureFindTests.cs" />
  </ItemGroup>
</Project>
```

- [ ] **Step 4: Run it and watch it fail for the right reason**

```bash
"$DOTNET" test responder/tests/Responder.Tests.csproj
```

Expected: FAIL — the eight test files do not exist yet, so the build errors with
`CS2001: Source file ... could not be found`. That is the correct failure. If it
fails with a namespace or type error instead, Step 1's `sed` did not take;
fix that before continuing.

- [ ] **Step 5: Copy the eight test files and change their namespace usage**

```bash
for f in DevCommandsTests DevCommandRegistryTests DevBridgeGateTests \
         DevArtifactsTests DevArtifactNamesTests ShotRegionTests \
         CaptureBoundsTests CaptureFindTests; do
  cp "$BIOMANCY/tests/BiomancyMod.Tests/$f.cs" "responder/tests/$f.cs"
done
sed -i 's/^using Biomancy\.Common\.DevBridge;$/using TModLoaderMcp.DevBridge;/' responder/tests/*.cs
sed -i 's/^namespace BiomancyMod\.Tests$/namespace TModLoaderMcp.DevBridge.Tests/' responder/tests/*.cs
```

Some of these files may reference the bridge types without a `using` because
Biomancy's test namespace sat near them. If the build complains about an
unresolved type, add `using TModLoaderMcp.DevBridge;` to that file — do not
change the test bodies.

- [ ] **Step 6: Run the tests and watch them pass**

```bash
"$DOTNET" test responder/tests/Responder.Tests.csproj
```

Expected: PASS, all tests. Record the count — Task 2 and Task 5 both compare
against it.

- [ ] **Step 7: Commit**

```bash
git add global.json responder/
git commit -m "feat(responder): the vendorable half, with a compile line that proves it"
```

---

### Task 2: `FrameShot`, and the boundary made enforceable

`FrameShot.cs` cannot go on the compile line — it imports `Terraria` and
`Microsoft.Xna.Framework`. It still has to ship, and the folder still has to be
provably free of any mod's code. A source scan covers what the compiler cannot.

**Files:**

- Create: `responder/FrameShot.cs`
- Create: `responder/tests/VendorBoundaryTests.cs`
- Modify: `responder/tests/Responder.Tests.csproj` (add the new test file)

**Interfaces:**

- Consumes: the namespace and types from Task 1.
- Produces: nothing other tasks call. This is a guard.

- [ ] **Step 1: Write the failing test**

Create `responder/tests/VendorBoundaryTests.cs`. Adapted from Biomancy's
`DevBridgeBoundaryTests.cs`, keeping its two controls — the scan must be proven
to find the folder, and the comment stripper must be proven to keep code —
because every other assertion here is a "nothing was found" and would pass on a
broken scan.

```csharp
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Xunit;

namespace TModLoaderMcp.DevBridge.Tests
{
    /// <summary>
    /// responder/ is meant to be COPIED INTO A MOD as-is. The csproj beside this
    /// file proves seven of the eight compile with nothing of any mod's on the
    /// line. FrameShot cannot be on that line - it imports Terraria and XNA -
    /// so it is covered here, by reading it.
    ///
    /// A source scan, with the limits a source scan has: comments are stripped
    /// and code lines read, so it cannot see a reference made by reflection or
    /// assembled from strings. Neither appears in these files.
    /// </summary>
    public class VendorBoundaryTests
    {
        private const string Namespace = "namespace TModLoaderMcp.DevBridge";

        /// <summary>Names that would mean the folder came from one mod's tree.</summary>
        private static readonly string[] ForeignTypes = {
            "DevCapture", "DiagCollector", "DiagReport", "DiagCommand", "Biomancy",
        };

        private static string RepoRoot() {
            string dir = Directory.GetCurrentDirectory();
            while (dir != null && !Directory.Exists(Path.Combine(dir, "responder"))) {
                dir = Directory.GetParent(dir)?.FullName;
            }

            Assert.True(dir != null,
                "could not find a repository root with a responder/ above " +
                Directory.GetCurrentDirectory());
            return dir;
        }

        private static string[] BridgeFiles() =>
            Directory.GetFiles(Path.Combine(RepoRoot(), "responder"), "*.cs",
                SearchOption.TopDirectoryOnly);

        private static string CodeOnly(string body) {
            var kept = new List<string>();

            foreach (string line in body.Split('\n')) {
                if (line.TrimStart().StartsWith("//")) {
                    continue;
                }

                int slashes = line.IndexOf("//", StringComparison.Ordinal);
                kept.Add(slashes >= 0 ? line.Substring(0, slashes) : line);
            }

            return string.Join("\n", kept);
        }

        /// <summary>
        /// POSITIVE CONTROL, first deliberately. Every assertion below is a
        /// "nothing was found", so a wrong directory or a bad pattern satisfies
        /// all of them while proving nothing.
        /// </summary>
        [Fact]
        public void TheScanActuallyFindsTheFolder() {
            string[] files = BridgeFiles();

            Assert.True(files.Length >= 8,
                "expected at least the eight vendorable files, found " + files.Length +
                " - the scan itself is broken");

            var names = files.Select(Path.GetFileName).ToArray();
            Assert.Contains("DevCommands.cs", names);
            Assert.Contains("DevCommandRegistry.cs", names);
            Assert.Contains("FrameShot.cs", names);
        }

        /// <summary>CONTROL for the scan below: a stripper that ate everything
        /// would make the check pass on any file at all.</summary>
        [Fact]
        public void TheCommentStripperKeepsCode() {
            string stripped = CodeOnly(
                "/// <summary>DevCapture wrote this</summary>\n" +
                "public void Thing() { // DiagCollector\n" +
                "\tint x = 1;\n" +
                "}\n");

            Assert.Contains("public void Thing()", stripped);
            Assert.Contains("int x = 1;", stripped);
            Assert.DoesNotContain("DevCapture", stripped);
            Assert.DoesNotContain("DiagCollector", stripped);
        }

        [Fact]
        public void NoFileReferencesAnyModsOwnCode() {
            var offenders = new List<string>();

            foreach (string path in BridgeFiles()) {
                string code = CodeOnly(File.ReadAllText(path));

                foreach (string type in ForeignTypes) {
                    if (code.Contains(type)) {
                        offenders.Add(Path.GetFileName(path) + " -> " + type);
                    }
                }
            }

            Assert.True(offenders.Count == 0,
                "responder/ is copied into a mod as-is, and these name code that " +
                "would not come with it: " + string.Join("; ", offenders));
        }

        [Fact]
        public void EveryFileDeclaresTheVendorNamespace() {
            var offenders = new List<string>();

            foreach (string path in BridgeFiles()) {
                if (!File.ReadAllText(path).Contains(Namespace)) {
                    offenders.Add(Path.GetFileName(path));
                }
            }

            Assert.True(offenders.Count == 0,
                "these sit in responder/ but do not declare " + Namespace +
                ", so they would fail to compile in a consuming mod: " +
                string.Join(", ", offenders));
        }
    }
}
```

- [ ] **Step 2: Add it to the project and run it to verify it fails**

Add `<Compile Include="VendorBoundaryTests.cs" />` to the second `ItemGroup` of
`responder/tests/Responder.Tests.csproj`.

```bash
"$DOTNET" test responder/tests/Responder.Tests.csproj --filter VendorBoundaryTests
```

Expected: FAIL on `TheScanActuallyFindsTheFolder` — `found 7`, because
`FrameShot.cs` has not been copied yet. That is the positive control doing its
job on its first run, which is the best possible evidence it works.

- [ ] **Step 3: Copy FrameShot**

```bash
cp "$BIOMANCY/Common/DevBridge/FrameShot.cs" responder/FrameShot.cs
sed -i 's/^namespace Biomancy\.Common\.DevBridge$/namespace TModLoaderMcp.DevBridge/' responder/FrameShot.cs
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
"$DOTNET" test responder/tests/Responder.Tests.csproj
```

Expected: PASS, all of them, count = Task 1's count + 4.

If `NoFileReferencesAnyModsOwnCode` fails on `FrameShot.cs`, read the offending
line. A comment mentioning Biomancy is fine and gets stripped; a code reference
is a real finding and means the file was never as vendorable as believed —
report it rather than weakening the scan.

- [ ] **Step 5: Commit**

```bash
git add responder/
git commit -m "feat(responder): ship FrameShot, and prove the folder carries no mod's code"
```

---

### Task 3: CI runs the .NET tests

A test project nothing runs is a claim. This repo's own workflow comment already
says a check that cannot fire on the PR introducing it is worthless.

**Files:**

- Modify: `.github/workflows/tests.yml`

**Interfaces:**

- Consumes: `responder/tests/Responder.Tests.csproj` from Tasks 1-2.
- Produces: nothing other tasks call.

- [ ] **Step 1: Add a second job**

Append to `.github/workflows/tests.yml`, as a sibling of the existing `test`
job (same indentation level, under `jobs:`):

```yaml
# The C# half. It compiles responder/ with nothing of any mod's on the compile
# line, which is the whole vendorability claim - so it runs here rather than
# depending on somebody having a game installed. It does NOT build a mod:
# `tModLoader.dll -build` needs the engine and a real install, and FrameShot
# is excluded from this project for the same reason.
responder:
  runs-on: ubuntu-latest
  timeout-minutes: 10

  steps:
    - uses: actions/checkout@v7

    # global.json pins 8.0.x. Without it `dotnet` takes the highest SDK on the
    # runner, which is not the one anybody develops against.
    - uses: actions/setup-dotnet@v4
      with:
        dotnet-version: "8.0.x"

    - name: dotnet test
      run: dotnet test responder/tests/Responder.Tests.csproj --verbosity normal
```

- [ ] **Step 2: Verify the workflow parses**

```bash
python -c "import yaml,sys; d=yaml.safe_load(open('.github/workflows/tests.yml')); print(sorted(d['jobs']))"
```

Expected: `['responder', 'test']`. If `yaml` is unavailable, `uv run python -c ...`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/tests.yml
git commit -m "ci: run the responder's tests, so the compile-line proof is a gate"
```

---

### Task 4: `DevResponder`, the base every consumer subclasses

**Files:**

- Create: `responder/DevResponder.cs`
- Create: `responder/tests/DevResponderContractTests.cs`
- Modify: `responder/tests/Responder.Tests.csproj` (add the new test file)
- Read for reference: `$BIOMANCY/Common/Diagnostics/DevCapture.cs`

**Interfaces:**

- Consumes: everything from Task 1.
- Produces: `public abstract class DevResponder : ModSystem` in
  `TModLoaderMcp.DevBridge`, with `protected virtual void
RegisterCommands(DevCommandRegistry r)` and `protected abstract string
CollectDiag()`. Task 5 subclasses exactly these two signatures.

`DevResponder.cs` cannot go on the test project's compile line — it extends
`ModSystem`. It is verified two ways: the source-contract test below, and
Biomancy actually running on it in Task 6. Neither alone is enough and the plan
does not pretend otherwise.

- [ ] **Step 1: Write the failing test**

Create `responder/tests/DevResponderContractTests.cs`:

```csharp
using System.IO;
using System.Text.RegularExpressions;
using Xunit;

namespace TModLoaderMcp.DevBridge.Tests
{
    /// <summary>
    /// DevResponder extends ModSystem, so it cannot be on this project's compile
    /// line and cannot be instantiated here. What CAN be checked without a game
    /// is the shape a consumer codes against - and the shape is the contract:
    /// the two members they override, and the order the three generic verbs are
    /// registered in.
    ///
    /// A source scan, and it says so. Biomancy running on this in Task 6 is the
    /// other half; neither is sufficient alone.
    /// </summary>
    public class DevResponderContractTests
    {
        private static string Source() {
            string dir = Directory.GetCurrentDirectory();
            while (dir != null && !Directory.Exists(Path.Combine(dir, "responder"))) {
                dir = Directory.GetParent(dir)?.FullName;
            }

            Assert.True(dir != null, "no responder/ above " + Directory.GetCurrentDirectory());
            string path = Path.Combine(dir, "responder", "DevResponder.cs");
            Assert.True(File.Exists(path), "expected " + path);
            return File.ReadAllText(path);
        }

        /// <summary>POSITIVE CONTROL: the reader found real source, not "".</summary>
        [Fact]
        public void TheSourceIsActuallyRead() {
            Assert.Contains("namespace TModLoaderMcp.DevBridge", Source());
        }

        [Fact]
        public void ItIsAbstractSoAModMustSupplyItsOwnDiag() {
            Assert.Matches(new Regex(@"public\s+abstract\s+class\s+DevResponder\s*:\s*ModSystem"),
                Source());
        }

        /// <summary>
        /// Abstract rather than virtual-with-a-default. A default would be an
        /// empty dump that satisfies the compiler and fails MOD_CONTRACT, which
        /// is the failure this whole channel exists to make loud.
        /// </summary>
        [Fact]
        public void CollectDiagIsAbstract() {
            Assert.Matches(new Regex(@"protected\s+abstract\s+string\s+CollectDiag\s*\(\s*\)\s*;"),
                Source());
        }

        [Fact]
        public void RegisterCommandsIsVirtualAndOptional() {
            Assert.Matches(
                new Regex(@"protected\s+virtual\s+void\s+RegisterCommands\s*\(\s*DevCommandRegistry\s+\w+\s*\)"),
                Source());
        }

        /// <summary>
        /// The three the harness relies on are registered BEFORE the mod's, so
        /// the published list always leads with them and a mod cannot displace
        /// one by registering the same verb first - Register throws on a
        /// duplicate, which is the behaviour that makes this ordering a rule
        /// rather than a preference.
        /// </summary>
        [Fact]
        public void GenericVerbsAreRegisteredBeforeTheModsOwn() {
            string src = Source();

            int capture = src.IndexOf("Register(\"capture\"");
            int diag = src.IndexOf("Register(\"diag\"");
            int shot = src.IndexOf("Register(\"shot\"");
            int hook = src.IndexOf("RegisterCommands(");

            Assert.True(capture >= 0, "capture is not registered");
            Assert.True(diag >= 0, "diag is not registered");
            Assert.True(shot >= 0, "shot is not registered");
            Assert.True(hook >= 0, "the mod's hook is never called");

            Assert.True(capture < hook && diag < hook && shot < hook,
                "a generic verb is registered after the mod's hook, so a mod " +
                "could take the name and the harness would lose the verb it needs");
        }
    }
}
```

- [ ] **Step 2: Add it to the project and run to verify it fails**

Add `<Compile Include="DevResponderContractTests.cs" />` to the csproj.

```bash
"$DOTNET" test responder/tests/Responder.Tests.csproj --filter DevResponderContractTests
```

Expected: FAIL on `TheSourceIsActuallyRead` — `expected .../responder/DevResponder.cs`,
because the file does not exist. Every other test in the class fails the same
way. That is correct.

- [ ] **Step 3: Write `DevResponder.cs`**

Move, do not rewrite. Take these members from `$BIOMANCY/Common/Diagnostics/DevCapture.cs`
verbatim, including their comments — the comments record measurements (the
engine crash from a pre-armed trigger, the stale-static reload trap, the
heartbeat that reported a value older than the fact it described) and rewriting
them loses the evidence:

| from `DevCapture.cs`                                                                 | what it is                                    |
| ------------------------------------------------------------------------------------ | --------------------------------------------- |
| `_names`, `Names`, `_commands` (60-74)                                               | fields                                        |
| `IsLoadingEnabled` (92)                                                              | the gate                                      |
| `Load`, `Unload` (104, 110)                                                          | lifecycle                                     |
| `PublishCommands` (284)                                                              | writes `<mod>-commands.txt`                   |
| `TriggerName`/`ResultName`/`HeartbeatName`/`DiagName` (293-314)                      | name accessors                                |
| `HeartbeatMaxAge`, `WorldSettle`, `_triggerState`, `_armed` and neighbours (357-432) | poll state                                    |
| `PostUpdateInput`/`UpdateUI`/`PostUpdateEverything`/`PostUpdateWorld` (432-438)      | the four hooks                                |
| `OnWorldLoad`, `OnWorldUnload` (444, 450)                                            | settle/arm                                    |
| `Tick` (458)                                                                         | the poll, including the addressee rule at 553 |
| `Dispatch` (592)                                                                     | verb resolution and the two argument refusals |
| `CaptureNow` (636)                                                                   | `capture`                                     |
| `TakeShot` (752), `SettleShot` (772)                                                 | `shot`                                        |
| `Observe` (1228), `WriteHeartbeatIfStale` (1248)                                     | heartbeat                                     |
| `ViewRectProblem` (1320), `Begin` (1326), `Settle` (1350), `List` (1382)             | capture camera                                |
| `LocalPlayerName` (1413), `PathFor` (1429), `Report` (1433)                          | helpers                                       |

Then change exactly three things:

1. `public class DevCapture : ModSystem` becomes
   `public abstract class DevResponder : ModSystem`, in namespace
   `TModLoaderMcp.DevBridge`.
2. `BuildCommands` keeps only the three generic registrations and then calls the
   hook:

```csharp
private DevCommandRegistry BuildCommands() {
    var r = new DevCommandRegistry();

    r.Register("capture", false,
        "Save a PNG of the whole frame via Terraria's own capture camera.",
        _ => CaptureNow());

    r.Register("diag", false,
        "Write this side's state dump, from a live session.",
        _ => WriteDiag());

    r.Register("shot", true,
        "Save a PNG of one region of the frame, from the back buffer (" +
            ShotRegion.Names + ").",
        req => TakeShot(req.Argument));

    // The mod's own, AFTER the three above. Register throws on a duplicate, so
    // a mod that tries to take one of these names fails at load with a sentence
    // naming the verb - rather than silently replacing a verb the harness needs.
    RegisterCommands(r);

    return r;
}

/// <summary>This mod's own verbs. Nothing is registered after this.</summary>
protected virtual void RegisterCommands(DevCommandRegistry r) { }
```

3. `WriteDiag` stops naming Biomancy's collector and calls the hook:

```csharp
/// <summary>
/// Write this side's state where the harness reads it.
///
/// The FILE, the write and the failure report are the responder's; only the
/// content is the mod's. A mod that could not be asked for a diag would
/// satisfy this class and fail MOD_CONTRACT, which is why CollectDiag is
/// abstract rather than defaulted to an empty dump.
/// </summary>
private void WriteDiag() {
    try {
        File.WriteAllText(PathFor(DiagName), CollectDiag());
        Report("DIAG: " + PathFor(DiagName));
    }
    catch (Exception e) {
        Report("ERROR: diag failed: " + e);
    }
}

/// <summary>The body of &lt;mod&gt;-diag.txt. See docs/MOD_CONTRACT.md for the
/// grammar the harness parses.</summary>
protected abstract string CollectDiag();
```

Leave the nine Biomancy handlers behind entirely. They belong to Task 5.

- [ ] **Step 4: Run the tests and verify they pass**

```bash
"$DOTNET" test responder/tests/Responder.Tests.csproj
```

Expected: PASS. `VendorBoundaryTests.NoFileReferencesAnyModsOwnCode` now also
scans `DevResponder.cs` — if it fails naming `DevCapture` or `DiagCollector`, a
reference came across with the move. Fix the file, not the test.

- [ ] **Step 5: Commit**

```bash
git add responder/
git commit -m "feat(responder): the base a mod subclasses, and the two things it owns"
```

---

### Task 5: Biomancy runs on the vendored copy

The step that turns a folder into a proof. Nothing before this has compiled the
bridge inside a real mod.

**Files (in `$BIOMANCY`, a separate repository — branch it separately):**

- Delete: `Common/DevBridge/{DevArtifacts,DevBridgeGate,DevCommandRegistry,DevCommands,ShotRegion,CaptureBounds,CaptureFind,FrameShot}.cs`
- Keep: `Common/DevBridge/ArtifactHash.cs`
- Create: `Common/DevBridge/vendored/` — the eight files from `responder/`
- Modify: `Common/Diagnostics/DevCapture.cs`
- Modify: `tests/BiomancyMod.Tests/BiomancyMod.Tests.csproj`
- Delete: `tests/BiomancyMod.Tests/{DevCommandsTests,DevCommandRegistryTests,DevBridgeGateTests,DevArtifactsTests,DevArtifactNamesTests,ShotRegionTests,CaptureBoundsTests,CaptureFindTests,DevBridgeBoundaryTests}.cs`

**Interfaces:**

- Consumes: `DevResponder`, `RegisterCommands`, `CollectDiag` from Task 4.
- Produces: nothing this repo calls.

- [ ] **Step 1: Branch Biomancy**

```bash
cd "$BIOMANCY" && git checkout -b feat/vendor-the-responder && git status --short
```

Expected: clean. If it is not, STOP and report — another session may be working
there.

- [ ] **Step 2: Vendor the folder**

```bash
MCP=/home/mjarnold/tmodloader-mcp
mkdir -p Common/DevBridge/vendored
git rm -q Common/DevBridge/{DevArtifacts,DevBridgeGate,DevCommandRegistry,DevCommands,ShotRegion,CaptureBounds,CaptureFind,FrameShot}.cs
cp "$MCP"/responder/*.cs Common/DevBridge/vendored/
ls Common/DevBridge Common/DevBridge/vendored
```

Expected: `Common/DevBridge/` holds `ArtifactHash.cs` and `vendored/`;
`vendored/` holds nine files (the eight plus `DevResponder.cs`).

- [ ] **Step 3: Make `DevCapture` a subclass**

In `Common/Diagnostics/DevCapture.cs`: add `using TModLoaderMcp.DevBridge;`,
change the declaration to `public class DevCapture : DevResponder`, and delete
every member that moved to `DevResponder` in Task 4 — the whole table there.

What stays: the nine gameplay handlers (`PlantMutated`, `AskForStrains`,
`SeedWherePlayerStands`, `RemoveEveryCreepSource`, `PlaceCreepTilePatch`,
`PlantCreepWherePlayerStands`, `KillMutated`, `ReleaseCreature`, `RestartVat`),
`FirstConnectedPlayer`, and the class docstring.

`BuildCommands` becomes the override, keeping each verb's comment:

```csharp
protected override void RegisterCommands(DevCommandRegistry r) {
    r.Register("mutate", false,
        "Plant a mutated NPC in a world that is already ticking.",
        _ => PlantMutated());
    // ...vat, creature, kill, strains, seed, creep, place, killcreep -
    // each one moved verbatim from the old BuildCommands, comments included.
}

protected override string CollectDiag() =>
    DiagReport.Format(DiagCollector.Collect(Mod));
```

- [ ] **Step 4: Update the test project**

`BiomancyMod.Tests.csproj` currently has EIGHT `../../Common/DevBridge/*.cs`
compile entries. Seven of them move: `DevCommands`, `DevCommandRegistry`,
`DevBridgeGate`, `DevArtifacts`, `ShotRegion`, `CaptureFind`, `CaptureBounds` —
repoint each at `../../Common/DevBridge/vendored/`. The eighth, `ArtifactHash`,
keeps its existing path. Then delete the nine test-file entries listed above.
`DiagReport.cs` and `ArtifactHashTests.cs` are untouched — they are Biomancy's.

```bash
git rm -q tests/BiomancyMod.Tests/{DevCommandsTests,DevCommandRegistryTests,DevBridgeGateTests,DevArtifactsTests,DevArtifactNamesTests,ShotRegionTests,CaptureBoundsTests,CaptureFindTests,DevBridgeBoundaryTests}.cs
```

Those nine now live in `tmodloader-mcp/responder/tests/`. Keeping a second copy
here is how the two drift.

- [ ] **Step 5: Run Biomancy's tests**

```bash
cd "$BIOMANCY" && "$DOTNET" test tests/BiomancyMod.Tests/BiomancyMod.Tests.csproj
```

Expected: PASS. `DiagReportTests` and `ArtifactHashTests` still run; the bridge
tests are gone from this project by design.

- [ ] **Step 6: Build the mod for real**

```bash
cd /home/mjarnold/tmodloader-mcp && .venv/bin/python -c "
from tmodloader_mcp import build, config
r = build.build(config.load())
print(r.ok, r.errors, r.warnings)
print(r.summary)
"
```

Expected: `True 0 ...`. This is the first thing that compiles `DevResponder.cs`
at all — the headless test project cannot, because it extends `ModSystem`. A
failure here is a real finding about the move, not about the harness.

- [ ] **Step 7: Commit, in both repos**

```bash
cd "$BIOMANCY" && git add -A && git commit -m "refactor(devbridge): run on the vendored responder rather than our own copy"
```

---

### Task 6: Prove it against a running game

**Files:** none. This is a live run, recorded in the PR.

**Interfaces:**

- Consumes: everything.
- Produces: the evidence the extraction worked.

No collected test can do this — a Linux runner has no tModLoader. Run it by
hand and paste the output into the PR.

- [ ] **Step 1: Launch**

Use the MCP tools against the built mod: `launch` (mode `server_client`), then
`status`.

Expected: a session, with `started_pids` non-empty and the configured world.

- [ ] **Step 2: The command list is the mod's, published at load**

Call `commands`, then `commands(server=True)`.

Expected: `responder: true` on both, and twelve entries — `capture`, `diag`,
`shot` FIRST, in that order, then Biomancy's nine. The ordering is the thing to
check: it is what Task 4's contract test asserts about the source, and this is
the same claim measured against a running game.

- [ ] **Step 3: Both sides answer diag**

Call `diag`, then `diag(server=True)`.

Expected: parsed `fields` and `records` on both. This is `CollectDiag` running
through the base class's file handling — the seam Task 4 created, exercised end
to end for the first time.

- [ ] **Step 4: A picture comes back**

Call `shot(region="bottomleft")`, then `captures`, then `read_capture` on the
name it returned.

Expected: a path, a listing containing it, and PNG bytes. The PNG check merged
in #38 means a broken capture is refused here rather than reported as success.

- [ ] **Step 5: Stop, and record**

Call `stop`. Paste the outputs of steps 1-4 into the PR body.

If any step fails, do NOT proceed to Task 7 — the extraction has not been
proven, and the docs in Task 7 assert that it has.

---

### Task 7: Say what is now true

**Files:**

- Create: `responder/README.md`
- Modify: `README.md` (status banner, Phase 2 item 2, Requirements)
- Modify: `docs/MOD_CONTRACT.md` (point at the reference implementation)
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Write `responder/README.md`**

Cover exactly four things and no more: copy the folder into your mod's source
tree; subclass `DevResponder`; override `RegisterCommands` and `CollectDiag`;
the responder only loads where `ModSources/<YourMod>/` exists, so a played
install runs none of it. Include the minimal working subclass:

```csharp
using TModLoaderMcp.DevBridge;

public class MyDevResponder : DevResponder
{
    protected override string CollectDiag() =>
        "world: " + (Terraria.Main.worldName ?? "none") + "\n";
}
```

State the one thing that is easy to get wrong: `CollectDiag` must produce the
grammar in `docs/MOD_CONTRACT.md`, because the harness parses it.

- [ ] **Step 2: Correct the main README**

The status banner says the responder "lives inside Biomancy rather than in a
package your mod can depend on, so there is nothing to embed in yours but a
document." That stops being true here. Phase 2 item 2's remaining half — "extract
the responder itself" — is done; mark it, and reassess item 4 (a template mod),
since a vendorable folder with a README may be most of what it asked for.

`Requirements` currently reads "A mod embedding the trigger-file responder
(currently Biomancy's `DevCapture` / `FrameShot` / `DiagReport`)". It becomes
`responder/`.

- [ ] **Step 3: Point the contract at its implementation**

Add a line near the top of `docs/MOD_CONTRACT.md` naming `responder/` as the
reference implementation of the document. A contract with an implementation
beside it should say so; that adjacency is the whole reason this repo hosts the
folder.

- [ ] **Step 4: CHANGELOG**

Under `[Unreleased]`, an `### Added` entry: what moved, why it was not a rewrite
(DevBridge was already built for this), what the compile line proves that a
source scan cannot, and what is still not done — per-player naming, which is the
next plan.

- [ ] **Step 5: Run everything**

```bash
cd /home/mjarnold/tmodloader-mcp
.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check . && \
  .venv/bin/python -m ruff format --check . && \
  "$DOTNET" test responder/tests/Responder.Tests.csproj
```

Expected: pytest 294 passed, ruff clean twice, dotnet green.

- [ ] **Step 6: Commit and open the PRs**

Two PRs, this repo's first — Biomancy's vendored copy comes from it.

```bash
git add -A && git commit -m "docs: the mod-side half is yours now"
git push -u origin <branch> && gh pr create --draft --base master
```

---

## Not in this plan

**Per-player artifact naming.** Approved in the same spec and deliberately
sequenced after this, so a live failure in Task 6 has one candidate cause rather
than two. It gets its own plan: the token rule with its cross-language test
table, `triggers.Artifacts`, `captures.capture_pattern`, the `HeartbeatOut`
shape change, the unsuffixed-heartbeat fallback for a client with no character
yet, and the two-client live run.

**Vendor drift.** A mod that copies `responder/` has no way to learn its copy is
stale, and nothing here gives it one. Worth a version marker the responder
publishes and the harness compares — but it was not in the approved spec, so it
is raised rather than built.
