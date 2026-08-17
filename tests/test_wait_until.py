"""Waiting for the game to reach a state, rather than sleeping and hoping.

WHY THIS EXISTS

Every caller driving this server has written the same loop by hand: send a
trigger, sleep some number the author guessed, take a diag, check a field, give
up or go round again. A guessed sleep is wrong in both directions - too short
and the assertion reads the state BEFORE the thing happened, too long and every
run pays for the worst case. The first failure is the dangerous one, because it
looks exactly like the feature being broken.

So the wait belongs here, where the polling, the budget and the comparison can
be one thing that reports what it actually saw.

THE COMPARISON IS TYPED, and that is most of the value. `diag` already returns
`items: 1` as an int and `world-ready: True` as a bool; a wait that string-
matched them would reintroduce `"10" < "9"` and the truthiness of `"False"` at
the last possible moment, in the one place nothing downstream can catch it.

THE REFUSALS MATTER AS MUCH AS THE MATCHES. A wait on a field that does not
exist, or a numeric comparison against a composite string, can never come true.
Letting those run the clock out reports a TIMEOUT - which blames the game for a
request this side already knew was unanswerable, and sends the reader to look
at a game that is behaving perfectly.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tmodloader_mcp import session as session_mod
from tmodloader_mcp.diag import Diag
from tmodloader_mcp.session import Session
from tmodloader_mcp.triggers import TriggerError, artifacts_for


class _Cfg:
    """The two attributes a `Session` holds onto. `diag` is faked, so nothing
    here ever reaches a disk."""

    def __init__(self) -> None:
        self.root = Path("/fake")
        self.mod_name = "Biomancy"
        self.artifacts = artifacts_for("Biomancy")

    def artifact(self, name: str, *, server: bool) -> Path:
        return self.root / (f"server-{name}" if server else name)


def _session() -> Session:
    return Session(cfg=_Cfg(), mode="server_client", port=7810, player="n43n")


class _Clock:
    """A monotonic clock that only moves when something sleeps.

    Real sleeps would make the budget assertions take as long as the budgets
    they assert, and a wall clock cannot be asked "was this poll given what was
    LEFT" without a tolerance wide enough to hide the bug. Here the answer is
    exact.
    """

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(session_mod.time, "monotonic", lambda: self.now)
        monkeypatch.setattr(session_mod.time, "sleep", self.advance)

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Raiser:
    """A scripted reading that runs a callable instead of being returned.

    A bare exception instance in the script is simply raised; this is for the
    case where the poll has to SPEND time before failing, which an instance
    cannot express.
    """

    def __init__(self, run) -> None:
        self.run = run


def _watching(monkeypatch, readings, *, cost: float = 0.0, clock: _Clock | None = None):
    """Replace `Session.diag` with a script of readings. Returns the budgets seen.

    The LAST reading repeats forever, which is how "the condition never arrives"
    is expressed without writing out a poll count the test would then be pinned
    to. A reading may be an exception, which is raised instead of returned.

    `cost` is how long each poll takes on the fake clock, so a test can spend a
    budget on the diags themselves rather than only on the sleeps between them.
    """
    seen: list[float] = []
    queue = list(readings)

    def fake_diag(self, *, server=False, target=None, timeout=60.0):
        seen.append(timeout)
        # A FAKE CLOCK ONLY MOVES WHEN SOMETHING SLEEPS, so a wait that stopped
        # sleeping would poll here forever and hang the run rather than fail it.
        # That is not hypothetical - it is what the mutation these tests are
        # checked against does - and a harness that hangs on a broken build
        # tells you nothing you can read.
        if len(seen) > 500:
            raise AssertionError(
                f"{len(seen)} polls without the clock moving: the wait is "
                "spinning rather than sleeping between them"
            )
        if clock is not None and cost:
            clock.advance(cost)
        reading = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reading, _Raiser):
            reading.run()
        if isinstance(reading, BaseException):
            raise reading
        return Diag(fields=dict(reading), records={})

    monkeypatch.setattr(Session, "diag", fake_diag)
    return seen


#: A plausible top-level slice of a real dump, as `diag.parse` hands it back:
#: counters as ints, the heartbeat's booleans as bools, composites left whole,
#: and the mod's absence marker as None.
_FIELDS = {
    "items": 1,
    "vats": 1,
    "creep-tiles": 0,
    "npcs": "active=4 mutated=0",
    "side": "server netmode=2",
    "world-ready": True,
    "strain-readout": None,
}


# ---- matching --------------------------------------------------------------


def test_a_condition_already_true_matches_on_the_first_poll(monkeypatch):
    """No sleep, no second read: the state was already there when asked."""
    clock = _Clock()
    clock.install(monkeypatch)
    _watching(monkeypatch, [_FIELDS], clock=clock)

    got = _session().wait_until("items", ">=", "1", timeout=60.0, poll=2.0)

    assert got.matched
    assert got.polls == 1, "a condition already true cost more than one read"
    assert got.last == 1
    assert got.elapsed == 0.0


def test_a_condition_that_becomes_true_is_caught_on_the_poll_it_becomes_true(
    monkeypatch,
):
    """The whole point: the wait ends when the game gets there, not on a timer."""
    clock = _Clock()
    clock.install(monkeypatch)
    _watching(
        monkeypatch,
        [
            {**_FIELDS, "vats": 0},
            {**_FIELDS, "vats": 0},
            {**_FIELDS, "vats": 3},
            {**_FIELDS, "vats": 9},
        ],
        clock=clock,
    )

    got = _session().wait_until("vats", ">", "0", timeout=60.0, poll=2.0)

    assert got.matched
    assert got.polls == 3, "the wait did not stop on the poll that satisfied it"
    assert got.last == 3, "it reported a reading it never actually took"
    assert got.elapsed == pytest.approx(4.0), "two sleeps of two seconds"


def test_a_boolean_field_is_compared_as_a_boolean_not_as_text(monkeypatch):
    """`world-ready: False` is the exact value a text comparison gets wrong.

    Left as text every heartbeat boolean is TRUTHY, `"False"` included. This
    asserts the wait agrees with the parser that already fixed that, in both
    directions - and the second half is the control that makes the first mean
    something.
    """
    clock = _Clock()
    clock.install(monkeypatch)
    _watching(monkeypatch, [{**_FIELDS, "world-ready": False}], clock=clock)

    assert (
        not _session()
        .wait_until("world-ready", "==", "true", timeout=4.0, poll=2.0)
        .matched
    )

    _watching(monkeypatch, [{**_FIELDS, "world-ready": False}], clock=clock)
    assert (
        _session()
        .wait_until("world-ready", "==", "false", timeout=4.0, poll=2.0)
        .matched
    )


def test_contains_is_how_a_composite_field_is_waited_on(monkeypatch):
    """`npcs` is one string carrying two numbers, and the mod owns that format.

    Splitting it here would put this server back in the business of parsing the
    mod's formatting, which is the job `diag` exists to stop doing badly.
    """
    clock = _Clock()
    clock.install(monkeypatch)
    _watching(
        monkeypatch,
        [_FIELDS, {**_FIELDS, "npcs": "active=4 mutated=1"}],
        clock=clock,
    )

    got = _session().wait_until("npcs", "contains", "mutated=1", timeout=60.0, poll=2.0)

    assert got.matched
    assert got.polls == 2


# ---- timing out ------------------------------------------------------------


def test_a_condition_that_never_arrives_reports_what_it_last_saw(monkeypatch):
    """A timeout that says only "timed out" makes the caller take another diag
    to find out why - against a state that has moved on since."""
    clock = _Clock()
    clock.install(monkeypatch)
    _watching(monkeypatch, [{**_FIELDS, "vats": 2}], clock=clock)

    got = _session().wait_until("vats", ">=", "10", timeout=10.0, poll=2.0)

    assert not got.matched
    assert got.last == 2, "the caller is told it timed out and not what it saw"
    assert got.polls == 5
    assert got.elapsed == pytest.approx(10.0)


def test_the_whole_call_spends_one_budget_across_every_poll(monkeypatch):
    """ONE budget, the rule `_left_of` already enforces one layer down.

    A wait handing its full `timeout` to each `diag` would let a call bounded to
    10s run for as long as the game took to answer ten times - and the caller
    who chose 10s to fit their own budget is the one who least expects that.
    """
    clock = _Clock()
    clock.install(monkeypatch)
    seen = _watching(monkeypatch, [{**_FIELDS, "vats": 0}], cost=1.0, clock=clock)

    got = _session().wait_until("vats", ">", "0", timeout=10.0, poll=2.0)

    assert not got.matched
    assert seen == [10.0, 7.0, 4.0, 1.0], (
        "each poll was handed a budget that ignored what the polls before it "
        f"had already spent: {seen}"
    )
    assert got.elapsed == pytest.approx(10.0), (
        "the call overran the timeout it was given"
    )


def test_a_poll_is_never_started_with_no_budget_left(monkeypatch):
    """A `diag(timeout=0)` reports "the game may not be polling" - blaming the
    game for a wait this side never made. So the loop stops instead."""
    clock = _Clock()
    clock.install(monkeypatch)
    seen = _watching(monkeypatch, [{**_FIELDS, "vats": 0}], cost=2.0, clock=clock)

    _session().wait_until("vats", ">", "0", timeout=4.0, poll=2.0)

    assert seen == [4.0], f"a poll was started with nothing left to spend on it: {seen}"


def test_a_game_that_stops_answering_inside_the_budget_is_raised_not_swallowed(
    monkeypatch,
):
    """A refused or unanswered diag is a real failure, not "not yet".

    Returning `matched=False` for it would report a condition that did not come
    true, when what happened is that nobody was asked.
    """
    clock = _Clock()
    clock.install(monkeypatch)
    _watching(
        monkeypatch,
        [{**_FIELDS, "vats": 0}, TriggerError("the game refused a diag: no responder")],
        clock=clock,
    )

    with pytest.raises(TriggerError, match="no responder"):
        _session().wait_until("vats", ">", "0", timeout=60.0, poll=2.0)


def test_a_final_poll_cut_short_by_the_budget_reports_rather_than_raises(monkeypatch):
    """The same exception at the very end of the budget means something else.

    The last poll is handed whatever is left, so a tight budget makes it time
    out ON PURPOSE. Raising there would turn "the condition did not come true
    in 10s" into "the game is not responding", about a game that answered every
    earlier poll.
    """
    clock = _Clock()
    clock.install(monkeypatch)

    late = TriggerError("no diag dump within 1s")

    def spend_then_fail(*_a, **_k):
        clock.advance(60.0)
        raise late

    readings = [{**_FIELDS, "vats": 0}, _Raiser(spend_then_fail)]
    _watching(monkeypatch, readings, clock=clock)

    got = _session().wait_until("vats", ">", "0", timeout=60.0, poll=2.0)

    assert not got.matched
    assert got.last == 0, "the reading it did get was thrown away with the error"
    assert got.note and "no diag dump within 1s" in got.note, (
        "the caller is not told the last poll was cut off, so a game that has "
        f"genuinely stopped answering is indistinguishable from one that has not: {got.note}"
    )


# ---- refusing what can never come true -------------------------------------


def test_an_unknown_field_refuses_at_once_and_names_the_fields_that_exist(monkeypatch):
    """A typo is the commonest reason a wait never matches, and the fields are
    right there in the dump that was just read."""
    clock = _Clock()
    clock.install(monkeypatch)
    seen = _watching(monkeypatch, [_FIELDS], clock=clock)

    with pytest.raises(TriggerError) as caught:
        _session().wait_until("vat", ">", "0", timeout=60.0, poll=2.0)

    assert "vat" in str(caught.value)
    assert "vats" in str(caught.value), (
        "the refusal does not name the fields that DO exist, which is the whole "
        f"of what makes it actionable: {caught.value}"
    )
    assert len(seen) == 1, "it kept polling a field that will never appear"

    # POSITIVE CONTROL, same test: the spelling it suggested works against the
    # very same dump, so the refusal is about the name and not about the wait.
    _watching(monkeypatch, [_FIELDS], clock=clock)
    assert _session().wait_until("vats", ">", "0", timeout=60.0, poll=2.0).matched


def test_a_numeric_comparison_against_a_composite_string_refuses(monkeypatch):
    """`npcs > 3` reads perfectly and can never be true: the value is text.

    This is the mistake the design was corrected for - `npcs.active` does not
    exist, `diag` splits top-level `key: value` and nothing further - so the
    refusal has to say what the value ACTUALLY is, or the reader tries the same
    thing again.
    """
    clock = _Clock()
    clock.install(monkeypatch)
    _watching(monkeypatch, [_FIELDS], clock=clock)

    with pytest.raises(TriggerError) as caught:
        _session().wait_until("npcs", ">", "3", timeout=60.0, poll=2.0)

    assert "active=4 mutated=0" in str(caught.value), (
        f"the refusal does not show the value it is refusing to order: {caught.value}"
    )

    # POSITIVE CONTROL: the same field, waited on the way it can be.
    _watching(monkeypatch, [_FIELDS], clock=clock)
    assert (
        _session()
        .wait_until("npcs", "contains", "active=4", timeout=60.0, poll=2.0)
        .matched
    )


def test_contains_against_a_counter_refuses(monkeypatch):
    """The mirror image, and the more tempting one: `items contains 1` looks
    like it should work and is a substring test against an integer."""
    clock = _Clock()
    clock.install(monkeypatch)
    _watching(monkeypatch, [_FIELDS], clock=clock)

    with pytest.raises(TriggerError) as caught:
        _session().wait_until("items", "contains", "1", timeout=60.0, poll=2.0)

    assert "items" in str(caught.value)

    _watching(monkeypatch, [_FIELDS], clock=clock)
    assert _session().wait_until("items", "==", "1", timeout=60.0, poll=2.0).matched


def test_a_value_that_is_not_a_number_refuses_against_a_counter(monkeypatch):
    """The refusal is about the CALLER's value rather than the game's."""
    clock = _Clock()
    clock.install(monkeypatch)
    _watching(monkeypatch, [_FIELDS], clock=clock)

    with pytest.raises(TriggerError, match="lots"):
        _session().wait_until("items", ">=", "lots", timeout=60.0, poll=2.0)


def test_an_unknown_operator_names_the_ones_that_exist(monkeypatch):
    """Refused before the first poll: nothing about the game can make `=~`
    mean something."""
    clock = _Clock()
    clock.install(monkeypatch)
    seen = _watching(monkeypatch, [_FIELDS], clock=clock)

    with pytest.raises(TriggerError, match="contains"):
        _session().wait_until("items", "=~", "1", timeout=60.0, poll=2.0)

    assert seen == [], "it asked the game about a comparison it cannot make"


def test_an_absence_marker_is_not_yet_rather_than_never(monkeypatch):
    """THE LINE BETWEEN A REFUSAL AND A NON-MATCH.

    A composite string will never become an integer, so ordering one is
    refused. An ABSENCE is different in kind: `strain-readout: NONE` is the mod
    saying "nothing showing right now", and the next poll may well show
    something. Refusing that would make this unusable for the waits it is most
    obviously for - wait until a thing that is not there yet appears.
    """
    clock = _Clock()
    clock.install(monkeypatch)
    _watching(
        monkeypatch,
        [_FIELDS, _FIELDS, {**_FIELDS, "strain-readout": 3}],
        clock=clock,
    )

    got = _session().wait_until("strain-readout", ">", "0", timeout=60.0, poll=2.0)

    assert got.matched, (
        "an absence was refused as if it were a comparison that could never "
        "come true, which is exactly the wait this is most useful for"
    )
    assert got.polls == 3

    # And the marker itself is addressable, so "wait until it goes quiet again"
    # is expressible too.
    _watching(monkeypatch, [_FIELDS], clock=clock)
    assert (
        _session()
        .wait_until("strain-readout", "==", "none", timeout=60.0, poll=2.0)
        .matched
    )


# ---- changed ---------------------------------------------------------------


def test_changed_fires_on_a_shift_and_not_before(monkeypatch):
    """For the waits where the caller does not know the target value - only
    that the number they are watching should stop being what it is."""
    clock = _Clock()
    clock.install(monkeypatch)
    _watching(
        monkeypatch,
        [_FIELDS, _FIELDS, {**_FIELDS, "items": 4}],
        clock=clock,
    )

    got = _session().wait_until("items", "changed", timeout=60.0, poll=2.0)

    assert got.matched
    assert got.polls == 3, "it fired on a reading identical to its baseline"
    assert got.last == 4

    # POSITIVE CONTROL: an unchanging field does NOT fire, so the match above
    # is about the shift rather than about the second poll existing.
    _watching(monkeypatch, [_FIELDS], clock=clock)
    steady = _session().wait_until("items", "changed", timeout=10.0, poll=2.0)
    assert not steady.matched
    assert steady.last == 1


def test_the_baseline_is_the_first_reading_not_a_value_the_caller_supplies(
    monkeypatch,
):
    """`changed` with a value would read as "changed to", which it is not."""
    clock = _Clock()
    clock.install(monkeypatch)
    _watching(monkeypatch, [_FIELDS], clock=clock)

    with pytest.raises(TriggerError, match="changed"):
        _session().wait_until("items", "changed", "4", timeout=60.0, poll=2.0)


def test_every_other_operator_needs_a_value(monkeypatch):
    """`items >` is not a comparison, and running the clock out on it would
    report a timeout for a request that was never one."""
    clock = _Clock()
    clock.install(monkeypatch)
    seen = _watching(monkeypatch, [_FIELDS], clock=clock)

    with pytest.raises(TriggerError, match="value"):
        _session().wait_until("items", ">", timeout=60.0, poll=2.0)

    assert seen == []


# ---- the real clock, once --------------------------------------------------


def test_it_actually_sleeps_between_polls(monkeypatch):
    """Every test above runs on a fake clock, which cannot tell a wait that
    sleeps from one that spins. This one uses the real one.

    A busy loop would pass all of them while hammering the game with diags as
    fast as it could answer - which is the failure mode a poll interval exists
    to prevent.
    """
    _watching(monkeypatch, [{**_FIELDS, "vats": 0}])

    began = time.monotonic()
    got = _session().wait_until("vats", ">", "0", timeout=0.3, poll=0.1)
    took = time.monotonic() - began

    assert not got.matched
    assert got.polls <= 4, f"it polled {got.polls} times in 0.3s - it is spinning"
    assert took >= 0.2, f"the whole wait took {took:.3f}s and never slept"
