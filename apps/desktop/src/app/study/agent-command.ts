import type { StudyIntervention } from '@/types/hermes'

interface StudyInterventionSelection {
  assistance_level: string
  capability: string
  duration_minutes: number
  evidence_attempt_ids: string[]
  evidence_target: string
  intervention_id: string
  kind: string
  objective_id: string
  project_id: string
  requires_evaluator: boolean
  success_criteria: string[]
}

/** Build the skill command used by every StudyOS intervention launch surface. */
export function buildStudyInterventionCommand(projectId: string, intervention: StudyIntervention): string {
  const selection: StudyInterventionSelection = {
    assistance_level: intervention.recommended_activity.assistance_level,
    capability: intervention.capability,
    duration_minutes: intervention.recommended_activity.duration_minutes,
    evidence_attempt_ids: intervention.evidence_attempt_ids,
    evidence_target: intervention.recommended_activity.evidence_target,
    intervention_id: intervention.intervention_id,
    kind: intervention.kind,
    objective_id: intervention.objective_id,
    project_id: projectId,
    requires_evaluator: intervention.recommended_activity.requires_evaluator,
    success_criteria: intervention.recommended_activity.success_criteria
  }

  return [
    '/study-os Execute the learner-selected StudyOS intervention below.',
    '',
    'Selected intervention:',
    JSON.stringify(selection, null, 2),
    '',
    'First read project.status and re-run project-scope study_coach.prioritize.',
    'Verify that intervention_id is still current; if it is stale, explain the change and stop instead of silently substituting another intervention.',
    'Start an explicit learning Session for this activity. Present one learner activity at a time and wait for the learner response before feedback.',
    'After each response, evaluate it and call study_coach.advance with explicit evaluator and assistance provenance.',
    'Do not save or decide a Plan Proposal and do not mutate a Schedule.'
  ].join('\n')
}
