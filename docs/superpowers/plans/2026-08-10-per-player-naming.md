# Per-Player Artifact Naming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two developers on one machine, or two clients on one server, stop
sharing every answer file — each client's heartbeat, diag, capture and shot
carry its player token.

**Architecture:** Requests stay shared and addressed; only ANSWERS gain the
player. The token rule is computed independently on both sides from a name each
already holds — `Main.LocalPlayer.name` in C#, `Session.player` in Python — so
the two implementations are pinned together by one shared table of vectors
rather than by trust. A client with no character yet keeps writing the
unsuffixed heartbeat, because that file is what `launch` reads to tell "live, no
world" from "absent".

**Tech Stack:** Python 3.12+ / pytest / ruff; C# .NET 8 / xunit; tModLoader
`ModSystem`; Windows `dotnet.exe` driving WSL paths.

## Global Constraints

- `DOTNET="/mnt/c/Program Files/dotnet/dotnet.exe"` — there is no `dotnet` in
  WSL. The CI job keeps the bare command.
- Python is `.venv/bin/python -m pytest` — bare `python` is miniconda and cannot
  import the package.
- `pytest -q` prints NO summary line: `pyproject.toml` already sets `addopts =
"-q"`, so a second `-q` makes it `-qq`. Run bare `pytest` when you need counts.
- **Token rule, exact:** lowercase; every run of non-alphanumeric characters
  becomes a single `-`; leading and trailing `-` trimmed; then `-` plus the
  first four hex characters of the MD5 of the ORIGINAL name's UTF-8 bytes.
- **Token grammar, exact:** `[a-z0-9][a-z0-9-]*-[0-9a-f]{4}`. It must not begin
  with `-`, or an empty slug would parse as a valid token. Defined ONCE as
  `triggers.PLAYER_TOKEN_GRAMMAR` and referenced by every regex that embeds it —
  two hand-written copies are two grammars, and they will diverge.
- **A name with no alphanumerics** (`!!!`) slugs to the empty string. The token
  is then `player-<digest>`, NOT the bare digest: a bare digest does not match
  the grammar above, and a token the grammar rejects is invisible to `captures`
  and to heartbeat discovery.
- **Computed vectors** (recomputed 2026-08-10; do not trust recall, these were
  fabricated once): `n43n → n43n-003f`, `Big Bird → big-bird-44a3`,
  `BigBird → bigbird-ca4c`.
- The hash is ALWAYS present, never only on collision — one branch on both
  sides rather than two that must agree about when to disambiguate.
- A dedicated server has no player and keeps the existing `-server` suffix
  unchanged. `<mod>-capture.trigger` and `<mod>-commands.txt` do NOT gain a
  token.
- `responder/*.cs` and Biomancy's `Common/DevBridge/vendored/*.cs` must stay
  BYTE-IDENTICAL. Any C# change means re-copying and regenerating
  `responder/SHA256SUMS`, which `tests/test_vendor_manifest.py` enforces
  upstream and `VendoredIntegrityTests` enforces in Biomancy.
- Every new test is run against the pre-change state first and seen to FAIL for
  its stated reason. Every negative assertion carries a positive control in the
  same test.

---

## File Structure

| File                                  | Responsibility                                    |
| ------------------------------------- | ------------------------------------------------- |
| `src/tmodloader_mcp/triggers.py`      | `player_token()`, `Artifacts` gains a player      |
| `src/tmodloader_mcp/session.py`       | threads `self.player` into `path()`               |
| `src/tmodloader_mcp/captures.py`      | `capture_pattern` gains the token                 |
| `src/tmodloader_mcp/heartbeat.py`     | discovering many heartbeat files                  |
| `src/tmodloader_mcp/server.py`        | `HeartbeatOut` becomes a list of clients          |
| `responder/DevArtifacts.cs`           | `PlayerToken()` + per-player naming               |
| `responder/DevResponder.cs`           | uses the token for answers, not for triggers      |
| `docs/MOD_CONTRACT.md`                | the rule, the grammar, limitation section removed |
| `tests/test_player_token.py`          | the shared vector table, Python side              |
| `responder/tests/PlayerTokenTests.cs` | the same table, C# side                           |

---

### Task 1: The token rule, both languages, one table

**Files:**

- Create: `tests/test_player_token.py`
- Create: `responder/tests/PlayerTokenTests.cs`
- Modify: `src/tmodloader_mcp/triggers.py`
- Modify: `responder/DevArtifacts.cs`
- Modify: `responder/tests/Responder.Tests.csproj`

**Interfaces:**

- Produces: `triggers.player_token(name: str) -> str` and
  `DevArtifacts.PlayerToken(string name)` returning `string`. Both return
  `null`/`None` for a null-or-empty name — callers use that to mean "no
  character yet".

- [ ] **Step 1: Write the failing Python test**

```python
"""One rule, computed twice, in two languages that cannot import each other.

The table is the only thing that would catch them disagreeing. It is duplicated
verbatim in responder/tests/PlayerTokenTests.cs on purpose: a shared fixture
file would be one more thing to vendor, and the point is that both sides agree
about VALUES, not that they read one file.
"""

import pytest

from tmodloader_mcp.triggers import player_token

# Recomputed, not illustrative. `Big Bird` and `BigBird` are the pair that
# proves the hash is load-bearing: slugging alone collapses them into one token
# and lands back in the shared-file bug this whole change exists to remove.
VECTORS = [
    ("n43n", "n43n-003f"),
    ("Big Bird", "big-bird-44a3"),
    ("BigBird", "bigbird-ca4c"),
]


@pytest.mark.parametrize("name,expected", VECTORS)
def test_the_token_for_a_name_is_exactly_this(name, expected):
    assert player_token(name) == expected


def test_two_names_that_slug_alike_still_differ():
    # The positive control for the assertion above it: both produce a token at
    # all, so this cannot pass because the function returned None twice.
    a, b = player_token("Big Bird"), player_token("BigBird")
    assert a and b
    assert a != b


def test_a_name_with_no_alphanumerics_still_produces_a_usable_token():
    # "!!!" slugs to the empty string. Without care that yields a filename
    # fragment starting with `-`, or worse an empty one that collides with
    # every other unusable name.
    token = player_token("!!!")
    assert token
    assert not token.startswith("-")


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_no_character_yet_has_no_token(empty):
    assert player_token(empty) is None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_player_token.py`
