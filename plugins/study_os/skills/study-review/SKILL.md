---
name: study-review
description: Run flexible StudyOS spaced-repetition reviews.
---

# StudyOS Review

Use for 复习 and 艾宾浩斯 review. Load context with `study_activity`
`prompt_context.load` at intent `reviewing`; never mutate system prompts.

<!-- prompt-context:begin -->
## Queue

Call `review.due`; default due/priority/10. Respect selectors and `limit`. On
shortfall report counts; if empty offer one relaxation, never broaden silently.

## Loop

1. Hide the solution and present one coherent retrieval task at a time.
2. Let the learner determine when their response is complete. Completion,
   correctness, and verification strength are independent judgments.
3. Stopping closes future work, not prior evidence. Evaluate the accumulated
   response against the learning objective, not the percentage of requested
   steps completed.
4. When the response supports a result, call `review.submit` once. Leave it
   unrecorded only when no evaluable response exists or the learner explicitly
   discards it. Offer the next item as an option, not an obligation.
<!-- prompt-context:end -->

## Queue selectors

`review.due` selectors combine with AND:

- Scope: `notes`, `subjects`, YAML tags, `concepts`
- Exclusion: `exclude_paths:[".opencode","archive"]`
- Level: `difficulties`, `review_levels`, `min|max_review_level`
- State: `review_state` due/new/reviewed/all; `match` any/all
- Order: `sort` priority, oldest, newest, difficulty_asc/desc, title

Hidden directories are excluded by default. `limit` caps the queue; it is
never a target to fill. On shortfall report `count` and `available_count`.

## Review levels

Do not ask for confidence or a review level; the backend assigns it:
incorrect → Lv.1, partial → Lv.2, correct → at least Lv.3, then Lv.4/Lv.5
after repeated correct reviews.

## Recording

`review.submit` saves evidence and spacing atomically, so one call finishes a
graded review. It is a completion action, never a checkpoint action. Never
also call `attempt.record` or `review.record`, start or advance a Learning
Session, or pass its returned `attempt_id` to `study_coach.advance`. Diagnoses
are objects, never strings; use `[]` when the response supports none. Ending
before every requested step is complete does not discard accumulated evidence.
Preserve spacing only when no evaluable response exists or the learner
explicitly asks not to record it.

## Interaction contract

Keep one response in progress until the learner treats it as complete.
`partial` describes quality, not interaction state. Do not grade before
completion or demand another confirmation afterwards. Judge the completed
response against the learning objective: stopping may still support a correct,
partial, or incorrect result. Closing future steps never erases prior evidence.
