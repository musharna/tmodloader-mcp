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

    import re

    from tmodloader_mcp.triggers import PLAYER_TOKEN_GRAMMAR

    assert re.fullmatch(PLAYER_TOKEN_GRAMMAR, token)


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_no_character_yet_has_no_token(empty):
    assert player_token(empty) is None