Expected: FAIL — `ImportError: cannot import name 'player_token'`.

- [ ] **Step 3: Implement it in `triggers.py`**

Add near the top of the module, above `class Artifacts`:

```python
import hashlib
import re

#: A slug plus four hex of MD5. Pinned here as a constant because
#: `captures.capture_pattern` has to embed the SAME grammar to stay
#: unambiguous against the three-digit index that follows it, and two
#: hand-written copies of a regex are two regexes.
PLAYER_TOKEN_GRAMMAR = r"[a-z0-9][a-z0-9-]*-[0-9a-f]{4}"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def player_token(name: str | None) -> str | None:
    """A character name as a filename fragment, or None if there is no name.

    Lowercase, runs of non-alphanumerics collapsed to one `-`, trimmed, plus
    four hex of the MD5 of the ORIGINAL bytes.

    The hash is always present rather than only on collision. Adding it only
    when two names clash needs both sides to agree about WHEN, and they cannot
    see each other's players — the mod knows one name, the harness knows one
    name. A rule with one branch is a rule that cannot disagree.

    MD5 is a filename disambiguator, not a security boundary: nothing here
    depends on it being hard to collide deliberately.
    """
    if not name or not name.strip():
        return None
    slug = _NON_ALNUM.sub("-", name.lower()).strip("-")
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()[:4]
    # A name of pure punctuation slugs to "". `player` rather than nothing,
    # because the bare digest does not match PLAYER_TOKEN_GRAMMAR - and a token
    # the grammar rejects is a file `captures` and heartbeat discovery cannot
    # see, which is a silent disappearance rather than an error.
    return f"{slug or 'player'}-{digest}"
```

- [ ] **Step 4: Run the Python test and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_player_token.py`
Expected: PASS, 7 tests.

`player_token("!!!")` returns `player-<digest>`. Add this assertion to that test
rather than leaving it at "truthy" — the grammar is what makes the file findable,
so "produces something" is not the property that matters:

```python
    import re

    from tmodloader_mcp.triggers import PLAYER_TOKEN_GRAMMAR

    assert re.fullmatch(PLAYER_TOKEN_GRAMMAR, token)
```

- [ ] **Step 5: Write the failing C# test**

Create `responder/tests/PlayerTokenTests.cs`:

```csharp
using TModLoaderMcp.DevBridge;
using Xunit;

namespace Responder.Tests
{
	/// <summary>
	/// The same table as tests/test_player_token.py, deliberately duplicated.
	///
	/// The mod and the harness each compute this token from a name only they
	/// hold — Main.LocalPlayer.name here, Session.player there — and then have
	/// to open the same file. Nothing at runtime would notice them disagreeing:
	/// each would write and read its own name happily, and the harness would
	/// simply wait forever for an answer the mod had already written somewhere
	/// else. This table is the only place that disagreement is visible.
	/// </summary>
	public class PlayerTokenTests
	{
		[Theory]
		[InlineData("n43n", "n43n-003f")]
		[InlineData("Big Bird", "big-bird-44a3")]
		[InlineData("BigBird", "bigbird-ca4c")]
		public void TheTokenForANameIsExactlyThis(string name, string expected) {
			Assert.Equal(expected, DevArtifacts.PlayerToken(name));
		}

		[Fact]
		public void TwoNamesThatSlugAlikeStillDiffer() {
			var a = DevArtifacts.PlayerToken("Big Bird");
			var b = DevArtifacts.PlayerToken("BigBird");
			// Positive control in the same test: this cannot pass by both
			// being null.
			Assert.False(string.IsNullOrEmpty(a));
			Assert.False(string.IsNullOrEmpty(b));
			Assert.NotEqual(a, b);
		}

		[Fact]
		public void ANameWithNoAlphanumericsStillProducesAUsableToken() {
			var token = DevArtifacts.PlayerToken("!!!");
			Assert.False(string.IsNullOrEmpty(token));
			Assert.False(token.StartsWith("-"));
		}

		[Theory]
		[InlineData(null)]
		[InlineData("")]
		[InlineData("   ")]
		public void NoCharacterYetHasNoToken(string empty) {
			Assert.Null(DevArtifacts.PlayerToken(empty));
		}
	}
}
```

- [ ] **Step 6: Add it to the test project**

In `responder/tests/Responder.Tests.csproj`, ItemGroup 3 (the test files), add:

```xml
    <Compile Include="PlayerTokenTests.cs" />
```

- [ ] **Step 7: Run it and watch it fail**

Run: `"$DOTNET" test responder/tests/Responder.Tests.csproj`
Expected: FAIL to COMPILE — `DevArtifacts` does not contain `PlayerToken`. A
compile failure is the correct red here; there is no way to reference a method
that does not exist.

- [ ] **Step 8: Implement `PlayerToken` in `DevArtifacts.cs`**

Add `using System.Security.Cryptography;`, `using System.Text;` and
`using System.Globalization;` at the top if absent, then inside the class:

```csharp
		/// <summary>
		/// A character name as a filename fragment, or null if there is no name.
		///
		/// Lowercase, runs of non-alphanumerics collapsed to one '-', trimmed,
		/// plus four hex of the MD5 of the ORIGINAL bytes. Kept identical to
		/// triggers.player_token on the harness side; the vector table in
		/// PlayerTokenTests is what holds the two together, because nothing at
		/// runtime would notice them drifting apart - each side would write and
		/// read its own spelling and simply never meet.
		///
		/// The hash is always present rather than only on collision: adding it
		/// only when two names clash needs both sides to agree about WHEN, and
		/// neither can see the other's players.
		/// </summary>
		public static string PlayerToken(string name) {
			if (string.IsNullOrWhiteSpace(name)) {
				return null;
			}

			var slug = new StringBuilder();
			bool pendingDash = false;
			foreach (char raw in name.ToLowerInvariant()) {
				if ((raw >= 'a' && raw <= 'z') || (raw >= '0' && raw <= '9')) {
					if (pendingDash && slug.Length > 0) {
						slug.Append('-');
					}

					pendingDash = false;
					slug.Append(raw);
				}
				else {
					// Deferred rather than appended: this collapses a RUN to
					// one dash and drops leading and trailing ones without a
					// second trimming pass.
					pendingDash = true;
				}
			}

			// UTF-8 of the ORIGINAL name, not the slug - the slug is lossy and
			// is exactly what the hash exists to disambiguate.
			byte[] digest = MD5.HashData(Encoding.UTF8.GetBytes(name));
			var hex = new StringBuilder(4);
			for (int i = 0; i < 2; i++) {
				hex.Append(digest[i].ToString("x2", CultureInfo.InvariantCulture));
			}

			// "player" rather than nothing when the slug is empty: the bare
			// digest does not match the token grammar the harness matches
			// against, and a file that grammar rejects simply disappears.
			return (slug.Length > 0 ? slug.ToString() : "player") + "-" + hex;
		}
