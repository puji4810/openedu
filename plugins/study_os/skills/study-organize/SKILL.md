---
name: study-organize
description: Organize problems into StudyOS notes.
platforms: [linux, macos, windows]
---

# StudyOS Organize

Use when the user asks to 整理, analyze, or turn a problem into notes. Call
`study_activity(resource="prompt_context", action="load",
data={"intent":"organizing"})`; never mutate system prompts.

## Evidence-First Organization

1. Read the problem source. Extract candidate concepts, conditions, reusable
   triggers, solution invariants, and likely failure points.
2. Search before writing: call `study_activity(resource="note", action="list")`
   for concept/pattern matches, read the closest notes, then call `note.extract`
   for links and aliases.
3. Decide explicitly: reuse an existing concept, improve one incomplete note,
   create a concept note, create a pattern note, or keep this as a standalone
   explanation. Do not create `/examples/` unless the user asks to add a
   reviewable problem.
4. On a requested write, assemble complete `{path, content}` objects and call
   `study_activity(resource="note", action="validate", data={"notes":[...]})`.
   Never use a generic file-writing tool for Vault notes. Validation follows
   WikiLinks recursively through both the batch and existing notes. If it
   reports a missing target, add a substantive note for that target to the same
   batch (and resolve any links in that note) until `missing` is empty.
5. Persist the unchanged validated batch with `note.save`. Set `overwrite:true`
   only for an intentional update. Read saved notes back to verify path, type,
   concepts, and links; use `note.audit` when the user asks to check the wider
   Vault.
6. Summarize the source, concepts/patterns found, files changed, and unresolved
   ambiguity. Do not claim a concept is mastered just because it was organized.

Create a pattern only when it has a stable recognition signal, required
conditions, and a reusable solution routine. Prefer links to existing Box notes
over copying their explanation.
