"""Day-plan projection: window derivation, packing, routing, and budgets."""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

import pytest

from plugins.study_os.day_plan import (
    DAY_PLAN_SCHEMA_VERSION,
    DEFAULT_WINDOW,
    MIN_WINDOW_SAMPLE,
    active_phase,
    build_day_plan,
    route_to_schedule,
    study_window,
)

TZ = ZoneInfo("Asia/Shanghai")
TARGET = date(2026, 7, 26)


def _attempt(hour: int, day: int = 20) -> dict:
    return {"occurred_at": f"2026-07-{day:02d}T{hour:02d}:30:00+08:00"}


def _item(objective: str, *, kind: str = "evidence_probe", minutes: int = 30, index: int = 0) -> dict:
    return {
        "intervention_id": f"iv-{objective}-{index}",
        "objective_id": objective,
        "capability": f"Capability for {objective}",
        "kind": kind,
        "evidence_dimension": "execution",
        "reasons": ["No evaluator-provenanced execution evidence has been recorded."],
        "recommended_activity": {
            "duration_minutes": minutes,
            "success_criteria": ["Solve independently."],
        },
    }


def _schedule(schedule_id: str, *, phase_id: str = "p1", effort: int | None = None) -> dict:
    phase = {"id": phase_id, "title": "Phase", "start": "2026-07-22", "end": "2026-07-27", "goal": "g"}
    if effort is not None:
        phase["effort_minutes"] = effort
    return {"schedule_id": schedule_id, "title": schedule_id, "phases": [phase], "events": []}


# ── study_window ──────────────────────────────────────────────────────────


def test_study_window_falls_back_to_default_below_the_sample_floor():
    window = study_window([_attempt(21)] * (MIN_WINDOW_SAMPLE - 1), tzinfo=TZ)

    assert window["source"] == "default"
    assert (window["start_hour"], window["end_hour"]) == DEFAULT_WINDOW
    assert window["coverage"] is None


def test_study_window_derives_a_night_block_from_evidence():
    attempts = [_attempt(hour) for hour in (20, 21, 22, 23) for _ in range(10)]

    window = study_window(attempts, tzinfo=TZ)

    # Three of the four hours already carry 75% of the activity, and the
    # shortest qualifying span wins, so the window stops at 22.
    assert window["source"] == "evidence"
    assert (window["start_hour"], window["end_hour"]) == (20, 22)
    assert window["coverage"] == 0.75


def test_study_window_derives_a_morning_block_from_evidence():
    attempts = [_attempt(hour) for hour in (8, 9, 10, 11) for _ in range(10)]

    window = study_window(attempts, tzinfo=TZ)

    assert (window["start_hour"], window["end_hour"]) == (8, 10)


def test_study_window_prefers_the_shortest_qualifying_span():
    # 40 attempts at 21:00 alone already clear the 70% bar against 10 strays.
    attempts = [_attempt(21) for _ in range(40)] + [_attempt(hour) for hour in range(9, 19)]

    window = study_window(attempts, tzinfo=TZ)

    assert window["start_hour"] == 21
    assert window["end_hour"] == 21


def test_study_window_ignores_naive_and_unparsable_timestamps():
    attempts = [_attempt(21) for _ in range(MIN_WINDOW_SAMPLE)]
    attempts += [{"occurred_at": "2026-07-20T10:00:00"}, {"occurred_at": "nonsense"}, {}]

    window = study_window(attempts, tzinfo=TZ)

    assert window["sample_size"] == MIN_WINDOW_SAMPLE
    assert window["start_hour"] == 21


def test_study_window_is_computed_in_the_project_timezone():
    utc_evening = [{"occurred_at": f"2026-07-20T13:00:00+00:00"} for _ in range(MIN_WINDOW_SAMPLE)]

    window = study_window(utc_evening, tzinfo=TZ)

    assert window["start_hour"] == 21  # 13:00Z is 21:00 in Asia/Shanghai


# ── active_phase ──────────────────────────────────────────────────────────


