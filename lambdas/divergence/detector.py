"""Retrospective divergence detection across prior answers.

The problem this solves
-----------------------
Over years of answering security questionnaires, an organisation accumulates
answers written by different people, at different times, to different
audiences. They drift. Nobody notices, because no human ever reads two years
of questionnaires side by side.

The failure modes are not equally bad, and the difference matters:

  We told Acme we rotate keys annually.
  We told Northwind we rotate keys quarterly.
  Both in Q1 2026.
      -> Two customers hold contradictory written statements about the same
         control in the same period. There is no benign explanation. If both
         ever land in front of the same auditor or acquirer, it is a finding.

  We told Acme annually in 2024.
  We told Northwind quarterly in 2026.
      -> Almost certainly the control improved. Legitimate, but the 2024
         answer is now wrong and must stop being cited.

Same surface symptom, completely different response. Collapsing them into one
"inconsistency" alert trains reviewers to ignore the alert, so this module
keeps them apart.

Design constraint
-----------------
The model is asked exactly one narrow question — *do these two statements
contradict each other?* — and nothing else. It does not assign severity, pick
a tier, or decide what to do. That is done by the deterministic matrix in
`classify`, which is testable, explainable to an auditor, and consistent with
the pipeline's existing rule that no tier is ever set by an LLM's own
assessment of its work.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from itertools import combinations
from typing import Callable, Iterable, Sequence

from shared.models import Tier
from shared.provenance import Provenance

__all__ = [
    "ClaimRelation",
    "DivergenceKind",
    "PriorStatement",
    "DivergenceFinding",
    "DivergenceReport",
    "DivergenceDetector",
]

DETECTOR_VERSION = "2026-08-26"

#: Two answers this far apart are treated as belonging to different periods,
#: so a difference between them is plausibly evolution rather than
#: inconsistency. Six months is deliberately generous: the cost of calling a
#: legitimate improvement a contradiction is a reviewer learning to dismiss
#: findings, which is worse than a missed one at POC stage.
DEFAULT_PERIOD_DAYS = 180


class ClaimRelation(str, Enum):
    """The only judgement the model is asked to make."""

    AGREE = "agree"
    CONTRADICT = "contradict"
    UNRELATED = "unrelated"      # different sub-topics; not comparable
    UNCERTAIN = "uncertain"      # model declined to call it


class DivergenceKind(str, Enum):
    CUSTOMER_DIVERGENCE = "customer_divergence"
    TEMPORAL_DRIFT = "temporal_drift"
    UNRESOLVED_DIVERGENCE = "unresolved_divergence"
    STALE_CONSENSUS = "stale_consensus"
    SELF_CONTRADICTION = "self_contradiction"


#: Tier floor each kind forces. Consumed by the hard-rules engine, which
#: applies it the same way it applies the pricing and compliance rules —
#: this module never sets a tier itself.
SEVERITY: dict[DivergenceKind, Tier] = {
    DivergenceKind.CUSTOMER_DIVERGENCE: Tier.RED,
    DivergenceKind.SELF_CONTRADICTION: Tier.RED,
    DivergenceKind.UNRESOLVED_DIVERGENCE: Tier.AMBER,
    DivergenceKind.TEMPORAL_DRIFT: Tier.AMBER,
    DivergenceKind.STALE_CONSENSUS: Tier.AMBER,
}


@dataclass(frozen=True)
class PriorStatement:
    """One prior answer, with the attribution that makes comparison possible."""

    statement_id: str
    question_text: str
    answer_text: str
    provenance: Provenance

    @property
    def customer_key(self) -> str | None:
        """Prefer the stable id — customers get renamed and acquired, and
        grouping on display name would split one customer's history in two."""
        return self.provenance.customer_id or self.provenance.customer_name


@dataclass(frozen=True)
class DivergenceFinding:
    kind: DivergenceKind
    tier_floor: Tier
    left: PriorStatement
    right: PriorStatement
    detail: str
    days_apart: int | None = None

    def render(self) -> str:
        """One-line summary for the Excel reviewer comment."""
        return f"[{self.kind.value.replace('_', ' ').upper()}] {self.detail}"


