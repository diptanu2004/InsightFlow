import { useEffect, useRef, useState } from 'react'

import { useDatasets, useDiscoveryJob, useLatestDiscoveryJob, useUploadDataset } from '../api/queries'
import type { DatasetOut, JobOut } from '../api/types'

/**
 * How long a non-terminal job runs before we say something. Discovery on the Olist sample
 * finishes in under ten seconds, so a minute means something is wrong -- and per Phase 7 M4 a
 * SIGKILL'd worker leaves its Job row stuck at `running` forever with no watchdog to reclaim it.
 * An indefinite spinner would be a lie in that case, so say so and name the job id.
 */
const STALL_AFTER_MS = 60_000

/**
 * A job worth showing on a page that didn't start it: one still in progress, or a failure newer than the
 * project's newest dataset (an older failure was already superseded by a later successful upload).
 */
function jobToResume(latest: JobOut | null | undefined, newestDataset: DatasetOut | undefined, dismissedId: string | null) {
  if (!latest || latest.id === dismissedId) return null
  if (latest.status === 'pending' || latest.status === 'running') return latest.id
  if (latest.status !== 'failed') return null
  if (newestDataset && new Date(newestDataset.created_at) > new Date(latest.created_at)) return null
  return latest.id
}

export default function DatasetUpload({ projectId }: { projectId: string }) {
  const [files, setFiles] = useState<File[]>([])
  const [startedJobId, setStartedJobId] = useState<string | null>(null)
  const [dismissedJobId, setDismissedJobId] = useState<string | null>(null)
  const [hasStalled, setHasStalled] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const upload = useUploadDataset(projectId)
  const latestJob = useLatestDiscoveryJob(projectId)
  const datasets = useDatasets(projectId)
  // The job this page started wins; otherwise pick up the project's latest one after a reload.
  const jobId = startedJobId ?? jobToResume(latestJob.data, datasets.data?.[0], dismissedJobId)
  const job = useDiscoveryJob(projectId, jobId)

  const status = job.data?.status
  const isRunning = jobId !== null && (status === undefined || status === 'pending' || status === 'running')

  // Timer-driven rather than comparing Date.now() during render: reading the clock while
  // rendering makes output depend on when React happens to re-render. The flag is only ever
  // raised here and cleared by the next submit, so a job that finishes after stalling needs no
  // reset -- `showStall` below gates on the job still running.
  useEffect(() => {
    if (!isRunning) return
    const timer = setTimeout(() => setHasStalled(true), STALL_AFTER_MS)
    return () => clearTimeout(timer)
  }, [isRunning, jobId])

  const showStall = isRunning && hasStalled

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (files.length === 0) return
    setHasStalled(false)
    const created = await upload.mutateAsync(files)
    setStartedJobId(created.id)
    setFiles([])
    if (inputRef.current) inputRef.current.value = ''
  }

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <form onSubmit={onSubmit}>
        <label htmlFor="csv-files" className="block text-sm font-medium text-slate-700">
          Upload CSVs
        </label>
        <p className="mt-1 text-xs text-slate-500">
          Column names can be anything — they're profiled and mapped to semantic types
          automatically. Upload related files together so relationships between them can be
          detected.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input
            id="csv-files"
            ref={inputRef}
            type="file"
            accept=".csv"
            multiple
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
            className="flex-1 text-sm text-slate-600 file:mr-3 file:rounded-md file:border file:border-slate-300 file:bg-white file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-slate-700"
          />
          <button
            type="submit"
            disabled={files.length === 0 || upload.isPending || isRunning}
            className="rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
          >
            {upload.isPending ? 'Uploading…' : 'Discover schema'}
          </button>
        </div>
        {files.length > 0 && (
          <p className="mt-2 text-xs text-slate-500">
            {files.length} file{files.length === 1 ? '' : 's'}: {files.map((f) => f.name).join(', ')}
          </p>
        )}
      </form>

      {upload.isError && (
        <p role="alert" className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          {upload.error.message.replace(/^HTTP \d+: /, '')}
        </p>
      )}

      {jobId !== null && (
        <div className="mt-4 border-t border-slate-100 pt-3">
          {isRunning && (
            <p className="text-sm text-slate-600">
              Profiling columns and inferring the semantic model…{' '}
              <span className="text-slate-400">({status ?? 'queued'})</span>
            </p>
          )}

          {showStall && (
            <p role="alert" className="mt-2 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">
              This is taking much longer than usual. The job may be stuck — a worker that dies
              mid-job leaves its status unchanged, and nothing reclaims it automatically. Job id{' '}
              <span className="font-mono text-xs">{jobId}</span>.
            </p>
          )}

          {status === 'done' && job.data?.dataset && (
            <p className="text-sm text-emerald-700">
              Discovery complete — {job.data.dataset.semantic_model.entities.length} entities,{' '}
              {job.data.dataset.semantic_model.relationships.length} relationships.
            </p>
          )}

          {status === 'failed' && (
            <div role="alert" className="flex items-start justify-between gap-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
              <p>Discovery failed — {job.data?.error ?? 'no error message was recorded'}</p>
              <button
                type="button"
                onClick={() => {
                  setDismissedJobId(jobId)
                  setStartedJobId(null)
                }}
                className="shrink-0 text-xs font-medium text-red-800 underline-offset-2 hover:underline"
              >
                Dismiss
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