```

- [ ] **Step 9: Run the C# tests and watch them pass**

Run: `"$DOTNET" test responder/tests/Responder.Tests.csproj`
Expected: PASS. 137 existing + 8 new = 145.

- [ ] **Step 10: Prove the two languages actually agree**

Run this and confirm it prints three `MATCH` lines:

```bash
.venv/bin/python -c "
from tmodloader_mcp.triggers import player_token
for n in ['n43n', 'Big Bird', 'BigBird']:
    print(n, '->', player_token(n))
"
```

Compare against the C# `[InlineData]` rows by eye. They are the same three
strings in both files; if either side disagrees with the table, STOP — that is
the failure this task exists to make visible, not a test to adjust.

- [ ] **Step 11: Regenerate the manifest and commit**

```bash
cd responder && sha256sum *.cs > SHA256SUMS && cd ..
.venv/bin/python -m pytest --color=no | tail -1
git add -A
git commit -m "feat(naming): one token rule, computed in two languages"
```

---

### Task 2: Answers carry the player, requests do not

**Files:**

- Modify: `src/tmodloader_mcp/triggers.py` (`Artifacts`, `artifacts_for`)
- Modify: `src/tmodloader_mcp/session.py:217`
- Test: `tests/test_artifact_names.py`

**Interfaces:**

- Consumes: `triggers.player_token` from Task 1.
- Produces: `Artifacts(prefix: str, player: str | None)`;
  `artifacts_for(mod_name: str, player: str | None = None) -> Artifacts`.
  `trigger` and `commands` are unchanged; `result`, `diag`, `heartbeat` and
  `shot` gain `-<token>` before the extension when `player` is not None.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_artifact_names.py`:

```python
from tmodloader_mcp.triggers import artifacts_for


def test_answers_carry_the_player_and_requests_do_not():
    a = artifacts_for("Biomancy", player="n43n")
    # Requests are SHARED and addressed - the trigger is how a client is told
    # the request is not for it, so per-player triggers would remove the very
    # mechanism that already works.
    assert a.trigger == "biomancy-capture.trigger"
    # The verb set is the mod's and identical for every client.
    assert a.commands == "biomancy-commands.txt"

    assert a.diag == "biomancy-diag-n43n-003f.txt"
    assert a.heartbeat == "biomancy-hooks-n43n-003f.txt"
    assert a.result == "biomancy-capture-n43n-003f.txt"
    assert a.shot == "biomancy-shot-n43n-003f.png"


def test_the_token_goes_before_the_extension_not_after():
    # A name ending `.txt-n43n-003f` is not a text file to anything that reads
    # extensions, including the harness's own capture reader.
    a = artifacts_for("Biomancy", player="n43n")
    for name in (a.diag, a.heartbeat, a.result):
        assert name.endswith(".txt")
    assert a.shot.endswith(".png")


def test_no_player_reproduces_todays_names_exactly():
    # The dedicated server has no player, and a client has none before a
    # character exists. Both keep the old names, so this is also the proof that
    # the change is backward compatible where it has to be.
    a = artifacts_for("Biomancy")
    assert a.trigger == "biomancy-capture.trigger"
    assert a.result == "biomancy-capture.txt"
    assert a.diag == "biomancy-diag.txt"
    assert a.heartbeat == "biomancy-hooks.txt"
    assert a.shot == "biomancy-shot.png"
    assert a.commands == "biomancy-commands.txt"


def test_all_covers_every_artifact_including_the_per_player_ones():
    # `all` is what clears artifacts between runs. A name missing from it is a
    # stale file that survives a run and reads as this run's answer.
    a = artifacts_for("Biomancy", player="n43n")
    assert set(a.all) == {a.trigger, a.result, a.diag, a.heartbeat, a.shot, a.commands}
    assert len(a.all) == 6
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/test_artifact_names.py`
Expected: FAIL — `artifacts_for() got an unexpected keyword argument 'player'`.

- [ ] **Step 3: Implement it**

In `triggers.py`, change the dataclass and its properties:

```python
@dataclass(frozen=True)
class Artifacts:
    prefix: str
    #: The client's player token, or None for the dedicated server and for a
    #: client that has not got a character yet. See MOD_CONTRACT.md: an
    #: unsuffixed heartbeat is not a legacy name, it MEANS "up, no character".
    player: str | None = None

    def _named(self, stem: str, ext: str) -> str:
        """`<prefix>-<stem>[-<player>].<ext>` — token before the extension.

        After, and `biomancy-diag.txt-n43n-003f` stops being a text file to
        anything that reads extensions.
        """
        token = f"-{self.player}" if self.player else ""
        return f"{self.prefix}-{stem}{token}.{ext}"

    @property
    def trigger(self) -> str:
        # NOT per player, deliberately. The trigger is how one client learns a
        # request is aimed at somebody else - `DevResponder` leaves a trigger
        # it is not addressed by exactly where it is, so the intended client
        # finds it on its own next poll. Per-player triggers would delete the
        # one part of multi-client that already worked.
        return f"{self.prefix}-capture.trigger"

    @property
    def result(self) -> str:
        return self._named("capture", "txt")

    @property
    def diag(self) -> str:
        return self._named("diag", "txt")

    @property
    def heartbeat(self) -> str:
        return self._named("hooks", "txt")

    @property
    def shot(self) -> str:
        return self._named("shot", "png")
```