def test_active_phase_matches_inclusive_bounds_and_skips_malformed():
    schedule = {
        "phases": [
            {"id": "bad", "start": "not-a-date", "end": "2026-07-27"},
            {"id": "hit", "start": "2026-07-26", "end": "2026-07-26"},
        ]
    }

    assert active_phase(schedule, TARGET)["id"] == "hit"
    assert active_phase(schedule, date(2026, 7, 28)) is None


# ── routing ───────────────────────────────────────────────────────────────


def test_route_reports_a_sole_schedule_honestly():
    targets = [{"schedule_id": "kaoyan-408-2027-summer-coverage-v1"}]

    assert route_to_schedule(_item("timed-integrated-408-performance"), targets) == (
        0,
        "sole-covering-schedule",
    )


def test_route_sends_an_objective_to_its_matching_schedule():
    targets = [
        {"schedule_id": "kaoyan-math-2026-linear-algebra-32"},
        {"schedule_id": "kaoyan-math-2026-probability-32"},
    ]

    index, method = route_to_schedule(_item("probability-independent-solving"), targets)

    assert (index, method) == (1, "objective-token-match")


def test_route_falls_back_when_no_schedule_shares_a_subject_token():
    targets = [
        {"schedule_id": "kaoyan-math-2026-linear-algebra-32"},
        {"schedule_id": "kaoyan-math-2026-probability-32"},
    ]

    index, method = route_to_schedule(_item("calculus-independent-solving"), targets)

    assert (index, method) == (0, "fallback-first-covering-schedule")


def test_route_ignores_numeric_tokens_shared_by_every_schedule():
    targets = [
        {"schedule_id": "kaoyan-math-2026-linear-algebra-32"},
        {"schedule_id": "kaoyan-math-2026-probability-32"},
    ]

    # "2026" is in both ids; matching on it would be a coincidence, not a subject.
    index, method = route_to_schedule(_item("readiness-2026"), targets)

    assert method == "fallback-first-covering-schedule"


# ── build_day_plan ────────────────────────────────────────────────────────


def _build(items, schedules, attempts=None, project=None):
    return build_day_plan(
        queue_items=items,
        schedules=schedules,
        attempts=attempts
        if attempts is not None
        else [_attempt(hour) for hour in range(20, 24) for _ in range(6)],
        project=project or {"project_id": "p", "tracks": [{"id": "math"}]},
        target=TARGET,
        tzinfo=TZ,
    )


def test_day_plan_packs_events_back_to_back_with_a_break():
    plan = _build([_item("a", index=i) for i in range(3)], [_schedule("s-one")])

    events = plan["schedules"][0]["events"]
    assert [event["start"][11:16] for event in events] == ["20:00", "20:40", "21:20"]
    assert [event["end"][11:16] for event in events] == ["20:30", "21:10", "21:50"]
    assert plan["minutes_planned"] == 90
    assert plan["schema_version"] == DAY_PLAN_SCHEMA_VERSION


def test_day_plan_events_satisfy_the_schedule_contract_arithmetic():
    plan = _build([_item("a", index=i, minutes=45) for i in range(2)], [_schedule("s-one")])

    for event in plan["schedules"][0]["events"]:
        assert 1 <= event["duration_minutes"] <= 720
        assert event["end"] > event["start"]
        assert event["goals"] and all(goal.strip() for goal in event["goals"])
        assert event["status"] == "planned"
        assert event["subject_id"]


def test_day_plan_never_overlaps_events_across_parallel_schedules():
    items = [
        _item("linear-algebra-independent-solving", index=0),
        _item("probability-independent-solving", index=1),
    ]
    plan = _build(
        items,
        [_schedule("kaoyan-math-2026-linear-algebra-32"), _schedule("kaoyan-math-2026-probability-32")],
    )

    assert len(plan["schedules"]) == 2
    spans = sorted(
        (event["start"], event["end"])
        for entry in plan["schedules"]
        for event in entry["events"]
    )
    for earlier, later in zip(spans, spans[1:]):
        assert earlier[1] <= later[0], "one learner cannot be in two events at once"


