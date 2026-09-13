/**
 * Typed fetch wrapper: injects the access token, and transparently refreshes it once on a 401.
 *
 * Defaults to the `/api` prefix the Vite dev server proxies to the backend with the prefix
 * stripped -- see vite.config.ts for why the API isn't called at the root.
 */
import { clearTokens, getAccessToken, getRefreshToken, setTokens } from '../auth/tokens'

const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? '/api'

export class ApiError extends Error {
  // Declared and assigned explicitly rather than as constructor parameter properties: the
  // template enables `erasableSyntaxOnly`, so TS must compile away by pure type erasure.
  readonly status: number
  readonly detail: string

  constructor(status: number, detail: string) {
    super(`HTTP ${status}: ${detail}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

/** FastAPI puts a string in `detail` for HTTPException and a list of objects for a 422. */
function readDetail(body: unknown, status: number): string {
  if (typeof body === 'object' && body !== null && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail
        .map((e) => {
          const { loc, msg } = e as { loc?: unknown[]; msg?: string }
          const where = Array.isArray(loc) ? loc.join('.') : ''
          return where ? `${where}: ${msg ?? ''}` : (msg ?? '')
        })
        .join('; ')
    }
  }
  return `request failed with status ${status}`
}

/**
 * Routes where a 401 is a real answer (bad credentials, dead refresh token) rather than an
 * expired access token, so retrying after a refresh would be wrong or would recurse.
 *
 * `/auth/me` is deliberately NOT here even though it shares the prefix: it's an ordinary
 * authenticated route, and it's the one fired on every app load, making it the single most
 * important place for refresh-on-401 to work. A prefix test over `/auth/` instead of this exact
 * set silently excluded it, so any session older than the access-token TTL bounced to the login
 * page instead of refreshing.
 */
const NO_REFRESH_PATHS = new Set(['/auth/login', '/auth/register', '/auth/refresh', '/auth/logout'])

let refreshInFlight: Promise<boolean> | null = null

/**
 * Exchange the refresh token for a new pair. Single-flighted, and that is a correctness
 * requirement rather than an optimization: the backend rotates refresh tokens and revokes the
 * presented one immediately (auth/jwt.py, routers/auth.py `refresh`). Two concurrent 401s each
 * calling this would mean the second request presents an already-revoked token, gets a 401, and
 * logs the user out for no reason. Verified against the real backend: replaying a used refresh
 * token returns 401 "invalid, expired, or revoked refresh token".
 */
function refreshAccessToken(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight

  refreshInFlight = (async (): Promise<boolean> => {
    const refreshToken = getRefreshToken()
    if (!refreshToken) return false

    const response = await fetch(`${BASE_URL}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })
    if (!response.ok) return false

    const pair = (await response.json()) as { access_token: string; refresh_token: string }
    setTokens(pair.access_token, pair.refresh_token)
    return true
  })()
    // A network failure rejects rather than resolving false; treat it as "not refreshed" but
    // leave the tokens alone, since an offline blip shouldn't destroy a valid session.
    .catch(() => false)
    .finally(() => {
      refreshInFlight = null
    })

  return refreshInFlight
}

function send(path: string, init?: RequestInit): Promise<Response> {
  const token = getAccessToken()
  return fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  })
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response = await send(path, init)

  if (response.status === 401 && !NO_REFRESH_PATHS.has(path)) {
    if (await refreshAccessToken()) {
      response = await send(path, init)
    } else {
      clearTokens()
    }
  }

  if (response.status === 204) return undefined as T

  // A proxy error or an unhandled backend exception can return HTML, not JSON -- surface the
  // status rather than throwing an opaque JSON.parse SyntaxError from inside the data layer.
  const text = await response.text()
  let body: unknown = null
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      if (!response.ok) throw new ApiError(response.status, text.slice(0, 200))
      throw new ApiError(response.status, 'expected JSON but the response was not parseable')
    }
  }

  if (!response.ok) throw new ApiError(response.status, readDetail(body, response.status))
  return body as T
}

export function postJson<T>(path: string, payload: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}
