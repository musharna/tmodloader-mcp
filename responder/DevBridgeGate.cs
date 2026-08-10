using System.IO;

namespace TModLoaderMcp.DevBridge
{
	/// <summary>
	/// Whether this install should run the dev responder at all.
	///
	/// WHY THIS IS NOT `#if DEBUG`, which is what it obviously should be:
	/// tModLoader's `-build` does not define DEBUG. Measured, not assumed - a
	/// conditional `#warning` in a throwaway file came back "DEBUG is NOT
	/// defined", with FNA the only symbol defined at all. So `#if DEBUG` would
	/// have compiled the responder out of the DEVELOPER's build too, and the only
	/// symptom would have been the harness waiting on a game that was never going
	/// to answer.
	///
	/// The deeper reason no compile-time flag can work here: the .tmod a player
	/// loads is the same artifact the developer builds. Identical bytes, so
	/// nothing inside them distinguishes the two. What differs is the INSTALL
	/// around them, and that is what this reads.
	///
	/// THE SIGNAL IS THE MOD'S OWN SOURCE FOLDER. tModLoader keeps mod sources in
	/// ModSources/&lt;Name&gt; beside the Mods folder it loads from. Somebody
	/// building this mod has that directory by definition - it is the thing
	/// `-build` is pointed at - and somebody who subscribed to the finished mod
	/// has no reason to have a folder named after its internals. It needs no
	/// setting, no marker file to remember to create, and no environment
	/// variable that a manually launched game would miss, so it cannot be
	/// switched on by a player and cannot be forgotten by a developer.
	///
	/// Deliberately NOT a check for "is a harness running". The responder is also
	/// driven by hand from tools/*.sh against a game started from the menu, and a
	/// gate keyed to the harness would break every one of those.
	/// </summary>
	public static class DevBridgeGate
	{
		public const string SourcesFolder = "ModSources";

		/// <summary>
		/// True where this mod is DEVELOPED, false where it is merely played.
		///
		/// Takes its paths rather than reading Main.SavePath itself, so the rule
		/// is testable without a game - which matters more here than usual,
		/// because the failure this guards against is invisible in both
		/// directions: gated on where it should be off, nothing happens and
		/// nobody notices; gated off where it should be on, the harness reports a
		/// timeout and blames the game.
		/// </summary>
		public static bool EnabledFor(string savePath, string modName) {
			if (string.IsNullOrWhiteSpace(savePath) || string.IsNullOrWhiteSpace(modName)) {
				return false;
			}

			return Directory.Exists(SourcePathFor(savePath, modName));
		}

		public static string SourcePathFor(string savePath, string modName) {
			return Path.Combine(savePath, SourcesFolder, modName);
		}

		/// <summary>
		/// What to say when it is off, for a log nobody reads until they are
		/// confused. Names the exact directory looked for, because "the responder
		/// is disabled" without it sends someone hunting a setting that does not
		/// exist.
		/// </summary>
		public static string Explain(string savePath, string modName) {
			return "the dev responder is off because " +
				SourcePathFor(savePath, modName) +
				" does not exist, which is how a played install is told from a " +
				"developed one. Nothing will answer a trigger file here.";
		}
	}
}
