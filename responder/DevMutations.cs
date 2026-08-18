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

			r.Register(DevMutationArgs.SetTile, true,
				"Fill a rectangle with one tile type, as <x>,<y>,<w>,<h>,<type> in " +
					"tiles (at most " + DevMutationArgs.MaxArea + "). Server side.",
				req => SetTile(req.Argument, report));

			r.Register(DevMutationArgs.ClearTile, true,
				"Remove every tile in a rectangle, as <x>,<y>,<w>,<h> in tiles (at " +
					"most " + DevMutationArgs.MaxArea + "). Server side.",
				req => ClearTile(req.Argument, report));

			r.Register(DevMutationArgs.Despawn, true,
				"Remove active NPCs, as <npcid> or \"" + DevMutationArgs.Everything +
					"\" (which spares town NPCs). Server side.",
				req => Despawn(req.Argument, report));
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

			// A BODY IS NOT A POINT, and `position` is its top-left corner - so
			// the tile a caller names has to be turned into the corner that puts
			// the character ON it: half a body left, and a whole body up. This
			// is vanilla's own spawn placement, and without it "teleport to tile
			// 252" leaves the feet three tiles above it and the body half a tile
			// to the right of it.
			var target = new Vector2(
				tileX * 16 - player.width / 2, tileY * 16 - player.height);
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
		/// Fill a rectangle with one tile type.
		///
		/// CLAMPED TO THE WORLD RATHER THAN REFUSED AT THE EDGE, the same way the
		/// tile query is, and the reply says how many were actually written so a
		/// clamp is visible rather than silent.
		///
		/// `forced: true` because the alternative is a placement that quietly
		/// does nothing: PlaceTile normally refuses where a tile already is, so
		/// filling over existing ground would report success and change none of
		/// it. `plr: -1` says no player did this, which keeps it out of anybody's
		/// building history.
		/// </summary>
		private static void SetTile(string argument, Action<string> report) {
			if (!Allowed(DevMutationArgs.SetTile, report)) {
				return;
			}

			if (!DevMutationArgs.TryResolveTileFill(argument, out int x, out int y,
					out int width, out int height, out int type, out string problem)) {
				report("REFUSED: " + problem);
				return;
			}

			// TileLoader rather than TileID.Count, the same rule spawn and give
			// already follow: the ceiling moves when another mod loads. This one
			// is also load-bearing in a way theirs are not - PlaceTile indexes
			// TileID.Sets and Main.tileFrameImportant by this value, arrays
			// sized to TileLoader.TileCount, so an id past the end is an
			// IndexOutOfRangeException out of the dispatch rather than a refusal.
			if (type >= TileLoader.TileCount) {
				report("REFUSED: " + type + " is past the highest tile id this " +
					"install has (" + (TileLoader.TileCount - 1) + "), counting " +
					"every loaded mod's");
				return;
			}

			int right = Math.Min(x + width, Main.maxTilesX);
			int bottom = Math.Min(y + height, Main.maxTilesY);
			int placed = 0;
			int already = 0;
			int looked = 0;

			for (int i = Math.Max(0, x); i < right; i++) {
				for (int j = Math.Max(0, y); j < bottom; j++) {
					looked++;
					bool was = Main.tile[i, j].HasTile &&
						Main.tile[i, j].TileType == type;
					WorldGen.PlaceTile(i, j, type, mute: true, forced: true, plr: -1,
						style: 0);

					if (was) {
						// A tile that was this type before the fill is not a
						// placement, and counting it as one reported a fill over
						// its own previous run as work done twice.
						already++;
					}
					else if (Main.tile[i, j].HasTile && Main.tile[i, j].TileType == type) {
						placed++;
					}
				}
			}

			SyncTiles(x, y, width, height);

			// PLACED IS COUNTED BACK OFF THE TILEMAP rather than from PlaceTile's
			// return, because a tile type that cannot go where it was asked -
			// anything needing a wall or a floor - fails per tile and would
			// otherwise be reported as a fill that worked.
			report("OK: placed " + placed + " tile(s) of type " + type +
				(already > 0 ? " (" + already + " already were)" : "") + " over " +
				looked + " looked at, of the " + (width * height) + " asked for");
		}

		/// <summary>
		/// Remove every tile in a rectangle.
		///
		/// `noItem: true` so the ground does not turn into several hundred
		/// dropped items - which would be a second change nobody asked for, and
		/// one that then has to be cleaned up itself.
		/// </summary>
		private static void ClearTile(string argument, Action<string> report) {
			if (!Allowed(DevMutationArgs.ClearTile, report)) {
				return;
			}

			if (!DevMutationArgs.TryResolveArea(argument, out int x, out int y,
					out int width, out int height, out string problem)) {
				report("REFUSED: " + problem);
				return;
			}

			int right = Math.Min(x + width, Main.maxTilesX);
			int bottom = Math.Min(y + height, Main.maxTilesY);
			int cleared = 0;
			int looked = 0;

			for (int i = Math.Max(0, x); i < right; i++) {
				for (int j = Math.Max(0, y); j < bottom; j++) {
					looked++;
					if (!Main.tile[i, j].HasTile) {
						continue;
					}

					WorldGen.KillTile(i, j, fail: false, effectOnly: false, noItem: true);
					if (!Main.tile[i, j].HasTile) {
						cleared++;
					}
				}
			}

			SyncTiles(x, y, width, height);

			report("OK: cleared " + cleared + " tile(s) over " + looked +
				" looked at, of the " + (width * height) + " asked for");
		}

		/// <summary>
		/// Remove active NPCs, by type or all of them.
		///
		/// TOWN NPCs ARE SPARED by "all". They are saved with the world and do
		/// not move back in on their own, so sweeping one away while clearing a
		/// test's monsters is a change somebody notices days later and cannot
		/// undo. Naming a town NPC's id explicitly still removes it - that is a
		/// request rather than collateral.
		/// </summary>
		private static void Despawn(string argument, Action<string> report) {
			if (!Allowed(DevMutationArgs.Despawn, report)) {
				return;
			}

			if (!DevMutationArgs.TryResolveDespawn(argument, out bool everything,
					out int type, out string problem)) {
				report("REFUSED: " + problem);
				return;
			}

			int removed = 0;
			int spared = 0;

			for (int i = 0; i < Main.maxNPCs; i++) {
				NPC npc = Main.npc[i];
				if (npc == null || !npc.active) {
					continue;
				}

				if (everything) {
					if (npc.townNPC) {
						spared++;
						continue;
					}
				}
				else if (npc.type != type) {
					continue;
				}

				npc.active = false;
				removed++;

				// The client's copy of a slot is not cleared by the server simply
				// forgetting it; without this the NPC stays on screen, alive to
				// everyone but the server, until something else touches the slot.
				if (Main.netMode == NetmodeID.Server) {
					NetMessage.SendData(MessageID.SyncNPC, number: i);
				}
			}

			report("OK: removed " + removed + " NPC(s)" +
				(everything ? ", spared " + spared + " town NPC(s)" : " of type " + type));
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

		/// <summary>
		/// How wide and tall one sync packet's square may be. Packet 20 carries
		/// its dimensions in single bytes, so anything past 255 is truncated or
		/// garbled on the wire; 100 keeps a comfortable margin under that and
		/// under whatever a client is willing to re-read at once.
		/// </summary>
		private const int SyncSquare = 100;

		/// <summary>
		/// Tell the clients which tiles changed.
		///
		/// A separate call from Sync because it carries a rectangle: WorldData
		/// says nothing about tiles, and without this the clients keep drawing
		/// and colliding with the ground that used to be there until something
		/// else makes them re-read the section.
		///
		/// CLAMPED AND CHUNKED, neither of which the fill loops needed for
		/// themselves. Clamped, because the fill clamps its WRITES to the world
		/// and used to sync the rectangle as REQUESTED - so an edge fill made
		/// the server serialise out-of-world tiles through 1.4's unchecked
		/// Tilemap indexer, the exact out-of-bounds read the capture-bounds
		/// check exists to prevent. Chunked, because a legal fill can be 16384
		/// tiles in one row and the packet cannot carry a side past a byte.
		/// </summary>
		private static void SyncTiles(int x, int y, int width, int height) {
			if (Main.netMode != NetmodeID.Server) {
				return;
			}

			int left = Math.Max(0, x);
			int top = Math.Max(0, y);
			int right = Math.Min(x + width, Main.maxTilesX);
			int bottom = Math.Min(y + height, Main.maxTilesY);

			for (int i = left; i < right; i += SyncSquare) {
				for (int j = top; j < bottom; j += SyncSquare) {
					NetMessage.SendTileSquare(-1, i, j,
						Math.Min(SyncSquare, right - i),
						Math.Min(SyncSquare, bottom - j),
						TileChangeType.None);
				}
			}
		}
	}
}
