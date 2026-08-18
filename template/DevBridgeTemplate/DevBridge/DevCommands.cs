using System;

namespace TModLoaderMcp.DevBridge
{
	/// <summary>
	/// A parsed trigger: what to do, and optionally WHO should do it.
	///
	/// The target exists because two clients on one machine share Main.SavePath,
	/// so they share a trigger file and would race for it - whichever polled
	/// first would consume a request meant for the other, and the answer would be
	/// attributed to the wrong player.
	///
	/// The obvious fix was a separate save directory per client, and it does not
	/// work: an alternate save directory never auto-joins. `-join -player &lt;name&gt;
	/// -skipselect` lands at the main menu under BOTH -savedirectory and
	/// -tmlsavedirectory, with and without a Worlds folder, and - measured with
	/// the user's own untouched character as a control - regardless of which
	/// character is used. So the separation moved in here, where it is testable.
	/// </summary>
	public readonly struct DevRequest
	{
		public DevRequest(string verb, string target)
			: this(verb, target, null, null) {
		}

		public DevRequest(string verb, string target, string argument)
			: this(verb, target, argument, null) {
		}

		public DevRequest(string verb, string target, string argument, string id) {
			Verb = verb;
			Target = target;
			Argument = argument;
			Id = id;
		}

		/// <summary>
		/// The command word, lowercased, or null if the payload was malformed.
		///
		/// It used to be a DevCommand enum value, which meant PARSE decided what
		/// was a real command - and it could only decide that because the set was
		/// closed at compile time. The set now lives in DevCommandRegistry, so
		/// this is the word as written and whether anything serves it is asked at
		/// dispatch.
		/// </summary>
		public readonly string Verb;

		/// <summary>
		/// The part after a colon, or null. Which commands read one is the
		/// registry's business, not the parser's.
		/// </summary>
		public readonly string Argument;

		/// <summary>Player this is addressed to, or null for "whoever finds it".</summary>
		public readonly string Target;

		/// <summary>
		/// The request id a harness attached, or null. Echoed as the reply's
		/// first line so a late reply to a timed-out request cannot be read as
		/// the answer to the next one - see DevResponder.Report. Never given a
		/// meaning beyond the echo: it does not address, does not argue, and a
		/// payload without one is served exactly as it always was.
		/// </summary>
		public readonly string Id;

		/// <summary>
		/// The payload could not be read at all - as distinct from a well-formed
		/// word nothing serves.
		///
		/// One enum value used to mean both, and they are not the same failure.
		/// "diag@" is a trigger somebody composed wrong; "mutate" against a mod
		/// that does not serve it is a trigger composed correctly for the wrong
		/// mod. The second is exactly what a published command list lets a harness
		/// avoid, so it is worth being able to say which happened.
		/// </summary>
		public bool IsMalformed => Verb == null;

		/// <summary>
		/// Whether this side should act on the request.
		///
		/// An untargeted request is for anyone, which keeps every existing script
		/// working. A targeted one is for exactly one ADDRESSEE, and a side with
		/// no address at all is never it.
		///
		/// AN ADDRESS IS NOT ALWAYS A PLAYER NAME, and this parameter used to say
		/// it was. A client's address is its character name; a dedicated server's
		/// is "port7810", read off its own command line - see
		/// DevArtifacts.ServerAddress for why the port and not something else. A
		/// server had no address before that and so could never be addressed,
		/// which is what left two servers sharing one save directory unable to
		/// tell each other's requests apart.
		///
		/// THE TWO KINDS CANNOT COLLIDE, and not because of how they are spelled.
		/// They are never compared against the same file: a client polls
		/// &lt;mod&gt;-capture.trigger and a dedicated server polls
		/// &lt;mod&gt;-capture-server.trigger, so a "port7810" target is only ever
		/// read by a server and a character name only ever by a client. A player
		/// really can be called port7810; the paths are what keep it harmless,
		/// so no spelling rule here has to.
		/// </summary>
		public bool IsFor(string localAddress) {
			if (string.IsNullOrEmpty(Target)) {
				return true;
			}

			if (string.IsNullOrEmpty(localAddress)) {
				return false;
			}

			return string.Equals(Target, localAddress, StringComparison.OrdinalIgnoreCase);
		}
	}

