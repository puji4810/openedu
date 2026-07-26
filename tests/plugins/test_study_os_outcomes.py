"""Intervention outcomes: did an accepted recommendation actually help?"""

from __future__ import annotations

from datetime import datetime

import pytest

from plugins.study_os.outcomes import (
    MIN_OUTCOME_SAMPLE,
    OUTCOME_SCHEMA_VERSION,
    build_intervention_outcomes,
)

AS_OF = datetime.fromisoformat("2026-08-10T09:00:00+08:00")
DECIDED = "2026-07-26T09:30:00+08:00"


def _proposal(
    *,
    status: str = "accepted",
    kind: str = "independence_probe",
    baseline: str = "supported",
    objective: str = "alpha-solving",
    dimension: str = "execution",
    proposal_id: str = "plan-one",
    decided_at: str | None = DECIDED,
    items: list[dict] | None = None,
) -> dict:
    proposal = {
        "proposal_id": proposal_id,
        "status": status,
        "items": items
        or [
            {
                "intervention_id": f"iv-{objective}-{dimension}",
                "objective_id": objective,
                "evidence_dimension": dimension,
                "kind": kind,
                "reason_factors": {"verification_status": baseline},
            }
        ],
    }
    if status != "proposed" and decided_at:
        proposal["decision"] = {"outcome": status, "decided_at": decided_at}
    return proposal


def _attempt(
    when: str,
    *,
    objective: str = "alpha-solving",
    dimension: str = "execution",
    independent: bool = True,
    attempt_id: str | None = None,
) -> dict:
    return {
        "attempt_id": attempt_id or f"a-{when}",
        "occurred_at": when,
        "objective_ids": [objective],
        "transfer_level": dimension,
        "result": "correct" if independent else "incorrect",
        "score": 1.0 if independent else 0.2,
        "evaluator": {"kind": "human" if independent else "agent"},
        "assistance": {"level": "independent" if independent else "guided"},
        "concepts": [],
        "diagnoses": [],
    }


def _build(proposals, attempts, as_of=AS_OF):
    from plugins.study_os.learning import _diagnosis

    return build_intervention_outcomes(
        proposals=proposals,
        attempts=attempts,
        diagnosis_builder=_diagnosis,
        as_of=as_of,
    )


# ── which proposals are measured ──────────────────────────────────────────


def test_only_accepted_proposals_are_measured():
    result = _build(
        [
            _proposal(status="proposed", proposal_id="plan-a"),
            _proposal(status="rejected", proposal_id="plan-b"),
            _proposal(status="accepted", proposal_id="plan-c"),
        ],
        [],
    )

    assert [item["proposal_id"] for item in result["outcomes"]] == ["plan-c"]
    assert result["schema_version"] == OUTCOME_SCHEMA_VERSION


def test_an_accepted_proposal_without_a_decision_time_is_skipped():
    result = _build([_proposal(decided_at=None)], [])

    assert result["outcomes"] == []


# ── classification ────────────────────────────────────────────────────────


def test_no_evidence_since_the_decision_is_not_attempted_rather_than_failed():
    result = _build([_proposal()], [_attempt("2026-07-01T10:00:00+08:00")])

    outcome = result["outcomes"][0]
    assert outcome["outcome"] == "not_attempted"
    assert outcome["evidence_attempt_ids_since"] == []


def test_evidence_that_lifts_the_status_counts_as_improved():
    attempts = [
        _attempt("2026-07-28T20:00:00+08:00", attempt_id="a1"),
        _attempt("2026-07-29T20:00:00+08:00", attempt_id="a2"),
    ]

    outcome = _build([_proposal(baseline="supported")], attempts)["outcomes"][0]

    assert outcome["outcome"] == "improved"
    assert outcome["verification_status_now"] == "independent"
    assert outcome["evidence_attempt_ids_since"] == ["a1", "a2"]


def test_evidence_that_does_not_move_the_status_counts_as_unchanged():
    attempts = [_attempt("2026-07-28T20:00:00+08:00", independent=False)]

    outcome = _build([_proposal(baseline="developing")], attempts)["outcomes"][0]

    assert outcome["outcome"] == "unchanged"


def test_a_drop_in_status_counts_as_regressed():
    proposal = _proposal(baseline="independent")
    attempts = [_attempt("2026-07-28T20:00:00+08:00", independent=False)]

    outcome = _build([proposal], attempts)["outcomes"][0]

    assert outcome["outcome"] == "regressed"
    assert outcome["verification_status_now"] == "developing"


def test_evidence_on_another_objective_or_dimension_is_not_credited():
    attempts = [
        _attempt("2026-07-28T20:00:00+08:00", objective="beta-solving"),
        _attempt("2026-07-28T21:00:00+08:00", dimension="explanation"),
    ]

    outcome = _build([_proposal()], attempts)["outcomes"][0]

    assert outcome["outcome"] == "not_attempted"


def test_evidence_at_the_decision_instant_is_not_counted_as_since():
    outcome = _build([_proposal()], [_attempt(DECIDED)])["outcomes"][0]

    assert outcome["outcome"] == "not_attempted"


def test_days_since_decision_counts_whole_elapsed_days():
    outcome = _build([_proposal()], [])["outcomes"][0]

    # 07-26 09:30 to 08-10 09:00 is fifteen calendar days but only fourteen
    # elapsed ones, and elapsed is what "has this had time to work" needs.
    assert outcome["days_since_decision"] == 14


