"""The session lifecycle: the pure transition table, and the audit trail.

`advance` is where the confidence in this unit lives. It has no clients, no
clock and no table, so every legal and illegal move can be asserted exhaustively
— which is the point of putting the rule there rather than spreading it across
five handlers.
"""

from __future__ import annotations

import itertools

import pytest

from shared import session
from shared.session import (
    APPROVED,
    AWAITING_REVIEW,
    COMPLETE,
    CREATED,
    FAILED,
    IN_REVIEW,
    PROCESSING,
    REJECTED,
    UPLOADED,
    InvalidTransitionError,
    advance,
)

# (current, event, expected) — the full set of legal moves, written out here
# independently of the table the implementation reads, so a typo in one is not
# silently mirrored by the other.
LEGAL: list[tuple[str | None, str, str]] = [
    (None, session.PRESIGN, CREATED),
    (CREATED, session.UPLOAD_COMPLETE, UPLOADED),
    (UPLOADED, session.EXECUTION_STARTED, PROCESSING),
    (PROCESSING, session.REVIEW_PARKED, AWAITING_REVIEW),
    (AWAITING_REVIEW, session.REVIEW_OPENED, IN_REVIEW),
    (AWAITING_REVIEW, session.APPROVE, APPROVED),
    (IN_REVIEW, session.APPROVE, APPROVED),
    (AWAITING_REVIEW, session.REJECT, REJECTED),
    (IN_REVIEW, session.REJECT, REJECTED),
    (APPROVED, session.FINAL_WRITTEN, COMPLETE),
    (CREATED, session.FAIL, FAILED),
    (UPLOADED, session.FAIL, FAILED),
    (PROCESSING, session.FAIL, FAILED),
    (AWAITING_REVIEW, session.FAIL, FAILED),
    (IN_REVIEW, session.FAIL, FAILED),
    (APPROVED, session.FAIL, FAILED),
]

LEGAL_PAIRS = {(cur, event) for cur, event, _ in LEGAL}


@pytest.mark.parametrize(("current", "event", "expected"), LEGAL)
def test_legal_transitions(current: str | None, event: str, expected: str) -> None:
    assert advance(current, event) == expected


@pytest.mark.parametrize(
    ("current", "event"),
    [
        (cur, ev)
        for cur, ev in itertools.product([None, *sorted(session.STATUSES)], sorted(session.EVENTS))
        if (cur, ev) not in LEGAL_PAIRS
    ],
)
def test_every_other_pair_is_rejected(current: str | None, event: str) -> None:
    """Anything not in the table raises. Silence would be the bug."""
    with pytest.raises(InvalidTransitionError):
        advance(current, event)


def test_the_transition_the_ruling_calls_out() -> None:
    """APPROVED must never fall back to a pending state."""
    with pytest.raises(InvalidTransitionError):
        advance(APPROVED, session.REVIEW_PARKED)
    with pytest.raises(InvalidTransitionError):
        advance(APPROVED, session.REVIEW_OPENED)


def test_terminal_states_go_nowhere() -> None:
    for terminal in (REJECTED, COMPLETE, FAILED):
        assert terminal in session.TERMINAL
        for event in session.EVENTS:
            with pytest.raises(InvalidTransitionError):
                advance(terminal, event)


def test_reject_survives_a_state_machine_catch() -> None:
    """The Catch handler asks for FAIL; a rejected job must refuse it."""
    with pytest.raises(InvalidTransitionError):
        advance(REJECTED, session.FAIL)


def test_unknown_status_and_event_are_rejected() -> None:
    with pytest.raises(InvalidTransitionError):
        advance("PENDING", session.APPROVE)
    with pytest.raises(InvalidTransitionError):
        advance(CREATED, "teleport")


def test_empty_current_is_treated_as_no_session() -> None:
    assert advance("", session.PRESIGN) == CREATED


def test_advance_does_no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Purity guard: if advance ever grew a table read, this would break."""
    monkeypatch.setattr(
        session, "_reviews_table", lambda: pytest.fail("advance must not touch DynamoDB")
    )
    assert advance(PROCESSING, session.REVIEW_PARKED) == AWAITING_REVIEW
