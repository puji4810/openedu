"""Deep StudyOS model interface backed by immutable learning evidence.

Legacy handlers remain available to desktop and Python callers.  The plugin
registers only the two schemas in this module for model use.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from plugins.study_os.contract_models import (
    SCHEDULE_ID_PATTERN,
    SOURCE_ANCHOR_KINDS,
    study_project_id_json_schema,
)
from plugins.study_os.activities import activity_adapter_for
from plugins.study_os import tools as legacy
from plugins.study_os.adherence import DEFAULT_LOOKBACK_DAYS, build_plan_adherence
from plugins.study_os.calibration import (
    OUTCOME_LOOKBACK_DAYS,
    capacity_factor,
    outcome_adjustment,
)
from plugins.study_os.day_plan import active_phase
from plugins.study_os.interventions import InterventionOrchestrator, parse_as_of
from plugins.study_os.outcomes import build_intervention_outcomes
from plugins.study_os.notes import StudyNoteCatalog
from plugins.study_os.schemas import (
    ASSISTANCE_LEVELS,
    ATTEMPT_RESULTS,
    ATTEMPT_SCHEMA_VERSION,
    DIAGNOSIS_OBJECT_EXAMPLE,
    DIAGNOSIS_REQUIRED_FIELDS,
    EVIDENCE_DIMENSIONS,
    EVALUATOR_KINDS,
    INTERVENTION_POLICY_VERSION,
    LEARNING_MODES,
    PATTERN_PROPOSAL_SCHEMA_VERSION,
    PLAN_PROPOSAL_SCHEMA_VERSION,
    PLAN_PROPOSAL_STATUSES,
    PROJECT_SCHEMA_VERSION,
    validate_plan_proposal,
    validate_pattern_proposal,
    validate_study_attempt,
)
from plugins.study_os.runtime import LearningRuntime, LearningRuntimeError


EVIDENCE_DIMENSION_ORDER = EVIDENCE_DIMENSIONS

ANSWER_HEADING_RE = re.compile(
    r"^#{1,6}\s*(?:答案|解析|解答|参考答案|solution|answer)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _payload(args: dict[str, Any]) -> dict[str, Any]:
    data = args.get("data")
    result = dict(data) if isinstance(data, dict) else {}
    for key in ("vault_path", "project_id"):
        if args.get(key) is not None:
            result[key] = args[key]
    return result


def _project(vault: Path, project_id: Any = None) -> dict[str, Any]:
    return legacy._read_project_manifest(vault, project_id)


def _activity_dir(vault: Path, project_id: str) -> Path:
    path = legacy._project_dir(vault, project_id) / "activity"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _attempt_path(vault: Path, project_id: str, occurred_at: str) -> Path:
    month = occurred_at[:7]
    return _activity_dir(vault, project_id) / f"attempts-{month}.jsonl"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid activity JSON at {path.name}:{line_number}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"Activity record at {path.name}:{line_number} must be an object")
        records.append(value)
    return records


def _all_attempts(vault: Path, project_id: str) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    resolved_project_id = legacy._validate_project_id(project_id)
    root = (
        vault / ".StudyOS" / "projects" / resolved_project_id / "activity"
    ).resolve()
    try:
        root.relative_to(vault)
    except ValueError as exc:
        raise ValueError("Activity path escapes Vault") from exc
    if not root.exists():
        return attempts
    for path in sorted(root.glob("attempts-*.jsonl")):
        attempts.extend(_read_jsonl(path))
    attempts.sort(key=lambda item: (str(item.get("occurred_at", "")), str(item.get("attempt_id", ""))))
    return attempts


def _completed_reviews_for_local_date(
    attempts: list[dict[str, Any]],
    *,
    timezone_name: str,
    occurred_at: str,
) -> int:
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"project timezone is not a valid IANA timezone: {timezone_name}") from exc
    try:
        target = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("occurred_at must be a valid ISO datetime") from exc
    if target.tzinfo is None or target.utcoffset() is None:
        raise ValueError("occurred_at must include a timezone offset")
    target_date = target.astimezone(timezone).date()
    completed = 0
    for attempt in attempts:
        value = attempt.get("occurred_at")
        if attempt.get("activity_kind") != "review" or not isinstance(value, str):
            continue
        try:
            resolved = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if (
            resolved.tzinfo is not None
            and resolved.utcoffset() is not None
            and resolved.astimezone(timezone).date() == target_date
        ):
            completed += 1
    return completed


def _filtered_attempts(vault: Path, project_id: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
    attempts = _all_attempts(vault, project_id)
    concept = str(filters.get("concept") or "").casefold()
    pattern = str(filters.get("pattern") or "").casefold()
    result = str(filters.get("result") or "")
    item_id = str(filters.get("item_id") or "")
    session_id = str(filters.get("session_id") or "")
    attempt_ids = {str(value) for value in filters.get("attempt_ids", [])} if isinstance(filters.get("attempt_ids"), list) else set()
    start = str(filters.get("start_date") or "")
    end = str(filters.get("end_date") or "")

    def matches(attempt: dict[str, Any]) -> bool:
        occurred = str(attempt.get("occurred_at") or "")[:10]
        return not (
            (concept and concept not in {str(value).casefold() for value in attempt.get("concepts", [])})
            or (pattern and pattern not in {str(value).casefold() for value in attempt.get("patterns", [])})
            or (result and attempt.get("result") != result)
            or (item_id and attempt.get("item_id") != item_id)
            or (session_id and attempt.get("session_id") != session_id)
            or (attempt_ids and attempt.get("attempt_id") not in attempt_ids)
            or (start and occurred < start)
            or (end and occurred > end)
        )

    return [attempt for attempt in attempts if matches(attempt)]


def _record_attempt(args: dict[str, Any]) -> str:
    vault = legacy.resolve_vault_path(args.get("vault_path"))
    project = _project(vault, args.get("project_id"))
    result = str(args.get("result") or "").strip()
    default_score = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0, "abandoned": 0.0}.get(result)
    score = args.get("score")
    if score is None:
        score = default_score
    occurred_at = str(args.get("occurred_at") or datetime.now().astimezone().isoformat(timespec="seconds"))
    attempt = {
        "schema_version": ATTEMPT_SCHEMA_VERSION,
        "attempt_id": str(args.get("attempt_id") or f"att-{uuid4().hex[:16]}").strip(),
        "project_id": project["project_id"],
        "item_id": str(args.get("item_id") or "").strip(),
        "occurred_at": occurred_at,
        "response": str(args.get("response") or "").strip(),
        "result": result,
        "score": score,
        "duration_seconds": args.get("duration_seconds"),
        "hints_used": args.get("hints_used", 0),
        "evaluator_confidence": args.get("evaluator_confidence"),
        "evaluator": args.get("evaluator"),
        "assistance": args.get("assistance"),
        "transfer_level": args.get("transfer_level"),
        "concepts": args.get("concepts", []),
        "patterns": args.get("patterns", []),
        "objective_ids": args.get("objective_ids", []),
        "diagnoses": args.get("diagnoses", []),
        "source_anchors": args.get("source_anchors"),
        "artifact_refs": args.get("artifact_refs"),
        "activity_kind": args.get("activity_kind"),
        "source": args.get("source"),
        "session_id": args.get("session_id"),
        "revision_of": args.get("revision_of"),
    }
    attempt = {key: value for key, value in attempt.items() if value is not None}
    ok, validated = validate_study_attempt(attempt)
    if not ok:
        return legacy._err("VALIDATION_FAILED", "; ".join(validated), {"errors": validated})
    if any(existing.get("attempt_id") == attempt["attempt_id"] for existing in _all_attempts(vault, project["project_id"])):
        return legacy._err("ATTEMPT_EXISTS", f"Attempt already exists: {attempt['attempt_id']}")
    path = _attempt_path(vault, project["project_id"], occurred_at)
    legacy._append_text(path, json.dumps(validated, ensure_ascii=False) + "\n")
    return legacy._ok({"attempt": validated, "path": path.relative_to(vault).as_posix()})


def _remove_attempt(vault: Path, relative_path: str, attempt_id: str) -> None:
    """Remove one just-appended attempt while rolling back a compound write."""
    path = legacy._safe_relative_path(vault, relative_path)
    if not path.exists():
        return
    kept = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("attempt_id") != attempt_id
    ]
    if kept:
        legacy._write_text(path, "\n".join(kept) + "\n")
    else:
        path.unlink()


def handle_study_review_detail(args: dict[str, Any], **_kwargs: Any) -> str:
    """Return one example split into prompt and initially-hidden answer."""
    try:
        vault = legacy.resolve_vault_path(args.get("vault_path"))
        note_ref = str(args.get("note") or args.get("path") or "").strip()
        path, matches = legacy._find_note(vault, note_ref)
        if matches:
            return legacy._err(
                "NOTE_AMBIGUOUS",
                f"More than one note matched {note_ref!r}",
                {"matches": [item.relative_to(vault).as_posix() for item in matches[:20]]},
            )
        if path is None:
            return legacy._err("NOTE_NOT_FOUND", f"Note not found: {note_ref}")
        note, warnings = legacy.parse_note(path, vault, include_body=True)
        if note.get("layer") != "example":
            return legacy._err("NOT_REVIEW_ITEM", "Only example notes can be opened in the review runner")
        body = str(note.pop("body", ""))
        match = ANSWER_HEADING_RE.search(body)
        prompt = body[: match.start()].strip() if match else body.strip()
        answer = body[match.start() :].strip() if match else None
        return legacy._ok(
            {
                "item": note,
                "prompt_markdown": prompt,
                "answer_markdown": answer,
                "has_answer": answer is not None,
            },
            warnings,
        )
    except Exception as exc:
        return legacy._err("REVIEW_DETAIL_FAILED", str(exc))


def handle_study_review_submission(args: dict[str, Any], **_kwargs: Any) -> str:
    """Atomically record evidence and advance one spaced-repetition item."""
    vault: Path | None = None
    note_path: Path | None = None
    original_note: str | None = None
    attempt_path: str | None = None
    attempt_id: str | None = None
    try:
        vault = legacy.resolve_vault_path(args.get("vault_path"))
        project = _project(vault, args.get("project_id"))
        note_ref = str(args.get("note") or "").strip()
        note_path, matches = legacy._find_note(vault, note_ref)
        if matches:
            return legacy._err("NOTE_AMBIGUOUS", f"More than one note matched {note_ref!r}")
        if note_path is None:
            return legacy._err("NOTE_NOT_FOUND", f"Note not found: {note_ref}")
        note, warnings = legacy.parse_note(note_path, vault, include_body=False)
        if note.get("layer") != "example":
            return legacy._err("NOT_REVIEW_ITEM", "Only example notes can be submitted for review")

        result = str(args.get("result") or "").strip()
        if result not in {"correct", "partial", "incorrect"}:
            return legacy._err("VALIDATION_FAILED", "result must be correct, partial, or incorrect")
        duration = args.get("duration_seconds")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration < 0:
            return legacy._err("VALIDATION_FAILED", "duration_seconds must be a non-negative integer")
        occurred_at = str(args.get("occurred_at") or datetime.now().astimezone().isoformat(timespec="seconds"))
        attempt_args = {
            "vault_path": str(vault),
            "project_id": project["project_id"],
            "attempt_id": args.get("attempt_id"),
            "item_id": note["path"],
            "occurred_at": occurred_at,
            "response": args.get("response"),
            "result": result,
            "score": {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}[result],
            "duration_seconds": duration,
            "hints_used": args.get("hints_used", 0),
            "evaluator": args.get("evaluator"),
            "assistance": args.get("assistance"),
            "transfer_level": args.get("transfer_level", "execution"),
            "concepts": note.get("concepts", []),
            "patterns": note.get("patterns", []),
            "objective_ids": args.get("objective_ids", []),
            "diagnoses": args.get("diagnoses", []),
            "source_anchors": args.get("source_anchors"),
            "artifact_refs": args.get("artifact_refs"),
            "activity_kind": args.get("activity_kind", "review"),
            "source": note["path"],
            "session_id": args.get("session_id"),
        }
        original_note = note_path.read_text(encoding="utf-8")
        attempt_result = json.loads(_record_attempt(attempt_args))
        if not attempt_result.get("ok"):
            return json.dumps(attempt_result, ensure_ascii=False)
        attempt = attempt_result["data"]["attempt"]
        attempt_id = str(attempt["attempt_id"])
        attempt_path = str(attempt_result["data"]["path"])

        review_result = json.loads(
            legacy.handle_study_record_review(
                {
                    "vault_path": str(vault),
                    "note": note["path"],
                    "result": result,
                    "log_error": False,
                    "detail": args.get("detail"),
                }
            )
        )
        if not review_result.get("ok"):
            legacy._write_text(note_path, original_note)
            _remove_attempt(vault, attempt_path, attempt_id)
            return legacy._err("REVIEW_SUBMISSION_FAILED", review_result.get("error", {}).get("message", "Review update failed"))
        completed_today = _completed_reviews_for_local_date(
            _all_attempts(vault, project["project_id"]),
            timezone_name=str(project["timezone"]),
            occurred_at=occurred_at,
        )
        return legacy._ok(
            {
                "attempt": attempt,
                "review": review_result["data"],
                "completed_today_increment": 1,
                "completed_today": completed_today,
            },
            warnings + review_result.get("warnings", []),
        )
    except Exception as exc:
        if vault is not None and note_path is not None and original_note is not None:
            try:
                legacy._write_text(note_path, original_note)
                if attempt_path and attempt_id:
                    _remove_attempt(vault, attempt_path, attempt_id)
            except Exception:
                pass
        return legacy._err("REVIEW_SUBMISSION_FAILED", str(exc))


def _attempt_activity(action: str, args: dict[str, Any]) -> str:
    if action == "record":
        return _record_attempt(args)
    vault = legacy.resolve_vault_path(args.get("vault_path"))
    project = _project(vault, args.get("project_id"))
    if action == "list":
        limit = max(1, min(int(args.get("limit", 100)), 500))
        attempts = _filtered_attempts(vault, project["project_id"], args)
        return legacy._ok({"project_id": project["project_id"], "count": len(attempts), "attempts": attempts[-limit:]})
    if action == "read":
        attempt_id = str(args.get("attempt_id") or "").strip()
        for attempt in _all_attempts(vault, project["project_id"]):
            if attempt.get("attempt_id") == attempt_id:
                return legacy._ok({"attempt": attempt})
        return legacy._err("ATTEMPT_NOT_FOUND", f"Attempt not found: {attempt_id}")
    return legacy._err("INVALID_ACTION", f"Unsupported attempt action: {action}")


def _proposal_dir(vault: Path, project_id: str) -> Path:
    path = legacy._project_dir(vault, project_id) / "pattern-proposals"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _proposal_activity(action: str, args: dict[str, Any]) -> str:
    vault = legacy.resolve_vault_path(args.get("vault_path"))
    project = _project(vault, args.get("project_id"))
    root = _proposal_dir(vault, project["project_id"])
    if action == "save":
        proposal = dict(args.get("proposal") or {})
        proposal.setdefault("schema_version", PATTERN_PROPOSAL_SCHEMA_VERSION)
        proposal.setdefault("project_id", project["project_id"])
        proposal.setdefault("status", "candidate")
        proposal.setdefault("created_at", datetime.now().astimezone().isoformat(timespec="seconds"))
        ok, validated = validate_pattern_proposal(proposal)
        if not ok:
            validation_errors = validated if isinstance(validated, list) else ["Invalid pattern proposal"]
            return legacy._err("VALIDATION_FAILED", "; ".join(validation_errors), {"errors": validation_errors})
        if not isinstance(validated, dict):
            return legacy._err("VALIDATION_FAILED", "Pattern proposal validator returned invalid data")
        known_ids = {attempt.get("attempt_id") for attempt in _all_attempts(vault, project["project_id"])}
        missing = [item for item in validated["evidence_attempt_ids"] if item not in known_ids]
        if missing:
            return legacy._err("EVIDENCE_NOT_FOUND", "Proposal references unknown attempts", {"attempt_ids": missing})
        proposal_id = legacy._validate_schedule_id(validated["proposal_id"])
        path = root / f"{proposal_id}.json"
        if path.exists():
            return legacy._err("PROPOSAL_EXISTS", f"Pattern proposal already exists: {proposal_id}")
        legacy._write_text(path, json.dumps(validated, ensure_ascii=False, indent=2) + "\n")
        return legacy._ok({"proposal": validated, "path": path.relative_to(vault).as_posix()})
    if action == "list":
        proposals = [legacy._read_json_file(path) for path in sorted(root.glob("*.json"))]
        return legacy._ok({"project_id": project["project_id"], "proposals": proposals})
    if action == "read":
        proposal_id = legacy._validate_schedule_id(args.get("proposal_id"))
        path = root / f"{proposal_id}.json"
        if not path.exists():
            return legacy._err("PROPOSAL_NOT_FOUND", f"Pattern proposal not found: {proposal_id}")
        return legacy._ok({"proposal": legacy._read_json_file(path)})
    return legacy._err("INVALID_ACTION", f"Unsupported pattern_proposal action: {action}")


def _plan_proposal_dir(vault: Path, project_id: str, *, create: bool = True) -> Path:
    """Where a project's Plan Proposals live.

    ``create=False`` for the read-only paths.  Deriving a proposal must leave
    no trace until the learner saves one, and a directory appearing on disk is
    a trace: the tests hold ``propose_plan`` to exactly that.
    """

    path = legacy._project_dir(vault, project_id) / "plan-proposals"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _validated_plan_proposal(path: Path) -> dict[str, Any]:
    proposal = legacy._read_json_file(path)
    ok, validated = validate_plan_proposal(proposal)
    if not ok:
        validation_errors = validated if isinstance(validated, list) else ["Invalid Plan Proposal"]
        raise ValueError(f"Invalid Plan Proposal {path.name}: {'; '.join(validation_errors)}")
    if not isinstance(validated, dict):
        raise ValueError("Plan Proposal validator returned invalid data")
    return validated


def _ensure_today_proposal(
    vault: Path,
    project: dict[str, Any],
    args: dict[str, Any],
    root: Path,
) -> str:
    """Return today's Plan Proposal, deriving and persisting it exactly once.

    This is the seam both entry points share.  The desktop calls it when the
    learner first opens StudyOS for the day and a cron job may call it earlier
    as a nudge; whichever runs first persists, and the other one reads what is
    already there.  Idempotence is by existence-of-a-proposed-plan-for-today
    rather than by fingerprint: the day plan is anchored to the current time,
    so re-deriving at 21:30 what was derived at 09:00 legitimately produces
    different events and a different id, and without this check the learner
    would collect a new proposal every time they opened the app.

    A day already decided -- accepted or rejected -- is not regenerated.  The
    learner has answered for today; proposing again would be nagging, not
    planning.
    """

    as_of = parse_as_of(args.get("as_of"))
    target = as_of.date().isoformat()
    existing = [
        proposal
        for proposal in (_validated_plan_proposal(path) for path in sorted(root.glob("*.json")))
        if (proposal.get("day_plan") or {}).get("target_date") == target
    ]
    decided = [item for item in existing if item.get("status") != "proposed"]
    pending = [item for item in existing if item.get("status") == "proposed"]
    if pending:
        return legacy._ok(
            {
                "project_id": project["project_id"],
                "proposal": pending[0],
                "created": False,
                "reason": "a proposed plan already exists for this date",
            }
        )
    if decided:
        return legacy._ok(
            {
                "project_id": project["project_id"],
                "proposal": decided[0],
                "created": False,
                "reason": f"this date was already {decided[0].get('status')}",
            }
        )

    orchestration = _intervention_orchestration(vault, project, dict(args))
    proposal = orchestration.get("proposal")
    if not proposal:
        return legacy._ok(
            {
                "project_id": project["project_id"],
                "proposal": None,
                "created": False,
                "reason": "the Intervention Queue is empty, so there is nothing to plan",
            }
        )
    saved = json.loads(
        _plan_proposal_activity(
            "save",
            {
                "vault_path": str(vault),
                "project_id": project["project_id"],
                "proposal": proposal,
            },
        )
    )
    if not saved.get("ok"):
        return json.dumps(saved, ensure_ascii=False)
    return legacy._ok(
        {
            "project_id": project["project_id"],
            "proposal": saved["data"]["proposal"],
            "created": True,
            "reason": "derived from current evidence",
        }
    )


def _events_only_change(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """True when two Schedules differ in nothing but their ``events``.

    Asserted rather than assumed. Applying a day plan is the one path that
    writes a Schedule without the learner authoring it, so the guarantee that
    it cannot touch phases, range, or title is worth enforcing structurally --
    a future refactor that widened the merge would otherwise be silent.
    """

    return {key: value for key, value in before.items() if key != "events"} == {
        key: value for key, value in after.items() if key != "events"
    }


def _apply_plan_proposal(
    vault: Path,
    project: dict[str, Any],
    args: dict[str, Any],
    root: Path,
) -> str:
    """Write an accepted proposal's day plan into its Schedules as events.

    This is the third act of ADR-0003's derive / decide / apply, collapsed
    from "read the Schedule, merge, validate, save" into one call for the
    narrow case it can be made safe for: appending dated events that the
    proposal already contains. It refuses anything else. Phases are never
    touched, so accepting a day never silently rewrites the long-term plan.

    Idempotent by event id: the day plan derives deterministic ids, so
    re-applying replaces rather than duplicates.
    """

    proposal_id = legacy._validate_schedule_id(args.get("proposal_id"))
    path = root / f"{proposal_id}.json"
    if not path.exists():
        return legacy._err("PROPOSAL_NOT_FOUND", f"Plan Proposal not found: {proposal_id}")
    proposal = _validated_plan_proposal(path)
    if proposal.get("status") != "accepted":
        return legacy._err(
            "PROPOSAL_NOT_ACCEPTED",
            f"Only an accepted Plan Proposal may be applied; {proposal_id} is {proposal.get('status')}",
        )

    day_plan = proposal.get("day_plan") or {}
    entries = [entry for entry in (day_plan.get("schedules") or []) if entry.get("events")]
    if not entries:
        return legacy._err(
            "NOTHING_TO_APPLY",
            "This Plan Proposal carries no day-plan events to write",
        )
    target = str(day_plan.get("target_date") or "")
    try:
        target_date = date.fromisoformat(target)
    except ValueError:
        return legacy._err("VALIDATION_FAILED", "day_plan.target_date must be an ISO date")

    applied: list[dict[str, Any]] = []
    for entry in entries:
        schedule_id = str(entry.get("schedule_id") or "")
        schedule_path = legacy._schedule_path(vault, project["project_id"], schedule_id)
        if not schedule_path.exists():
            return legacy._err(
                "SCHEDULE_NOT_FOUND",
                f"Plan Proposal targets a Schedule that no longer exists: {schedule_id}",
            )
        before = json.loads(schedule_path.read_text(encoding="utf-8"))
        phase = active_phase(before, target_date)
        if phase is None or str(phase.get("id")) != str(entry.get("phase_id")):
            # The Schedule moved on since the plan was derived; writing the
            # old events would attach them to a phase that no longer governs
            # that day.
            return legacy._err(
                "PHASE_DRIFTED",
                (
                    f"Schedule {schedule_id} no longer has phase {entry.get('phase_id')} "
                    f"covering {target}; re-derive the plan"
                ),
            )

        merged = {
            str(event.get("id")): event
            for event in (before.get("events") or [])
            if isinstance(event, dict)
        }
        for event in entry["events"]:
            merged[str(event["id"])] = {
                **event,
                "source_plan_proposal_id": proposal_id,
            }
        after = {
            **before,
            "events": sorted(merged.values(), key=lambda item: (str(item.get("start")), str(item.get("id")))),
        }
        if not _events_only_change(before, after):
            return legacy._err(
                "APPLY_WOULD_CHANGE_MORE_THAN_EVENTS",
                f"Refusing to write {schedule_id}: applying a day plan may only add events",
            )
        ok, validated = legacy._validate_schedule_for_project(after, project)
        if not ok:
            errors = validated if isinstance(validated, list) else ["Invalid Schedule"]
            return legacy._err("VALIDATION_FAILED", "; ".join(errors), {"errors": errors})
        legacy._write_text(schedule_path, legacy._json(validated))
        applied.append(
            {
                "schedule_id": schedule_id,
                "path": schedule_path.relative_to(vault).as_posix(),
                "events_written": len(entry["events"]),
                "events_total": len(validated["events"]),
            }
        )

    return legacy._ok(
        {
            "project_id": project["project_id"],
            "proposal_id": proposal_id,
            "target_date": target,
            "applied": applied,
            "schedule_mutated": True,
            "scope_policy": (
                "Only events were written. Phases, range, and title are untouched, so "
                "an applied day never rewrites the long-term plan."
            ),
        }
    )


def _plan_proposal_activity(action: str, args: dict[str, Any]) -> str:
    vault = legacy.resolve_vault_path(args.get("vault_path"))
    project = _project(vault, args.get("project_id"))
    root = _plan_proposal_dir(vault, project["project_id"])

    if action == "save":
        proposal = dict(args.get("proposal") or {})
        proposal.setdefault("schema_version", PLAN_PROPOSAL_SCHEMA_VERSION)
        proposal.setdefault("policy_version", INTERVENTION_POLICY_VERSION)
        proposal.setdefault("project_id", project["project_id"])
        proposal.setdefault("status", "proposed")
        if proposal.get("status") != "proposed":
            return legacy._err(
                "INVALID_PROPOSAL_TRANSITION",
                "plan_proposal.save only creates proposed items; use accept or reject for decisions",
            )
        ok, validated = validate_plan_proposal(proposal)
        if not ok:
            validation_errors = validated if isinstance(validated, list) else ["Invalid Plan Proposal"]
            return legacy._err(
                "VALIDATION_FAILED",
                "; ".join(validation_errors),
                {"errors": validation_errors},
            )
        if not isinstance(validated, dict):
            return legacy._err("VALIDATION_FAILED", "Plan Proposal validator returned invalid data")
        if validated["project_id"] != project["project_id"]:
            return legacy._err("VALIDATION_FAILED", "proposal project_id must match project manifest")
        expected_fingerprint = InterventionOrchestrator.fingerprint(
            project=project,
            items=validated["items"],
            day_plan=validated.get("day_plan"),
        )
        expected_proposal_id = f"plan-{expected_fingerprint[:20]}"
        if (
            validated["generation_fingerprint"] != expected_fingerprint
            or validated["proposal_id"] != expected_proposal_id
        ):
            return legacy._err(
                "PROPOSAL_FINGERPRINT_MISMATCH",
                "Plan Proposal id or fingerprint does not match its semantic content",
            )

        if project.get("schema_version") == PROJECT_SCHEMA_VERSION:
            known_objectives = {
                str(item.get("objective_id"))
                for item in project.get("objectives", [])
                if isinstance(item, dict)
            }
            unknown_objectives = sorted(
                {
                    str(item.get("objective_id"))
                    for item in validated["items"]
                    if item.get("objective_id") not in known_objectives
                }
            )
            if unknown_objectives:
                return legacy._err(
                    "OBJECTIVE_NOT_FOUND",
                    "Proposal references unknown Objectives",
                    {"objective_ids": unknown_objectives},
                )

        known_attempt_ids = {
            str(attempt.get("attempt_id"))
            for attempt in _all_attempts(vault, project["project_id"])
        }
        missing_attempt_ids = [
            attempt_id
            for attempt_id in validated["evidence_attempt_ids"]
            if attempt_id not in known_attempt_ids
        ]
        if missing_attempt_ids:
            return legacy._err(
                "EVIDENCE_NOT_FOUND",
                "Proposal references unknown attempts",
                {"attempt_ids": missing_attempt_ids},
            )

        proposal_id = legacy._validate_schedule_id(validated["proposal_id"])
        path = root / f"{proposal_id}.json"
        if path.exists():
            existing = _validated_plan_proposal(path)
            if existing.get("generation_fingerprint") != validated["generation_fingerprint"]:
                return legacy._err(
                    "PROPOSAL_CONFLICT",
                    f"Plan Proposal id is already used by different content: {proposal_id}",
                )
            return legacy._ok(
                {
                    "proposal": existing,
                    "path": path.relative_to(vault).as_posix(),
                    "created": False,
                }
            )
        legacy._write_text(path, json.dumps(validated, ensure_ascii=False, indent=2) + "\n")
        return legacy._ok(
            {
                "proposal": validated,
                "path": path.relative_to(vault).as_posix(),
                "created": True,
            }
        )

    if action == "list":
        status = str(args.get("status") or "").strip()
        if status and status not in PLAN_PROPOSAL_STATUSES:
            return legacy._err(
                "VALIDATION_FAILED",
                "status must be proposed, accepted, or rejected",
            )
        proposals = [
            _validated_plan_proposal(path)
            for path in sorted(root.glob("*.json"))
        ]
        if status:
            proposals = [proposal for proposal in proposals if proposal.get("status") == status]
        return legacy._ok({"project_id": project["project_id"], "proposals": proposals})

    if action == "ensure_today":
        return _ensure_today_proposal(vault, project, args, root)

    if action == "apply":
        return _apply_plan_proposal(vault, project, args, root)

    if action not in {"read", "accept", "reject"}:
        return legacy._err(
            "INVALID_ACTION",
            f"Unsupported plan_proposal action: {action}",
        )
    proposal_id = legacy._validate_schedule_id(args.get("proposal_id"))
    path = root / f"{proposal_id}.json"
    if not path.exists():
        return legacy._err(
            "PROPOSAL_NOT_FOUND",
            f"Plan Proposal not found: {proposal_id}",
        )
    proposal = _validated_plan_proposal(path)
    if action == "read":
        return legacy._ok({"proposal": proposal})

    target_status = "accepted" if action == "accept" else "rejected"
    if proposal["status"] == target_status:
        return legacy._ok(
            {
                "proposal": proposal,
                "path": path.relative_to(vault).as_posix(),
                "changed": False,
                "schedule_mutated": False,
            }
        )
    if proposal["status"] != "proposed":
        return legacy._err(
            "INVALID_PROPOSAL_TRANSITION",
            f"Cannot {action} a Plan Proposal with status {proposal['status']}",
        )

    decided_at = str(
        args.get("decided_at")
        or datetime.now().astimezone().isoformat(timespec="seconds")
    )
    decision: dict[str, Any] = {
        "outcome": target_status,
        "decided_at": decided_at,
    }
    note = str(args.get("decision_note") or "").strip()
    if note:
        decision["note"] = note
    updated = {**proposal, "status": target_status, "decision": decision}
    ok, validated = validate_plan_proposal(updated)
    if not ok:
        validation_errors = validated if isinstance(validated, list) else ["Invalid intervention decision"]
        return legacy._err(
            "VALIDATION_FAILED",
            "; ".join(validation_errors),
            {"errors": validation_errors},
        )
    if not isinstance(validated, dict):
        return legacy._err("VALIDATION_FAILED", "Plan Proposal validator returned invalid data")
    legacy._write_text(path, json.dumps(validated, ensure_ascii=False, indent=2) + "\n")
    return legacy._ok(
        {
            "proposal": validated,
            "path": path.relative_to(vault).as_posix(),
            "changed": True,
            "schedule_mutated": False,
            "schedule_policy": (
                "Acceptance records the learner decision only. Call plan_proposal.apply to write this "
                "plan's events into their Schedules; it writes events and nothing else. A change to "
                "phases or range remains a separate schedule.validate then schedule.save."
            ),
        }
    )


_RESOURCE_HANDLERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "project": legacy.handle_study_project,
    "learning_record": legacy.handle_study_learning_record,
    "decision": legacy.handle_study_decision,
    "lesson": legacy.handle_study_lesson,
    "prompt_context": legacy.handle_study_prompt_context,
    "session": legacy.handle_study_log_session,
    "memory": legacy.handle_study_sync_memory,
}


def _schedule_request(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Adapt the public single-data envelope to the legacy schedule handler."""

    request = dict(payload)
    request["action"] = action
    if action not in {"validate", "save"}:
        return request

    nested_schedule = payload.get("schedule")
    nested_data = payload.get("data")
    if isinstance(nested_schedule, dict):
        schedule = dict(nested_schedule)
    elif isinstance(nested_data, dict):
        schedule = dict(nested_data)
    else:
        schedule = {
            key: value
            for key, value in payload.items()
            if key not in {"action", "data", "schedule", "vault_path"}
        }

    request = {"action": action, "data": schedule}
    for key in ("vault_path", "project_id"):
        if payload.get(key) is not None:
            request[key] = payload[key]
    return request


