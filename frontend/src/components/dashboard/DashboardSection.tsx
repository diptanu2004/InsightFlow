import { ApiError } from '../../api/client'
import { useGenerateDashboard } from '../../api/queries'
import DashboardView from './DashboardView'

/**
 * Keyed by dataset version in the parent, so a dashboard generated for one version never lingers
 * after a mapping review or re-upload makes another the active one.
 */
export default function DashboardSection({ projectId }: { projectId: string }) {
  const generate = useGenerateDashboard(projectId)

  const errorText = (() => {
    if (!generate.isError) return null
    const error = generate.error
    if (error instanceof ApiError && error.status === 503) return error.detail
    if (error instanceof ApiError && error.status === 422) return `This dataset can't support a dashboard: ${error.detail}`
    if (error instanceof ApiError && error.status === 429) return 'Too many AI requests for this project right now. Wait a minute and try again.'
    return error.message.replace(/^HTTP \d+: /, '')
  })()

  return (
    <section className="mt-10">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Dashboard</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            An AI planner picks what to show; every number is computed by the analytics engine, not the AI.
          </p>
        </div>
        <button
          type="button"
          onClick={() => generate.mutate()}
          disabled={generate.isPending}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
        >
          {generate.isPending ? 'Generating…' : generate.data ? 'Regenerate' : 'Generate dashboard'}
        </button>
      </div>

      {generate.isPending && !generate.data && (
        <p className="mt-4 text-sm text-slate-500">Planning the dashboard and computing each component — usually about ten seconds.</p>
      )}

      {errorText && (
        <p role="alert" className="mt-4 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          {errorText}
        </p>
      )}

      {generate.data && (
        // Keep the previous render visible, dimmed, while regenerating -- no layout jump.
        <div className={`mt-4 transition-opacity ${generate.isPending ? 'opacity-50' : ''}`}>
          <DashboardView dashboard={generate.data} />
        </div>
      )}
    </section>
  )
}
