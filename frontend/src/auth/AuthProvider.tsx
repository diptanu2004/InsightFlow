import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type ReactNode } from 'react'

import { postJson, request } from '../api/client'
import type { MeOut, TokenPair } from '../api/types'
import { AuthContext, type AuthContextValue } from './context'
import { clearTokens, getRefreshToken, onAuthChange, setTokens } from './tokens'

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()

  // Token presence is React state, not a localStorage read during render: the `me` query's
  // `enabled` flag has to change through a re-render to take effect, and the API client can clear
  // tokens from outside React entirely (a failed refresh mid-request).
  const [hasToken, setHasToken] = useState(() => getRefreshToken() !== null)

  useEffect(
    () =>
      onAuthChange(() => {
        const present = getRefreshToken() !== null
        setHasToken(present)
        // Drop the cached identity too -- otherwise the shell keeps rendering a logged-in user
        // whose every subsequent request 401s.
        if (!present) queryClient.removeQueries({ queryKey: ['me'] })
      }),
    [queryClient],
  )

  // `/auth/me` is the single source of truth for identity -- the JWT is never decoded here. A
  // stored token the backend rejects (expired past refresh, revoked, deleted user) resolves to
  // null, which the router reads as logged out.
  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => request<MeOut>('/auth/me'),
    enabled: hasToken,
    staleTime: Infinity,
  })

  async function authenticate(path: '/auth/login' | '/auth/register', email: string, password: string) {
    const pair = await postJson<TokenPair>(path, { email, password })
    setTokens(pair.access_token, pair.refresh_token)
    // Explicit refetch rather than relying on `enabled` flipping: if `me` is already in an error
    // state from a previous attempt (backend was down, say), enabling alone won't retry it since
    // global retry is off.
    await queryClient.invalidateQueries({ queryKey: ['me'] })
  }

  const value: AuthContextValue = {
    user: me.data ?? null,
    isResolving: me.isLoading,
    login: (email, password) => authenticate('/auth/login', email, password),
    register: (email, password) => authenticate('/auth/register', email, password),
    logout: async () => {
      const refreshToken = getRefreshToken()
      if (refreshToken) {
        // Revoke server-side so the refresh token can't outlive the session, but a failure here
        // must not trap the user in a logged-in UI -- local state is cleared either way.
        await postJson('/auth/logout', { refresh_token: refreshToken }).catch(() => undefined)
      }
      clearTokens()
      queryClient.clear()
    },
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