def _dispatch_resource(resource: str, action: str, data: dict[str, Any]) -> str:
    if resource == "schedule":
        return legacy.handle_study_schedule(_schedule_request(action, data))
    if resource in _RESOURCE_HANDLERS:
        if resource not in {"session", "memory"}:
            data.setdefault("action", action)
        return _RESOURCE_HANDLERS[resource](data)
    if resource == "note":
        if action in {"audit", "graph", "validate", "save"}:
            return _note_activity(action, data)
        handler = {
            "list": legacy.handle_study_list_notes,
            "read": legacy.handle_study_read_note,
            "extract": legacy.handle_study_extract_concepts,
        }.get(action)
    elif resource == "review":
        handler = {
            "due": legacy.handle_study_due_reviews,
            "record": legacy.handle_study_record_review,
            "submit": handle_study_review_submission,
            "create_task": legacy.handle_study_create_review_task,
            "stats": legacy.handle_study_review_stats,
            "weekly_report": legacy.handle_study_generate_weekly_report,
            "export_anki": legacy.handle_study_export_anki_candidates,
        }.get(action)
    elif resource == "error":
        handler = legacy.handle_study_log_error if action == "record" else None
    elif resource == "concept":
        handler = {
            "graph": legacy.handle_study_concept_graph,
            "queue": legacy.handle_study_learning_queue,
            "update_state": legacy.handle_study_update_concept_state,
        }.get(action)
    elif resource == "curriculum":
        handler = {
            "create": legacy.handle_study_create_curriculum,
            "list": legacy.handle_study_list_curricula,
            "import_plan": legacy.handle_study_import_plan,
            "progress": legacy.handle_study_plan_progress,
        }.get(action)
    else:
        handler = None
    if handler is None:
        return legacy._err("INVALID_RESOURCE_ACTION", f"Unsupported StudyOS operation: {resource}.{action}")
    return handler(data)


