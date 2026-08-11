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

		/// <summary>
		/// The three-argument overload has the same absence rule as its sibling
		/// above, and a token must not be able to smuggle it past. Before this
		/// was guarded, ForSide(null, false, "tok") threw a NullReferenceException
		/// (LastIndexOf on null) and ForSide("", false, "tok") returned "-tok" -
		/// the token alone LOOKS like a plausible filename, which is exactly the
		/// failure the two-argument sibling test above exists to forbid.
		/// </summary>
		[Fact]
		public void EmptyAndNullStayThatWayEvenWithAToken() {
			Assert.Null(DevArtifacts.ForSide(null, dedicatedServer: false, playerToken: "n43n-003f"));
			Assert.Equal(string.Empty,
				DevArtifacts.ForSide(string.Empty, dedicatedServer: false, playerToken: "n43n-003f"));

			// Both axes null/empty at once, and the dedicated-server side of it too.
			Assert.Null(DevArtifacts.ForSide(null, dedicatedServer: true, playerToken: "n43n-003f"));
			Assert.Equal(string.Empty,
				DevArtifacts.ForSide(string.Empty, dedicatedServer: true, playerToken: null));
		}

		/// <summary>
		/// LATENT ORDERING DIVERGENCE between the two languages, not exercised
		/// anywhere else in this file. This method composes SIDE then TOKEN:
		/// ForSide(name, dedicatedServer: true, playerToken: "tok") produces
		/// "biomancy-diag-server-tok.txt". The harness composes the reverse -
		/// triggers.py's Artifacts._named splices the token in first, and
		/// config.py's Config.artifact then appends "-server" to that ALREADY
		/// tokenised name, producing "biomancy-diag-tok-server.txt". Those two
		/// spellings are different files.
		///
		/// They agree TODAY only because a dedicated server never has a player
		/// token: DevResponder.LocalPlayerName returns null whenever
		/// Main.dedServ is true (source-scanned by
		/// DevResponderContractTests.LocalPlayerNameIsNullOnADedicatedServer),
		/// so this method is never actually asked to combine dedicatedServer:
		/// true with a non-null token at runtime. Nothing HERE enforces that -
		/// this test only proves the two orderings genuinely diverge, so a
		/// future change that gives a server a token cannot be made safely
		/// without also fixing this composition to match the harness's order.
		/// </summary>
		[Fact]
		public void ADedicatedServerWithATokenWouldNotMatchTheHarnessesOrdering() {
			string sideThenToken =
				DevArtifacts.ForSide("biomancy-diag.txt", dedicatedServer: true, playerToken: "n43n-003f");

			// The harness's ordering, replicated inline rather than imported -
			// there is no shared code between the two languages, which is the
			// whole reason this divergence can exist unnoticed.
			const string tokenThenSide = "biomancy-diag-n43n-003f-server.txt";

			Assert.NotEqual(tokenThenSide, sideThenToken);
		}

		/// <summary>
		/// The one case where the divergence above is silent: a null token
		/// makes both orderings collapse to the same sided-only name, because
		/// there is nothing left to order.
		/// </summary>
		[Fact]
		public void ADedicatedServerWithNoTokenIsWhereTheTwoOrderingsHappenToAgree() {
			Assert.Equal(
				DevArtifacts.ForSide("biomancy-diag.txt", dedicatedServer: true),
				DevArtifacts.ForSide("biomancy-diag.txt", dedicatedServer: true, playerToken: null));
		}
	}
}
