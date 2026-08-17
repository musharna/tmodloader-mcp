"""The template mod, and the copy of `responder/` inside it.

`template/DevBridgeTemplate/` is a whole tModLoader mod: build files, a `Mod`
class, a `DevResponder` subclass, and a vendored `DevBridge/` copy of
`responder/*.cs`. It exists because three claims this repository makes were
argued rather than checked — that the vendored folder compiles AS VENDORED,
that the subclass in `responder/README.md` is a subclass that works, and that a
consumer can get from an empty folder to a driveable mod.

WHY THE COPY IS CHECKED IN RATHER THAN SYNCED AT BUILD TIME. tModLoader's
`-build` compiles the mod DIRECTORY; a file outside it is not on the compile
line at all, so a template that said "copy `responder/` in here" would not
compile as checked in — and a template nobody can build proves none of the
three.

WHY THAT COPY NEEDS A TEST. A checked-in copy rots. This repository has already
paid for exactly that once, which is why `SHA256SUMS` exists: two comments were
edited upstream and had to be re-copied by hand afterwards, and nothing noticed
in between. Here the copy is inside the same repository as its original, so
drift is not merely detectable — it is a red test on the commit that caused it.

Re-sync with:

    cp responder/*.cs responder/SHA256SUMS template/DevBridgeTemplate/DevBridge/
"""

from __future__ import annotations

import hashlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESPONDER = REPO / "responder"
TEMPLATE = REPO / "template" / "DevBridgeTemplate"
VENDORED = TEMPLATE / "DevBridge"

RESYNC = "cp responder/*.cs responder/SHA256SUMS template/DevBridgeTemplate/DevBridge/"


def _digests(directory: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.glob("*.cs"))
    }


def test_the_template_is_a_mod_tmodloader_would_recognise():
    """POSITIVE CONTROL for everything below: the folder is really there, and
    is really a mod source rather than a directory of loose C#."""
    assert (TEMPLATE / "build.txt").is_file(), (
        f"{TEMPLATE} has no build.txt, so tModLoader would not treat it as a "
        "mod source at all"
    )
    assert (TEMPLATE / "description.txt").is_file()
    assert VENDORED.is_dir(), f"{VENDORED} is missing"


def test_the_template_carries_every_responder_file_unchanged():
    """The claim the whole template rests on: what it compiles is what a
    consumer would copy, byte for byte."""
    upstream = _digests(RESPONDER)
    vendored = _digests(VENDORED)

    assert upstream, "no responder sources found - this check found nothing to do"

    missing = sorted(set(upstream) - set(vendored))
    extra = sorted(set(vendored) - set(upstream))
    changed = sorted(
        name
        for name in set(upstream) & set(vendored)
        if upstream[name] != vendored[name]
    )

    assert not (missing or extra or changed), (
        "the template's vendored copy has drifted from responder/ — "
        f"missing {missing}, extra {extra}, changed {changed}. The template is "
        "the only thing that compiles these files against a real tModLoader, so "
        f"a stale copy means that proof is about an older folder. Re-sync: {RESYNC}"
    )


def test_the_manifest_travels_with_the_copy():
    """`SHA256SUMS` is what lets a consumer check their own copy offline. A
    vendored folder without it cannot answer either question it exists for."""
    manifest = VENDORED / "SHA256SUMS"
    assert manifest.is_file(), f"{manifest} is missing. Re-sync: {RESYNC}"
    assert manifest.read_bytes() == (RESPONDER / "SHA256SUMS").read_bytes(), (
        "the template carries a fingerprint of a different version of the "
        f"folder it contains. Re-sync: {RESYNC}"
    )


def test_the_subclass_is_the_one_the_readme_documents():
    """`responder/README.md` says a mod overrides two members. Nothing checked
    that the documented shape was a shape that compiles — the template is that
    check, and this is what keeps the template honest about being it."""
    source = (TEMPLATE / "TemplateResponder.cs").read_text()

    assert ": DevResponder" in source, (
        "the template's responder does not subclass DevResponder, so it "
        "demonstrates nothing about how a mod uses this folder"
    )
    assert "protected override string CollectDiag()" in source
    assert "protected override void RegisterCommands(" in source


def test_the_template_is_where_the_opt_in_is_shown():
    """A's safety property is that nothing registers the mutating verbs for
    you. The template is the only place in this repository that opts IN, which
    makes it the worked example of the line a consumer has to write — and the
    only place a live run can drive those verbs from."""
    source = (TEMPLATE / "TemplateResponder.cs").read_text()

    assert "DevMutations.RegisterInto(r, Report)" in source, (
        "the template does not turn the mutating verbs on, so nothing here "
        "shows a consumer how, and nothing can drive them live"
    )

    # And the responder it copies still does not do it for anybody.
    responder = (VENDORED / "DevResponder.cs").read_text()
    code = "\n".join(
        line.split("//")[0]
        for line in responder.splitlines()
        if not line.lstrip().startswith("//")
    )
    assert "DevMutations" not in code, (
        "the vendored DevResponder registers the world-changing verbs itself, "
        "so a mod would get them by updating its copy rather than by asking"
    )