def _note_activity(action: str, data: dict[str, Any]) -> str:
    vault = legacy.resolve_vault_path(data.get("vault_path"))
    catalog = StudyNoteCatalog(vault)
    if action in {"audit", "graph"}:
        raw_roots = data.get("roots")
        roots = (
            [str(item) for item in raw_roots if str(item).strip()]
            if isinstance(raw_roots, list)
            else None
        )
        graph = catalog.wikilink_graph(roots=roots)
        return legacy._ok(
            {
                "vault_path": str(vault),
                "graph": graph,
                "broken_link_count": len(graph["missing"]),
                "broken_links": graph["missing"],
            },
            graph["warnings"],
        )

    notes = data.get("notes")
    overwrite = bool(data.get("overwrite", False))
    try:
        if action == "validate":
            result = catalog.validate_batch(notes, overwrite=overwrite)
        else:
            result = catalog.save_batch(notes, overwrite=overwrite)
    except FileExistsError as exc:
        return legacy._err("NOTE_EXISTS", str(exc))

    if result["missing"]:
        return legacy._err(
            "BROKEN_WIKILINKS",
            (
                "The note batch contains dangling WikiLinks. Add substantive "
                "notes for every missing target to the same notes array and "
                "retry; validation follows those notes recursively."
            ),
            {
                "missing": result["missing"],
                "notes": result["notes"],
                "graph": result["graph"],
            },
        )
    return legacy._ok(
        {
            "vault_path": str(vault),
            **result,
            "broken_link_count": 0,
            "broken_links": [],
        }
    )


