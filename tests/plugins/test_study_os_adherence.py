"""Plan adherence: did the day StudyOS planned actually happen?"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from plugins.study_os.adherence import (
    ADHERENCE_SCHEMA_VERSION,
    MIN_ADHERENCE_SAMPLE,
    build_plan_adherence,
)

TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime.fromisoformat("2026-07-20T23:00:00+08:00")


def _event(
    event_id: str,
    *,
    start: str,
    minutes: int = 30,
    objective: str = "trace-request",
    dimension: str = "execution",
    proposal_id: str | None = "plan-abc",
    kind: str = "evidence_probe",
) -> dict:
    begins = datetime.fromisoformat(start)
    event = {
        "id": event_id,
        "title": f"{kind}: trace a request",
        "subject_id": "runtime",
        "type": kind,
        "start": begins.isoformat(timespec="seconds"),
        "end": (begins + timedelta(minutes=minutes)).isoformat(timespec="seconds"),
        "duration_minutes": minutes,
        "goals": ["Produce evaluator-provenanced evidence."],
        "status": "planned",
        "source_intervention_id": f"iv-{event_id}",
        "source_objective_id": objective,
        "evidence_dimension": dimension,
    }
    if proposal_id:
        event["source_plan_proposal_id"] = proposal_id
    return event


def _schedule(events: list[dict], schedule_id: str = "runtime-plan") -> dict:
    return {"schedule_id": schedule_id, "title": "Runtime", "events": events}


def _attempt(
    attempt_id: str,
    *,
    occurred_at: str,
    minutes: int | None = 30,
    objective: str = "trace-request",
    dimension: str = "execution",
) -> dict:
    return {
        "attempt_id": attempt_id,
        "occurred_at": occurred_at,
        "objective_ids": [objective],
        "transfer_level": dimension,
        "duration_seconds": minutes * 60 if minutes is not None else None,
        "result": "correct",
        "score": 1.0,
    }


def _build(schedules, attempts, *, start="2026-07-18", end="2026-07-20", as_of=AS_OF):
    return build_plan_adherence(
        schedules=schedules,
        attempts=attempts,
        tzinfo=TZ,
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        as_of=as_of,
    )


def test_matching_evidence_on_the_planned_day_reads_as_followed():
    result = _build(
        [_schedule([_event("dp-1", start="2026-07-20T19:00:00+08:00", minutes=30)])],
        [_attempt("att-1", occurred_at="2026-07-20T19:05:00+08:00", minutes=32)],
    )

    assert result["schema_version"] == ADHERENCE_SCHEMA_VERSION
    day = result["days"][0]
    event = day["events"][0]
    assert event["status"] == "on_plan"
    assert event["observed_minutes"] == 32
    assert event["attempt_ids"] == ["att-1"]
    assert day["events_occurred"] == 1
    assert day["unplanned_attempt_ids"] == []


def test_evidence_at_another_hour_still_counts_and_reports_the_delay():
    result = _build(
        [_schedule([_event("dp-1", start="2026-07-20T19:00:00+08:00", minutes=30)])],
        [_attempt("att-1", occurred_at="2026-07-20T22:00:00+08:00", minutes=30)],
    )

    event = result["days"][0]["events"][0]
    assert event["status"] == "on_plan"
    assert event["start_delay_minutes"] == 180


def test_no_matching_evidence_reads_as_not_started():
    result = _build(
        [_schedule([_event("dp-1", start="2026-07-20T19:00:00+08:00")])],
        [],
    )

    event = result["days"][0]["events"][0]
    assert event["status"] == "not_started"
    assert event["observed_minutes"] is None
    assert event["attempt_ids"] == []
    assert result["days"][0]["events_measured"] == 1
    assert result["days"][0]["events_occurred"] == 0


def test_shorter_and_longer_sessions_are_named_rather_than_averaged_away():
    short = _build(
        [_schedule([_event("dp-1", start="2026-07-20T19:00:00+08:00", minutes=60)])],
        [_attempt("att-1", occurred_at="2026-07-20T19:00:00+08:00", minutes=20)],
    )
    long = _build(
        [_schedule([_event("dp-1", start="2026-07-20T19:00:00+08:00", minutes=60)])],
        [_attempt("att-1", occurred_at="2026-07-20T19:00:00+08:00", minutes=120)],
    )

    assert short["days"][0]["events"][0]["status"] == "under_run"
    assert long["days"][0]["events"][0]["status"] == "over_run"
    # Both happened, so both count as occurred: the finding is about effort,
    # not about compliance.
    assert short["days"][0]["events_occurred"] == 1
    assert long["days"][0]["events_occurred"] == 1


def test_untimed_evidence_is_occurred_rather_than_zero_minutes():
    result = _build(
        [_schedule([_event("dp-1", start="2026-07-20T19:00:00+08:00", minutes=60)])],
        [_attempt("att-1", occurred_at="2026-07-20T19:10:00+08:00", minutes=None)],
    )

    event = result["days"][0]["events"][0]
    assert event["status"] == "occurred"
    assert event["observed_minutes"] is None


def test_an_event_that_has_not_finished_is_pending_not_missed():
    result = _build(
        [_schedule([_event("dp-1", start="2026-07-20T23:30:00+08:00", minutes=30)])],
        [],
        as_of=datetime.fromisoformat("2026-07-20T21:00:00+08:00"),
    )

    day = result["days"][0]
    assert day["events"][0]["status"] == "pending"
    assert day["events_measured"] == 0
    assert day["events_pending"] == 1
    assert result["totals"]["days_pending"] == 1


def test_events_the_learner_authored_are_reported_as_unmeasured_not_missed():
    result = _build(
        [
            _schedule(
                [_event("evt-manual", start="2026-07-20T19:00:00+08:00", proposal_id=None)]
            )
        ],
        [],
    )

    assert result["unmeasured_events"] == 1
    assert result["days"] == []


def test_an_event_without_provenance_is_unmeasurable_rather_than_missed():
    result = _build(
        [
            _schedule(
                [_event("dp-1", start="2026-07-20T19:00:00+08:00", objective="")]
            )
        ],
        [],
    )

    day = result["days"][0]
    assert day["events"][0]["status"] == "unmeasurable"
    assert day["events_measured"] == 0


def test_studying_something_else_is_reported_separately_from_studying_nothing():
    result = _build(
        [_schedule([_event("dp-1", start="2026-07-20T19:00:00+08:00")])],
        [
            _attempt(
                "att-other",
                occurred_at="2026-07-20T19:30:00+08:00",
                objective="another-objective",
            )
        ],
    )

    day = result["days"][0]
    assert day["events"][0]["status"] == "not_started"
    assert day["unplanned_attempt_ids"] == ["att-other"]


def test_one_attempt_cannot_complete_two_planned_events():
    result = _build(
        [
            _schedule(
                [
                    _event("dp-1", start="2026-07-20T19:00:00+08:00"),
                    _event("dp-2", start="2026-07-20T20:00:00+08:00"),
                ]
            )
        ],
        [_attempt("att-1", occurred_at="2026-07-20T19:05:00+08:00")],
    )

    statuses = [event["status"] for event in result["days"][0]["events"]]
    assert statuses == ["on_plan", "not_started"]


def test_naive_timestamps_never_move_evidence_across_a_date_boundary():
    result = _build(
        [_schedule([_event("dp-1", start="2026-07-20T19:00:00+08:00")])],
        [{"attempt_id": "att-naive", "occurred_at": "2026-07-20T19:05:00", "objective_ids": ["trace-request"], "transfer_level": "execution"}],
    )

    assert result["days"][0]["events"][0]["status"] == "not_started"
    assert result["days"][0]["unplanned_attempt_ids"] == []


def test_a_thin_sample_withholds_its_verdict():
    result = _build(
        [_schedule([_event("dp-1", start="2026-07-20T19:00:00+08:00")])],
        [_attempt("att-1", occurred_at="2026-07-20T19:00:00+08:00")],
    )

    totals = result["totals"]
    assert totals["verdict"] == "insufficient_evidence"
    assert totals["completion_rate"] is None
    assert totals["needed_for_signal"] == MIN_ADHERENCE_SAMPLE - 1


def test_enough_planned_days_produce_a_completion_rate():
    events = [
        _event("dp-1", start="2026-07-18T19:00:00+08:00"),
        _event("dp-2", start="2026-07-19T19:00:00+08:00"),
        _event("dp-3", start="2026-07-20T19:00:00+08:00"),
        _event("dp-4", start="2026-07-20T20:00:00+08:00"),
    ]
    result = _build(
        [_schedule(events)],
        [
            _attempt("att-1", occurred_at="2026-07-18T19:00:00+08:00"),
            _attempt("att-2", occurred_at="2026-07-20T19:00:00+08:00"),
        ],
    )

    totals = result["totals"]
    assert totals["verdict"] == "measured"
    assert totals["days_measured"] == 3
    assert totals["events_measured"] == 4
    assert totals["events_occurred"] == 2
    assert totals["completion_rate"] == 0.5


def test_days_the_learner_was_never_asked_to_study_do_not_count_against_them():
    events = [
        _event("dp-1", start="2026-07-18T19:00:00+08:00"),
        _event("dp-2", start="2026-07-19T19:00:00+08:00"),
        _event("dp-3", start="2026-07-20T19:00:00+08:00"),
    ]
    result = _build(
        [_schedule(events)],
        [
            _attempt("att-1", occurred_at="2026-07-18T19:00:00+08:00"),
            _attempt("att-2", occurred_at="2026-07-19T19:00:00+08:00"),
            _attempt("att-3", occurred_at="2026-07-20T19:00:00+08:00"),
        ],
        start="2026-07-06",
    )

    # Two weeks of range, three planned days, all followed.
    assert result["totals"]["days_measured"] == 3
    assert result["totals"]["completion_rate"] == 1.0
