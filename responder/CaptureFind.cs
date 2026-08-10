using System.Collections.Generic;
using System.Linq;

namespace TModLoaderMcp.DevBridge
{
	/// <summary>
	/// Decides which file a capture produced.
	///
	/// This exists as a separate pure function because where QuickScreenshot()
	/// writes is not exposed by any member of the assembly, and no screenshots
	/// directory existed on this machine to observe. Rather than hardcode a
	/// guessed path - which would fail silently, reporting success while pointing
	/// at nothing - the caller lists the directory before and after and asks this
	/// what changed.
	/// </summary>
	public static class CaptureFind
	{
		public struct CandidateFile
		{
			public string Path;
			public long Length;
		}

		/// <summary>
		/// The new, finished file, or null if the capture did not produce one.
		/// Null is a real answer and must be handled: returning a path anyway
		/// would hand back a stale screenshot to be "verified" as the new one.
		/// </summary>
		public static string PickNew(
				IEnumerable<CandidateFile> before, IEnumerable<CandidateFile> after) {
			if (after == null) {
				return null;
			}

			var seen = new HashSet<string>(
				(before ?? Enumerable.Empty<CandidateFile>()).Select(f => f.Path));

			// Zero-length means caught mid-flush. An empty PNG is
			// indistinguishable from a black frame once it reaches a reader, so it
			// must never be reported as finished.
			List<CandidateFile> fresh = after
				.Where(f => !seen.Contains(f.Path) && f.Length > 0)
				.ToList();

			if (fresh.Count == 0) {
				return null;
			}

			// Largest, not first: a partially-flushed sibling is smaller, and
			// picking arbitrarily would make the failure intermittent.
			return fresh.OrderByDescending(f => f.Length).First().Path;
		}
	}
}