Leave `commands` and `all` as they are — `all` already reads the properties, so
it picks the new names up without editing. Then:

```python
def artifacts_for(mod_name: str, player: str | None = None) -> Artifacts:
    """The artifact names one mod writes and this harness reads.

    `player` is a RAW character name; the token rule is applied here so no
    caller has to remember to apply it, and so a caller cannot pass an
    already-tokenised name and get it tokenised twice.
    """
    return Artifacts(prefix=mod_name.lower(), player=player_token(player))
```

- [ ] **Step 4: Run and watch them pass**

Run: `.venv/bin/python -m pytest tests/test_artifact_names.py`
Expected: PASS.

- [ ] **Step 5: Thread the player through the session**

`Session.path` currently ignores the player entirely. Change it so the
per-player names are used for the CLIENT and the unsuffixed ones for the server:

```python
    def path(self, name: str, *, server: bool) -> Path:
        return self.cfg.artifact(name, server=server)
```

stays as-is — `name` already arrives resolved. What changes is every CALLER
that reads `self.cfg.artifacts.<x>`: those must use a per-player `Artifacts`.
Add to `Session`:

```python
    @property
    def artifacts(self) -> Artifacts:
        """This session's names, carrying its player.

        `cfg.artifacts` has no player and stays the right answer for the
        dedicated server and for anything reading off disk without a session.
        """
        return artifacts_for(self.cfg.mod_name, self.player)
```

Import `Artifacts` and `artifacts_for` from `.triggers`, then change exactly
these three lines — they are every place a session names a client answer:

| line             | from                                                  | to                                                     |
| ---------------- | ----------------------------------------------------- | ------------------------------------------------------ |
| `session.py:266` | `self.path(self.cfg.artifacts.result, server=server)` | `self.path(self._names(server).result, server=server)` |
| `session.py:287` | `self.path(self.cfg.artifacts.diag, server=server)`   | `self.path(self._names(server).diag, server=server)`   |
| `session.py:326` | `self.path(self.cfg.artifacts.shot, server=False)`    | `self.path(self.artifacts.shot, server=False)`         |

`result` and `diag` take a `server` flag, so they cannot use `self.artifacts`
unconditionally — a server answer must keep the unsuffixed name. Add:

```python
    def _names(self, server: bool) -> Artifacts:
        """Per-player names for the client, unsuffixed ones for the server.

        The dedicated server has no player. Handing it a token would rename
        files it writes under names nothing reads.
        """
        return self.cfg.artifacts if server else self.artifacts
```

`session.py:622-623` reads BOTH heartbeats during launch readiness. Leave those
on `cfg.artifacts` for now — Task 4 replaces that readiness check with one that
walks every client file, and changing it here would give Task 4 two things to
undo.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/python -m pytest --color=no | tail -1`
Expected: PASS. Some existing tests will need their expected filenames updated —
that is legitimate, they encode the old contract. Do NOT weaken an assertion to
make it pass; change the expected NAME only.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat(naming): answers carry the player, requests stay shared"
```

---

### Task 3: The capture pattern stays unambiguous

**Files:**

- Modify: `src/tmodloader_mcp/captures.py:35-44`
- Test: `tests/test_captures.py`

**Interfaces:**

- Consumes: `triggers.PLAYER_TOKEN_GRAMMAR` from Task 1.
- Produces: `capture_pattern(mod_name: str) -> re.Pattern[str]` matching
  `<mod>-shot-<token>-<index>-<region>.png`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_captures.py`:

```python
from tmodloader_mcp.captures import capture_pattern


def test_a_per_player_capture_matches():
    assert capture_pattern("Biomancy").match("biomancy-shot-n43n-003f-001-full.png")


def test_the_token_cannot_swallow_the_index():
    """The grammar is pinned rather than open for exactly this.

    `[a-z0-9-]+` alone is greedy across dashes AND digits, so a token pattern
    that did not end in four hex could absorb `-001` and leave the region to
    stand in for the index. The four-hex tail is what makes the boundary
    findable.
    """
    m = capture_pattern("Biomancy").match(
        "biomancy-shot-big-bird-44a3-012-topright.png"
    )
    assert m

    # The positive control: a name that is NOT a valid token is refused, so
    # the test above cannot be passing because the pattern matches anything.
    assert not capture_pattern("Biomancy").match(
        "biomancy-shot-nothex-zzzz-012-topright.png"
    )


def test_an_unanchored_lookalike_is_still_refused():
    # The reason the docstring gives for anchoring both ends, re-asserted
    # against the new shape.
    assert not capture_pattern("Biomancy").match(
        "evil-biomancy-shot-n43n-003f-001-full.png"
    )
    assert not capture_pattern("Biomancy").match(
        "biomancy-shot-n43n-003f-001-full.png.exe"
    )


def test_another_mods_capture_is_not_ours():
    assert not capture_pattern("Biomancy").match("othermod-shot-n43n-003f-001-full.png")
```

- [ ] **Step 2: Run and watch them fail**

Run: `.venv/bin/python -m pytest tests/test_captures.py`
Expected: FAIL — the first test returns None, since today's pattern expects the
index immediately after `-shot-`.

- [ ] **Step 3: Implement it**

```python
def capture_pattern(mod_name: str) -> re.Pattern[str]:
    """What `shot` writes for ONE mod: its artifact prefix, the player token,
    then the index and region this harness renames the drop box to.

    Anchored at both ends — an unanchored match would accept
    `evil-biomancy-shot-001-full.png.exe`. Built per mod rather than fixed,
    because two mods share one save directory and neither should be served the
    other's captures.

    The token's grammar is PINNED rather than left as `[a-z0-9-]+`. An open
    token is greedy across dashes and digits alike, so it would swallow the
    three-digit index and leave the region matching what was meant to be the
    index — the pattern would still match, and would extract the wrong fields.
    The four-hex tail is what makes the boundary findable at all.
    """
    token = r"[a-z0-9][a-z0-9-]*-[0-9a-f]{4}"
    return re.compile(
        rf"^{re.escape(mod_name.lower())}-shot-{token}-\d{{3}}-[a-z]+\.png$"
    )
