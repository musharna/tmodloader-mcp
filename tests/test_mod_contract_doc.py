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

from tmodloader_mcp.triggers import artifacts_for

DOC = Path(__file__).resolve().parent.parent / "docs" / "MOD_CONTRACT.md"

#: What the document writes where a real mod name would go. The names in it are
#: `<mod>-hooks.txt` rather than `biomancy-hooks.txt`, because spelling one
#: mod's prefix into the specification is the exact habit this project spent
#: three pull requests removing from the code.
PLACEHOLDER = "<mod>"

#: A documented artifact: the placeholder, a dash, then a filename. Anchored on
#: the placeholder so the prose phrase "mod-side" cannot match — it did, when
#: this scanned for a bare `mod-` prefix, and reported the document as
#: describing a file called `mod-side`.
_MENTION = re.compile(rf"{re.escape(PLACEHOLDER)}-[A-Za-z0-9.\-]+")


def _expected() -> set[str]:
    """Every artifact name, spelled the way the document spells it."""
    return {
        name.replace("mod-", f"{PLACEHOLDER}-", 1) for name in artifacts_for("mod").all
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

    # The renamed per-capture form is the HARNESS's product, not something the
    # mod writes, so it is legitimately in the document and not in `Artifacts`.
    mentioned = {m for m in mentioned if not m.startswith(f"{PLACEHOLDER}-shot-")}

    # Positive control: a scan that matches nothing would pass while proving
    # nothing at all. This is the assertion that catches a broken regex.
    assert mentioned, "no artifact names found in the document - the scan broke"

    invented = sorted(mentioned - _expected())
    assert not invented, f"the document describes files nothing writes: {invented}"
