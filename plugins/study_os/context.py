"""Turn-local context for active StudyOS Sessions and Schedule phases."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from plugins.study_os import tools as legacy
from plugins.study_os.runtime import (
    LearningRuntimeError,
    active_session_for_conversation,
    active_vault_for_conversation,
)


MAX_ACTIVE_CONTEXT_CHARS = 2800
MAX_SCHEDULE_CONTEXT_CHARS = 3000

_PLANNING_TERMS = (
    "plan",
    "roadmap",
    "schedule",
    "calendar",
    "规划",
    "计划",
    "排期",
    "日历",
)
_LEARNING_TERMS = (
    "studyos",
    ".studyos",
    "study os",
    "study",
    "learn",
    "exam",
    "学习",
    "复习",
    "备考",
    "考研",
    "课程",
    "科目",
    "数学",
    "线性代数",
    "概率",
    "高数",
    "真题",
    "考点",
)


def _message_has_any(message: str, terms: tuple[str, ...]) -> bool:
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", message)
        if term.isascii()
        else term in message
        for term in terms
    )


def _is_study_os_start(message: str) -> bool:
    stripped = message.strip()
    return stripped in {"/study-os", "/study_os"} or (
        'user has invoked the "study-os" skill' in stripped
    )


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _candidate_vaults(conversation_session_id: str) -> list[Path]:
    candidates: list[Path] = []
    bound = active_vault_for_conversation(conversation_session_id)
    if bound is not None:
        candidates.append(bound)
    try:
        fallback = legacy.resolve_vault_path()
    except (FileNotFoundError, ValueError):
        fallback = None
    if fallback is not None and fallback not in candidates:
        candidates.append(fallback)
    return candidates


def _context_payload(session: dict[str, Any], *, include_details: bool = True) -> dict[str, Any]:
    contract_value = session.get("contract")
    contract: dict[str, Any] = contract_value if isinstance(contract_value, dict) else {}
    activity_value = session.get("current_activity")
    activity: dict[str, Any] = activity_value if isinstance(activity_value, dict) else {}
    current_activity: dict[str, Any] = {
        "activity_id": activity.get("activity_id"),
        "activity_adapter": activity.get("activity_adapter"),
            "kind": activity.get("kind"),
            "evidence_target": activity.get("evidence_target"),
            "assistance_level": activity.get("assistance_level"),
        "evidence_requirements": list(activity.get("evidence_requirements", [])),
        "instructions": _clip(activity.get("instructions"), 600),
        "response_policy": _clip(activity.get("response_policy"), 300),
        "reason": _clip(activity.get("reason"), 350),
    }
    payload: dict[str, Any] = {
        "session_id": session.get("session_id"),
        "project_id": session.get("project_id"),
        "mode": contract.get("mode"),
        "objective": _clip(contract.get("objective"), 600),
        "objective_ids": list(contract.get("objective_ids", []))[:12],
        "assistance_level": contract.get("assistance_level"),
        "required_evidence": list(contract.get("evidence_targets", [])),
        "recorded_evidence_ids": list(session.get("evidence_ids", []))[-20:],
        "current_activity": current_activity,
    }
    if include_details:
        current_activity["rubric_requirements"] = [
            _clip(item, 140) for item in activity.get("rubric_requirements", [])[:4]
        ]
        current_activity["source_anchors"] = [
            {
                "kind": anchor.get("kind"),
                "ref": _clip(anchor.get("ref"), 160),
                "locator": _clip(anchor.get("locator"), 100),
            }
            for anchor in activity.get("source_anchors", [])[:3]
            if isinstance(anchor, dict)
        ]
    return payload


def _render_context(session: dict[str, Any]) -> str:
    prefix = (
        "[StudyOS active learning session — turn-local context]\n"
        "This is workflow state, not proof of mastery. Follow the assistance level, collect the learner's "
        "own response before feedback, and record evaluated evidence with study_coach.advance.\n"
    )
    context = prefix + json.dumps(_context_payload(session), ensure_ascii=False, separators=(",", ":"))
    if len(context) > MAX_ACTIVE_CONTEXT_CHARS:
        context = prefix + json.dumps(
            _context_payload(session, include_details=False),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return context if len(context) <= MAX_ACTIVE_CONTEXT_CHARS else context[: MAX_ACTIVE_CONTEXT_CHARS - 1] + "…"


def active_learning_context(*, session_id: str = "", **_kwargs: Any) -> dict[str, str] | None:
    """Build cache-safe user-message context for one bound Hermes conversation."""

    conversation_id = str(session_id or "").strip()
    if not conversation_id:
        return None
    for vault in _candidate_vaults(conversation_id):
        try:
            session = active_session_for_conversation(vault, conversation_id)
        except (LearningRuntimeError, OSError, ValueError):
            continue
        if session is not None:
            return {"context": _render_context(session)}
    return None


def _schedule_context(vault: Path, as_of: datetime | None) -> str:
    from plugins.study_os.workspace import StudyWorkspace

    workspace = StudyWorkspace(vault=vault, source="context")
    project_id = workspace.active_project_id()
    if project_id is None:
        return ""
    catalog = workspace.discover_schedules(project_id)
    current = as_of or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.astimezone()
    active_schedules: list[dict[str, Any]] = []
    for artifact in catalog.schedules:
        schedule = artifact.schedule
        try:
            local_now = current.astimezone(ZoneInfo(schedule["timezone"]))
        except (KeyError, ZoneInfoNotFoundError):
            continue
        current_date = local_now.date().isoformat()
        phases = [
            {
                "id": phase["id"],
                "title": phase["title"],
                "start": phase["start"],
                "end": phase["end"],
                "goal": _clip(phase["goal"], 500),
                "goals": [_clip(goal, 240) for goal in phase.get("goals", [])[:6]],
            }
            for phase in schedule.get("phases", [])
            if phase["start"] <= current_date <= phase["end"]
        ]
        events: list[dict[str, Any]] = []
        for event in schedule.get("events", []):
            try:
                start = datetime.fromisoformat(event["start"].replace("Z", "+00:00"))
                end = datetime.fromisoformat(event["end"].replace("Z", "+00:00"))
            except (KeyError, TypeError, ValueError):
                continue
            if start <= current <= end:
                events.append(
                    {
                        "id": event["id"],
                        "title": event["title"],
                        "start": event["start"],
                        "end": event["end"],
                        "goals": [_clip(goal, 240) for goal in event.get("goals", [])[:6]],
                    }
                )
        if phases or events:
            active_schedules.append(
                {
                    "schedule_id": schedule["schedule_id"],
                    "title": schedule["title"],
                    "timezone": schedule["timezone"],
                    "active_phases": phases,
                    "active_events": events,
                }
            )
    if not active_schedules:
        return ""
    prefix = (
        "[StudyOS active Schedule — turn-local context]\n"
        "These are current planning constraints, not evidence of mastery. Concurrent Schedules coexist; "
        "do not silently replace one with another.\n"
    )
    payload = {
        "as_of": current.isoformat(timespec="seconds"),
        "project_id": project_id,
        "schedules": active_schedules,
    }
    context = prefix + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return context if len(context) <= MAX_SCHEDULE_CONTEXT_CHARS else context[: MAX_SCHEDULE_CONTEXT_CHARS - 1] + "…"


def study_planning_context(
    *,
    user_message: str = "",
    as_of: datetime | None = None,
    **_kwargs: Any,
) -> dict[str, str] | None:
    """Route learning-plan persistence through StudyOS on the current turn."""

    message = str(user_message or "").strip().casefold()
    is_planning = bool(message) and _message_has_any(message, _PLANNING_TERMS) and _message_has_any(
        message,
        _LEARNING_TERMS,
    )
    is_start = _is_study_os_start(message)
    if not is_planning and not is_start:
        return None
    vaults = [
        vault
        for vault in _candidate_vaults("")
        if (vault / ".StudyOS" / "projects" / "active.json").is_file()
    ]
    if not vaults:
        return None
    parts = [_schedule_context(vaults[0], as_of)]
    if is_planning:
        parts.append(
            "[StudyOS planning workflow — turn-local context]\n"
            "This Vault has an active StudyOS Learning Project. First call study_activity for "
            "project.status, then prompt_context.load with intent planning or schedule_adjustment, "
            "and read the existing curriculum and target Schedule. A Markdown plan is only an "
            "optional human-readable draft; it never registers a StudyOS Schedule. If the learner "
            "asked to complete, save, register, apply, or add the plan to the calendar, the request "
            "is incomplete until the same complete study_schedule.v1 object succeeds through "
            "schedule.validate and schedule.save. Do not substitute terminal/file writes and do not "
            "promise a future tool call. If study_activity is unavailable, state that the study "
            "toolset must be enabled instead of claiming persistence."
        )
    context = "\n\n".join(part for part in parts if part)
    return {"context": context} if context else None