# ── aggregation honesty ───────────────────────────────────────────────────


def test_a_thin_sample_withholds_an_improvement_rate():
    result = _build([_proposal()], [_attempt("2026-07-28T20:00:00+08:00")])

    row = result["by_kind"][0]
    assert row["verdict"] == "insufficient_evidence"
    assert row["improvement_rate"] is None
    assert row["needed_for_signal"] == MIN_OUTCOME_SAMPLE - 1


def test_a_sufficient_sample_reports_a_rate():
    proposals = []
    attempts = []
    for index in range(MIN_OUTCOME_SAMPLE):
        objective = f"obj-{index}-solving"
        proposals.append(
            _proposal(objective=objective, proposal_id=f"plan-{index}")
        )
        # Every one of them acted on; the first three reach independence.
        independent = index < 3
        attempts.append(
            _attempt(
                "2026-07-28T20:00:00+08:00",
                objective=objective,
                independent=independent,
                attempt_id=f"a{index}-1",
            )
        )
        attempts.append(
            _attempt(
                "2026-07-29T20:00:00+08:00",
                objective=objective,
                independent=independent,
                attempt_id=f"a{index}-2",
            )
        )

    row = _build(proposals, attempts)["by_kind"][0]

    assert row["verdict"] == "measured"
    assert row["acted_on"] == MIN_OUTCOME_SAMPLE
    assert row["improvement_rate"] == round(3 / MIN_OUTCOME_SAMPLE, 3)


def test_non_adherence_is_excluded_from_the_improvement_rate():
    """Advice nobody followed must not be scored as advice that failed."""

    proposals = []
    attempts = []
    for index in range(MIN_OUTCOME_SAMPLE):
        objective = f"obj-{index}-solving"
        proposals.append(_proposal(objective=objective, proposal_id=f"plan-{index}"))
        attempts.append(
            _attempt("2026-07-28T20:00:00+08:00", objective=objective, attempt_id=f"a{index}-1")
        )
        attempts.append(
            _attempt("2026-07-29T20:00:00+08:00", objective=objective, attempt_id=f"a{index}-2")
        )
    # Five more decisions the learner never acted on.
    for index in range(MIN_OUTCOME_SAMPLE, MIN_OUTCOME_SAMPLE * 2):
        proposals.append(
            _proposal(objective=f"obj-{index}-solving", proposal_id=f"plan-{index}")
        )

    row = _build(proposals, attempts)["by_kind"][0]

    assert row["decided"] == MIN_OUTCOME_SAMPLE * 2
    assert row["acted_on"] == MIN_OUTCOME_SAMPLE
    assert row["improvement_rate"] == 1.0, "acted-on interventions all improved"
    assert row["adherence_rate"] == 0.5


def test_kinds_are_aggregated_separately():
    result = _build(
        [
            _proposal(kind="independence_probe", objective="a-solving", proposal_id="plan-a"),
            _proposal(kind="guided_repair", objective="b-solving", proposal_id="plan-b"),
        ],
        [],
    )

    assert [row["kind"] for row in result["by_kind"]] == [
        "guided_repair",
        "independence_probe",
    ]


def test_totals_count_every_decided_intervention():
    result = _build(
        [_proposal(proposal_id=f"plan-{i}", objective=f"o{i}-solving") for i in range(3)],
        [],
    )

    assert result["totals"]["decided"] == 3
    assert result["totals"]["not_attempted"] == 3


@pytest.mark.parametrize("bad", ["", None, "not-a-time"])
def test_an_unparsable_decision_time_is_skipped_rather_than_raising(bad):
    assert _build([_proposal(decided_at=bad)], [])["outcomes"] == []


def test_outcomes_are_deterministic():
    proposals = [_proposal(proposal_id=f"plan-{i}", objective=f"o{i}-solving") for i in range(3)]
    attempts = [_attempt("2026-07-28T20:00:00+08:00", objective="o1-solving")]

    assert _build(proposals, attempts) == _build(proposals, attempts)


# ── end to end through the tool ───────────────────────────────────────────


def test_evaluate_interventions_requires_project_scope(tmp_path):
    import json

    from plugins.study_os.learning import handle_study_coach
    from plugins.study_os.tools import handle_study_project

    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    handle_study_project(
        {"vault_path": str(vault), "action": "init", "project_id": "eval-project"}
    )

    result = json.loads(
        handle_study_coach(
            {
                "vault_path": str(vault),
                "action": "evaluate_interventions",
                "project_id": "eval-project",
                "scope": "week",
                "data": {},
            }
        )
    )

    assert result["error"]["code"] == "INVALID_SCOPE"


def test_evaluate_interventions_reports_an_empty_loop_without_failing(tmp_path):
    import json

    from plugins.study_os.learning import handle_study_coach
    from plugins.study_os.tools import handle_study_project

    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    handle_study_project(
        {"vault_path": str(vault), "action": "init", "project_id": "eval-project"}
    )

    result = json.loads(
        handle_study_coach(
            {
                "vault_path": str(vault),
                "action": "evaluate_interventions",
                "project_id": "eval-project",
                "data": {"as_of": "2026-08-10T09:00:00+08:00"},
            }
        )
    )

    assert result["ok"] is True
    assert result["data"]["intervention_outcomes"]["outcomes"] == []
    assert result["data"]["intervention_outcomes"]["totals"]["decided"] == 0
