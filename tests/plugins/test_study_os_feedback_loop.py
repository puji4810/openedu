"""The recommender reading its own record: measurement reaching the next plan.

Every module in the loop is tested in isolation elsewhere.  These tests hold
the wiring: that a measured outcome actually moves a priority, that observed
durations actually shape the calendar, that a day the learner never finishes
actually tightens the next one -- and that with nothing measured yet, the queue
is exactly the one StudyOS derived before any of this existed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _loads(value: str) -> dict:
    return json.loads(value)


def _objective(objective_id: str) -> dict:
    return {
        "objective_id": objective_id,
        "capability": f"Demonstrate {objective_id} under an observable rubric.",
        "success_criteria": ["The result is correct and independently evaluated."],
        "evidence_targets": ["execution"],
        "source_anchors": [],
    }


def _project(objectives: list[dict] | None = None) -> dict:
    return {
        "schema_version": "study_project.v2",
        "project_id": "learning-runtime",
        "title": "Learning Runtime",
        "domain": "engineering",
        "timezone": "Asia/Shanghai",
        "phase": "implementation",
        "domain_pack": "engineering.v1",
        "workspace_type": "engineering-repo",
        "artifact_policy": "source-and-command",
        "deadline": "2026-12-31",
        "tracks": [{"id": "runtime", "label": "Runtime"}],
        "objectives": objectives or [_objective("trace-request")],
        "prompt_policy": {
            "base_max_chars": 2000,
            "intent_max_chars": 2500,
            "domain_max_chars": 2000,
            "project_summary_max_chars": 1200,
            "total_max_chars": 6000,
            "updates_apply": "next_session",
        },
        "created_at": "2026-07-01T09:00:00+08:00",
        "updated_at": "2026-07-01T09:00:00+08:00",
    }


def _attempt(
    attempt_id: str,
    *,
    objective_id: str = "trace-request",
    occurred_at: str,
    minutes: int | None = None,
    activity_kind: str = "independence_probe",
) -> dict:
    return {
        "attempt_id": attempt_id,
        "occurred_at": occurred_at,
        "objective_ids": [objective_id],
        "transfer_level": "execution",
        "activity_kind": activity_kind,
        "duration_seconds": minutes * 60 if minutes is not None else None,
        "score": 1.0,
        "result": "correct",
        "assistance": {"level": "guided", "hints_used": 0},
        "evaluator": {"kind": "program", "confidence": 0.9},
        "concepts": [],
        "diagnoses": [],
    }


def _schedule(*, effort_minutes: int | None = None) -> dict:
    phase: dict = {
        "id": "ph",
        "title": "Phase",
        "start": "2026-07-20",
        "end": "2026-07-20",
        "goal": "Cover the runtime.",
        "subject_ids": ["runtime"],
    }
    if effort_minutes is not None:
        phase["effort_minutes"] = effort_minutes
    return {
        "schema_version": "study_schedule.v1",
        "schedule_id": "runtime-plan",
        "project_id": "learning-runtime",
        "title": "Runtime",
        "timezone": "Asia/Shanghai",
        "range": {"start": "2026-07-01", "end": "2026-12-31"},
        "phases": [phase],
        "events": [],
    }


def _build(
    project: dict,
    attempts: list[dict],
    *,
    as_of: str = "2026-07-20T19:00:00+08:00",
    schedules: list[dict] | None = None,
    outcomes: dict | None = None,
    adherence: dict | None = None,
) -> dict:
    from plugins.study_os.interventions import InterventionOrchestrator, parse_as_of
    from plugins.study_os.learning import _diagnosis

    return InterventionOrchestrator(
        project=project,
        diagnosis_builder=_diagnosis,
    ).build(
        attempts=attempts,
        as_of=parse_as_of(as_of),
        schedules=schedules,
        outcomes=outcomes,
        adherence=adherence,
    )


def _measured_kind(kind: str, rate: float, acted_on: int = 8) -> dict:
    return {
        "by_kind": [
            {
                "kind": kind,
                "verdict": "measured",
                "acted_on": acted_on,
                "improvement_rate": rate,
            }
        ]
    }


def _measured_adherence(completion_rate: float) -> dict:
    return {
        "totals": {
            "verdict": "measured",
            "days_measured": 5,
            "completion_rate": completion_rate,
        }
    }


# ── the recommender's record moves the recommendation ──────────────────────


def test_a_kind_that_has_worked_outranks_the_same_gap_without_that_record():
    project = _project()
    attempts = [_attempt("att-1", occurred_at="2026-07-19T20:00:00+08:00")]

    plain = _build(project, attempts)["queue"]["items"][0]
    credited = _build(
        project,
        attempts,
        outcomes=_measured_kind(plain["kind"], 1.0),
    )["queue"]["items"][0]

    assert credited["priority_score"] == plain["priority_score"] + 8
    assert credited["reason_factors"]["outcome_adjustment"] == 8
    assert credited["reason_factors"]["outcome_improvement_rate"] == 1.0
    assert credited["reason_factors"]["outcome_sample_size"] == 8
    assert credited["reason_factors"]["outcome_source"] == "measured"
    assert any("100%" in reason for reason in credited["reasons"])


def test_a_kind_that_has_not_worked_is_demoted_by_the_same_measurement():
    project = _project()
    attempts = [_attempt("att-1", occurred_at="2026-07-19T20:00:00+08:00")]

    plain = _build(project, attempts)["queue"]["items"][0]
    demoted = _build(
        project,
        attempts,
        outcomes=_measured_kind(plain["kind"], 0.0),
    )["queue"]["items"][0]

    assert demoted["priority_score"] == plain["priority_score"] - 8
    assert demoted["priority_band"] == _band(demoted["priority_score"])


def _band(score: int) -> str:
    return "high" if score >= 80 else "medium" if score >= 55 else "low"


def test_nothing_measured_yet_derives_the_queue_it_always_derived():
    project = _project()
    attempts = [_attempt("att-1", occurred_at="2026-07-19T20:00:00+08:00")]

    item = _build(project, attempts, schedules=[_schedule()])["queue"]["items"][0]

    assert item["reason_factors"]["outcome_adjustment"] == 0
    assert item["reason_factors"]["outcome_source"] == "insufficient_evidence"
    assert item["recommended_activity"]["duration_source"] == "domain-pack-default"
    assert item["recommended_activity"]["duration_minutes"] == 45


# ── observed duration reaches the calendar ─────────────────────────────────


def test_how_long_the_learner_actually_takes_becomes_how_long_the_block_is():
    project = _project()
    attempts = [
        _attempt(
            f"att-{index}",
            occurred_at=f"2026-07-19T{18 + index:02d}:00:00+08:00",
            minutes=90,
        )
        for index in range(5)
    ]

    result = _build(project, attempts, schedules=[_schedule()])

    item = result["queue"]["items"][0]
    assert item["kind"] == "independence_probe"
    assert item["recommended_activity"]["duration_minutes"] == 90
    assert item["recommended_activity"]["duration_source"] == "observed-median"
    assert item["recommended_activity"]["duration_sample_size"] == 5

    event = result["day_plan"]["schedules"][0]["events"][0]
    assert event["duration_minutes"] == 90


# ── a day that is never finished tightens the next one ─────────────────────


def test_a_half_completed_history_shrinks_tomorrows_budget():
    project = _project([_objective("trace-request"), _objective("shard-writes")])
    schedules = [_schedule(effort_minutes=90)]

    nominal = _build(project, [], schedules=schedules)["day_plan"]
    tightened = _build(
        project,
        [],
        schedules=schedules,
        adherence=_measured_adherence(0.5),
    )["day_plan"]

    assert nominal["minutes_budget"] == 90
    assert len(nominal["schedules"][0]["events"]) == 2
    assert nominal["unplaced"] == []

    assert tightened["capacity"]["factor"] == 0.5
    assert tightened["capacity"]["source"] == "observed-adherence"
    assert tightened["minutes_budget"] == 45
    assert tightened["minutes_budget_nominal"] == 90
    assert len(tightened["schedules"][0]["events"]) == 1
    assert "45 minute daily budget" in tightened["unplaced"][0]["reason"]


def test_a_learner_who_finishes_everything_keeps_the_whole_budget():
    project = _project([_objective("trace-request"), _objective("shard-writes")])

    plan = _build(
        project,
        [],
        schedules=[_schedule(effort_minutes=90)],
        adherence=_measured_adherence(1.0),
    )["day_plan"]

    assert plan["minutes_budget"] == 90
    assert len(plan["schedules"][0]["events"]) == 2


# ── end to end, through the tools the model actually calls ────────────────


@pytest.fixture
def planned_vault(tmp_path: Path) -> Path:
    from plugins.study_os.tools import handle_study_project

    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    created = _loads(
        handle_study_project({"action": "init", "vault_path": str(vault), **_project()})
    )
    assert created["ok"], created
    schedules = vault / ".StudyOS" / "projects" / "learning-runtime" / "schedules"
    schedules.mkdir(parents=True, exist_ok=True)
    (schedules / "runtime-plan.json").write_text(
        json.dumps(_schedule(effort_minutes=180), ensure_ascii=False),
        encoding="utf-8",
    )
    return vault


def _activity(vault: Path, resource: str, action: str, data: dict) -> dict:
    from plugins.study_os.learning import handle_study_activity

    return _loads(
        handle_study_activity(
            {
                "vault_path": str(vault),
                "resource": resource,
                "action": action,
                "project_id": "learning-runtime",
                "data": data,
            }
        )
    )


def _coach(vault: Path, action: str, data: dict, scope: str = "project") -> dict:
    from plugins.study_os.learning import handle_study_coach

    return _loads(
        handle_study_coach(
            {
                "action": action,
                "scope": scope,
                "vault_path": str(vault),
                "project_id": "learning-runtime",
                "data": data,
            }
        )
    )


def _applied_day(vault: Path, when: str = "2026-07-20T09:00:00+08:00") -> dict:
    proposal = _activity(vault, "plan_proposal", "ensure_today", {"as_of": when})
    assert proposal["ok"], proposal
    proposal_id = proposal["data"]["proposal"]["proposal_id"]
    accepted = _activity(vault, "plan_proposal", "accept", {"proposal_id": proposal_id})
    assert accepted["ok"], accepted
    applied = _activity(vault, "plan_proposal", "apply", {"proposal_id": proposal_id})
    assert applied["ok"], applied
    return proposal["data"]["proposal"]


def test_evaluate_adherence_reports_an_applied_day_the_learner_skipped(planned_vault):
    _applied_day(planned_vault)

    result = _coach(
        planned_vault,
        "evaluate_adherence",
        {"as_of": "2026-07-20T23:59:00+08:00", "start_date": "2026-07-20", "end_date": "2026-07-20"},
    )

    assert result["ok"], result
    day = result["data"]["plan_adherence"]["days"][0]
    assert day["date"] == "2026-07-20"
    assert day["events"][0]["status"] == "not_started"
    assert day["events"][0]["source_plan_proposal_id"].startswith("plan-")
    # One day is not a habit, so the correction it produces is still none.
    assert result["data"]["capacity"]["factor"] == 1.0
    assert result["data"]["capacity"]["source"] == "uncalibrated"


def test_evidence_recorded_that_day_closes_the_planned_event(planned_vault):
    _applied_day(planned_vault)
    recorded = _activity(
        planned_vault,
        "attempt",
        "record",
        {
            "item_id": "trace-01",
            "response": "Followed the request through the gateway.",
            "result": "correct",
            "score": 1.0,
            "occurred_at": "2026-07-20T19:10:00+08:00",
            "duration_seconds": 2700,
            "objective_ids": ["trace-request"],
            "transfer_level": "execution",
            "activity_kind": "evidence_probe",
            "assistance": {"level": "independent", "hints_used": 0},
            "evaluator": {"kind": "program", "confidence": 0.9},
        },
    )
    assert recorded["ok"], recorded

    result = _coach(
        planned_vault,
        "evaluate_adherence",
        {"as_of": "2026-07-20T23:59:00+08:00", "start_date": "2026-07-20", "end_date": "2026-07-20"},
    )

    event = result["data"]["plan_adherence"]["days"][0]["events"][0]
    assert event["status"] in {"on_plan", "under_run", "over_run"}
    assert event["observed_minutes"] == 45
    assert event["attempt_ids"] == [recorded["data"]["attempt"]["attempt_id"]]


def test_a_derived_plan_carries_the_measurements_that_shaped_it(planned_vault):
    _applied_day(planned_vault)

    result = _coach(
        planned_vault,
        "propose_plan",
        {"as_of": "2026-07-21T09:00:00+08:00"},
    )

    assert result["ok"], result
    item = result["data"]["intervention_queue"]["items"][0]
    # Provenance rather than a value: with one applied day and no decided
    # history there is nothing to correct yet, and the plan says so.
    assert item["reason_factors"]["outcome_source"] == "insufficient_evidence"
    assert item["recommended_activity"]["duration_source"] == "domain-pack-default"


def test_evaluate_interventions_shows_the_correction_it_produces(planned_vault):
    _applied_day(planned_vault)

    result = _coach(
        planned_vault,
        "evaluate_interventions",
        {"as_of": "2026-07-27T09:00:00+08:00"},
    )

    assert result["ok"], result
    kinds = [row["kind"] for row in result["data"]["intervention_outcomes"]["by_kind"]]
    calibration = result["data"]["calibration"]
    assert [row["kind"] for row in calibration] == kinds
    # One decision cannot move a priority, and the report says so rather than
    # publishing a delta the sample cannot support.
    assert all(row["delta"] == 0 for row in calibration)
    assert all(row["source"] == "insufficient_evidence" for row in calibration)


def test_evaluate_adherence_refuses_an_inverted_range(planned_vault):
    result = _coach(
        planned_vault,
        "evaluate_adherence",
        {"start_date": "2026-07-20", "end_date": "2026-07-10"},
    )

    assert result["error"]["code"] == "VALIDATION_FAILED"


def test_evaluate_adherence_requires_project_scope(planned_vault):
    result = _coach(planned_vault, "evaluate_adherence", {}, scope="week")

    assert result["error"]["code"] == "INVALID_SCOPE"