def test_day_plan_splits_phase_effort_across_remaining_days():
    # 600 minutes across 2026-07-26..27 inclusive is 300 a day, so the second
    # 240-minute item cannot fit even though the window is long enough.
    plan = _build(
        [_item("a", index=0, minutes=240), _item("a", index=1, minutes=240)],
        [_schedule("s-one", effort=600)],
        attempts=[_attempt(hour) for hour in range(8, 22) for _ in range(3)],
    )

    assert plan["schedules"][0]["minutes_budget"] == 300
    assert plan["minutes_planned"] == 240
    assert len(plan["unplaced"]) == 1
    assert "daily budget" in plan["unplaced"][0]["reason"]


def test_day_plan_reports_items_that_run_past_the_window():
    plan = _build(
        [_item("a", index=0, minutes=180), _item("a", index=1, minutes=180)],
        [_schedule("s-one")],
        attempts=[_attempt(20) for _ in range(20)],  # 20:00-20:59 window
    )

    assert plan["minutes_planned"] == 0
    assert len(plan["unplaced"]) == 2
    assert all("study window" in entry["reason"] for entry in plan["unplaced"])


def test_day_plan_reports_every_item_when_no_phase_covers_the_date():
    schedule = _schedule("s-one")
    schedule["phases"][0].update({"start": "2026-08-01", "end": "2026-08-05"})

    plan = _build([_item("a", index=0)], [schedule])

    assert plan["schedules"] == []
    assert plan["unplaced"][0]["reason"] == "no Schedule phase covers the target date"


def test_day_plan_rejects_an_item_without_a_usable_duration():
    item = _item("a")
    item["recommended_activity"]["duration_minutes"] = 0

    plan = _build([item], [_schedule("s-one")])

    assert plan["schedules"] == []
    assert "duration_minutes" in plan["unplaced"][0]["reason"]


def test_day_plan_is_deterministic():
    items = [_item("a", index=i) for i in range(4)]
    schedules = [_schedule("s-one")]

    assert _build(items, schedules) == _build(items, schedules)


def test_day_plan_carries_provenance_back_to_the_intervention():
    plan = _build([_item("probability-independent-solving")], [_schedule("s-one")])

    event = plan["schedules"][0]["events"][0]
    assert event["source_intervention_id"] == "iv-probability-independent-solving-0"
    assert event["source_objective_id"] == "probability-independent-solving"
    assert event["routing"] == "sole-covering-schedule"


@pytest.mark.parametrize("empty", [[], None])
def test_day_plan_handles_absent_schedules(empty):
    plan = build_day_plan(
        queue_items=[_item("a")],
        schedules=list(empty or []),
        attempts=[],
        project={"project_id": "p"},
        target=TARGET,
        tzinfo=TZ,
    )

    assert plan["schedules"] == []
    assert len(plan["unplaced"]) == 1


# ── proposal identity ─────────────────────────────────────────────────────


def _orchestrator(objectives):
    from plugins.study_os.interventions import InterventionOrchestrator

    project = {
        "schema_version": "study_project.v2",
        "project_id": "identity-project",
        "title": "Identity",
        "timezone": "Asia/Shanghai",
        "objectives": [
            {
                "objective_id": objective,
                "capability": f"Capability {objective}",
                "success_criteria": ["Solve independently."],
                "evidence_targets": ["execution"],
            }
            for objective in objectives
        ],
    }
    return InterventionOrchestrator(
        project=project,
        diagnosis_builder=lambda attempts: {
            "evidence_dimensions": {},
            "diagnosis_clusters": [],
        },
    )


def _proposal_on(day: str, schedules):
    from datetime import datetime

    return _orchestrator(["alpha-solving"]).build(
        attempts=[],
        as_of=datetime.fromisoformat(f"{day}T09:00:00+08:00"),
        max_items=5,
        schedules=schedules,
    )["proposal"]


