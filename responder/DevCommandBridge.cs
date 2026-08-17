using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Xna.Framework;
using Terraria;
using Terraria.ModLoader;

namespace TModLoaderMcp.DevBridge
{
	/// <summary>
	/// Runs the commands a mod ALREADY REGISTERED, and hands back what they say.
	///
	/// THE PROBLEM THIS SOLVES. Everything else here has to be anticipated: a
	/// question nobody wrote a verb for costs a C# edit, a rebuild and a
	/// relaunch. Other harnesses answer that with reflection or an eval tool -
	/// `reflect_invoke`, `execute_code` - which buys unlimited reach and throws
	/// away the property this whole design rests on: that everything is
	/// published, typed and refusable.
	///
	/// A mod's own `ModCommand`s are the middle. The mod DECIDED they exist,
	/// gave each one a name, a usage line and a description, and most mods
	/// already have the debug commands somebody would otherwise be adding a verb
	/// for. Running one is not new power - it is the power a developer already
	/// has by typing into chat - and the set is enumerable, so an unknown one is
	/// refused by NAMING the ones that exist rather than by silence.
	///
	/// OPT-IN, and separate from DevMutations. Wanting a mod's debug commands is
	/// not the same as wanting to spawn NPCs, and a consumer should be able to
	/// have either without the other:
	///
	///     protected override void RegisterCommands(DevCommandRegistry r) =>
	///         DevCommandBridge.RegisterInto(r, Report);
	///
	/// THE PAYLOAD SPLITS ON THE FIRST COLON AND STRIPS `@` FIRST, so
	/// `command:gimme 5` arrives as the argument "gimme 5" and a command
	/// containing an `@` cannot be sent this way. That is the trigger grammar
	/// rather than a limit of this file; see DevCommands.Parse.
	/// </summary>
	public static class DevCommandBridge
	{
		/// <summary>How many reply lines one command may produce before the rest
		/// are counted instead of quoted. A `/help` can run to hundreds.</summary>
		public const int MaxReplyLines = 40;

		public static void RegisterInto(DevCommandRegistry r, Action<string> report) {
			if (r == null) {
				throw new ArgumentNullException(nameof(r));
			}

			if (report == null) {
				throw new ArgumentNullException(nameof(report),
					"the command bridge answers through Report; without it every " +
					"command would run and say nothing");
			}

			r.Register("command", true,
				"Run one of this install's registered ModCommands and return what " +
					"it printed, as <name> or <name> <args>.",
				req => Run(req.Argument, report));

			r.Register("commandlist", false,
				"List the ModCommands this install registers, with their usage.",
				_ => List(report));
		}

		/// <summary>
		/// Every registered command, from every loaded mod.
		///
		/// Enumerated rather than remembered. A command list held here would be
		/// a copy of somebody else's decision, which is exactly the duplication
		/// DevCommandRegistry.Publish exists to remove one layer up.
		/// </summary>
		private static List<ModCommand> All() {
			var found = new List<ModCommand>();

			foreach (Mod mod in ModLoader.Mods) {
				try {
					found.AddRange(mod.GetContent<ModCommand>());
				}
				catch (Exception) {
					// One mod that cannot be enumerated is not a reason to refuse
					// every command in every other mod.
				}
			}

			return found;
		}

		/// <summary>
		/// What kind of caller this side is, in tModLoader's own vocabulary.
		///
		/// A dedicated server is a console; anything with a player behind it is
		/// chat. `CommandLoader.Matches` then decides whether a given command
		/// accepts that caller, which keeps the rule in the loader that owns it
		/// rather than in a copy here that could drift.
		/// </summary>
		private static CommandType CallerType =>
			Main.dedServ ? CommandType.Console : CommandType.Chat;

		private static void List(Action<string> report) {
			List<ModCommand> all = All();
			if (all.Count == 0) {
				report("OK: this install registers no ModCommands at all");
				return;
			}

			var lines = new List<string> {
				"OK: " + all.Count + " command(s), for a " + CallerType + " caller"
			};

			foreach (ModCommand command in all.OrderBy(c => c.Command,
					StringComparer.OrdinalIgnoreCase)) {
				lines.Add("  " + command.Command +
					" [" + command.Type + (Usable(command) ? "" : ", not here") + "] " +
					Safely(() => command.Usage));
			}

			report(string.Join("\n", lines));
		}

