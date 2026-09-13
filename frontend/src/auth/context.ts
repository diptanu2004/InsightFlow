/**
 * The auth context and its hook, kept out of AuthProvider.tsx so that file exports only a
 * component -- a module exporting both breaks React Fast Refresh.
 */
import { createContext, useContext } from 'react'

import type { MeOut } from '../api/types'

export type AuthContextValue = {
  user: MeOut | null
  /** True only while a stored token is still being resolved against the backend. */
  isResolving: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (context === null) throw new Error('useAuth must be used inside <AuthProvider>')
  return context
}
