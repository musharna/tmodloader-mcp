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

        /// <summary>
        /// The single highest-risk property this task added: the trigger and
        /// the command list must stay addressed by ONE name shared by every
        /// client, or the IsFor(LocalPlayerName) addressing check at Tick()
        /// becomes meaningless - each client would poll its own private,
        /// nonexistent trigger and never see a request meant for it. These
        /// three tests are what stops a future edit from folding PathFor and
        /// AnswerPathFor back together "for simplicity" and silently deleting
        /// multi-client addressing while every other test in this project
        /// stays green (DevResponder.cs is not on the compile line - nothing
        /// else here would notice).
        ///
        /// Each was mutation-tested: pointed at AnswerPathFor (or given a
        /// second parameter) and confirmed to fail, then reverted. See
        /// task-5-report.md for the transcript.
        /// </summary>
        [Fact]
        public void TheTriggerIsReadThroughThePlainPathForNotTheTokenedOne() {
            // Negative lookbehind for "Answer": "AnswerPathFor(TriggerName)"
            // contains the literal substring "PathFor(TriggerName)" - Answer
            // ends in the very letters PathFor starts with - so a plain
            // Assert.Contains("PathFor(TriggerName)", ...) would pass on the
            // very regression this test exists to catch.
            Assert.Matches(new Regex(@"(?<!Answer)PathFor\(TriggerName\)"), Source());
        }

        [Fact]
        public void TheCommandListIsPublishedThroughThePlainPathForNotTheTokenedOne() {
            Assert.Matches(new Regex(@"(?<!Answer)PathFor\(Names\.Commands\)"), Source());
        }

        /// <summary>
        /// PathFor's declaration takes exactly ONE argument. A second
        /// parameter is exactly how a token would sneak into the trigger and
        /// command-list path without either call site above visibly changing.
        /// </summary>
        [Fact]
        public void PathForTakesExactlyOneArgumentSoItCannotAcquireAToken() {
            Assert.Matches(
                new Regex(@"private\s+static\s+string\s+PathFor\s*\(\s*string\s+name\s*\)\s*\{"),
                Source());
        }

        /// <summary>
        /// A dedicated server never has a PLAYER token, because LocalPlayerName
        /// short-circuits to null before ever reaching Main.LocalPlayer. It does
        /// now have an address token of its own - see AnswerTokenOrNull - and
        /// the two must not be confused: a server acquiring a player token would
        /// mean Main.dedServ had a LocalPlayer, which is a different and much
        /// worse claim than the one this change makes. See
        /// DevArtifactsTests.ADedicatedServerWithATokenMatchesTheHarnessesOrdering
        /// for the composition that had to be fixed before a server could carry
        /// any token at all.
        /// </summary>
        [Fact]
        public void LocalPlayerNameIsNullOnADedicatedServer() {
            Assert.Matches(
                new Regex(@"private\s+static\s+string\s+LocalPlayerName\s*\{\s*get\s*\{\s*if\s*\(\s*Main\.dedServ\s*\)\s*\{\s*return\s+null\s*;"),
                Source());
        }

        /// <summary>
        /// THE ONE LINE THAT MAKES SERVER ADDRESSING EXIST, and nothing else in
        /// this repository can reach it: DevResponder.cs is not on the compile
        /// line, so the whole mechanism - a server's port becoming an address -
        /// is inert unless Tick compares against LocalAddress rather than
        /// LocalPlayerName. And the reverted spelling still COMPILES and still
        /// passes every other test here, because LocalPlayerName is null on a
        /// dedicated server by the test above - it would quietly restore exactly
        /// the state this change removed: two servers, one save directory, every
        /// request going to whichever polled first.
        /// </summary>
        [Fact]
        public void TheAddressingCheckComparesAgainstLocalAddressNotJustAPlayer() {
            Assert.Matches(new Regex(@"request\.IsFor\(LocalAddress\)"), Source());
        }

        /// <summary>
        /// And the answers follow the address. Addressing alone is half a fix
        /// that LOOKS whole: server A would correctly serve a request addressed
        /// to A and then write its answer into the very file B is also writing,
        /// so B's harness reads A's diag - the same collision per-player naming
        /// removed for clients, surviving behind a routing fix.
        /// </summary>
        [Fact]
        public void AnswersAreNamedForTheAddresseeIncludingAServers() {
            Assert.Matches(
                new Regex(@"ForSide\(name,\s*Main\.dedServ,\s*AnswerTokenOrNull\)"),
                Source());
            Assert.Matches(
                new Regex(@"AnswerTokenOrNull\s*=>\s*\r?\n?\s*Main\.dedServ\s*\?\s*ServerAddress\s*:"),
                Source());
        }
    }
}
