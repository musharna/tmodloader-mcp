using System;
using System.Collections.Generic;
using System.IO;
using Microsoft.Xna.Framework;
using Terraria;
using Terraria.Graphics.Capture;
using Terraria.ModLoader;

namespace TModLoaderMcp.DevBridge
{
	/// <summary>
	/// Lets a screenshot be requested from outside the game, without touching the
	/// game's input at all - the request is a FILE, so no synthetic keystrokes and
	/// no window focus are involved. That also means it cannot be fooled by
	/// another window sitting on top of the game, which is exactly how OS-level
	/// capture failed here: a cropped screen grab returned a picture of Discord
	/// and passed every check available.
	///
	/// WHAT A CAPTURE CANNOT SEE: dust. Measured - three captures with a Bloom
	/// mutant on screen and three without contained zero magenta pixels either
	/// way, while the mod's own counter showed 147 particles spawned on that
	/// client. Terraria's capture camera renders the world and its entities, not
	/// the particle layer. Do not try to verify a particle effect with a
	/// screenshot here; count the emissions instead.
	///
	/// CAPTURE is client only: Main.dedServ is a graphics-less path, so
	/// QuickScreenshot would have nothing to draw with. The POLLER is not. A
	/// dedicated server can be asked for a diag mid-session, and that is the only
	/// way to ask both sides of a multiplayer session what they see at the same
	/// moment - which is the question "the client reports no NPC" cannot answer
	/// on its own. Server-side artifacts are suffixed (DevArtifacts) because both
	/// sides share one Main.SavePath.
	/// </summary>
	public abstract class DevResponder : ModSystem
	{
		/// <summary>
		/// The filenames, derived from THIS mod's name rather than spelled out.
		///
		/// They were five consts reading "biomancy-", which is what made this
		/// responder Biomancy's rather than any mod's. Built from Mod.Name so
		/// the same source vendored into another mod addresses its own files -
		/// and, because tModLoader's internal name here is Biomancy, every name
		/// produced is byte-for-byte the one that was hardcoded. That is checked
		/// by DevArtifactNamesTests rather than assumed: an external harness
		/// polls for these by name, so a rename would strand it silently.
		///
		/// STATIC because Report and PathFor are, and every write goes through
		/// them - the compiler caught that, not a review. Assigned in Load,
		/// where ModSystem.Mod is already set, and cleared in Unload for the
		/// reason FrameShot spells out: a static field outlives a mod reload,
		/// and one still holding the previous load's name is a stale answer
		/// rather than an obvious failure.
		/// </summary>
		private static DevArtifactNames _names;

		private static DevArtifactNames Names => _names;

		/// <summary>
		/// The verbs this side serves, built at load rather than compiled in.
		///
		/// Instance rather than static because the handlers are instance methods -
		/// and because a registry surviving a mod reload would hold delegates onto
		/// the PREVIOUS load's DevCapture, which is the same stale-static trap
		/// _names is cleared in Unload to avoid, one indirection deeper and far
		/// harder to see: every command would still dispatch, into an object whose
		/// world is gone.
		/// </summary>
		private DevCommandRegistry _commands;

		/// <summary>
		/// A PLAYED INSTALL DOES NOT LOAD THIS AT ALL.
		///
		/// Refused here rather than guarded inside each hook, which is the
		/// difference between not running and running while doing nothing. This
		/// class polls the save directory on four update hooks, writes a
		/// heartbeat, publishes a command list and - the part that matters -
		/// executes whatever a trigger file asks for, including planting enemies
		/// and stamping tiles into somebody's world. A guard at the top of Tick
		/// would leave every one of those code paths reachable by anything that
		/// called them.
		///
		/// See DevBridgeGate for why this is not `#if DEBUG`: tModLoader's build
		/// does not define it, so the obvious version of this would have removed
		/// the responder from the developer's build too.
		/// </summary>
		public override bool IsLoadingEnabled(Mod mod) {
			if (DevBridgeGate.EnabledFor(Main.SavePath, mod.Name)) {
				return true;
			}

			// Info rather than silence. A responder that is absent by design and
			// a responder that is broken look identical from outside, and this
			// line is the only thing on the machine that tells them apart.
			mod.Logger.Info(DevBridgeGate.Explain(Main.SavePath, mod.Name));
			return false;
		}

		public override void Load() {
			_names = new DevArtifactNames(Mod.Name);
			_commands = BuildCommands();
			PublishCommands();
		}

		public override void Unload() {
			_names = null;
			_commands = null;
		}

