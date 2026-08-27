"""Session lifecycle for one uploaded questionnaire.

The human ruling describes seven steps — upload, work record, first-pass RAG,
quoted sources, HITL review, download of the filled questionnaire, and
re-ingestion of that questionnaire. Steps 2, 5, 6 and 7 all need to agree on
*where a job is*, and until now each handler invented its own answer from a
mixture of ``status`` and ``review_status``.

``session_status`` on the Jobs table is the single authoritative lifecycle.
``status`` and ``review_status`` are still written exactly as before so the
existing UI polling keeps working; they are now derived views, not the source
of truth.

Two pieces live here:

``advance``
    A pure, table-driven transition function. No I/O, no clients, no clock.
    Every legal move is one row in ``_TRANSITIONS``; anything absent from that
    table is rejected with :class:`InvalidTransitionError` rather than being
    silently written. That explicitness is the point: a job that is APPROVED
    must never quietly fall back to PENDING because a retried Lambda replayed
    an earlier step.

``log_step``
    Appends one row to the Reviews table — the audit trail of who moved the
    job and when. It is deliberately **fail-soft**: an audit write that fails
    is logged and swallowed. Losing an audit row is bad; failing a customer's
    questionnaire because we could not write one is worse.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import boto3

from shared.logging_config import configure_logging

logger = configure_logging()

__all__ = [
    "APPROVED",
    "AWAITING_REVIEW",
    "COMPLETE",
    "CREATED",
    "EVENTS",
    "FAILED",
    "IN_REVIEW",
    "PROCESSING",
    "REJECTED",
    "STATUSES",
    "TERMINAL",
    "UPLOADED",
    "InvalidTransitionError",
    "advance",
    "log_step",
    "now_iso",
]

# ---------------------------------------------------------------------------
# Lifecycle vocabulary
# ---------------------------------------------------------------------------

CREATED = "CREATED"  # upload_api.presign — URL issued, no bytes yet
UPLOADED = "UPLOADED"  # upload_api.start — PUT done, before StartExecution
PROCESSING = "PROCESSING"  # upload_api.start — execution running
AWAITING_REVIEW = "AWAITING_REVIEW"  # review_gate — task token parked
IN_REVIEW = "IN_REVIEW"  # review_api.get_review, first GET only
APPROVED = "APPROVED"  # review_api.approve — SendTaskSuccess sent
REJECTED = "REJECTED"  # review_api.reject — terminal
COMPLETE = "COMPLETE"  # excel_writer final pass — downloadable
FAILED = "FAILED"  # state machine Catch — terminal

STATUSES: frozenset[str] = frozenset(
    {
        CREATED,
        UPLOADED,
        PROCESSING,
        AWAITING_REVIEW,
        IN_REVIEW,
        APPROVED,
        REJECTED,
        COMPLETE,
        FAILED,
    }
)

#: Nothing leaves these. ``APPROVED`` is deliberately *not* terminal — the
#: final workbook write still has to happen before the human can download it.
TERMINAL: frozenset[str] = frozenset({REJECTED, COMPLETE, FAILED})

# Events, named for the thing that happened rather than the state it produces,
# so a caller cannot accidentally assert its own conclusion.
PRESIGN = "presign"
UPLOAD_COMPLETE = "upload_complete"
EXECUTION_STARTED = "execution_started"
REVIEW_PARKED = "review_parked"
REVIEW_OPENED = "review_opened"
APPROVE = "approve"
REJECT = "reject"
FINAL_WRITTEN = "final_written"
FAIL = "fail"

EVENTS: frozenset[str] = frozenset(
    {
        PRESIGN,
        UPLOAD_COMPLETE,
        EXECUTION_STARTED,
        REVIEW_PARKED,
        REVIEW_OPENED,
        APPROVE,
        REJECT,
        FINAL_WRITTEN,
        FAIL,
    }
)

#: (current status, event) → next status. ``None`` as the current status means
#: "no session record yet", which only ``presign`` may act on.
_TRANSITIONS: dict[tuple[str | None, str], str] = {
    (None, PRESIGN): CREATED,
    (CREATED, UPLOAD_COMPLETE): UPLOADED,
    (UPLOADED, EXECUTION_STARTED): PROCESSING,
    (PROCESSING, REVIEW_PARKED): AWAITING_REVIEW,
    (AWAITING_REVIEW, REVIEW_OPENED): IN_REVIEW,
    # A reviewer may approve or reject straight from the queue without ever
    # opening the job, so both AWAITING_REVIEW and IN_REVIEW are valid origins.
    (AWAITING_REVIEW, APPROVE): APPROVED,
    (IN_REVIEW, APPROVE): APPROVED,
    (AWAITING_REVIEW, REJECT): REJECTED,
    (IN_REVIEW, REJECT): REJECTED,
    (APPROVED, FINAL_WRITTEN): COMPLETE,
    # Anything still in flight can fail. Terminal states cannot: a rejected job
    # whose SendTaskFailure surfaces as a state-machine error must stay
    # REJECTED, not be relabelled FAILED by the Catch handler.
    (CREATED, FAIL): FAILED,
    (UPLOADED, FAIL): FAILED,
    (PROCESSING, FAIL): FAILED,
    (AWAITING_REVIEW, FAIL): FAILED,
    (IN_REVIEW, FAIL): FAILED,
    (APPROVED, FAIL): FAILED,
}


class InvalidTransitionError(ValueError):
    """Raised when a caller asks for a lifecycle move the table does not allow."""


def advance(current: str | None, event: str) -> str:
    """Return the status reached by applying ``event`` to ``current``.

    Pure: no clients, no clock, no table reads. ``current`` may be ``None`` or
    an empty string for a job that has no session record yet.

    Raises:
        InvalidTransitionError: unknown status, unknown event, or a (status, event)
            pair that is not a legal move.
    """
    cur: str | None = current or None
    if cur is not None and cur not in STATUSES:
        raise InvalidTransitionError(f"unknown session_status {current!r}")
    if event not in EVENTS:
        raise InvalidTransitionError(f"unknown lifecycle event {event!r}")
    try:
        return _TRANSITIONS[(cur, event)]
    except KeyError:
        raise InvalidTransitionError(
            f"{cur or '<none>'} --{event}--> is not a legal transition"
        ) from None


def now_iso() -> str:
    """Timestamp used for both ``updated_at`` and the Reviews sort key."""
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Reviews table — append-only HITL step log
# ---------------------------------------------------------------------------

_ddb_resource: Any = None


def _reviews_table() -> Any:
    global _ddb_resource
    if _ddb_resource is None:
        _ddb_resource = boto3.resource("dynamodb")
    return _ddb_resource.Table(os.environ["REVIEWS_TABLE"])


def log_step(
    job_id: str,
    step: str,
    actor: str = "system",
    detail: dict[str, Any] | None = None,
    *,
    reviewed_at: str | None = None,
) -> bool:
    """Append one row to Reviews. Returns True when the row was written.

    ``step`` is the ``session_status`` being entered. ``actor`` is ``"system"``
    for pipeline transitions and, for the human-driven ones, the ``email``
    claim of the reviewer's verified JWT (``sub`` when the pool carries no
    email) — see ``review_api.handler.jwt_actor``. ``detail`` is a small map
    whose shape depends on the
    step (``{question_count}``, ``{tier_counts}``, ``{edited, sme_edit_count}``,
    ``{reason}``).

    Never raises. The audit trail is not on the critical path of answering a
    questionnaire, and a Reviews write that fails must not take the pipeline
    down with it.
    """
    try:
        _reviews_table().put_item(
            Item={
                "jobId": job_id,
                "reviewedAt": reviewed_at or now_iso(),
                "step": step,
                "actor": actor,
                "detail": detail or {},
            }
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft by design, see docstring
        logger.warning("session.log_step_failed", job_id=job_id, step=step, error=str(exc))
        return False
    return True
