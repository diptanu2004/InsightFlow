import { useState, type ReactNode } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import type { ComponentType, HydratedComponent, HydratedDashboard } from '../../api/types'
import { formatExact, formatHeadline, humanize, type MetricFormat } from './format'

// Reference palette (dataviz skill, light mode). Slots 1-5 validated with validate_palette.js: all
// hard checks pass; slots 3-5 sit below 3:1 on the surface, so every chart ships a labeled legend or
// a table view (the "relief rule"). Text never wears these -- only marks do.
const SURFACE = '#fcfcfb'
const SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4']
const DE_EMPHASIS = '#c3c2b7'
const GRID = '#e1e0d9'
const AXIS = '#c3c2b7'
const MUTED_INK = '#898781'
const PIE_SEGMENTS = 5 // + "Other": the skill caps part-to-whole at 6 segments

type Row = { label: string; value: number }

function rowsOf(component: HydratedComponent): Row[] {
  const dimension = component.spec.dimension ?? ''
  return (component.result.rows ?? []).map((row) => {
    const raw = row[dimension]
    return { label: raw === null || raw === undefined || raw === '' ? '(blank)' : String(raw), value: Number(row.value ?? 0) }
  })
}

function titleOf(component: HydratedComponent): string {
  const { metric_name, dimension } = component.spec
  return dimension ? `${humanize(metric_name)} by ${humanize(dimension).toLowerCase()}` : humanize(metric_name)
}

// ---------------------------------------------------------------------------------------------
// Frame shared by every component: title, the LLM planner's note, engine caveats, and the SQL.

function ComponentFrame({
  component,
  children,
  tableView,
  note,
}: {
  component: HydratedComponent
  children: ReactNode
  tableView?: ReactNode
  note?: string
}) {
  const [showTable, setShowTable] = useState(false)
  const { result, spec } = component

  return (
    <article className="flex flex-col rounded-lg border border-slate-200 bg-[#fcfcfb] p-4">
      <header className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-medium text-slate-900">
          {titleOf(component)}
          {note && <span className="ml-2 text-xs font-normal text-slate-500">{note}</span>}
        </h3>
        {tableView && (
          <button
            type="button"
            onClick={() => setShowTable((v) => !v)}
            className="text-xs font-medium text-slate-500 underline-offset-2 hover:text-slate-900 hover:underline"
          >
            {showTable ? 'Chart' : 'Table'}
          </button>
        )}
      </header>

      <div className="mt-3 flex-1">{showTable && tableView ? tableView : children}</div>

      {result.caveats && result.caveats.length > 0 && (
        <p className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <span className="font-medium">Can’t be interpreted from this data: </span>
          {result.caveats.join(' ')}
        </p>
      )}

      {spec.rationale && (
        <p className="mt-3 text-xs text-slate-500">
          <span className="font-medium text-slate-600">Planner note: </span>
          {spec.rationale}
        </p>
      )}

      <details className="mt-2 text-xs text-slate-500">
        <summary className="cursor-pointer select-none hover:text-slate-900">How this was computed</summary>
        <p className="mt-1">
          {result.metadata.row_count} row{result.metadata.row_count === 1 ? '' : 's'} · {result.metadata.execution_time_ms.toFixed(0)} ms
        </p>
        <pre className="mt-1 overflow-x-auto rounded bg-slate-50 p-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-slate-700">
          {result.metadata.sql}
        </pre>
      </details>
    </article>
  )
}

function Refusal({ component, reason }: { component: HydratedComponent; reason: string }) {
  return (
    <ComponentFrame component={component}>
      <p role="alert" className="rounded-md bg-slate-100 px-3 py-2 text-sm text-slate-600">
        {reason}
      </p>
    </ComponentFrame>
  )
}

