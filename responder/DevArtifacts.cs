using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;

namespace TModLoaderMcp.DevBridge
{
	/// <summary>
	/// Names the responder's files per side.
	///
	/// A dedicated server and a client running on one machine share a single
	/// Main.SavePath, so one set of filenames is not a naming preference - it is
	/// a race. Both sides would poll the SAME trigger and whichever polled first
	/// would consume it, silently answering a request meant for the other. The
	/// heartbeats and diag dumps would then overwrite each other, which is worse
	/// than losing them: what survives is a well-formed file attributed to the
	/// wrong side, and that is exactly the mistake this whole diag channel exists
	/// to catch. It has already happened once at a different layer - a client
	/// "diag" that was really the server's, because the wait keyed on a marker in
	/// a shared log.
	///
	/// The client keeps the unsuffixed names it already had. tools/capture.sh and
	/// tools/run_selftest.sh address those by name and none of this changes what
	/// they do.
	/// </summary>
	/// <summary>
	/// The filenames this responder and its harness must agree on, DERIVED
	/// from the mod's name rather than spelled out.
	///
	/// They were constants reading "biomancy-", which is exactly what made this
	/// responder Biomancy's rather than any mod's: vendored into another mod it
	/// would write triggers under Biomancy's names, and two mods on one machine
	/// would consume each other's requests.
	///
	/// The rule is lowercase-the-mod-name, chosen because it REPRODUCES THE
	/// EXISTING NAMES EXACTLY - tModLoader's internal name here is Biomancy, so
	/// every file keeps the name it already had. That was the requirement rather
	/// than a nicety: an external harness polls for these by name, and a tidier
	/// scheme would have left it waiting on a trigger nobody writes.
	///
	/// Side suffixing still applies on top, via ForSide below. The two solve the
	/// same race one axis apart: this keeps two MODS out of each other's files,
	/// that keeps two SIDES out.
	/// </summary>
	public sealed class DevArtifactNames
	{
		public string Prefix { get; }

		public DevArtifactNames(string modName) {
			Prefix = (modName ?? string.Empty).ToLowerInvariant();
		}

		public string Trigger => Prefix + "-capture.trigger";

		public string Result => Prefix + "-capture.txt";

		public string Diag => Prefix + "-diag.txt";

		public string Heartbeat => Prefix + "-hooks.txt";

		public string Shot => Prefix + "-shot.png";

		/// <summary>
		/// What this responder answers to, written once at load.
		///
		/// The other five are a CHANNEL - a request goes in, an answer comes out.
		/// This one is a description of the channel itself, which is why it is
		/// written without being asked for: a harness has to know what it may ask
		/// before it can ask anything, and the alternative to publishing it is the
		/// arrangement this replaces, where the harness held its own copy of the
		/// list and the two drifted in silence.
		/// </summary>
		public string Commands => Prefix + "-commands.txt";
	}

	public static class DevArtifacts
	{
		public const string SideSuffix = "-server";

		/// <summary>
		/// Insert the side tag before the extension: biomancy-hooks.txt becomes
		/// biomancy-hooks-server.txt. Client names are returned untouched.
		/// </summary>
		public static string ForSide(string name, bool dedicatedServer) {
			if (!dedicatedServer || string.IsNullOrEmpty(name)) {
				return name;
			}

			return Path.GetFileNameWithoutExtension(name)
				+ SideSuffix
				+ Path.GetExtension(name);
		}

		/// <summary>
		/// Side suffix and player token together, in that order.
		///
		/// The two axes are independent and compose: the mod prefix keeps two
		/// MODS apart, the side suffix keeps two SIDES apart, and the token
		/// keeps two CLIENTS apart. A dedicated server passes a null token
		/// because it has no client to be confused with.
		/// </summary>
		public static string ForSide(string name, bool dedicatedServer, string playerToken) {
			string sided = ForSide(name, dedicatedServer);
			if (string.IsNullOrEmpty(playerToken)) {
				return sided;
			}

			// Before the extension. After it, `biomancy-diag.txt-n43n-003f` is
			// not a text file to anything that reads extensions.
			int dot = sided.LastIndexOf('.');
			return dot < 0
				? sided + "-" + playerToken
				: sided.Substring(0, dot) + "-" + playerToken + sided.Substring(dot);
		}

		/// <summary>
		/// A character name as a filename fragment, or null if there is no name.
		///
		/// Lowercase, runs of non-alphanumerics collapsed to one '-', trimmed,
		/// plus four hex of the MD5 of the ORIGINAL bytes. Kept identical to
		/// triggers.player_token on the harness side; the vector table in
		/// PlayerTokenTests is what holds the two together, because nothing at
		/// runtime would notice them drifting apart - each side would write and
		/// read its own spelling and simply never meet.
		///
		/// The hash is always present rather than only on collision: adding it
		/// only when two names clash needs both sides to agree about WHEN, and
		/// neither can see the other's players.
		/// </summary>
		public static string PlayerToken(string name) {
			if (string.IsNullOrWhiteSpace(name)) {
				return null;
			}

			var slug = new StringBuilder();
			bool pendingDash = false;
			foreach (char raw in name.ToLowerInvariant()) {
				if ((raw >= 'a' && raw <= 'z') || (raw >= '0' && raw <= '9')) {
					if (pendingDash && slug.Length > 0) {
						slug.Append('-');
					}

					pendingDash = false;
					slug.Append(raw);
				}
				else {
					// Deferred rather than appended: this collapses a RUN to
					// one dash and drops leading and trailing ones without a
					// second trimming pass.
					pendingDash = true;
				}
			}

			// UTF-8 of the ORIGINAL name, not the slug - the slug is lossy and
			// is exactly what the hash exists to disambiguate.
			byte[] digest = MD5.HashData(Encoding.UTF8.GetBytes(name));
			var hex = new StringBuilder(4);
			for (int i = 0; i < 2; i++) {
				hex.Append(digest[i].ToString("x2", CultureInfo.InvariantCulture));
			}

			// "player" rather than nothing when the slug is empty: the bare
			// digest does not match the token grammar the harness matches
			// against, and a file that grammar rejects simply disappears.
			return (slug.Length > 0 ? slug.ToString() : "player") + "-" + hex;
		}
	}
}
