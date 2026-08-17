using System;
using Microsoft.Xna.Framework;
using Terraria;
using Terraria.DataStructures;
using Terraria.ID;
using Terraria.ModLoader;

namespace TModLoaderMcp.DevBridge
{
	/// <summary>
	/// Five verbs that CHANGE the world, and are off until a mod asks for them.
	///
	/// EVERYTHING ELSE IN THIS FOLDER READS. capture, diag and shot observe a
	/// world and write files outside it; these spawn enemies into it, move
	/// somebody's character and set the clock. That difference is why they are
	/// not registered by DevResponder itself: a mod updating its vendored copy
	/// must not silently GAIN the power to do any of it. One line turns them on:
	///
	///     protected override void RegisterCommands(DevCommandRegistry r) =>
	///         DevMutations.RegisterInto(r, Report);
	///
	/// Registration is the whole of the opt-in. Not a setting, not a marker
	/// file, not an environment variable - each of those can be switched on
	/// somewhere other than the source somebody will read when they ask why an
	/// NPC appeared. DevBridgeGate still applies underneath: a played install
	/// runs none of this, whatever a mod registers.
	///
	/// `report` is PASSED IN rather than reached for. DevResponder.Report is
	/// protected, so only a subclass can hand it over - which makes "only a mod
	/// can turn these on" a rule the compiler enforces rather than a convention.
	///
	/// The rules - what an argument may say, and which side may say it - live in
	/// DevMutationArgs, which imports nothing but System and is therefore
	/// testable. This file is the applier, kept as thin as it can be made,
	/// because it is the half no test on a build machine can reach.
	/// </summary>
	public static class DevMutations
	{
		/// <summary>How long weather set by hand lasts before the world moves on.</summary>
		private const int WeatherTicks = 60 * 60 * 10;

		public static void RegisterInto(DevCommandRegistry r, Action<string> report) {
			if (r == null) {
				throw new ArgumentNullException(nameof(r));
			}

			if (report == null) {
				throw new ArgumentNullException(nameof(report),
					"the mutating verbs answer through Report; without it every " +
					"one of them would change the world and say nothing");
			}

			r.Register(DevMutationArgs.Time, true,
				"Set the clock (" + DevMutationArgs.TimeNames + "). Server side.",
				req => SetTime(req.Argument, report));

			r.Register(DevMutationArgs.Weather, true,
				"Set the weather (" + DevMutationArgs.WeatherNames + "). Server side.",
				req => SetWeather(req.Argument, report));

			r.Register(DevMutationArgs.Spawn, true,
				"Spawn NPCs at the world spawn, as <npcid> or <npcid>,<count>. " +
					"Server side.",
				req => SpawnNpc(req.Argument, report));

			r.Register(DevMutationArgs.Give, true,
				"Put an item in the local player's inventory, as <itemid> or " +
					"<itemid>,<stack>.",
				req => Give(req.Argument, report));

			r.Register(DevMutationArgs.Teleport, true,
				"Move the local player to \"spawn\" or to tile coordinates x,y.",
				req => Teleport(req.Argument, report));
		}

		/// <summary>
		/// The two questions every verb asks first: may this side do it, and is
		/// there a world to do it in.
		///
		/// The world check is here rather than in each verb because all five
		/// fail the same way without one - Main.spawnTileX is 0, the NPC array
		/// is empty, and the answer is a success report about a change made to
		/// nothing. DevResponder only dispatches once a world is ready, so this
		/// is a belt to that braces; it costs a comparison and removes a class
		/// of report that would be a lie.
		/// </summary>
		private static bool Allowed(string verb, Action<string> report) {
			string side = DevMutationArgs.SideProblem(verb, Main.dedServ,
				Main.netMode == NetmodeID.MultiplayerClient);

			if (side != null) {
				report("REFUSED: " + side);
				return false;
			}

			if (Main.maxTilesX <= 0 || Main.maxTilesY <= 0) {
				report("REFUSED: \"" + verb + "\" needs a loaded world, and this " +
					"side has none");
				return false;
			}

			return true;
		}

		private static void SetTime(string argument, Action<string> report) {
			if (!Allowed(DevMutationArgs.Time, report)) {
				return;
			}

			if (!DevMutationArgs.TryResolveTime(argument, out bool dayTime,
					out double fraction, out string problem)) {
				report("REFUSED: " + problem);
				return;
			}

			// The world says how long a phase is; DevMutationArgs deliberately
			// does not know, so that it can be read without the game.
			double length = dayTime ? Main.dayLength : Main.nightLength;

			Main.dayTime = dayTime;
			Main.time = fraction * length;

			Sync();
			report("OK: " + (dayTime ? "day" : "night") + " at " +
				Main.time.ToString("F0") + " of " + length.ToString("F0"));
		}