		private DevCommandRegistry BuildCommands() {
			var r = new DevCommandRegistry();

			r.Register("capture", false,
				"Save a PNG of the whole frame via Terraria's own capture camera.",
				_ => CaptureNow());

			r.Register("diag", false,
				"Write this side's state dump, from a live session.",
				_ => WriteDiag());

			// Save a PNG of part of the frame, read from Terraria's OWN back
			// buffer.
			//
			// Not a screen grab, and that distinction is the only reason this is
			// allowed to exist. An OS-level capture here once returned a picture of
			// Discord sitting on top of the game, and a full-screen one caught a
			// Teams inbox full of real names and addresses. GetBackBufferData reads
			// what THIS PROCESS rendered, so no other window can appear in it by
			// construction rather than by luck.
			//
			// Takes a region - "shot:bottomleft" - because the game's own frame
			// still holds a character name, a world name and any chat on screen,
			// and in multiplayer that chat belongs to somebody else. There is no
			// default region, deliberately: see ShotRegion.TryResolve.
			//
			// This is what makes the interface layer checkable at all. The capture
			// camera draws neither dust nor UI, which is why the motes and the
			// readout could only ever be verified by counters and a human looking.
			r.Register("shot", true,
				"Save a PNG of one region of the frame, from the back buffer (" +
					ShotRegion.Names + ").",
				req => TakeShot(req.Argument));

			// WHAT IS ACTUALLY IN THE WORLD, which nothing else here could ask.
			// Terraria is a tile game and a diag reports whatever a mod chose to
			// count; a picture shows tiles and cannot be compared to anything.
			// This is in the base rather than behind an opt-in because it only
			// READS, and reading a world the harness can already photograph adds
			// no power to anybody.
			//
			// COUNTS BY TYPE rather than a grid of ids. A caller asking "did the
			// placement work" wants to know that 40 of type 812 appeared, not to
			// receive 16,000 numbers and count them - and the grid would be the
			// same information in the form that makes it somebody else's job.
			r.Register("tiles", true,
				"Count tile types in a rectangle, as <x>,<y>,<w>,<h> in tiles (at " +
					"most " + DevMutationArgs.MaxArea + " of them).",
				req => CountTiles(req.Argument));

			// THE OTHER HALF OF THE WORLD. The comment above `tiles` argues that a
			// diag reports whatever a mod chose to count, and that argument is
			// about counting rather than about tiles - it applies unchanged to
			// what is moving around on top of them. A mod that never thought to
			// count its boss cannot be asked whether the boss is there.
			//
			// BOTH SIDES ANSWER, which is deliberate rather than an oversight. A
			// dedicated server owns these arrays and a multiplayer client holds
			// synced copies, so neither is refused - and asking both the same
			// question is how a desync becomes visible instead of theoretical.
			r.Register("entities", true,
				"Count entity types, as <kind> or <kind>,<x>,<y>,<w>,<h> in tiles (" +
					DevMutationArgs.EntityKindNames + ").",
				req => CountEntities(req.Argument));

			// WHICH ONES, rather than how many. A count says the boss is there and
			// cannot say it is at a third of its health - and that second question
			// is what a mod under test is usually about.
			r.Register("find", true,
				"List entities one line each, as <kind> or <kind>,<id> (" +
					DevMutationArgs.EntityKindNames + ").",
				req => FindEntities(req.Argument));

			// SEPARATE FROM `entities` because players have no type. Counting them
			// by one would report "1 distinct type" for any number of people.
			r.Register("players", false,
				"List the connected players, with position and health.",
				_ => ListPlayers());

			// The mod's own, AFTER the seven above. Register throws on a duplicate, so
			// a mod that tries to take one of these names fails at load with a sentence
			// naming the verb - rather than silently replacing a verb the harness needs.
			RegisterCommands(r);

			return r;
		}

		/// <summary>This mod's own verbs. Nothing is registered after this.</summary>
		protected virtual void RegisterCommands(DevCommandRegistry r) { }

		/// <summary>
		/// Write the command list where a harness can read it.
		///
		/// A failure here is reported rather than thrown: a mod that refuses to
		/// LOAD because it could not write a description of itself is a worse
		/// outcome than one whose harness has to fall back to asking. It is not
		/// swallowed either - a silently absent list reads to the harness exactly
		/// like a mod that serves no commands.
		/// </summary>
		private void PublishCommands() {
			try {
				File.WriteAllText(PathFor(Names.Commands), _commands.Publish());
			}
			catch (Exception e) {
				Mod.Logger.Warn("could not publish the dev command list: " + e.Message);
			}
		}

		private static string TriggerName => Names.Trigger;

		private static string ResultName => Names.Result;

		/// <summary>
		/// Records which update hooks actually fire, and in which game state.
		///
		/// Earned the hard way: the first version polled from PostUpdateInput
		/// alone, and a capture request sat unconsumed for 60s against a client
		/// that provably had this code loaded (the .tmod was inflated and checked).
		/// Nothing on disk could distinguish "the hook never fired" from "it fired
		/// and saw no trigger" - so the next attempt would have been a guess. This
		/// file makes that difference observable.
		/// </summary>
		private static string HeartbeatName => Names.Heartbeat;

		/// <summary>
		/// Mid-session state dump. A mod's own self-test driver may well write a
		/// file by this name too, so this one lives in Main.SavePath rather than
		/// the working directory and the two cannot overwrite each other's
		/// answer.
		/// </summary>
		private static string DiagName => Names.Diag;

		/// <summary>
		/// Polling every tick would stat a directory tree 60 times a second
		/// forever, for a feature used a handful of times a session.
		/// </summary>
		private const int PollInterval = 30;

		/// <summary>
		/// Ticks to wait for the PNG to appear AND finish writing. A bounded
		/// guess: nothing documents how long QuickScreenshot takes. Bounded
		/// matters more than correct - when it expires it says so, rather than
		/// leaving a caller waiting on a file that is never coming.
		///
		/// Generous because SEVERAL hooks now drive this: three hooks firing in one
		/// frame decrement it three times, so the real wall-clock window is this
		/// divided by however many are live. Over-waiting costs nothing;
		/// under-waiting reports a failure that did not happen.
		/// </summary>
		private const int SettleTicks = 900;

		private int _tick;
		private List<CaptureFind.CandidateFile> _before;
		private int _waiting;

		/// <summary>
		/// Ticks left waiting for a back-buffer shot to land.
		///
		/// Much shorter than SettleTicks: that one waits on QuickScreenshot, which
		/// queues work and writes a file at its leisure. A shot happens on the
		/// very next drawn frame, so anything beyond a couple of seconds means
		/// Main.OnPostDraw is not firing at all - which is a real answer, and one
		/// worth saying rather than waiting out a 900-tick timeout to reach.
		/// </summary>
		private int _shotWaiting;

		private const int ShotSettleTicks = 120;

		private readonly SortedSet<string> _hooksSeen = new SortedSet<string>(StringComparer.Ordinal);
		private bool _menuState;
		private bool _heartbeatStale = true;

		/// <summary>
		/// Last observed trigger presence. Tracked because the heartbeat REPORTS
		/// trigger-exists, and a file whose freshness does not depend on the value
		/// it reports will report a stale one. It did: trigger-exists:False was
		/// written 31s before the trigger existed, then read as though it were
		/// current - and named a SavePath mismatch that did not exist.
		/// </summary>
		private bool _triggerState;

		private long _polls;
		private DateTime _lastWrite = DateTime.MinValue;

