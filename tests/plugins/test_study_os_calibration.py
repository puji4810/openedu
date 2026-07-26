"""Calibration: bounded corrections the recommender makes to itself."""

from __future__ import annotations

from plugins.study_os.calibration import (
    MAX_OUTCOME_ADJUSTMENT,
    MIN_CAPACITY_FACTOR,
    MIN_DURATION_SAMPLE,
    calibrated_duration,
    capacity_factor,
    outcome_adjustment,
)


def _attempts(minutes: list[int | None], *, kind: str = "evidence_probe") -> list[dict]:
    return [
        {
            "attempt_id": f"att-{index}",
            "activity_kind": kind,
            "duration_seconds": value * 60 if value is not None else None,
        }
        for index, value in enumerate(minutes)
    ]


def _adherence(totals: dict) -> dict:
    return {"totals": totals}


def _measured(completion_rate: float, days: int = 5) -> dict:
    return _adherence(
        {
            "verdict": "measured",
            "days_measured": days,
            "completion_rate": completion_rate,
        }
    )


def test_a_thin_sample_leaves_the_domain_pack_duration_alone():
    result = calibrated_duration(
        attempts=_attempts([90] * (MIN_DURATION_SAMPLE - 1)),
        kind="evidence_probe",
        default_minutes=30,
    )

    assert result["minutes"] == 30
    assert result["source"] == "domain-pack-default"
    assert result["observed_median_minutes"] is None
    assert result["sample_size"] == MIN_DURATION_SAMPLE - 1


def test_enough_same_kind_evidence_replaces_the_stated_duration():
    result = calibrated_duration(
        attempts=_attempts([44, 46, 45, 47, 43]),
        kind="evidence_probe",
        default_minutes=30,
    )

    assert result["source"] == "observed-median"
    assert result["observed_median_minutes"] == 45
    assert result["minutes"] == 45
    assert result["clamped"] is False


def test_a_marathon_evening_cannot_become_every_future_block():
    result = calibrated_duration(
        attempts=_attempts([240] * MIN_DURATION_SAMPLE),
        kind="evidence_probe",
        default_minutes=30,
    )

    assert result["observed_median_minutes"] == 240
    assert result["minutes"] == 60  # the 2.0x band ceiling
    assert result["clamped"] is True


def test_a_run_of_interrupted_attempts_cannot_shrink_a_block_to_nothing():
    result = calibrated_duration(
        attempts=_attempts([1] * MIN_DURATION_SAMPLE),
        kind="evidence_probe",
        default_minutes=30,
    )

    assert result["minutes"] == 15  # the 0.5x band floor
    assert result["clamped"] is True


def test_another_kind_of_work_never_sets_this_kind_of_duration():
    result = calibrated_duration(
        attempts=_attempts([2] * 40, kind="review"),
        kind="far_transfer_probe",
        default_minutes=45,
    )

    assert result["minutes"] == 45
    assert result["source"] == "domain-pack-default"
    assert result["sample_size"] == 0


def test_attempts_without_a_recorded_duration_do_not_count_as_a_sample():
    result = calibrated_duration(
        attempts=_attempts([None] * 10 + [50, 50]),
        kind="evidence_probe",
        default_minutes=30,
    )

    assert result["sample_size"] == 2
    assert result["source"] == "domain-pack-default"


def test_calibrated_durations_land_on_a_readable_grid():
    result = calibrated_duration(
        attempts=_attempts([37, 38, 37, 39, 36]),
        kind="evidence_probe",
        default_minutes=30,
    )

    assert result["observed_median_minutes"] == 37
    assert result["minutes"] == 35


def test_unmeasured_adherence_leaves_the_day_budget_untouched():
    assert capacity_factor(None)["factor"] == 1.0
    thin = capacity_factor(
        _adherence({"verdict": "insufficient_evidence", "days_measured": 2, "completion_rate": None})
    )
    assert thin["factor"] == 1.0
    assert thin["source"] == "uncalibrated"


def test_a_day_that_is_never_finished_tightens_tomorrow():
    result = capacity_factor(_measured(0.6))

    assert result["factor"] == 0.6
    assert result["source"] == "observed-adherence"
    assert result["completion_rate"] == 0.6


def test_calibration_stops_short_of_planning_the_day_away():
    assert capacity_factor(_measured(0.05))["factor"] == MIN_CAPACITY_FACTOR


def test_a_learner_who_finishes_everything_is_never_volunteered_for_more():
    assert capacity_factor(_measured(1.0))["factor"] == 1.0


def test_an_unmeasured_intervention_kind_moves_no_score():
    rows = [{"kind": "guided_repair", "verdict": "insufficient_evidence", "acted_on": 2}]

    result = outcome_adjustment(by_kind=rows, kind="guided_repair")

    assert result["delta"] == 0
    assert result["source"] == "insufficient_evidence"
    assert result["sample_size"] == 2


def test_an_unseen_intervention_kind_moves_no_score():
    result = outcome_adjustment(by_kind=[], kind="guided_repair")

    assert result["delta"] == 0
    assert result["improvement_rate"] is None


def test_a_kind_that_reliably_works_is_promoted_and_one_that_does_not_is_demoted():
    def row(rate: float) -> list[dict]:
        return [
            {
                "kind": "independence_probe",
                "verdict": "measured",
                "acted_on": 8,
                "improvement_rate": rate,
            }
        ]

    assert outcome_adjustment(by_kind=row(1.0), kind="independence_probe")["delta"] == (
        MAX_OUTCOME_ADJUSTMENT
    )
    assert outcome_adjustment(by_kind=row(0.0), kind="independence_probe")["delta"] == (
        -MAX_OUTCOME_ADJUSTMENT
    )
    assert outcome_adjustment(by_kind=row(0.5), kind="independence_probe")["delta"] == 0
    assert outcome_adjustment(by_kind=row(0.75), kind="independence_probe")["delta"] == 4
