using Xunit;
using TModLoaderMcp.DevBridge;

namespace TModLoaderMcp.DevBridge.Tests
{
	/// <summary>
	/// Screenshot bounds. Tested because an off-screen or zero-area rectangle
	/// handed to GetBackBufferData throws inside the draw loop, which takes the
	/// frame with it - and because a region that silently widens to the whole
	/// screen would defeat the reason regions exist.
	/// </summary>
	public class ShotRegionTests
	{
		private const int W = 1920;
		private const int H = 1080;

		[Theory]
		[InlineData("topleft")]
		[InlineData("topright")]
		[InlineData("bottomleft")]
		[InlineData("bottomright")]
		[InlineData("full")]
		public void EveryRegionStaysInsideTheScreen(string name) {
			Assert.True(ShotRegion.TryResolve(name, W, H,
				out int x, out int y, out int w, out int h));

			Assert.True(w > 0 && h > 0, "a zero-area capture throws in the draw loop");
			Assert.True(x >= 0 && y >= 0, $"origin ({x},{y}) is off-screen");
			Assert.True(x + w <= W, $"right edge {x + w} exceeds {W}");
			Assert.True(y + h <= H, $"bottom edge {y + h} exceeds {H}");
		}

		[Fact]
		public void TheCornersAreActuallyInTheirCorners() {
			ShotRegion.TryResolve("topleft", W, H, out int tlx, out int tly, out _, out _);
			Assert.Equal(0, tlx);
			Assert.Equal(0, tly);

			ShotRegion.TryResolve("bottomleft", W, H, out int blx, out int bly, out _, out int blh);
			Assert.Equal(0, blx);
			Assert.Equal(H - blh, bly);

			ShotRegion.TryResolve("bottomright", W, H, out int brx, out int bry, out int brw, out int brh);
			Assert.Equal(W - brw, brx);
			Assert.Equal(H - brh, bry);
		}

		/// <summary>
		/// The readout panel sits bottom-left, so that region has to actually
		/// contain it rather than stopping short.
		/// </summary>
		[Fact]
		public void BottomLeftCoversTheReadoutPanel() {
			ShotRegion.TryResolve("bottomleft", W, H,
				out int x, out int y, out int w, out int h);

			// The panel is 260x86 at x=16, anchored 90px above the bottom edge.
			Assert.True(x <= 16, "panel's left edge is outside the capture");
			Assert.True(x + w >= 16 + 260, "panel's right edge is outside the capture");
			Assert.True(y <= H - 176, "panel's top edge is above the capture");
			Assert.True(y + h >= H - 90, "panel's bottom edge is below the capture");
		}

		/// <summary>
		/// THE LOAD-BEARING ONE. A misspelled region falling back to the full
		/// frame would capture the whole screen - character name, world name and
		/// any chat - while looking exactly like the command working. Regions
		/// exist to bound what ends up in the picture, so an unknown one is an
		/// error, never a wider capture.
		/// </summary>
		[Theory]
		[InlineData("bottomlft")]
		[InlineData("centre")]
		[InlineData("")]
		[InlineData(null)]
		[InlineData("everything")]
		public void AnUnknownRegionIsRefusedRatherThanWidened(string name) {
			Assert.False(ShotRegion.TryResolve(name, W, H,
				out int x, out int y, out int w, out int h));

			Assert.Equal(0, w);
			Assert.Equal(0, h);
			Assert.Equal(0, x);
			Assert.Equal(0, y);
		}

		[Theory]
		[InlineData("bottom-left")]
		[InlineData("bottom_left")]
		[InlineData("BottomLeft")]
		[InlineData("  BOTTOM LEFT  ")]
		public void SeparatorsAndCaseAreNoise(string name) {
			Assert.True(ShotRegion.TryResolve(name, W, H, out _, out int y, out _, out int h));
			Assert.Equal(H - h, y);
		}

		/// <summary>
		/// A window smaller than a corner region must clamp rather than produce
		/// a rectangle hanging off the edge.
		/// </summary>
		[Fact]
		public void ACornerNeverExceedsASmallWindow() {
			const int small = 320;
			Assert.True(ShotRegion.TryResolve("bottomright", small, small,
				out int x, out int y, out int w, out int h));

			Assert.Equal(small, w);
			Assert.Equal(small, h);
			Assert.Equal(0, x);
			Assert.Equal(0, y);
		}

		[Theory]
		[InlineData(0, 1080)]
		[InlineData(1920, 0)]
		[InlineData(-1, -1)]
		public void ANonsenseScreenSizeIsRefused(int w, int h) {
			Assert.False(ShotRegion.TryResolve("full", w, h, out _, out _, out _, out _));
		}
	}
}
