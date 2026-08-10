namespace TModLoaderMcp.DevBridge
{
	/// <summary>
	/// Which part of the frame a screenshot captures.
	///
	/// REGIONS EXIST FOR PRIVACY, not for file size. The frame this reads is
	/// Terraria's own back buffer, so it can only ever contain what the game
	/// drew - no other window can appear in it, which is the whole reason this
	/// is allowed to exist at all after an OS-level screen grab returned a
	/// picture of Discord. But the game's own frame still holds a character
	/// name, a world name and any chat on screen, and in multiplayer that chat
	/// is somebody else's. Capturing only the corner under test keeps the
	/// picture to the thing actually being checked.
	///
	/// Terraria-free arithmetic so the bounds are unit-testable: an off-screen
	/// or zero-area rectangle handed to GetBackBufferData is an exception in the
	/// draw loop, which takes the frame with it.
	/// </summary>
	public static class ShotRegion
	{
		/// <summary>Corner region size, before clamping to the screen.</summary>
		public const int CornerWidth = 640;

		public const int CornerHeight = 360;

		/// <summary>
		/// Resolve a named region against a screen size.
		///
		/// Returns false for an unknown name rather than falling back to the
		/// full frame. A misspelled region quietly capturing the WHOLE screen is
		/// exactly the failure the region names exist to prevent, and it would
		/// look identical to the command working.
		/// </summary>
		public static bool TryResolve(string name, int screenWidth, int screenHeight,
				out int x, out int y, out int width, out int height) {
			x = 0;
			y = 0;
			width = 0;
			height = 0;

			if (screenWidth <= 0 || screenHeight <= 0) {
				return false;
			}

			// A corner cannot be larger than the screen it sits in - a small
			// window would otherwise produce a rectangle running off the edge.
			int cw = CornerWidth < screenWidth ? CornerWidth : screenWidth;
			int ch = CornerHeight < screenHeight ? CornerHeight : screenHeight;

			switch (Normalise(name)) {
				case "full":
					width = screenWidth;
					height = screenHeight;
					return true;

				case "topleft":
					width = cw;
					height = ch;
					return true;

				case "topright":
					x = screenWidth - cw;
					width = cw;
					height = ch;
					return true;

				case "bottomleft":
					y = screenHeight - ch;
					width = cw;
					height = ch;
					return true;

				case "bottomright":
					x = screenWidth - cw;
					y = screenHeight - ch;
					width = cw;
					height = ch;
					return true;

				default:
					return false;
			}
		}

		/// <summary>Every region name, for the error message when one misses.</summary>
		public static string Names => "topleft, topright, bottomleft, bottomright, full";

		/// <summary>
		/// Case and separators are noise from a human typing into a trigger file:
		/// "bottom-left", "bottom_left" and "BottomLeft" all mean one thing.
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
	}
}