def test_proposal_identity_ignores_the_date_when_no_events_are_placed():
    """An empty day plan schedules nothing, so a re-run stays idempotent."""

    first = _proposal_on("2026-07-26", [])
    second = _proposal_on("2026-07-27", [])

    assert first["day_plan"]["schedules"] == []
    assert first["proposal_id"] == second["proposal_id"]


def test_proposal_identity_separates_days_once_events_are_placed():
    """Two days of concrete events are two proposals, not one saved twice."""

    schedules = [_schedule("alpha-track", phase_id="ph")]

    first = _proposal_on("2026-07-26", schedules)
    second = _proposal_on("2026-07-27", schedules)

    assert first["day_plan"]["schedules"][0]["events"]
    assert first["proposal_id"] != second["proposal_id"]
    assert first["day_plan"]["target_date"] != second["day_plan"]["target_date"]


def test_proposal_identity_is_reproducible_for_the_same_day():
    schedules = [_schedule("alpha-track", phase_id="ph")]

    assert _proposal_on("2026-07-26", schedules)["proposal_id"] == (
        _proposal_on("2026-07-26", schedules)["proposal_id"]
    )


# ── "now" clamping ────────────────────────────────────────────────────────


def _build_at(now_iso: str, items=None, schedules=None):
    from datetime import datetime

    return build_day_plan(
        queue_items=items or [_item("a", index=i) for i in range(3)],
        schedules=schedules or [_schedule("s-one")],
        attempts=[_attempt(hour) for hour in range(19, 24) for _ in range(6)],
        project={"project_id": "p", "tracks": [{"id": "math"}]},
        target=TARGET,
        tzinfo=TZ,
        now=datetime.fromisoformat(now_iso),
    )


def test_day_plan_starts_at_the_window_when_opened_early():
    plan = _build_at("2026-07-26T09:00:00+08:00")

    assert plan["schedules"][0]["events"][0]["start"][11:16] == "19:00"


def test_day_plan_never_proposes_a_session_in_the_past():
    plan = _build_at("2026-07-26T21:30:00+08:00")

    starts = [event["start"] for event in plan["schedules"][0]["events"]]
    assert starts[0][11:16] == "21:30"
    assert all(start >= "2026-07-26T21:30:00+08:00" for start in starts)


def test_day_plan_rounds_a_ragged_start_up_to_a_clean_slot():
    plan = _build_at("2026-07-26T21:31:00+08:00")

    assert plan["schedules"][0]["events"][0]["start"][11:16] == "21:35"


def test_day_plan_reports_that_the_window_has_already_closed():
    plan = _build_at("2026-07-26T23:50:00+08:00")

    assert plan["schedules"] == []
    assert len(plan["unplaced"]) == 3
    assert "23:50" in plan["unplaced"][0]["reason"]


def test_day_plan_ignores_now_from_a_different_day():
    plan = _build_at("2026-07-25T21:30:00+08:00")

    assert plan["schedules"][0]["events"][0]["start"][11:16] == "19:00"


# ── ensure_today ──────────────────────────────────────────────────────────


