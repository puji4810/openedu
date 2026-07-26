"""Project an Intervention Queue onto one day's concrete study events.

The Intervention Queue says which capability is least supported by evidence;
a Schedule phase says what the week is for and how much effort it may cost.
Neither answers "what do I do today", and until this module existed nothing
did: every Schedule in practice carried phases and an empty ``events`` list,
so the day-level calendar had nothing to render.

This module is the missing projection.  It is pure -- it reads a queue, the
already-loaded Schedules, and the attempt history, and returns a proposed day
plan.  It writes nothing and mutates no Schedule, because a proposed day is a
recommendation the learner still has to accept.

The study window is derived from evidence rather than assumed.  Two real
projects on this machine study at opposite ends of the day (one clusters at
20:00-23:00, the other at 08:00-12:00), so any fixed default would be wrong
for someone.  When the history is too thin to be meaningful the window falls
back to a stated default and says so in ``source``.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

DAY_PLAN_SCHEMA_VERSION = "study_day_plan.v1"

# Below this many timestamped attempts an hour histogram is noise, not habit.
MIN_WINDOW_SAMPLE = 12
# The derived window must cover at least this share of observed study activity.
WINDOW_COVERAGE = 0.7
# A derived window wider than this stops describing a habit and starts
# describing "awake", so it is clamped and the plan simply packs from its start.
MAX_WINDOW_HOURS = 10
DEFAULT_WINDOW = (19, 23)

# Events are packed back to back with a short changeover so a proposed day
# reads like a plan a person could follow rather than an unbroken block.
BREAK_MINUTES = 10
MAX_EVENT_MINUTES = 720  # the Schedule contract's per-event ceiling


def _hour_histogram(attempts: list[dict[str, Any]], tzinfo: ZoneInfo) -> Counter:
    hours: Counter = Counter()
    for attempt in attempts:
        raw = attempt.get("occurred_at")
        if not raw:
            continue
        try:
            moment = datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        if moment.tzinfo is None:
            continue
        hours[moment.astimezone(tzinfo).hour] += 1
    return hours


def study_window(attempts: list[dict[str, Any]], *, tzinfo: ZoneInfo) -> dict[str, Any]:
    """Derive the learner's habitual study window from timestamped evidence.

    Returns the shortest contiguous span of hours covering ``WINDOW_COVERAGE``
    of observed activity.  Contiguous rather than "top N hours" because the
    result has to be a bookable block; shortest rather than widest because a
    window that spans the whole day places events at hours the learner has
    never once studied in.
    """

    hours = _hour_histogram(attempts, tzinfo)
    total = sum(hours.values())
    if total < MIN_WINDOW_SAMPLE:
        start, end = DEFAULT_WINDOW
        return {
            "start_hour": start,
            "end_hour": end,
            "source": "default",
            "sample_size": total,
            "coverage": None,
        }

    needed = total * WINDOW_COVERAGE
    best: tuple[int, int, int] | None = None  # (span, start, covered)
    for start in range(24):
        covered = 0
        for end in range(start, 24):
            covered += hours.get(end, 0)
            if covered >= needed:
                span = end - start + 1
                if best is None or (span, start) < (best[0], best[1]):
                    best = (span, start, covered)
                break

    if best is None:
        # No contiguous span reaches the coverage bar -- activity is scattered
        # across midnight or too sparse.  Anchor on the single busiest hour
        # instead of inventing a block the evidence does not support.
        busiest = max(hours.items(), key=lambda item: (item[1], -item[0]))[0]
        return {
            "start_hour": busiest,
            "end_hour": min(23, busiest + 3),
            "source": "modal-hour",
            "sample_size": total,
            "coverage": round(hours[busiest] / total, 3),
        }

    span, start, covered = best
    end = min(23, start + min(span, MAX_WINDOW_HOURS) - 1)
    return {
        "start_hour": start,
        "end_hour": end,
        "source": "evidence",
        "sample_size": total,
        "coverage": round(covered / total, 3),
    }


def active_phase(schedule: dict[str, Any], target: date) -> dict[str, Any] | None:
    """The phase whose date range contains *target*, if any."""
    for phase in schedule.get("phases") or []:
        try:
            start = date.fromisoformat(str(phase.get("start")))
            end = date.fromisoformat(str(phase.get("end")))
        except (TypeError, ValueError):
            continue
        if start <= target <= end:
            return phase
    return None


def _phase_daily_budget(phase: dict[str, Any], target: date) -> int | None:
    """Split a phase's aggregate effort across the days it still has left.

    ``effort_minutes`` is the whole phase's workload, so charging a single day
    with it would propose a twelve-hour session.  Remaining days rather than
    total days, so a phase that is already half spent tightens what is left
    instead of pretending the budget renews.
    """

    effort = phase.get("effort_minutes")
    if not isinstance(effort, int) or isinstance(effort, bool) or effort <= 0:
        return None
    try:
        end = date.fromisoformat(str(phase.get("end")))
    except (TypeError, ValueError):
        return None
    days_left = (end - target).days + 1
    if days_left <= 0:
        return None
    return max(1, effort // days_left)


def _subject_id(phase: dict[str, Any] | None, project: dict[str, Any]) -> str:
    """Best-effort subject for an event.

    Objectives do not declare a ``track_id``, so there is no authoritative
    objective-to-subject link to read.  The phase's own subjects come first,
    then the project's first track; the event additionally carries
    ``source_objective_id`` so the real provenance stays explicit rather than
    being implied by this fallback.
    """

    subjects = (phase or {}).get("subject_ids")
    if isinstance(subjects, list) and subjects:
        first = str(subjects[0]).strip()
        if first:
            return first
    tracks = project.get("tracks")
    if isinstance(tracks, list):
        for track in tracks:
            if isinstance(track, dict):
                identifier = str(track.get("id") or "").strip()
                if identifier:
                    return identifier
    domain = str(project.get("domain") or "").strip()
    return domain or "general"


def _tokens(value: str) -> set[str]:
    """Subject-bearing tokens of an identifier.

    Purely numeric parts are dropped: project numbers and years ("408",
    "2026") appear in every Schedule id of a project, so they add no
    discriminating signal while making a coincidental match look meaningful.
    """

    return {
        token
        for token in str(value).replace("_", "-").split("-")
        if len(token) > 2 and not token.isdigit()
    }


def route_to_schedule(
    item: dict[str, Any],
    targets: list[dict[str, Any]],
) -> tuple[int, str]:
    """Pick which Schedule an Intervention's event belongs in.

    A project may run one Schedule per subject -- this machine has a maths
    project with separate linear-algebra, probability and integrated
    Schedules -- and nothing in the data links an Objective to a Schedule.
    Falling back to "the first one" means alphabetical order silently decides
    that a probability probe belongs to the linear-algebra plan, so instead
    the Objective id is matched against Schedule ids by shared token, and a
    single unambiguous winner routes.  Ties and misses fall back to the first
    covering Schedule, and the method is reported per event so a wrong guess
    is visible rather than implied.
    """

    if len(targets) == 1:
        return 0, "sole-covering-schedule"
    objective_tokens = _tokens(item.get("objective_id") or "")
    if objective_tokens:
        scored = [
            (len(objective_tokens & _tokens(target["schedule_id"])), index)
            for index, target in enumerate(targets)
        ]
        ranked = sorted(scored, key=lambda pair: (-pair[0], pair[1]))
        if ranked[0][0] > 0 and (len(ranked) == 1 or ranked[0][0] > ranked[1][0]):
            return ranked[0][1], "objective-token-match"
    return 0, "fallback-first-covering-schedule"


def _event_title(item: dict[str, Any]) -> str:
    kind = str(item.get("kind") or "activity").replace("_", " ")
    capability = str(item.get("capability") or "").strip()
    if len(capability) > 60:
        capability = capability[:59].rstrip() + "…"
    return f"{kind}: {capability}" if capability else kind


def _round_up(moment: datetime, minutes: int = 5) -> datetime:
    remainder = moment.minute % minutes
    base = moment.replace(second=0, microsecond=0)
    return base if (remainder == 0 and moment.second == 0) else base + timedelta(
        minutes=minutes - remainder
    )


def build_day_plan(
    *,
    queue_items: list[dict[str, Any]],
    schedules: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    project: dict[str, Any],
    target: date,
    tzinfo: ZoneInfo,
    now: datetime | None = None,
    capacity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Lay the queue out over *target* inside the learner's study window.

    Items are placed in queue order -- the queue is already sorted by
    priority, so the most evidence-starved capability gets the first and
    freshest slot.  An item that does not fit is reported in ``unplaced``
    with the budget that excluded it, because a silently dropped
    recommendation is indistinguishable from one that was never made.

    ``now`` keeps the plan in the future.  A day plan is generated when the
    learner sits down, which is usually part-way through their own study
    window, so anchoring on the window start alone would propose a morning of
    sessions to someone opening the app at night.

    ``capacity`` is the measured share of a planned day this learner actually
    completes, from :func:`plugins.study_os.calibration.capacity_factor`.  A
    phase budget states intent; a plan that has never once been finished states
    what fits.  Absent or unmeasured, the nominal budget stands.
    """

    window = study_window(attempts, tzinfo=tzinfo)
    factor = capacity.get("factor") if isinstance(capacity, dict) else None
    if not isinstance(factor, (int, float)) or isinstance(factor, bool) or not 0 < factor <= 1:
        factor = 1.0
    day_start = datetime(
        target.year, target.month, target.day, window["start_hour"], tzinfo=tzinfo
    )
    day_end = datetime(
        target.year, target.month, target.day, window["end_hour"], tzinfo=tzinfo
    ) + timedelta(hours=1)
    window_minutes = max(0, int((day_end - day_start).total_seconds() // 60))

    # One active phase per Schedule; a project may legitimately run several
    # Schedules in parallel (one per subject), so each keeps its own budget.
    targets: list[dict[str, Any]] = []
    for schedule in schedules:
        phase = active_phase(schedule, target)
        if phase is None:
            continue
        targets.append(
            {
                "schedule_id": str(schedule.get("schedule_id") or ""),
                "schedule_title": str(schedule.get("title") or ""),
                "phase": phase,
                "budget": _phase_daily_budget(phase, target),
            }
        )

    if not targets:
        return {
            "schema_version": DAY_PLAN_SCHEMA_VERSION,
            "target_date": target.isoformat(),
            "timezone": str(tzinfo),
            "study_window": window,
            "capacity": capacity if isinstance(capacity, dict) else None,
            "minutes_planned": 0,
            "schedules": [],
            "unplaced": [
                {
                    "intervention_id": str(item.get("intervention_id") or ""),
                    "reason": "no Schedule phase covers the target date",
                }
                for item in queue_items
            ],
        }

    for entry in targets:
        entry["events"] = []
        entry["spent"] = 0
        if entry["budget"] is None:
            entry["budget"] = window_minutes
        entry["nominal_budget"] = entry["budget"]
        # At least one minute survives the factor: a day whose budget rounds to
        # zero would report every Intervention as unplaced for a reason the
        # learner cannot act on.
        entry["budget"] = max(1, int(entry["budget"] * factor))

    # One learner, one clock: the cursor is global so events from parallel
    # Schedules cannot be proposed for the same minute. Budgets stay per
    # Schedule because each phase owns its own effort.
    unplaced: list[dict[str, Any]] = []
    cursor = day_start
    if now is not None and now.astimezone(tzinfo).date() == target:
        cursor = max(cursor, _round_up(now.astimezone(tzinfo)))
    spent = 0

    for index, item in enumerate(queue_items):
        activity = item.get("recommended_activity") or {}
        duration = activity.get("duration_minutes")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
            unplaced.append(
                {
                    "intervention_id": str(item.get("intervention_id") or ""),
                    "reason": "recommended_activity.duration_minutes is missing or not positive",
                }
            )
            continue
        duration = min(duration, MAX_EVENT_MINUTES)
        target_index, routing = route_to_schedule(item, targets)
        entry = targets[target_index]

        if cursor + timedelta(minutes=duration) > day_end:
            unplaced.append(
                {
                    "intervention_id": str(item.get("intervention_id") or ""),
                    "reason": (
                        f"does not fit between {cursor.strftime('%H:%M')} and the "
                        f"{window['end_hour']:02d}:59 end of the study window"
                    ),
                }
            )
            continue
        if entry["spent"] + duration > entry["budget"]:
            unplaced.append(
                {
                    "intervention_id": str(item.get("intervention_id") or ""),
                    "reason": (
                        f"exceeds the {entry['budget']} minute daily budget of phase "
                        f"{entry['phase'].get('id')} in {entry['schedule_id']}"
                    ),
                }
            )
            continue

        end = cursor + timedelta(minutes=duration)
        goals = [str(reason) for reason in (item.get("reasons") or []) if str(reason).strip()]
        criteria = [
            str(value)
            for value in (activity.get("success_criteria") or [])
            if str(value).strip()
        ]
        entry["events"].append(
            {
                "id": f"dp-{target.isoformat()}-{index + 1:02d}-{str(item.get('kind') or 'activity')}",
                "title": _event_title(item),
                "subject_id": _subject_id(entry["phase"], project),
                "type": str(item.get("kind") or "activity"),
                "start": cursor.isoformat(timespec="seconds"),
                "end": end.isoformat(timespec="seconds"),
                "duration_minutes": duration,
                "goals": (goals + criteria) or ["Produce evaluator-provenanced evidence."],
                "status": "planned",
                "source_intervention_id": str(item.get("intervention_id") or ""),
                "source_objective_id": str(item.get("objective_id") or ""),
                "evidence_dimension": str(item.get("evidence_dimension") or ""),
                "routing": routing,
            }
        )
        entry["spent"] += duration
        spent += duration
        cursor = end + timedelta(minutes=BREAK_MINUTES)

    return {
        "schema_version": DAY_PLAN_SCHEMA_VERSION,
        "target_date": target.isoformat(),
        "timezone": str(tzinfo),
        "study_window": window,
        "capacity": capacity if isinstance(capacity, dict) else None,
        "minutes_budget": sum(entry["budget"] for entry in targets),
        "minutes_budget_nominal": sum(entry["nominal_budget"] for entry in targets),
        "minutes_planned": spent,
        "schedules": [
            {
                "schedule_id": entry["schedule_id"],
                "schedule_title": entry["schedule_title"],
                "phase_id": str(entry["phase"].get("id") or ""),
                "phase_goal": str(entry["phase"].get("goal") or ""),
                "minutes_budget": entry["budget"],
                "minutes_budget_nominal": entry["nominal_budget"],
                "minutes_planned": entry["spent"],
                "events": entry["events"],
            }
            for entry in targets
            if entry["events"]
        ],
        "unplaced": unplaced,
    }
