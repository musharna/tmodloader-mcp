using System;
using System.Globalization;

namespace TModLoaderMcp.DevBridge
{
	/// <summary>
	/// Everything the mutating verbs decide BEFORE they touch the world.
	///
	/// WHY THIS IS A SEPARATE FILE FROM DevMutations. That one imports Terraria,
	/// so it cannot be on the vendor test project's compile line - the same
	/// position FrameShot is in, covered by a source scan rather than by tests.
	/// Left there, every refusal message and every argument rule would be
	/// untestable, and those are most of what there is to get wrong: a verb that
	/// spawns the wrong thing is caught the first time somebody looks at the
	/// world, and a verb that refuses with a sentence naming the wrong side is
	/// never caught at all.
	///
	/// So the rules live here, importing nothing but System, and the applier is
	/// as thin as it can be made.
	///
	/// It also holds the two questions this side genuinely cannot answer, by NOT
	/// answering them: the live id ceiling (which includes modded content) and
	/// how long a day is. Time resolves to a FRACTION of a phase rather than to
	/// ticks for exactly that reason - hard-coding 54000 here would be a fact
	/// about the game written into a file that cannot see it.
	/// </summary>
	public static class DevMutationArgs
	{
		public const string Time = "time";

		public const string Weather = "weather";

		public const string Spawn = "spawn";

		public const string Give = "give";

		public const string Teleport = "teleport";

		/// <summary>
		/// The most of anything one request may create.
		///
		/// A cap rather than trust. "spawn:1,10000" is one keystroke away from
		/// "spawn:1,1000" and would fill the NPC array, and the game does not
		/// come back from that quickly - a harness driving this would report a
		/// timeout and blame the responder. Refusing names the cap, so the
		/// second attempt is a smaller number rather than a bug report.
		/// </summary>
		public const int MaxCount = 50;

		public static string TimeNames => "dawn, noon, dusk, midnight";

		public static string WeatherNames => "clear, rain, storm";

		/// <summary>
		/// Where in the day this name falls: which phase, and how far into it.
		///
		/// Fractions rather than ticks - see the class summary. `dawn` and `dusk`
		/// are the STARTS of their phases, `noon` and `midnight` the middles,
		/// which is what those words mean to the person typing them.
		/// </summary>
		public static bool TryResolveTime(string argument, out bool dayTime,
				out double fraction, out string problem) {
			dayTime = true;
			fraction = 0.0;
			problem = null;

			switch (Normalise(argument)) {
				case "dawn":
					dayTime = true;
					fraction = 0.0;
					return true;

				case "noon":
					dayTime = true;
					fraction = 0.5;
					return true;

				case "dusk":
					dayTime = false;
					fraction = 0.0;
					return true;

				case "midnight":
					dayTime = false;
					fraction = 0.5;
					return true;

				default:
					problem = Unknown(argument, TimeNames);
					return false;
			}
		}

		/// <summary>
		/// Rain, and how hard.
		///
		/// Severity is a 0-1 dial rather than a second verb because that is the
		/// shape the world keeps it in; `storm` and `rain` differ by the number
		/// alone, and spelling them as two words is what a caller wants to type.
		/// </summary>
		public static bool TryResolveWeather(string argument, out bool raining,
				out float severity, out string problem) {
			raining = false;
			severity = 0f;
			problem = null;

			switch (Normalise(argument)) {
				case "clear":
					return true;

				case "rain":
					raining = true;
					severity = 0.5f;
					return true;

				case "storm":
					raining = true;
					severity = 1.0f;
					return true;

				default:
					problem = Unknown(argument, WeatherNames);
					return false;
			}
		}

