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
## Layered Organization

Organize into Vault notes only; never mutate system prompts or skill files.

Choose the lightest layer that satisfies the request:

1. **Capture** preserves enough context for later work with minimal discovery.
2. **Synthesize** produces the smallest reusable note change after targeted
   discovery.
3. **Curate** improves collection-wide coherence through broader analysis.

Choose by requested outcome, scope, and reversibility; when uncertain, prefer
the least mutating layer. A request for a persisted note authorizes that scoped
write, while an analysis request does not. `note.save` validates links and
saves atomically; reserve `note.validate` for previews or higher-risk batches.

Report source, concepts/patterns found, files changed, and unresolved
ambiguity. Organizing a concept never makes it mastered.

Create a pattern only when it has a stable recognition signal, required
conditions, and a reusable solution routine. Prefer links to existing Box notes
over copying their explanation.
<!-- prompt-context:end -->

## Note Write Mechanics

The loaded operation guide carries the call shapes, while backend validation
owns these mechanics. They live outside the prompt-context region as reference.

- On a requested write, assemble complete `{path, content}` objects and call
  `study_activity(resource="note", action="save", data={"notes":[...]})`.
  Never use a generic file-writing tool for Vault notes.
- Validation follows WikiLinks recursively through both the batch and existing
  notes. If it reports a missing target, add a substantive note for that target
  to the same batch — and resolve any links introduced by that new note — until
  `missing` is empty. A batch that still has missing targets is rejected.
- `note.save` performs the same recursive validation before its atomic write.
  Use `note.validate` as a non-writing preview for a large or high-risk batch.
  `overwrite:true` permits replacement, so set it only for an intentional
  update and read that updated note back afterwards.

## Vault-Wide Checks

Use `note.audit` when the user asks to check the wider Vault. It reports
WikiLink integrity across notes that already exist and is independent of the
batch just written, so it is not part of a routine organizing pass.
