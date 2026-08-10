using System.IO;

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
	}
}