		/// <summary>
		/// "5" or "5,3": a content id, and how many of it.
		///
		/// `what` names the thing in the refusal - "NPC id", "item id" - because
		/// the two verbs sharing this shape have completely different id spaces
		/// and a message saying only "id" sends the reader to look up the wrong
		/// list.
		///
		/// Zero is refused SPECIFICALLY. It parses, it is in range, and in both
		/// of Terraria's id spaces it means "nothing" - so it is the one value
		/// that would succeed, do nothing, and report success.
		/// </summary>
		public static bool TryResolveIdAndCount(string argument, string what,
				out int id, out int count, out string problem) {
			id = 0;
			count = 1;
			problem = null;

			string text = (argument ?? string.Empty).Trim();
			if (text.Length == 0) {
				problem = "\"" + what + "\" needs a number, as <id> or <id>,<count>";
				return false;
			}

			string[] parts = text.Split(',');
			if (parts.Length > 2) {
				problem = "\"" + text + "\" has " + parts.Length +
					" comma-separated parts; " + what + " takes <id> or <id>,<count>";
				return false;
			}

			if (!TryWhole(parts[0], out id)) {
				problem = "\"" + parts[0].Trim() + "\" is not a positive whole " +
					what;
				return false;
			}

			if (id == 0) {
				problem = "0 is not a " + what + " - it is how Terraria spells " +
					"\"nothing\", so this would have succeeded and done nothing";
				return false;
			}

			if (parts.Length == 1) {
				return true;
			}

			if (!TryWhole(parts[1], out count) || count == 0) {
				problem = "\"" + parts[1].Trim() + "\" is not a count of one or more";
				return false;
			}

			if (count > MaxCount) {
				problem = "a count of " + count + " is past the limit of " +
					MaxCount + " one request may create";
				return false;
			}

			return true;
		}

		/// <summary>
		/// "spawn", or "x,y" in TILE coordinates.
		///
		/// Tiles rather than world units because that is what the game's own map
		/// and every wiki coordinate are in, and because a caller who means tile
		/// 400 and gets world position 400 lands twenty-five tiles from where
		/// they meant - close enough to look like a bug in the teleport rather
		/// than in the units.
		///
		/// The upper bound belongs to the world and is checked by the applier;
		/// what can be decided here is that a coordinate is a whole number and
		/// not negative.
		/// </summary>
		public static bool TryResolvePlace(string argument, out bool atSpawn,
				out int tileX, out int tileY, out string problem) {
			atSpawn = false;
			tileX = 0;
			tileY = 0;
			problem = null;

			string text = (argument ?? string.Empty).Trim();

			if (Normalise(text) == "spawn") {
				atSpawn = true;
				return true;
			}

			string[] parts = text.Split(',');
			if (parts.Length != 2) {
				problem = "\"" + text + "\" is neither \"spawn\" nor a pair of " +
					"tile coordinates written x,y";
				return false;
			}

			if (!TryWhole(parts[0], out tileX) || !TryWhole(parts[1], out tileY)) {
				problem = "\"" + text + "\" is not a pair of whole, non-negative " +
					"tile coordinates";
				return false;
			}

			return true;
		}

		/// <summary>The largest rectangle one `tiles` query may scan.
		///
		/// The smallest Terraria world is 4200 by 1200 tiles, so an unbounded
		/// query is a request to walk five million of them and build a string
		/// out of the answer. The cap refuses and names itself, for the same
		/// reason MaxCount does: the second attempt should be a smaller
		/// rectangle rather than a bug report about a game that stopped
		/// answering.</summary>
		public const int MaxArea = 128 * 128;

