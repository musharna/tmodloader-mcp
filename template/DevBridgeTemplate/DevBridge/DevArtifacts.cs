using System;
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
		/// Token and side suffix together, TOKEN FIRST:
		/// biomancy-diag-n43n-003f-server.txt.
		///
		/// The two axes are independent and compose: the mod prefix keeps two
		/// MODS apart, the side suffix keeps two SIDES apart, and the token
		/// keeps two ADDRESSEES apart - two clients by their player tokens, and
		/// (since a dedicated server got an address) two servers by theirs.
		///
		/// THE ORDER IS THE HARNESS'S, and this method used to have the other
		/// one. triggers.py's Artifacts._named splices the token in first and
		/// config.py's Config.artifact then appends "-server" to that already
		/// tokened name, giving `-token-server`; this composed `-server-token`.
		/// Those are two different files, and they agreed only because the one
		/// combination that tells them apart - a dedicated server WITH a token -
		/// could not arise: LocalPlayerName is null whenever Main.dedServ is.
		/// DevArtifactsTests carried that divergence as a standing warning that
		/// giving a server a token could not be done safely without fixing this
		/// composition first. Giving a server a token is exactly what
		/// ServerAddress below does, so this is that fix.
		///
		/// No existing name moved. With no token the side suffix is the only
		/// operation, and with no side suffix the token is - the order can only
		/// be observed when BOTH apply, which is precisely the case that had
		/// never happened.
		/// </summary>
		public static string ForSide(string name, bool dedicatedServer, string token) {
			// Absence must stay absence even with a token in hand: a null name
			// has nothing to tag, and "-n43n-003f" alone would be a real,
			// plausible-looking file in Main.SavePath - the same failure the
			// two-argument sibling above guards against for "-server".
			if (string.IsNullOrEmpty(name) || string.IsNullOrEmpty(token)) {
				return ForSide(name, dedicatedServer);
			}

			// Same Path methods the two-argument overload uses, not manual
			// dot-finding, so the two cannot silently diverge on a name with a
			// dot in a directory component. Before the extension: after it,
			// `biomancy-diag.txt-n43n-003f` is not a text file to anything
			// that reads extensions.
			string tokened =
				Path.GetFileNameWithoutExtension(name) + "-" + token + Path.GetExtension(name);

			// The side suffix goes on LAST, through the same overload the
			// untokened path uses, so there is one implementation of "-server"
			// rather than two that can drift.
			return ForSide(tokened, dedicatedServer);
		}

		/// <summary>
		/// What a dedicated server answers to, or null if it cannot tell.
		///
		/// A dedicated server has no LocalPlayer, so until now it had no address
		/// at all: every request written to its side of the trigger was
		/// untargeted, and two harness sessions each driving their own server out
		/// of ONE Main.SavePath were indistinguishable on disk. Whichever server
		/// polled first took the request, both wrote their answers to the same
		/// filename, and either session's cleanup could destroy the other's
		/// in-flight request. That is the same race per-player naming removed for
		/// clients, on the axis nothing had covered.
		///
		/// THE PORT IS THE ADDRESS because it is the one value both sides already
		/// hold independently, with no handshake to get wrong: the harness passes
		/// -port when it launches the server, and the server reads the same
		/// argument back off its own command line. It is also unique by
		/// construction - two servers cannot bind one port, so two servers
		/// sharing a save directory cannot share an address.
		///
		/// READ FROM THE COMMAND LINE rather than from Terraria's own networking
		/// state, and that is deliberate: Environment.GetCommandLineArgs() is
		/// plain .NET, so this rule compiles and is tested in responder/tests with
		/// nothing of Terraria's on the compile line - the same vendorability
		/// boundary every other file here respects. A rule that read a Terraria
		/// field would live where nothing in this repository can check it.
		///
		/// ONE STRING FOR BOTH JOBS. The value returned here is both the target a
		/// request is addressed to (`diag@port7810`) and the token that names the
		/// server's answers (`biomancy-diag-port7810-server.txt`). Two spellings
		/// of one identity is one more place for the two sides to disagree.
		///
		/// NULL IS A SUPPORTED ANSWER, not a failure: a server started without
		/// -port, or with one that is not a port, has no address and behaves
		/// exactly as every server did before this existed - it answers anything
		/// untargeted and is never the addressee of a targeted request. That is
		/// the old ambiguity rather than a new outage.
		///
		/// The LAST -port decides. Terraria's own argument handling lets a later
		/// occurrence win, and picking an earlier one would be this side guessing
		/// differently from the process it is describing.
		/// </summary>
		public static string ServerAddress(string[] commandLine) {
			if (commandLine == null) {
				return null;
			}

			string found = null;
			for (int i = 0; i + 1 < commandLine.Length; i++) {
				if (string.Equals(commandLine[i], "-port", StringComparison.OrdinalIgnoreCase)) {
					found = commandLine[i + 1];
				}
			}

			// NumberStyles.None on purpose: no sign, no whitespace, no group
			// separators. "+7810" and " 7810" are things a shell can produce and
			// the harness never writes, and accepting them here would let the two
			// sides derive different addresses from one command line.
			if (found == null
				|| !int.TryParse(found, NumberStyles.None, CultureInfo.InvariantCulture, out int port)
				|| port < 1
				|| port > 65535) {
				return null;
			}

			// Re-rendered from the parsed int rather than echoed, so "07810" and
			// "7810" are one address. The harness formats an int too.
			return "port" + port.ToString(CultureInfo.InvariantCulture);
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
