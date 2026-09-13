/**
 * Token storage. Kept in localStorage rather than memory so a page reload doesn't log the user
 * out, and rather than cookies because the backend authenticates via an `Authorization: Bearer`
 * header, not a session cookie -- so there's no CSRF surface to defend with SameSite.
 *
 * Both tokens live here because the refresh token is the one credential that must survive a
 * reload; the access token is short-lived (15 min by default) and re-derivable from it.
 */
const ACCESS_KEY = 'insightflow.access_token'
const REFRESH_KEY = 'insightflow.refresh_token'

type Listener = () => void
const listeners = new Set<Listener>()

/** Notifies React when tokens are cleared out from under it -- e.g. a failed refresh mid-request. */
export function onAuthChange(listener: Listener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function notify(): void {
  for (const listener of listeners) listener()
}

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_KEY)
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY)
}

export function setTokens(accessToken: string, refreshToken: string): void {
  localStorage.setItem(ACCESS_KEY, accessToken)
  localStorage.setItem(REFRESH_KEY, refreshToken)
  notify()
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_KEY)
  localStorage.removeItem(REFRESH_KEY)
  notify()
}
