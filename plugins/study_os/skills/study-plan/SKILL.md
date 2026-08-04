---
name: study-plan
description: Create, revise, and persist StudyOS learning schedules.
platforms: [linux, macos, windows]
---

# StudyOS Planning

<!-- prompt-context:begin -->
## Schedule

`events` are timezone-aware sessions inside the phase range that satisfy
`duration_minutes == end - start`. Never encode a phase as a multi-day event.

## Workflow

1. Read the active project, curricula, and target Schedule. Create a missing
   curriculum first.
2. Map observable objectives, prerequisites, sources, time, and a checkpoint.
   Never invent dates, scores, or availability; keep topic names stable.
3. For advice, “how should I plan?”, or an explicit draft, return a compact
   draft without mutation. An imperative request to create, complete, update,
   register, or add a **StudyOS** plan/calendar authorizes persistence.
4. Missing daily time slots never block a long-term Schedule: save dated
   `phases` with `events: []` and add events only when times are known.
5. Do not end with “I will register it next.” Continue in the same turn
   through `schedule.validate` and `schedule.save`. Claim
   saved/registered/written/completed only after success, and report the
   returned path.

## Proposals

`plan_proposal.ensure_today` derives and persists the day's plan once; call it
for a daily plan or briefing rather than building one by hand, and do not
create a second plan when it returns `created: false`.

List pending proposals before saving one. Only an explicit learner decision
permits accept/reject. Apply an accepted proposal with `plan_proposal.apply`,
which writes its day-plan events and nothing else. Changing `phases` or `range`
is still `schedule.validate` then `schedule.save`.
<!-- prompt-context:end -->

## Reference

Everything below is background for a human or for `skill_view`. The loaded
operation guide carries call shapes; backend validation owns their invariants.

### Entry sequence

First call `study_activity` for `project.status`, then `prompt_context.load`
with intent `planning` or `schedule_adjustment`. Never mutate system prompts.

If `study_activity` is unavailable, ask to enable the `study` toolset; never
substitute file tools. (This case cannot be covered by the injected fragment:
`prompt_context.load` is itself a `study_activity` resource, so when the tool
is missing there is no fragment to read.)

### Schedule shape

- Long-term roadmaps belong in `phases`. Use `phase.goal`, optional `goals`,
  and an optional aggregate `effort_minutes` to describe a phase.
- `events` are optional concrete sessions: `events` may be empty until daily
  times are known, and are filled in later without reshaping the phase.

### Persisting a Schedule

Pass the same complete `study_schedule.v1` object to both calls, in order:

- `study_activity(resource="schedule", action="validate", project_id="...", data={...})`
- `study_activity(resource="schedule", action="save", project_id="...", data={...})`

`data` is the Schedule itself, never `data.schedule`, `data.data`, or a
prewritten file. `schedule.save` is registration: it validates, writes the
canonical file that the StudyOS panel discovers, and returns that path. Do not
write or register the Schedule separately.

A Markdown roadmap, including one written under `.StudyOS/plans/`, is not a
Schedule. Producing one does not satisfy a request to create, complete,
update, register, or add a StudyOS plan.

### Proposal tools

Use `study_coach.prioritize` to rank a project-wide Intervention Queue and
`study_coach.propose_plan` to produce a read-only Plan Proposal. Proposals are
stored through `study_activity` with `resource="plan_proposal"`, which supports
`save`, `list`, `read`, `accept`, and `reject`.

Acceptance records a decision and never mutates a Schedule on its own. Cron
sessions may save proposals but can never decide them or save Schedules.