```

Note the leading `[a-z0-9]`: it forbids a token starting with `-`, which is
what makes `biomancy-shot--001-full.png` fail rather than parse as an empty
token. It also admits the pure-digest token from Task 1 Step 4.

- [ ] **Step 4: Run and watch them pass**

Run: `.venv/bin/python -m pytest tests/test_captures.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(captures): the pattern reads a player token without losing the index"
```

---

### Task 4: `heartbeat` answers which clients are alive

**Files:**

- Modify: `src/tmodloader_mcp/heartbeat.py`
- Modify: `src/tmodloader_mcp/server.py:158-184` (`HeartbeatSideOut`,
  `HeartbeatOut`, `heartbeat`)
- Test: `tests/test_heartbeat.py`

**Interfaces:**

- Consumes: `triggers.PLAYER_TOKEN_GRAMMAR`.
- Produces: `HeartbeatOut` becomes
  `{"clients": list[HeartbeatSideOut], "server": HeartbeatSideOut}`.
  `HeartbeatSideOut` gains `player: str | None`. **This is a breaking change to
  the tool's output shape** and is intended: the old shape cannot express two
  clients, which is the whole point.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_heartbeat.py`:

```python
from tmodloader_mcp import heartbeat as heartbeat_mod


def test_it_finds_one_file_per_client(tmp_path):
    (tmp_path / "biomancy-hooks-n43n-003f.txt").write_text("side: client\n")
    (tmp_path / "biomancy-hooks-big-bird-44a3.txt").write_text("side: client\n")
    found = heartbeat_mod.client_files(tmp_path, "biomancy")
    assert sorted(p.name for p in found) == [
        "biomancy-hooks-big-bird-44a3.txt",
        "biomancy-hooks-n43n-003f.txt",
    ]


def test_an_unsuffixed_heartbeat_is_a_client_with_no_character_yet(tmp_path):
    """The one place the old name survives, carrying a new meaning.

    `launch` tells "live, no world" from "absent" by reading a heartbeat
    written BEFORE a world is up — and before a world is up there is no
    LocalPlayer to build a token from. Dropping this file would make the
    harness blind at exactly the moment the heartbeat matters most.
    """
    (tmp_path / "biomancy-hooks.txt").write_text("side: client\n")
    found = heartbeat_mod.client_files(tmp_path, "biomancy")
    assert [p.name for p in found] == ["biomancy-hooks.txt"]


def test_the_servers_file_is_not_mistaken_for_a_clients(tmp_path):
    # `-server` is a side suffix, not a player token, and a glob loose enough
    # to catch it would report the dedicated server as a client.
    (tmp_path / "biomancy-hooks-server.txt").write_text("side: server\n")
    (tmp_path / "biomancy-hooks-n43n-003f.txt").write_text("side: client\n")
    found = heartbeat_mod.client_files(tmp_path, "biomancy")
    # Positive control in the same test: the real client IS found, so this
    # cannot pass by finding nothing at all.
    assert [p.name for p in found] == ["biomancy-hooks-n43n-003f.txt"]


def test_another_mods_heartbeat_is_not_ours(tmp_path):
    (tmp_path / "othermod-hooks-n43n-003f.txt").write_text("side: client\n")
    assert heartbeat_mod.client_files(tmp_path, "biomancy") == []
```

- [ ] **Step 2: Run and watch them fail**

Run: `.venv/bin/python -m pytest tests/test_heartbeat.py`
Expected: FAIL — `module 'tmodloader_mcp.heartbeat' has no attribute
'client_files'`.

- [ ] **Step 3: Implement discovery**

Add to `heartbeat.py`:

```python
import re
from pathlib import Path

from .triggers import PLAYER_TOKEN_GRAMMAR


def client_files(save_dir: Path, mod_prefix: str) -> list[Path]:
    """Every CLIENT heartbeat in this directory, sorted by name.

    Two forms are accepted and both are real:

    - `<mod>-hooks-<token>.txt` — a client with a character
    - `<mod>-hooks.txt` — a client that is up and has not got one yet

    `<mod>-hooks-server.txt` is excluded by construction rather than by a
    special case: `-server` is not a token, because a token ends in four hex
    characters and `server` does not. A looser glob would report the dedicated
    server as a client, which is the diagnosis this tool exists to keep apart.
    """
    pattern = re.compile(
        rf"^{re.escape(mod_prefix)}-hooks(-{PLAYER_TOKEN_GRAMMAR})?\.txt$"
    )
    if not save_dir.is_dir():
        return []
    return sorted(
        (e for e in save_dir.iterdir() if pattern.match(e.name)), key=lambda p: p.name
    )
```

- [ ] **Step 4: Run and watch them pass**

Run: `.venv/bin/python -m pytest tests/test_heartbeat.py`
Expected: PASS.

- [ ] **Step 5: Change the tool's shape**

In `server.py`, add `player` to `HeartbeatSideOut`:

```python
    # Null for the dedicated server, and for a client that is up without a
    # character. Null is not "unknown" here — it is a state the caller can act
    # on, so it is reported rather than omitted.
    player: str | None
```

Replace `HeartbeatOut`:

```python
class HeartbeatOut(TypedDict):
    """Every client, plus the server.

    A LIST rather than one `client` key, because the old shape could not
    express two clients at once and silently reported whichever had written
    last. That was the shared-file bug wearing a different hat: two clients,
    one answer, no way to tell.
    """

    clients: list[HeartbeatSideOut]
    server: HeartbeatSideOut
```

Then replace the loop in `heartbeat()` (currently `server.py:766-769`, the
`for name, is_server in (("client", False), ("server", True))` block) with:

