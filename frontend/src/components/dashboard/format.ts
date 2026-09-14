import type { MetricResult } from '../../api/types'

export type MetricFormat = MetricResult['format']

const exactFormatters: Record<NonNullable<MetricFormat>, Intl.NumberFormat> = {
  // No currency symbol on purpose: nothing in an upload says which currency it's in (the Kaggle
  // Olist data is Brazilian reais), so the registry's "money" format is amount-shaped, never "$".
  money: new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
  count: new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }),
  percent: new Intl.NumberFormat(undefined, { style: 'percent', minimumFractionDigits: 1, maximumFractionDigits: 1 }),
  number: new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }),
}

const compact = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 })

/** The full-precision reading -- tables, tooltips, and the line under a stat tile's headline. */
export function formatExact(value: number, format: MetricFormat = 'number'): string {
  return exactFormatters[format ?? 'number'].format(value)
}

/** A stat tile's headline: auto-compact large amounts and counts (16M, 99.4K); percentages never compact. */
export function formatHeadline(value: number, format: MetricFormat = 'number'): string {
  if (format === 'percent' || Math.abs(value) < 10_000) return formatExact(value, format)
  return compact.format(value)
}

const ACRONYMS: Record<string, string> = { aov: 'AOV', id: 'ID' }

/** metric_name / dimension identifiers -> sentence-case labels ("repeat_purchase_rate" -> "Repeat purchase rate"). */
export function humanize(identifier: string): string {
  const words = identifier.split('_').map((w) => ACRONYMS[w] ?? w)
  const sentence = words.join(' ')
  return sentence.charAt(0).toUpperCase() + sentence.slice(1)
}
