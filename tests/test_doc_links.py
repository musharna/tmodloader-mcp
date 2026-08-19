"""Every relative link and anchor in the documentation resolves.

WHY THIS IS A TEST RATHER THAN A THING SOMEBODY CHECKS. A broken anchor does
not fail loudly: GitHub renders the link, the reader clicks it, and the page
does not move. Nothing reports anything. Three of them sat in
`docs/MOD_CONTRACT.md` for weeks — all pointing at the same heading, all
missing one hyphen — and were found only by writing this check while tidying
for a release.

It reads the REAL documents rather than a fixture, because the thing being
tested is those documents. A fixture would assert that the checker works.

`docs/superpowers/` is excluded for the reason ruff excludes it: those are
plans and specs, not published documentation, and they deliberately reference
files at the paths they WOULD have rather than the paths that exist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

#: Directories whose markdown is not published documentation.
SKIP = ("docs/superpowers", ".superpowers", "/bin/", "/obj/", ".venv")

#: `[label](target)`. Reference-style links are not used in this repository;
#: if that changes this pattern has to grow, and the test failing to notice a
#: new form is the risk worth naming here.
LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")

#: Links into this repository's own GitHub tree. The README links absolutely
#: because PyPI renders it verbatim and rewrites nothing — a relative link
#: there is dead on the project page. An absolute link would otherwise escape
#: this file entirely (external URLs are skipped below), so these are mapped
#: back onto the working tree and held to the same standard as relative ones.
SELF_REPO = re.compile(
    r"https://github\.com/musharna/tmodloader-mcp/(?:blob|tree)/master/"
    r"([^#]*)(?:#(.*))?$"
)


def _documents() -> list[Path]:
    return sorted(
        path
        for path in REPO.rglob("*.md")
        if not any(skip in str(path) for skip in SKIP)
    )


def _anchors(text: str) -> set[str]:
    """The heading slugs GitHub would generate.

    GitHub keeps word characters and hyphens, drops everything else, and turns
    spaces into hyphens. Backticks and angle brackets vanish, which is why
    `` `<mod>-capture.trigger` — the request `` becomes
    `mod-capturetrigger--the-request` and NOT `modcapturetrigger--the-request`.
    That one missing hyphen is the bug this file was written for.
    """
    found = set()
    for line in text.splitlines():
        heading = re.match(r"^#{1,6}\s+(.*)", line)
        if heading:
            slug = re.sub(r"[^\w\s-]", "", heading.group(1).lower())
            found.add(slug.strip().replace(" ", "-"))
    return found


def _broken(doc: Path) -> list[str]:
    problems = []
    for _label, target in LINK.findall(doc.read_text()):
        own = SELF_REPO.match(target)
        if own:
            path_part, fragment = own.group(1), own.group(2) or ""
            base = REPO
        elif target.startswith(("http://", "https://", "mailto:")):
            continue
        else:
            path_part, _, fragment = target.partition("#")
            base = doc.parent

        if path_part:
            dest = (base / path_part).resolve()
            if not dest.exists():
                problems.append(f"{target} -> no such file")
                continue
            holder = dest / "README.md" if dest.is_dir() else dest
        else:
            holder = doc

        if (
            fragment
            and holder.suffix == ".md"
            and holder.is_file()
            and fragment not in _anchors(holder.read_text())
        ):
            problems.append(f"{target} -> no such heading in {holder.name}")

    return problems


@pytest.mark.parametrize("doc", _documents(), ids=lambda p: str(p.relative_to(REPO)))
def test_every_link_in_this_document_resolves(doc):
    problems = _broken(doc)
    assert not problems, f"{doc.relative_to(REPO)}:\n  " + "\n  ".join(problems)


def test_there_are_documents_to_check():
    """POSITIVE CONTROL for the collection itself.

    Every test above is parametrised over `_documents()`. If that returned
    nothing - a renamed directory, an over-broad skip - the file would pass
    with zero assertions and read as a clean documentation set.
    """
    docs = _documents()
    assert len(docs) >= 5, f"only found {[str(d) for d in docs]}"
    assert any(d.name == "README.md" for d in docs)
    assert any(d.name == "MOD_CONTRACT.md" for d in docs)


def test_a_broken_anchor_would_actually_be_caught(tmp_path):
    """The checker, shown failing.

    Without this the parametrised tests above pass on a checker that returns an
    empty list unconditionally - which is exactly what a `_broken` with an
    early `return []` would do.
    """
    doc = tmp_path / "sample.md"
    doc.write_text("# Real Heading\n\n[fine](#real-heading)\n[broken](#nope)\n")

    problems = _broken(doc)

    assert len(problems) == 1, problems
    assert "#nope" in problems[0]


def test_a_missing_file_would_actually_be_caught(tmp_path):
    doc = tmp_path / "sample.md"
    (tmp_path / "there.md").write_text("# There\n")
    doc.write_text("[here](there.md)\n[gone](absent.md)\n")

    problems = _broken(doc)

    assert len(problems) == 1, problems
    assert "absent.md" in problems[0]


def test_a_broken_absolute_link_into_this_repo_would_actually_be_caught(tmp_path):
    """Absolute self-repo links are held to the relative-link standard.

    Without this, converting a README link to its GitHub URL would move it
    from "checked on every run" to "skipped as external" — the exact silence
    the conversion was not supposed to buy.
    """
    base = "https://github.com/musharna/tmodloader-mcp"
    doc = tmp_path / "sample.md"
    doc.write_text(
        f"[fine]({base}/blob/master/README.md)\n"
        f"[gone]({base}/blob/master/no-such-file.md)\n"
        f"[external](https://example.com/no-such-file.md)\n"
    )

    problems = _broken(doc)

    assert len(problems) == 1, problems
    assert "no-such-file" in problems[0]
    assert "example.com" not in problems[0]
