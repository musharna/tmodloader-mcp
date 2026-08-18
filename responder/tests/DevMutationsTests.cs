using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text.RegularExpressions;
using Xunit;

namespace TModLoaderMcp.DevBridge.Tests
{
    /// <summary>
    /// The mutating verbs, in the two halves they were split into.
    ///
    /// DevMutationArgs imports nothing but System, so everything it decides -
    /// what an argument may say, and which side may say it - is tested by
    /// running it. DevMutations imports Terraria and cannot be on this
    /// project's compile line, so the two properties that make the split SAFE
    /// are checked by reading it: that nothing registers these by default, and
    /// that the verbs it registers are the ones the other half names.
    ///
    /// A source scan, and it says so. The template mod compiling this folder
    /// against a real tModLoader is the other half; neither is sufficient alone.
    /// </summary>
    public class DevMutationsTests
    {
        // ---- time ----------------------------------------------------------

        [Theory]
        [InlineData("dawn", true, 0.0)]
        [InlineData("noon", true, 0.5)]
        [InlineData("dusk", false, 0.0)]
        [InlineData("midnight", false, 0.5)]
        public void EachTimeNameIsAPhaseAndAPositionInIt(string name, bool day, double where) {
            Assert.True(DevMutationArgs.TryResolveTime(name, out bool dayTime,
                out double fraction, out string problem), problem);

            Assert.Equal(day, dayTime);
            Assert.Equal(where, fraction, 6);
        }

        /// <summary>
        /// A FRACTION rather than a tick count, deliberately. Ticks would put
        /// 54000 and 32400 into a file that cannot see the game, and would be
        /// wrong the day a mod changes the day length. This pins the choice: a
        /// fraction is never larger than one, and a tick count always is.
        /// </summary>
        [Fact]
        public void NoTimeResolvesToSomethingThatCouldBeATickCount() {
            foreach (string name in new[] { "dawn", "noon", "dusk", "midnight" }) {
                Assert.True(DevMutationArgs.TryResolveTime(name, out _,
                    out double fraction, out _));
                Assert.InRange(fraction, 0.0, 1.0);
            }
        }

        [Theory]
        [InlineData("NOON")]
        [InlineData("  noon  ")]
        [InlineData("Mid-Night")]
        [InlineData("mid night")]
        public void CaseAndSeparatorsAreNoiseFromSomebodyTyping(string written) {
            Assert.True(DevMutationArgs.TryResolveTime(written, out _, out _,
                out string problem), problem);
        }

        [Fact]
        public void AnUnknownTimeNamesTheOnesThatExist() {
            Assert.False(DevMutationArgs.TryResolveTime("teatime", out _, out _,
                out string problem));

            Assert.Contains("teatime", problem);
            Assert.Contains("noon", problem);

            // POSITIVE CONTROL, same test: the vocabulary it just recited works
            // against the very same call, so the refusal is about the word.
            Assert.True(DevMutationArgs.TryResolveTime("noon", out _, out _, out _));
        }

        // ---- weather -------------------------------------------------------

        [Fact]
        public void WeatherIsThreeStatesAndAStormIsWorseThanRain() {
            Assert.True(DevMutationArgs.TryResolveWeather("clear", out bool clear,
                out float clearSeverity, out _));
            Assert.False(clear);
            Assert.Equal(0f, clearSeverity);

            Assert.True(DevMutationArgs.TryResolveWeather("rain", out bool raining,
                out float rain, out _));
            Assert.True(raining);

            Assert.True(DevMutationArgs.TryResolveWeather("storm", out bool storming,
                out float storm, out _));
            Assert.True(storming);

            Assert.True(storm > rain,
                "a storm that is no worse than rain makes the two words the same " +
                "command wearing different names");
        }

        [Fact]
        public void AnUnknownWeatherNamesTheOnesThatExist() {
            Assert.False(DevMutationArgs.TryResolveWeather("snow", out _, out _,
                out string problem));

            Assert.Contains("snow", problem);
            Assert.Contains("storm", problem);
        }

        // ---- ids and counts ------------------------------------------------

        [Fact]
        public void AnIdOnItsOwnMeansOneOfIt() {
            Assert.True(DevMutationArgs.TryResolveIdAndCount("5", "NPC id",
                out int id, out int count, out string problem), problem);

            Assert.Equal(5, id);
            Assert.Equal(1, count);
        }

