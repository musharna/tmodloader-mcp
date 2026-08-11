using Xunit;
using TModLoaderMcp.DevBridge;

namespace TModLoaderMcp.DevBridge.Tests
{
	/// <summary>
	/// The artifact filenames, which are a contract with a harness outside this
	/// repository rather than a naming preference.
	///
	/// They were five constants spelling "biomancy-", which is what makes this
	/// responder Biomancy's rather than any mod's. Deriving them from the mod's
	/// name is the point of the extraction - but the derivation has to reproduce
	/// the existing names EXACTLY, because tmodloader-mcp polls for them by name
	/// and a rename would leave it waiting on a trigger nobody writes.
	/// </summary>
	public class DevArtifactNamesTests
	{
		[Fact]
		public void ReproducesBiomancysExistingNames() {
			var names = new DevArtifactNames("Biomancy");

			Assert.Equal("biomancy-capture.trigger", names.Trigger);
			Assert.Equal("biomancy-capture.txt", names.Result);
			Assert.Equal("biomancy-diag.txt", names.Diag);
			Assert.Equal("biomancy-hooks.txt", names.Heartbeat);
			Assert.Equal("biomancy-shot.png", names.Shot);
		}

		[Fact]
		public void AnotherModGetsItsOwn() {
			// Without this the test above is satisfied by returning constants and
			// calling them derived.
			var names = new DevArtifactNames("MyCoolMod");

			Assert.Equal("mycoolmod-capture.trigger", names.Trigger);
			Assert.Equal("mycoolmod-shot.png", names.Shot);
			Assert.DoesNotContain("biomancy", names.Diag);
		}

		[Fact]
		public void TwoModsNeverCollide() {
			// Both sides of a session share one Main.SavePath, and so do two mods
			// on one machine. This is the same race SideSuffix exists for, one
			// axis over.
			var a = new DevArtifactNames("Biomancy");
			var b = new DevArtifactNames("Otherwise");

			Assert.NotEqual(a.Trigger, b.Trigger);
			Assert.NotEqual(a.Result, b.Result);
			Assert.NotEqual(a.Diag, b.Diag);
			Assert.NotEqual(a.Heartbeat, b.Heartbeat);
			Assert.NotEqual(a.Shot, b.Shot);
		}

		[Fact]
		public void TheSideSuffixStillApplies() {
			// The derived names must still go through ForSide, or a server and a
			// client would share a trigger again - which is the race DevArtifacts
			// was written for.
			var names = new DevArtifactNames("Biomancy");

			Assert.Equal(
				"biomancy-hooks-server.txt",
				DevArtifacts.ForSide(names.Heartbeat, true));
			Assert.Equal(
				"biomancy-hooks.txt",
				DevArtifacts.ForSide(names.Heartbeat, false));
		}

		[Theory]
		[InlineData("BIOMANCY")]
		[InlineData("biomancy")]
		[InlineData("BioMancy")]
		public void TheCaseOfTheModNameDoesNotChangeTheFiles(string given) {
			// tModLoader's internal name is fixed, but nothing stops a caller
			// spelling it differently; the files on disk must not move because of
			// it.
			Assert.Equal("biomancy-diag.txt", new DevArtifactNames(given).Diag);
		}

		[Fact]
		public void AClientsAnswersCarryItsPlayer() {
			Assert.Equal("biomancy-diag-n43n-003f.txt",
				DevArtifacts.ForSide("biomancy-diag.txt", false, "n43n-003f"));
			Assert.Equal("biomancy-shot-n43n-003f.png",
				DevArtifacts.ForSide("biomancy-shot.png", false, "n43n-003f"));
		}

		[Fact]
		public void TheDedicatedServerKeepsItsSideSuffixAndGainsNoPlayer() {
			// Two axes that compose: the side suffix keeps two SIDES apart, the
			// token keeps two CLIENTS apart. A server has no client to be
			// confused with, and adding a token would rename files the harness
			// reads by their old names.
			Assert.Equal("biomancy-diag-server.txt",
				DevArtifacts.ForSide("biomancy-diag.txt", true, null));
		}

		[Fact]
		public void AClientWithNoCharacterYetKeepsTheUnsuffixedName() {
			// Not a legacy fallback - this name MEANS "up, no character yet",
			// and `launch` depends on being able to read it before a world is
			// loaded. See MOD_CONTRACT.md.
			Assert.Equal("biomancy-hooks.txt",
				DevArtifacts.ForSide("biomancy-hooks.txt", false, null));
		}
	}
}