		/// <summary>
		/// Rewrite this often even when nothing changed. A file written only on
		/// change is a state dump, not a heartbeat: a frozen one cannot be told
		/// from a dead process, which is precisely the question it exists to
		/// answer. Wall-clock rather than a poll count because the poll RATE
		/// depends on how many of the three hooks tick, which is what we are
		/// trying to find out.
		/// </summary>
		private static readonly TimeSpan HeartbeatMaxAge = TimeSpan.FromSeconds(20);

		/// <summary>
		/// When a world became live on THIS side, from the engine's own world
		/// lifecycle hook. Commands are refused until it has been live this long.
		///
		/// This used to be inferred from a Main.gameMenu transition, which is a
		/// client-only proxy for the thing actually meant: a dedicated server has
		/// no menu, so no transition ever occurs and the settle comparison could
		/// never come true there. ModSystem.OnWorldLoad is the real signal and it
		/// fires on every side - verified out of the assembly, not recalled:
		///
		///   SystemLoader::OnWorldLoad  &lt;- Terraria.IO.WorldFile.LoadWorld
		///   SystemLoader::OnWorldLoad  &lt;- Terraria.Netplay.InnerClientLoop
		///
		/// The first covers singleplayer and the dedicated server, which load a
		/// world file; the second covers a joining client, which never does.
		///
		/// Earned by crashing the game. A trigger armed at the main menu sat
		/// unconsumed for seven minutes - these hooks do not poll there - and then
		/// fired 281ms after world entry, the first instant they did poll. The
		/// engine died in Terraria.Graphics.Capture.CaptureCamera.DrawTick ->
		/// TileDrawing.PrepareForAreaDrawing with IndexOutOfRangeException. Waiting
		/// for the menu to clear is not enough: the least safe moment to capture is
		/// the exact moment capture first becomes possible.
		/// </summary>
		private DateTime _enteredWorld = DateTime.MaxValue;

		private static readonly TimeSpan WorldSettle = TimeSpan.FromSeconds(3);

		/// <summary>
		/// True once a poll has seen NO trigger while settled in-world - i.e. a
		/// clean baseline exists, so a trigger seen later is one that ARRIVED.
		///
		/// Acting on presence rather than arrival is what turned a menu-armed
		/// request into a landmine that detonated on world entry. Presence cannot
		/// distinguish "the user just asked" from "this was lying here before we
		/// were able to look".
		/// </summary>
		private bool _armed;

		// Four hooks, because which of them ticks depends on game state and that
		// dependence is undocumented. All are Public+Virtual on ModSystem -
		// verified against tModLoader.dll metadata via tools/apispike, not recalled.
		// Whichever one fires, the poll runs.
		//
		// PostUpdateWorld is the one that matters on a dedicated server, where the
		// two input/UI hooks have nothing to drive them. Its path, read out of the
		// assembly:
		//
		//   SystemLoader::PostUpdateWorld      <- WorldGen.UpdateWorld
		//   SystemLoader::PostUpdateEverything <- Main.DoUpdateInWorld
		//                                      <- Main.DoUpdate <- Main.Update
		//
		// hooks-seen in the heartbeat reports which ones actually fired, so this
		// stays a measurement rather than an assumption.
		public override void PostUpdateInput() => Tick("PostUpdateInput");

		public override void UpdateUI(GameTime gameTime) => Tick("UpdateUI");

		public override void PostUpdateEverything() => Tick("PostUpdateEverything");

		public override void PostUpdateWorld() => Tick("PostUpdateWorld");

		/// <summary>
		/// A world is live. Stamp it and drop the arming baseline: a trigger left
		/// on disk from before this world existed must not be served to it.
		/// </summary>
		public override void OnWorldLoad() {
			_enteredWorld = DateTime.UtcNow;
			_armed = false;
			_heartbeatStale = true;
		}

		public override void OnWorldUnload() {
			// MaxValue rather than a second flag: it keeps the settle comparison
			// false without another piece of state to hold in step.
			_enteredWorld = DateTime.MaxValue;
			_armed = false;
			_heartbeatStale = true;
		}

		private void Tick(string hook) {
			Observe(hook);

			if (_waiting > 0) {
				Settle();
				return;
			}

			if (_shotWaiting > 0) {
				SettleShot();
				return;
			}

			if (++_tick < PollInterval) {
				return;
			}

			_tick = 0;
			_polls++;

			// One File.Exists per poll, shared by the heartbeat and the trigger
			// check, so the two can never disagree about what was on disk.
			string trigger = PathFor(TriggerName);
			bool present;
			try {
				present = File.Exists(trigger);
			}
			catch (Exception e) {
				Report("ERROR: could not stat the trigger: " + e.Message);
				return;
			}

			if (present != _triggerState) {
				_triggerState = present;
				_heartbeatStale = true;
			}

			// A world has to have been live a while before any command is served.
			// For capture this is a safety rule and cannot be enforced with a
			// try/catch around QuickScreenshot: that call only QUEUES the work,
			// which runs later on the draw tick, so the IndexOutOfRangeException
			// surfaced in Main.DoDraw - outside our stack, uncatchable, and fatal.
			// Timing is the only lever we have.
			//
			// The menu check that used to live here has moved into the capture
			// path, where it belongs: "the world is drawable" is a requirement of
			// taking a screenshot, not of answering a question about world state.
			// Conflating the two is what made this gate unsatisfiable on a server.
			bool ready = DateTime.UtcNow - _enteredWorld >= WorldSettle;
			if (!ready) {
				// No clean baseline while not ready, so nothing observed now counts
				// as an arrival.
				_armed = false;
				WriteHeartbeatIfStale(trigger, present, false);
				return;
			}

			WriteHeartbeatIfStale(trigger, present, true);

			if (!present) {
				_armed = true;
				return;
			}

			if (!_armed) {
				// Present on our very first settled look, so it predates us. Clear
				// it rather than serve it - this is the landmine that crashed the
				// engine - and say so, instead of failing silently.
				try {
					File.Delete(trigger);
				}
				catch {
					// Reported below regardless; a stuck file would re-enter here.
				}

				_armed = true;
				Report("IGNORED: a trigger was already on disk when this world " +
					"became capturable, so it was armed before the game could poll. " +
					"Serving one at that moment crashed the engine once. Re-run " +
					"tools/capture.sh now that a world is loaded.");
				return;
			}

			// Read the command BEFORE deleting, obviously, but delete before acting
			// on it: a trigger still present after a throw would re-fire on every
			// poll forever.
			string raw = null;
			try {
				raw = File.ReadAllText(trigger);
			}
			catch {
				// Unreadable is not fatal - Parse(null) means the historical
				// bare-trigger behaviour, a capture.
			}

			// ADDRESSED TO SOMEBODY ELSE? Leave it exactly where it is.
			//
			// This has to happen BEFORE the delete below, and that ordering is the
			// whole mechanism: two clients share one Main.SavePath and therefore
			// one trigger file, so a client that consumed a request meant for the
			// other would both answer as the wrong player AND destroy the request
			// before its recipient ever polled. Not deleting is what lets the
			// intended client find it on its own next poll.
			//
			// Silent on purpose. The other client is going to see this trigger on
			// every poll until its owner takes it, and reporting each time would
			// bury the actual answer under its own noise.
			DevRequest request = DevCommands.Parse(raw);
			if (!request.IsFor(LocalAddress)) {
				return;
			}

			try {
				File.Delete(trigger);
			}
			catch (Exception e) {
				Report("ERROR: could not clear the trigger: " + e.Message);
				return;
			}

			Dispatch(request, raw);
		}

