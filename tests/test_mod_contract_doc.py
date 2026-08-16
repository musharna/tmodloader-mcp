"""The mod-side contract document, checked against the code it describes.

A specification nothing executes rots quietly, and this one is load-bearing:
until the responder is extracted into something a mod can vendor, it is the
only thing standing between "works for Biomancy" and "works for yours".

So the artifact names in it are checked against `triggers.Artifacts` rather
than trusted. A document naming a file the harness does not write would send a
mod author to implement the wrong protocol, and nothing else here would notice.
"""

from __future__ import annotations

import re
from pathlib import Path

from tmodloader_mcp import config
from tmodloader_mcp.triggers import (
    artifacts_for,
    artifacts_for_server,
    player_token,
)

DOC = Path(__file__).resolve().parent.parent / "docs" / "MOD_CONTRACT.md"

#: What the document writes where a real mod name would go. The names in it are
#: `<mod>-hooks.txt` rather than `biomancy-hooks.txt`, because spelling one
#: mod's prefix into the specification is the exact habit this project spent
#: three pull requests removing from the code.
PLACEHOLDER = "<mod>"

#: What the document writes where a real player token would go, the same way
#: PLACEHOLDER stands in for a real mod name. A literal hash is one player's
#: fact, not the protocol's — the document names the SHAPE.
TOKEN_PLACEHOLDER = "<token>"

#: What the document writes where a real port would go, the same way
#: TOKEN_PLACEHOLDER stands in for a real player token.
PORT_PLACEHOLDER = "<N>"

#: The port whose address this test computes from the real `server_address`,
#: then substitutes back out for PORT_PLACEHOLDER. Any port would do; this one
#: is `launch`'s own default.
_PROBE_PORT = 7810

#: A name whose token this test computes from the real `player_token`, then
#: substitutes back out for TOKEN_PLACEHOLDER below. Any non-empty name would
#: do; this one is also one of the three vectors the document itself quotes.
_PROBE_PLAYER = "n43n"

#: A documented artifact: the placeholder, a dash, then a filename. `<` and `>`
#: are in the class so a per-player mention like `<mod>-diag-<token>.txt` scans
#: as ONE name rather than stopping at the token placeholder's `<` — a version
#: of this pattern that excluded them matched only `<mod>-diag-` and reported
#: every per-player name as an invented file called that.
#:
#: Anchored on the placeholder so the prose phrase "mod-side" cannot match — it
#: did, when this scanned for a bare `mod-` prefix, and reported the document
#: as describing a file called `mod-side`.
_MENTION = re.compile(rf"{re.escape(PLACEHOLDER)}-[A-Za-z0-9.\-<>]+")


def _expected() -> set[str]:
    """Every artifact name, spelled the way the document spells it.

    Two forms per per-player artifact go in: the shared name with no player
    (from `artifacts_for("mod")`, player=None) and the tokened name (from
    `artifacts_for("mod", _PROBE_PLAYER)`), with the PROBE's real computed
    token subbed back out for TOKEN_PLACEHOLDER — so this checks the
    document's GRAMMAR, not one player's literal hash.

    The per-player half is `tokened.all - shared.all` — a SET DIFFERENCE
    against `.all`, not four named accessors (`.result`, `.diag`, ...) picked
    by hand. `trigger` and `commands` fall out of the tokened half on their
    own, because they are identical in both sets and a difference cancels
    them — nobody has to remember to exclude them. A NAMED list would also
    have quietly matched today's four correctly and then said nothing the day
    a fifth per-player artifact was added to `Artifacts` and this function was
    not updated to match: not in the list, so absence from the document would
    never register as "missing", and being undocumented, it could never be
    flagged as "invented" either — the exact silent gap this whole test
    exists to prevent, one level removed. A difference against the live
    `.all` cannot go stale that way; a fifth tokened name shows up here the
    moment `Artifacts` computes one.
    """
    shared_names = artifacts_for("mod").all

    tokened = artifacts_for("mod", _PROBE_PLAYER)
    token = player_token(_PROBE_PLAYER)
    assert token, "the probe player name must be non-empty to produce a token"

    shared = {name.replace("mod-", f"{PLACEHOLDER}-", 1) for name in shared_names}
    per_player = {
        name.replace(token, TOKEN_PLACEHOLDER).replace("mod-", f"{PLACEHOLDER}-", 1)
        for name in set(tokened.all) - set(shared_names)
    }

    return shared | per_player


