import { useState } from 'react'

import { ApiError } from '../../api/client'
import { useAskQuestion } from '../../api/queries'
import type { AnalyticalQuery, Answer, TimeFilter } from '../../api/types'
import { formatExact, humanize, type MetricFormat } from '../dashboard/format'

const EXAMPLES = [
  'What was total revenue last quarter?',
  'How many orders were there by region?',
  'Which category’s revenue declined the most last quarter?',
]

type Turn = { id: number; question: string; answer?: Answer; error?: string }

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 503) return error.detail
    if (error.status === 429) return 'Too many AI requests for this project right now. Wait a minute and ask again.'
    if (error.status === 422) return error.detail
  }
  return error instanceof Error ? error.message.replace(/^HTTP \d+: /, '') : 'Something went wrong.'
}

const formatDate = (iso: string) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })

const formatWindow = (tf: TimeFilter) => `${formatDate(tf.start_date)} – ${formatDate(tf.end_date)}`

/** The period actually computed, read off the validated queries -- never inferred from the wording. */
function describePeriod(queries: AnalyticalQuery[]): string {
  const [first, second] = queries
  if (!first) return 'no period'
  if (first.growth) return `${formatWindow(first.growth.current_period)} vs ${formatWindow(first.growth.comparison_period)}`
  if (second?.time_filter && first.time_filter) return `${formatWindow(first.time_filter)} vs ${formatWindow(second.time_filter)}`
  if (first.time_filter) return formatWindow(first.time_filter)
  return 'all time'
}

function Interpretation({ answer }: { answer: Answer }) {
  const intent = answer.intent
  if (!intent?.answerable || !intent.metric_name) return null
  const parts = [humanize(intent.metric_name)]
  if (intent.dimension) parts.push(`by ${humanize(intent.dimension).toLowerCase()}`)
  if (intent.operation === 'growth' || intent.operation === 'growth_by_dimension') parts.push('change')
  return (
    <p className="text-xs text-slate-500">
      <span className="font-medium text-slate-600">Interpreted as: </span>
      {parts.join(' ')} · {describePeriod(answer.queries ?? [])}
      {answer.data_through && <> · data through {formatDate(answer.data_through)}</>}
    </p>
  )
}

