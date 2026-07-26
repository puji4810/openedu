"""Study OS plugin.

Registers a small Obsidian-backed study toolset. The plugin is bundled and
auto-loaded, but the tools only enter a model schema when the ``study`` toolset
is enabled for the active platform/profile.
"""

from __future__ import annotations

from pathlib import Path

from agent.skill_utils import parse_frontmatter
from plugins.study_os.context import active_learning_context, study_planning_context
from plugins.study_os.domain_packs import domain_pack_registry
from plugins.study_os.learning import (
    STUDY_ACTIVITY_SCHEMA,
    STUDY_COACH_SCHEMA,
    handle_study_activity,
    handle_study_coach,
)


_TOOLS = (
    ("study_activity", STUDY_ACTIVITY_SCHEMA, handle_study_activity, "study"),
    ("study_coach", STUDY_COACH_SCHEMA, handle_study_coach, "study"),
)

# Skills routed unconditionally, in ladder order. Only the names live here: the
# routing description is read from each SKILL.md frontmatter, because a second
# copy in this file is a copy that drifts -- study-review routed on "Run StudyOS
# spaced repetition reviews." for months while its frontmatter said "Run flexible
# StudyOS spaced-repetition reviews."
_ROUTED_SKILLS = (
    "study-os",
    "study-plan",
    "study-organize",
    "study-review",
    "study-teach",
    "study-lesson",
    "study-assessment",
    "study-grill",
)

_SKILLS_ROOT = Path(__file__).resolve().parent / "skills"


def _skill_description(skill_path: Path, expected_name: str, label: str) -> str:
    """Return the frontmatter description, the single source of truth for routing.

    No length rule here. ``agent.skill_utils.extract_skill_description`` does
    clip at 60 characters, but its one caller (``agent.prompt_builder``) indexes
    the ``~/.hermes/skills`` tree plus the configured external directories, and
    plugin skills enter neither -- they are explicit loads only. The description
    registered here is read whole by ``agent.skill_commands``, and ``skill_view``
    re-derives it from this same frontmatter. Rejecting a 61-character
    description would trade a truncation that cannot happen for a plugin that
    refuses to load.
    """

    frontmatter, _body = parse_frontmatter(skill_path.read_text(encoding="utf-8"))
    skill_name = str(frontmatter.get("name") or "").strip()
    description = str(frontmatter.get("description") or "").strip()
    if skill_name != expected_name or not description:
        raise ValueError(
            f"{label} metadata must declare "
            f"name={expected_name!r} and a description"
        )
    return description


def _skill_registrations() -> list[tuple[str, Path, str]]:
    """Resolve every skill this plugin routes: ``(name, path, description)``."""

    sources = [(name, f"StudyOS skill {name}") for name in _ROUTED_SKILLS]
    sources += [
        (pack.prompt_skill, f"DomainPack {pack.id} prompt skill")
        for pack in domain_pack_registry().values()
        if pack.prompt_skill is not None
    ]
    registrations = []
    for skill_name, label in sources:
        skill_path = _SKILLS_ROOT / skill_name / "SKILL.md"
        registrations.append(
            (skill_name, skill_path, _skill_description(skill_path, skill_name, label))
        )
    return registrations


def register(ctx) -> None:
    """Register Study OS tools and the opt-in Study OS skill."""
    # Every SKILL.md is read and validated before anything reaches the registry,
    # because registration is not transactional: raising part way through leaves
    # the tools and the two pre_llm_call hooks live and some skills routable,
    # while the plugin manager records study_os as failed with no tools at all.
    # All-or-nothing is the only state a caller can reason about.
    registrations = _skill_registrations()

    for name, schema, handler, toolset in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset=toolset,
            schema=schema,
            handler=handler,
            emoji="📚",
        )

    ctx.register_hook("pre_llm_call", active_learning_context)
    ctx.register_hook("pre_llm_call", study_planning_context)

    for skill_name, skill_path, description in registrations:
        ctx.register_skill(skill_name, skill_path, description)
