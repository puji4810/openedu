---
name: study-review
description: Run flexible StudyOS spaced-repetition reviews.
platforms: [linux, macos, windows]
---

# StudyOS Review

Use for 复习 and 艾宾浩斯 review. Load context with
`study_activity(resource="prompt_context", action="load",
data={"intent":"reviewing"})`; never mutate system prompts.

## Queue

Call `study_activity(resource="review", action="due", data=...)`; default data
is `{"review_state":"due","sort":"priority","limit":10}`. For “YAML tag X,
N questions”, add `tags:["X"]`. `limit` is a cap; on shortfall report `count`
and `available_count`. Never broaden selectors to fill it.

Selectors combine with AND:

- Scope: `notes:["course/examples/a.md"]`, `subjects`, YAML `tags`, `concepts`
- Exclusion: `exclude_paths:[".opencode","archive"]`
- Level: `difficulties`, `review_levels`, `min_review_level`, `max_review_level`
- State: `review_state` due/new/reviewed/all; `match` any/all
- Order: priority, oldest, newest, difficulty asc/desc, title

Hidden directories are excluded. If ambiguous, ask one scope question. If
empty, report filters and offer one relaxation; never switch to all.

## Loop

1. Read one note via `note.read` with `include_body:true`. Ask the question;
   hide the solution; give and count hints only on request.
2. Grade as `correct`, `partial`, or `incorrect`; missing a required condition
   is not fully correct. Explain the gap and give a concise correction.
   Do not ask for confidence or a review level. The backend assigns it automatically:
   incorrect → Lv.1, partial → Lv.2, correct → at least Lv.3 and then Lv.4/Lv.5
   after repeated correct reviews.
3. Record exactly once:

   `study_activity(resource="review", action="submit", project_id="...",
   data={"note":"...","response":"...","result":"correct|partial|incorrect",
   "duration_seconds":0,"hints_used":0,"diagnoses":[]})`

   This saves evidence and spacing atomically. Do not also call `attempt.record`
   or `review.record`. Diagnoses are objects, never strings.
4. Continue only after success. On failure, say it was not recorded and retry;
   do not move on or invent a count.
5. End with result counts, weak concepts, and one next action. Call
   `memory.sync` when available.
