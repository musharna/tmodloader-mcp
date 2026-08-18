using Xunit;
using TModLoaderMcp.DevBridge;

namespace TModLoaderMcp.DevBridge.Tests
{
	/// <summary>
	/// The trigger's contents pick the action, so a misparse either takes a
	/// screenshot nobody asked for or silently does nothing.
	///
	/// Parse no longer decides whether a command EXISTS - that moved to
	/// DevCommandRegistry, so the set can be extended and published instead of
	/// being fixed at compile time. What Parse still owns is the SHAPE of the
	/// payload, and the rule that an unrecognised word is never quietly turned
	/// into the default action.
	/// </summary>
	public class DevCommandsTests
	{
		[Theory]
		[InlineData("capture")]
		[InlineData("CAPTURE")]
		[InlineData("  capture  ")]
		[InlineData("capture\n")]
		[InlineData("capture\r\n")]
		public void ReadsTheVerbThroughCaseAndWhitespace(string raw) {
			Assert.Equal("capture", DevCommands.Parse(raw).Verb);
		}

		[Theory]
		[InlineData("diag", "diag")]
		[InlineData("Diag", "diag")]
		[InlineData(" diag\r\n", "diag")]
		[InlineData("MUTATE", "mutate")]
		[InlineData(" killcreep \n", "killcreep")]
		[InlineData("shot", "shot")]
		public void TheVerbIsLowercasedAndTrimmed(string raw, string verb) {
			DevRequest r = DevCommands.Parse(raw);

			Assert.Equal(verb, r.Verb);
			Assert.False(r.IsMalformed);
		}

		/// <summary>
		/// THE LOAD-BEARING CASE, and it survived the move to a registry unchanged
		/// in spirit. Falling back to capture on an unrecognised word would make a
		/// misspelled "diag" take a screenshot and look like success - a failure
		/// indistinguishable from the feature working.
		///
		/// It reads differently now: Parse hands the word back as written rather
		/// than mapping it to an Unknown, and DevCapture.Dispatch refuses it
		/// against the registry. So what is asserted here is that the word is
		/// carried through intact and is NOT the default verb.
		/// </summary>
		[Theory]
		[InlineData("diagnose")]
		[InlineData("dig")]
		[InlineData("screenshot")]
		[InlineData("mutant")]
		[InlineData("places")]
		[InlineData("cree")]
		public void AnUnrecognisedWordIsNeverSilentlyTheDefault(string raw) {
			DevRequest r = DevCommands.Parse(raw);

			Assert.Equal(raw, r.Verb);
			Assert.NotEqual(DevCommands.DefaultVerb, r.Verb);
		}

		/// <summary>
		/// A word with a space in it is a word, not two.
		///
		/// "capture now" used to parse as Unknown by falling off the end of a
		/// switch. It still must not become "capture": the space is not a
		/// delimiter, so the whole string is one unservable verb.
		/// </summary>
		[Fact]
		public void ASpaceDoesNotSplitTheVerb() {
			DevRequest r = DevCommands.Parse("capture now");

			Assert.Equal("capture now", r.Verb);
			Assert.NotEqual("capture", r.Verb);
		}

		/// <summary>
		/// A bare trigger meant "capture" before commands existed, and scripts
		/// that predate them must keep working.
		/// </summary>
		[Theory]
		[InlineData("")]
		[InlineData("   ")]
		[InlineData("\n")]
		[InlineData(null)]
		public void EmptyMeansCaptureForBackwardCompatibility(string raw) {
			DevRequest r = DevCommands.Parse(raw);

			Assert.Equal("capture", r.Verb);
			Assert.Equal(DevCommands.DefaultVerb, r.Verb);
			Assert.False(r.IsMalformed);
			Assert.True(r.IsFor("anyone"), "an untargeted request is for whoever finds it");
		}

		// --- arguments -------------------------------------------------------

		[Fact]
		public void ShotCarriesItsRegion() {
			DevRequest r = DevCommands.Parse("shot:bottomleft");

			Assert.Equal("shot", r.Verb);
			Assert.Equal("bottomleft", r.Argument);
			Assert.Null(r.Target);
		}

		/// <summary>
		/// All three parts at once. The argument is taken off AFTER the target,
		/// so a colon and an at-sign in one trigger must not swallow each other.
		/// </summary>
		[Fact]
		public void AnArgumentAndATargetCoexist() {
			DevRequest r = DevCommands.Parse("shot:topleft@n43n");

			Assert.Equal("shot", r.Verb);
			Assert.Equal("topleft", r.Argument);
			Assert.Equal("n43n", r.Target);
			Assert.True(r.IsFor("n43n"));
			Assert.False(r.IsFor("tst2"));
		}