@dataclass
class DivergenceReport:
    findings: list[DivergenceFinding] = field(default_factory=list)
    compared_pairs: int = 0
    statements_examined: int = 0

    @property
    def tier_floor(self) -> Tier | None:
        """Most severe floor across all findings, or None if clean."""
        if not self.findings:
            return None
        if any(f.tier_floor is Tier.RED for f in self.findings):
            return Tier.RED
        return Tier.AMBER

    @property
    def summary(self) -> dict:
        by_kind: dict[str, int] = {}
        for f in self.findings:
            by_kind[f.kind.value] = by_kind.get(f.kind.value, 0) + 1
        return {
            "detector_version": DETECTOR_VERSION,
            "statements_examined": self.statements_examined,
            "compared_pairs": self.compared_pairs,
            "findings": len(self.findings),
            "findings_by_kind": by_kind,
            "tier_floor": self.tier_floor.value if self.tier_floor else None,
        }


#: Signature of the injected comparator. Kept narrow on purpose — see module
#: docstring. In deployment this is a Haiku call; in tests it is a stub.
Comparator = Callable[[str, str, str], ClaimRelation]


class DivergenceDetector:
    def __init__(
        self,
        comparator: Comparator,
        period_days: int = DEFAULT_PERIOD_DAYS,
        max_pairs: int = 60,
    ) -> None:
        self._compare = comparator
        self._period = timedelta(days=period_days)
        # Pairwise comparison is O(n^2) in model calls. A heavily-answered
        # topic can carry dozens of priors, and 40 statements is 780 calls for
        # one questionnaire cell. Cap it and compare the most recent, which is
        # where a live contradiction is most likely to still matter.
        self._max_pairs = max_pairs

    # -- classification ------------------------------------------------------

    def classify(
        self, left: PriorStatement, right: PriorStatement, relation: ClaimRelation
    ) -> DivergenceFinding | None:
        """Deterministic. Given a relation, decide what kind of problem it is.

        The matrix, for the contradictory case:

            same customer, any period      -> SELF_CONTRADICTION   (red)
            different customer, same period-> CUSTOMER_DIVERGENCE  (red)
            different customer, apart      -> UNRESOLVED_DIVERGENCE(amber)

        Note that same-customer contradiction is red rather than amber even
        when the answers are years apart. Telling one customer two different
        things over time is a different and more awkward conversation than the
        control having improved, because they hold both documents.
        """
        days_apart = self._days_apart(left, right)

        if relation is ClaimRelation.CONTRADICT:
            same_customer = (
                left.customer_key is not None
                and left.customer_key == right.customer_key
            )
            if same_customer:
                return DivergenceFinding(
                    kind=DivergenceKind.SELF_CONTRADICTION,
                    tier_floor=SEVERITY[DivergenceKind.SELF_CONTRADICTION],
                    left=left, right=right, days_apart=days_apart,
                    detail=(
                        f"{self._who(left)} received contradictory answers to this "
                        f"question {self._gap(days_apart)}. Both statements are in "
                        f"their possession."
                    ),
                )

            if days_apart is not None and days_apart <= self._period.days:
                return DivergenceFinding(
                    kind=DivergenceKind.CUSTOMER_DIVERGENCE,
                    tier_floor=SEVERITY[DivergenceKind.CUSTOMER_DIVERGENCE],
                    left=left, right=right, days_apart=days_apart,
                    detail=(
                        f"{self._who(left)} and {self._who(right)} were given "
                        f"contradictory answers {self._gap(days_apart)} — within the "
                        f"same {self._period.days}-day window, so this is not "
                        f"explained by the control changing."
                    ),
                )

            return DivergenceFinding(
                kind=DivergenceKind.UNRESOLVED_DIVERGENCE,
                tier_floor=SEVERITY[DivergenceKind.UNRESOLVED_DIVERGENCE],
                left=left, right=right, days_apart=days_apart,
                detail=(
                    f"{self._who(left)} and {self._who(right)} were given differing "
                    f"answers {self._gap(days_apart)}. Could be the control "
                    f"evolving or a genuine inconsistency — confirm which before "
                    f"citing either."
                ),
            )

        if relation is ClaimRelation.UNCERTAIN:
            # Treat as unresolved rather than clean. A comparator that cannot
            # decide is not evidence of agreement.
            return DivergenceFinding(
                kind=DivergenceKind.UNRESOLVED_DIVERGENCE,
                tier_floor=SEVERITY[DivergenceKind.UNRESOLVED_DIVERGENCE],
                left=left, right=right, days_apart=days_apart,
                detail=(
                    f"Could not determine whether the answers given to "
                    f"{self._who(left)} and {self._who(right)} are consistent. "
                    f"Manual comparison required."
                ),
            )

        return None

    # -- orchestration -------------------------------------------------------

    def detect(self, statements: Sequence[PriorStatement]) -> DivergenceReport:
        report = DivergenceReport(statements_examined=len(statements))
        if len(statements) < 2:
            # A single prior can still be a problem if it is the only thing
            # backing the answer and it has expired.
            report.findings.extend(self._stale_findings(statements))
            return report

        ordered = sorted(
            statements,
            key=lambda s: s.provenance.answered_at or s.provenance.approved_at
            or _EPOCH,
            reverse=True,
        )

        for left, right in self._pairs(ordered):
            report.compared_pairs += 1
            try:
                relation = self._compare(
                    left.question_text, left.answer_text, right.answer_text
                )
            except Exception:  # noqa: BLE001
                # A comparator failure must not silently read as agreement.
                relation = ClaimRelation.UNCERTAIN

            finding = self.classify(left, right, relation)
            if finding is not None:
                report.findings.append(finding)

        if not report.findings:
            report.findings.extend(self._stale_findings(ordered))

        return report

    def _pairs(
        self, ordered: Sequence[PriorStatement]
    ) -> Iterable[tuple[PriorStatement, PriorStatement]]:
        count = 0
        for pair in combinations(ordered, 2):
            if count >= self._max_pairs:
                return
            count += 1
            yield pair

    @staticmethod
    def _stale_findings(
        statements: Sequence[PriorStatement],
    ) -> list[DivergenceFinding]:
        """Everything agrees, but everything is also out of date.

        Worth surfacing separately: consistent-and-expired reads as a clean
        result on every other signal in the pipeline, which is exactly how
        stale answers get shipped.
        """
        if not statements:
            return []
        if not all(s.provenance.is_expired for s in statements):
            return []
        newest = statements[0]
        return [
            DivergenceFinding(
                kind=DivergenceKind.STALE_CONSENSUS,
                tier_floor=SEVERITY[DivergenceKind.STALE_CONSENSUS],
                left=newest, right=newest,
                detail=(
                    f"All {len(statements)} prior answer(s) are consistent but every "
                    f"one has expired. Re-confirm with the control owner before "
                    f"reusing."
                ),
            )
        ]

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _days_apart(left: PriorStatement, right: PriorStatement) -> int | None:
        a = left.provenance.answered_at or left.provenance.approved_at
        b = right.provenance.answered_at or right.provenance.approved_at
        if a is None or b is None:
            return None
        return abs((a - b).days)

    @staticmethod
    def _who(s: PriorStatement) -> str:
        return s.provenance.customer_name or "an unattributed source"

    @staticmethod
    def _gap(days: int | None) -> str:
        if days is None:
            return "on unknown dates"
        if days == 0:
            return "on the same day"
        if days < 60:
            return f"{days} days apart"
        if days < 730:
            return f"about {round(days / 30)} months apart"
        return f"about {round(days / 365)} years apart"


from datetime import datetime, timezone  # noqa: E402  (used only as a sort floor)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
