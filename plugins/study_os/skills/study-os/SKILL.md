---
name: study-os
description: Route StudyOS learning workflows.
platforms: [linux, macos, windows]
---

# StudyOS Router

<!-- prompt-context:begin -->
## Route

Route plans and next steps to `study-plan`, organization to `study-organize`,
recall to `study-review`, teaching to `study-teach`, and diagnosis to
`study-assessment`. Route Domain Packs to their matching skill; use
`study-lesson` for visuals and `study-grill` for strategic decisions.

## Flow

1. Enter the workflow the learner asked for. Call `plan_proposal.ensure_today`
   only for daily planning; never interrupt review or teaching with it.
2. The learner controls scope, pace, and stopping. Treat interaction completion
   and evidence verification separately; never continue solely to strengthen a
   verification label. Stopping closes future work without erasing supported
   observations already produced.
3. Read relevant records before changes; persist only completed outcomes:
   LearningRecord for demonstrated progress, LearningDecisionRecord for an
   accepted strategy.
4. Use one evidence owner. Atomic workflows complete themselves; otherwise
   follow one focused Session's ActivitySpec.
5. Never infer mastery from chat, counts, or plans.
   Never mutate system prompts; active Session state is turn-local context.
<!-- prompt-context:end -->

## Reference

Everything below this line stays in the skill document but is deliberately
outside the prompt-context markers: it is either already stated by the
`study_activity` / `study_coach` tool schema descriptions the model always
sees when the `study` toolset is on, or it is guidance for a human reading
`skill_view` rather than a rule the router has to carry into every turn.

### Setup

Enable `study` before a new chat.

### Entering a workflow

Check `project.status`; initialize only when asked. Load `prompt_context.load`
for the intent. Both steps are already in the `study_activity` description, and
this document only ever reaches the model *as* the output of
`prompt_context.load`, so repeating them inside the markers could not change
anything.

### Tool division of labour

`study_activity` owns state; `study_coach` concludes.

### Writes

Never use generic writes for supported resources: `schedule.save` registers
schedules; Vault notes require `note.validate`/`note.save` and all recursive
WikiLink misses. Audit with `note.graph`.

### Session call shapes

Start a focused Session once with the complete minimal contract:

```python
study_coach(action="start", data={"session_id": "learn-topic-001", "contract":
  {"mode": "learn", "objective": "observable capability",
   "time_budget_minutes": 30, "assistance_level": "guided",
   "evidence_targets": ["explanation"]}})
```

Use schema enums and an integer budget; optional `objective_ids` must exist.

Advance only after the learner responds, and reuse that `session_id` for
`snapshot` and `finish` — both rules are already carried by the `study_coach`
`action` and `observation` schema descriptions:

```python
study_coach(action="advance", data={"session_id": "learn-topic-001",
  "observation": {"response": "observed response", "result": "partial",
   "evaluator": {"kind": "agent"}, "diagnoses": []}})
```

### Evidence rules

Record only observed learner work. Never infer mastery from chat, counts, or
plans, nor auto-apply proposals. Cron may save a proposal but cannot decide it
or save a Schedule.
