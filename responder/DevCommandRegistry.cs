using System;
using System.Collections.Generic;
using System.Text;

namespace TModLoaderMcp.DevBridge
{
	/// <summary>What to run when a verb arrives.</summary>
	public delegate void DevCommandHandler(DevRequest request);

	/// <summary>
	/// One command the responder serves: its verb, whether it reads an argument,
	/// a line of help, and the thing to run.
	///
	/// TakesArgument is here rather than inferred because the HARNESS needs it and
	/// currently guesses. tmodloader-mcp refuses an argument for the eleven
	/// commands whose argument the mod parses and then discards, and it knows
	/// which eleven by holding its own copy of the list - so the day a command
	/// starts reading an argument, the harness silently refuses to send one and
	/// the feature looks broken from the outside. Published rather than
	/// duplicated, it cannot drift.
	/// </summary>
	public sealed class DevCommandEntry
	{
		public DevCommandEntry(string name, bool takesArgument, string summary, DevCommandHandler handler) {
			Name = name;
			TakesArgument = takesArgument;
			Summary = summary;
			Handler = handler;
		}

		public string Name { get; }

		public bool TakesArgument { get; }

		public string Summary { get; }

		public DevCommandHandler Handler { get; }
	}

	/// <summary>
	/// The verbs this responder answers to, as data rather than as a switch.
	///
	/// It was a closed enum and a closed switch, which meant the set of commands
	/// was decided at COMPILE time by this mod - so the generic half of this
	/// responder could not be vendored into another mod without carrying
	/// Biomancy's gameplay verbs (mutate, vat, creep, killcreep) with it, and a
	/// second mod could not add one of its own without editing a file it does not
	/// own. Worse, the set existed in three places that had to agree by hand: the
	/// enum, the switch, and the harness's allowlist in another repository.
	///
	/// A registry makes the set answerable at RUN time, which is what lets it be
	/// published (see Publish) and therefore learned rather than duplicated.
	///
	/// It also makes "is this command actually served?" a question a test can ask.
	/// CreepSyncWiringTests had to scan DevCapture.cs for the literal text
	/// "case DevCommand.Creep:" because a closed switch cannot be interrogated -
	/// and that scan states its own limitation, that it cannot see an unreachable
	/// call. Registration is reachable by construction.
	/// </summary>
	public sealed class DevCommandRegistry
	{
		private readonly Dictionary<string, DevCommandEntry> _byName =
			new Dictionary<string, DevCommandEntry>(StringComparer.OrdinalIgnoreCase);

		private readonly List<DevCommandEntry> _inOrder = new List<DevCommandEntry>();

		/// <summary>
		/// Add a command, or throw.
		///
		/// Every rule below refuses a registration that would LOOK fine and never
		/// fire, which is the only failure mode worth being loud about here - a
		/// command that throws at load is fixed in a minute, and one that silently
		/// never runs is the bug this whole diagnostic channel exists to catch.
		///
		/// The name rule is the load-bearing one. ':' and '@' are the payload's
		/// delimiters, so a command called "kill:creep" would be split by
		/// DevCommands.Parse into the verb "kill" and the argument "creep" and
		/// could never be delivered under the name it registered. Refusing the
		/// name is the only place that can be caught: by dispatch time the verb
		/// has already been cut in half.
		/// </summary>
		public void Register(string name, bool takesArgument, string summary, DevCommandHandler handler) {
			if (string.IsNullOrWhiteSpace(name)) {
				throw new ArgumentException("a command needs a name", nameof(name));
			}

			if (handler == null) {
				throw new ArgumentNullException(nameof(handler),
					"command \"" + name + "\" has no handler, so it would parse and do nothing");
			}

			string verb = name.Trim().ToLowerInvariant();

			foreach (char c in verb) {
				if (!char.IsLetterOrDigit(c)) {
					throw new ArgumentException(
						"command \"" + name + "\" contains \"" + c + "\", which the trigger " +
						"payload cannot carry - ':' and '@' are its delimiters and whitespace " +
						"is trimmed, so this command could never be addressed by the name it " +
						"registered under",
						nameof(name));
				}
			}

			// Two handlers under one verb means one of them silently never runs,
			// and which one depends on registration order.
			if (_byName.ContainsKey(verb)) {
				throw new ArgumentException(
					"command \"" + verb + "\" is already registered; the second handler " +
					"would never run",
					nameof(name));
			}

			// A newline would split one command across two lines of the published
			// list, and the harness reading it would see a malformed second entry.
			string help = (summary ?? string.Empty).Replace('\r', ' ').Replace('\n', ' ').Trim();

			var entry = new DevCommandEntry(verb, takesArgument, help, handler);
			_byName[verb] = entry;
			_inOrder.Add(entry);
		}

		public bool TryResolve(string verb, out DevCommandEntry entry) {
			entry = null;

			if (string.IsNullOrEmpty(verb)) {
				return false;
			}

			return _byName.TryGetValue(verb, out entry);
		}

		public int Count => _inOrder.Count;

		/// <summary>
		/// Every command, in registration order - generic ones first, then the
		/// mod's, which is the order a human wants to read them in.
		/// </summary>
		public IReadOnlyList<DevCommandEntry> Entries => _inOrder;

		/// <summary>
		/// The verbs, comma-joined, for the unknown-command help.
		///
		/// This used to be a hand-written sentence listing twelve commands, next to
		/// a switch listing the same twelve. They were already one command apart
		/// once: the help is the only thing that tells a human what the trigger
		/// accepts, so a command missing from it is undiscoverable even though it
		/// works.
		/// </summary>
		public string Names {
			get {
				var sb = new StringBuilder();

				foreach (DevCommandEntry e in _inOrder) {
					if (sb.Length > 0) {
						sb.Append(", ");
					}

					sb.Append(e.Name);

					if (e.TakesArgument) {
						sb.Append(":<arg>");
					}
				}

				return sb.ToString();
			}
		}

		/// <summary>
		/// The command list as the harness reads it: one command per line, tab
		/// separated, '#' comments.
		///
		/// This is the half of the extraction that pays off across the repository
		/// boundary. The harness held its own copy of this mod's twelve verbs and
		/// of which of them read an argument; a mod that added a command got no
		/// harness support until somebody edited Python, and a mod that removed one
		/// left the harness composing triggers nothing would answer. Published at
		/// load, the list is a fact about the running mod rather than a belief
		/// about it.
		/// </summary>
		public string Publish() {
			var sb = new StringBuilder();

			sb.Append("# commands served by this responder, written at load\n");
			// A CAPABILITY, NOT A COMMENT, despite the '#'. The harness reads
			// this exact line to learn that replies from this responder echo a
			// request id (see DevResponder.Report) and only then attaches one -
			// so a harness driving an older vendored copy never sends what that
			// copy would mistake for part of a target. It is spelled as a
			// comment because that is the one line format every EXISTING parser
			// of this file already skips: an older harness reading a newer
			// responder sees nothing new at all.
			sb.Append("# replies: tagged\n");
			sb.Append("# name\targ|noarg\tsummary\n");

			foreach (DevCommandEntry e in _inOrder) {
				sb.Append(e.Name);
				sb.Append('\t');
				sb.Append(e.TakesArgument ? "arg" : "noarg");
				sb.Append('\t');
				sb.Append(e.Summary);
				sb.Append('\n');
			}

			return sb.ToString();
		}
	}
}
