using TModLoaderMcp.DevBridge;
using Xunit;

namespace Responder.Tests
{
	/// <summary>
	/// The same table as tests/test_player_token.py, deliberately duplicated.
	///
	/// The mod and the harness each compute this token from a name only they
	/// hold — Main.LocalPlayer.name here, Session.player there — and then have
	/// to open the same file. Nothing at runtime would notice them disagreeing:
	/// each would write and read its own name happily, and the harness would
	/// simply wait forever for an answer the mod had already written somewhere
	/// else. This table is the only place that disagreement is visible.
	/// </summary>
	public class PlayerTokenTests
	{
		[Theory]
		[InlineData("n43n", "n43n-003f")]
		[InlineData("Big Bird", "big-bird-44a3")]
		[InlineData("BigBird", "bigbird-ca4c")]
		public void TheTokenForANameIsExactlyThis(string name, string expected) {
			Assert.Equal(expected, DevArtifacts.PlayerToken(name));
		}

		[Fact]
		public void TwoNamesThatSlugAlikeStillDiffer() {
			var a = DevArtifacts.PlayerToken("Big Bird");
			var b = DevArtifacts.PlayerToken("BigBird");
			// Positive control in the same test: this cannot pass by both
			// being null.
			Assert.False(string.IsNullOrEmpty(a));
			Assert.False(string.IsNullOrEmpty(b));
			Assert.NotEqual(a, b);
		}

		[Fact]
		public void ANameWithNoAlphanumericsStillProducesAUsableToken() {
			var token = DevArtifacts.PlayerToken("!!!");
			Assert.False(string.IsNullOrEmpty(token));
			Assert.False(token.StartsWith("-"));
		}

		[Theory]
		[InlineData(null)]
		[InlineData("")]
		[InlineData("   ")]
		public void NoCharacterYetHasNoToken(string empty) {
			Assert.Null(DevArtifacts.PlayerToken(empty));
		}
	}
}