        [Fact]
        public void AnIdAndACountAreReadAsWritten() {
            Assert.True(DevMutationArgs.TryResolveIdAndCount(" 5 , 3 ", "NPC id",
                out int id, out int count, out string problem), problem);

            Assert.Equal(5, id);
            Assert.Equal(3, count);
        }

        /// <summary>
        /// THE ONE VALUE THAT WOULD HAVE SUCCEEDED AND DONE NOTHING. Zero parses,
        /// is in range, and means "no item" and "no NPC" in the two id spaces
        /// these verbs write into - so without this it is the only argument that
        /// reports OK against a world it did not change.
        /// </summary>
        [Fact]
        public void ZeroIsRefusedBecauseItIsHowTerrariaSpellsNothing() {
            Assert.False(DevMutationArgs.TryResolveIdAndCount("0", "item id",
                out _, out _, out string problem));

            Assert.Contains("nothing", problem);

            // POSITIVE CONTROL: one, the very next number, is fine.
            Assert.True(DevMutationArgs.TryResolveIdAndCount("1", "item id",
                out _, out _, out _));
        }

        [Theory]
        [InlineData("")]
        [InlineData("   ")]
        [InlineData("abc")]
        [InlineData("-1")]
        [InlineData("+2")]
        [InlineData("1.5")]
        [InlineData("5,")]
        [InlineData("5,0")]
        [InlineData("5,-2")]
        [InlineData("5,3,7")]
        public void WhatIsNotAnIdAndACountIsRefused(string written) {
            Assert.False(DevMutationArgs.TryResolveIdAndCount(written, "NPC id",
                out _, out _, out string problem),
                "\"" + written + "\" was accepted");

            Assert.False(string.IsNullOrWhiteSpace(problem),
                "refused without saying why, which is a timeout wearing a " +
                "different hat");
        }

        /// <summary>
        /// An empty argument is refused by the shape rule rather than by the
        /// number rule, and the difference is the whole reason that branch
        /// exists.
        ///
        /// Without it, "" still fails - one line later, as "\"\" is not a
        /// positive whole NPC id", which tells somebody who wrote nothing that
        /// nothing is not a number. The branch buys the sentence that says what
        /// to write instead, so the sentence is what this pins. Found by
        /// mutation: deleting the branch left every other test here green.
        /// </summary>
        [Fact]
        public void AnEmptyArgumentIsToldWhatToWriteRatherThanWhatIsWrong() {
            Assert.False(DevMutationArgs.TryResolveIdAndCount("  ", "NPC id",
                out _, out _, out string problem));

            Assert.Contains("<id>", problem);
            Assert.Contains("<count>", problem);
        }

        [Fact]
        public void TheRefusalNamesWhichIdSpaceItMeant() {
            Assert.False(DevMutationArgs.TryResolveIdAndCount("abc", "item id",
                out _, out _, out string problem));

            Assert.Contains("item id", problem);

            // The two verbs share this shape and have completely different id
            // spaces, so a message saying only "id" sends the reader to look up
            // the wrong list.
            Assert.DoesNotContain("NPC", problem);
        }

        [Fact]
        public void ACountPastTheLimitIsRefusedAndTheLimitIsNamed() {
            Assert.False(DevMutationArgs.TryResolveIdAndCount(
                "1," + (DevMutationArgs.MaxCount + 1), "NPC id",
                out _, out _, out string problem));

            Assert.Contains(DevMutationArgs.MaxCount.ToString(), problem);

            // POSITIVE CONTROL: the limit itself is allowed, so this is a
            // boundary rather than an off-by-one refusing the documented cap.
            Assert.True(DevMutationArgs.TryResolveIdAndCount(
                "1," + DevMutationArgs.MaxCount, "NPC id", out _, out int count, out _));
            Assert.Equal(DevMutationArgs.MaxCount, count);
        }

        // ---- places --------------------------------------------------------

        [Fact]
        public void SpawnIsAPlaceWithoutCoordinates() {
            Assert.True(DevMutationArgs.TryResolvePlace("spawn", out bool atSpawn,
                out _, out _, out string problem), problem);

            Assert.True(atSpawn);
        }

        [Fact]
        public void APairOfNumbersIsTileCoordinates() {
            Assert.True(DevMutationArgs.TryResolvePlace("100,250", out bool atSpawn,
                out int x, out int y, out string problem), problem);

            Assert.False(atSpawn);
            Assert.Equal(100, x);
            Assert.Equal(250, y);
        }

