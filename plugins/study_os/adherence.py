"""Reconcile the day StudyOS planned against the day the learner had.

Applying a Plan Proposal writes concrete events into a Schedule, and until
this module existed that was the end of the story: nothing ever asked whether
the block happened.  :mod:`plugins.study_os.outcomes` could only see whether
*any* evidence arrived after a decision, so a plan the learner half-followed
and a plan they followed exactly were indistinguishable, and a plan that was
simply too big to fit in an evening looked like a learner who would not study.

This module answers the narrower question the calendar makes answerable: for
each event an accepted plan wrote, did matching evidence arrive that day, and
how much time did it actually take.  It derives and stores nothing -- an
applied event already carries its provenance and every attempt carries its own
timestamp, so adherence is recomputed from evidence like every other StudyOS
judgment.

Three rules keep the answer honest:

*Only what StudyOS promised is measured.*  Events without a
``source_plan_proposal_id`` were authored by the learner, not proposed by the
recommender, and holding the recommender to them would credit or blame it for
plans it never made.  They are counted as ``unmeasured_events`` so the
omission is visible rather than implied.

*An event that has not finished is not a missed event.*  A plan derived at
09:00 describes an evening the learner has not reached yet.  Events ending
after ``as_of`` are reported as ``pending`` and excluded from every rate.

*Studying something else is not the same as not studying.*  Attempts that
match no planned event are reported as ``unplanned_attempt_ids`` rather than
discarded, because "followed a different plan" and "did nothing" are different
findings and only one of them means the day was too full.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

ADHERENCE_SCHEMA_VERSION = "study_plan_adherence.v1"

# Observed effort within this share of the planned duration is the plan being
# followed, not a deviation worth naming.
EFFORT_TOLERANCE = 0.25

# Fewer measured days than this cannot tell a habit from a bad week, so the
# summary withholds its verdict rather than publishing noise.
MIN_ADHERENCE_SAMPLE = 3

# How far back a caller looks when it does not say. Long enough to cross a
# thin week, short enough that a habit the learner has already changed stops
# shaping today's plan.
DEFAULT_LOOKBACK_DAYS = 14

STATUSES = (
    "not_started",
    "occurred",
    "on_plan",
    "under_run",
    "over_run",
)

# Statuses that mean the event happened at all, whatever it cost.
_OCCURRED = {"occurred", "on_plan", "under_run", "over_run"}


def _moment(value: Any) -> datetime | None:
    """Parse a timezone-aware ISO timestamp, or None.

    Naive timestamps are rejected rather than assumed local: an event and an
    attempt are compared by the day they fall on, and guessing an offset would
    silently move evidence across a date boundary.
    """

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _duration_minutes(attempt: dict[str, Any]) -> int | None:
    seconds = attempt.get("duration_seconds")
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
        return None
    return max(1, int(round(seconds / 60)))


def _objective_ids(attempt: dict[str, Any]) -> set[str]:
    return {str(value) for value in attempt.get("objective_ids") or []}


def _status(planned: int, observed: int | None) -> str:
    if observed is None:
        return "occurred"
    if planned <= 0:
        return "occurred"
    ratio = observed / planned
    if ratio < 1 - EFFORT_TOLERANCE:
        return "under_run"
    if ratio > 1 + EFFORT_TOLERANCE:
        return "over_run"
    return "on_plan"


def _planned_events(
    schedules: list[dict[str, Any]],
    *,
    tzinfo: ZoneInfo,
    start: date,
    end: date,
) -> tuple[list[dict[str, Any]], int]:
    """Applied day-plan events falling inside the range, plus what was skipped."""

    planned: list[dict[str, Any]] = []
    unmeasured = 0
    for schedule in schedules:
        schedule_id = str(schedule.get("schedule_id") or "")
        for event in schedule.get("events") or []:
            if not isinstance(event, dict):
                continue
            begins = _moment(event.get("start"))
            if begins is None:
                continue
            local = begins.astimezone(tzinfo)
            if not start <= local.date() <= end:
                continue
            if not str(event.get("source_plan_proposal_id") or "").strip():
                unmeasured += 1
                continue
            duration = event.get("duration_minutes")
            minutes = (
                duration
                if isinstance(duration, int) and not isinstance(duration, bool) and duration > 0
                else 0
            )
            ends = _moment(event.get("end")) or (begins + timedelta(minutes=minutes))
            planned.append(
                {
                    "event_id": str(event.get("id") or ""),
                    "schedule_id": schedule_id,
                    "source_plan_proposal_id": str(event.get("source_plan_proposal_id") or ""),
                    "source_intervention_id": str(event.get("source_intervention_id") or ""),
                    "objective_id": str(event.get("source_objective_id") or ""),
                    "evidence_dimension": str(event.get("evidence_dimension") or ""),
                    "kind": str(event.get("type") or ""),
                    "planned_start": local.isoformat(timespec="seconds"),
                    "planned_minutes": minutes,
                    "_local": local,
                    "_ends": ends,
                }
            )
    planned.sort(key=lambda item: (item["_local"], item["event_id"]))
    return planned, unmeasured


def build_plan_adherence(
    *,
    schedules: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    tzinfo: ZoneInfo,
    start: date,
    end: date,
    as_of: datetime,
) -> dict[str, Any]:
    """Compare applied day-plan events against the evidence recorded that day.

    Matching is by ``(objective, evidence dimension, local date)`` -- the same
    key :mod:`plugins.study_os.outcomes` uses -- and deliberately not by clock
    time.  A learner who moves the 20:00 block to 22:00 followed the plan; only
    the delay is worth reporting, so ``start_delay_minutes`` records it instead
    of the match rejecting it.

    An attempt is claimed by at most one event, earliest first, so a single
    piece of evidence can never make two planned blocks look completed.
    """

    events, unmeasured_events = _planned_events(
        schedules, tzinfo=tzinfo, start=start, end=end
    )
    by_date: dict[date, list[dict[str, Any]]] = {}
    for event in events:
        by_date.setdefault(event["_local"].date(), []).append(event)

    attempts_by_date: dict[date, list[dict[str, Any]]] = {}
    for attempt in attempts:
        occurred = _moment(attempt.get("occurred_at"))
        if occurred is None:
            continue
        local = occurred.astimezone(tzinfo)
        if start <= local.date() <= end:
            attempts_by_date.setdefault(local.date(), []).append(
                {"attempt": attempt, "at": local}
            )

    days: list[dict[str, Any]] = []
    for target in sorted(set(by_date) | set(attempts_by_date)):
        day_events = by_date.get(target, [])
        day_attempts = sorted(
            attempts_by_date.get(target, []), key=lambda entry: entry["at"]
        )
        claimed: set[int] = set()
        rendered: list[dict[str, Any]] = []

        for event in day_events:
            if event["_ends"] > as_of:
                rendered.append({**_public(event), "status": "pending"})
                continue
            if not event["objective_id"] or not event["evidence_dimension"]:
                # Nothing in the evidence can be attributed to this block, so
                # calling it missed would be an accusation the data cannot
                # support.
                rendered.append({**_public(event), "status": "unmeasurable"})
                continue

            matched: list[dict[str, Any]] = []
            for index, entry in enumerate(day_attempts):
                if index in claimed:
                    continue
                attempt = entry["attempt"]
                if str(attempt.get("transfer_level") or "") != event["evidence_dimension"]:
                    continue
                if event["objective_id"] not in _objective_ids(attempt):
                    continue
                claimed.add(index)
                matched.append(entry)

            observed_parts = [
                minutes
                for entry in matched
                if (minutes := _duration_minutes(entry["attempt"])) is not None
            ]
            observed = sum(observed_parts) if observed_parts else None
            first = matched[0]["at"] if matched else None
            rendered.append(
                {
                    **_public(event),
                    "attempt_ids": [
                        str(entry["attempt"].get("attempt_id") or "") for entry in matched
                    ],
                    "observed_minutes": observed,
                    "first_evidence_at": first.isoformat(timespec="seconds") if first else None,
                    "start_delay_minutes": (
                        int((first - event["_local"]).total_seconds() // 60)
                        if first is not None
                        else None
                    ),
                    "status": (
                        "not_started"
                        if not matched
                        else _status(event["planned_minutes"], observed)
                    ),
                }
            )

        measured = [item for item in rendered if item["status"] in STATUSES]
        occurred_events = [item for item in measured if item["status"] in _OCCURRED]
        days.append(
            {
                "date": target.isoformat(),
                "events": rendered,
                "events_measured": len(measured),
                "events_occurred": len(occurred_events),
                "events_pending": sum(1 for item in rendered if item["status"] == "pending"),
                "minutes_planned": sum(item["planned_minutes"] for item in measured),
                "minutes_observed": sum(
                    item["observed_minutes"] or 0 for item in occurred_events
                ),
                "unplanned_attempt_ids": [
                    str(entry["attempt"].get("attempt_id") or "")
                    for index, entry in enumerate(day_attempts)
                    if index not in claimed
                ],
            }
        )

    return {
        "schema_version": ADHERENCE_SCHEMA_VERSION,
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "timezone": str(tzinfo),
        "as_of": as_of.astimezone(tzinfo).isoformat(timespec="seconds"),
        "days": days,
        "unmeasured_events": unmeasured_events,
        "totals": _totals(days),
    }


def _public(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if not key.startswith("_")}


def _totals(days: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate only over days that actually carried a measurable plan.

    Days with no planned event are not adherence failures -- the learner was
    never asked to do anything -- so including them would report a recommender
    that never proposed as a learner who never complied.
    """

    planned_days = [day for day in days if day["events_measured"]]
    events_measured = sum(day["events_measured"] for day in planned_days)
    events_occurred = sum(day["events_occurred"] for day in planned_days)
    minutes_planned = sum(day["minutes_planned"] for day in planned_days)
    minutes_observed = sum(day["minutes_observed"] for day in planned_days)
    totals: dict[str, Any] = {
        "days_measured": len(planned_days),
        "days_pending": sum(1 for day in days if day["events_pending"]),
        "events_measured": events_measured,
        "events_occurred": events_occurred,
        "minutes_planned": minutes_planned,
        "minutes_observed": minutes_observed,
        "unplanned_attempts": sum(len(day["unplanned_attempt_ids"]) for day in days),
    }
    if len(planned_days) >= MIN_ADHERENCE_SAMPLE:
        totals["completion_rate"] = round(events_occurred / events_measured, 3)
        totals["effort_ratio"] = (
            round(minutes_observed / minutes_planned, 3) if minutes_planned else None
        )
        totals["verdict"] = "measured"
    else:
        totals["completion_rate"] = None
        totals["effort_ratio"] = None
        totals["verdict"] = "insufficient_evidence"
        totals["needed_for_signal"] = MIN_ADHERENCE_SAMPLE - len(planned_days)
    return totals
