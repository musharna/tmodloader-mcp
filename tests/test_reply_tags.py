"""The request id: how a late reply stops answering the wrong question.

The reply file is named per PLAYER, not per request. A request that times out
after the mod consumed its trigger leaves the game still working, and the
reply it eventually writes lands on the very file the NEXT request waits at —
so that caller read the previous answer as its own. For `diag` that was a
wrong dump with nothing visibly wrong; for `shot` it promoted a wrong PICTURE
under the new request's region label.

The fix is a correlator, not a rename: the payload gains a trailing
`#r-<hex>` and the reply's first line echoes it. Everything here is gated on
the responder PUBLISHING that it tags (`# replies: tagged` in the command
list), because an older vendored responder would read the suffix as part of a
target and the request would match nobody — so half these tests are about the
id working and the other half are about it staying away from responders that
never heard of it.
"""

from __future__ import annotations

import pytest

from tmodloader_mcp import commands as commands_mod
from tmodloader_mcp import session as session_mod
from tmodloader_mcp.session import Session
from tmodloader_mcp.triggers import (
    Request,
    TriggerError,
    artifacts_for,
    compose,
    parse,
)

SELF = "n43n"


class FakeCfg:
    """The same shape as `test_addressing.FakeCfg`, for the same reason."""

    def __init__(self, root, mod_name: str = "Biomancy"):
        self.root = root
        self.mod_name = mod_name
        self.artifacts = artifacts_for(mod_name)

    def artifact(self, name: str, *, server: bool):
        return self.root / (f"server-{name}" if server else name)


@pytest.fixture
def cfg(tmp_path) -> FakeCfg:
    return FakeCfg(tmp_path)


@pytest.fixture
def sess(cfg) -> Session:
    return Session(cfg=cfg, mode="server_client", port=1, player=SELF)


def _publish(cfg: FakeCfg, *, tagged: bool) -> None:
    head = "# replies: tagged\n" if tagged else ""
    cfg.artifact(cfg.artifacts.commands, server=False).write_text(
        head + "diag\tnoarg\tstate dump\nsay\targ\tprint a line\n"
    )


def _game(cfg: FakeCfg, monkeypatch, *, replies) -> list[str]:
    """A responder standing at the trigger: consumes each payload, answers
    with the next entry from `replies` — `{id}` interpolates the request id's
    echo line, which is how a test writes a CORRECTLY tagged answer without
    knowing the uuid the session drew."""
    real = session_mod._claim_atomically
    result = cfg.artifact(artifacts_for(cfg.mod_name, SELF).result, server=False)
    payloads: list[str] = []
    queue = list(replies)

    def answering(trigger, payload):
        real(trigger, payload)
        payloads.append(payload)
        heard = parse(payload)
        echo = f"#{heard.request_id}\n" if heard.request_id else ""
        result.write_text(queue.pop(0).replace("{id}", echo))

    monkeypatch.setattr(session_mod, "_claim_atomically", answering)
    return payloads


# ---- the grammar, both directions ---------------------------------------


def test_parse_takes_the_id_off_the_end_and_nothing_else_changes():
    assert parse("diag@n43n#r-abc123") == Request("diag", "n43n", None, "r-abc123")
    assert parse("shot:topleft@n43n#r-0123456789ab") == Request(
        "shot", "n43n", "topleft", "r-0123456789ab"
    )
    assert parse("diag") == Request("diag", None, None, None)


def test_a_tail_that_is_not_an_id_is_payload_and_stays():
    """One character wrong and the whole tail stays where it was typed —
    `say:issue #beef` is four hex characters somebody WROTE, and stripping
    them would silently change what `say` says. Mirrors the C# rule, which is
    the point: two languages, one grammar."""
    for raw, argument in [
        ("say:issue #beef", "issue #beef"),
        ("say:fix #r-BEEF", "fix #r-BEEF"),
        ("say:see #r-abc", "see #r-abc"),
        ("say:see #r-abcg", "see #r-abcg"),
    ]:
        heard = parse(raw)
        assert heard.argument == argument, raw
        assert heard.request_id is None, raw


def test_a_bare_tagged_trigger_is_a_capture_with_an_id():
    assert parse("#r-abcd12") == Request("capture", request_id="r-abcd12")


def test_compose_appends_the_id_last_and_it_round_trips(cfg):
    _publish(cfg, tagged=True)
    published = commands_mod.read(cfg.artifact(cfg.artifacts.commands, server=False))

    payload = compose("diag", target=SELF, commands=published, request_id="r-abc123")

    assert payload == "diag@n43n#r-abc123"
    assert parse(payload) == Request("diag", SELF, None, "r-abc123")