        [Theory]
        [InlineData("100")]
        [InlineData("100,")]
        [InlineData("-1,2")]
        [InlineData("a,b")]
        [InlineData("100,200,300")]
        [InlineData("")]
        public void WhatIsNeitherSpawnNorAPairIsRefused(string written) {
            Assert.False(DevMutationArgs.TryResolvePlace(written, out _, out _,
                out _, out string problem),
                "\"" + written + "\" was accepted");

            Assert.False(string.IsNullOrWhiteSpace(problem));
        }

        // ---- which side may do what ----------------------------------------

        /// <summary>
        /// Every verb needs exactly one kind of authority. A verb needing both
        /// could never run anywhere in multiplayer, and one needing neither has
        /// no reason to be in this file.
        /// </summary>
        [Theory]
        [InlineData(DevMutationArgs.Time)]
        [InlineData(DevMutationArgs.Weather)]
        [InlineData(DevMutationArgs.Spawn)]
        [InlineData(DevMutationArgs.Give)]
        [InlineData(DevMutationArgs.Teleport)]
        public void EveryVerbNeedsExactlyOneKindOfAuthority(string verb) {
            bool world = DevMutationArgs.NeedsWorldAuthority(verb);
            bool local = DevMutationArgs.NeedsLocalPlayer(verb);

            Assert.True(world ^ local,
                verb + " needs world=" + world + " local=" + local);
        }

        [Fact]
        public void SingleplayerIsBothSidesAndMayDoEverything() {
            foreach (string verb in AllVerbs) {
                Assert.Null(DevMutationArgs.SideProblem(verb,
                    dedicatedServer: false, multiplayerClient: false));
            }
        }

        [Fact]
        public void AClientMayNotChangeWhatTheServerOwns() {
            string problem = DevMutationArgs.SideProblem(DevMutationArgs.Time,
                dedicatedServer: false, multiplayerClient: true);

            Assert.NotNull(problem);

            // The refusal has to name the other side. A client that set the
            // clock would be corrected by the next world packet, so the change
            // appears to work and then undoes itself - and a refusal that does
            // not say where to send it instead just costs the round trip
            // without buying anything.
            Assert.Contains("server", problem, StringComparison.OrdinalIgnoreCase);

            // POSITIVE CONTROL: the same verb on the server side is allowed, so
            // this is about the side rather than about the verb.
            Assert.Null(DevMutationArgs.SideProblem(DevMutationArgs.Time,
                dedicatedServer: true, multiplayerClient: false));
        }

        [Fact]
        public void ADedicatedServerMayNotDoWhatNeedsAPlayer() {
            string problem = DevMutationArgs.SideProblem(DevMutationArgs.Give,
                dedicatedServer: true, multiplayerClient: false);

            Assert.NotNull(problem);
            Assert.Contains("client", problem, StringComparison.OrdinalIgnoreCase);

            Assert.Null(DevMutationArgs.SideProblem(DevMutationArgs.Give,
                dedicatedServer: false, multiplayerClient: true));
        }

        /// <summary>
        /// The whole table at once, so a verb added later cannot quietly land in
        /// neither column.
        /// </summary>
        [Fact]
        public void EveryVerbIsRefusedOnExactlyOneOfTheTwoMultiplayerSides() {
            foreach (string verb in AllVerbs) {
                bool onClient = DevMutationArgs.SideProblem(verb, false, true) != null;
                bool onServer = DevMutationArgs.SideProblem(verb, true, false) != null;

                Assert.True(onClient ^ onServer,
                    verb + " refused on client=" + onClient + " server=" + onServer);
            }
        }

        // ---- the names are addressable at all ------------------------------

        /// <summary>
        /// Registered into a REAL registry rather than compared as strings.
        ///
        /// DevCommandRegistry refuses a name containing ':' or '@' because the
        /// payload cannot carry one - a verb called "set:time" would be split
        /// into a verb and an argument and could never be delivered under the
        /// name it registered. That rule is the one place a bad verb name is
        /// catchable, and this is the only test on this side that can run it
        /// against every one of them.
        /// </summary>
        [Fact]
        public void EveryVerbIsANameTheTriggerCanActuallyCarry() {
            var registry = new DevCommandRegistry();

            foreach (string verb in AllVerbs) {
                registry.Register(verb, true, "under test", _ => { });
            }

            Assert.Equal(AllVerbs.Length, registry.Count);

            foreach (string verb in AllVerbs) {
                Assert.True(registry.TryResolve(verb, out _), verb + " did not resolve");
            }
        }

        // ---- areas, for the tile query --------------------------------------