def handle_study_activity(args: dict[str, Any], **_kwargs: Any) -> str:
    """Record/query evidence and access all durable StudyOS resources."""
    try:
        resource = str(args.get("resource") or "").strip()
        action = str(args.get("action") or "").strip()
        data = _payload(args)
        session_id = str(_kwargs.get("session_id") or "")
        if session_id.startswith("cron_") and (
            (resource == "schedule" and action == "save")
            or (
                resource == "plan_proposal"
                # ``apply`` writes events into a Schedule, so it belongs with
                # the decisions a scheduled run may not make on its own.
                and action in {"accept", "reject", "apply"}
            )
        ):
            return legacy._err(
                "CRON_PROPOSAL_ONLY",
                "Scheduled StudyOS runs may create Plan Proposals but cannot decide them or save Schedules.",
            )
        if resource == "attempt":
            return _attempt_activity(action, data)
        if resource == "pattern_proposal":
            return _proposal_activity(action, data)
        if resource == "plan_proposal":
            return _plan_proposal_activity(action, data)
        return _dispatch_resource(resource, action, data)
    except ValueError as exc:
        return legacy._err("VALIDATION_FAILED", str(exc))
    except FileNotFoundError as exc:
        return legacy._err("NOT_FOUND", str(exc))
    except Exception as exc:
        return legacy._err("STUDY_ACTIVITY_FAILED", str(exc))


