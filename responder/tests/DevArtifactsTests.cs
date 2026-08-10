using Xunit;
using TModLoaderMcp.DevBridge;

namespace TModLoaderMcp.DevBridge.Tests
{
	public class DevArtifactsTests
	{
		/// <summary>
		/// The whole reason this type exists. A server and a client on one machine
		/// share Main.SavePath, so if both sides resolved a name to the same file
		/// they would race for the trigger and overwrite each other's answers.
		/// </summary>
		[Fact]
		public void TheTwoSidesNeverResolveANameToTheSameFile() {
			foreach (string name in new[] {
					"biomancy-capture.trigger",
					"biomancy-capture.txt",
					"biomancy-hooks.txt",
					"biomancy-diag.txt" }) {
				Assert.NotEqual(
					DevArtifacts.ForSide(name, dedicatedServer: false),
					DevArtifacts.ForSide(name, dedicatedServer: true));
			}
		}

		/// <summary>
		/// tools/capture.sh and tools/run_selftest.sh address the client's files by
		/// literal name. Suffixing those too would have been a silent break of
		/// every script in the harness.
		/// </summary>
		[Fact]
		public void ClientNamesAreReturnedExactlyAsGiven() {
			Assert.Equal(
				"biomancy-capture.trigger",
				DevArtifacts.ForSide("biomancy-capture.trigger", dedicatedServer: false));
			Assert.Equal(
				"biomancy-hooks.txt",
				DevArtifacts.ForSide("biomancy-hooks.txt", dedicatedServer: false));
		}

		[Fact]
		public void TheServerTagGoesBeforeTheExtensionNotAfterIt() {
			// Appending to the whole name would produce biomancy-hooks.txt-server,
			// which is not a .txt and would stop matching any *.txt glob a script
			// or a file browser uses.
			Assert.Equal(
				"biomancy-hooks-server.txt",
				DevArtifacts.ForSide("biomancy-hooks.txt", dedicatedServer: true));
			Assert.Equal(
				"biomancy-capture-server.trigger",
				DevArtifacts.ForSide("biomancy-capture.trigger", dedicatedServer: true));
		}

		/// <summary>
		/// Not a hypothetical: an extensionless name must still round-trip rather
		/// than throwing or silently dropping the tag.
		/// </summary>
		[Fact]
		public void ANameWithNoExtensionStillGetsTheTag() {
			Assert.Equal("marker-server", DevArtifacts.ForSide("marker", dedicatedServer: true));
		}

		[Fact]
		public void EmptyAndNullAreReturnedUnchangedRatherThanBecomingTheTag() {
			// "-server" as a filename would be a real file in Main.SavePath, so
			// this must not turn absence into a plausible-looking name.
			Assert.Null(DevArtifacts.ForSide(null, dedicatedServer: true));
			Assert.Equal(string.Empty, DevArtifacts.ForSide(string.Empty, dedicatedServer: true));
		}
	}
}
