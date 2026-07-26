import { describe, expect, it } from 'vitest'

import { parseSlashCommand } from '@/lib/chat-runtime'
import type { StudyIntervention } from '@/types/hermes'

import { buildStudyInterventionCommand } from './agent-command'

describe('buildStudyInterventionCommand', () => {
  it('dispatches /study-os with the complete learner-selected intervention', () => {
    const intervention: StudyIntervention = {
      intervention_id: 'iv-independent-recall',
      objective_id: 'project-readiness',
      capability: 'Demonstrate readiness for 数一考研数学.',
      kind: 'independence_probe',
      evidence_dimension: 'recall',
      priority_score: 60,
      priority_band: 'medium',
      reasons: ['Successful recall evidence is not independently verified.'],
      evidence_attempt_ids: ['att-recall'],
      recommended_activity: {
        activity_kind: 'independence_probe',
        evidence_target: 'recall',
        assistance_level: 'independent',
        duration_minutes: 30,
        requires_evaluator: true,
        success_criteria: ['Produce evaluator-provenanced evidence without hidden assistance.']
      }
    }

    const { arg, name } = parseSlashCommand(buildStudyInterventionCommand('kaoyan-math-2026', intervention))
    const selectionMatch = arg.match(/Selected intervention:\n(\{[\s\S]*?\})\n\nFirst read/)

    expect(name).toBe('study-os')
    expect(selectionMatch).not.toBeNull()
    expect(JSON.parse(selectionMatch?.[1] ?? '{}')).toEqual({
      assistance_level: 'independent',
      capability: 'Demonstrate readiness for 数一考研数学.',
      duration_minutes: 30,
      evidence_attempt_ids: ['att-recall'],
      evidence_target: 'recall',
      intervention_id: 'iv-independent-recall',
      kind: 'independence_probe',
      objective_id: 'project-readiness',
      project_id: 'kaoyan-math-2026',
      requires_evaluator: true,
      success_criteria: ['Produce evaluator-provenanced evidence without hidden assistance.']
    })
    expect(arg).toContain('re-run project-scope study_coach.prioritize')
    expect(arg).toContain('call study_coach.advance with explicit evaluator and assistance provenance')
    expect(arg).toContain('do not mutate a Schedule')
  })
})
