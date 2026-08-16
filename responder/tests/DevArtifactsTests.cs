using System.Text.RegularExpressions;
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
			Assert.Null(DevArtifacts.ForSide(null, dedicatedServer: false, token: "n43n-003f"));
			Assert.Equal(string.Empty,
				DevArtifacts.ForSide(string.Empty, dedicatedServer: false, token: "n43n-003f"));

			// Both axes null/empty at once, and the dedicated-server side of it too.
			Assert.Null(DevArtifacts.ForSide(null, dedicatedServer: true, token: "n43n-003f"));
			Assert.Equal(string.Empty,
				DevArtifacts.ForSide(string.Empty, dedicatedServer: true, token: null));
		}

		/// <summary>
		/// A DEDICATED SERVER WITH A TOKEN NOW MATCHES THE HARNESS'S ORDERING,
		/// and until 2026-08-16 it did not. This test used to assert the
		/// opposite - that the two orderings diverge - and stood as a warning
		/// that a future change giving a server a token could not be made
		/// safely without fixing this composition first. DevArtifacts.
		/// ServerAddress is that change, so this is the warning being paid off
		/// rather than a test that flipped.
		///
		/// The divergence was real. This method composed SIDE then TOKEN
		/// ("biomancy-diag-server-tok.txt"); the harness composes the reverse -
		/// triggers.py's Artifacts._named splices the token in first, and
		/// config.py's Config.artifact then appends "-server" to that already
		/// tokened name. It was invisible because the one combination that can
		/// tell the two apart could not arise: LocalPlayerName is null whenever
		/// Main.dedServ is true (source-scanned by
		/// DevResponderContractTests.LocalPlayerNameIsNullOnADedicatedServer),
		/// so this method was never asked to combine a server with a token.
		/// </summary>
		[Fact]
		public void ADedicatedServerWithATokenMatchesTheHarnessesOrdering() {
			// The harness's ordering, spelled inline rather than imported -
			// there is no shared code between the two languages, which is the
			// whole reason the divergence could exist unnoticed. Written out
			// here, a future edit to ForSide has to disagree with a literal.
			const string tokenThenSide = "biomancy-diag-port7810-server.txt";

			Assert.Equal(tokenThenSide,
				DevArtifacts.ForSide("biomancy-diag.txt", dedicatedServer: true, token: "port7810"));
		}

		/// <summary>
		/// THE NAMES THAT ALREADY EXISTED DID NOT MOVE. Reordering the
		/// composition is only observable when BOTH a side suffix and a token
		/// apply, and every name in use before this change had at most one of
		/// them: a client is tokened and unsided, a server was sided and
		/// untokened. This is the claim that makes the reorder safe to ship
		/// without renaming a single file the mod already writes.
		/// </summary>
		[Fact]
		public void NeitherExistingSpellingChangedWhenTheOrderDid() {
			Assert.Equal("biomancy-diag-n43n-003f.txt",
				DevArtifacts.ForSide("biomancy-diag.txt", dedicatedServer: false, token: "n43n-003f"));
			Assert.Equal("biomancy-diag-server.txt",
				DevArtifacts.ForSide("biomancy-diag.txt", dedicatedServer: true, token: null));
		}

		/// <summary>
		/// A null token makes both orderings collapse to the same sided-only
		/// name, because there is nothing left to order. This was the case that
		/// hid the divergence; it is kept because it is still the boundary
		/// between the two branches of the three-argument overload.
		/// </summary>
		[Fact]
		public void ADedicatedServerWithNoTokenIsWhereTheTwoOrderingsHappenToAgree() {
			Assert.Equal(
				DevArtifacts.ForSide("biomancy-diag.txt", dedicatedServer: true),
				DevArtifacts.ForSide("biomancy-diag.txt", dedicatedServer: true, token: null));
		}

		/// <summary>
		/// THE SERVER'S ANSWER NAMES MUST NOT LOOK LIKE A CLIENT'S. heartbeat.py
		/// tells the two apart by matching PLAYER_TOKEN_GRAMMAR - a slug ending
		/// in a dash and four hex characters - and excludes the server "by
		/// construction rather than by a special case", because `server` does
		/// not end that way. A port CAN end that way: 7810 is four hex digits,
		/// so giving the server a name is precisely the change that could have
		/// broken it.
		///
		/// TWO GUARDS, AND THIS SIDE OWNS THE STRONGER ONE. Only
		/// `-server-7810` - side suffix before the token, bare port - is read
		/// back as a client. The composition order fixed in ForSide above is
		/// enough on its own, and the harness's `port` prefix is enough on its
		/// own; the bad spelling needs both to be wrong. Measured on the harness
		/// side over all four combinations, in
		/// test_only_one_of_the_four_possible_server_spellings_would_have_broken_that.
		/// </summary>
		[Theory]
		[InlineData(7810)]
		[InlineData(7777)]
		[InlineData(1)]
		[InlineData(65535)]
		public void AServerHeartbeatIsStillNotMistakableForAClients(int port) {
			string name = DevArtifacts.ForSide("biomancy-hooks.txt", dedicatedServer: true,
				token: DevArtifacts.ServerAddress(new[] { "-port", port.ToString() }));

			// PLAYER_TOKEN_GRAMMAR, transcribed from triggers.py.
			var asAClient = new Regex(@"^biomancy-hooks(-[a-z0-9][a-z0-9-]*-[0-9a-f]{4})?\.txt$");

			Assert.DoesNotMatch(asAClient, name);
		}

		/// <summary>
		/// The address the harness will address requests to, read off the same
		/// command line the harness wrote. These vectors are the contract: the
		/// Python half asserts the identical strings in
		/// test_addressing.py, because there is no shared code between the two
		/// languages and nothing at runtime would notice them drifting apart -
		/// each side would compose its own spelling and simply never meet. The
		/// same arrangement PlayerTokenTests holds for the player token.
		/// </summary>
		[Theory]
		[InlineData(7810, "port7810")]
		[InlineData(7777, "port7777")]
		[InlineData(1, "port1")]
		[InlineData(65535, "port65535")]
		public void ThePortIsTheAddress(int port, string expected) {
			Assert.Equal(expected,
				DevArtifacts.ServerAddress(new[] { "tModLoader.dll", "-server", "-port", port.ToString() }));
		}

		/// <summary>
		/// A server with nothing to read has NO address, which is a supported
		/// state and not a failure: it behaves exactly as every dedicated server
		/// did before addresses existed - answering anything untargeted, never
		/// the addressee of a targeted request. That is the old ambiguity, which
		/// is survivable; inventing an address would be two servers claiming one.
		/// </summary>
		public static TheoryData<string[]> NoAddressIn => new TheoryData<string[]> {
			new string[0],
			new[] { "tModLoader.dll", "-server" },
			// A trailing -port with nothing after it: the value is off the end
			// of the array, which is a read this must not attempt.
			new[] { "tModLoader.dll", "-port" },
			new[] { "-port", "" },
			new[] { "-port", "notaport" },
			new[] { "-port", "-1" },
			new[] { "-port", "0" },
			new[] { "-port", "65536" },
			// NumberStyles.None: a sign or surrounding whitespace is something
			// a shell can produce and the harness never writes, so accepting it
			// would let the two sides derive different addresses from one
			// command line.
			new[] { "-port", "+7810" },
			new[] { "-port", " 7810" },
		};

		[Theory]
		[MemberData(nameof(NoAddressIn))]
		public void AnUnreadablePortIsNoAddressRatherThanAGuess(string[] commandLine) {
			Assert.Null(DevArtifacts.ServerAddress(commandLine));
		}

		[Fact]
		public void ANullCommandLineIsNoAddress() {
			Assert.Null(DevArtifacts.ServerAddress(null));
		}

		/// <summary>
		/// Leading zeros are the same port, so they must be the same address.
		/// The harness formats an int and can never write "07810"; a server
		/// started by hand can. Echoing the argument instead of re-rendering the
		/// parsed value would put those two sessions on two filenames while they
		/// contended for one socket.
		/// </summary>
		[Fact]
		public void AnAddressIsRenderedFromTheParsedPortNotEchoed() {
			Assert.Equal("port7810", DevArtifacts.ServerAddress(new[] { "-port", "07810" }));
		}

		/// <summary>
		/// The LAST -port decides, because Terraria's own argument handling lets
		/// a later occurrence win and picking an earlier one would be this side
		/// describing a different process than the one that is running.
		/// </summary>
		[Fact]
		public void TheLastPortWinsTheWayTheGameReadsIt() {
			Assert.Equal("port7811",
				DevArtifacts.ServerAddress(new[] { "-port", "7810", "-port", "7811" }));
		}

		/// <summary>
		/// And an unreadable LAST -port is no address, rather than a fallback to
		/// the readable earlier one. Falling back would answer a question nobody
		/// asked: the port in force is the last one, and if that cannot be read
		/// then neither can the address.
		/// </summary>
		[Fact]
		public void AnUnreadableLastPortDoesNotFallBackToAnEarlierOne() {
			Assert.Null(DevArtifacts.ServerAddress(new[] { "-port", "7810", "-port", "nonsense" }));
		}

		/// <summary>
		/// tModLoader is launched from a Windows shell, where argument case is
		/// not something a caller thinks about.
		/// </summary>
		[Fact]
		public void TheFlagIsMatchedWithoutRegardToCase() {
			Assert.Equal("port7810", DevArtifacts.ServerAddress(new[] { "-PORT", "7810" }));
		}

		/// <summary>
		/// An address is used verbatim as a filename fragment, so it has to BE
		/// one. Asserted over the whole legal port range rather than at the
		/// handful of ports anybody types.
		/// </summary>
		[Theory]
		[InlineData(1)]
		[InlineData(7810)]
		[InlineData(65535)]
		public void AnAddressIsSafeToPutInAFilename(int port) {
			string address = DevArtifacts.ServerAddress(new[] { "-port", port.ToString() });

			Assert.Matches(new Regex("^port[0-9]+$"), address);
		}
	}
}