        [Fact]
        public void ARectangleIsFourWholeNumbers() {
            Assert.True(DevMutationArgs.TryResolveArea("10,20,4,5", out int x, out int y,
                out int w, out int h, out string problem), problem);

            Assert.Equal(10, x);
            Assert.Equal(20, y);
            Assert.Equal(4, w);
            Assert.Equal(5, h);
        }

        [Theory]
        [InlineData("10,20,4")]
        [InlineData("10,20,4,5,6")]
        [InlineData("10,20,-4,5")]
        [InlineData("a,b,c,d")]
        [InlineData("")]
        public void WhatIsNotARectangleIsRefused(string written) {
            Assert.False(DevMutationArgs.TryResolveArea(written, out _, out _, out _,
                out _, out string problem), "\"" + written + "\" was accepted");

            Assert.False(string.IsNullOrWhiteSpace(problem));
        }

        /// <summary>
        /// A rectangle with a zero side is not a small query, it is no query -
        /// and it would report "0 tiles, 0 distinct types", which reads exactly
        /// like a region that exists and is empty.
        /// </summary>
        [Theory]
        [InlineData("10,20,0,5")]
        [InlineData("10,20,4,0")]
        public void ARectangleWithNoAreaIsRefusedRatherThanAnsweredWithNothing(string written) {
            Assert.False(DevMutationArgs.TryResolveArea(written, out _, out _, out _,
                out _, out string problem));

            Assert.Contains("no tiles", problem);
        }

        /// <summary>
        /// The smallest Terraria world is 4200 by 1200 tiles, so an unbounded
        /// query is a request to walk five million of them and build a string
        /// out of the answer.
        /// </summary>
        [Fact]
        public void AnAreaPastTheLimitIsRefusedAndTheLimitIsNamed() {
            int side = 1;
            while (side * side <= DevMutationArgs.MaxArea) {
                side++;
            }

            Assert.False(DevMutationArgs.TryResolveArea(
                "0,0," + side + "," + side, out _, out _, out _, out _,
                out string problem));

            Assert.Contains(DevMutationArgs.MaxArea.ToString(), problem);

            // POSITIVE CONTROL: the limit itself is allowed, so this is a
            // boundary rather than an off-by-one refusing the documented cap.
            Assert.True(DevMutationArgs.TryResolveArea(
                "0,0," + DevMutationArgs.MaxArea + ",1", out _, out _, out _, out _,
                out _));
        }

        /// <summary>
        /// The cap must survive 32-bit arithmetic, because a product that wraps
        /// is precisely the request the cap exists to stop: 65536 x 65536 IS
        /// zero as an int, and 46341 x 46341 is just past 2^31 and negative -
        /// both sailed under the limit and asked the game to walk the whole
        /// world.
        /// </summary>
        [Theory]
        [InlineData("0,0,65536,65536")]
        [InlineData("0,0,46341,46341")]
        [InlineData("0,0,2000000000,2000000000")]
        public void AnAreaWhoseProductOverflowsIsStillPastTheLimit(string written) {
            Assert.False(DevMutationArgs.TryResolveArea(written, out _, out _,
                out _, out _, out string problem),
                "\"" + written + "\" was accepted - the cap computed its product " +
                "in 32 bits");

            Assert.Contains(DevMutationArgs.MaxArea.ToString(), problem);
        }

        // ---- kinds and areas, for the entity query ---------------------------

        [Theory]
        [InlineData("npc", DevMutationArgs.EntityNpc)]
        [InlineData("item", DevMutationArgs.EntityItem)]
        [InlineData("projectile", DevMutationArgs.EntityProjectile)]
        [InlineData("PROJECTILE", DevMutationArgs.EntityProjectile)]
        [InlineData("  Npc  ", DevMutationArgs.EntityNpc)]
        public void AKindOnItsOwnMeansEverywhere(string written, string expected) {
            Assert.True(DevMutationArgs.TryResolveEntityQuery(written, out string kind,
                out bool everywhere, out _, out _, out _, out _, out string problem),
                problem);

            Assert.Equal(expected, kind);
            Assert.True(everywhere, "a kind with no rectangle did not mean everywhere");
        }

        [Fact]
        public void AKindCanNameARectangleToLookIn() {
            Assert.True(DevMutationArgs.TryResolveEntityQuery("npc,10,20,4,5",
                out string kind, out bool everywhere, out int x, out int y,
                out int w, out int h, out string problem), problem);

            Assert.Equal(DevMutationArgs.EntityNpc, kind);
            Assert.False(everywhere, "a rectangle was given and ignored");
            Assert.Equal(10, x);
            Assert.Equal(20, y);
            Assert.Equal(4, w);
            Assert.Equal(5, h);
        }