		/// <summary>
		/// Hand the request to whatever serves it, or say why nothing does.
		///
		/// Every exit here is loud, and that has been the rule since the beginning:
		/// an unrecognised command that silently captured would be
		/// indistinguishable from the command working. What is new is that the
		/// three ways to fail are now told apart. They used to collapse into one
		/// "unknown trigger command" sentence listing twelve verbs by hand, next to
		/// a switch listing the same twelve - two copies that were already one
		/// command apart once.
		/// </summary>
		private void Dispatch(DevRequest request, string raw) {
			if (request.IsMalformed) {
				Report("ERROR: could not read the trigger \"" +
					(raw ?? string.Empty).Trim() +
					"\" - write a command as name, name@player, name:argument or " +
					"name:argument@player, with no empty half (this mod serves " +
					_commands.Names + ")");
				return;
			}

			if (!_commands.TryResolve(request.Verb, out DevCommandEntry command)) {
				Report("ERROR: this mod does not serve \"" + request.Verb +
					"\" - it serves " + _commands.Names);
				return;
			}

			// AN ARGUMENT NOTHING READS IS REFUSED RATHER THAN DROPPED.
			//
			// Eleven of these commands used to parse an argument and discard it in
			// silence, so "place:mycelium" reported success having ignored the word
			// entirely - and the only thing standing between a caller and that
			// confusion was the harness keeping its own list of which commands read
			// one. Now that the mod publishes the answer, it can also enforce it,
			// which puts the check on the side that actually knows.
			if (request.Argument != null && !command.TakesArgument) {
				Report("REFUSED: \"" + command.Name + "\" takes no argument, and \"" +
					request.Argument + "\" would have been ignored. " +
					command.Summary);
				return;
			}

			if (request.Argument == null && command.TakesArgument) {
				Report("REFUSED: \"" + command.Name + "\" needs an argument, as " +
					command.Name + ":<argument>. " + command.Summary);
				return;
			}

			command.Handler(request);
		}

		/// <summary>
		/// Both refusals are capture's own requirements, which is why they sit here
		/// and not in the shared ready gate.
		/// </summary>
		private void CaptureNow() {
			if (Main.dedServ) {
				Report("REFUSED: capture on a dedicated server, which has no " +
					"graphics device to draw with. A server can answer diag " +
					"and nothing else; ask the client for pictures.");
				return;
			}

			if (Main.gameMenu) {
				Report("REFUSED: at the main menu, so there is no world to " +
					"draw. Load a world and try again.");
				return;
			}

			Begin();
		}

		/// <summary>
		/// Write this side's state where the harness reads it.
		///
		/// The FILE, the write and the failure report are the responder's; only the
		/// content is the mod's. A mod that could not be asked for a diag would
		/// satisfy this class and fail MOD_CONTRACT, which is why CollectDiag is
		/// abstract rather than defaulted to an empty dump.
		/// </summary>
		private void WriteDiag() {
			try {
				File.WriteAllText(AnswerPathFor(DiagName), CollectDiag());
				Report("DIAG: " + AnswerPathFor(DiagName));
			}
			catch (Exception e) {
				Report("ERROR: diag failed: " + e);
			}
		}

		/// <summary>
		/// Count the tile types in a rectangle.
		///
		/// CLAMPED TO THE WORLD RATHER THAN REFUSED AT THE EDGE. A caller asking
		/// for a hundred tiles around something near the border means the tiles
		/// that exist, and refusing the whole query for the part that does not
		/// would make every edge case a special case for the caller. The reply
		/// says how many were actually looked at, so a clamp is visible.
		///
		/// EMPTY IS A COUNT. "nothing there" and "the query did not run" are
		/// different answers, and reporting the first as an empty string would
		/// make them identical.
		/// </summary>
		private void CountTiles(string argument) {
			if (!DevMutationArgs.TryResolveArea(argument, out int x, out int y,
					out int width, out int height, out string problem)) {
				Report("REFUSED: " + problem);
				return;
			}

			// `Main.maxTilesX` rather than a null check on `Main.tile`: the
			// tilemap is a STRUCT in 1.4, so comparing it to null does not
			// compile at all. A world that has not loaded has no width, which is
			// the same question asked of something that can answer it.
			if (Main.maxTilesX <= 0 || Main.maxTilesY <= 0) {
				Report("REFUSED: this side has no world loaded to look at");
				return;
			}

			int right = Math.Min(x + width, Main.maxTilesX);
			int bottom = Math.Min(y + height, Main.maxTilesY);
			var counts = new SortedDictionary<int, int>();
			int looked = 0;
			int empty = 0;

			for (int i = Math.Max(0, x); i < right; i++) {
				for (int j = Math.Max(0, y); j < bottom; j++) {
					looked++;
					Tile tile = Main.tile[i, j];

					if (!tile.HasTile) {
						empty++;
						continue;
					}

					int type = tile.TileType;
					counts[type] = counts.TryGetValue(type, out int seen) ? seen + 1 : 1;
				}
			}

			var sb = new System.Text.StringBuilder();
			sb.Append("OK: looked at ").Append(looked)
				.Append(" tile(s) of the ").Append(width * height)
				.Append(" asked for, ").Append(empty).Append(" empty, ")
				.Append(counts.Count).Append(" distinct type(s)");

			foreach (KeyValuePair<int, int> pair in counts) {
				sb.Append("\n  id=").Append(pair.Key).Append(" count=").Append(pair.Value);
			}

			Report(sb.ToString());
		}

