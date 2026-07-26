---
name: study-assessment
description: Analyze StudyOS exams and mistakes.
platforms: [linux, macos, windows]
---

# StudyOS Assessment

## When To Use

Use for mock exams, weekly review, 错题, and diagnostics. Call
`study_activity(resource="prompt_context", action="load",
data={"intent":"assessment"})` or use `"error_analysis"`;
never mutate system prompts.

<!-- prompt-context:begin -->
## Diagnose From Evidence

1. Set the scope: one attempt, a concept, a session, a week, or the project.
   Read existing attempts before using `study_coach` on the same scope; it may
   summarize or recommend but never proves unobserved dimensions.
2. For a new answer, classify outcome, reasoning, missed conditions, concept,
   pattern, and next action. Record an immutable `attempt` first; log an
   `error` only when a concrete failure needs durable remediation.
3. Use `review.weekly_report` for a requested weekly artifact. Create
   `review.create_task` only for an accepted follow-up, not every
   recommendation.
4. For repeated evidence, request `study_coach.generate_probe`, ask one
   controlled retest before feedback, then record it as a new attempt. Pattern
   proposals stay candidates until explicitly saved and validated.
5. Return the evidence ids, diagnosis, highest-impact next step, and what is
   still unverified. Sync memory only after a meaningful completed session.

Separate careless execution from missing conditions, concept confusion, and
method gaps. Never convert a score, a review count, or a single correct answer
into a mastery claim.
<!-- prompt-context:end -->

## Diagnosis Payload Shape

Each non-empty `diagnoses` item must be an object with non-empty `kind` and
observed `evidence`, never a string label. `kind` is a short, stable category
such as `condition_missed` or `concept_confusion`; `evidence` quotes the
specific observed response or reasoning that supports it. An optional `concept`
names the concept most directly implicated. Use `[]` when no specific diagnosis
is supported by what was actually observed — an empty list is always better
than a guess.

## Choosing The Right Record

Shorthand above maps onto the persistence tool: `attempt.record` and
`review.weekly_report` are
`study_activity(resource="attempt", action="record", ...)` and
`study_activity(resource="review", action="weekly_report", ...)`. Existing
evidence is read back the same way, through `study_activity`, before any
`study_coach` analysis of that scope.

- `attempt` is the immutable evidence unit: one observed answer, its result,
  and its diagnoses. Record it first, before any downstream artifact.
- `error` is durable remediation state for a concrete failure worth revisiting.
  Not every wrong answer earns one; an attempt already carries the evidence.
- `review.submit` is the graded-review path. It stores the attempt and advances
  spacing atomically, so do not also call `attempt.record` for the same answer.
- `review.create_task` is a commitment. It belongs to a follow-up the learner
  accepted, not to every recommendation the analysis produced.
- Pattern proposals from `study_coach.propose_pattern` stay candidates until
  they are explicitly saved and validated. Never auto-apply one.

## Reporting A Diagnosis

Close with the evidence ids the diagnosis rests on, the diagnosis itself, the
single highest-impact next step, and an explicit statement of what remains
unverified. Naming the unverified part is what keeps a diagnosis honest: the
scope only covers dimensions that were actually observed, and a summary from
`study_coach` inherits that same limit.

Sync memory only after a meaningful completed session, so that stored context
reflects demonstrated work rather than an in-progress conversation.
