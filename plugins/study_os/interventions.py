"""Derive a bounded next-action queue from StudyOS evidence.

The orchestrator is intentionally pure: it reads a project plus immutable
attempts and returns a queue and a reviewable proposal.  Persistence and
Schedule mutation remain outside this module so callers cannot accidentally
turn a recommendation into a calendar change.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from plugins.study_os.calibration import (
    calibrated_duration,
    capacity_factor,
    outcome_adjustment,
)
from plugins.study_os.day_plan import build_day_plan
from plugins.study_os.domain_packs import domain_pack_for
from plugins.study_os.schemas import (
    EVIDENCE_DIMENSIONS,
    INTERVENTION_POLICY_VERSION,
    INTERVENTION_QUEUE_SCHEMA_VERSION,
    PLAN_PROPOSAL_SCHEMA_VERSION,
    PROJECT_SCHEMA_VERSION,
)


DiagnosisBuilder = Callable[[list[dict[str, Any]]], dict[str, Any]]

_FRESHNESS_DAYS = {
    "recall": 14,
    "recognition": 21,
    "execution": 30,
    "explanation": 30,
    "near_transfer": 45,
    "far_transfer": 60,
}
_STATUS_BASE_SCORE = {
    "unobserved": 70,
    "developing": 76,
    "supported": 60,
    "independent": 42,
}
_DEADLINE_BOOST = {
    "none": 0,
    "distant": 0,
    "approaching": 6,
    "near": 12,
    "critical": 18,
    "overdue": 20,
}
_AGE_BOOST = {
    "unobserved": 0,
    "fresh": 0,
    "aging": 4,
    "stale": 10,
}


def parse_as_of(value: Any = None) -> datetime:
    """Resolve a timezone-aware orchestration clock value."""

    if value is None:
        return datetime.now().astimezone()
    if not isinstance(value, str) or not value.strip():
        raise ValueError("as_of must be an ISO datetime with timezone offset")
    try:
        resolved = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "as_of must be a valid ISO datetime with timezone offset"
        ) from exc
    if resolved.tzinfo is None or resolved.utcoffset() is None:
        raise ValueError("as_of must include a timezone offset")
    return resolved


def _digest(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _attempt_id(attempt: dict[str, Any]) -> str:
    return str(attempt.get("attempt_id") or "").strip()


def _attempt_datetime(attempt: dict[str, Any]) -> datetime | None:
    value = attempt.get("occurred_at")
    if not isinstance(value, str):
        return None
    try:
        resolved = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (
        resolved
        if resolved.tzinfo is not None and resolved.utcoffset() is not None
        else None
    )


def _deadline(project: dict[str, Any]) -> date | None:
    value = project.get("deadline") or project.get("exam_date")
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _in_project_timezone(project: dict[str, Any], as_of: datetime) -> datetime:
    timezone = project.get("timezone")
    if not isinstance(timezone, str) or not timezone.strip():
        return as_of
    try:
        return as_of.astimezone(ZoneInfo(timezone))
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"project timezone is not a valid IANA timezone: {timezone}"
        ) from exc


def _deadline_state(project: dict[str, Any], as_of: datetime) -> tuple[int | None, str]:
    deadline = _deadline(project)
    if deadline is None:
        return None, "none"
    days = (deadline - as_of.date()).days
    if days < 0:
        return days, "overdue"
    if days <= 7:
        return days, "critical"
    if days <= 30:
        return days, "near"
    if days <= 90:
        return days, "approaching"
    return days, "distant"


def _age_state(
    attempts: list[dict[str, Any]],
    *,
    as_of: datetime,
    threshold: int,
) -> tuple[int | None, str, str | None]:
    occurred = [
        value for item in attempts if (value := _attempt_datetime(item)) is not None
    ]
    if not occurred:
        return None, "unobserved", None
    latest = max(occurred)
    age = max(0, (as_of.date() - latest.astimezone(as_of.tzinfo).date()).days)
    if age <= threshold // 2:
        band = "fresh"
    elif age <= threshold:
        band = "aging"
    else:
        band = "stale"
    return age, band, latest.isoformat(timespec="seconds")


def _priority_band(score: int) -> str:
    if score >= 80:
        return "high"
    if score >= 55:
        return "medium"
    return "low"


def _duration_for(
    project: dict[str, Any],
    attempts: list[dict[str, Any]],
    kind: str,
) -> dict[str, Any]:
    """The Domain Pack's stated duration, corrected by observed evidence."""

    return calibrated_duration(
        attempts=attempts,
        kind=kind,
        default_minutes=domain_pack_for(project).intervention_duration,
    )


