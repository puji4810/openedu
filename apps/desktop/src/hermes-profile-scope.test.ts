import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  checkHermesUpdate,
  getActionStatus,
  getElevenLabsVoices,
  getMemoryProviderConfig,
  getStatus,
  getStudySettings,
  restartGateway,
  saveMemoryProviderConfig,
  setApiRequestProfile,
  speakText,
  transcribeAudio,
  updateHermes,
  updateStudySettings
} from './hermes'

// Contract: every backend-targeted action helper must carry the active gateway
// profile, so a multi-profile / global-remote user's restart, status poll, and
// update hit the backend they're actually on — not the primary/default. The
// System-panel "restart does nothing" bug was these helpers dropping it.
describe('backend action helpers are profile-scoped', () => {
  const api = vi.fn(async (_req: { path: string; profile?: string }) => ({}) as never)

  beforeEach(() => {
    ;(window as { hermesDesktop?: unknown }).hermesDesktop = { api }
    api.mockClear()
  })

  afterEach(() => {
    setApiRequestProfile(null)
    delete (window as { hermesDesktop?: unknown }).hermesDesktop
  })

  const lastProfile = () => api.mock.calls.at(-1)?.[0].profile

  it('omits profile when none is active (single-profile users unaffected)', () => {
    void getStatus()
    expect(lastProfile()).toBeUndefined()
  })

  it('forwards the active profile to memory provider config calls', () => {
    setApiRequestProfile('coder')

    void getMemoryProviderConfig('honcho')
    void saveMemoryProviderConfig('honcho', { workspace: 'w' })

    for (const call of api.mock.calls) {
      expect(call[0].profile).toBe('coder')
    }
  })

  it('forwards the active profile to every backend action', () => {
    setApiRequestProfile('coder')

    void getStatus()
    void restartGateway()
    void updateHermes()
    void checkHermesUpdate()
    void getActionStatus('gateway-restart')

    for (const call of api.mock.calls) {
      expect(call[0].profile).toBe('coder')
    }
  })

  // Audio endpoints (transcribe / speak / voices) write to the active
  // profile's config in the settings UI but historically called the backend
  // without a profile scope, so playback used the default profile's TTS/voice
  // config instead of the active one (#53441).
  it('forwards the active profile to audio endpoints', () => {
    setApiRequestProfile('jarvis')

    void transcribeAudio('data:audio/webm;base64,AAAA', 'audio/webm')
    void speakText('hello')
    void getElevenLabsVoices()

    expect(api.mock.calls).toHaveLength(3)

    for (const call of api.mock.calls) {
      expect(call[0].profile).toBe('jarvis')
    }
  })
})

describe('StudyOS settings are profile-scoped', () => {
  const vaults = new Map<string, string>()

  const api = vi.fn(
    async (req: { body?: { vault_path?: string }; method?: string; path: string; profile?: string }) => {
      const profile = req.profile ?? 'default'

      if (req.method === 'PUT' && req.body?.vault_path) {
        vaults.set(profile, req.body.vault_path)
      }

      return {
        configured: vaults.has(profile),
        vault_path: vaults.get(profile) ?? null
      } as never
    }
  )

  beforeEach(() => {
    ;(window as { hermesDesktop?: unknown }).hermesDesktop = { api }
    vaults.clear()
    api.mockClear()
  })

  afterEach(() => {
    setApiRequestProfile(null)
    delete (window as { hermesDesktop?: unknown }).hermesDesktop
  })

  it('keeps vault changes isolated when switching Hermes profiles', async () => {
    setApiRequestProfile('study-a')
    await updateStudySettings('/vaults/study-a')

    setApiRequestProfile('study-b')
    await updateStudySettings('/vaults/study-b')

    setApiRequestProfile('study-a')
    const studyA = await getStudySettings()
    setApiRequestProfile('study-b')
    const studyB = await getStudySettings()

    expect(studyA.vault_path).toBe('/vaults/study-a')
    expect(studyB.vault_path).toBe('/vaults/study-b')
  })
})
