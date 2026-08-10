using Xunit;
using TModLoaderMcp.DevBridge;

namespace TModLoaderMcp.DevBridge.Tests
{
	/// <summary>
	/// The guard that keeps a capture request from crashing the engine.
	///
	/// The real failure it prevents was measured, not imagined: a capture served
	/// 281ms after world entry took the process down with
	/// IndexOutOfRangeException in TileDrawing.PrepareForAreaDrawing, reached via
	/// CaptureCamera.DrawTick. It cannot be caught at runtime - QuickScreenshot
	/// only queues the work, so the throw lands in Main.DoDraw - which is why the
	/// check has to be right up front.
	///
	/// The world here is 4200x1200, matching the world that actually crashed.
	/// </summary>
	public class CaptureBoundsTests
	{
		private const int W = 4200;
		private const int H = 1200;

		/// <summary>
		/// POSITIVE CONTROL. Without this, every other test in this file passes
		/// against a Problem() hardwired to return "nope" for all input - a
		/// refusal that refuses everything protects nothing, because it also
		/// refuses every legitimate capture.
		/// </summary>
		[Fact]
		public void AcceptsAnOrdinaryMidWorldView() {
			Assert.Null(CaptureBounds.Problem(2000, 500, 2120, 570, W, H));
		}

		[Fact]
		public void RejectsAViewRunningOffTheLeftEdge() {
			string p = CaptureBounds.Problem(-3, 500, 120, 570, W, H);
			Assert.NotNull(p);
			Assert.Contains("margin", p);
		}

		[Fact]
		public void RejectsAViewRunningOffTheTop() {
			Assert.NotNull(CaptureBounds.Problem(2000, 0, 2120, 70, W, H));
		}

		[Fact]
		public void RejectsAViewRunningPastTheRightEdge() {
			Assert.NotNull(CaptureBounds.Problem(4100, 500, 4199, 570, W, H));
		}

		[Fact]
		public void RejectsAViewRunningPastTheBottom() {
			Assert.NotNull(CaptureBounds.Problem(2000, 1100, 2120, 1200, W, H));
		}

		/// <summary>
		/// The world-entry case. Before the world is set up, maxTiles is 0 - and a
		/// bounds check against a zero-sized world silently accepts everything if
		/// it only compares against the upper limit.
		/// </summary>
		[Fact]
		public void RejectsWhenWorldBoundsAreNotSetYet() {
			string p = CaptureBounds.Problem(0, 0, 120, 70, 0, 0);
			Assert.NotNull(p);
			Assert.Contains("not set yet", p);
		}

		[Fact]
		public void RejectsAnEmptyOrInvertedRect() {
			Assert.NotNull(CaptureBounds.Problem(2000, 500, 2000, 570, W, H));
			Assert.NotNull(CaptureBounds.Problem(2200, 500, 2100, 570, W, H));
		}

		/// <summary>
		/// Exactly on the margin is allowed; one tile inside it is not. Pins the
		/// boundary so a later "off by one" refactor has to fail here.
		/// </summary>
		[Fact]
		public void MarginBoundaryIsExact() {
			Assert.Null(CaptureBounds.Problem(4, 4, W - 4, H - 4, W, H));
			Assert.NotNull(CaptureBounds.Problem(3, 4, W - 4, H - 4, W, H));
			Assert.NotNull(CaptureBounds.Problem(4, 4, W - 3, H - 4, W, H));
		}

		/// <summary>
		/// The reason is the product, not a side effect - four wrong diagnoses in
		/// this feature came from guards that refused without saying what they saw.
		/// </summary>
		[Fact]
		public void ReasonNamesTheRectAndTheWorld() {
			string p = CaptureBounds.Problem(-3, 500, 120, 570, W, H);
			Assert.Contains("(-3,500)-(120,570)", p);
			Assert.Contains("4200x1200", p);
		}
	}
}
