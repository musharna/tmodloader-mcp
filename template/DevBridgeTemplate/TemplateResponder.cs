using System.Text;
using Terraria;
using Terraria.ID;
using TModLoaderMcp.DevBridge;

namespace DevBridgeTemplate
{
	/// <summary>
	/// THE WHOLE OF WHAT A MOD HAS TO WRITE.
	///
	/// `DevResponder` is a `ModSystem`, so tModLoader finds this class and loads
	/// it with no registration on your part. Two members: what your mod knows
	/// that is worth reading back, and - optionally - verbs of your own.
	///
	/// Copy this file into your mod, rename it, and replace the body of
	/// CollectDiag with the counters you actually care about. Everything else in
	/// DevBridge/ is taken verbatim and never edited; see DevBridge/README-less
	/// note in SHA256SUMS for how a copy is checked against upstream.
	/// </summary>
	public class TemplateResponder : DevResponder
	{
		/// <summary>
		/// The body of &lt;mod&gt;-diag.txt, in the grammar the harness PARSES.
		///
		/// THE ONE THING THAT IS EASY TO GET WRONG. `key: value` becomes a typed
		/// field; an INDENTED line becomes a record under the key above it.
		/// Nothing here fails loudly when it is wrong - a malformed line is
		/// simply dropped, so the symptom is a diag with fewer fields than you
		/// wrote rather than an error.
		///
		/// Three rules worth copying:
		///
		///   - A COUNT IS A BARE NUMBER. `npcs-active: 4` comes back as the
		///     integer 4. Writing `npcs-active: 4 NPCs` comes back as a string,
		///     and every comparison against it silently becomes a string
		///     comparison.
		///   - NOTHING IS SPELLED "NONE", not "" and not "0". Zero is a
		///     measurement; the harness turns NONE into a null so the two cannot
		///     be confused.
		///   - A SUMMARY LINE MAY CARRY ITS OWN RECORDS. `npcs: active=4` with
		///     indented lines under it gives a caller both the count and which
		///     four, which is the question a count alone cannot answer.
		/// </summary>
		protected override string CollectDiag() {
			var sb = new StringBuilder();

			// `side` is read by the harness to tell the two halves of a
			// multiplayer session apart, so it is the one key here that is not
			// optional. First word wins; the rest is for a human.
			sb.Append("side: ").Append(SideName())
				.Append(" netmode=").Append(Main.netMode).Append('\n');

			sb.Append("world: ").Append(Blank(Main.worldName)).Append('\n');
			sb.Append("world-width: ").Append(Main.maxTilesX).Append('\n');
			sb.Append("world-height: ").Append(Main.maxTilesY).Append('\n');
			sb.Append("spawn: ").Append(Main.spawnTileX).Append(',')
				.Append(Main.spawnTileY).Append('\n');

			sb.Append("day: ").Append(Main.dayTime).Append('\n');
			sb.Append("raining: ").Append(Main.raining).Append('\n');

			// A dedicated server has no local player, and that is a different
			// state from a player whose name happens to be empty.
			Player local = Main.dedServ ? null : Main.LocalPlayer;
			sb.Append("player: ")
				.Append(local == null || !local.active
					? "N/A (no local player)"
					: local.name)
				.Append('\n');

			// WHERE THE PLAYER IS, AND WHAT THEY ARE CARRYING, in tiles and in
			// occupied slots. Two counters rather than decoration: they are what
			// makes `teleport` and `give` VERIFIABLE from outside. A verb whose
			// only evidence is its own success report is a verb nobody can check
			// - which is the failure this whole channel exists to remove.
			// THE TILE THE CHARACTER IS STANDING ON, which is not the tile their
			// position field names. `position` is the TOP-LEFT corner of a body
			// about three tiles tall, so a character standing perfectly on tile
			// 252 reports 249 - and a live run read that as a teleport that had
			// missed by three tiles when it had in fact landed exactly. Centre
			// horizontally, feet vertically: that is what "where are they" means
			// to anybody comparing it against a spawn point.
			sb.Append("player-tile: ")
				.Append(local == null || !local.active
					? "NONE"
					: ((int)(local.Center.X / 16)).ToString() + "," +
						((int)(local.Bottom.Y / 16)).ToString())
				.Append('\n');

			// BOTH NUMBERS, because one of them cannot see the commonest case.
			// A live run gave five torches to a character already carrying
			// torches: they merged into the existing stack, the occupied-slot
			// count stayed at 25, and the check read that as the verb having
			// done nothing. Slots answer "how full is the bag"; stacks answer
			// "did anything arrive", which is the question `give` raises.
			sb.Append("inventory-slots: ")
				.Append(local == null || !local.active ? "NONE" : OccupiedSlots(local).ToString())
				.Append('\n');

			sb.Append("inventory-stacks: ")
				.Append(local == null || !local.active ? "NONE" : HeldItems(local).ToString())
				.Append('\n');

			AppendNpcs(sb);
			AppendItems(sb);

			// Whether the world-changing verbs are on, said out loud. Without it
			// a caller learns that "spawn" is unserved only by asking for it and
			// being refused, and the refusal reads like a mod that is broken
			// rather than one that never opted in.
			sb.Append("mutations: on\n");

			return sb.ToString();
		}

