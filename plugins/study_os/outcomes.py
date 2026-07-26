"""Measure whether accepted Interventions actually moved the learner.

StudyOS holds itself to deriving competency from evidence, but it exempted its
own recommendations: it proposed an Intervention, the learner accepted it, and
nothing ever checked whether the capability improved afterwards.  The
recommender's scoring tables were therefore unfalsifiable -- hand-written
constants that no observation could correct.

This module closes that loop, and derives rather than stores.  The anchor is
already durable: an accepted Plan Proposal records ``decided_at`` plus the
verification status each Intervention was reasoning about at the time.
Everything after that is a comparison against attempts that arrived later, so
an outcome is recomputed from evidence like every other StudyOS judgment and
cannot drift out of sync with it.

Two distinctions carry the honesty of the result:

*Non-adherence is not failure.*  An accepted Intervention that produced no
subsequent evidence says nothing about whether the recommendation was good.
Folding those into a failure rate would calibrate away sound advice that was
simply never followed, so they are reported separately and excluded from the
rate entirely.

*A thin sample is not a signal.*  Aggregates over a handful of decisions are
noise, and presenting them as a verdict would repeat the mistake this module
exists to fix. Below :data:`MIN_OUTCOME_SAMPLE` the aggregate reports
``insufficient_evidence`` and no rate.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Callable

OUTCOME_SCHEMA_VERSION = "intervention_outcomes.v1"

# Fewer decisions than this cannot distinguish a working recommendation from a
# lucky one, so the aggregate withholds a rate rather than publishing noise.
MIN_OUTCOME_SAMPLE = 5

# Ordered weakest to strongest; comparison is by position, never by name.
STATUS_RANK = ("unobserved", "developing", "supported", "independent")

OUTCOMES = ("improved", "unchanged", "regressed", "not_attempted")


def _rank(status: Any) -> int:
    try:
        return STATUS_RANK.index(str(status))
    except ValueError:
        return 0


def _moment(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _attempts_for(
    attempts: list[dict[str, Any]],
    *,
    objective_id: str,
    dimension: str,
    after: datetime,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for attempt in attempts:
        if str(attempt.get("transfer_level") or "") != dimension:
            continue
        if objective_id not in {str(value) for value in attempt.get("objective_ids") or []}:
            continue
        occurred = _moment(attempt.get("occurred_at"))
        if occurred is None or occurred <= after:
            continue
        selected.append(attempt)
    return selected


def _classify(baseline: str, current: str, evidence_since: int) -> str:
    if evidence_since == 0:
        return "not_attempted"
    difference = _rank(current) - _rank(baseline)
    if difference > 0:
        return "improved"
    if difference < 0:
        return "regressed"
    return "unchanged"


def build_intervention_outcomes(
    *,
    proposals: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    diagnosis_builder: Callable[[list[dict[str, Any]]], dict[str, Any]],
    as_of: datetime,
) -> dict[str, Any]:
    """Compare each accepted Intervention's baseline against evidence since.

    Only accepted proposals are measured. A rejected one was never acted on,
    and a still-proposed one has not been decided, so neither can say anything
    about whether the recommendation would have worked.
    """

    outcomes: list[dict[str, Any]] = []
    for proposal in proposals:
        if proposal.get("status") != "accepted":
            continue
        decided_at = _moment((proposal.get("decision") or {}).get("decided_at"))
        if decided_at is None:
            continue
        for item in proposal.get("items") or []:
            objective_id = str(item.get("objective_id") or "")
            dimension = str(item.get("evidence_dimension") or "")
            if not objective_id or not dimension:
                continue
            baseline = str(
                (item.get("reason_factors") or {}).get("verification_status") or "unobserved"
            )
            since = _attempts_for(
                attempts,
                objective_id=objective_id,
                dimension=dimension,
                after=decided_at,
            )
            # The current status is judged over the whole history for that
            # objective, not only the evidence since: independence is a claim
            # about the capability, not about a time slice of it.
            scoped = [
                attempt
                for attempt in attempts
                if objective_id in {str(value) for value in attempt.get("objective_ids") or []}
            ]
            dimensions = diagnosis_builder(scoped).get("evidence_dimensions") or {}
            current = str(
                (dimensions.get(dimension) or {}).get("verification_status") or "unobserved"
            )
            outcomes.append(
                {
                    "proposal_id": str(proposal.get("proposal_id") or ""),
                    "intervention_id": str(item.get("intervention_id") or ""),
                    "objective_id": objective_id,
                    "evidence_dimension": dimension,
                    "kind": str(item.get("kind") or ""),
                    "decided_at": decided_at.isoformat(timespec="seconds"),
                    "days_since_decision": (as_of - decided_at).days,
                    "verification_status_at_decision": baseline,
                    "verification_status_now": current,
                    "evidence_attempt_ids_since": [
                        str(attempt.get("attempt_id")) for attempt in since
                    ],
                    "outcome": _classify(baseline, current, len(since)),
                }
            )

    outcomes.sort(key=lambda item: (item["decided_at"], item["intervention_id"]))
    return {
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "as_of": as_of.isoformat(timespec="seconds"),
        "outcomes": outcomes,
        "by_kind": _aggregate(outcomes),
        "totals": _tally(outcomes),
    }


def _tally(outcomes: list[dict[str, Any]]) -> dict[str, int]:
    counts = {name: 0 for name in OUTCOMES}
    for outcome in outcomes:
        counts[outcome["outcome"]] = counts.get(outcome["outcome"], 0) + 1
    counts["decided"] = len(outcomes)
    return counts


def _aggregate(outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-kind effectiveness, with the sample size it rests on.

    ``improvement_rate`` deliberately excludes ``not_attempted`` from both
    numerator and denominator: the question is whether the Intervention worked
    when the learner acted on it, and mixing adherence into effectiveness makes
    both unreadable. ``adherence_rate`` reports the other half separately.
    """

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for outcome in outcomes:
        grouped[outcome["kind"]].append(outcome)

    rows: list[dict[str, Any]] = []
    for kind in sorted(grouped):
        items = grouped[kind]
        counts = {name: sum(1 for item in items if item["outcome"] == name) for name in OUTCOMES}
        acted = len(items) - counts["not_attempted"]
        row: dict[str, Any] = {
            "kind": kind,
            "decided": len(items),
            "acted_on": acted,
            **counts,
            "adherence_rate": round(acted / len(items), 3) if items else None,
        }
        if acted >= MIN_OUTCOME_SAMPLE:
            row["improvement_rate"] = round(counts["improved"] / acted, 3)
            row["verdict"] = "measured"
        else:
            row["improvement_rate"] = None
            row["verdict"] = "insufficient_evidence"
            row["needed_for_signal"] = MIN_OUTCOME_SAMPLE - acted
        rows.append(row)
    return rows