def _attempt_score(attempt: dict[str, Any]) -> float:
    score = attempt.get("score")
    return float(score) if isinstance(score, (int, float)) else 0.0


def _assistance_level(attempt: dict[str, Any]) -> str:
    assistance = attempt.get("assistance")
    if isinstance(assistance, dict) and assistance.get("level"):
        return str(assistance["level"])
    return "unrecorded"


def _evaluator_kind(attempt: dict[str, Any]) -> str:
    evaluator = attempt.get("evaluator")
    if isinstance(evaluator, dict) and evaluator.get("kind"):
        return str(evaluator["kind"])
    return "unprovenanced"


def _independently_verified(attempt: dict[str, Any]) -> bool:
    evaluator = attempt.get("evaluator")
    if not isinstance(evaluator, dict) or evaluator.get("kind") not in {"agent", "program", "human"}:
        return False
    confidence = evaluator.get("confidence")
    evaluator_is_credible = confidence is None or (
        isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and confidence >= 0.5
    )
    return _attempt_score(attempt) >= 0.8 and _assistance_level(attempt) == "independent" and evaluator_is_credible


# One unaided success is a fluke; a dimension is only independent once it has
# been demonstrated more than once AND the latest evidence still shows it. The
# single-attempt rule this replaced reported 47 attempts averaging 0.62 as
# independently mastered because exactly one of them was unaided -- and an
# independent dimension is skipped by the Intervention Queue entirely, so the
# mistake did not merely mislabel the capability, it stopped recommending it.
MIN_INDEPENDENT_ATTEMPTS = 2


def _latest_attempt(items: list[dict[str, Any]]) -> dict[str, Any]:
    """The most recent attempt by its own timestamp, not by list order.

    Callers hand this module attempt lists assembled in different ways, so
    recency is read from the evidence rather than assumed from ordering.
    """

    return max(
        items,
        key=lambda item: (str(item.get("occurred_at") or ""), str(item.get("attempt_id") or "")),
    )


