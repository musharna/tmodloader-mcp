using System;
using System.Linq;
using TModLoaderMcp.DevBridge;
using Xunit;

namespace TModLoaderMcp.DevBridge.Tests
{
	/// <summary>
	/// The command set became data so it could be published and extended. These
	/// check the two things that buys and the four ways it could quietly fail.
	///
	/// Every refusal below asserts a legitimate registration in the SAME test. A
	/// registry that threw on everything would otherwise satisfy each
	/// Assert.Throws and read as fully guarded.
	/// </summary>
	public class DevCommandRegistryTests
	{
		private static DevCommandRegistry OneCommand(string name = "diag", bool takesArgument = false) {
			var r = new DevCommandRegistry();
			r.Register(name, takesArgument, "summary", _ => { });
			return r;
		}

		[Fact]
		public void ARegisteredCommandResolves() {
			DevCommandRegistry r = OneCommand();

			Assert.True(r.TryResolve("diag", out DevCommandEntry e));
			Assert.Equal("diag", e.Name);
			Assert.NotNull(e.Handler);
		}

		/// <summary>
		/// The trigger is written by hand often enough that case is noise -
		/// DevCommands.Parse lowercases the verb, and this is the other half of
		/// that contract.
		/// </summary>
		[Theory]
		[InlineData("DIAG")]
		[InlineData("Diag")]
		[InlineData("dIaG")]
		public void ResolvingIgnoresCase(string asked) {
			Assert.True(OneCommand().TryResolve(asked, out _));
		}

		[Fact]
		public void RegisteringLowercasesTheName() {
			DevCommandRegistry r = OneCommand("DIAG");

			Assert.True(r.TryResolve("diag", out DevCommandEntry e));

			// The published name is what a harness will send back, so it has to be
			// the canonical spelling rather than however it was registered.
			Assert.Equal("diag", e.Name);
			Assert.Contains("diag", r.Publish());
			Assert.DoesNotContain("DIAG", r.Publish());
		}

		[Fact]
		public void AnUnregisteredVerbDoesNotResolve() {
			DevCommandRegistry r = OneCommand();

			Assert.False(r.TryResolve("mutate", out DevCommandEntry missing));
			Assert.Null(missing);

			// Positive control: the registry is not simply refusing everything.
			Assert.True(r.TryResolve("diag", out _));
		}

		[Theory]
		[InlineData(null)]
		[InlineData("")]
		[InlineData("   ")]
		public void AnEmptyVerbDoesNotResolve(string asked) {
			Assert.False(OneCommand().TryResolve(asked, out _));
		}

		/// <summary>
		/// THE RULE THAT MATTERS MOST, and the only one that cannot be caught later.
		///
		/// ':' and '@' are the payload's delimiters. A command registered as
		/// "kill:creep" would be cut in half by DevCommands.Parse into the verb
		/// "kill" and the argument "creep", so it could never be delivered under
		/// the name it asked for - it would register cleanly, appear in the
		/// published list, and never once run.
		/// </summary>
		[Theory]
		[InlineData("kill:creep")]
		[InlineData("diag@player")]
		[InlineData("two words")]
		[InlineData("kill-creep")]
		[InlineData("kill_creep")]
		public void ANameTheTriggerCouldNotCarryIsRefused(string name) {
			var r = new DevCommandRegistry();

			Assert.Throws<ArgumentException>(() => r.Register(name, false, "s", _ => { }));

			// Positive control: the same registry takes a deliverable name.
			r.Register("killcreep", false, "s", _ => { });
			Assert.True(r.TryResolve("killcreep", out _));
		}

		[Fact]
		public void ADuplicateNameIsRefusedRatherThanShadowed() {
			var r = new DevCommandRegistry();
			r.Register("diag", false, "first", _ => { });

			// Two handlers under one verb means one silently never runs, and which
			// one depends on registration order.
			Assert.Throws<ArgumentException>(() => r.Register("diag", false, "second", _ => { }));

			// Casing must not smuggle one past: resolution is case-insensitive, so
			// "DIAG" is the same command.
			Assert.Throws<ArgumentException>(() => r.Register("DIAG", false, "third", _ => { }));

			// The first registration survives intact.
			Assert.True(r.TryResolve("diag", out DevCommandEntry e));
			Assert.Equal("first", e.Summary);
			Assert.Equal(1, r.Count);
		}