		/// <summary>
		/// Count the entity types of one kind, optionally only inside a
		/// rectangle.
		///
		/// THE RECTANGLE IS A FILTER, NOT A BUDGET. Every one of these walks the
		/// whole fixed-size array whatever it was asked - 200 NPC slots, 400 item
		/// slots - because that is where the answer lives. Naming a rectangle
		/// removes entities from the count; it does not remove work, which is why
		/// DevMutationArgs caps the tile query's rectangle and not this one.
		///
		/// THE NAME IS REPORTED BESIDE THE ID because these id spaces are opaque
		/// and three of them are in play. "id=4 count=1" is a fact the reader
		/// then has to go look up; "id=4 count=1 name=Eye of Cthulhu" is the
		/// answer they wanted, and getting it wrong is visible instantly rather
		/// than after a wiki search against the wrong list.
		/// </summary>
		private void CountEntities(string argument) {
			if (!DevMutationArgs.TryResolveEntityQuery(argument, out string kind,
					out bool everywhere, out int x, out int y, out int width,
					out int height, out string problem)) {
				Report("REFUSED: " + problem);
				return;
			}

			// Same question as CountTiles asks, for the same reason: a side with
			// no world has empty arrays, and reporting "0 active" there would be
			// a confident answer to a question that was never asked of a world.
			if (Main.maxTilesX <= 0 || Main.maxTilesY <= 0) {
				Report("REFUSED: this side has no world loaded to look at");
				return;
			}

			// Tile coordinates in, world units out - the entities are positioned
			// in world units, and 1 tile is 16 of them. Doing it here rather than
			// in the rules file keeps that constant next to the arrays it
			// describes.
			float left = x * 16f;
			float top = y * 16f;
			float right = (x + width) * 16f;
			float bottom = (y + height) * 16f;

			var counts = new SortedDictionary<int, int>();
			ArrayFor(kind, out Entity[] slots, out int used,
				out Func<Entity, int> typeOf, out Func<int, string> nameOf);

			if (slots == null) {
				Report("REFUSED: this side has no " + kind + " array to look at");
				return;
			}

			int active = 0;
			int looked = Math.Min(used, slots.Length);

			for (int i = 0; i < looked; i++) {
				Entity entity = slots[i];

				if (entity == null || !entity.active) {
					continue;
				}

				if (!everywhere) {
					Vector2 at = entity.Center;
					if (at.X < left || at.X >= right || at.Y < top || at.Y >= bottom) {
						continue;
					}
				}

				active++;
				int type = typeOf(entity);
				counts[type] = counts.TryGetValue(type, out int seen) ? seen + 1 : 1;
			}

			var sb = new System.Text.StringBuilder();
			sb.Append("OK: looked at ").Append(looked).Append(' ').Append(kind)
				.Append(" slot(s)");

			if (!everywhere) {
				sb.Append(" in ").Append(width).Append('x').Append(height)
					.Append(" tiles at ").Append(x).Append(',').Append(y);
			}

			sb.Append(", ").Append(active).Append(" active, ")
				.Append(counts.Count).Append(" distinct type(s)");

			foreach (KeyValuePair<int, int> pair in counts) {
				sb.Append("\n  id=").Append(pair.Key)
					.Append(" count=").Append(pair.Value)
					.Append(" name=").Append(Named(nameOf, pair.Key));
			}

			Report(sb.ToString());
		}

		/// <summary>
		/// Which array a kind means, and how to read a type and a name out of it.
		///
		/// Shared by the counting query and the detail query so the two cannot
		/// come to disagree about which array `item` refers to - a `find` reading
		/// projectiles while `entities` counted items would be a discrepancy with
		/// no visible cause.
		/// </summary>
		private static void ArrayFor(string kind, out Entity[] slots, out int used,
				out Func<Entity, int> typeOf, out Func<int, string> nameOf) {
			switch (kind) {
				case DevMutationArgs.EntityNpc:
					slots = Main.npc;
					used = Main.maxNPCs;
					typeOf = e => ((NPC)e).type;
					nameOf = Lang.GetNPCNameValue;
					return;

				case DevMutationArgs.EntityItem:
					slots = Main.item;
					used = Main.maxItems;
					typeOf = e => ((Item)e).type;
					nameOf = Lang.GetItemNameValue;
					return;

				default:
					slots = Main.projectile;
					used = Main.maxProjectiles;
					typeOf = e => ((Projectile)e).type;

					// The odd one out: this returns a LocalizedText where the
					// other two return a string.
					nameOf = type => Lang.GetProjectileName(type).Value;
					return;
			}
		}

		/// <summary>The most entities one `find` reports one line each for.
		///
		/// A cap rather than everything: 1000 projectile slots during a boss
		/// fight is a reply nobody reads and a file the harness then has to carry
		/// through a trigger round trip. The reply says how many MATCHED as well
		/// as how many are shown, so the cap is visible rather than looking like
		/// the whole answer.</summary>
		private const int MaxFound = 64;