```python
    def _entry(path: Path, player: str | None) -> HeartbeatSideOut:
        hb = heartbeat_mod.read(path)
        return HeartbeatSideOut(
            # `hb.side` is the mod's own account of itself and can disagree
            # with the file this was read from. Where they differ the mod is
            # right, so it is reported rather than the key it was filed under.
            side=hb.side,
            player=player,
            present=hb.present,
            live=hb.live,
            age_seconds=hb.age,
            world_ready=hb.world_ready,
            armed=hb.armed,
        )

    prefix = cfg.mod_name.lower()
    clients = [
        _entry(path, heartbeat_mod.player_of(path.name, prefix))
        for path in heartbeat_mod.client_files(cfg.save_dir, prefix)
    ]
    return HeartbeatOut(
        clients=clients,
        server=_entry(cfg.artifact(cfg.artifacts.heartbeat, server=True), None),
    )
```

with a module-level helper in `heartbeat.py`:

```python
def player_of(filename: str, mod_prefix: str) -> str | None:
    """The token in a client heartbeat's name, or None for the unsuffixed form.

    Returns the TOKEN, not the character name — the name cannot be recovered,
    the slug is lossy and the hash is one way. Callers that want a name have
    the session's; this is for telling two clients apart on disk.
    """
    stem = filename[len(mod_prefix) + len("-hooks") : -len(".txt")]
    return stem.lstrip("-") or None
```

Copy the remaining `HeartbeatSideOut` fields verbatim from the existing loop
body rather than from this snippet — `server.py:770-780` is the authority on
what that TypedDict currently contains, and it may have gained a field.

Keep the server half's semantics exactly as they are: one file, no token.

- [ ] **Step 6: Run the whole suite and fix the callers**

Run: `.venv/bin/python -m pytest --color=no | tail -1`
Expected: PASS. `launch`'s readiness check reads this — it must now consider a
client ready if ANY entry is live, which is the correct reading and also what
makes a second client not break the first's launch.

- [ ] **Step 7: Two more readers that name the heartbeat**

AMENDED mid-run, after Task 2's review surfaced them. Neither was in the
original plan, and both are reachable bugs the moment Task 2 lands.

**7a. `src/tmodloader_mcp/server.py:990-991`** — inside the `diagnose_silence`
prompt:

```python
        client = heartbeat_mod.read(cfg.artifact(cfg.artifacts.heartbeat, server=False))
```

This reads the ONE unsuffixed client heartbeat. Once a client has a character it
writes a per-player name, so this reports "client heartbeat: absent" — in the
one tool whose entire job is explaining why the game is silent. Replace it with
a walk over `heartbeat_mod.client_files(...)`, rendering one
`heartbeat_mod.diagnose(...)` line per client (and a line saying so when there
are none). Keep the server line as it is.

**7b. `src/tmodloader_mcp/session.py:546`** — stale-artifact cleanup before
launch:

```python
    for name in cfg.artifacts.all:
        for server in (False, True):
            cfg.artifact(name, server=server).unlink(missing_ok=True)
```

Its own comment states the stakes: "A heartbeat or reply left by a previous run
is what lets a readiness check pass against a dead process." `cfg.artifacts` has
no player, so after Task 2 this clears only the unsuffixed names and a
per-player reply from a dead run survives into this one. Clear BOTH
`cfg.artifacts.all` and `session.artifacts.all` — the session knows its own
player by then.

Leave other players' files alone, deliberately: they are not this session's to
delete, and Task 4's `client_files` walk reports them with an age, so a dead
one reads as not-live rather than as a phantom client.

- [ ] **Step 8: Write the failing test for 7b, then fix it**

```python
def test_launch_clears_a_stale_per_player_reply(tmp_path):
    """The cleanup comment's own scenario, now reachable.

    A per-player diag left by a dead run is exactly what lets a readiness
    check pass against a process that is gone.
    """
    stale = tmp_path / "biomancy-diag-n43n-003f.txt"
    stale.write_text("from a previous run\n")
    fresh = tmp_path / "biomancy-diag.txt"
    fresh.write_text("also stale\n")

    _clear_stale_artifacts(cfg_for(tmp_path), player="n43n")

    assert not stale.exists()
    # Positive control: the unsuffixed form was being cleared before this
    # change and must still be, so a green result cannot mean "cleared
    # nothing".
    assert not fresh.exists()
```

Extract the cleanup loop into a named helper so it can be tested at all — it is
currently inline in `launch`, which needs a real config and a real process.
Match the surrounding code's naming.

- [ ] **Step 9: Commit**

```bash
git add -A && git commit -m "feat(heartbeat): report which clients are alive, not one of them"
```

---

### Task 5: The mod writes per-player answers

**Files:**

- Modify: `responder/DevResponder.cs` (`PathFor`, ~line 808)
- Modify: `responder/DevArtifacts.cs`
- Test: `responder/tests/DevArtifactNamesTests.cs`

**Interfaces:**

- Consumes: `DevArtifacts.PlayerToken` from Task 1.
- Produces: `DevArtifacts.ForSide(string name, bool dedicatedServer, string
playerToken)` — the existing two-argument overload stays and forwards with a
  null token, so nothing that does not know about players has to change.

- [ ] **Step 1: Write the failing test**

Append to `responder/tests/DevArtifactNamesTests.cs`:

```csharp
		[Fact]
		public void AClientsAnswersCarryItsPlayer() {
			Assert.Equal("biomancy-diag-n43n-003f.txt",
				DevArtifacts.ForSide("biomancy-diag.txt", false, "n43n-003f"));
			Assert.Equal("biomancy-shot-n43n-003f.png",
				DevArtifacts.ForSide("biomancy-shot.png", false, "n43n-003f"));
		}

		[Fact]
		public void TheDedicatedServerKeepsItsSideSuffixAndGainsNoPlayer() {
			// Two axes that compose: the side suffix keeps two SIDES apart, the
			// token keeps two CLIENTS apart. A server has no client to be
			// confused with, and adding a token would rename files the harness
			// reads by their old names.
			Assert.Equal("biomancy-diag-server.txt",
				DevArtifacts.ForSide("biomancy-diag.txt", true, null));
		}

		[Fact]
		public void AClientWithNoCharacterYetKeepsTheUnsuffixedName() {
			// Not a legacy fallback - this name MEANS "up, no character yet",
			// and `launch` depends on being able to read it before a world is
			// loaded. See MOD_CONTRACT.md.
			Assert.Equal("biomancy-hooks.txt",
				DevArtifacts.ForSide("biomancy-hooks.txt", false, null));
		}
```