def _assistance_for(kind: str) -> str:
    if kind in {
        "independence_probe",
        "near_transfer_probe",
        "far_transfer_probe",
        "retention_probe",
    }:
        return "independent"
    if kind in {"guided_repair", "prerequisite_repair"}:
        return "guided"
    return "hints_only"


def _kind_for(
    *,
    verification_status: str,
    target: str,
    repeated_cluster: dict[str, Any] | None,
) -> str:
    if verification_status == "independent":
        return "retention_probe"
    if verification_status == "supported":
        return "independence_probe"
    if verification_status == "developing":
        if repeated_cluster:
            return (
                "prerequisite_repair"
                if repeated_cluster.get("kind") == "concept_confusion"
                else "misconception_probe"
            )
        return "guided_repair"
    if target == "near_transfer":
        return "near_transfer_probe"
    if target == "far_transfer":
        return "far_transfer_probe"
    return "evidence_probe"


def _activates_on(objective: dict[str, Any]) -> date | None:
    raw = objective.get("activates_on")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def _objective_views(
    project: dict[str, Any],
    attempts: list[dict[str, Any]],
    as_of_date: date | None = None,
) -> tuple[list[tuple[dict[str, Any], list[dict[str, Any]]]], list[str], list[str]]:
    """Split evidence per Objective, holding back the ones not yet in scope.

    An Objective may declare ``activates_on``.  A timed full-paper Objective is
    a real target with no evidence, so it scores as the most urgent gap in the
    queue and keeps recommending a mock exam to a learner who is still on their
    first pass through the syllabus.  Deferring it is not the same as hiding
    it: evidence recorded against a deferred Objective is still attributed, and
    the queue reports which Objectives it held back and until when.
    """

    if project.get("schema_version") != PROJECT_SCHEMA_VERSION:
        synthetic = {
            "objective_id": "project-readiness",
            "capability": f"Demonstrate readiness for {project.get('title', project['project_id'])}.",
            "success_criteria": [
                "Produce evaluator-provenanced evidence without hidden assistance."
            ],
            "evidence_targets": list(EVIDENCE_DIMENSIONS),
            "source_anchors": [],
        }
        return [(synthetic, attempts)], [], []

    objectives = [
        item for item in project.get("objectives", []) if isinstance(item, dict)
    ]
    known_ids = {str(item.get("objective_id")) for item in objectives}
    views: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    deferred: list[str] = []
    scoped_ids: set[str] = set()
    for objective in objectives:
        objective_id = str(objective.get("objective_id"))
        scoped = [
            attempt
            for attempt in attempts
            if objective_id
            in {str(value) for value in attempt.get("objective_ids", [])}
        ]
        # Attribution happens for every Objective, active or not, so deferring
        # one never pushes its evidence into the unscoped bucket.
        scoped_ids.update(_attempt_id(item) for item in scoped)
        activates_on = _activates_on(objective)
        if as_of_date is not None and activates_on is not None and as_of_date < activates_on:
            deferred.append(f"{objective_id} until {activates_on.isoformat()}")
            continue
        views.append((objective, scoped))
    unscoped = [
        _attempt_id(attempt)
        for attempt in attempts
        if _attempt_id(attempt)
        and (
            _attempt_id(attempt) not in scoped_ids
            or not known_ids.intersection(
                str(value) for value in attempt.get("objective_ids", [])
            )
        )
    ]
    return views, _unique(unscoped), deferred


