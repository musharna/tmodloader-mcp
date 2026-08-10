using System.Collections.Generic;
using Xunit;
using TModLoaderMcp.DevBridge;

namespace TModLoaderMcp.DevBridge.Tests
{
	public class CaptureFindTests
	{
		private static CaptureFind.CandidateFile F(string path, long len) {
			return new CaptureFind.CandidateFile { Path = path, Length = len };
		}

		/// <summary>
		/// The ordinary case: one file appeared that was not there before.
		/// </summary>
		[Fact]
		public void PicksTheFileThatAppeared() {
			var before = new List<CaptureFind.CandidateFile> { F("a.png", 100) };
			var after = new List<CaptureFind.CandidateFile> {
				F("a.png", 100), F("b.png", 250),
			};

			Assert.Equal("b.png", CaptureFind.PickNew(before, after));
		}

		/// <summary>
		/// Nothing appeared, so the capture did not happen. Returning any path
		/// here would hand back a stale screenshot and call it the new one - the
		/// caller would then "verify" against an image of something else, which
		/// is the exact failure mode that killed OS-level screen capture.
		/// </summary>
		[Fact]
		public void ReturnsNullWhenNothingAppeared() {
			var before = new List<CaptureFind.CandidateFile> { F("a.png", 100) };
			var after = new List<CaptureFind.CandidateFile> { F("a.png", 100) };

			Assert.Null(CaptureFind.PickNew(before, after));

			// Positive control: the same call DOES find a file when one arrives,
			// so a PickNew hardwired to null could not pass this test.
			var grew = new List<CaptureFind.CandidateFile> {
				F("a.png", 100), F("c.png", 7),
			};
			Assert.Equal("c.png", CaptureFind.PickNew(before, grew));
		}

		/// <summary>
		/// A zero-length file is a capture caught mid-flush, not a capture. It
		/// must not be reported as finished - a reader would get an empty PNG and
		/// could not tell that from a black frame.
		/// </summary>
		[Fact]
		public void IgnoresAZeroLengthFileAsStillBeingWritten() {
			var before = new List<CaptureFind.CandidateFile>();
			var after = new List<CaptureFind.CandidateFile> { F("half.png", 0) };

			Assert.Null(CaptureFind.PickNew(before, after));
		}

		/// <summary>
		/// If several appeared, take the largest rather than an arbitrary one. A
		/// partially-flushed sibling is smaller; picking arbitrarily would
		/// sometimes return the incomplete one, and the failure would be
		/// intermittent - the worst kind to diagnose.
		/// </summary>
		[Fact]
		public void PrefersTheLargestWhenSeveralAppeared() {
			var before = new List<CaptureFind.CandidateFile>();
			var after = new List<CaptureFind.CandidateFile> {
				F("small.png", 10), F("big.png", 9000),
			};

			Assert.Equal("big.png", CaptureFind.PickNew(before, after));
		}
	}
}