		[Fact]
		public void ACommandWithNoHandlerIsRefused() {
			var r = new DevCommandRegistry();

			// A command that parses and does nothing is the exact failure this
			// whole channel exists to catch.
			Assert.Throws<ArgumentNullException>(() => r.Register("diag", false, "s", null));

			r.Register("diag", false, "s", _ => { });
			Assert.True(r.TryResolve("diag", out _));
		}

		[Theory]
		[InlineData(null)]
		[InlineData("")]
		[InlineData("   ")]
		public void ANamelessCommandIsRefused(string name) {
			var r = new DevCommandRegistry();

			Assert.Throws<ArgumentException>(() => r.Register(name, false, "s", _ => { }));

			r.Register("diag", false, "s", _ => { });
			Assert.Equal(1, r.Count);
		}

		[Fact]
		public void TheHandlerRegisteredIsTheHandlerReturned() {
			var r = new DevCommandRegistry();
			string ran = null;
			r.Register("diag", false, "s", _ => ran = "diag");
			r.Register("shot", true, "s", req => ran = "shot:" + req.Argument);

			r.TryResolve("shot", out DevCommandEntry e);
			e.Handler(new DevRequest("shot", null, "bottomleft"));

			// Not merely "a handler ran" - the right one, with its argument.
			Assert.Equal("shot:bottomleft", ran);
		}

		[Fact]
		public void EntriesKeepRegistrationOrder() {
			var r = new DevCommandRegistry();
			r.Register("capture", false, "s", _ => { });
			r.Register("diag", false, "s", _ => { });
			r.Register("mutate", false, "s", _ => { });

			Assert.Equal(new[] { "capture", "diag", "mutate" }, r.Entries.Select(e => e.Name));
		}

		/// <summary>
		/// The help sentence used to be hand-written next to a switch listing the
		/// same commands, and the two were one command apart once. Now it is
		/// derived, so it cannot be.
		/// </summary>
		[Fact]
		public void TheHelpNamesEveryCommandAndSaysWhichTakeAnArgument() {
			var r = new DevCommandRegistry();
			r.Register("diag", false, "s", _ => { });
			r.Register("shot", true, "s", _ => { });

			Assert.Equal("diag, shot:<arg>", r.Names);
		}

		[Fact]
		public void ThePublishedListCarriesNameAndWhetherItTakesAnArgument() {
			var r = new DevCommandRegistry();
			r.Register("diag", false, "state dump", _ => { });
			r.Register("shot", true, "one region", _ => { });

			string[] lines = r.Publish()
				.Split('\n')
				.Where(l => l.Length > 0 && !l.StartsWith("#"))
				.ToArray();

			// The harness parses this. Tab separated, one command per line, and the
			// argument flag is the field it currently keeps its own copy of.
			Assert.Equal(2, lines.Length);
			Assert.Equal("diag\tnoarg\tstate dump", lines[0]);
			Assert.Equal("shot\targ\tone region", lines[1]);
		}

		/// <summary>
		/// A newline in a summary would split one command across two lines and the
		/// reader would see a malformed second entry - so the format is defended
		/// where the text enters, not where it is written out.
		/// </summary>
		[Theory]
		[InlineData("first\nsecond")]
		[InlineData("first\r\nsecond")]
		[InlineData("first\rsecond")]
		public void ASummaryCannotBreakThePublishedFormat(string summary) {
			var r = new DevCommandRegistry();
			r.Register("diag", false, summary, _ => { });

			string[] lines = r.Publish()
				.Split('\n')
				.Where(l => l.Length > 0 && !l.StartsWith("#"))
				.ToArray();

			Assert.Single(lines);
			Assert.DoesNotContain('\r', lines[0]);
		}