		private static void SetWeather(string argument, Action<string> report) {
			if (!Allowed(DevMutationArgs.Weather, report)) {
				return;
			}

			if (!DevMutationArgs.TryResolveWeather(argument, out bool raining,
					out float severity, out string problem)) {
				report("REFUSED: " + problem);
				return;
			}

			if (raining) {
				Main.raining = true;
				Main.rainTime = WeatherTicks;
				Main.maxRaining = severity;
				Main.cloudAlpha = severity;
			}
			else {
				Main.StopRain();
			}

			Sync();
			report("OK: raining=" + Main.raining + " severity=" +
				Main.maxRaining.ToString("F2"));
		}

		private static void SpawnNpc(string argument, Action<string> report) {
			if (!Allowed(DevMutationArgs.Spawn, report)) {
				return;
			}

			if (!DevMutationArgs.TryResolveIdAndCount(argument, "NPC id",
					out int type, out int count, out string problem)) {
				report("REFUSED: " + problem);
				return;
			}

			// NPCLoader rather than NPCID.Count: the ceiling moves when another
			// mod loads, and a hard-coded vanilla bound would refuse every
			// modded NPC on a machine that has any.
			if (type >= NPCLoader.NPCCount) {
				report("REFUSED: " + type + " is past the highest NPC id this " +
					"install has (" + (NPCLoader.NPCCount - 1) + "), counting " +
					"every loaded mod's");
				return;
			}

			var source = new EntitySource_Misc("TModLoaderMcp.DevBridge:spawn");
			int x = Main.spawnTileX * 16;
			int y = Main.spawnTileY * 16;
			int made = 0;

			for (int i = 0; i < count; i++) {
				// NewNPC syncs itself when this is the server, which is the only
				// side allowed to get here in multiplayer.
				int index = NPC.NewNPC(source, x, y, type);
				if (index >= 0 && index < Main.maxNPCs && Main.npc[index].active) {
					made++;
				}
			}

			if (made == 0) {
				report("ERROR: nothing spawned - the NPC array may be full, or " +
					type + " may refuse to exist here");
				return;
			}

			// The count is reported rather than assumed. The array has a ceiling
			// and NewNPC returns quietly when it is reached, so a request for
			// fifty that produced three would otherwise read as fifty.
			report("OK: spawned " + made + " of " + count + " id=" + type +
				" at tile " + Main.spawnTileX + "," + Main.spawnTileY);
		}

		private static void Give(string argument, Action<string> report) {
			if (!Allowed(DevMutationArgs.Give, report)) {
				return;
			}

			if (!DevMutationArgs.TryResolveIdAndCount(argument, "item id",
					out int type, out int stack, out string problem)) {
				report("REFUSED: " + problem);
				return;
			}

			if (type >= ItemLoader.ItemCount) {
				report("REFUSED: " + type + " is past the highest item id this " +
					"install has (" + (ItemLoader.ItemCount - 1) + "), counting " +
					"every loaded mod's");
				return;
			}

			Player player = Main.LocalPlayer;
			if (player == null || !player.active) {
				report("ERROR: there is no active local player to give to");
				return;
			}

			player.QuickSpawnItem(
				new EntitySource_Misc("TModLoaderMcp.DevBridge:give"), type, stack);

			report("OK: gave " + stack + " of item id=" + type + " to " +
				player.name);
		}

		private static void Teleport(string argument, Action<string> report) {
			if (!Allowed(DevMutationArgs.Teleport, report)) {
				return;
			}

			if (!DevMutationArgs.TryResolvePlace(argument, out bool atSpawn,
					out int tileX, out int tileY, out string problem)) {
				report("REFUSED: " + problem);
				return;
			}

			if (atSpawn) {
				tileX = Main.spawnTileX;
				tileY = Main.spawnTileY;
			}
			else if (tileX >= Main.maxTilesX || tileY >= Main.maxTilesY) {
				report("REFUSED: tile " + tileX + "," + tileY + " is outside this " +
					"world, which is " + Main.maxTilesX + " by " + Main.maxTilesY +
					" tiles");
				return;
			}

			Player player = Main.LocalPlayer;
			if (player == null || !player.active) {
				report("ERROR: there is no active local player to move");
				return;
			}

			// Placed a little above the tile so the arrival is standing on it
			// rather than inside it.
			var target = new Vector2(tileX * 16, tileY * 16 - player.height);
			player.Teleport(target, 1);

			if (Main.netMode == NetmodeID.MultiplayerClient) {
				// Vanilla's own teleport idiom. Without it the server learns the
				// new position from the next movement packet, which is late
				// enough for other clients to see the character walk there.
				NetMessage.SendData(MessageID.TeleportEntity, -1, -1, null, 0,
					player.whoAmI, target.X, target.Y, 1);
			}

			report("OK: moved " + player.name + " to tile " + tileX + "," + tileY);
		}

		/// <summary>
		/// Tell the clients what changed, on the side that is allowed to.
		///
		/// Only time and weather need this: NPC.NewNPC syncs itself, and the two
		/// player-side verbs run where the player is. Singleplayer has nobody to
		/// tell.
		/// </summary>
		private static void Sync() {
			if (Main.netMode == NetmodeID.Server) {
				NetMessage.SendData(MessageID.WorldData);
			}
		}
	}
}