- [ ] **Step 2: Run and watch it fail**

Run: `"$DOTNET" test responder/tests/Responder.Tests.csproj`
Expected: FAIL to compile — no three-argument `ForSide`.

- [ ] **Step 3: Implement the overload**

In `DevArtifacts.cs`, keep the existing method and add:

```csharp
		/// <summary>
		/// Side suffix and player token together, in that order.
		///
		/// The two axes are independent and compose: the mod prefix keeps two
		/// MODS apart, the side suffix keeps two SIDES apart, and the token
		/// keeps two CLIENTS apart. A dedicated server passes a null token
		/// because it has no client to be confused with.
		/// </summary>
		public static string ForSide(string name, bool dedicatedServer, string playerToken) {
			string sided = ForSide(name, dedicatedServer);
			if (string.IsNullOrEmpty(playerToken)) {
				return sided;
			}

			// Before the extension. After it, `biomancy-diag.txt-n43n-003f` is
			// not a text file to anything that reads extensions.
			int dot = sided.LastIndexOf('.');
			return dot < 0
				? sided + "-" + playerToken
				: sided.Substring(0, dot) + "-" + playerToken + sided.Substring(dot);
		}
```

- [ ] **Step 4: Run and watch it pass**

Run: `"$DOTNET" test responder/tests/Responder.Tests.csproj`
Expected: PASS.

- [ ] **Step 5: Use it for answers only**

In `DevResponder.cs`, change `PathFor`:

```csharp
		private static string PathFor(string name) {
			return Path.Combine(Main.SavePath,
				DevArtifacts.ForSide(name, Main.dedServ, PlayerTokenOrNull));
		}

		/// <summary>
		/// This client's token, or null on a dedicated server and before a
		/// character exists.
		/// </summary>
		private static string PlayerTokenOrNull => DevArtifacts.PlayerToken(LocalPlayerName);
```

The trigger path must NOT use it. Find where the capture trigger is read and
confirm it uses the unsuffixed name — the addressing check at
`DevResponder.cs:446` (`request.IsFor(LocalPlayerName)`) is what keeps two
clients out of each other's requests, and it only works if they poll the SAME
file.

- [ ] **Step 6: Run everything, regenerate, commit**

```bash
"$DOTNET" test responder/tests/Responder.Tests.csproj
cd responder && sha256sum *.cs > SHA256SUMS && cd ..
.venv/bin/python -m pytest --color=no | tail -1
git add -A && git commit -m "feat(responder): a client's answers carry its player"
```

---

### Task 6: The contract says so

**Files:**

- Modify: `docs/MOD_CONTRACT.md` (the filename table, and `## Known limitation:
one client per side` at line 193)
- Modify: `responder/README.md`
- Test: `tests/test_mod_contract_doc.py`

- [ ] **Step 1: Run the contract test and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_mod_contract_doc.py`
Expected: FAIL — it already fails in BOTH directions, so after Tasks 2-5 it
should report that the protocol writes names the document does not mention.
**If it passes, stop:** the test is not covering the new names and must be
extended before the document is written, or it will pass against a contract
nobody implements.

- [ ] **Step 2: Rewrite the filename table**

Replace the six-filename table with the per-player forms, and add the token
rule verbatim from Global Constraints, including the grammar
`[a-z0-9][a-z0-9-]*-[0-9a-f]{4}` and the three computed vectors.

- [ ] **Step 3: Replace the limitation section**

`## Known limitation: one client per side` (line 193) documents the bug this
change removes. Replace it with `## Two clients at once`, keeping its
diagnosis — requests could be told apart and answers could not — as the
EXPLANATION of why answers are namespaced now, and stating the one surviving
unsuffixed name and what it means.

- [ ] **Step 4: Document the no-character-yet rule explicitly**

In `responder/README.md`, under a new heading, state that a responder must keep
writing the unsuffixed heartbeat until a local player exists. Per the spec: it
must be spelled out rather than left as a fallback an implementer discovers,
because a responder that skipped it would look correct until somebody watched a
launch.

- [ ] **Step 5: The CHANGELOG entry this plan forgot**

AMENDED mid-run. Task 4's review found no task in this plan covers `CHANGELOG.md`
at all, while Task 4 changed a published tool's output shape. `CHANGELOG.md:231`
establishes the `**Breaking:**` precedent — follow it.

Under `[Unreleased]`, add `### Changed` with the `heartbeat` shape change:
`{client, server}` became `{clients: [...], server}`, and `HeartbeatSideOut`
gained `player`. Say why the break was taken rather than shimmed: the old shape
could not express two clients and silently reported whichever wrote last, which
is the same shared-file ambiguity this release removes, wearing a different hat.
Add an `### Added` entry for per-player artifact naming itself: what gained a
token, what deliberately did not (the trigger and the command list), and the
token rule with its grammar.

- [ ] **Step 6: A refusal message that now describes a filename shape nobody writes**

AMENDED mid-run, found by Task 3's review. `captures.contained` raises a
`CaptureError` whose text still spells the old shape `<mod>-shot-<index>-
<region>.png`. It is a string that lies to the caller about the protocol, in the
one place a caller reads when their name was rejected. Correct it to the
tokened shape, and check the rest of that module's messages for the same
staleness rather than fixing only the one that was reported.

- [ ] **Step 7: Run everything and commit**

```bash
.venv/bin/python -m pytest --color=no | tail -1
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .
git add -A && git commit -m "docs(contract): two clients, and the name that means no character yet"
```

NOTE: the "one surviving limitation" section is written from what is KNOWN now.
Three behaviours can only be settled by Task 8's live run — a client appearing
twice in `heartbeat` (tokened and live, plain and aging out), an untargeted
request being answerable by either client, and the `capture` verb picking the
largest new PNG in a shared directory. Write the section so those can be added
to it, and do not assert what the live run has not shown.