#: A Config that exists only to lend this test its REAL `-server` sider.
#: Transcribing that rule here instead is what the whole file exists to avoid:
#: a second copy of a naming rule is a second naming rule.
_SIDER = config.load({"TMODLOADER_MOD_NAME": "mod"})


def _sided(name: str) -> str:
    return _SIDER.artifact(name, server=True).name


def _legitimate() -> set[str]:
    """`_expected()`, plus every SIDED spelling of it.

    Not folded into `_expected()`, and the asymmetry is the point. Every
    unsided name is a file some responder must write, so its absence from the
    document is a gap worth failing on. A sided one is the same artifact seen
    from the server, and the document names the handful that are worth
    discussing rather than all sixteen — requiring each would turn a
    specification into an inventory. So sided names are LEGITIMATE to mention
    and not REQUIRED to be mentioned, and only the invention check reads this.

    The server's per-port forms come from `artifacts_for_server`, so they are
    derived here the same way the per-player ones are: the probe port's real
    computed address subbed back out for the placeholder.
    """
    per_port = {
        name.replace(f"port{_PROBE_PORT}", f"port{PORT_PLACEHOLDER}").replace(
            "mod-", f"{PLACEHOLDER}-", 1
        )
        for name in artifacts_for_server("mod", _PROBE_PORT).all
    }

    unsided = _expected() | per_port
    return unsided | {
        _sided(name.replace(PLACEHOLDER, "mod", 1)).replace(
            "mod-", f"{PLACEHOLDER}-", 1
        )
        for name in unsided
    }


def test_the_contract_document_exists():
    assert DOC.is_file(), "the mod-side contract is documented nowhere"


def test_every_artifact_the_harness_uses_is_documented():
    """A file the mod must write, missing from the spec, is a silent gap.

    Derived from `Artifacts` so a seventh filename added to the protocol fails
    HERE — rather than being discovered by whoever implements a responder from
    a document that never mentioned it.
    """
    text = DOC.read_text()

    missing = sorted(name for name in _expected() if name not in text)
    assert not missing, f"part of the protocol, absent from the document: {missing}"


def test_the_document_does_not_describe_files_that_do_not_exist():
    """The other direction, which the check above cannot see.

    A document can name every real artifact AND several imagined ones, and the
    first test passes either way. One half catches an omission; this one
    catches an invention. Neither is sufficient alone.
    """
    mentioned = set(_MENTION.findall(DOC.read_text()))

    # The renamed per-capture form (`<mod>-shot-<token>-<index>-<region>.png`)
    # is the HARNESS's product, not something the mod writes, so it is
    # legitimately in the document and not in `Artifacts`. Narrowed to that
    # exact prefix rather than a blanket "starts with `<mod>-shot-`" — the
    # broader form would also swallow `<mod>-shot-<token>.png`, the mod's own
    # pre-rename drop box, which IS in `Artifacts` and belongs in this check.
    mentioned = {
        m
        for m in mentioned
        if not m.startswith(f"{PLACEHOLDER}-shot-{TOKEN_PLACEHOLDER}-")
    }

    # Positive control: a scan that matches nothing would pass while proving
    # nothing at all. This is the assertion that catches a broken regex.
    assert mentioned, "no artifact names found in the document - the scan broke"

    invented = sorted(mentioned - _legitimate())
    assert not invented, f"the document describes files nothing writes: {invented}"
