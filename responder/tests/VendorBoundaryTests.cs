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