def _diagnosis(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    diagnosis_groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    concept_attempts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    transfer: Counter[str] = Counter()
    dimension_attempts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attempt in attempts:
        attempt_id = str(attempt.get("attempt_id"))
        if attempt.get("transfer_level"):
            dimension = str(attempt["transfer_level"])
            transfer[dimension] += 1
            dimension_attempts[dimension].append(attempt)
        for concept in attempt.get("concepts", []):
            concept_attempts[str(concept)].append(attempt)
        for item in attempt.get("diagnoses", []):
            concept = str(item.get("concept") or "unscoped")
            diagnosis_groups[(str(item.get("kind") or "unclassified"), concept)].append(attempt_id)

    concepts = []
    for concept, items in concept_attempts.items():
        average = sum(_attempt_score(item) for item in items) / len(items)
        concepts.append({
            "concept": concept,
            "attempt_count": len(items),
            "average_score": round(average, 3),
            "evidence_attempt_ids": [str(item.get("attempt_id")) for item in items],
        })
    concepts.sort(key=lambda item: (item["average_score"], -item["attempt_count"], item["concept"]))
    clusters = [
        {"kind": kind, "concept": concept, "count": len(ids), "evidence_attempt_ids": ids}
        for (kind, concept), ids in diagnosis_groups.items()
    ]
    clusters.sort(key=lambda item: (-item["count"], item["concept"], item["kind"]))
    dimensions: dict[str, dict[str, Any]] = {}
    for dimension in EVIDENCE_DIMENSION_ORDER:
        items = dimension_attempts.get(dimension, [])
        successful = [item for item in items if _attempt_score(item) >= 0.8]
        independently_verified = [item for item in items if _independently_verified(item)]
        if (
            len(independently_verified) >= MIN_INDEPENDENT_ATTEMPTS
            and _independently_verified(_latest_attempt(items))
        ):
            verification_status = "independent"
        elif successful:
            verification_status = "supported"
        elif items:
            verification_status = "developing"
        else:
            verification_status = "unobserved"
        dimensions[dimension] = {
            "status": "observed" if items else "unobserved",
            "verification_status": verification_status,
            "attempt_count": len(items),
            "average_score": round(sum(_attempt_score(item) for item in items) / len(items), 3) if items else None,
            "evidence_attempt_ids": [str(item.get("attempt_id")) for item in items],
            "independently_verified_attempt_ids": [
                str(item.get("attempt_id")) for item in independently_verified
            ],
            "assistance_provenance": dict(sorted(Counter(_assistance_level(item) for item in items).items())),
            "evaluator_provenance": dict(sorted(Counter(_evaluator_kind(item) for item in items).items())),
        }
    midpoint = len(attempts) // 2
    score_delta = None
    if midpoint:
        earlier = attempts[:midpoint]
        later = attempts[midpoint:]
        early_average = sum(_attempt_score(item) for item in earlier) / len(earlier)
        late_average = sum(_attempt_score(item) for item in later) / len(later)
        score_delta = round(late_average - early_average, 3)
    return {
        "attempt_count": len(attempts),
        "average_score": round(sum(_attempt_score(item) for item in attempts) / len(attempts), 3) if attempts else 0.0,
        "concepts": concepts,
        "diagnosis_clusters": clusters,
        "transfer_evidence": dict(transfer),
        "evidence_dimensions": dimensions,
        "score_delta_earlier_to_later": score_delta,
    }


def _recommendations(diagnosis: dict[str, Any]) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    for cluster in diagnosis["diagnosis_clusters"][:3]:
        if cluster["count"] < 2:
            continue
        recommendations.append({
            "priority": "high",
            "intervention": "prerequisite_repair" if cluster["kind"] == "concept_confusion" else "misconception_probe",
            "concept": cluster["concept"],
            "reason": f"{cluster['kind']} repeated {cluster['count']} times",
            "evidence_attempt_ids": cluster["evidence_attempt_ids"],
        })
    supported_dimensions = [
        (dimension, result)
        for dimension, result in diagnosis["evidence_dimensions"].items()
        if result.get("verification_status") == "supported"
    ]
    if supported_dimensions:
        dimension, result = supported_dimensions[0]
        recommendations.append({
            "priority": "medium",
            "intervention": "independence_probe",
            "evidence_dimension": dimension,
            "reason": f"{dimension} has successful but not independently verified evidence",
            "evidence_attempt_ids": result["evidence_attempt_ids"],
        })
    transfer_verified = any(
        diagnosis["evidence_dimensions"][dimension].get("verification_status") == "independent"
        for dimension in ("near_transfer", "far_transfer")
    )
    if diagnosis["attempt_count"] and not transfer_verified:
        recommendations.append({
            "priority": "medium",
            "intervention": "near_transfer_probe",
            "reason": "No independently verified transfer evidence has been recorded yet",
            "evidence_attempt_ids": [],
        })
    if not recommendations and diagnosis["attempt_count"]:
        recommendations.append({
            "priority": "medium",
            "intervention": "retention_probe",
            "reason": "No repeated gap dominates; verify retention after spacing",
            "evidence_attempt_ids": [],
        })
    return recommendations


def _pattern_proposals(project_id: str, diagnosis: dict[str, Any]) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    for index, cluster in enumerate(diagnosis["diagnosis_clusters"]):
        if cluster["count"] < 2:
            continue
        concept_slug = legacy._slugify(cluster["concept"], "unscoped")
        kind_slug = legacy._slugify(cluster["kind"], "gap")
        proposals.append({
            "schema_version": PATTERN_PROPOSAL_SCHEMA_VERSION,
            "proposal_id": f"proposal-{date.today().isoformat()}-{concept_slug}-{kind_slug}-{index + 1}",
            "project_id": project_id,
            "title": f"补充 {cluster['concept']} 的 {cluster['kind']} 失败路径",
            "change_type": "supplement",
            "status": "candidate",
            "rationale": f"The same diagnosis appeared in {cluster['count']} attempts; keep it a candidate until a transfer probe validates the change.",
            "evidence_attempt_ids": cluster["evidence_attempt_ids"],
            "suggested_change": {
                "recognition_signal": cluster["concept"],
                "failure_path": cluster["kind"],
                "validation_needed": "near_transfer",
            },
            "created_at": created_at,
        })
    return proposals


def _probe_blueprint(diagnosis: dict[str, Any]) -> dict[str, Any] | None:
    if not diagnosis["attempt_count"]:
        return None
    recommendations = _recommendations(diagnosis)
    selected = recommendations[0] if recommendations else {
        "intervention": "retention_probe",
        "reason": "Verify retained understanding",
        "evidence_attempt_ids": [],
    }
    weakest = diagnosis["concepts"][0] if diagnosis["concepts"] else None
    purpose = str(selected["intervention"])
    variation = {
        "prerequisite_repair": "isolate the prerequisite before the original procedure",
        "misconception_probe": "change the condition that distinguishes the observed wrong rule from the correct rule",
        "near_transfer_probe": "change surface details while preserving the solution invariant",
        "independence_probe": "repeat the demonstrated capability without hints or guided steps",
        "retention_probe": "use delayed free retrieval without cues",
    }.get(purpose, "test the targeted gap with one controlled variation")
    evidence_ids = list(dict.fromkeys(
        list(selected.get("evidence_attempt_ids", []))
        + (list(weakest.get("evidence_attempt_ids", [])) if weakest else [])
    ))
    return {
        "purpose": purpose,
        "target_concept": weakest.get("concept") if weakest else None,
        "variation_instruction": variation,
        "difficulty_policy": "change one diagnostic variable at a time",
        "response_policy": "ask for the learner's answer before revealing feedback",
        "rubric_requirements": ["correct outcome", "valid reasoning", "conditions checked", "independent completion"],
        "evidence_attempt_ids": evidence_ids,
        "reason": selected.get("reason"),
    }


def _learning_runtime(vault: Path, project: dict[str, Any]) -> LearningRuntime:
    """Bind the lifecycle module to StudyOS' existing evidence interfaces."""

    def record_attempt(attempt_args: dict[str, Any]) -> dict[str, Any]:
        result = json.loads(_record_attempt(attempt_args))
        return result if isinstance(result, dict) else {}

    return LearningRuntime(
        vault=vault,
        project=project,
        project_dir=legacy._project_dir(vault, project["project_id"]),
        attempt_reader=lambda project_id: _all_attempts(vault, project_id),
        attempt_recorder=record_attempt,
        snapshot_builder=_diagnosis,
        recommendation_builder=_recommendations,
        activity_adapter=activity_adapter_for(project),
    )


def _project_schedules(vault: Path, project_id: str) -> list[dict[str, Any]]:
    """Load a project's Schedules for day-plan projection.

    Invalid files are skipped rather than raised: a day plan is a
    recommendation, and one malformed Schedule should narrow it, not fail the
    whole prioritisation. ``schedule.validate`` remains where a Schedule is
    held to the contract.
    """

    root = (vault / ".StudyOS" / "projects" / legacy._validate_project_id(project_id) / "schedules").resolve()
    try:
        root.relative_to(vault)
    except ValueError as exc:
        raise ValueError("Schedule path escapes Vault") from exc
    schedules: list[dict[str, Any]] = []
    if not root.exists():
        return schedules
    for path in sorted(root.glob("*.json")):
        try:
            schedule = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(schedule, dict):
            schedules.append(schedule)
    return schedules


def _project_timezone(project: dict[str, Any], fallback: datetime) -> Any:
    """The project's own clock, or the caller's when it declares none."""

    name = project.get("timezone")
    if not isinstance(name, str) or not name.strip():
        return fallback.tzinfo
    try:
        return ZoneInfo(name.strip())
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"project timezone is not a valid IANA timezone: {name}"
        ) from exc


def _readable_plan_proposals(root: Path) -> list[dict[str, Any]]:
    """Every Plan Proposal that still validates, skipping the ones that do not.

    Deliberately more forgiving than ``plan_proposal.read``: these proposals
    are read to measure the recommender's own history, and one unreadable file
    should narrow that history rather than block the learner from getting a
    plan today.
    """

    if not root.is_dir():
        return []
    proposals: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            proposals.append(_validated_plan_proposal(path))
        except (ValueError, OSError, json.JSONDecodeError):
            continue
    return proposals


def _recent_outcomes(
    vault: Path,
    project: dict[str, Any],
    attempts: list[dict[str, Any]],
    as_of: datetime,
) -> dict[str, Any]:
    """Measured effectiveness of decisions recent enough to still describe now."""

    horizon = as_of - timedelta(days=OUTCOME_LOOKBACK_DAYS)
    proposals = [
        proposal
        for proposal in _readable_plan_proposals(
            _plan_proposal_dir(vault, project["project_id"], create=False)
        )
        if _decided_after(proposal, horizon)
    ]
    return build_intervention_outcomes(
        proposals=proposals,
        attempts=attempts,
        diagnosis_builder=_diagnosis,
        as_of=as_of,
    )


def _decided_after(proposal: dict[str, Any], horizon: datetime) -> bool:
    decided = (proposal.get("decision") or {}).get("decided_at")
    if not isinstance(decided, str):
        return False
    try:
        moment = datetime.fromisoformat(decided.replace("Z", "+00:00"))
    except ValueError:
        return False
    return moment.tzinfo is not None and moment >= horizon