		/// <summary>
		/// "shot:" names no region. Letting it through to some default would
		/// capture a wider picture than was asked for, which is the one thing
		/// regions exist to prevent.
		/// </summary>
		[Theory]
		[InlineData("shot:")]
		[InlineData("shot:   ")]
		[InlineData(":bottomleft")]
		public void AnEmptySideOfTheColonIsAnError(string raw) {
			DevRequest r = DevCommands.Parse(raw);

			Assert.True(r.IsMalformed);
			Assert.Null(r.Verb);
			Assert.Null(r.Argument);
		}

		/// <summary>
		/// Positive control for the argument parsing: commands that take no
		/// argument must still parse, and must report a null one rather than an
		/// empty string that a caller might treat as a value.
		/// </summary>
		[Theory]
		[InlineData("diag")]
		[InlineData("seed")]
		[InlineData("capture@n43n")]
		public void ACommandWithoutAnArgumentHasANullOne(string raw) {
			DevRequest r = DevCommands.Parse(raw);

			Assert.Null(r.Argument);
			Assert.False(r.IsMalformed);
		}

		/// <summary>
		/// Malformed and unserved are different answers, and one enum value used to
		/// mean both. "diag@" is a trigger composed wrong; "mutate" against a mod
		/// that does not serve it is a trigger composed correctly for the wrong
		/// mod - and the second is exactly what a published command list lets a
		/// harness avoid, so the two have to be distinguishable.
		/// </summary>
		[Fact]
		public void MalformedIsNotTheSameAnswerAsUnserved() {
			Assert.True(DevCommands.Parse("diag@").IsMalformed);

			DevRequest unserved = DevCommands.Parse("notacommand");
			Assert.False(unserved.IsMalformed);
			Assert.Equal("notacommand", unserved.Verb);
		}

		// --- addressing ------------------------------------------------------

		[Theory]
		[InlineData("diag@tst2", "diag", "tst2")]
		[InlineData("DIAG@tst2", "diag", "tst2")]
		[InlineData(" diag @ tst2 \r\n", "diag", "tst2")]
		[InlineData("capture@n43n", "capture", "n43n")]
		public void ParsesATargetedRequest(string raw, string verb, string target) {
			DevRequest r = DevCommands.Parse(raw);

			Assert.Equal(verb, r.Verb);
			Assert.Equal(target, r.Target);
		}

		/// <summary>
		/// Player names are shown back to a human, so their case survives; the
		/// comparison is still case-insensitive.
		/// </summary>
		[Fact]
		public void TargetKeepsItsCaseButComparesLoosely() {
			DevRequest r = DevCommands.Parse("diag@TsT2");

			Assert.Equal("TsT2", r.Target);
			Assert.True(r.IsFor("tst2"));
			Assert.True(r.IsFor("TST2"));
		}

		/// <summary>
		/// THE POINT OF ADDRESSING. Two clients share one trigger file; the wrong
		/// one must decline, or it answers as the wrong player and destroys a
		/// request its owner never saw.
		/// </summary>
		[Fact]
		public void ATargetedRequestIsRefusedByEveryoneElse() {
			DevRequest r = DevCommands.Parse("diag@tst2");

			Assert.True(r.IsFor("tst2"), "the addressee must act on it");
			Assert.False(r.IsFor("n43n"), "the other client must NOT act on it");

			// A side with NO address - a client at the menu with no character, a
			// dedicated server started without -port - is never an addressee.
			Assert.False(r.IsFor(null));
			Assert.False(r.IsFor(""));
		}

		/// <summary>
		/// THE SAME RULE, ONE AXIS OVER. Two dedicated servers sharing one
		/// Main.SavePath poll one server-side trigger, and until a server had an
		/// address every request there was untargeted - so whichever polled first
		/// took it, and either session's cleanup could destroy the other's
		/// in-flight request. A server's address is its port; nothing else about
		/// this method changes, which is the point: one comparison, two kinds of
		/// addressee.
		/// </summary>
		[Fact]
		public void ATargetedRequestIsRefusedByTheOtherServerToo() {
			DevRequest r = DevCommands.Parse("diag@port7810");

			Assert.True(r.IsFor("port7810"), "the addressed server must act on it");
			Assert.False(r.IsFor("port7811"), "the other server must NOT act on it");
			Assert.False(r.IsFor(null), "a server with no -port is never an addressee");
		}

		/// <summary>
		/// A player really can be called port7810, and this asserts that it
		/// matches - because pretending otherwise would need a spelling rule
		/// here, and there is none. What keeps the two kinds of address from
		/// colliding is that they are never read from the same file: a client
		/// polls &lt;mod&gt;-capture.trigger and a dedicated server polls
		/// &lt;mod&gt;-capture-server.trigger. This test is what makes that
		/// reasoning explicit rather than assumed - if the separation ever came
		/// from the strings, this is where it would show.
		/// </summary>
		[Fact]
		public void AnAddressIsNotDistinguishedByItsSpelling() {
			Assert.True(DevCommands.Parse("diag@port7810").IsFor("port7810"));
		}