		// --- near-miss resolution --------------------------------------------
		//
		// These were parse tests against a closed enum. The property they protect
		// is real and measured, but it was never about the enum: it is that a word
		// NEAR a command must not resolve TO that command. That is a property of
		// exact-match lookup, so it is tested here, on the mechanism that now
		// decides it.
		//
		// Each uses the specific pair whose confusion was actually observed rather
		// than the whole command list, deliberately: a test holding its own copy of
		// every command name would reintroduce the second list this refactor
		// removed. What the real registry contains is answered at runtime by the
		// published list, not by a copy kept here.

		/// <summary>
		/// "kill" and "killcreep" share a prefix and destroy different things: one
		/// despawns mutated NPCs, the other strips the territory. A misresolve
		/// during the retraction test would remove nothing and the ground would
		/// correctly fail to revert, which reads as the reversibility claim being
		/// false.
		/// </summary>
		[Fact]
		public void APrefixDoesNotResolveToTheLongerCommand() {
			var r = new DevCommandRegistry();
			r.Register("kill", false, "kill", _ => { });
			r.Register("killcreep", false, "killcreep", _ => { });

			Assert.True(r.TryResolve("kill", out DevCommandEntry kill));
			Assert.Equal("kill", kill.Name);

			Assert.True(r.TryResolve("killcreep", out DevCommandEntry creep));
			Assert.Equal("killcreep", creep.Name);

			// A truncation is not the shorter command's business either.
			Assert.False(r.TryResolve("killcree", out _));
			Assert.False(r.TryResolve("killcreeps", out _));
		}

		/// <summary>
		/// "creep" and "creature" share their first three letters, and they are the
		/// two commands that both WRITE to the world. A misresolve either spawns an
		/// entity when territory was asked for or the reverse, and both look like
		/// "the trigger worked" from outside - the harness would then read a creep
		/// count that nothing had planted.
		/// </summary>
		[Fact]
		public void CommandsSharingAStemDoNotResolveToEachOther() {
			var r = new DevCommandRegistry();
			r.Register("creep", false, "creep", _ => { });
			r.Register("creature", false, "creature", _ => { });

			Assert.True(r.TryResolve("creep", out DevCommandEntry creep));
			Assert.Equal("creep", creep.Name);

			Assert.True(r.TryResolve("creature", out DevCommandEntry creature));
			Assert.Equal("creature", creature.Name);

			Assert.False(r.TryResolve("cree", out _));
			Assert.False(r.TryResolve("creeps", out _));
		}

		/// <summary>
		/// "seed" changes the world and "strains" reads it. A misresolve that
		/// turned a read into a write would seed a strain nobody asked for and then
		/// report the world it had just altered.
		/// </summary>
		[Fact]
		public void AReadAndAWriteDoNotResolveToEachOther() {
			var r = new DevCommandRegistry();
			r.Register("seed", false, "seed", _ => { });
			r.Register("strains", false, "strains", _ => { });

			Assert.True(r.TryResolve("seed", out _));
			Assert.True(r.TryResolve("strains", out _));
			Assert.False(r.TryResolve("seeds", out _));
			Assert.False(r.TryResolve("strain", out _));
		}

		[Fact]
		public void AnEmptyRegistryPublishesNoCommands() {
			var r = new DevCommandRegistry();

			Assert.Equal(string.Empty, r.Names);
			Assert.Equal(0, r.Count);

			// Header only. An empty list still has to be well-formed, because that
			// is what a harness reads from a mod that serves nothing.
			Assert.DoesNotContain(
				r.Publish().Split('\n').Where(l => l.Length > 0),
				l => !l.StartsWith("#"));
		}

		/// <summary>
		/// The capability line, spelled as a comment because a comment is the
		/// one format every existing parser of this file already skips. The
		/// harness reads this exact string to learn that replies echo a request
		/// id, and only then attaches one - so its exact spelling is a contract,
		/// pinned here against a tidy-up that rewords a "comment".
		/// </summary>
		[Fact]
		public void ThePublishedListAdvertisesTaggedReplies() {
			var r = new DevCommandRegistry();
			r.Register("diag", false, "a summary", _ => { });

			string published = r.Publish();

			Assert.Contains("# replies: tagged\n", published);

			// In the header, before any command line - a harness that stops
			// reading at the first command still sees it.
			Assert.True(published.IndexOf("# replies: tagged") < published.IndexOf("diag"),
				"the capability line sits below the first command");
		}
	}
}