        /// <summary>
        /// The kinds have separate id spaces, so a query answered about the
        /// wrong one comes back as a plausible list of numbers meaning nothing.
        /// The refusal lists the three, which is what makes it one round trip
        /// rather than a guess.
        /// </summary>
        [Theory]
        [InlineData("mob")]
        [InlineData("npcs")]
        [InlineData("player")]
        [InlineData("")]
        public void AnUnknownKindIsRefusedByNamingTheOnesThatExist(string written) {
            Assert.False(DevMutationArgs.TryResolveEntityQuery(written, out _, out _,
                out _, out _, out _, out _, out string problem),
                "\"" + written + "\" was accepted as a kind");

            Assert.Contains(DevMutationArgs.EntityNpc, problem);
            Assert.Contains(DevMutationArgs.EntityItem, problem);
            Assert.Contains(DevMutationArgs.EntityProjectile, problem);
        }

        [Theory]
        [InlineData("npc,10,20")]
        [InlineData("npc,10,20,4")]
        [InlineData("npc,10,20,4,5,6")]
        [InlineData("npc,a,b,c,d")]
        [InlineData("npc,10,20,-4,5")]
        public void HalfARectangleIsRefusedRatherThanTreatedAsNone(string written) {
            Assert.False(DevMutationArgs.TryResolveEntityQuery(written, out _, out _,
                out _, out _, out _, out _, out string problem),
                "\"" + written + "\" was accepted");

            Assert.False(string.IsNullOrWhiteSpace(problem));
        }

        [Theory]
        [InlineData("npc,10,20,0,5")]
        [InlineData("npc,10,20,4,0")]
        public void AnEntityRectangleWithNoAreaIsRefusedToo(string written) {
            Assert.False(DevMutationArgs.TryResolveEntityQuery(written, out _, out _,
                out _, out _, out _, out _, out string problem));

            Assert.Contains("no tiles", problem);
        }

        /// <summary>
        /// THE ASYMMETRY WITH THE TILE QUERY, pinned so nobody "fixes" it into
        /// consistency. A tile query pays per tile, so an unbounded rectangle is
        /// five million visits and is capped. An entity query walks a few
        /// hundred fixed array slots whatever rectangle it is handed, so the
        /// same rectangle is free and capping it would refuse a query that costs
        /// nothing.
        ///
        /// Both halves are asserted together: the entity query accepting a
        /// rectangle the tile query refuses is the whole claim, and either half
        /// alone would pass against code that had no cap anywhere.
        /// </summary>
        [Fact]
        public void ARectangleTooBigToScanTileByTileIsStillFreeToFilterEntitiesBy() {
            int side = 1;
            while (side * side <= DevMutationArgs.MaxArea) {
                side++;
            }

            string rectangle = "0,0," + side + "," + side;

            Assert.True(DevMutationArgs.TryResolveEntityQuery("npc," + rectangle,
                out _, out bool everywhere, out _, out _, out int w, out int h,
                out string problem), problem);

            Assert.False(everywhere);
            Assert.Equal(side, w);
            Assert.Equal(side, h);

            // THE OTHER HALF: the identical rectangle is refused for tiles. If
            // this ever passes, the cap has been deleted rather than scoped and
            // the test above stopped meaning anything.
            Assert.False(DevMutationArgs.TryResolveArea(rectangle, out _, out _,
                out _, out _, out _),
                "the tile query stopped capping, so the asymmetry above is vacuous");
        }

        // ---- filling and clearing tiles --------------------------------------

        [Fact]
        public void ATileFillIsARectangleAndAType() {
            Assert.True(DevMutationArgs.TryResolveTileFill("10,20,4,5,53", out int x,
                out int y, out int w, out int h, out int type, out string problem),
                problem);

            Assert.Equal(10, x);
            Assert.Equal(20, y);
            Assert.Equal(4, w);
            Assert.Equal(5, h);
            Assert.Equal(53, type);
        }