	public static class DevCommands
	{
		/// <summary>
		/// The verb a bare trigger means.
		///
		/// An empty or whitespace-only file means capture, because that is what a
		/// bare `touch` of the trigger meant before commands existed and old
		/// scripts should keep working.
		/// </summary>
		public const string DefaultVerb = "capture";

		/// <summary>
		/// Split the trigger's contents: "diag", or "diag@somebody", or
		/// "shot:bottomleft@n43n".
		///
		/// This no longer decides whether a command EXISTS - see DevRequest.Verb.
		/// It decides only whether the payload has a shape that can be delivered,
		/// and an unrecognised word is still never treated as a default action:
		/// silently taking a screenshot because someone misspelled "diag" would
		/// look exactly like the command working.
		///
		/// The verb is lowercased; the TARGET keeps its case, because it is a
		/// player name that gets shown back to a human. Comparison is still
		/// case-insensitive - see DevRequest.IsFor.
		/// </summary>
		public static DevRequest Parse(string raw) {
			if (raw == null) {
				return new DevRequest(DefaultVerb, null);
			}

			// The shell writes with printf and no newline, but a human using echo
			// gets one, and a Windows editor adds \r. None of those should change
			// the meaning.
			string text = raw.Trim();

			// The request id comes off FIRST, from the very end, because it is
			// appended last - after the target - by a harness that composes
			// `verb:arg@target#r-<hex>`. The shape is deliberately unlikely as
			// prose: a bare `#beef` inside a free-text argument is four hex
			// characters somebody typed, and stripping it would silently change
			// what `say` says - so the marker requires the `r-` and the tail is
			// only taken when EVERY character after it is lowercase hex. A tail
			// that is not an id is left exactly where it was written.
			string id = null;
			int hash = text.LastIndexOf('#');
			if (hash >= 0 && IsRequestId(text, hash + 1)) {
				id = text.Substring(hash + 1);
				text = text.Substring(0, hash).TrimEnd();
			}

			if (text.Length == 0) {
				// A bare tagged trigger is still the historical bare trigger -
				// a capture - now with an id to answer under.
				return new DevRequest(DefaultVerb, null, null, id);
			}

			string target = null;
			int at = text.IndexOf('@');
			if (at >= 0) {
				target = text.Substring(at + 1).Trim();
				text = text.Substring(0, at).Trim();

				// "diag@" addresses nobody. Treating that as untargeted would send
				// it to whichever client polled first, which is the exact failure
				// the target exists to prevent - so it is an error instead.
				if (target.Length == 0 || text.Length == 0) {
					return Malformed(id);
				}
			}

			// The argument comes off AFTER the target, so "shot:bottomleft@n43n"
			// parses as all three. An empty half is an error for the same reason
			// "diag@" is: "shot:" names no region, and silently capturing some
			// default would be a wider picture than was asked for.
			string argument = null;
			int colon = text.IndexOf(':');
			if (colon >= 0) {
				argument = text.Substring(colon + 1).Trim();
				text = text.Substring(0, colon).Trim();

				if (argument.Length == 0 || text.Length == 0) {
					return Malformed(id);
				}
			}

			return new DevRequest(text.ToLowerInvariant(), target, argument, id);
		}

		/// <summary>
		/// Whether text from `from` onward is a request id: `r-` then 4 to 32
		/// lowercase hex characters. Narrow on purpose - see the caller.
		/// </summary>
		private static bool IsRequestId(string text, int from) {
			int hexAt = from + 2;
			int hexLength = text.Length - hexAt;

			if (hexAt > text.Length || text[from] != 'r' || text[from + 1] != '-') {
				return false;
			}

			if (hexLength < 4 || hexLength > 32) {
				return false;
			}

			for (int i = hexAt; i < text.Length; i++) {
				char c = text[i];
				if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) {
					return false;
				}
			}

			return true;
		}

		private static DevRequest Malformed(string id = null) {
			// The id survives a malformed payload so the ERROR naming the
			// problem still reaches the caller who asked, rather than reading
			// as a stale reply to them and as an answer to whoever asks next.
			return new DevRequest(null, null, null, id);
		}
	}
}
