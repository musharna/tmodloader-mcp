using System.IO;
using TModLoaderMcp.DevBridge;
using Xunit;

namespace TModLoaderMcp.DevBridge.Tests
{
	/// <summary>
	/// The gate that decides whether this install runs the dev responder.
	///
	/// It fails invisibly in BOTH directions, which is why it is tested at all:
	/// left on where it should be off, a player's game quietly polls their save
	/// directory and will act on anything that appears there; turned off where it
	/// should be on, the harness waits on a game that was never going to answer
	/// and reports a timeout against the wrong thing.
	/// </summary>
	public class DevBridgeGateTests
	{
		private static string InstallWithSource(string root, string modName) {
			Directory.CreateDirectory(Path.Combine(root, DevBridgeGate.SourcesFolder, modName));
			return root;
		}

		[Fact]
		public void AnInstallHoldingThisModsSourceIsADevelopedOne() {
			string root = Path.Combine(Path.GetTempPath(), Path.GetRandomFileName());
			try {
				InstallWithSource(root, "Biomancy");

				Assert.True(DevBridgeGate.EnabledFor(root, "Biomancy"));
			}
			finally {
				Directory.Delete(root, recursive: true);
			}
		}

		[Fact]
		public void AnInstallWithoutItIsAPlayedOne() {
			string root = Path.Combine(Path.GetTempPath(), Path.GetRandomFileName());
			try {
				// A player's install: Mods, Worlds, Players - everything but a
				// source folder for the mod they are playing.
				Directory.CreateDirectory(Path.Combine(root, "Mods"));
				Directory.CreateDirectory(Path.Combine(root, "Worlds"));

				Assert.False(DevBridgeGate.EnabledFor(root, "Biomancy"));

				// Positive control in the same test: the ONLY thing that differs
				// is the source folder. Without this, a gate that returned false
				// for every input would pass the assertion above.
				InstallWithSource(root, "Biomancy");
				Assert.True(DevBridgeGate.EnabledFor(root, "Biomancy"));
			}
			finally {
				Directory.Delete(root, recursive: true);
			}
		}

		/// <summary>
		/// SOMEBODY ELSE'S SOURCE FOLDER IS NOT THIS MOD'S.
		///
		/// A modder with a ModSources directory full of other people's projects
		/// is still a player of THIS mod, and would be handed a responder that
		/// nothing on their machine is driving.
		/// </summary>
		[Fact]
		public void AnotherModsSourceDoesNotOpenThisGate() {
			string root = Path.Combine(Path.GetTempPath(), Path.GetRandomFileName());
			try {
				InstallWithSource(root, "SomeOtherMod");

				Assert.False(DevBridgeGate.EnabledFor(root, "Biomancy"));

				// Positive control: the check is looking in the right place.
				Assert.True(DevBridgeGate.EnabledFor(root, "SomeOtherMod"));
			}
			finally {
				Directory.Delete(root, recursive: true);
			}
		}

		/// <summary>
		/// A FILE of the right name is not a source folder. Directory.Exists is
		/// false for a file, and this pins that rather than trusting it, because
		/// the opposite - Exists() being true for either - is the more common
		/// API shape and would open the gate on a stray download.
		/// </summary>
		[Fact]
		public void AFileWhereTheFolderShouldBeDoesNotOpenTheGate() {
			string root = Path.Combine(Path.GetTempPath(), Path.GetRandomFileName());
			try {
				Directory.CreateDirectory(Path.Combine(root, DevBridgeGate.SourcesFolder));
				File.WriteAllText(DevBridgeGate.SourcePathFor(root, "Biomancy"), "not a folder");

				Assert.False(DevBridgeGate.EnabledFor(root, "Biomancy"));
			}
			finally {
				Directory.Delete(root, recursive: true);
			}
		}

		[Theory]
		[InlineData(null, "Biomancy")]
		[InlineData("", "Biomancy")]
		[InlineData("   ", "Biomancy")]
		[InlineData("/tmp", null)]
		[InlineData("/tmp", "")]
		[InlineData("/tmp", "   ")]
		public void AMissingPathOrNameIsClosedRatherThanThrown(string savePath, string modName) {
			// Closed, not open, and not an exception either: this runs during
			// content loading, where a throw takes the whole mod down and an
			// open gate is the thing being prevented.
			Assert.False(DevBridgeGate.EnabledFor(savePath, modName));
		}

		[Fact]
		public void TheSourcePathIsTheOneTModLoaderUses() {
			string path = DevBridgeGate.SourcePathFor("/save", "Biomancy");

			Assert.Equal(Path.Combine("/save", "ModSources", "Biomancy"), path);
		}

		/// <summary>
		/// The refusal has to name the directory it looked for. "The responder is
		/// disabled" on its own sends somebody hunting for a setting that does
		/// not exist.
		/// </summary>
		[Fact]
		public void TheExplanationNamesThePathItLookedFor() {
			string said = DevBridgeGate.Explain("/save", "Biomancy");

			Assert.Contains(DevBridgeGate.SourcePathFor("/save", "Biomancy"), said);
			Assert.Contains("trigger", said);
		}
	}
}