@pytest.fixture
def planned_vault(tmp_path):
    """A v2 project with one Schedule whose phase covers the target date."""
    import json as _json

    from plugins.study_os.tools import handle_study_project

    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    created = _json.loads(
        handle_study_project(
            {
                "vault_path": str(vault),
                "action": "init",
                "project_id": "plan-project",
                "schema_version": "study_project.v2",
                "tracks": [{"id": "math", "label": "Math"}],
                "objectives": [
                    {
                        "objective_id": "alpha-solving",
                        "capability": "Solve alpha problems independently.",
                        "success_criteria": ["No condition missed."],
                        "evidence_targets": ["execution"],
                    }
                ],
            }
        )
    )
    assert created["ok"], created
    schedules = vault / ".StudyOS" / "projects" / "plan-project" / "schedules"
    schedules.mkdir(parents=True, exist_ok=True)
    (schedules / "alpha-track.json").write_text(
        _json.dumps(
            {
                "schema_version": "study_schedule.v1",
                "schedule_id": "alpha-track",
                "project_id": "plan-project",
                "title": "Alpha",
                "timezone": "Asia/Shanghai",
                "range": {"start": "2026-07-01", "end": "2026-12-31"},
                "phases": [
                    {
                        "id": "ph",
                        "title": "Phase",
                        "start": "2026-07-22",
                        "end": "2026-07-27",
                        "goal": "Cover alpha.",
                        "subject_ids": ["math"],
                    }
                ],
                "events": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return vault


def _ensure(vault, when: str, **kwargs):
    import json as _json

    from plugins.study_os.learning import handle_study_activity

    return _json.loads(
        handle_study_activity(
            {
                "vault_path": str(vault),
                "resource": "plan_proposal",
                "action": "ensure_today",
                "project_id": "plan-project",
                "data": {"as_of": when},
            },
            **kwargs,
        )
    )


def test_ensure_today_creates_once_and_then_reuses(planned_vault):
    first = _ensure(planned_vault, "2026-07-26T09:00:00+08:00")
    second = _ensure(planned_vault, "2026-07-26T21:30:00+08:00")

    assert first["ok"] and first["data"]["created"] is True
    assert second["data"]["created"] is False
    assert second["data"]["proposal"]["proposal_id"] == first["data"]["proposal"]["proposal_id"]


def test_ensure_today_creates_a_separate_plan_for_the_next_day(planned_vault):
    first = _ensure(planned_vault, "2026-07-26T09:00:00+08:00")
    tomorrow = _ensure(planned_vault, "2026-07-27T09:00:00+08:00")

    assert tomorrow["data"]["created"] is True
    assert tomorrow["data"]["proposal"]["proposal_id"] != first["data"]["proposal"]["proposal_id"]


def test_ensure_today_does_not_re_propose_a_decided_day(planned_vault):
    import json as _json

    from plugins.study_os.learning import handle_study_activity

    created = _ensure(planned_vault, "2026-07-26T09:00:00+08:00")["data"]["proposal"]
    rejected = _json.loads(
        handle_study_activity(
            {
                "vault_path": str(planned_vault),
                "resource": "plan_proposal",
                "action": "reject",
                "project_id": "plan-project",
                "data": {"proposal_id": created["proposal_id"]},
            }
        )
    )
    assert rejected["ok"]

    again = _ensure(planned_vault, "2026-07-26T20:00:00+08:00")

    assert again["data"]["created"] is False
    assert "rejected" in again["data"]["reason"]


def test_ensure_today_is_available_to_cron_but_deciding_is_not(planned_vault):
    import json as _json

    from plugins.study_os.learning import handle_study_activity

    created = _ensure(planned_vault, "2026-07-26T07:00:00+08:00", session_id="cron_daily")
    assert created["data"]["created"] is True

    denied = _json.loads(
        handle_study_activity(
            {
                "vault_path": str(planned_vault),
                "resource": "plan_proposal",
                "action": "accept",
                "project_id": "plan-project",
                "data": {"proposal_id": created["data"]["proposal"]["proposal_id"]},
            },
            session_id="cron_daily",
        )
    )
    assert denied["error"]["code"] == "CRON_PROPOSAL_ONLY"


def test_study_day_plan_blueprint_is_registered_and_fills():
    from cron.blueprint_catalog import fill_blueprint, get_blueprint

    blueprint = get_blueprint("study-day-plan")
    assert blueprint is not None
    assert "study-os" in blueprint.skills

    filled = fill_blueprint(
        blueprint,
        {"vault_path": "/home/learner/Math", "time": "20:00", "deliver": "origin"},
    )
    assert filled["schedule"] == "0 20 * * *"
    assert "ensure_today" in filled["prompt"]
    assert "/home/learner/Math" in filled["prompt"]
    # A scheduled run proposes; it must never be told it may decide.
    assert "only the learner decides" in filled["prompt"]
