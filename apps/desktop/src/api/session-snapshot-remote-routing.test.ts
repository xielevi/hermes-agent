import { beforeEach, expect, it, vi } from 'vitest'

import { routeRemoteSessionRequest } from '../../electron/profile-session-routing'

import type * as ClientApi from './client'

vi.mock('@/lib/legacy-session-owner-backfill', () => ({ maybeBackfillLegacySessionOwners: vi.fn() }))
vi.mock('@/store/transcript-tail', () => ({ recordTranscriptTail: vi.fn() }))
vi.mock('./client', async importOriginal => ({
  ...await importOriginal<typeof ClientApi>(), hermesApi: vi.fn()
}))
import { hermesApi, setApiRequestConnection, setApiRequestProfile } from './client'
import { setSessionArchived } from './sessions'

beforeEach(() => {
  vi.mocked(hermesApi).mockReset()
  setApiRequestConnection(null)
  setApiRequestProfile('remote-work')
})

it.each(['global', 'profile-override'])('archive fences against the remote owner in legacy %s mode', async mode => {
  const calls: Array<{ host: string; path: string; body: unknown }> = []
  // Deliberately different stores: a local snapshot must never authorize the remote mutation.
  vi.mocked(hermesApi).mockImplementation(async request => {
    const receive = async (path: string, body?: unknown) => {
      calls.push({ host: 'remote', path, body })

      if (body === undefined) {
        return { exists: true, runtime_revision: 19, runtime_generation: 4 }
      }

      expect(body).toMatchObject({ archived: true, request_id: expect.any(String),
        expected_revision: 19, expected_generation: 4, profile: mode === 'global' ? 'remote-work' : 'work' })

      return { ok: true }
    }

    const result = await routeRemoteSessionRequest(request, {
      remoteOwnerProfileForSession: async () => null,
      profileHasRemoteOverride: () => mode === 'profile-override',
      ownerScopeForProfile: () => 'work',
      globalRemoteActive: () => mode === 'global',
      fetchJsonForProfile: async (_profile, path) => receive(path),
      requestJsonForProfile: async (_profile, path, _method, body) => receive(path, body)
    })

    if (result === undefined) {
      throw new Error('404: {"detail":"No such API endpoint"}')
    }

    return result as never
  })

  await setSessionArchived(`legacy-${mode}`, true, 'remote-work')
  expect(calls).toHaveLength(2)
  expect(calls.map(call => call.host)).toEqual(['remote', 'remote'])
  expect(calls.map(call => new URL(call.path, 'http://desktop.test').searchParams.get('profile') || (call.body as { profile?: string })?.profile))
    .toEqual(mode === 'global' ? ['remote-work', 'remote-work'] : ['work', 'work'])
})
