---
name: study-organize
description: Organize problems into StudyOS notes.
platforms: [linux, macos, windows]
---

# StudyOS Organize

Use when the user asks to 整理, analyze, or turn a problem into notes. Call
`study_activity(resource="prompt_context", action="load",
data={"intent":"organizing"})`; never mutate system prompts.

<!-- prompt-context:begin -->
## Evidence-First Organization

Organize into Vault notes only; never mutate system prompts or skill files.

1. Extract candidate concepts, conditions, reusable triggers, solution
   invariants, and likely failure points from the problem source.
2. Search before writing: `note.list` for concept/pattern matches, read the
   closest notes, then `note.extract` for links and aliases.
3. Decide explicitly: reuse an existing concept, improve one incomplete note,
   create a concept note, create a pattern note, or keep this as a standalone
   explanation. Do not create `/examples/` unless the user asks to add a
   reviewable problem.
4. Write only on request. Persist the unchanged validated batch; set
   `overwrite:true` only for an intentional update, then read the saved notes
   back to verify path, type, concepts, and links.
5. Report source, concepts/patterns found, files changed, and unresolved
   ambiguity. Organizing a concept never makes it mastered.

Create a pattern only when it has a stable recognition signal, required
conditions, and a reusable solution routine. Prefer links to existing Box notes
over copying their explanation.
<!-- prompt-context:end -->

## Note Write Mechanics

The `study_activity` tool schema already states these mechanics to every model
that has the study toolset enabled, so they live outside the prompt-context
region rather than being repeated in the loaded fragment. They still apply.

- On a requested write, assemble complete `{path, content}` objects and call
  `study_activity(resource="note", action="validate", data={"notes":[...]})`
  before persisting anything. Never use a generic file-writing tool for Vault
  notes.
- Validation follows WikiLinks recursively through both the batch and existing
  notes. If it reports a missing target, add a substantive note for that target
  to the same batch — and resolve any links introduced by that new note — until
  `missing` is empty. A batch that still has missing targets is rejected.
- Persist with `study_activity(resource="note", action="save")` and pass the
  validated batch unchanged. `overwrite:true` permits an item to replace an
  existing note, so set it only for an intentional update.
- After saving, read the notes back and confirm path, type, concepts, and links
  match what was intended.

## Vault-Wide Checks

Use `note.audit` when the user asks to check the wider Vault. It reports
WikiLink integrity across notes that already exist and is independent of the
batch just written, so it is not part of a routine organizing pass.
