"""Parser tests using the generated synthetic RFP as the fixture."""
from __future__ import annotations

from pathlib import Path

from excel_parser.parser import parse_workbook

FIXTURE = Path(__file__).resolve().parents[2] / "data" / "incoming" / "sample_rfp_acmesec.xlsx"


def test_parses_all_synthetic_questions() -> None:
    """The synthetic RFP has 30 questions — all should be extracted."""
    job_id, questions = parse_workbook(FIXTURE)
    assert job_id
    assert len(questions) == 30


def test_section_assignment() -> None:
    _, questions = parse_workbook(FIXTURE)
    # First question is in section A
    assert questions[0].section is not None
    assert questions[0].section.startswith("A.")


def test_answer_cells_are_to_the_right() -> None:
    _, questions = parse_workbook(FIXTURE)
    for q in questions:
        # Answer coordinate column letter should be one column to the right
        # of the question column. In our fixture, questions live in B, answers in C.
        assert q.answer_cell.coordinate.startswith("C")
        assert q.confidence_cell.coordinate.startswith("D")


def test_question_ids_are_unique_and_ordered() -> None:
    _, questions = parse_workbook(FIXTURE)
    ids = [q.question_id for q in questions]
    assert len(ids) == len(set(ids))
    assert ids == sorted(ids)  # q-0001, q-0002, ...


def test_instructions_are_not_treated_as_questions() -> None:
    """The fixture has a 'Please respond to each question in the Answer
    column...' instruction. It must not appear as a question."""
    _, questions = parse_workbook(FIXTURE)
    texts = [q.text.lower() for q in questions]
    assert not any("please respond to each question" in t for t in texts)


def test_insider_threat_question_is_extracted() -> None:
    """Regression: 'How do you detect and respond to insider threats?'
    was previously misclassified as an instruction because it contained
    the substring 'respond to'."""
    _, questions = parse_workbook(FIXTURE)
    assert any("insider threats" in q.text.lower() for q in questions)