class InterventionOrchestrator:
    """Turn evidence into one bounded Intervention per active Objective.

    Its result is side-effect free: the caller may inspect the queue, persist
    the proposal, or discard both. ``fingerprint`` lets the persistence seam
    verify that a proposal still represents the derived semantic state.
    """

    def __init__(self, *, project: dict[str, Any], diagnosis_builder: DiagnosisBuilder):
        self._project = project
        self._diagnosis_builder = diagnosis_builder

    def build(
        self,
        *,
        attempts: list[dict[str, Any]],
        as_of: datetime,
        max_items: int = 5,
        schedules: list[dict[str, Any]] | None = None,
        outcomes: dict[str, Any] | None = None,
        adherence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Derive the queue, optionally corrected by its own measured history.

        ``outcomes`` and ``adherence`` are the recommender's record of itself,
        from :mod:`plugins.study_os.outcomes` and
        :mod:`plugins.study_os.adherence`.  Both are optional and both degrade
        to no correction when the sample is too thin, so a new project derives
        exactly the queue it derived before this feedback existed.
        """

        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must include a timezone offset")
        as_of = _in_project_timezone(self._project, as_of)
        if (
            not isinstance(max_items, int)
            or isinstance(max_items, bool)
            or not 1 <= max_items <= 20
        ):
            raise ValueError("max_items must be an integer from 1 to 20")

        project_deadline = _deadline(self._project)
        days_to_deadline, deadline_band = _deadline_state(self._project, as_of)
        objective_views, unscoped_attempt_ids, deferred_objectives = _objective_views(
            self._project, attempts, as_of.date()
        )
        candidates: list[tuple[int, int, dict[str, Any]]] = []
        considered_evidence: list[str] = []

        for objective_index, (objective, scoped_attempts) in enumerate(objective_views):
            considered_evidence.extend(_attempt_id(item) for item in scoped_attempts)
            diagnosis = self._diagnosis_builder(scoped_attempts)
            target_candidates: list[tuple[int, dict[str, Any]]] = []
            declared_targets = {
                str(value) for value in objective.get("evidence_targets", [])
            }
            targets = [
                dimension
                for dimension in EVIDENCE_DIMENSIONS
                if dimension in declared_targets
            ]
            for target_index, target in enumerate(targets):
                dimension = diagnosis.get("evidence_dimensions", {}).get(target, {})
                verification_status = str(
                    dimension.get("verification_status") or "unobserved"
                )
                target_evidence_ids = _unique([
                    str(value) for value in dimension.get("evidence_attempt_ids", [])
                ])
                target_evidence_id_set = set(target_evidence_ids)
                target_attempts = [
                    item
                    for item in scoped_attempts
                    if _attempt_id(item) in target_evidence_id_set
                ]
                threshold = _FRESHNESS_DAYS[target]
                age_days, age_band, latest_evidence_at = _age_state(
                    target_attempts,
                    as_of=as_of,
                    threshold=threshold,
                )
                if verification_status == "independent" and age_band != "stale":
                    continue

                repeated_cluster = (
                    next(
                        (
                            cluster
                            for cluster in diagnosis.get("diagnosis_clusters", [])
                            if isinstance(cluster, dict)
                            and int(cluster.get("count") or 0) >= 2
                        ),
                        None,
                    )
                    if verification_status == "developing"
                    else None
                )
                kind = _kind_for(
                    verification_status=verification_status,
                    target=target,
                    repeated_cluster=repeated_cluster,
                )
                evidence_ids = list(target_evidence_ids)
                if repeated_cluster and verification_status == "developing":
                    evidence_ids = _unique(
                        evidence_ids
                        + [
                            str(value)
                            for value in repeated_cluster.get(
                                "evidence_attempt_ids", []
                            )
                        ]
                    )

                score = _STATUS_BASE_SCORE[verification_status]
                score += _DEADLINE_BOOST[deadline_band]
                score += _AGE_BOOST[age_band]
                repeated_count = (
                    int(repeated_cluster.get("count") or 0) if repeated_cluster else 0
                )
                if verification_status == "developing" and repeated_count:
                    score += min(12, repeated_count * 3)
                # The recommender's own record, applied last so the evidence
                # about the learner decides the score and the evidence about
                # the advice only adjusts it.
                adjustment = outcome_adjustment(
                    by_kind=(outcomes or {}).get("by_kind"), kind=kind
                )
                score += adjustment["delta"]
                score = max(0, min(100, score))
                duration = _duration_for(self._project, attempts, kind)

                reasons = self._reasons(
                    verification_status=verification_status,
                    target=target,
                    age_days=age_days,
                    threshold=threshold,
                    deadline_band=deadline_band,
                    days_to_deadline=days_to_deadline,
                    repeated_cluster=repeated_cluster,
                )
                if adjustment["delta"]:
                    reasons.append(
                        f"{kind.replace('_', ' ')} has improved this project's evidence in "
                        f"{int(round(adjustment['improvement_rate'] * 100))}% of the "
                        f"{adjustment['sample_size']} times it was acted on."
                    )
                semantic_key = {
                    "project_id": self._project["project_id"],
                    "objective_id": objective["objective_id"],
                    "target": target,
                    "kind": kind,
                    "verification_status": verification_status,
                    "evidence_age_band": age_band,
                    "deadline_band": deadline_band,
                    "evidence_attempt_ids": evidence_ids,
                }
                item = {
                    "intervention_id": f"iv-{_digest(semantic_key)[:16]}",
                    "objective_id": objective["objective_id"],
                    "capability": objective["capability"],
                    "kind": kind,
                    "evidence_dimension": target,
                    "priority_score": score,
                    "priority_band": _priority_band(score),
                    "reasons": reasons,
                    "reason_factors": {
                        "verification_status": verification_status,
                        "evidence_age_days": age_days,
                        "evidence_age_band": age_band,
                        "freshness_threshold_days": threshold,
                        "days_to_deadline": days_to_deadline,
                        "deadline_band": deadline_band,
                        "repeated_diagnosis_count": repeated_count,
                        "outcome_adjustment": adjustment["delta"],
                        "outcome_improvement_rate": adjustment["improvement_rate"],
                        "outcome_sample_size": adjustment["sample_size"],
                        "outcome_source": adjustment["source"],
                    },
                    "latest_evidence_at": latest_evidence_at,
                    "evidence_attempt_ids": evidence_ids,
                    "recommended_activity": {
                        "activity_kind": kind,
                        "evidence_target": target,
                        "assistance_level": _assistance_for(kind),
                        "duration_minutes": duration["minutes"],
                        "duration_source": duration["source"],
                        "duration_sample_size": duration["sample_size"],
                        "requires_evaluator": True,
                        "success_criteria": list(objective.get("success_criteria", [])),
                        "source_anchors": list(objective.get("source_anchors", [])),
                    },
                }
                target_candidates.append((target_index, item))

                # Once a required dimension is not independently verified,
                # later dimensions must wait.  Stale independent dimensions
                # remain candidates only when every required dimension is
                # otherwise independent.
                if verification_status != "independent":
                    target_candidates = [(target_index, item)]
                    break

            if target_candidates:
                target_index, selected = max(
                    target_candidates,
                    key=lambda value: (value[1]["priority_score"], -value[0]),
                )
                candidates.append((objective_index, target_index, selected))

        candidates.sort(
            key=lambda value: (
                -value[2]["priority_score"],
                value[0],
                value[1],
                value[2]["objective_id"],
            )
        )
        items = [value[2] for value in candidates[:max_items]]
        generated_at = as_of.isoformat(timespec="seconds")
        queue = {
            "schema_version": INTERVENTION_QUEUE_SCHEMA_VERSION,
            "project_id": self._project["project_id"],
            "policy_version": INTERVENTION_POLICY_VERSION,
            "generated_at": generated_at,
            "as_of": generated_at,
            "deadline": project_deadline.isoformat() if project_deadline else None,
            "days_to_deadline": days_to_deadline,
            "items": items,
            "evidence_attempt_ids": _unique(considered_evidence),
            "unscoped_attempt_ids": unscoped_attempt_ids,
            "deferred_objectives": deferred_objectives,
            "warnings": (
                [
                    "Some attempts were not attributed to a declared Objective and did not affect priority."
                ]
                if unscoped_attempt_ids
                else []
            )
            + (
                [
                    "Objectives not yet in scope were held back: "
                    + "; ".join(deferred_objectives)
                ]
                if deferred_objectives
                else []
            ),
        }
        day_plan = (
            build_day_plan(
                queue_items=items,
                schedules=list(schedules or []),
                attempts=attempts,
                project=self._project,
                target=as_of.date(),
                tzinfo=as_of.tzinfo,
                now=as_of,
                capacity=capacity_factor(adherence),
            )
            if items
            else None
        )
        return {
            "queue": queue,
            "day_plan": day_plan,
            "proposal": self._proposal(queue, day_plan) if items else None,
        }

    @staticmethod
    def fingerprint(
        *,
        project: dict[str, Any],
        items: list[dict[str, Any]],
        day_plan: dict[str, Any] | None = None,
    ) -> str:
        """Hash only semantic fields, excluding clocks and explanatory prose.

        The day plan contributes its target date and its placed events, not its
        derivation: two proposals for the same evidence but different days are
        genuinely different proposals and must not collide on ``proposal_id``,
        while re-deriving the same day from the same evidence must reproduce
        the same id so a re-run is recognised as a duplicate rather than a
        conflict.
        """

        semantic_items = [
            {
                "objective_id": item["objective_id"],
                "capability": item["capability"],
                "evidence_dimension": item["evidence_dimension"],
                "kind": item["kind"],
                "verification_status": item["reason_factors"]["verification_status"],
                "evidence_age_band": item["reason_factors"]["evidence_age_band"],
                "deadline_band": item["reason_factors"]["deadline_band"],
                "repeated_diagnosis_count": item["reason_factors"][
                    "repeated_diagnosis_count"
                ],
                "evidence_attempt_ids": item["evidence_attempt_ids"],
                "recommended_activity": item["recommended_activity"],
            }
            for item in items
        ]
        # A day plan contributes to identity only once it actually places
        # something. An empty plan schedules nothing, so letting its target
        # date into the digest would make the same unchanged queue produce a
        # new proposal every day -- the opposite of the idempotence that lets
        # a repeated save be recognised as a duplicate.
        placed = [
            {
                "id": event.get("id"),
                "start": event.get("start"),
                "end": event.get("end"),
                "source_intervention_id": event.get("source_intervention_id"),
            }
            for entry in (day_plan or {}).get("schedules") or []
            for event in entry.get("events") or []
        ]
        semantic_day_plan = (
            {"target_date": (day_plan or {}).get("target_date"), "events": placed}
            if placed
            else None
        )
        return _digest({
            "policy_version": INTERVENTION_POLICY_VERSION,
            "project_id": project["project_id"],
            "project_title": project.get("title"),
            "items": semantic_items,
            "day_plan": semantic_day_plan,
        })

    @staticmethod
    def _reasons(
        *,
        verification_status: str,
        target: str,
        age_days: int | None,
        threshold: int,
        deadline_band: str,
        days_to_deadline: int | None,
        repeated_cluster: dict[str, Any] | None,
    ) -> list[str]:
        if verification_status == "unobserved":
            reasons = [f"No evaluator-provenanced {target} evidence has been recorded."]
        elif verification_status == "developing":
            reasons = [
                f"Observed {target} evidence does not yet meet the success threshold."
            ]
        elif verification_status == "supported":
            reasons = [f"Successful {target} evidence is not independently verified."]
        else:
            reasons = [
                f"Independent {target} evidence is {age_days} days old, beyond the {threshold}-day freshness threshold."
            ]
        if repeated_cluster:
            reasons.append(
                f"{repeated_cluster.get('kind', 'diagnosis')} repeated "
                f"{int(repeated_cluster.get('count') or 0)} times."
            )
        if deadline_band in {"near", "critical", "overdue"}:
            reasons.append(
                "The project deadline is overdue."
                if deadline_band == "overdue"
                else f"Only {days_to_deadline} days remain before the project deadline."
            )
        return reasons

    def _proposal(
        self,
        queue: dict[str, Any],
        day_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        item_evidence = _unique([
            str(attempt_id)
            for item in queue["items"]
            for attempt_id in item.get("evidence_attempt_ids", [])
        ])
        fingerprint = self.fingerprint(
            project=self._project,
            items=queue["items"],
            day_plan=day_plan,
        )
        return {
            "schema_version": PLAN_PROPOSAL_SCHEMA_VERSION,
            "proposal_id": f"plan-{fingerprint[:20]}",
            "project_id": self._project["project_id"],
            "policy_version": INTERVENTION_POLICY_VERSION,
            "generation_fingerprint": fingerprint,
            "title": f"Next learning plan for {self._project.get('title', self._project['project_id'])}",
            "status": "proposed",
            "rationale": (
                "Derived from evidence gaps, independent verification, evidence freshness, and deadline; "
                "no Schedule change has been applied."
            ),
            "created_at": queue["generated_at"],
            "as_of": queue["as_of"],
            "items": deepcopy(queue["items"]),
            "day_plan": deepcopy(day_plan) if day_plan else None,
            "evidence_attempt_ids": item_evidence,
            "schedule_change": {
                "state": "not_applied",
                "requires_explicit_save": True,
            },
        }