		/// <summary>
		/// One line per entity, rather than a count of them.
		///
		/// WHAT `entities` CANNOT ANSWER. A count says a boss is there; it cannot
		/// say the boss is at a third of its health and drifting off-screen, and
		/// those are the questions a mod under test is actually about. The detail
		/// differs by kind because the kinds differ: an NPC has health, a dropped
		/// item has a stack size, a projectile belongs to somebody.
		/// </summary>
		private void FindEntities(string argument) {
			if (!DevMutationArgs.TryResolveFind(argument, out string kind,
					out bool anyType, out int type, out string problem)) {
				Report("REFUSED: " + problem);
				return;
			}

			if (Main.maxTilesX <= 0 || Main.maxTilesY <= 0) {
				Report("REFUSED: this side has no world loaded to look at");
				return;
			}

			ArrayFor(kind, out Entity[] slots, out int used,
				out Func<Entity, int> typeOf, out Func<int, string> nameOf);

			if (slots == null) {
				Report("REFUSED: this side has no " + kind + " array to look at");
				return;
			}

			int matched = 0;
			int looked = Math.Min(used, slots.Length);
			var lines = new List<string>();

			for (int i = 0; i < looked; i++) {
				Entity entity = slots[i];

				if (entity == null || !entity.active) {
					continue;
				}

				int found = typeOf(entity);
				if (!anyType && found != type) {
					continue;
				}

				matched++;
				if (lines.Count >= MaxFound) {
					continue;
				}

				var line = new System.Text.StringBuilder();
				line.Append("  slot=").Append(i)
					.Append(" id=").Append(found)
					.Append(" name=").Append(Named(nameOf, found))
					.Append(" tile=").Append((int)(entity.Center.X / 16f))
					.Append(',').Append((int)(entity.Center.Y / 16f));

				// Tile coordinates, not world units: every wiki coordinate and
				// the game's own map are in tiles, and `teleport` already takes
				// them - so a position read here can be handed straight back.
				if (entity is NPC npc) {
					line.Append(" life=").Append(npc.life).Append('/').Append(npc.lifeMax);
				}
				else if (entity is Item item) {
					line.Append(" stack=").Append(item.stack);
				}
				else if (entity is Projectile shot) {
					line.Append(" owner=").Append(shot.owner);
				}

				lines.Add(line.ToString());
			}

			var sb = new System.Text.StringBuilder();
			sb.Append("OK: ").Append(matched).Append(' ').Append(kind)
				.Append(anyType ? "" : " of type " + type)
				.Append(" active, showing ").Append(lines.Count)
				.Append(" of ").Append(looked).Append(" slot(s)");

			foreach (string line in lines) {
				sb.Append('\n').Append(line);
			}

			Report(sb.ToString());
		}

		/// <summary>
		/// Who is connected, and where.
		///
		/// NOT part of `entities`, deliberately. That verb counts by TYPE, and
		/// players have no type - they have names. Bending them into a count
		/// would produce "1 distinct type" for any number of people, which is
		/// true and useless.
		///
		/// Both sides answer. A server sees everyone; a client sees its own
		/// synced view, and the two disagreeing is worth being able to see.
		/// </summary>
		private void ListPlayers() {
			if (Main.player == null) {
				Report("REFUSED: this side has no player array to look at");
				return;
			}

			int looked = Math.Min(Main.maxPlayers, Main.player.Length);
			var lines = new List<string>();

			for (int i = 0; i < looked; i++) {
				Player player = Main.player[i];

				if (player == null || !player.active) {
					continue;
				}

				lines.Add("  slot=" + i +
					" name=" + (string.IsNullOrEmpty(player.name) ? "(unnamed)" : player.name) +
					" tile=" + (int)(player.Center.X / 16f) + "," +
						(int)(player.Bottom.Y / 16f) +
					" life=" + player.statLife + "/" + player.statLifeMax);
			}

			var sb = new System.Text.StringBuilder();
			sb.Append("OK: looked at ").Append(looked).Append(" player slot(s), ")
				.Append(lines.Count).Append(" active");

			foreach (string line in lines) {
				sb.Append('\n').Append(line);
			}

			Report(sb.ToString());
		}

		/// <summary>
		/// A display name, or the reason there is not one.
		///
		/// Lang goes through the localisation tables, and a modded id whose
		/// entry never loaded throws rather than returning empty. Letting that
		/// escape would lose the whole count - every id in it, including the ones
		/// that resolved - over one unnameable entry, so the id stays and the
		/// name says what happened.
		/// </summary>
		private static string Named(Func<int, string> nameOf, int type) {
			try {
				string name = nameOf(type);
				return string.IsNullOrWhiteSpace(name) ? "(unnamed)" : name;
			}
			catch (Exception e) {
				return "(no name: " + e.GetType().Name + ")";
			}
		}

		/// <summary>The body of &lt;mod&gt;-diag.txt. See docs/MOD_CONTRACT.md for the
		/// grammar the harness parses.</summary>
		protected abstract string CollectDiag();

		/// <summary>
		/// Ask FrameShot for a region of the frame.
		///
		/// The capture itself cannot happen here - this runs from an update hook
		/// and the back buffer is only complete after the frame is drawn - so the
		/// request is armed and SettleShot reports whatever comes back.
		/// </summary>
		private void TakeShot(string region) {
			FrameShot.LastPath = null;
			FrameShot.LastError = null;

			// FrameShot.Capture combines this with Main.SavePath and applies
			// ForSide(name, Main.dedServ) itself - a no-op here, since a shot
			// can only ever be taken client-side (Request refuses on
			// Main.dedServ above), so that second call never sees
			// dedicatedServer true and never re-suffixes what AnswerName
			// already produced.
			if (!FrameShot.Request(region, AnswerName(Names.Shot), out string problem)) {
				Report("REFUSED: " + problem);
				return;
			}

			_shotWaiting = ShotSettleTicks;
		}