function ValueTable({ rows, format, dimension }: { rows: Row[]; format: MetricFormat; dimension: string }) {
  return (
    <div className="max-h-80 overflow-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-left text-xs text-slate-500">
            <th className="py-1.5 pr-3 font-medium">{humanize(dimension)}</th>
            <th className="py-1.5 text-right font-medium">Value</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={`${row.label}-${i}`} className="border-b border-slate-100 last:border-0">
              <td className="py-1.5 pr-3 text-slate-800">{row.label}</td>
              <td className="py-1.5 text-right text-slate-800 tabular-nums">{formatExact(row.value, format)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------------------------
// Renderers. Each checks the result shape it needs rather than inferring it from value presence
// (a scalar can legitimately be null -- see insightflow_core's MetricResult.shape).

function KpiRenderer({ component }: { component: HydratedComponent }) {
  const { result } = component
  if (result.shape !== 'scalar') return <Refusal component={component} reason="Expected a single value for a KPI, got grouped rows." />
  return (
    <ComponentFrame component={component}>
      {result.value === null || result.value === undefined ? (
        <p>
          <span className="text-3xl font-semibold text-slate-400">—</span>
          <span className="mt-1 block text-xs text-slate-500">Undefined for this data (for example, a ratio over zero rows)</span>
        </p>
      ) : (
        <p>
          <span className="text-3xl font-semibold text-slate-900">{formatHeadline(result.value, result.format)}</span>
          <span className="mt-1 block text-xs text-slate-500 tabular-nums">{formatExact(result.value, result.format)}</span>
        </p>
      )}
    </ComponentFrame>
  )
}

function ChartTooltip({ active, payload, format }: { active?: boolean; payload?: ReadonlyArray<{ payload?: Row }>; format: MetricFormat }) {
  const row = active ? payload?.[0]?.payload : undefined
  if (!row) return null
  return (
    <div className="rounded-md border border-slate-200 bg-white px-3 py-2 shadow-sm">
      <div className="text-sm font-semibold text-slate-900 tabular-nums">{formatExact(row.value, format)}</div>
      <div className="text-xs text-slate-500">{row.label}</div>
    </div>
  )
}

const truncate = (text: string, max = 22) => (text.length > max ? `${text.slice(0, max - 1)}…` : text)

function BarRenderer({ component }: { component: HydratedComponent }) {
  const { result, spec } = component
  if (result.shape !== 'grouped' || !spec.dimension) return <Refusal component={component} reason="Expected grouped rows for a bar chart." />
  const rows = rowsOf(component)
  const maxIndex = rows.reduce((best, row, i) => (row.value > rows[best].value ? i : best), 0)
  // Plot height grows with the category count so the axis band is never cut off.
  const height = rows.length * 28 + 36

  return (
    <ComponentFrame
      component={component}
      note={result.truncated ? `top ${rows.length}` : undefined}
      tableView={<ValueTable rows={rows} format={result.format} dimension={spec.dimension} />}
    >
      {rows.length === 0 ? (
        <p className="text-sm text-slate-500">No rows for this breakdown.</p>
      ) : (
        <ResponsiveContainer width="100%" height={height}>
          <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 72, bottom: 4, left: 0 }} barCategoryGap={2}>
            <CartesianGrid horizontal={false} stroke={GRID} />
            <XAxis
              type="number"
              tick={{ fill: MUTED_INK, fontSize: 11 }}
              axisLine={{ stroke: AXIS }}
              tickLine={false}
              tickFormatter={(v: number) => formatHeadline(v, result.format === 'percent' ? 'percent' : 'number')}
            />
            <YAxis
              type="category"
              dataKey="label"
              width={150}
              tick={{ fill: MUTED_INK, fontSize: 11 }}
              tickFormatter={(v: string) => truncate(v)}
              axisLine={{ stroke: AXIS }}
              tickLine={false}
            />
            <Tooltip cursor={{ fill: 'rgba(11,11,11,0.04)' }} content={(props) => <ChartTooltip {...props} format={result.format} />} />
            {/* One series, one hue for every bar: these are nominal categories, so a darker-where-bigger
                ramp would double-encode length. Rounded data end, square at the baseline. */}
            <Bar dataKey="value" fill={SERIES[0]} barSize={20} radius={[0, 4, 4, 0]} isAnimationActive={false}>
              <LabelList
                dataKey="value"
                content={({ x, y, width, height: h, index, value }) =>
                  index === maxIndex ? (
                    <text x={Number(x) + Number(width) + 6} y={Number(y) + Number(h) / 2} dominantBaseline="central" fontSize={11} fill="#52514e">
                      {formatHeadline(Number(value), result.format)}
                    </text>
                  ) : null
                }
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </ComponentFrame>
  )
}

function PieRenderer({ component }: { component: HydratedComponent }) {
  const { result, spec } = component
  if (result.shape !== 'grouped' || !spec.dimension) return <Refusal component={component} reason="Expected grouped rows for a pie chart." />
  const rows = [...rowsOf(component)].sort((a, b) => b.value - a.value)
  const head = rows.slice(0, PIE_SEGMENTS)
  const tail = rows.slice(PIE_SEGMENTS)
  const slices = tail.length > 0 ? [...head, { label: `Other (${tail.length})`, value: tail.reduce((s, r) => s + r.value, 0) }] : head
  const total = slices.reduce((s, r) => s + r.value, 0)
  const colorOf = (i: number) => (i < PIE_SEGMENTS ? SERIES[i] : DE_EMPHASIS)

  return (
    <ComponentFrame component={component} tableView={<ValueTable rows={rows} format={result.format} dimension={spec.dimension} />}>
      {result.truncated ? (
        // A share of the whole needs the whole. When the engine's row cap cut the list off, the
        // percentages and "Other" would be shares of only what was returned -- so show the largest
        // groups as plain values and say why, instead of a confidently wrong donut.
        <div>
          <p className="rounded-md bg-slate-100 px-3 py-2 text-xs text-slate-600">
            More groups than can be fetched at once, so shares of the whole can’t be computed. Largest {head.length} shown.
          </p>
          <ul className="mt-3 space-y-1 text-xs">
            {head.map((slice) => (
              <li key={slice.label} className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-slate-700" title={slice.label}>
                  {slice.label}
                </span>
                <span className="text-slate-900 tabular-nums">{formatExact(slice.value, result.format)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : total <= 0 ? (
        <p className="text-sm text-slate-500">Nothing to divide up — every group is zero.</p>
      ) : (
        <div className="flex flex-wrap items-center gap-4">
          <div className="h-44 w-44 shrink-0">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                {/* 2px surface-colored gap between segments -- the gap separates them, not a border. */}
                <Pie data={slices} dataKey="value" nameKey="label" innerRadius={48} outerRadius={80} stroke={SURFACE} strokeWidth={2} isAnimationActive={false}>
                  {slices.map((slice, i) => (
                    <Cell key={slice.label} fill={colorOf(i)} />
                  ))}
                </Pie>
                <Tooltip content={(props) => <ChartTooltip {...props} format={result.format} />} />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <ul className="min-w-0 flex-1 space-y-1 text-xs">
            {slices.map((slice, i) => (
              <li key={slice.label} className="flex items-center gap-2">
                <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: colorOf(i) }} />
                <span className="min-w-0 flex-1 truncate text-slate-700" title={slice.label}>
                  {slice.label}
                </span>
                <span className="text-slate-900 tabular-nums">{formatExact(slice.value, result.format)}</span>
                <span className="w-12 text-right text-slate-500 tabular-nums">{((slice.value / total) * 100).toFixed(1)}%</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </ComponentFrame>
  )
}

function TableRenderer({ component }: { component: HydratedComponent }) {
  const { result, spec } = component
  if (result.shape !== 'grouped' || !spec.dimension) return <Refusal component={component} reason="Expected grouped rows for a table." />
  const rows = rowsOf(component)
  return (
    <ComponentFrame component={component} note={result.truncated ? `top ${rows.length}` : undefined}>
      <ValueTable rows={rows} format={result.format} dimension={spec.dimension} />
    </ComponentFrame>
  )
}

function UnsupportedRenderer({ component }: { component: HydratedComponent }) {
  return (
    <Refusal
      component={component}
      reason={`“${component.spec.type}” components aren’t rendered yet — the engine has no time bucketing for trend lines.`}
    />
  )
}

/**
 * Every component type the backend can emit maps to exactly one renderer. A `Record` keyed by the
 * generated `ComponentType` union makes this exhaustive at compile time: a new type added to the
 * backend's ComponentType fails the frontend build instead of silently rendering nothing. The LLM's
 * spec is only ever data -- it selects from this map, it never supplies anything that runs.
 */
const RENDERERS: Record<ComponentType, (props: { component: HydratedComponent }) => ReactNode> = {
  kpi: KpiRenderer,
  bar_chart: BarRenderer,
  pie_chart: PieRenderer,
  table: TableRenderer,
  line_chart: UnsupportedRenderer,
}

function renderComponent(component: HydratedComponent) {
  // Runtime guard too: the payload crosses a network boundary, so a type the build never saw still
  // gets a visible refusal rather than a crash.
  const Renderer = RENDERERS[component.spec.type] ?? UnsupportedRenderer
  return <Renderer key={component.spec.component_id} component={component} />
}

export default function DashboardView({ dashboard }: { dashboard: HydratedDashboard }) {
  const components = dashboard.components ?? []
  const kpis = components.filter((c) => c.spec.type === 'kpi')
  const rest = components.filter((c) => c.spec.type !== 'kpi')

  return (
    <div>
      <h3 className="text-base font-semibold text-slate-900">{dashboard.title}</h3>
      {dashboard.narrative && (
        <p className="mt-1 max-w-3xl text-sm text-slate-600">
          <span className="font-medium text-slate-700">Planner summary: </span>
          {dashboard.narrative}
        </p>
      )}

      {kpis.length > 0 && <div className="mt-4 grid grid-cols-1 items-start gap-3 sm:grid-cols-2 lg:grid-cols-4">{kpis.map(renderComponent)}</div>}
      {rest.length > 0 && <div className="mt-3 grid grid-cols-1 items-start gap-3 lg:grid-cols-2">{rest.map(renderComponent)}</div>}
    </div>
  )
}