        /// <summary>
        /// THE ONE PLACE THE ID RULES DIVERGE. `spawn` and `give` refuse 0
        /// because it is how Terraria spells "nothing" in their id spaces. In
        /// the TILE space 0 is Dirt, so refusing it would refuse the commonest
        /// block in the game.
        ///
        /// Both halves are asserted together: without the second, a parser that
        /// accepted 0 everywhere would pass the first.
        /// </summary>
        [Fact]
        public void ZeroIsDirtHereEvenThoughItIsNothingForSpawnAndGive() {
            Assert.True(DevMutationArgs.TryResolveTileFill("10,20,1,1,0", out _, out _,
                out _, out _, out int type, out string problem), problem);
            Assert.Equal(0, type);

            Assert.False(DevMutationArgs.TryResolveIdAndCount("0", "NPC id", out _,
                out _, out _),
                "0 became acceptable as an NPC id, so the contrast above is vacuous");
        }

        [Theory]
        [InlineData("10,20,4,5")]
        [InlineData("10,20,4,5,6,7")]
        [InlineData("10,20,4,5,x")]
        [InlineData("10,20,4,5,-1")]
        [InlineData("10,20,0,5,53")]
        [InlineData("")]
        public void WhatIsNotARectangleAndATypeIsRefused(string written) {
            Assert.False(DevMutationArgs.TryResolveTileFill(written, out _, out _,
                out _, out _, out _, out string problem),
                "\"" + written + "\" was accepted");

            Assert.False(string.IsNullOrWhiteSpace(problem));
        }

        /// <summary>
        /// Placing PAYS per tile, so it is capped exactly as scanning is - and
        /// unlike the entity query, whose rectangle only filters.
        /// </summary>
        [Fact]
        public void AFillPastTheLimitIsRefusedAndTheLimitIsNamed() {
            int side = 1;
            while (side * side <= DevMutationArgs.MaxArea) {
                side++;
            }

            Assert.False(DevMutationArgs.TryResolveTileFill(
                "0,0," + side + "," + side + ",53", out _, out _, out _, out _, out _,
                out string problem));

            Assert.Contains(DevMutationArgs.MaxArea.ToString(), problem);

            // POSITIVE CONTROL: the limit itself is allowed.
            Assert.True(DevMutationArgs.TryResolveTileFill(
                "0,0," + DevMutationArgs.MaxArea + ",1,53", out _, out _, out _, out _,
                out _, out _));
        }

        /// <summary>
        /// The fill's cap survives overflow for the scan's reason, and it is
        /// the worse of the two to lose: `settile:0,0,65536,65536,0` wrapped to
        /// an area of zero and rewrote the whole world as dirt.
        /// </summary>
        [Theory]
        [InlineData("0,0,65536,65536,0")]
        [InlineData("0,0,46341,46341,53")]
        public void AFillWhoseProductOverflowsIsStillPastTheLimit(string written) {
            Assert.False(DevMutationArgs.TryResolveTileFill(written, out _, out _,
                out _, out _, out _, out string problem),
                "\"" + written + "\" was accepted - the cap computed its product " +
                "in 32 bits");

            Assert.Contains(DevMutationArgs.MaxArea.ToString(), problem);
        }

        // ---- despawning ------------------------------------------------------

        [Theory]
        [InlineData("all")]
        [InlineData("ALL")]
        [InlineData("  all  ")]
        public void AllIsEveryNpcRatherThanATypeCalledAll(string written) {
            Assert.True(DevMutationArgs.TryResolveDespawn(written, out bool everything,
                out _, out string problem), problem);

            Assert.True(everything);
        }

        [Fact]
        public void ADespawnCanNameOneType() {
            Assert.True(DevMutationArgs.TryResolveDespawn("42", out bool everything,
                out int type, out string problem), problem);

            Assert.False(everything);
            Assert.Equal(42, type);
        }

        [Theory]
        [InlineData("0")]
        [InlineData("-1")]
        [InlineData("everything")]
        [InlineData("")]
        public void WhatIsNeitherAllNorAnIdIsRefused(string written) {
            Assert.False(DevMutationArgs.TryResolveDespawn(written, out _, out _,
                out string problem), "\"" + written + "\" was accepted");

            Assert.False(string.IsNullOrWhiteSpace(problem));
        }

        // ---- the detail query ------------------------------------------------

        [Theory]
        [InlineData("npc", DevMutationArgs.EntityNpc)]
        [InlineData("ITEM", DevMutationArgs.EntityItem)]
        [InlineData("  projectile ", DevMutationArgs.EntityProjectile)]
        public void AFindCanBeAKindOnItsOwn(string written, string expected) {
            Assert.True(DevMutationArgs.TryResolveFind(written, out string kind,
                out bool anyType, out _, out string problem), problem);

            Assert.Equal(expected, kind);
            Assert.True(anyType, "a kind with no id did not mean every type");
        }

