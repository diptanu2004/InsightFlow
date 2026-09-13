import { useState } from 'react'
import { Navigate } from 'react-router-dom'

import { ApiError } from '../api/client'
import { useAuth } from '../auth/context'

export default function LoginPage() {
  const { login, register, user } = useAuth()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await (mode === 'login' ? login(email, password) : register(email, password))
    } catch (e) {
      // The auth routes are IP-rate-limited (10/min by default), and a 429 there looks nothing
      // like a credential problem -- say so instead of letting it read as "wrong password".
      if (e instanceof ApiError && e.status === 429) {
        setError('Too many attempts. Wait a minute and try again.')
      } else {
        setError(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Something went wrong.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  // Covers both "just signed in" and "already signed in, navigated to /login by hand" -- the
  // form itself never navigates, it just sets tokens and lets identity resolve.
  if (user) return <Navigate to="/" replace />

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 p-6">
      <div className="w-full max-w-sm">
        <h1 className="text-center text-2xl font-semibold text-slate-900">InsightFlow</h1>
        <p className="mt-1 text-center text-sm text-slate-500">
          {mode === 'login' ? 'Sign in to your workspace' : 'Create an account'}
        </p>

        <form
          onSubmit={onSubmit}
          className="mt-6 space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-sm"
        >
          <div>
            <label htmlFor="email" className="block text-sm font-medium text-slate-700">
              Email
            </label>
            <input
              id="email"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-500"
            />
          </div>

          <div>
            <label htmlFor="password" className="block text-sm font-medium text-slate-700">
              Password
            </label>
            <input
              id="password"
              type="password"
              required
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-500"
            />
          </div>

          {error && (
            <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
          >
            {submitting ? 'Working…' : mode === 'login' ? 'Sign in' : 'Create account'}
          </button>
        </form>

        <p className="mt-4 text-center text-sm text-slate-500">
          {mode === 'login' ? "Don't have an account?" : 'Already have an account?'}{' '}
          <button
            type="button"
            onClick={() => {
              setMode(mode === 'login' ? 'register' : 'login')
              setError(null)
            }}
            className="font-medium text-slate-900 underline"
          >
            {mode === 'login' ? 'Create one' : 'Sign in'}
          </button>
        </p>
      </div>
    </main>
  )
}