		/// <summary>
		/// "x,y,w,h" in TILE coordinates, for a rectangle to look at.
		///
		/// It lives beside the mutation rules because this file is the folder's
		/// Terraria-free half — every argument rule that can be decided without
		/// the game, and therefore every one that can be TESTED without it. The
		/// file's name is older than that job and stays, because renaming a
		/// vendored file breaks every consumer's copy for no gain.
		/// </summary>
		public static bool TryResolveArea(string argument, out int x, out int y,
				out int width, out int height, out string problem) {
			x = 0;
			y = 0;
			width = 0;
			height = 0;
			problem = null;

			string text = (argument ?? string.Empty).Trim();
			string[] parts = text.Split(',');

			if (parts.Length != 4) {
				problem = "\"" + text + "\" is not a rectangle written x,y,w,h in " +
					"tile coordinates";
				return false;
			}

			if (!TryWhole(parts[0], out x) || !TryWhole(parts[1], out y)
					|| !TryWhole(parts[2], out width) || !TryWhole(parts[3], out height)) {
				problem = "\"" + text + "\" holds something that is not a whole, " +
					"non-negative number";
				return false;
			}

			if (width == 0 || height == 0) {
				problem = "a rectangle " + width + " by " + height +
					" holds no tiles at all";
				return false;
			}

			if (width * height > MaxArea) {
				problem = width + " by " + height + " is " + (width * height) +
					" tiles, past the limit of " + MaxArea + " one query may scan";
				return false;
			}

			return true;
		}

		/// <summary>
		/// Verbs that change something the SERVER owns: the clock, the weather,
		/// the NPC array.
		/// </summary>
		public static bool NeedsWorldAuthority(string verb) {
			string v = Normalise(verb);
			return v == Time || v == Weather || v == Spawn;
		}

		/// <summary>Verbs that need somebody standing in the world.</summary>
		public static bool NeedsLocalPlayer(string verb) {
			string v = Normalise(verb);
			return v == Give || v == Teleport;
		}

		/// <summary>
		/// Why this side cannot do this, or null.
		///
		/// THE REFUSAL NAMES THE OTHER SIDE, which is the whole point of having
		/// one. A client that set Main.time would be corrected by the next world
		/// packet: the command reports success, the clock moves, and it moves
		/// back - a change that appears to work and then undoes itself, which is
		/// far worse to debug than a refusal costing one round trip.
		///
		/// Singleplayer is neither a dedicated server nor a multiplayer client,
		/// so it passes both tests and does everything - which is correct rather
		/// than a loophole: it IS both sides.
		/// </summary>
		public static string SideProblem(string verb, bool dedicatedServer,
				bool multiplayerClient) {
			if (multiplayerClient && NeedsWorldAuthority(verb)) {
				return "\"" + Normalise(verb) + "\" changes something the SERVER " +
					"owns, and a client that changed it would be corrected by the " +
					"next world packet - the change would appear to work and then " +
					"undo itself. Send this to the server: " +
					Normalise(verb) + "@<server-address>.";
			}

			if (dedicatedServer && NeedsLocalPlayer(verb)) {
				return "\"" + Normalise(verb) + "\" needs a local player, and a " +
					"dedicated server has none - it runs the world without " +
					"standing in it. Ask a client, by name.";
			}

			return null;
		}

		/// <summary>
		/// Case and separators are noise from a human typing into a trigger
		/// file. Same rule as ShotRegion, for the same reason.
		/// </summary>
		private static string Normalise(string name) {
			if (string.IsNullOrEmpty(name)) {
				return string.Empty;
			}

			var sb = new System.Text.StringBuilder(name.Length);
			foreach (char c in name) {
				if (c == '-' || c == '_' || c == ' ') {
					continue;
				}

				sb.Append(char.ToLowerInvariant(c));
			}

			return sb.ToString();
		}

		/// <summary>
		/// NumberStyles.None on purpose: no sign, no thousands separators, no
		/// surrounding whitespace beyond the trim. "-1" and "+2" and "1 000" are
		/// all refused as what they are rather than silently reinterpreted, and
		/// a negative count reaching the applier is a loop that never ends.
		/// </summary>
		private static bool TryWhole(string text, out int value) {
			return int.TryParse((text ?? string.Empty).Trim(), NumberStyles.None,
				CultureInfo.InvariantCulture, out value);
		}

		private static string Unknown(string argument, string names) {
			return "\"" + (argument ?? string.Empty).Trim() +
				"\" is not one of " + names;
		}
	}
}