def test_compose_refuses_an_id_the_grammar_would_leave_in_the_payload(cfg):
    """An id `parse` will not strip is not a correlator, it is a silent
    argument change — refused at the source rather than shipped."""
    _publish(cfg, tagged=True)
    published = commands_mod.read(cfg.artifact(cfg.artifacts.commands, server=False))

    for bad in ("BEEF", "r-BEEF", "r-abc", "r-xyz9", ""):
        with pytest.raises(TriggerError, match="request id"):
            compose("diag", commands=published, request_id=bad)


# ---- the capability line -------------------------------------------------


def test_the_capability_line_is_read_and_its_absence_means_no():
    tagged = commands_mod.parse_published(
        "# replies: tagged\ndiag\tnoarg\tstate dump\n"
    )
    plain = commands_mod.parse_published("diag\tnoarg\tstate dump\n")

    assert tagged.tagged_replies
    assert not plain.tagged_replies
    # And it is a comment to everything that does not know it, which is the
    # whole compatibility story: same verbs either way.
    assert tagged.names == plain.names


# ---- ask(), end to end ----------------------------------------------------


def test_ask_attaches_an_id_only_when_the_responder_says_it_tags(
    sess, cfg, monkeypatch
):
    _publish(cfg, tagged=True)
    payloads = _game(cfg, monkeypatch, replies=["{id}OK: tagged"])

    reply = sess.ask("diag", timeout=2.0)

    assert reply.text == "OK: tagged", "the echo line leaked into the answer"
    assert "#r-" in payloads[0], "no id rode the payload of a tagging responder"


def test_ask_sends_no_id_to_a_responder_that_never_published_the_capability(
    sess, cfg, monkeypatch
):
    """THE COMPATIBILITY HALF. An older vendored responder reads a trailing
    `#r-...` as part of the target, and a request matching nobody wedges the
    slot. The id must therefore be earned by the published list, not assumed."""
    _publish(cfg, tagged=False)
    payloads = _game(cfg, monkeypatch, replies=["OK: plain"])

    reply = sess.ask("diag", timeout=2.0)

    assert reply.text == "OK: plain"
    assert "#" not in payloads[0], "an id was sent to a responder that cannot strip it"


def test_a_stale_reply_wearing_another_requests_id_is_not_returned(
    sess, cfg, monkeypatch
):
    """THE DEFECT ITSELF, replayed. The file already holds a whole, stable,
    perfectly parseable answer — to an EARLIER request. Without the tag check
    the stability loop returns it; with it, the wait times out and says what
    it saw, which is the difference between a wrong answer and a diagnosis."""
    _publish(cfg, tagged=True)
    result = cfg.artifact(artifacts_for(cfg.mod_name, SELF).result, server=False)

    real = session_mod._claim_atomically

    def late_reply_lands(trigger, payload):
        real(trigger, payload)
        # The previous request's answer arrives AFTER this one unlinked the
        # reply file - the exact interleaving of a timed-out diag being
        # finished by the game while its successor waits.
        result.write_text("#r-000000000000\nOK: the previous answer")

    monkeypatch.setattr(session_mod, "_claim_atomically", late_reply_lands)

    with pytest.raises(TriggerError, match="DIFFERENT request id"):
        sess.ask("diag", timeout=1.5)


def test_the_right_reply_is_returned_even_after_a_stale_one_sat_there(
    sess, cfg, monkeypatch
):
    """POSITIVE CONTROL for the test above: the stale answer is waited PAST,
    not merely refused — when the real answer overwrites it inside the
    budget, the caller gets it."""
    _publish(cfg, tagged=True)
    result = cfg.artifact(artifacts_for(cfg.mod_name, SELF).result, server=False)

    real = session_mod._claim_atomically
    seen: list[str] = []

    def stale_then_real(trigger, payload):
        real(trigger, payload)
        seen.append(payload)
        heard = parse(payload)
        result.write_text("#r-000000000000\nOK: the previous answer")

        # The real answer lands on a later stability poll: patch the reader so
        # the second stable read finds it, without this test having to time
        # anything.
        reads = {"n": 0}
        real_read = type(result).read_text

        def eventually_the_real_one(self, *args, **kwargs):
            if self == result:
                reads["n"] += 1
                if reads["n"] >= 4:
                    real_read(self)  # keep the file honest for any later assertions
                    return f"#{heard.request_id}\nOK: yours"
            return real_read(self, *args, **kwargs)

        monkeypatch.setattr(type(result), "read_text", eventually_the_real_one)

    monkeypatch.setattr(session_mod, "_claim_atomically", stale_then_real)

    assert sess.ask("diag", timeout=3.0).text == "OK: yours"
