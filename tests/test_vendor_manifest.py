"""The vendored folder carries a fingerprint of itself.

`responder/` is copied into other people's mods by hand. Nothing in git can see
those copies, so nothing in git can tell a mod that its copy has gone stale —
and this session proved the sync is forgettable, because it was forgotten: two
comments were edited upstream and had to be re-copied by hand afterwards.

`SHA256SUMS` is the anchor that makes drift detectable at all. It ships WITH the
folder, so a vendored copy carries the fingerprint of the upstream it came from,
and one file is enough to answer both questions a consumer has:

  - "has anybody edited my copy?" - recompute and compare, offline, no network
  - "is my copy behind upstream?" - compare this one small file against
    upstream's, rather than fetching nine sources

This test is what keeps the manifest TRUE. Without it the manifest is a file
somebody updated once: `responder/*.cs` would change, the sums would not, and a
consumer comparing against a stale fingerprint would be told it was in sync
while it was not — a false PASS on the one check that exists to prevent drift.

Regenerate with:

    cd responder && sha256sum *.cs > SHA256SUMS
"""

import hashlib
from pathlib import Path

RESPONDER = Path(__file__).resolve().parent.parent / "responder"
MANIFEST = RESPONDER / "SHA256SUMS"

REGENERATE = "cd responder && sha256sum *.cs > SHA256SUMS"


def _recorded() -> dict[str, str]:
    """The manifest, as {filename: sha256}, in `sha256sum` output format."""
    entries = {}
    for line in MANIFEST.read_text().splitlines():
        if not line.strip():
            continue
        # `sha256sum` writes "<hash>  <name>" - two spaces, the second column
        # being a mode marker for binary. split(None, 1) handles either.
        digest, name = line.split(None, 1)
        entries[name.strip()] = digest
    return entries


def _actual() -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(RESPONDER.glob("*.cs"))
    }


def test_the_manifest_exists_at_all():
    assert MANIFEST.is_file(), (
        f"{MANIFEST} is missing. A vendored copy with no fingerprint cannot be "
        f"checked against upstream by anybody. Create it: {REGENERATE}"
    )


def test_the_manifest_covers_exactly_the_source_files():
    # Named separately from the content check because the failures have
    # different causes and different fixes. A file ADDED upstream and left out
    # of the manifest is the worse one: every hash present still matches, so a
    # content-only check passes while a consumer silently never learns about the
    # new file.
    assert set(_recorded()) == set(_actual()), (
        "the manifest does not list the same files as responder/*.cs.\n"
        f"  only in manifest: {sorted(set(_recorded()) - set(_actual()))}\n"
        f"  only on disk:     {sorted(set(_actual()) - set(_recorded()))}\n"
        f"Regenerate: {REGENERATE}"
    )


def test_every_recorded_hash_matches_the_file_on_disk():
    recorded, actual = _recorded(), _actual()
    drifted = sorted(
        name
        for name in recorded.keys() & actual.keys()
        if recorded[name] != actual[name]
    )
    assert not drifted, (
        f"these files changed without the manifest being regenerated: {drifted}\n"
        "Anyone who vendored this folder is now comparing against a fingerprint "
        f"that describes an older version of it.\nRegenerate: {REGENERATE}"
    )


def test_the_hashes_are_of_bytes_rather_than_text():
    """A line-ending translation would change every hash on a Windows checkout.

    These files came from a Windows drive mount and are read by a Windows game.
    Hashing decoded text would make the manifest agree with itself on one
    platform and disagree on another, which is the worst kind of guard: it
    accuses a consumer of drift they did not cause. This pins the reading mode
    rather than trusting it to stay right.
    """
    sample = min(RESPONDER.glob("*.cs"))
    raw = sample.read_bytes()
    assert _recorded()[sample.name] == hashlib.sha256(raw).hexdigest()