---

### Task 7: Biomancy vendors the change and still builds

**Files:**

- Modify: Biomancy `Common/DevBridge/vendored/*.cs` (copy, not edit)
- Modify: Biomancy `Common/Diagnostics/DevCapture.cs` if it names any artifact

- [ ] **Step 1: Re-sync the vendored copy**

```bash
V="/mnt/c/Users/a2b32/Documents/My Games/Terraria/tModLoader/ModSources/Biomancy/Common/DevBridge/vendored"
cd /home/mjarnold/tmodloader-mcp
cp responder/*.cs responder/SHA256SUMS "$V/"
```

- [ ] **Step 2: Prove it is a copy, not a merge**

```bash
for f in responder/*.cs; do diff -q "$f" "$V/$(basename $f)" || echo "DIFF $f"; done
cd "$V" && sha256sum -c SHA256SUMS
```

Expected: no `DIFF` lines, every file `OK`.

- [ ] **Step 3: Run Biomancy's tests**

```bash
cd ".../Biomancy/tests/BiomancyMod.Tests" && "$DOTNET" test
```

Expected: PASS, including `VendoredIntegrityTests`.

- [ ] **Step 4: Build the mod**

```bash
cd /home/mjarnold/tmodloader-mcp
.venv/bin/python -c "from tmodloader_mcp import server; print(server.build_mod())"
```

Expected: `ok: True`, 0 errors, 0 warnings.

- [ ] **Step 5: Commit in Biomancy**

```bash
git add -A && git commit -m "chore(devbridge): vendor per-player artifact naming"
```

---

### Task 8: Two clients, live — the case that has never been runnable

**Files:** none. A live run, recorded in the PR.

This is the point of the whole change. Everything before it is a claim.

- [ ] **Step 1: Launch and confirm one client**

```bash
.venv/bin/python -c "
from tmodloader_mcp import server
print(server.launch(mode='server_client', port=7812))
print(server.heartbeat())
"
```

Expected: **one or two** entries in `clients`, at least one with `player` set to
the token for `n43n`.

AMENDED after Task 5's review. Two entries is the CORRECT expectation once a
character loads, and the plan originally said one. Nothing deletes
`<mod>-hooks.txt` when the client switches to its tokenised name, and
`client_files` matches both forms — so one client legitimately shows up twice:
the tokenised entry live, and the plain entry aging past `HEARTBEAT_MAX_AGE`
(45s) with `player: null`.

Record which you see, and treat it as a QUESTION TO SETTLE rather than a pass or
fail: either the plain file should be deleted when the mod starts writing a
tokenised one, or `heartbeat` should suppress a stale plain entry when a live
tokenised one exists, or two entries is simply the honest answer and the tool's
docstring should say so. Do not "fix" it mid-run — report what happened.

- [ ] **Step 2: Join a second client against the same server**

tModLoader is launched directly as `dotnet tModLoader.dll -join ...`, not
through Steam, so nothing stops a second process joining. Use a DIFFERENT
character. If `launch` has no second-client mode, start it by hand with the
same command line `launch` uses, changing only `-player`.

- [ ] **Step 3: Both clients appear, separately**

Run `server.heartbeat()`.
Expected: TWO entries under `clients`, different `player` values, both `live`.
This is the assertion the old shape could not even express.

- [ ] **Step 4: Each answers for itself**

```bash
.venv/bin/python -c "
from tmodloader_mcp import server
a = server.diag(target='n43n')
b = server.diag(target='<second character>')
print(a['fields'].get('player'), b['fields'].get('player'))
"
```

Expected: each reports its OWN player. If both report the same name, the
answers are still shared and the change has failed.

- [ ] **Step 5: Neither eats the other's trigger**

Take a `shot` addressed to each in turn and confirm two distinct files, each
matching `capture_pattern`, with the right token. Confirm from `logs` that
neither client consumed a request addressed to the other.

- [ ] **Step 5a: An UNTARGETED request, with two clients listening**

AMENDED after Task 5's review. `DevRequest.IsFor` returns true for ANY client
when no `@player` is given, while `Session.ask` waits on THIS session's
per-player result file and `trigger`'s `target` defaults to None. So a bare
`trigger("diag")` can be consumed by the other client, which writes its own
tokenised answer, and this session times out with nothing on disk explaining
why.

Run `trigger("diag")` with no target, twice, and record what happens each time.
This is the first moment that behaviour has ever been observable. Report it;
do not fix it here.

- [ ] **Step 5b: Two captures at once — does `capture` attribute correctly?**

AMENDED after Task 5's review, which found this by reading and could not test
it. The mod's `capture` verb enumerates every `*.png` under the shared
`Main.SavePath` and picks the largest path not present before it started
waiting. Per-player shot names make this WORSE than it was: previously both
clients wrote the same path, so a rewrite was normally excluded by the
before-set; now client B's shot creates a genuinely new path that is "fresh" to
a concurrent client A, so A can report `PNG: <B's file>` — a well-formed answer
attributed to the wrong player, which is the exact failure class this whole plan
exists to remove.

Trigger a capture on both clients as close to simultaneously as you can manage,
and record which file each reports. If A reports B's file, that is the predicted
defect confirmed — write it up, do not fix it in this task.

- [ ] **Step 6: Stop, and record**

Run `server.stop()`. Paste the output of steps 1-5 into the PR body.

If any step fails, do NOT open the PR as ready — the change has not been
proven, and the docs in Task 6 assert that it has.

---

## Not in this plan

- **A second-client mode for `launch`.** Task 8 starts one by hand. Whether the
  tool should own that is a separate question, and answering it inside this
  change would mean a live failure had two candidate causes.
- **Migrating existing captures.** The old `biomancy-shot-001-full.png` files
  stop matching `capture_pattern` and become invisible to `captures`. They are
  a developer's scratch output, not data, and 13 of them exist. Deleting or
  renaming them is a one-line decision the user can make after seeing this work.