		/// <summary>
		/// Positive control for the assertion above: without a target, none of
		/// those sides decline. Otherwise "IsFor is always false" would pass it.
		/// </summary>
		[Fact]
		public void AnUntargetedRequestIsForEverySide() {
			DevRequest r = DevCommands.Parse("diag");

			Assert.Null(r.Target);
			Assert.True(r.IsFor("tst2"));
			Assert.True(r.IsFor("n43n"));
			Assert.True(r.IsFor(null));
		}

		/// <summary>
		/// "diag@" addresses nobody. Treating it as untargeted would hand it to
		/// whichever client polled first - precisely the race the target exists
		/// to remove - so it is an error rather than a broadcast.
		/// </summary>
		[Theory]
		[InlineData("diag@")]
		[InlineData("diag@   ")]
		[InlineData("@tst2")]
		public void AnEmptySideOfTheAtSignIsAnError(string raw) {
			DevRequest r = DevCommands.Parse(raw);

			Assert.True(r.IsMalformed);
			Assert.Null(r.Verb);
			Assert.Null(r.Target);
		}

		// ---- the request id --------------------------------------------------

		/// <summary>
		/// The id comes off the very end, after the target, and everything else
		/// parses exactly as it does without one. It exists because the reply
		/// file is named per player rather than per request, so a late reply to
		/// a timed-out request needs telling apart from the answer to the next
		/// one - see DevResponder.Report.
		/// </summary>
		[Theory]
		[InlineData("diag#r-abc123", "diag", null, null)]
		[InlineData("diag@n43n#r-abc123", "diag", "n43n", null)]
		[InlineData("shot:topleft@n43n#r-0123456789ab", "shot", "n43n", "topleft")]
		[InlineData("shot:topleft#r-abcd", "shot", null, "topleft")]
		public void ARequestIdComesOffTheEndAndChangesNothingElse(
				string raw, string verb, string target, string argument) {
			DevRequest r = DevCommands.Parse(raw);

			Assert.Equal(verb, r.Verb);
			Assert.Equal(target, r.Target);
			Assert.Equal(argument, r.Argument);
			Assert.StartsWith("r-", r.Id);
		}

		[Fact]
		public void APayloadWithoutAnIdCarriesNull() {
			Assert.Null(DevCommands.Parse("diag@n43n").Id);
		}

		/// <summary>
		/// A '#' tail that is not an id is PAYLOAD, and stripping it would
		/// silently change what `say` says. The marker earns its keep by being
		/// unlikely as prose: `r-` then 4-32 characters of lowercase hex, with
		/// one character wrong meaning the whole tail stays where it was typed.
		/// </summary>
		[Theory]
		[InlineData("say:issue #beef", "issue #beef")]         // no r- marker
		[InlineData("say:fix #r-BEEF", "fix #r-BEEF")]         // uppercase is not hex here
		[InlineData("say:see #r-abc", "see #r-abc")]           // 3 hex is too short
		[InlineData("say:see #r-abcg", "see #r-abcg")]         // g is not hex
		[InlineData("say:ref #r-0123456789abcdef0123456789abcdef0", // 33 is too long
			"ref #r-0123456789abcdef0123456789abcdef0")]
		public void ATailThatIsNotAnIdStaysInThePayload(string raw, string argument) {
			DevRequest r = DevCommands.Parse(raw);

			Assert.Equal("say", r.Verb);
			Assert.Equal(argument, r.Argument);
			Assert.Null(r.Id);
		}

		/// <summary>
		/// A bare tagged trigger is still the historical bare trigger - a
		/// capture - answered under the id it carried.
		/// </summary>
		[Fact]
		public void ABareTaggedTriggerIsACaptureWithAnId() {
			DevRequest r = DevCommands.Parse("#r-abcd12");

			Assert.Equal(DevCommands.DefaultVerb, r.Verb);
			Assert.Null(r.Target);
			Assert.Equal("r-abcd12", r.Id);
		}

		/// <summary>
		/// The id survives a malformed payload, so the ERROR naming the problem
		/// reaches the caller who asked instead of reading as a stale reply to
		/// them and as an answer to whoever asks next.
		/// </summary>
		[Fact]
		public void TheIdSurvivesAMalformedPayload() {
			DevRequest r = DevCommands.Parse("diag@#r-abcd12");

			Assert.True(r.IsMalformed);
			Assert.Equal("r-abcd12", r.Id);
		}
	}
}