		/// <summary>
		/// Report a shot once it lands, or say plainly that it never did.
		///
		/// Bounded rather than open-ended for the same reason the capture settle
		/// is: a caller waiting on a file that is never coming, with nothing on
		/// disk explaining why, is the failure this whole diagnostic layer exists
		/// to remove.
		/// </summary>
		private void SettleShot() {
			_shotWaiting--;

			if (FrameShot.LastPath != null) {
				_shotWaiting = 0;
				Report("SHOT: " + FrameShot.LastPath);
				return;
			}

			if (FrameShot.LastError != null) {
				_shotWaiting = 0;
				Report("ERROR: the shot failed: " + FrameShot.LastError);
				return;
			}

			if (_shotWaiting <= 0) {
				Report("ERROR: no shot appeared within " + ShotSettleTicks +
					" ticks - Main.OnPostDraw is not firing on this side.");
			}
		}

		/// <summary>
		/// Note a hook firing, and note when the game state changes. Both make the
		/// heartbeat worth rewriting; nothing else does.
		/// </summary>
		private void Observe(string hook) {
			if (_hooksSeen.Add(hook)) {
				_heartbeatStale = true;
			}

			// Reported, not acted on. World entry and exit are stamped by
			// OnWorldLoad/OnWorldUnload now; this only keeps the heartbeat honest
			// about a value it prints.
			if (Main.gameMenu != _menuState) {
				_menuState = Main.gameMenu;
				_heartbeatStale = true;
			}
		}

		/// <summary>
		/// Written when a reported value changes, and at least every
		/// HeartbeatMaxAge regardless. Not a 60Hz disk writer, but not a
		/// write-once state dump either - the periodic rewrite is what lets a
		/// reader tell "nothing changed" from "the process is gone".
		/// </summary>
		private void WriteHeartbeatIfStale(string trigger, bool present, bool ready) {
			if (!_heartbeatStale && DateTime.UtcNow - _lastWrite < HeartbeatMaxAge) {
				return;
			}

			_heartbeatStale = false;
			_lastWrite = DateTime.UtcNow;

			// trigger-exists is the load-bearing line: it answers, from INSIDE the
			// process, whether the directory this polls is the directory the shell
			// script writes to. A path mismatch is otherwise indistinguishable from
			// a hook that never fires. It is passed in rather than recomputed so it
			// is the SAME observation the trigger check acts on.
			//
			// written/polls exist so a reader can see liveness in the CONTENT, not
			// just infer it from an mtime the reader might forget to check. The
			// first version of this file had neither, and its frozen trigger-exists
			// was read as current 31 seconds after the fact.
			// world-ready and capture-ready are separate lines because they are now
			// separate questions. A dedicated server is world-ready and can never
			// be capture-ready, and a reader that saw only the old single flag
			// would conclude the server was not polling at all.
			string body =
				"hooks-seen: " + string.Join(",", _hooksSeen) + "\n" +
				"gameMenu: " + Main.gameMenu + "\n" +
				"dedServ: " + Main.dedServ + "\n" +
				"savepath: " + Main.SavePath + "\n" +
				"trigger-path: " + trigger + "\n" +
				"trigger-exists: " + present + "\n" +
				"world-ready: " + ready + "\n" +
				"capture-ready: " + (ready && !Main.dedServ && !Main.gameMenu) + "\n" +
				"armed: " + _armed + "\n" +
				"polls: " + _polls + "\n" +
				"written: " + DateTime.UtcNow.ToString("HH:mm:ss") + "Z\n";

			try {
				File.WriteAllText(AnswerPathFor(HeartbeatName), body);
			}
			catch {
				// Nothing useful left to do - the report channel itself is gone.
			}
		}

		/// <summary>
		/// Why the current view cannot safely be captured, or null if it can.
		///
		/// QuickScreenshot builds its capture rectangle from the CURRENT VIEW and
		/// hands it over with no clamping. That is not a guess - it is what the
		/// method does, read out of the assembly with tools/apispike --calls:
		///
		///   Main::get_ViewPosition -> Utils::ToTileCoordinates
		///   Main::get_ViewPosition + Main::get_ViewSize -> Utils::ToTileCoordinates
		///   newobj Rectangle::.ctor
		///   CaptureInterface::StartCamera
		///
		/// So an unready view produces an out-of-world tile rect, and
		/// TileDrawing.PrepareForAreaDrawing indexes off the end of the tile array
		/// and kills the process. That is the crash. Computing the same rectangle
		/// from the same public inputs is the only way to refuse it BEFORE the
		/// engine chokes on it - the throw happens later, on the draw tick, where
		/// no try/catch of ours can reach.
		///
		/// The margin exists because the camera expands the area for edge blending,
		/// so a rect that merely touches the boundary is not safe either.
		/// </summary>
		/// <summary>
		/// Reads the view, then defers to CaptureBounds - which is where the rule
		/// lives and where it is tested. Deliberately NOT a second copy of the
		/// comparisons: this feature already shipped a test that passed while the
		/// real code was broken, because the test exercised a transcription of the
		/// predicate rather than the predicate.
		/// </summary>
		private static string ViewRectProblem() {
			Point tl = Main.ViewPosition.ToTileCoordinates();
			Point br = (Main.ViewPosition + Main.ViewSize).ToTileCoordinates();
			return CaptureBounds.Problem(tl.X, tl.Y, br.X, br.Y, Main.maxTilesX, Main.maxTilesY);
		}

		private void Begin() {
			// Checked before the call, not around it. QuickScreenshot only queues
			// the capture: the work runs later on the draw tick, so the throw lands
			// in Main.DoDraw, outside this stack, uncatchable and fatal. The catch
			// below covers only what the QUEUEING can throw.
			string problem = ViewRectProblem();
			if (problem != null) {
				Report("REFUSED: " + problem + ". Capturing this would crash the " +
					"engine in TileDrawing.PrepareForAreaDrawing. Move away from the " +
					"world edge, or wait for the view to settle, and try again.");
				return;
			}

			try {
				_before = List();
				CaptureInterface.QuickScreenshot();
				_waiting = SettleTicks;
			}
			catch (Exception e) {
				_waiting = 0;
				Report("ERROR: QuickScreenshot threw while queueing: " + e);
			}
		}

