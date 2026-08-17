using System;
using System.IO;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using Terraria;
using Terraria.ModLoader;

namespace TModLoaderMcp.DevBridge
{
	/// <summary>
	/// Saves a PNG of part of the frame, read from Terraria's own back buffer.
	///
	/// WHY THIS IS NOT THE THING THAT WENT WRONG BEFORE
	///
	/// OS-level capture is banned here and stays banned: a full-screen grab
	/// caught a Teams inbox with real colleagues' names and addresses, and a
	/// CROPPED window grab returned a picture of Discord that passed every check
	/// available, because another window was sitting on top of the game.
	///
	/// GraphicsDevice.GetBackBufferData reads what THIS PROCESS rendered. A
	/// window in front of the game is not in Terraria's back buffer, so it cannot
	/// appear in the output - by construction, not by luck. That is the entire
	/// justification, and it is why the capture is taken here rather than by
	/// anything outside the game.
	///
	/// It still holds the player's own frame, which is why every request must
	/// name a region: see ShotRegion.
	///
	/// WHY Main.OnPostDraw
	///
	/// It fires after the whole frame is drawn - world, dust, vanilla HUD and our
	/// own interface layers. PostDrawInterface runs mid-interface and would catch
	/// a half-drawn frame, which is worse than no picture: it would look like a
	/// rendering bug that does not exist. Verified against the assembly:
	/// Main.add_OnPostDraw(Action`1&lt;GameTime&gt;) is Public and Static.
	///
	/// This is what finally makes the interface layer checkable. The capture
	/// camera draws neither dust nor UI - measured, six captures with and without
	/// a mutant on screen, zero cue-coloured pixels either way - so the motes,
	/// the readout and the Apex bar had no mechanical check before this.
	/// </summary>
	public class FrameShot : ModSystem
	{
		/// <summary>Region requested but not yet taken, or null.</summary>
		private static string _pending;

		/// <summary>
		/// The filename the pending shot writes to, carried with the request.
		///
		/// It was the literal "biomancy-shot.png", which is what made this
		/// capture Biomancy's rather than any mod's. The caller supplies it now
		/// because the caller is the ModSystem that knows its own name; this
		/// class draws pixels and should not know whose they are.
		/// </summary>
		private static string _pendingName;

		/// <summary>Where the last shot went, reported back through DevCapture.</summary>
		public static string LastPath;

		/// <summary>Why the last shot failed, or null.</summary>
		public static string LastError;

		public override void Load() {
			if (Main.dedServ) {
				// A dedicated server has no graphics device and no back buffer to
				// read. Subscribing there would be a null reference waiting for
				// somebody to arm a trigger.
				return;
			}

			Main.OnPostDraw += Capture;
		}

		public override void Unload() {
			// Unsubscribed explicitly: the event is a STATIC field on Main and
			// outlives a mod reload, so leaving this attached would call into a
			// disposed assembly on the next Build + Reload.
			Main.OnPostDraw -= Capture;
			_pending = null;
			_pendingName = null;
			LastPath = null;
			LastError = null;
		}

		/// <summary>
		/// Ask for a shot. Returns false with a reason if the request cannot be
		/// served, so the caller can report it rather than leave the requester
		/// waiting on a file that is never coming.
		/// </summary>
		public static bool Request(string region, string outputName, out string problem) {
			if (Main.dedServ) {
				problem = "a dedicated server has no back buffer to read; ask a client";
				return false;
			}

			if (string.IsNullOrWhiteSpace(region)) {
				problem = "no region given - use shot:<region>, one of " + ShotRegion.Names;
				return false;
			}

			// Resolved against the CURRENT screen so a bad name is refused now,
			// while there is somebody to tell, rather than inside the draw loop.
			if (!ShotRegion.TryResolve(region, Main.screenWidth, Main.screenHeight,
					out _, out _, out _, out _)) {
				problem = "unknown region \"" + region + "\" - expected one of " + ShotRegion.Names;
				return false;
			}

			_pending = region;
			_pendingName = outputName;
			problem = null;
			return true;
		}

		private static void Capture(GameTime gameTime) {
			string region = _pending;
			if (region == null) {
				return;
			}

			// Cleared FIRST. A throw below with the request still pending would
			// re-enter on every frame forever, writing the same failure sixty
			// times a second.
			_pending = null;

			try {
				GraphicsDevice device = Main.graphics?.GraphicsDevice;
				if (device == null) {
					LastError = "no graphics device";
					return;
				}

				if (!ShotRegion.TryResolve(region, Main.screenWidth, Main.screenHeight,
						out int x, out int y, out int w, out int h)) {
					// The screen can be resized between Request and here.
					LastError = "region \"" + region + "\" does not fit the current screen";
					return;
				}

				var pixels = new Color[w * h];
				device.GetBackBufferData(new Rectangle(x, y, w, h), pixels, 0, pixels.Length);

				using (var texture = new Texture2D(device, w, h)) {
					texture.SetData(pixels);

					string path = Path.Combine(Main.SavePath,
						DevArtifacts.ForSide(_pendingName, Main.dedServ));

					using (var stream = File.Create(path)) {
						texture.SaveAsPng(stream, w, h);
					}

					LastPath = path;
					LastError = null;
				}
			}
			catch (Exception e) {
				// Loud, and swallowed only in the sense that it must not take the
				// frame down - an exception escaping a draw event kills the game.
				LastError = e.ToString();
			}
		}
	}
}