		private static bool Usable(ModCommand command) {
			try {
				return CommandLoader.Matches(command.Type, CallerType);
			}
			catch (Exception) {
				return false;
			}
		}

		private static void Run(string argument, Action<string> report) {
			string text = (argument ?? string.Empty).Trim();
			if (text.Length == 0) {
				report("REFUSED: \"command\" needs the command to run, as " +
					"command:<name> or command:<name> <args>");
				return;
			}

			string[] parts = text.Split(new[] { ' ', '\t' },
				StringSplitOptions.RemoveEmptyEntries);
			string name = parts[0].TrimStart('/');
			string[] args = parts.Skip(1).ToArray();

			List<ModCommand> all = All();
			ModCommand match = all.FirstOrDefault(c =>
				string.Equals(c.Command, name, StringComparison.OrdinalIgnoreCase));

			if (match == null) {
				// NAMES WHAT EXISTS. A misspelling is the commonest reason a
				// command does nothing, and the whole list is right here - a
				// refusal that withheld it would send the reader to ask for the
				// list this call could have included.
				report("REFUSED: no registered command called \"" + name +
					"\". This install serves: " +
					(all.Count == 0
						? "none at all"
						: string.Join(", ", all.Select(c => c.Command)
							.OrderBy(c => c, StringComparer.OrdinalIgnoreCase))));
				return;
			}

			if (!Usable(match)) {
				// The side rule, and it names the side. A Console command run
				// from a client would either do nothing or reach for a player
				// that is not there.
				report("REFUSED: \"" + match.Command + "\" is a " + match.Type +
					" command and this is the " +
					(Main.dedServ ? "dedicated server" : "client") +
					", which calls as " + CallerType + ". Send it to the other side.");
				return;
			}

			var caller = new Capturing(CallerType,
				Main.dedServ ? null : Main.LocalPlayer);

			try {
				match.Action(caller, text, args);
			}
			catch (Exception failed) {
				// The command's own failure, reported as ITS failure rather than
				// as this bridge breaking. Running somebody else's code means
				// this is a normal outcome, not an exceptional one.
				report("ERROR: \"" + match.Command + "\" threw: " + failed.Message);
				return;
			}

			report(Answer(match, caller));
		}

		/// <summary>
		/// What the command said, or the fact that it said nothing.
		///
		/// SILENCE IS REPORTED AS SILENCE. Plenty of commands do their work and
		/// print nothing, and an empty OK that looked identical to a command
		/// that had not run would make the two indistinguishable - which is the
		/// confusion this whole channel exists to remove.
		/// </summary>
		private static string Answer(ModCommand command, Capturing caller) {
			if (caller.Lines.Count == 0) {
				return "OK: \"" + command.Command +
					"\" ran and printed nothing (which many commands do)";
			}

			var kept = caller.Lines.Take(MaxReplyLines).ToList();
			string head = "OK: \"" + command.Command + "\" printed " +
				caller.Lines.Count + " line(s)";

			if (caller.Lines.Count > MaxReplyLines) {
				head += ", first " + MaxReplyLines + " shown";
			}

			return head + "\n" + string.Join("\n", kept);
		}

		private static string Safely(Func<string> read) {
			try {
				return read() ?? string.Empty;
			}
			catch (Exception) {
				return string.Empty;
			}
		}

		/// <summary>
		/// A caller that writes the command's replies down instead of to a
		/// screen nobody is looking at.
		///
		/// This is the whole trick. `ModCommand.Action` reports through whatever
		/// `CommandCaller` it is handed, so capturing the output needs no hook,
		/// no detour and no reflection - just a different implementation of an
		/// interface tModLoader already asks for.
		/// </summary>
		private sealed class Capturing : CommandCaller
		{
			public Capturing(CommandType type, Player player) {
				CommandType = type;
				Player = player;
				Lines = new List<string>();
			}

			public CommandType CommandType { get; }

			public Player Player { get; }

			public List<string> Lines { get; }

			public void Reply(string text, Color color) {
				Lines.Add(text ?? string.Empty);
			}
		}
	}
}
