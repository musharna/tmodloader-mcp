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