        [Fact]
        public void AFindCanNameOneType() {
            Assert.True(DevMutationArgs.TryResolveFind("npc,4", out string kind,
                out bool anyType, out int type, out string problem), problem);

            Assert.Equal(DevMutationArgs.EntityNpc, kind);
            Assert.False(anyType);
            Assert.Equal(4, type);
        }

        [Theory]
        [InlineData("mob")]
        [InlineData("npc,4,5")]
        [InlineData("npc,x")]
        [InlineData("npc,0")]
        [InlineData("npc,-1")]
        [InlineData("")]
        public void WhatIsNotAKindAndAnIdIsRefused(string written) {
            Assert.False(DevMutationArgs.TryResolveFind(written, out _, out _, out _,
                out string problem), "\"" + written + "\" was accepted");

            Assert.False(string.IsNullOrWhiteSpace(problem));
        }

        /// <summary>
        /// The counting query and the detail query resolve a kind through ONE
        /// function, so they cannot come to disagree about what `item` means.
        /// </summary>
        [Theory]
        [InlineData("npc")]
        [InlineData("item")]
        [InlineData("projectile")]
        [InlineData("mob")]
        [InlineData("")]
        public void BothQueriesAgreeAboutWhatIsAKind(string written) {
            bool counting = DevMutationArgs.TryResolveEntityQuery(written,
                out string countedKind, out _, out _, out _, out _, out _, out _);
            bool detail = DevMutationArgs.TryResolveFind(written, out string foundKind,
                out _, out _, out _);

            Assert.Equal(counting, detail);
            Assert.Equal(countedKind, foundKind);
        }

        // ---- the opt-in, read rather than run -------------------------------

        private static readonly string[] AllVerbs = {
            DevMutationArgs.Time,
            DevMutationArgs.Weather,
            DevMutationArgs.Spawn,
            DevMutationArgs.Give,
            DevMutationArgs.Teleport,
            DevMutationArgs.SetTile,
            DevMutationArgs.ClearTile,
            DevMutationArgs.Despawn,
        };

        private static string RepoRoot() {
            string dir = Directory.GetCurrentDirectory();
            while (dir != null && !Directory.Exists(Path.Combine(dir, "responder"))) {
                dir = Directory.GetParent(dir)?.FullName;
            }

            Assert.True(dir != null, "no responder/ above " + Directory.GetCurrentDirectory());
            return dir;
        }

        private static string Source(string file) {
            string path = Path.Combine(RepoRoot(), "responder", file);
            Assert.True(File.Exists(path), "expected " + path);
            return File.ReadAllText(path);
        }

        /// <summary>POSITIVE CONTROL for the scans below.</summary>
        [Fact]
        public void TheSourcesAreActuallyRead() {
            Assert.Contains("namespace TModLoaderMcp.DevBridge", Source("DevMutations.cs"));
            Assert.Contains("namespace TModLoaderMcp.DevBridge", Source("DevResponder.cs"));
        }

        /// <summary>
        /// SetTile checks the live tile ceiling the way spawn and give check
        /// theirs - and this one is load-bearing rather than merely polite:
        /// PlaceTile indexes arrays sized to TileLoader.TileCount, so an id
        /// past the end is an IndexOutOfRangeException out of the dispatch,
        /// not a refusal. A source scan because DevMutations imports Terraria
        /// and cannot be on this compile line; the argument layer cannot know
        /// the ceiling, so the check can only live in the applier.
        /// </summary>
        [Fact]
        public void SetTileChecksTheLiveTileCeiling() {
            Assert.Contains("TileLoader.TileCount", Source("DevMutations.cs"));
        }

        /// <summary>
        /// The tile sync sends the CLAMPED rectangle, in chunks. Unclamped, an
        /// edge fill made the server serialise out-of-world tiles through
        /// 1.4's unchecked Tilemap indexer; unchunked, a legal 16384x1 fill
        /// exceeded the byte-sized dimensions packet 20 carries.
        /// </summary>
        [Fact]
        public void TheTileSyncClampsAndChunks() {
            string src = Source("DevMutations.cs");

            Assert.Contains("SyncSquare", src);
            Assert.Contains("private const int SyncSquare", src);
        }

