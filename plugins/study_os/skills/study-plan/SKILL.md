---
name: study-plan
description: Create, revise, and persist StudyOS learning schedules.
platforms: [linux, macos, windows]
---

# StudyOS Planning

First call `study_activity` for `project.status`, then `prompt_context.load`
with intent `planning` or `schedule_adjustment`. Never mutate system prompts.

## Schedule

- Long-term roadmaps belong in `phases`; use `phase.goal`, optional `goals`, and
  optional aggregate `effort_minutes`.
- `events` are timezone-aware study sessions; `events` may be empty
  until daily times are known. Never encode a phase as a multi-day event.
- Events stay within the range and satisfy `duration_minutes == end - start`.

## Workflow

1. Read the active project, curricula, and target Schedule.
2. Map observable objectives, prerequisites, sources, time, and a checkpoint.
   Never invent dates, scores, or availability; keep topic names stable.
3. For advice, “how should I plan?”, or an explicit draft, return a compact
   draft without mutation. An imperative request to create, complete, update,
   register, or add a **StudyOS** plan/calendar authorizes persistence.
4. Create missing curriculum, then pass the same complete `study_schedule.v1`
   to:
   - `study_activity(resource="schedule", action="validate", project_id="...", data={...})`
   - `study_activity(resource="schedule", action="save", project_id="...", data={...})`

`data` is the Schedule itself, never `data.schedule`, `data.data`, or a
prewritten file. `schedule.save` is registration and returns the canonical path.

## Completion

- A Markdown roadmap, including one under `.StudyOS/plans/`, is not a Schedule
  and does not satisfy a request to create or update a StudyOS plan.
- Missing exact daily time slots do not block a long-term Schedule. Save dated
  `phases` with `events: []`; add concrete events only when times are known.
- Do not end with “I will register it next.” Continue in the same turn through
  `schedule.validate` and `schedule.save`.
- Claim saved/registered/written/completed only after success and report the
  returned path. If `study_activity` is unavailable, ask to enable the `study`
  toolset; never substitute file tools.

## Proposals and Cron

Use `study_coach.prioritize` and `propose_plan`; list pending proposals before
saving one. Only an explicit learner decision permits accept/reject. Acceptance
never mutates a Schedule: apply with `source_plan_proposal_id`, validate, and
save. Cron may save proposals but never decide them or save Schedules.
