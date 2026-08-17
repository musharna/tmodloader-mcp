using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Xna.Framework;
using Terraria;
using Terraria.GameContent.UI.Chat;
using Terraria.ID;
using Terraria.Localization;

namespace TModLoaderMcp.DevBridge
{
	/// <summary>
	/// Reading what the game SAID, and saying something back.
	///
	/// THE GAP THIS CLOSES. A screenshot can show that chat happened and cannot
	/// be read as text; `diag` reports what a mod chose to put in it. Neither
	/// reaches `Main.NewText`, which is where most Terraria mods print the
	/// things a developer wants to see - warnings, state changes, the output of
	/// their own commands. It was the one channel this harness could not hear.
	///
	/// HOW THE READING WORKS, and why it is not a detour. `Main.chatMonitor` is
	/// a public field of a public interface, and everything printed to chat goes
	/// through it. So this wraps whatever is there in a recorder that writes
	/// each line down and passes it straight on. No MonoMod hook, no reflection,
	/// nothing that a tModLoader update can silently change the shape of - just
	/// a second implementation of an interface the game already calls.
	///
	/// A DEDICATED SERVER HAS NO CHAT MONITOR, because it draws nothing. The
	/// recorder is only installed on a client, and asking a server to read chat
	/// is refused rather than answered with an empty list - "nothing was said"
	/// and "this side cannot hear" are different answers.
	///
	/// Opt-in, separately from the other two:
	///
	///     protected override void RegisterCommands(DevCommandRegistry r) =>
	///         DevChat.RegisterInto(r, Report);
	/// </summary>
	public static class DevChat
	{
		/// <summary>How many lines are kept. A session prints a lot and this is
		/// a diagnostic buffer, not a transcript.</summary>
		public const int Remembered = 200;

		/// <summary>How many are returned when a caller does not say.</summary>
		public const int DefaultShown = 25;

		private static readonly List<string> _heard = new List<string>();

		private static readonly object _lock = new object();

		public static void RegisterInto(DevCommandRegistry r, Action<string> report) {
			if (r == null) {
				throw new ArgumentNullException(nameof(r));
			}

			if (report == null) {
				throw new ArgumentNullException(nameof(report),
					"chat answers through Report; without it a read would find " +
					"lines and have nowhere to put them");
			}

			Listen();

			r.Register("chat", false,
				"Return the chat lines this client has heard since the mod loaded.",
				_ => Read(report));

			r.Register("say", true,
				"Print a line to chat - locally in singleplayer, to everybody " +
					"from a server, to the server from a client.",
				req => Say(req.Argument, report));
		}

		/// <summary>
		/// Start recording, once.
		///
		/// GUARDED AGAINST DOUBLE WRAPPING. A mod reload re-runs registration
		/// with the previous recorder still installed, and wrapping a wrapper
		/// every time would build a chain that grows for as long as somebody
		/// keeps reloading - each layer forwarding to the last, each recording
		/// the same line again.
		/// </summary>
		private static void Listen() {
			if (Main.dedServ) {
				return;
			}

			if (Main.chatMonitor is Recorder) {
				return;
			}

			if (Main.chatMonitor == null) {
				// Nothing to wrap and nothing to forward to. Reading will refuse
				// rather than return an empty list, which is the honest answer.
				return;
			}

			Main.chatMonitor = new Recorder(Main.chatMonitor);
		}

		private static void Remember(string line) {
			if (string.IsNullOrEmpty(line)) {
				return;
			}

			lock (_lock) {
				_heard.Add(line);
				if (_heard.Count > Remembered) {
					_heard.RemoveRange(0, _heard.Count - Remembered);
				}
			}
		}

		private static void Read(Action<string> report) {
			if (Main.dedServ) {
				report("REFUSED: a dedicated server draws no chat and has no " +
					"monitor to listen to. Ask a client, by name.");
				return;
			}

			if (!(Main.chatMonitor is Recorder)) {
				// Distinguished from "nothing was said". A recorder that never
				// installed means every line since load was missed, and
				// answering "no chat" would be a confident lie.
				report("ERROR: nothing is listening to chat on this client, so " +
					"no line since load was recorded");
				return;
			}

			string[] lines;
			lock (_lock) {
				lines = _heard.Skip(Math.Max(0, _heard.Count - DefaultShown)).ToArray();
			}

			if (lines.Length == 0) {
				report("OK: nothing has been said since the mod loaded");
				return;
			}

			report("OK: " + lines.Length + " line(s)\n" + string.Join("\n", lines));
		}

		/// <summary>
		/// Say something, on whichever side is asked.
		///
		/// The three cases are genuinely different and none of them is a
		/// fallback for another. A dedicated server BROADCASTS - it has no
		/// screen of its own to print to. A multiplayer client SENDS, so the
		/// line reaches everybody rather than only the person who asked for it.
		/// Singleplayer prints locally, because there is nobody to send to.
		/// </summary>
		private static void Say(string argument, Action<string> report) {
			string text = (argument ?? string.Empty).Trim();
			if (text.Length == 0) {
				report("REFUSED: \"say\" needs something to say");
				return;
			}

			try {
				if (Main.netMode == NetmodeID.Server) {
					Terraria.Chat.ChatHelper.BroadcastChatMessage(
						NetworkText.FromLiteral(text), Color.White, -1);
					report("OK: broadcast to every client");
					return;
				}

				if (Main.netMode == NetmodeID.MultiplayerClient) {
					Terraria.Chat.ChatHelper.SendChatMessageFromClient(
						new Terraria.Chat.ChatMessage(text));
					report("OK: sent to the server");
					return;
				}

				Main.NewText(text, 255, 255, 255);
				report("OK: printed locally");
			}
			catch (Exception failed) {
				report("ERROR: chat refused the line: " + failed.Message);
			}
		}

		/// <summary>
		/// An IChatMonitor that writes every line down and forwards everything.
		///
		/// EVERY MEMBER FORWARDS. This sits in the game's own draw path, so a
		/// method that did nothing would stop chat rendering, scrolling or
		/// clearing - and the symptom would be a broken game rather than a
		/// broken harness, which is the worst possible way for a diagnostic to
		/// fail.
		/// </summary>
		private sealed class Recorder : IChatMonitor
		{
			private readonly IChatMonitor _inner;

			public Recorder(IChatMonitor inner) {
				_inner = inner;
			}

			public void NewText(string newText, byte R, byte G, byte B) {
				Remember(newText);
				_inner.NewText(newText, R, G, B);
			}

			public void NewTextMultiline(string text, bool force, Color c, int WidthLimit) {
				Remember(text);
				_inner.NewTextMultiline(text, force, c, WidthLimit);
			}

			public void Clear() => _inner.Clear();

			public void Update() => _inner.Update();

			public void ResetOffset() => _inner.ResetOffset();

			public void OnResolutionChange() => _inner.OnResolutionChange();

			public void Offset(int linesOffset) => _inner.Offset(linesOffset);

			public void DrawChat(bool drawingPlayerChat) => _inner.DrawChat(drawingPlayerChat);
		}
	}
}
