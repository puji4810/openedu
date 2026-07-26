---
name: study-teach
description: Teach through StudyOS learning records.
platforms: [linux, macos, windows]
---

# StudyOS Teach

Use for an explicit concept or skill lesson. Before teaching, call
`study_activity(resource="prompt_context", action="load",
data={"intent":"teaching"})` to load the loop below, then inspect the relevant
`learning_record` entries and source notes. Never mutate system prompts: the
loaded fragment is turn-local context, not prompt content to rewrite.

<!-- prompt-context:begin -->
## Teach-Test-Record

1. Inspect relevant `learning_record` entries and source notes first. Set one
   small objective grounded in the learner's request and that evidence, and
   state any prerequisite gap before teaching dependent material.
2. Explain only what the objective needs, from trusted project sources rather
   than unsupported claims.
3. Ask for retrieval or a small application before feedback; vary one thing at
   a time when checking transfer. Do not reveal the answer before a genuine
   attempt.
4. Record only after the learner responds. Inside an active Session use
   `study_coach.advance` so the Session advances; otherwise use
   `study_activity(resource="attempt", action="record")` with result, concepts,
   transfer level, evaluator provenance, and a specific diagnosis. Create a
   `learning_record` only for demonstrated, durable progress, with concrete
   evidence and source links.
5. Report what remains unverified. Delivering an explanation never justifies a
   VisualLesson, a learning record, or a mastery claim.
<!-- prompt-context:end -->

## Recording Details

`study_coach.advance` carries evaluator and assistance provenance on the
observation payload. The `observation` tool schema lists the exact fields and
which of them are required, so follow it rather than guessing a shape here.

Use `diagnoses: []` when no specific diagnosis is supported. Otherwise every
item is an object with a non-empty `kind` and the observed `evidence`, never a
bare string; the `diagnoses` schema states the same rule and carries a worked
example.

## Sources That Count

"Trusted project sources" means files, papers, commands, or notes that actually
exist in the project, not recalled claims. Name the specific source an
explanation came from so the learner can re-check it, and say plainly which
parts of the lesson remain unverified.

## Handoffs

Hand a reusable note to `study-organize`, a visual need to `study-lesson`, and
a strategic tradeoff to `study-grill`. The `study-os` router skill carries this
same routing table, so it is stated once there for the loaded context and
repeated here only for readers of this skill.

## Editing This Skill

Only the text between the `prompt-context` begin and end HTML comment markers
is inlined by `prompt_context.load` and counted against the teaching budget.
Everything outside those markers is free: put explanation, examples, and
rationale here rather than shrinking the loop above. Do not write either marker
string anywhere else in this file — a second pair would be read as a second
fragment region.