        /// <summary>
        /// THE SAFETY PROPERTY OF THIS WHOLE FILE.
        ///
        /// Everything else in responder/ reads. These change the world a
        /// developer is sitting in, so a mod that updates its vendored copy must
        /// not silently gain the power to spawn enemies into somebody's save.
        /// The only thing standing between that and this is DevResponder never
        /// mentioning DevMutations, which is a fact about a file and therefore
        /// checkable without a game.
        /// </summary>
        [Theory]
        [InlineData("DevMutations", "the world-changing verbs")]
        [InlineData("DevCommandBridge", "the ability to run any mod's commands")]
        [InlineData("DevChat", "a listener on chat, and a way to speak into it")]
        public void DevResponderDoesNotRegisterAnOptInClassForYou(string name, string what) {
            var offenders = new List<string>();

            foreach (string line in Source("DevResponder.cs").Split('\n')) {
                string bare = line.TrimStart();
                if (bare.StartsWith("//") || bare.StartsWith("///")) {
                    continue;
                }

                int comment = line.IndexOf("//", StringComparison.Ordinal);
                string codeOnly = comment >= 0 ? line.Substring(0, comment) : line;

                // `name` and not a prefix of it: DevResponder legitimately names
                // DevMutationArgs for the tile query's area rule, and that class
                // registers nothing. Only an exact mention is an opt-in leaking
                // into the base.
                foreach (string token in codeOnly.Split(
                        new[] { ' ', '\t', '.', '(', ')', ';', ',', '=', '>' },
                        StringSplitOptions.RemoveEmptyEntries)) {
                    if (token == name) {
                        offenders.Add(line.Trim());
                    }
                }
            }

            Assert.True(offenders.Count == 0,
                "DevResponder names " + name + " in code, so a mod vendoring this " +
                "folder would get " + what + " without asking: " +
                string.Join(" | ", offenders));
        }

        /// <summary>
        /// CONTROL for the scan above. Every name it checks has to be a name
        /// that EXISTS - a typo would make the check pass against a folder that
        /// registers everything for you, which is the state it exists to catch.
        /// </summary>
        [Theory]
        [InlineData("DevMutations")]
        [InlineData("DevCommandBridge")]
        [InlineData("DevChat")]
        public void EachOptInClassIsRealAndTakesTheReportChannel(string name) {
            string code = Source(name + ".cs");

            Assert.Contains("public static class " + name, code);
            Assert.Matches(
                new Regex(@"public\s+static\s+void\s+RegisterInto\s*\(\s*DevCommandRegistry\s+\w+\s*,\s*Action<string>\s+\w+\s*\)"),
                code);
        }

        /// <summary>
        /// The applier registers the verbs the rules file names, and takes the
        /// report channel as an argument.
        ///
        /// Both are read rather than run: DevMutations imports Terraria and
        /// cannot compile here. The scan cannot see a reference made by
        /// reflection or built from strings; neither appears in that file.
        /// </summary>
        [Fact]
        public void TheApplierRegistersEveryVerbTheRulesFileNames() {
            string code = Source("DevMutations.cs");

            // DERIVED, not listed again. This test used to spell out five
            // constants by hand, so three verbs added to AllVerbs later were
            // never checked here at all - found by mutation, which deleted a
            // registration and watched every test stay green. Reflection maps
            // each verb VALUE back to the constant NAME the applier registers
            // it under, which is the step a hand-written list keeps getting
            // wrong.
            foreach (string verb in AllVerbs) {
                string constant = ConstantNameFor(verb);

                Assert.Matches(
                    new Regex(@"r\.Register\(\s*DevMutationArgs\." + constant + @"\s*,"),
                    code);
            }
        }

        /// <summary>The name of the DevMutationArgs constant holding this verb.</summary>
        private static string ConstantNameFor(string verb) {
            foreach (FieldInfo field in typeof(DevMutationArgs).GetFields(
                    BindingFlags.Public | BindingFlags.Static)) {
                if (field.IsLiteral && (field.GetRawConstantValue() as string) == verb) {
                    return field.Name;
                }
            }

            throw new Xunit.Sdk.XunitException(
                "no DevMutationArgs constant holds the verb \"" + verb + "\", so " +
                "the applier cannot be registering it by name");
        }

        [Fact]
        public void RegisteringTakesTheReportChannelRatherThanReachingForIt() {
            Assert.Matches(
                new Regex(@"public\s+static\s+void\s+RegisterInto\s*\(\s*DevCommandRegistry\s+\w+\s*,\s*Action<string>\s+\w+\s*\)"),
                Source("DevMutations.cs"));
        }
    }
}
