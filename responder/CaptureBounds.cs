namespace TModLoaderMcp.DevBridge
{
	/// <summary>
	/// Decides whether a view rectangle is safe to hand to the capture camera.
	///
	/// Split out of DevCapture with no dependency on Terraria types so it can be
	/// tested. It has to be: this predicate is the only thing standing between a
	/// capture request and a hard engine crash, and the failure it prevents cannot
	/// be caught at runtime. QuickScreenshot only QUEUES the capture, so the
	/// IndexOutOfRangeException surfaces later inside Main.DoDraw, where no
	/// try/catch of ours can reach it. Getting this wrong kills the game.
	///
	/// Why these bounds: QuickScreenshot builds its rectangle from the current
	/// view and passes it on unclamped - read out of the assembly, not assumed:
	///
	///   Main::get_ViewPosition -> Utils::ToTileCoordinates
	///   Main::get_ViewPosition + Main::get_ViewSize -> Utils::ToTileCoordinates
	///   newobj Rectangle::.ctor -> CaptureInterface::StartCamera
	/// </summary>
	public static class CaptureBounds
	{
		/// <summary>
		/// The camera expands the requested area for edge blending, so a rectangle
		/// that merely touches the world boundary is still unsafe.
		/// </summary>
		public const int DefaultMargin = 4;

		/// <summary>
		/// Why this rectangle cannot be captured, or null when it can.
		///
		/// Returns a reason rather than a bool so the refusal can say what it saw.
		/// A bare "false" here would have been one more guard that reports nothing,
		/// which is how this feature produced four wrong diagnoses in a row.
		/// </summary>
		public static string Problem(
				int tlX, int tlY, int brX, int brY,
				int maxTilesX, int maxTilesY, int margin = DefaultMargin) {

			string rect = "(" + tlX + "," + tlY + ")-(" + brX + "," + brY + ") in a " +
				maxTilesX + "x" + maxTilesY + " world";

			if (maxTilesX <= 0 || maxTilesY <= 0) {
				return "world bounds are not set yet: " + rect;
			}

			if (brX <= tlX || brY <= tlY) {
				return "view rect is empty or inverted: " + rect;
			}

			if (tlX < margin || tlY < margin) {
				return "view rect starts inside the " + margin + "-tile margin: " + rect;
			}

			if (brX > maxTilesX - margin || brY > maxTilesY - margin) {
				return "view rect ends inside the " + margin + "-tile margin: " + rect;
			}

			return null;
		}
	}
}