function VerifiedResult({ answer }: { answer: Answer }) {
  const result = answer.result
  if (!result) return null

  if (result.category_deltas) {
    const format: MetricFormat = result.format ?? 'number'
    return (
      <div className="overflow-x-auto">
        <p className="mb-1 text-xs text-slate-500">
          {result.category_deltas.length} largest declines first
        </p>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs text-slate-500">
              <th className="py-1.5 pr-3 font-medium">{humanize(answer.intent?.dimension ?? 'group')}</th>
              <th className="py-1.5 pr-3 text-right font-medium">Before</th>
              <th className="py-1.5 pr-3 text-right font-medium">After</th>
              <th className="py-1.5 pr-3 text-right font-medium">Change</th>
              <th className="py-1.5 text-right font-medium">%</th>
            </tr>
          </thead>
          <tbody>
            {result.category_deltas.map((d) => (
              <tr key={d.dimension_value} className="border-b border-slate-100 last:border-0 tabular-nums">
                <td className="py-1.5 pr-3 text-slate-800">{d.dimension_value}</td>
                <td className="py-1.5 pr-3 text-right text-slate-800">{formatExact(d.comparison_value, format)}</td>
                <td className="py-1.5 pr-3 text-right text-slate-800">{formatExact(d.current_value, format)}</td>
                <td className="py-1.5 pr-3 text-right text-slate-900">
                  {d.delta > 0 ? '+' : ''}
                  {formatExact(d.delta, format)}
                </td>
                <td className="py-1.5 text-right text-slate-500">
                  {d.pct_change === null || d.pct_change === undefined ? 'n/a' : formatExact(d.pct_change, 'percent')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  const metric = result.metric_result
  if (!metric) return null
  if (metric.shape === 'scalar') {
    return (
      <p className="text-2xl font-semibold text-slate-900 tabular-nums">
        {metric.value === null || metric.value === undefined ? (
          <span className="text-slate-400">— (undefined for this period)</span>
        ) : (
          formatExact(metric.value, metric.format)
        )}
      </p>
    )
  }

  const dimension = answer.intent?.dimension ?? answer.queries?.[0]?.dimension ?? ''
  return (
    <div className="max-h-72 overflow-auto">
      {metric.truncated && (
        <p className="mb-1 text-xs text-slate-500">
          Largest {metric.rows?.length ?? 0} shown — there are more {humanize(dimension || 'group').toLowerCase()} values than fit in one result.
        </p>
      )}
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-left text-xs text-slate-500">
            <th className="py-1.5 pr-3 font-medium">{humanize(dimension || 'group')}</th>
            <th className="py-1.5 text-right font-medium">{humanize(metric.metric_name)}</th>
          </tr>
        </thead>
        <tbody>
          {(metric.rows ?? []).map((row, i) => (
            <tr key={i} className="border-b border-slate-100 last:border-0">
              <td className="py-1.5 pr-3 text-slate-800">{String(row[dimension] ?? '(blank)')}</td>
              <td className="py-1.5 text-right text-slate-800 tabular-nums">{formatExact(Number(row.value ?? 0), metric.format)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function AnswerCard({ answer }: { answer: Answer }) {
  const metric = answer.result?.metric_result
  const caveats = metric?.caveats ?? []

  if (answer.refused) {
    return (
      <div className="space-y-2">
        <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <span className="font-medium">Can’t answer this from the data: </span>
          {answer.reason ?? answer.explanation}
        </p>
        <Interpretation answer={answer} />
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <VerifiedResult answer={answer} />
      {caveats.length > 0 && (
        <p className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <span className="font-medium">Can’t be interpreted from this data: </span>
          {caveats.join(' ')}
        </p>
      )}
      {/* A caveated answer's explanation is the backend's deterministic restatement of the value and
          caveat shown just above -- repeating it adds nothing. Only LLM explanations are shown. */}
      {caveats.length === 0 && (
        <p className="text-sm text-slate-700">
          <span className="font-medium text-slate-600">AI explanation: </span>
          {answer.explanation}
        </p>
      )}
      <Interpretation answer={answer} />
      {metric && (
        <details className="text-xs text-slate-500">
          <summary className="cursor-pointer select-none hover:text-slate-900">How this was computed</summary>
          <p className="mt-1">
            {metric.metadata.row_count} row{metric.metadata.row_count === 1 ? '' : 's'} · {metric.metadata.execution_time_ms.toFixed(0)} ms
          </p>
          <pre className="mt-1 overflow-x-auto rounded bg-slate-50 p-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-slate-700">
            {metric.metadata.sql}
          </pre>
        </details>
      )}
    </div>
  )
}

/** Keyed by dataset version in the parent: answers from a previous version don't carry over. */
export default function ChatPanel({ projectId }: { projectId: string }) {
  const ask = useAskQuestion(projectId)
  const [turns, setTurns] = useState<Turn[]>([])
  const [draft, setDraft] = useState('')

  async function submit(question: string) {
    const trimmed = question.trim()
    if (!trimmed || ask.isPending) return
    const id = Date.now()
    setTurns((t) => [...t, { id, question: trimmed }])
    setDraft('')
    try {
      const answer = await ask.mutateAsync(trimmed)
      setTurns((t) => t.map((turn) => (turn.id === id ? { ...turn, answer } : turn)))
    } catch (error) {
      setTurns((t) => t.map((turn) => (turn.id === id ? { ...turn, error: errorMessage(error) } : turn)))
    }
  }

  return (
    <section className="mt-10">
      <h2 className="text-sm font-semibold text-slate-900">Ask</h2>
      <p className="mt-0.5 text-xs text-slate-500">
        The AI interprets your question; the numbers come from the analytics engine. Each question is answered on its own.
      </p>

      {turns.length > 0 && (
        <ol className="mt-4 space-y-4">
          {turns.map((turn) => (
            <li key={turn.id} className="rounded-lg border border-slate-200 bg-white p-4">
              <p className="text-sm font-medium text-slate-900">{turn.question}</p>
              <div className="mt-3">
                {turn.answer ? (
                  <AnswerCard answer={turn.answer} />
                ) : turn.error ? (
                  <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
                    {turn.error}
                  </p>
                ) : (
                  <p className="text-sm text-slate-500">Interpreting and computing…</p>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}

      <form
        className="mt-4 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          void submit(draft)
        }}
      >
        <label htmlFor="question" className="sr-only">
          Question
        </label>
        <input
          id="question"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask about revenue, orders, customers…"
          className="flex-1 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-slate-500"
        />
        <button
          type="submit"
          disabled={ask.isPending || !draft.trim()}
          className="rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
        >
          {ask.isPending ? 'Asking…' : 'Ask'}
        </button>
      </form>

      {turns.length === 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => void submit(example)}
              className="rounded-full border border-slate-300 bg-white px-3 py-1 text-xs text-slate-600 hover:border-slate-400 hover:text-slate-900"
            >
              {example}
            </button>
          ))}
        </div>
      )}
    </section>
  )
}
