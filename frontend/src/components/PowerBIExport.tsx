import { useState } from 'react'

import { ApiError, downloadFile } from '../api/client'

type Props = { projectId: string }

/**
 * Downloads this project's active dataset as a Power BI project (Phase 9).
 *
 * Deliberately not a react-query mutation: there is no server state to cache or invalidate, and the
 * result is a file the browser saves rather than anything this app renders. A plain handler with its
 * own pending/error state is the smaller honest thing.
 *
 * The warning below is not decoration. Every other read path in this app shows numbers the engine
 * computed, carrying caveats, truncation flags and a number-grounding check. This one hands over the
 * rows themselves, and once the zip is on a disk the project's roles no longer apply to it — so the
 * person clicking should know that before they click, not from a README afterwards. Shown only to
 * Analysts and above, matching the route.
 */
export default function PowerBIExport({ projectId }: Props) {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleClick() {
    setPending(true)
    setError(null)
    try {
      const { blob, filename } = await downloadFile(`/projects/${projectId}/powerbi/export`)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = filename
      anchor.click()
      // Revoking synchronously can cancel the download in some browsers; the click is queued, not done.
      setTimeout(() => URL.revokeObjectURL(url), 10_000)
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : 'Export failed.')
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="mt-4 rounded-md border border-slate-200 bg-slate-50 px-3 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-medium text-slate-900">Power BI export</p>
          <p className="mt-0.5 text-xs text-slate-600">
            A Power BI project you open in Power BI Desktop, built from the mappings that passed review.
          </p>
        </div>
        <button
          type="button"
          onClick={handleClick}
          disabled={pending}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {pending ? 'Preparing…' : 'Download'}
        </button>
      </div>

      <p className="mt-2 text-xs text-slate-500">
        The download contains this dataset's raw rows. Power BI recomputes the numbers itself, so the
        caveats and checks InsightFlow applies aren't carried over, and this project's roles don't
        apply to the file once it's saved.
      </p>

      {error && (
        <p role="alert" className="mt-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </p>
      )}
    </div>
  )
}
