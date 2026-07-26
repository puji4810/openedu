"""Correct the recommender's own constants from what was actually measured.

StudyOS derives every claim about the *learner* from evidence, but its claims
about *itself* were hand-written: how long an Intervention takes came from a
Domain Pack constant, how much a day can hold came from dividing a phase
budget, and how much a kind of Intervention is worth came from a table no
observation could move.  :mod:`plugins.study_os.outcomes` and
:mod:`plugins.study_os.adherence` measure all three, and until this module
existed nothing read them -- the loop was closed on paper and open in code.

This module is the consumer.  It converts measurement into bounded
corrections, and every correction obeys the same three rules:

*A thin sample yields no correction.*  Below each minimum the constant stands
unchanged and says so through ``source``, because a recommender that reorders
itself on two data points is worse than one that does not learn at all.

*Corrections are bounded and reported.*  Each returns the number it produced,
the number it started from, and the evidence it rested on, so a surprising
plan can be traced to the observation that shaped it rather than to an opaque
score.

*Non-adherence never becomes a verdict on advice.*  A recommendation the
learner never acted on is excluded from effectiveness entirely; it flows to
:func:`capacity_factor`, where "the day was too full" is the finding it
actually supports.
"""

from __future__ import annotations

from statistics import median
from typing import Any

CALIBRATION_SCHEMA_VERSION = "study_calibration.v1"

# Fewer same-kind attempts than this describe one session's mood, not how long
# this learner needs.
MIN_DURATION_SAMPLE = 5

# A calibrated duration stays within these multiples of the Domain Pack
# default. One marathon evening must not turn every future probe into a
# four-hour block, and one interrupted attempt must not shrink them to nothing.
DURATION_BAND = (0.5, 2.0)

# Calibrated durations land on this grid so a derived plan still reads like a
# plan; the day plan already rounds its start times the same way.
DURATION_STEP = 5

# The most a measured improvement rate may move a priority score. Large enough
# to break ties between comparable gaps, too small to bury an unobserved
# capability under a well-performing one.
MAX_OUTCOME_ADJUSTMENT = 8

# The rate at which an Intervention kind is neither credited nor penalised.
NEUTRAL_IMPROVEMENT_RATE = 0.5

# Calibration may tighten a day, never below this share of the phase budget:
# past a point the finding is "this plan is wrong", which is a decision for the
# learner and not a number for this module to keep shaving.
MIN_CAPACITY_FACTOR = 0.5

# Decisions older than this stop describing the learner the recommender is
# advising now, so they leave the sample rather than anchoring it to a phase
# of study that has already ended.
OUTCOME_LOOKBACK_DAYS = 90


def _positive_minutes(value: Any) -> int | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return None
    return max(1, int(round(value / 60)))


def calibrated_duration(
    *,
    attempts: list[dict[str, Any]],
    kind: str,
    default_minutes: int,
) -> dict[str, Any]:
    """How long this learner actually takes over an Intervention of *kind*.

    Same-kind evidence only.  A project's attempts are dominated by whichever
    activity the learner does most -- usually short reviews -- so a pooled
    median would quietly propose three-minute transfer probes.  With too few
    same-kind attempts the Domain Pack default stands rather than borrowing a
    number from a different kind of work.

    The median rather than the mean: an abandoned attempt recorded as two
    minutes and a session left open overnight are both common, and both move a
    mean far more than they should move a plan.
    """

    observed = [
        minutes
        for attempt in attempts
        if str(attempt.get("activity_kind") or "") == kind
        and (minutes := _positive_minutes(attempt.get("duration_seconds"))) is not None
    ]
    result: dict[str, Any] = {
        "minutes": default_minutes,
        "default_minutes": default_minutes,
        "sample_size": len(observed),
        "observed_median_minutes": None,
        "source": "domain-pack-default",
        "clamped": False,
    }
    if len(observed) < MIN_DURATION_SAMPLE:
        return result

    raw = int(round(median(observed)))
    result["observed_median_minutes"] = raw
    low, high = DURATION_BAND
    floor = max(1, int(default_minutes * low))
    ceiling = max(floor, min(720, int(default_minutes * high)))
    bounded = min(max(raw, floor), ceiling)
    stepped = int(round(bounded / DURATION_STEP)) * DURATION_STEP
    result["minutes"] = min(max(stepped, floor), ceiling)
    result["source"] = "observed-median"
    result["clamped"] = bounded != raw
    return result


def capacity_factor(adherence: dict[str, Any] | None) -> dict[str, Any]:
    """How much of a day's nominal budget this learner actually completes.

    Completion of *events* rather than of minutes: an attempt may carry no
    duration at all, so a minutes ratio silently reads untimed work as time
    never spent, while "did the block happen" survives missing fields.

    The factor never exceeds 1.0.  A phase's ``effort_minutes`` is the
    learner's own statement of how much this stretch of study is worth, and
    measurement may show that statement to be optimistic -- it may not
    volunteer them for more than they asked for.
    """

    totals = (adherence or {}).get("totals") or {}
    result: dict[str, Any] = {
        "factor": 1.0,
        "source": "uncalibrated",
        "days_measured": int(totals.get("days_measured") or 0),
        "completion_rate": None,
    }
    if totals.get("verdict") != "measured":
        return result
    rate = totals.get("completion_rate")
    if not isinstance(rate, (int, float)) or isinstance(rate, bool):
        return result
    result["completion_rate"] = round(float(rate), 3)
    result["factor"] = round(min(1.0, max(MIN_CAPACITY_FACTOR, float(rate))), 3)
    result["source"] = "observed-adherence"
    return result


def outcome_adjustment(
    *,
    by_kind: list[dict[str, Any]] | None,
    kind: str,
) -> dict[str, Any]:
    """Move an Intervention kind's priority by how often it has worked.

    The rate comes from :mod:`plugins.study_os.outcomes`, which already
    excludes recommendations the learner never acted on, so this reads
    effectiveness and never compliance.

    Its reach is deliberately short.  A kind's improvement rate is confounded
    with the difficulty of the capabilities it gets assigned to -- repair work
    is slow because it is repair work, not because repair is a bad idea -- so
    the adjustment is capped at :data:`MAX_OUTCOME_ADJUSTMENT` and never
    changes *which* Intervention an Objective receives. Kind selection stays
    with the evidence, in :func:`plugins.study_os.interventions._kind_for`;
    this only orders the queue between Objectives.
    """

    result: dict[str, Any] = {
        "delta": 0,
        "source": "insufficient_evidence",
        "improvement_rate": None,
        "sample_size": 0,
    }
    row = next(
        (
            item
            for item in (by_kind or [])
            if isinstance(item, dict) and str(item.get("kind") or "") == kind
        ),
        None,
    )
    if row is None:
        return result
    result["sample_size"] = int(row.get("acted_on") or 0)
    if row.get("verdict") != "measured":
        return result
    rate = row.get("improvement_rate")
    if not isinstance(rate, (int, float)) or isinstance(rate, bool):
        return result
    result["improvement_rate"] = round(float(rate), 3)
    scaled = (float(rate) - NEUTRAL_IMPROVEMENT_RATE) / NEUTRAL_IMPROVEMENT_RATE
    delta = int(round(MAX_OUTCOME_ADJUSTMENT * scaled))
    result["delta"] = max(-MAX_OUTCOME_ADJUSTMENT, min(MAX_OUTCOME_ADJUSTMENT, delta))
    result["source"] = "measured"
    return result