def _recent_adherence(
    project: dict[str, Any],
    schedules: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    as_of: datetime,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> dict[str, Any]:
    tzinfo = _project_timezone(project, as_of)
    end = as_of.astimezone(tzinfo).date()
    return build_plan_adherence(
        schedules=schedules,
        attempts=attempts,
        tzinfo=tzinfo,
        start=end - timedelta(days=lookback_days),
        end=end,
        as_of=as_of,
    )


def _intervention_orchestration(
    vault: Path,
    project: dict[str, Any],
    data: dict[str, Any],
) -> dict[str, Any]:
    """Derive the queue with the recommender's own measured history in hand.

    Both corrections are read here rather than inside the orchestrator: the
    orchestrator stays pure and testable on data alone, while this seam owns
    the Vault reads that produce that data.
    """

    max_items = data.get("max_items", 5)
    as_of = parse_as_of(data.get("as_of"))
    attempts = _all_attempts(vault, project["project_id"])
    schedules = _project_schedules(vault, project["project_id"])
    orchestrator = InterventionOrchestrator(
        project=project,
        diagnosis_builder=_diagnosis,
    )
    return orchestrator.build(
        attempts=attempts,
        as_of=as_of,
        max_items=max_items,
        schedules=schedules,
        outcomes=_recent_outcomes(vault, project, attempts, as_of),
        adherence=_recent_adherence(project, schedules, attempts, as_of),
    )


def handle_study_coach(args: dict[str, Any], **_kwargs: Any) -> str:
    """Derive diagnoses and next actions from immutable attempt evidence."""
    try:
        action = str(args.get("action") or "diagnose").strip()
        scope = str(args.get("scope") or "project").strip()
        data = _payload(args)
        vault = legacy.resolve_vault_path(data.get("vault_path"))
        project = _project(vault, data.get("project_id"))
        if action in {"start", "advance", "snapshot", "finish"}:
            runtime = _learning_runtime(vault, project)
            session_id = data.get("session_id")
            if action == "start":
                output = runtime.start(
                    session_id=session_id,
                    contract=data.get("contract"),
                    conversation_session_id=(
                        data.get("conversation_session_id") or _kwargs.get("session_id")
                    ),
                )
            elif action == "advance":
                output = runtime.advance(session_id=session_id, observation=data.get("observation"))
            elif action == "snapshot":
                output = runtime.snapshot(session_id=session_id)
            else:
                output = runtime.finish(session_id=session_id)
            return legacy._ok({"project_id": project["project_id"], **output})
        if action == "evaluate_adherence":
            if scope != "project":
                return legacy._err(
                    "INVALID_SCOPE",
                    "evaluate_adherence requires project scope so every applied plan is read on one clock",
                )
            as_of = parse_as_of(data.get("as_of"))
            tzinfo = _project_timezone(project, as_of)
            end = as_of.astimezone(tzinfo).date()
            try:
                if data.get("end_date"):
                    end = date.fromisoformat(str(data["end_date"]))
                start = (
                    date.fromisoformat(str(data["start_date"]))
                    if data.get("start_date")
                    else end - timedelta(days=DEFAULT_LOOKBACK_DAYS)
                )
            except ValueError:
                return legacy._err(
                    "VALIDATION_FAILED",
                    "start_date and end_date must be ISO dates (YYYY-MM-DD)",
                )
            if start > end:
                return legacy._err("VALIDATION_FAILED", "start_date must not follow end_date")
            attempts = _all_attempts(vault, project["project_id"])
            adherence = build_plan_adherence(
                schedules=_project_schedules(vault, project["project_id"]),
                attempts=attempts,
                tzinfo=tzinfo,
                start=start,
                end=end,
                as_of=as_of,
            )
            return legacy._ok(
                {
                    "project_id": project["project_id"],
                    "plan_adherence": adherence,
                    # The correction this measurement produces, shown next to
                    # it: an adherence report the learner cannot connect to
                    # tomorrow's plan is the open loop this action exists to
                    # close.
                    "capacity": capacity_factor(adherence),
                }
            )
        if action == "evaluate_interventions":
            if scope != "project":
                return legacy._err(
                    "INVALID_SCOPE",
                    "evaluate_interventions requires project scope so every decision is comparable",
                )
            root = _plan_proposal_dir(vault, project["project_id"])
            proposals = [
                _validated_plan_proposal(path) for path in sorted(root.glob("*.json"))
            ]
            outcomes = build_intervention_outcomes(
                proposals=proposals,
                attempts=_all_attempts(vault, project["project_id"]),
                diagnosis_builder=_diagnosis,
                as_of=parse_as_of(data.get("as_of")),
            )
            return legacy._ok(
                {
                    "project_id": project["project_id"],
                    "intervention_outcomes": outcomes,
                    # What this measurement changes, next to what it measured:
                    # the priority delta each kind now carries into the queue.
                    "calibration": [
                        {
                            "kind": row["kind"],
                            **outcome_adjustment(
                                by_kind=outcomes["by_kind"], kind=row["kind"]
                            ),
                        }
                        for row in outcomes["by_kind"]
                    ],
                }
            )
        if action in {"prioritize", "propose_plan"}:
            if scope != "project":
                return legacy._err(
                    "INVALID_SCOPE",
                    f"{action} requires project scope so evidence age and all Objectives stay comparable",
                )
            orchestration = _intervention_orchestration(vault, project, data)
            if action == "prioritize":
                output = {"intervention_queue": orchestration["queue"]}
            else:
                output = {
                    "proposal": orchestration["proposal"],
                    "intervention_queue": orchestration["queue"],
                    "policy": (
                        "This call is read-only. Persist with plan_proposal.save; only a non-cron "
                        "explicit accept/reject may decide it, and Schedule changes still require "
                        "schedule.validate then schedule.save."
                    ),
                }
            return legacy._ok({"project_id": project["project_id"], **output})
        if scope == "week" and not data.get("start_date") and not data.get("end_date"):
            today = date.today()
            data["start_date"] = (today - timedelta(days=today.weekday())).isoformat()
            data["end_date"] = today.isoformat()
        elif scope == "session" and not data.get("session_id") and not data.get("attempt_ids"):
            return legacy._err("MISSING_SCOPE_FILTER", "session scope requires data.session_id or data.attempt_ids")
        elif scope == "concept" and not data.get("concept"):
            return legacy._err("MISSING_SCOPE_FILTER", "concept scope requires data.concept")
        attempts = _filtered_attempts(vault, project["project_id"], data)
        diagnosis = _diagnosis(attempts)
        evidence_ids = [str(item.get("attempt_id")) for item in attempts]
        if action == "diagnose":
            output: dict[str, Any] = {"diagnosis": diagnosis}
        elif action == "summarize":
            weakest = diagnosis["concepts"][:3]
            strongest = sorted(diagnosis["concepts"], key=lambda item: (-item["average_score"], -item["attempt_count"]))[:3]
            transfer_verified = any(
                diagnosis["evidence_dimensions"][dimension].get("verification_status") == "independent"
                for dimension in ("near_transfer", "far_transfer")
            )
            output = {
                "summary": {
                    "scope": scope,
                    "attempt_count": diagnosis["attempt_count"],
                    "average_score": diagnosis["average_score"],
                    "strongest_concepts": strongest,
                    "weakest_concepts": weakest,
                    "unverified": [] if transfer_verified else ["transfer"],
                    "unverified_dimensions": [
                        dimension
                        for dimension, result in diagnosis["evidence_dimensions"].items()
                        if result.get("verification_status") != "independent"
                    ],
                    "evidence_attempt_ids": evidence_ids,
                }
            }
        elif action == "recommend":
            output = {"recommendations": _recommendations(diagnosis), "diagnosis": diagnosis}
        elif action == "propose_pattern":
            output = {
                "proposals": _pattern_proposals(project["project_id"], diagnosis),
                "policy": "Proposals are not persisted or applied automatically; save explicitly with study_activity after review.",
            }
        elif action == "generate_probe":
            blueprint = _probe_blueprint(diagnosis)
            if blueprint is None:
                return legacy._err("INSUFFICIENT_EVIDENCE", "Record at least one attempt before generating a diagnostic probe")
            output = {
                "probe_blueprint": blueprint,
                "policy": "Generate one problem from this blueprint; record the learner response as a new attempt before judging transfer.",
            }
        else:
            return legacy._err("INVALID_ACTION", f"Unsupported coach action: {action}")
        return legacy._ok({"project_id": project["project_id"], **output, "evidence_attempt_ids": evidence_ids})
    except LearningRuntimeError as exc:
        return legacy._err(exc.code, exc.message, exc.details)
    except ValueError as exc:
        return legacy._err("VALIDATION_FAILED", str(exc))
    except FileNotFoundError as exc:
        return legacy._err("NOT_FOUND", str(exc))
    except Exception as exc:
        return legacy._err("STUDY_COACH_FAILED", str(exc))


def _diagnoses_tool_schema() -> dict[str, Any]:
    """Return an independent model-facing schema for evidence diagnoses."""

    return {
        "type": "array",
        "description": (
            "Observed diagnoses. Use [] when no specific diagnosis is supported. "
            "Every non-empty item must be an object, never a string. "
            f"Example: [{DIAGNOSIS_OBJECT_EXAMPLE}]"
        ),
        "items": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Short, stable diagnosis category such as condition_missed or concept_confusion.",
                },
                "evidence": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Specific observed response or reasoning that supports this diagnosis.",
                },
                "concept": {
                    "type": "string",
                    "description": "Optional concept most directly implicated by the evidence.",
                },
            },
            "required": list(DIAGNOSIS_REQUIRED_FIELDS),
        },
    }


def _learning_contract_tool_schema() -> dict[str, Any]:
    """Return the model-facing shape required to start a learning Session."""

    return {
        "type": "object",
        "description": (
            "Required for start. The runtime supplies schema_version, contract_id, project_id, and created_at."
        ),
        "properties": {
            "mode": {
                "type": "string",
                "enum": sorted(LEARNING_MODES),
                "description": "Learning intent: learn, assess, execute, or research.",
            },
            "objective": {
                "type": "string",
                "minLength": 1,
                "description": "One observable capability for this Session.",
            },
            "objective_ids": {
                "type": "array",
                "description": "Optional Objective ids from the active Learning Project.",
                "items": {"type": "string", "minLength": 1},
            },
            "time_budget_minutes": {
                "type": "integer",
                "minimum": 1,
                "maximum": 720,
            },
            "assistance_level": {
                "type": "string",
                "enum": sorted(ASSISTANCE_LEVELS),
                "description": "Allowed help: direct, guided, hints_only, or independent.",
            },
            "evidence_targets": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "description": "Evidence dimensions the Session must try to observe.",
                "items": {
                    "type": "string",
                    "enum": list(EVIDENCE_DIMENSION_ORDER),
                },
            },
        },
        "required": [
            "mode",
            "objective",
            "time_budget_minutes",
            "assistance_level",
            "evidence_targets",
        ],
    }


