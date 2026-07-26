---
name: study-review
description: Run flexible StudyOS spaced-repetition reviews.
platforms: [linux, macos, windows]
---

# StudyOS Review

Use for 复习 and 艾宾浩斯 review. Load context with `study_activity`
`prompt_context.load` at intent `reviewing`; never mutate system prompts.

<!-- prompt-context:begin -->
## Queue

Call `study_activity(resource="review", action="due", data=...)`; default data
is `{"review_state":"due","sort":"priority","limit":10}`. For “YAML tag X, N
questions”, add `tags:["X"]`. On shortfall report `count` and
`available_count`; never broaden selectors to fill `limit`. If ambiguous, ask
one scope question. If empty, report the filters and offer one relaxation;
never switch to all.

## Loop

1. Read one note via `note.read` with `include_body:true`. Ask the question;
   hide the solution; give and count hints only on request.
2. Grade `correct`, `partial`, or `incorrect`; missing a required condition is
   not fully correct. Explain the gap and give a concise correction.
   Do not ask for confidence or a review level — the backend assigns it.
3. Record exactly once, and never also call `attempt.record`:

   `study_activity(resource="review", action="submit", project_id="...",
   data={"note":"...","response":"...","result":"correct|partial|incorrect",
   "duration_seconds":0,"hints_used":0,"diagnoses":[]})`

4. Continue only after success. On failure, say it was not recorded and retry;
   do not move on or invent a count.
5. End with result counts, weak concepts, and one next action. Call
   `memory.sync` when available.
<!-- prompt-context:end -->

## Queue selectors

`review.due` selectors combine with AND:

- Scope: `notes`, `subjects`, YAML `tags`, `concepts`
- Exclusion: `exclude_paths:[".opencode","archive"]`
- Level: `difficulties`, `review_levels`, `min|max_review_level`
- State: `review_state` due/new/reviewed/all; `match` any/all
- Order: `sort` priority, oldest, newest, difficulty_asc/desc, title

Hidden directories are excluded by default. `limit` caps the queue; it is
never a target to fill.

## Review levels

The backend assigns the level, so never ask for a confidence rating:
incorrect → Lv.1, partial → Lv.2, correct → at least Lv.3, then Lv.4/Lv.5
after repeated correct reviews.

## Recording

`review.submit` saves evidence and spacing atomically, so one call finishes a
graded review. Never also call `attempt.record` or `review.record`. Diagnoses
are objects, never strings; use `[]` when the response supports none.
