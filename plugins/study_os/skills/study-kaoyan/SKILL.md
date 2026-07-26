---
name: study-kaoyan
description: Guide 考研 learning with StudyOS.
platforms: [linux, macos, windows]
---

# StudyOS 考研 Domain Pack

Use only with `domain_pack:"kaoyan.v1"`. Load the active workflow intent with
`study_activity(resource="prompt_context", action="load", data={"intent":"..."})`;
never mutate system prompts.

Only the region between the `prompt-context` markers below is inlined into the
prompt fragment for this domain pack. Everything after the closing marker is
reference material for `skill_view` and for maintainers: it costs no prompt
budget, so it may be as detailed as it needs to be.

<!-- prompt-context:begin -->
## 考研 Operating Rules

- Storage stays generic: 考研 is a domain pack, not a separate persistence
  model.
- Confirm exam date, phase, subjects, available time, and material before
  proposing a schedule. The default project is `kaoyan-2027`, but never assume
  it replaces the user's active project.
- Build curriculum from 考点, prerequisites, textbook/exercise sources, and
  representative problems. Foundation work favors definitions, formulas, and
  examples; review work favors 错题 clusters, weak prerequisites, and timed
  transfer practice.
- Persist only validated curricula and schedules. A saved calendar artifact is
  read-only in the desktop UI; never imply drag/drop or unsaved edits exist.
- Strategic study-system tradeoffs belong to `study-grill`, never routine
  schedules, 整理, 复习, or 错题 remediation.
<!-- prompt-context:end -->

## Skill Routing

The base `study-os` router already carries the routing table, so these mappings
are restated here only as domain vocabulary — they do not need to occupy the
domain fragment's budget.

- Schedules and next steps -> `study-plan`.
- Problem capture and 整理 -> `study-organize`.
- Retrieval, 复习, and spaced repetition -> `study-review`.
- Mock exams (模考) and 错题 analysis -> `study-assessment`.
- A missing prerequisite -> `study-teach`.
- `study-lesson` only for a requested or genuinely visual concept.
- Strategic study-system tradeoffs -> `study-grill` (see the marked region: this
  one carries a negative guardrail, so it stays in the prompt fragment).

## Pack Defaults

Defined by `plugins/study_os/domain_packs/kaoyan.py`. These are seeds for
`project.init`, not assumptions to apply to an existing project.

| Field | Default |
| --- | --- |
| `project_id` | `kaoyan-2027` |
| `title` | 2027 考研学习计划 |
| `domain` / `exam_type` | `kaoyan` / 考研 |
| `exam_date` | 2027-12-20 |
| `phase` | `foundation` |
| `workspace_type` | `exam-vault` |
| `artifact_policy` | `lightweight` |
| Intervention duration | 30 minutes |

Default subjects and target scores: 数学 (`math`, 120), 英语一 (`english`, 75),
政治 (`politics`, 75). The starter schedule template uses the `Asia/Shanghai`
timezone and a 基础阶段 phase whose goal is 完成核心考点覆盖.

Confirm each of these with the learner before building on them. An exam date,
a phase boundary, or a subject list carried over from the defaults into a
learner's real plan is a silent error that only surfaces months later.

## Curriculum Notes

- 考点 are the unit of coverage. A curriculum entry that cannot be traced to a
  考点, a prerequisite concept, or a representative problem is decoration.
- Foundation phase: definitions, formulas, worked examples, and the smallest
  transfer step that proves the definition landed.
- Review phase: 错题 clusters first, then the prerequisites those clusters
  expose, then timed transfer practice under exam conditions.
- Textbook and exercise sources belong on the curriculum entry, so a later
  session can reopen the same material instead of guessing.

## Persistence Notes

- A Markdown draft is never persistence. Curricula and schedules reach the
  desktop panel only through the validated save path.
- A saved calendar artifact renders read-only in the desktop UI. Never describe
  drag/drop, inline editing, or unsaved local edits to the learner — the next
  change goes through a new validated save.