		/// <summary>
		/// This mod's own verbs, which here are only the shared ones.
		///
		/// THESE THREE LINES ARE THE OPT-INS, and they are separate on purpose.
		/// The base class already answers `capture`, `diag`, `shot` and `tiles`,
		/// all of which only READ. Each line below adds something that does not:
		///
		///   DevMutations      changes the world you are sitting in
		///   DevCommandBridge  runs any mod's registered ModCommands
		///   DevChat           listens to chat, and can speak into it
		///
		/// Delete any one and the rest still work. Delete all three and this is
		/// a read-only harness that cannot alter a save.
		/// </summary>
		protected override void RegisterCommands(DevCommandRegistry r) {
			DevMutations.RegisterInto(r, Report);
			DevCommandBridge.RegisterInto(r, Report);
			DevChat.RegisterInto(r, Report);
		}

		/// <summary>
		/// How many inventory slots hold something.
		///
		/// The MAIN inventory only - `Main.InventorySlotsTotal` stops before the
		/// coin, ammo and equipment rows - because an item handed over by `give`
		/// lands there, and counting the rest would move this number for reasons
		/// that have nothing to do with the verb being checked.
		/// </summary>
		private static int OccupiedSlots(Player player) {
			int held = 0;

			for (int i = 0; i < Main.InventorySlotsTotal; i++) {
				Item item = player.inventory[i];
				if (item != null && !item.IsAir) {
					held++;
				}
			}

			return held;
		}

		/// <summary>
		/// How many THINGS the player is carrying, counting stacks.
		///
		/// The counter that actually moves when `give` works. Five torches
		/// handed to somebody already carrying torches occupy no new slot, so
		/// the slot count above is blind to exactly the case a stackable item
		/// produces - which a live run demonstrated by reporting 25 before and
		/// 25 after a give that had in fact worked.
		/// </summary>
		private static int HeldItems(Player player) {
			int held = 0;

			for (int i = 0; i < Main.InventorySlotsTotal; i++) {
				Item item = player.inventory[i];
				if (item != null && !item.IsAir) {
					held += item.stack;
				}
			}

			return held;
		}

		private static string SideName() {
			if (Main.netMode == NetmodeID.Server) {
				return "server";
			}

			return Main.netMode == NetmodeID.MultiplayerClient
				? "client"
				: "singleplayer";
		}

		/// <summary>A summary line, and the records that justify it.</summary>
		private static void AppendNpcs(StringBuilder sb) {
			var records = new StringBuilder();
			int active = 0;

			for (int i = 0; i < Main.maxNPCs; i++) {
				NPC npc = Main.npc[i];
				if (npc == null || !npc.active) {
					continue;
				}

				active++;

				// Capped, and the cap is visible in the summary rather than
				// silent: a world full of NPCs would otherwise produce a diag
				// file large enough to be slow to write and to read.
				if (active <= RecordLimit) {
					records.Append("  idx=").Append(i)
						.Append(" id=").Append(npc.type)
						.Append(" name=").Append(npc.TypeName)
						.Append('\n');
				}
			}

			sb.Append("npcs: active=").Append(active)
				.Append(" listed=").Append(active < RecordLimit ? active : RecordLimit)
				.Append('\n');
			sb.Append(records);
		}

		private static void AppendItems(StringBuilder sb) {
			var records = new StringBuilder();
			int active = 0;

			for (int i = 0; i < Main.maxItems; i++) {
				Item item = Main.item[i];
				if (item == null || !item.active) {
					continue;
				}

				active++;

				if (active <= RecordLimit) {
					records.Append("  idx=").Append(i)
						.Append(" id=").Append(item.type)
						.Append(" stack=").Append(item.stack)
						.Append(" name=").Append(item.Name)
						.Append('\n');
				}
			}

			sb.Append("items: active=").Append(active)
				.Append(" listed=").Append(active < RecordLimit ? active : RecordLimit)
				.Append('\n');
			sb.Append(records);
		}

		private const int RecordLimit = 25;

		/// <summary>
		/// An empty string is not a value. See the NONE rule in CollectDiag -
		/// the harness turns this marker into a null, and leaving the line blank
		/// would instead produce the empty string, which reads as a real answer.
		/// </summary>
		private static string Blank(string text) {
			return string.IsNullOrEmpty(text) ? "NONE" : text;
		}
	}
}