def _source_anchors_tool_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": list(SOURCE_ANCHOR_KINDS)},
                "ref": {"type": "string", "minLength": 1},
                "version": {"type": "string", "minLength": 1},
                "locator": {"type": "string", "minLength": 1},
            },
            "required": ["kind", "ref"],
        },
    }


def _evaluated_observation_tool_schema() -> dict[str, Any]:
    """Return evidence and provenance fields required to advance a Session."""

    return {
        "type": "object",
        "description": (
            "Required for advance. Record only an observed learner response; applied engineering or research "
            "Activities may also require source_anchors and artifact_refs."
        ),
        "properties": {
            "attempt_id": {"type": "string", "minLength": 1},
            "response": {"type": "string", "minLength": 1},
            "result": {"type": "string", "enum": sorted(ATTEMPT_RESULTS)},
            "score": {"type": "number", "minimum": 0, "maximum": 1},
            "duration_seconds": {"type": "integer", "minimum": 0},
            "evaluator": {
                "type": "object",
                "description": "Who evaluated the response and with what confidence.",
                "properties": {
                    "kind": {"type": "string", "enum": sorted(EVALUATOR_KINDS)},
                    "id": {"type": "string", "minLength": 1},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["kind"],
            },
            "assistance": {
                "type": "object",
                "description": "Optional actual assistance used; defaults to the current Activity.",
                "properties": {
                    "level": {"type": "string", "enum": sorted(ASSISTANCE_LEVELS)},
                    "hints_used": {"type": "integer", "minimum": 0},
                },
            },
            "concepts": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "patterns": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "diagnoses": _diagnoses_tool_schema(),
            "source_anchors": _source_anchors_tool_schema(),
            "artifact_refs": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
        },
        "required": ["response", "result", "evaluator"],
    }


def _review_due_tool_properties() -> dict[str, Any]:
    """Reuse the canonical review queue selectors on the consolidated tool."""

    return {
        name: schema
        for name, schema in legacy.STUDY_DUE_REVIEWS_SCHEMA["parameters"]["properties"].items()
        if name != "vault_path"
    }


def _note_batch_tool_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "description": (
            "For note.validate/save, the complete recursive batch of notes. "
            "Every WikiLink reachable from these notes must resolve to an "
            "existing Vault note/attachment or another note in this batch."
        ),
        "items": {
            "oneOf": [
                dict(
                    legacy.STUDY_DUE_REVIEWS_SCHEMA["parameters"]["properties"][
                        "notes"
                    ]["items"]
                ),
                {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Vault-relative Markdown path.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Complete non-empty Markdown content.",
                        },
                        "overwrite": {
                            "type": "boolean",
                            "description": "Allow this item to replace an existing note.",
                        },
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            ]
        },
    }


STUDY_ACTIVITY_SCHEMA = {
    "description": "Single StudyOS persistence interface. For a StudyOS learning-planning request, first call project.status and prompt_context.load with planning or schedule_adjustment. Creating, completing, updating, registering, or adding a StudyOS plan requires schedule.validate followed by schedule.save; a Markdown file is only a draft and never completes persistence. Obsidian note writes must use note.validate then note.save, never a generic file-write tool: save is atomic and rejects every direct or transitively reachable dangling WikiLink until substantive notes for all missing targets are included in the batch. note.audit/graph reports existing WikiLink integrity; concept.graph remains the learning-dependency graph. Record/query immutable attempts and manage projects, notes, reviews, concepts, curricula, schedules, records, lessons, evidence-backed pattern proposals, and proactive Plan Proposals. For schedule.validate/save, data is the complete study_schedule.v1 object itself. Long-term date ranges belong in phases; phase.effort_minutes may hold aggregate workload, while events are optional concrete sessions and may be empty. schedule.save validates and writes the canonical file discovered by the StudyOS panel, so do not write or register a Schedule separately. plan_proposal supports ensure_today/save/list/read/accept/reject/apply; ensure_today derives and persists the day's plan once and returns the existing one afterwards, accept records a decision without mutating a Schedule, and apply then writes an accepted plan's events -- and only its events -- into their Schedules. Cron sessions may save proposals but cannot decide them or save Schedules. For review.due, data supports explicit notes, subjects, YAML tags, concepts, difficulties, levels, review_state, match, sort, limit, and exclude_paths selectors; hidden directories are excluded by default, and limit never broadens the selectors. For a graded interactive review, prefer review.submit: it atomically stores the immutable attempt and advances spaced repetition. Put operation parameters in data.",
    "parameters": {
        "type": "object",
        "properties": {
            "resource": {
                "type": "string",
                "enum": ["attempt", "pattern_proposal", "plan_proposal", "project", "schedule", "note", "review", "error", "concept", "curriculum", "learning_record", "decision", "lesson", "prompt_context", "session", "memory"],
            },
            "action": {"type": "string", "description": "Resource action, e.g. attempt.record/list/read, note.list/read/extract/audit/graph/validate/save, schedule.template/validate/save/list/read, review.due/submit/stats, concept.graph/queue/update_state, or project.init/status."},
            "vault_path": {"type": "string"},
            "project_id": study_project_id_json_schema(),
            "data": {
                "type": "object",
                "description": "Payload for the selected resource action. For schedule.validate/save, put the complete Schedule directly here: data={schema_version, schedule_id, project_id, ...}. Use phases (optionally phase.effort_minutes) for long-term ranges and events only for concrete sessions. Do not nest it under data.schedule or a second data.",
                "properties": {
                    **_review_due_tool_properties(),
                    "notes": _note_batch_tool_schema(),
                    "roots": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional note roots for note.audit/graph; omitted audits the full Vault.",
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "Allow note.save/validate to replace existing batch paths.",
                    },
                    "diagnoses": _diagnoses_tool_schema(),
                },
            },
        },
        "required": ["resource", "action"],
    },
}


STUDY_COACH_SCHEMA = {
    "description": "Evidence-driven StudyOS learning runtime and coach. Start, advance, inspect, or finish an explicit learning Session; diagnose attempts; summarize demonstrated change; recommend an intervention; prioritize a project-wide Intervention Queue; produce a read-only plan proposal; evaluate whether accepted Interventions were followed by improvement; evaluate whether applied day-plan events actually happened; generate a diagnostic-probe blueprint; or propose a versioned problem-pattern improvement. prioritize and propose_plan already apply what those two evaluations measure -- observed activity duration, measured effectiveness, and completed share of a planned day -- so call evaluate_adherence or evaluate_interventions to explain a plan, not to obtain one. Starting never creates evidence, advancing requires evaluator provenance, and proactive actions never persist or mutate a Schedule.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "advance", "snapshot", "finish", "diagnose", "summarize", "recommend", "prioritize", "propose_plan", "evaluate_interventions", "evaluate_adherence", "generate_probe", "propose_pattern"],
                "description": (
                    "start requires data.session_id and data.contract; advance requires data.session_id and "
                    "data.observation; snapshot/finish require data.session_id."
                ),
            },
            "scope": {"type": "string", "enum": ["session", "concept", "week", "project"]},
            "vault_path": {"type": "string"},
            "project_id": study_project_id_json_schema(),
            "data": {
                "type": "object",
                "description": "For lifecycle actions: session_id plus contract (start) or evaluated observation (advance). For evidence analysis: concept, pattern, item_id, result, start_date, or end_date filters. For prioritize/propose_plan: optional timezone-aware as_of and max_items (1-20). For evaluate_adherence: optional start_date/end_date bounding which applied days are reconciled, defaulting to the last two weeks.",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "pattern": SCHEDULE_ID_PATTERN,
                        "description": "Required for start, advance, snapshot, and finish.",
                    },
                    "conversation_session_id": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Optional explicit Hermes conversation binding for start.",
                    },
                    "contract": _learning_contract_tool_schema(),
                    "observation": _evaluated_observation_tool_schema(),
                    "concept": {"type": "string", "minLength": 1},
                    "pattern": {"type": "string", "minLength": 1},
                    "item_id": {"type": "string", "minLength": 1},
                    "result": {"type": "string", "enum": sorted(ATTEMPT_RESULTS)},
                    "attempt_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "start_date": {"type": "string", "description": "Inclusive ISO date YYYY-MM-DD."},
                    "end_date": {"type": "string", "description": "Inclusive ISO date YYYY-MM-DD."},
                    "as_of": {"type": "string", "description": "Timezone-aware ISO datetime."},
                    "max_items": {"type": "integer", "minimum": 1, "maximum": 20},
                },
            },
        },
        "required": ["action"],
    },
}
