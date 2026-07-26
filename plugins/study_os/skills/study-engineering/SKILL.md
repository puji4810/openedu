---
name: study-engineering
description: Guide engineering and skill learning with StudyOS.
---

# StudyOS Engineering Domain Pack

Use only with `domain_pack:"engineering.v1"`. Load the active workflow intent
with `study_activity(resource="prompt_context", action="load")`;
never mutate system prompts.

Only the region between the `prompt-context` markers below is inlined into the
prompt fragment for this domain pack. Everything after the closing marker is
reference material for `skill_view` and for maintainers: it costs no prompt
budget, so it may be as detailed as it needs to be.

<!-- prompt-context:begin -->
## Evidence-Driven Engineering Learning

1. Identify an `engineering-repo`, `skill-vault`, or `hybrid` workspace. Read
   the real code, docs, benchmark, command output, or paper before explaining.
2. Define an observable skill: trace a call path, explain an invariant,
   reproduce a benchmark, implement a change, or compare designs.
3. Perform its ActivitySpec in the workspace; anchor `advance` to a real
   command, test, trace, benchmark, diff, or file.
4. Create a concept note only when it blocks understanding, recurs across work,
   or will be reused. Every durable note needs a source anchor.
5. Separate unverified claims from observed performance.

Avoid exam-vault defaults such as daily dashboards, Anki export, and full error
systems unless the user asks. Prefer lightweight, maintained records over a
large taxonomy of notes.
<!-- prompt-context:end -->

## Session Lifecycle Behind the Steps

The base `study-os` router fragment and the `study_coach` schema already carry
the Session lifecycle, so the marked region does not restate it. The mapping is
recorded here for maintainers.

- Step 2 opens the Session: `study_coach(action="start", data={...})` with a
  contract carrying `objective`, `assistance_level`, `time_budget_minutes`, and
  `evidence_targets`. All four are required by the schema.
- Step 3 records work: `study_coach(action="advance", ...)` with `evaluator`
  provenance and a source anchor. Add `artifact_refs` for the execution and
  transfer dimensions — that is where the engineering artifact vocabulary in
  the marked region applies, since the schema itself accepts free strings.
- Step 5 inspects and closes: `study_coach(action="snapshot", ...)` to choose
  the next probe, and `study_coach(action="finish", ...)` when stopping.

## Source Anchors

`source_anchors[].kind` is a closed enum (`file`, `paper`, `book`, `web`,
`dataset`, `command`, `commit`, `note`, `other`), and the model sees it on the
tool schema. In this domain the anchor is usually a file, a symbol inside a
file, a command, a benchmark run, or a paper; put the precise position — line
range, symbol name, benchmark case, commit — in `locator` or `version` so a
later Session can reopen the same evidence instead of re-deriving it.

A concept note without an anchor is an assertion, not a record. That rule stays
in the marked region because nothing in the tool schemas enforces it.

## Workspace Shapes

`workspace_type` is a free string on the project manifest; this pack expects
one of three shapes, and `plugins/study_os/domain_packs/engineering.py` seeds
the general default of `skill-vault`.

| Shape | What it holds | Where evidence comes from |
| --- | --- | --- |
| `engineering-repo` | A real code tree under study | Call paths, tests, diffs, command output, benchmarks |
| `skill-vault` | Notes about a skill with no single repo | Papers, docs, reproductions, worked exercises |
| `hybrid` | Notes plus one or more repos | Either, but every durable note still points at a source |

Identify the shape before the first explanation. It decides what counts as an
observable skill in step 2 and what a legitimate `advance` anchor looks like in
step 3.

## Why the Exam Defaults Are Off

The 考研 pack's habits — daily dashboards, Anki export, a full 错题 system —
assume a fixed syllabus and a dated exam. Engineering and skill work has
neither: the target moves, and a heavy record system decays into unmaintained
notes faster than it repays the setup. Build one only when the user asks for
it, and prefer a small set of maintained records with live anchors over a large
taxonomy.

## Pack Defaults

Defined by `plugins/study_os/domain_packs/engineering.py`, which reuses the
general project defaults and overrides `domain_pack` to `engineering.v1`. The
intervention duration is 45 minutes, longer than the 考研 pack's 30, because a
useful engineering probe usually includes reading real source or running
something. Activities are shaped by `EngineeringActivityAdapter`.
