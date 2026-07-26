---
name: study-os
description: Route StudyOS learning workflows.
platforms: [linux, macos, windows]
---

# StudyOS Router

Enable `study` before a new chat. `study_activity` owns state; `study_coach`
concludes. Never use generic writes for supported resources:
`schedule.save` registers schedules; Vault notes require
`note.validate`/`note.save` and all recursive WikiLink misses. Audit with
`note.graph`.

## Flow

1. Check `project.status`; initialize only when asked.
2. Load `prompt_context.load` for the intent. Never mutate system prompts.
3. Read relevant records before changes; persist only completed outcomes.
4. Start a focused Session once with the complete minimal contract:
   `study_coach(action="start", data={"session_id":"learn-topic-001","contract":
   {"mode":"learn","objective":"observable capability","time_budget_minutes":30,
   "assistance_level":"guided","evidence_targets":["explanation"]}})`.
   Use schema enums and an integer budget; optional `objective_ids` must exist.
5. Follow the ActivitySpec. After the learner responds, call
   `study_coach(action="advance", data={"session_id":"learn-topic-001","observation":
   {"response":"observed response","result":"partial","evaluator":{"kind":"agent"},
   "diagnoses":[]}})`. Meet `evidence_requirements`; reuse the id for
   `snapshot` and `finish`.

## Route

Route plans and next steps to `study-plan`, organization to `study-organize`,
recall to `study-review`, teaching to `study-teach`, and diagnosis to
`study-assessment`. Route Domain Packs to their matching skill; use
`study-lesson` for visuals and `study-grill` for strategic decisions.

## Evidence Rules

Record only observed learner work. Never infer mastery from chat, counts, or
plans, nor auto-apply proposals. Cron may save a proposal but cannot decide it
or save a Schedule. Use LearningRecord for demonstrated progress and
LearningDecisionRecord for accepted strategy. Active Session state is
turn-local user context, never system prompt content.