		private void Settle() {
			_waiting--;

			string found;
			try {
				found = CaptureFind.PickNew(_before, List());
			}
			catch (Exception e) {
				_waiting = 0;
				Report("ERROR: listing failed: " + e.Message);
				return;
			}

			if (found != null) {
				_waiting = 0;
				Report("PNG: " + found);
				return;
			}

			if (_waiting <= 0) {
				// Loud. Silence here would leave the caller waiting on a file that
				// is never coming, with nothing saying why.
				Report("ERROR: no new PNG appeared under " + Main.SavePath +
					" - QuickScreenshot ran but produced nothing findable");
			}
		}

		/// <summary>
		/// Recursive, because the write location is not documented anywhere in the
		/// assembly and there was no existing screenshots directory to observe.
		/// Searching the tree is what makes a wrong guess impossible.
		/// </summary>
		private static List<CaptureFind.CandidateFile> List() {
			var files = new List<CaptureFind.CandidateFile>();
			if (!Directory.Exists(Main.SavePath)) {
				return files;
			}

			foreach (string p in Directory.EnumerateFiles(
					Main.SavePath, "*.png", SearchOption.AllDirectories)) {
				long len;
				try {
					len = new FileInfo(p).Length;
				}
				catch {
					// A file mid-write can refuse a stat. Treat it as unfinished
					// rather than crashing the poll.
					continue;
				}

				files.Add(new CaptureFind.CandidateFile { Path = p, Length = len });
			}

			return files;
		}

		/// <summary>
		/// This side's player name, or null where there is no local player.
		///
		/// A dedicated server has no LocalPlayer worth speaking of, so it never
		/// has one of these. It is no longer unaddressable because of that - see
		/// LocalAddress - but the two are still separate questions, and this one
		/// is only ever about a character.
		/// </summary>
		private static string LocalPlayerName {
			get {
				if (Main.dedServ) {
					return null;
				}

				Player p = Main.LocalPlayer;
				return p == null || string.IsNullOrEmpty(p.name) ? null : p.name;
			}
		}

		private static string _serverAddress;
		private static bool _serverAddressRead;

		/// <summary>
		/// This dedicated server's address, read ONCE.
		///
		/// Cached because Tick runs from four update hooks and would otherwise
		/// walk the process command line sixty times a second to learn a value
		/// that cannot change. A separate `read` flag rather than a null check,
		/// because null is a legitimate answer here - a server with no -port -
		/// and a null check would re-scan on every poll for exactly the case
		/// where scanning never helps.
		/// </summary>
		private static string ServerAddress {
			get {
				if (!_serverAddressRead) {
					_serverAddress = DevArtifacts.ServerAddress(Environment.GetCommandLineArgs());
					_serverAddressRead = true;
				}

				return _serverAddress;
			}
		}

		/// <summary>
		/// What a request's target is compared against on THIS side.
		///
		/// A client's address is its character name. A dedicated server's is its
		/// port, which is the whole of the change: before it, a server matched no
		/// target at all, so every request on its side of the trigger had to be
		/// untargeted - and two harness sessions each driving their own server out
		/// of one Main.SavePath were indistinguishable on disk. Whichever polled
		/// first took the request.
		///
		/// Null on a client until a character exists, which is unchanged and
		/// correct: a client at the menu has no name to be addressed by, and
		/// IsFor(null) declines every targeted request rather than guessing.
		/// </summary>
		private static string LocalAddress => Main.dedServ ? ServerAddress : LocalPlayerName;

		/// <summary>
		/// Full path to one of this side's SHARED artifacts - the trigger and
		/// the published command list. Both must stay addressed by the SAME
		/// name regardless of which client is polling: the trigger is how one
		/// client's request reaches whichever client it names (see the
		/// IsFor(LocalAddress) check in Tick), and a client-specific trigger
		/// path would make that addressing meaningless, since nobody would be
		/// polling the name a request actually landed under. Deliberately does
		/// NOT take a player token - see AnswerPathFor for the files that do.
		/// </summary>
		private static string PathFor(string name) {
			return Path.Combine(Main.SavePath, DevArtifacts.ForSide(name, Main.dedServ));
		}

		/// <summary>
		/// What names this side's answers, or null when nothing does.
		///
		/// A client's is its player token - the slug-plus-digest form, which is
		/// NOT its address. A dedicated server's is its address verbatim: the
		/// port is already a filename fragment and digesting it would invent a
		/// second spelling of one identity for the two languages to disagree
		/// about.
		///
		/// Null on a dedicated server with no -port, and on a client before a
		/// character exists. ForSide's null-token branch then returns the plain
		/// sided name unchanged, so a client at the menu still writes the
		/// unsuffixed heartbeat the harness reads before any world is loaded,
		/// and a server launched by hand writes exactly what it always did.
		/// </summary>
		private static string AnswerTokenOrNull =>
			Main.dedServ ? ServerAddress : DevArtifacts.PlayerToken(LocalPlayerName);

		/// <summary>
		/// This side's answer to a request, named for THIS addressee:
		/// biomancy-diag-n43n-003f.txt rather than biomancy-diag.txt, and
		/// biomancy-diag-port7810-server.txt rather than
		/// biomancy-diag-server.txt. Two clients - or, now, two servers -
		/// sharing one Main.SavePath would otherwise overwrite each other's
		/// diag, heartbeat and capture answers exactly the way DevArtifacts'
		/// own docs describe for sides.
		/// </summary>
		private static string AnswerPathFor(string name) {
			return Path.Combine(Main.SavePath, AnswerName(name));
		}

		/// <summary>
		/// The FILENAME (not the full path) an answer would be written under,
		/// sided and tokened. FrameShot writes its own file, on its own
		/// schedule, from an event this class does not control - so what
		/// crosses into FrameShot.Request is the finished filename rather than
		/// a path this class could combine itself.
		/// </summary>
		private static string AnswerName(string name) {
			return DevArtifacts.ForSide(name, Main.dedServ, AnswerTokenOrNull);
		}

		protected static void Report(string line) {
			try {
				File.WriteAllText(AnswerPathFor(ResultName), line + "\n");
			}
			catch {
				// Nothing useful left to do - the report channel itself is gone.
			}
		}
	}
}
