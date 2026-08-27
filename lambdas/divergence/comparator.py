"""The production comparator behind `DivergenceDetector`.

One model call answers one question: *do these two statements contradict each
other?* Nothing else. The model does not see dates, customer names, tiers or
kinds, and is not asked what to do about the answer it gives — all of that is
decided by the deterministic matrix in `detector.classify`. Keeping the model's
job this small is what makes the detector explainable to an auditor, and it is
the same rule that keeps `confidence_scorer` and `hard_rules` deterministic.

Two properties are load-bearing:

**Failure is never agreement.** Every path that does not produce a confident
reading — a throttled call, an unparseable response, a model that hedges —
returns UNCERTAIN, which the detector floors at AMBER. A comparator that
returned AGREE when it was actually broken would silently mark contradictory
answers clean, which is the one outcome this whole module exists to prevent.

**Comparison is symmetric, so the cache is too.** "Does A contradict B" and
"does B contradict A" are the same question, and the pairwise sweep is O(n²) in
model calls — 40 priors is 780 calls for a single questionnaire cell. The pair
is sorted before it reaches the cache, so the reversed pair is free. Sorting
also makes the result independent of the order the detector happened to walk
the pairs in, which is one less thing to explain to an auditor.
"""

from __future__ import annotations

import functools
import re
from typing import Any

from divergence.detector import ClaimRelation
from shared.logging_config import configure_logging

logger = configure_logging()

__all__ = ["BedrockComparator", "SYSTEM_PROMPT", "parse_relation"]

COMPARATOR_VERSION = "2026-08-27"

#: Answers are trimmed before they reach the model. A security-questionnaire
#: answer states its control in the first paragraph; the tail is boilerplate,
#: and paying for it on every one of several hundred pairwise calls is waste.
MAX_ANSWER_CHARS = 1500

#: The system prompt is stable across every call in a run, so Bedrock prompt
#: caching (enabled by default in shared.bedrock_client.invoke) pays for itself
#: from the second comparison onward.
SYSTEM_PROMPT = """\
You compare two answers that one organisation previously gave to the same \
security-questionnaire question.

Answer exactly one question: do the two answers make claims that cannot both be \
true of the same organisation at the same time?

Reply with exactly one word, and nothing else:

CONTRADICT - the answers assert incompatible facts about the control
AGREE      - the answers assert the same facts, however differently worded
UNRELATED  - the answers address different sub-topics and are not comparable
UNCERTAIN  - you cannot tell from the text alone

How to judge:
- Compare the factual claims only. Tone, length, formatting and completeness \
are not differences.
- One answer giving more detail than the other is not a contradiction.
- Different values for the same control IS a contradiction - for example key \
rotation "annually" versus "every 90 days", or backups "daily" versus "weekly".
- A control that one answer says exists and the other says does not exist IS a \
contradiction.
- Ignore dates, customer names, and who wrote the answers. You are not judging \
which answer is correct, which is newer, how serious the difference is, or what \
should be done about it.
- If you find yourself weighing it up, answer UNCERTAIN. UNCERTAIN is safe \
because it sends the pair to a human. A wrong AGREE is not, because it sends \
nothing to anyone."""

_USER_TEMPLATE = """\
QUESTION:
{question}

ANSWER A:
{left}

ANSWER B:
{right}

One word:"""

_RELATION_BY_WORD = {
    "CONTRADICT": ClaimRelation.CONTRADICT,
    "AGREE": ClaimRelation.AGREE,
    "UNRELATED": ClaimRelation.UNRELATED,
    "UNCERTAIN": ClaimRelation.UNCERTAIN,
}


def parse_relation(text: str | None) -> ClaimRelation:
    """Read one of four words out of the model's reply.

    Anything else is UNCERTAIN. That includes a reply naming two different
    relations ("AGREE, though it could be UNRELATED") — the model has not
    actually made the single call it was asked for, and guessing which half it
    meant would be inventing a judgement it did not make.
    """
    words = {w for w in re.findall(r"[A-Za-z]+", (text or "").upper()) if w in _RELATION_BY_WORD}
    if len(words) != 1:
        return ClaimRelation.UNCERTAIN
    return _RELATION_BY_WORD[words.pop()]


class BedrockComparator:
    """Callable matching `detector.Comparator`.

        detector = DivergenceDetector(comparator=BedrockComparator())

    Stateless apart from its cache, so a Lambda execution context can hold one
    instance across invocations.
    """

    def __init__(
        self,
        *,
        model_id: str | None = None,
        max_tokens: int = 8,
        temperature: float = 0.0,
        cache_size: int = 512,
    ) -> None:
        self._model_id = model_id
        self._max_tokens = max_tokens
        self._temperature = temperature
        self.calls = 0  # model calls actually issued
        self.errors = 0  # calls that failed and degraded to UNCERTAIN
        # Bounded deliberately: entries hold the prompt text, so an unbounded
        # cache is a slow memory leak in a warm Lambda execution context.
        self._cached = functools.lru_cache(maxsize=cache_size)(self._compare)

    # -- public ------------------------------------------------------------

    def __call__(self, question_text: str, left_answer: str, right_answer: str) -> ClaimRelation:
        left = (left_answer or "").strip()[:MAX_ANSWER_CHARS]
        right = (right_answer or "").strip()[:MAX_ANSWER_CHARS]
        question = (question_text or "").strip()[:MAX_ANSWER_CHARS]

        if not left or not right:
            # Nothing to compare. Not agreement — absence of an answer is
            # exactly the kind of gap a reviewer should look at.
            return ClaimRelation.UNCERTAIN
        if left == right:
            # Identical text needs no model call.
            return ClaimRelation.AGREE

        # Sort the pair so (a, b) and (b, a) are one cache entry and one call.
        first, second = sorted((left, right))
        return self._cached(question, first, second)

    @property
    def cache_info(self) -> Any:
        return self._cached.cache_info()

    # -- internals ---------------------------------------------------------

    def _compare(self, question: str, left: str, right: str) -> ClaimRelation:
        # Imported here rather than at module scope so the divergence package
        # stays importable without boto3, matching ingestion/connectors.py.
        from shared.bedrock_client import HAIKU, invoke  # noqa: PLC0415

        self.calls += 1
        try:
            response = invoke(
                model_id=self._model_id or HAIKU,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=_USER_TEMPLATE.format(question=question, left=left, right=right),
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                cache_system=True,
            )
        except Exception as exc:  # noqa: BLE001
            # The detector also catches this, but degrading here keeps the
            # error count honest and puts the model id in the log line.
            self.errors += 1
            logger.warning(
                "divergence.comparator_error",
                model_id=self._model_id or "haiku",
                error=str(exc),
            )
            return ClaimRelation.UNCERTAIN

        relation = parse_relation(response.get("text"))
        if relation is ClaimRelation.UNCERTAIN and response.get("text"):
            logger.info(
                "divergence.comparator_unparsed",
                response_hash=response.get("response_hash"),
                raw=str(response.get("text"))[:80],
            )
        return relation
