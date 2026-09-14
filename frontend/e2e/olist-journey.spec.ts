import { expect, test, type Page, type Response } from '@playwright/test'
import path from 'node:path'

import type { Answer, HydratedDashboard } from '../src/api/types'

/**
 * Phase 8 M6: the whole product, through the browser, against the real stack and a real LLM.
 *
 * A new user signs up, creates an org and project, uploads five real Olist CSVs, waits for real schema
 * discovery, reviews the mappings the way an analyst would, generates a dashboard, and asks questions.
 * Every number asserted below was computed independently with hand-written DuckDB SQL over the raw
 * CSVs -- never read back from this system -- so the test checks correctness, not just that it renders.
 */

const DATASET_DIR = process.env.OLIST_DIR ?? path.resolve(import.meta.dirname, '../../Brazillian_Ecom_dataset')
const FILES = [
  'olist_orders_dataset.csv',
  'olist_order_items_dataset.csv',
  'olist_order_payments_dataset.csv',
  'olist_customers_dataset.csv',
  'olist_products_dataset.csv',
]

// Ground truth (DuckDB over the raw CSVs; revenue = SUM(payment_value), dated by order_purchase_timestamp).
// "Last quarter" is Q2 2018: the data's trailing month (Sep/Oct 2018) is too sparse to anchor on.
const TRUTH = {
  revenue: 16_008_872.12,
  orders: 99_441,
  aov: 160.9886,
  revenueQ2_2018: 3_338_648.13,
  revenueGrowthQ2vsQ1_2018: 3_338_648.13 / 3_267_119.64 - 1, // +2.19%; Q1 2018 revenue is 3,267,119.64
}

/**
 * The analyst's review. Discovery is LLM-assisted, so which mappings it proposes -- and with what
 * confidence -- varies run to run; the review is therefore a policy, not a script of clicks.
 * Wrong mappings are rejected wherever they appear (even auto-accepted ones); correct ones awaiting
 * confirmation are confirmed; anything else awaiting confirmation is rejected as unverified.
 */
const WRONG = new Set([
  'seller_id→customer_id', // a seller is not a customer
  'freight_value→revenue', // shipping cost, not what was paid
  'price→revenue', // item price excludes freight; payments are the paid amount
  'customer_zip_code_prefix→region',
  'shipping_limit_date→transaction_date',
  // An order has one purchase date; approval/shipping/delivery dates would shift every period.
  'order_approved_at→transaction_date',
  'order_delivered_carrier_date→transaction_date',
  'order_delivered_customer_date→transaction_date',
  'order_estimated_delivery_date→transaction_date',
])
const CORRECT = new Set([
  'order_id→order_id',
  'customer_id→customer_id',
  'customer_unique_id→customer_id',
  'product_id→product_id',
  'payment_value→revenue',
  'price→price',
  'order_purchase_timestamp→transaction_date',
  'customer_city→region',
  'customer_state→region',
  'product_category_name→category',
])

const PASSWORD = 'e2e-hunter2hunter2'

function collectPageProblems(page: Page): string[] {
  const problems: string[] = []
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`))
  page.on('console', (m) => {
    if (m.type() === 'error') problems.push(`console: ${m.text()}`)
  })
  return problems
}

const isApi = (suffix: string) => (r: Response) => r.request().method() === 'POST' && r.url().endsWith(suffix)

async function ask(page: Page, question: string): Promise<{ answer: Answer; card: ReturnType<Page['locator']> }> {
  const panel = page.locator('section', { hasText: 'Each question is answered on its own' })
  const turns = panel.locator('ol > li')
  const index = await turns.count()
  await panel.getByLabel('Question').fill(question)
  const [response] = await Promise.all([
    page.waitForResponse(isApi('/chat/ask'), { timeout: 180_000 }),
    panel.getByRole('button', { name: 'Ask' }).click(),
  ])
  expect(response.status(), `chat/ask for "${question}": ${await response.text()}`).toBe(200)
  const card = turns.nth(index)
  await expect(card.getByText('Interpreting and computing…')).toHaveCount(0)
  return { answer: (await response.json()) as Answer, card }
}

test('new user: sign up → upload Olist → review mappings → dashboard → chat, numbers match ground truth', async ({ page }) => {
  const problems = collectPageProblems(page)
  const stamp = Date.now()

  await test.step('sign up through the UI', async () => {
    await page.goto('/login')
    await page.getByRole('button', { name: 'Create one' }).click()
    await page.getByLabel('Email').fill(`e2e-${stamp}@example.com`)
    await page.getByLabel('Password').fill(PASSWORD)
    await page.getByRole('button', { name: 'Create account' }).click()
    await expect(page.getByRole('heading', { name: 'Organizations' })).toBeVisible()
  })

  await test.step('create an organization and a project', async () => {
    await page.getByLabel('New organization').fill(`E2E Olist ${stamp}`)
    await page.getByRole('button', { name: 'Create' }).click()
    await page.getByRole('link', { name: new RegExp(`E2E Olist ${stamp}`) }).click()
    await page.getByLabel('New project').fill('Marketplace sales')
    await page.getByRole('button', { name: 'Create' }).click()
    await page.getByRole('link', { name: /Marketplace sales/ }).click()
    await expect(page.getByText('No data yet')).toBeVisible()
  })

  await test.step('upload five Olist CSVs and wait for real schema discovery', async () => {
    await page.setInputFiles('#csv-files', FILES.map((f) => path.join(DATASET_DIR, f)))
    await page.getByRole('button', { name: 'Discover schema' }).click()
    await expect(page.getByText(/Profiling columns/)).toBeVisible()
    // Reload mid-discovery: the page must pick the running job back up. The job id used to live only in page
    // state, so a reload lost it and a job that then failed was never reported.
    await page.reload()
    const outcome = page.getByText(/Discovery (complete|failed)/)
    await expect(outcome).toBeVisible({ timeout: 10 * 60_000 })
    await expect(outcome, 'discovery job').toContainText('Discovery complete')
    await expect(page.getByText('olist_order_payments_dataset', { exact: true })).toBeVisible()
  })

  await test.step('review mappings as an analyst and save a new dataset version', async () => {
    const reviewable = await page.$$eval('tbody tr', (rows) =>
      rows
        .filter((r) => r.querySelector('button[aria-label]'))
        .map((r) => ({
          column: r.children[1]?.textContent ?? '',
          name: r.children[0]?.textContent ?? '',
          status: r.children[3]?.textContent?.trim().toLowerCase() ?? '',
        })),
    )
    const decisions = new Map<string, 'confirm' | 'reject'>()
    for (const { column, name, status } of reviewable) {
      const key = `${column}→${name}`
      const awaiting = status === 'needs review'
      if (WRONG.has(key) && !/rejected/.test(status)) decisions.set(key, 'reject')
      else if (awaiting) decisions.set(key, CORRECT.has(key) ? 'confirm' : 'reject')
    }
    // Logged, not asserted: what discovery proposed varies by run, and the log is what makes a failure
    // further down diagnosable.
    console.log('review:', [...decisions].map(([k, d]) => `${d} ${k}`).join('; ') || '(nothing to review)')

    for (const [key, decision] of decisions) {
      const [column, name] = key.split('→')
      // The same column→name pair can appear in more than one file (e.g. order_id); decide them all.
      const buttons = page.getByRole('button', { name: `${decision} ${column} as ${name}`, exact: true })
      for (let i = 0; i < (await buttons.count()); i++) await buttons.nth(i).click()
    }

    if (decisions.size > 0) {
      await page.getByRole('button', { name: 'Save as new version' }).click()
      await expect(page.getByText(/earlier version/)).toBeVisible()
    }
    await expect(page.getByText(/mappings? needs? review/)).toHaveCount(0)

    // The decisions are data, not UI state: they survive a reload.
    await page.reload()
    await expect(page.getByText('olist_order_payments_dataset', { exact: true })).toBeVisible()
    await expect(page.getByText(/mappings? needs? review/)).toHaveCount(0)
  })

  await test.step('generate a dashboard; every KPI we can check matches ground truth', async () => {
    const [response] = await Promise.all([
      page.waitForResponse(isApi('/dashboard/generate'), { timeout: 5 * 60_000 }),
      page.getByRole('button', { name: 'Generate dashboard' }).click(),
    ])
    expect(response.status(), `dashboard/generate: ${await response.text()}`).toBe(200)
    const dashboard = (await response.json()) as HydratedDashboard
    await expect(page.getByText('Planner summary')).toBeVisible()

    const components = dashboard.components ?? []
    expect(components.length).toBeGreaterThan(0)
    const checked: string[] = []
    for (const { spec, result } of components) {
      if (spec.type !== 'kpi' || result.shape !== 'scalar' || result.value == null) continue
      const expected = { revenue: TRUTH.revenue, orders: TRUTH.orders, aov: TRUTH.aov }[result.metric_name as 'revenue']
      if (expected === undefined) continue
      expect(result.value, `${result.metric_name} KPI`).toBeCloseTo(expected, 2)
      checked.push(result.metric_name)
    }
    console.log(
      `dashboard: ${components.length} components (${components.map((c) => `${c.spec.type}:${c.spec.metric_name}`).join(', ')}); ground-truth KPIs checked: ${checked.join(', ') || 'none'}`,
    )
    if (checked.includes('revenue')) await expect(page.getByText('16,008,872.12', { exact: true }).first()).toBeVisible()
  })

  await test.step('chat: answers match ground truth, and an unanswerable question is refused', async () => {
    const q2 = await ask(page, 'What was total revenue last quarter?')
    expect(q2.answer.refused, q2.answer.reason ?? '').toBe(false)
    expect(q2.answer.result?.metric_result?.value).toBeCloseTo(TRUTH.revenueQ2_2018, 2)
    await expect(q2.card.getByText('3,338,648.13', { exact: true })).toBeVisible()
    await expect(q2.card.getByText(/Apr 1, 2018 – Jun 30, 2018/)).toBeVisible()

    const orders = await ask(page, 'How many orders were there in total?')
    expect(orders.answer.refused, orders.answer.reason ?? '').toBe(false)
    expect(orders.answer.result?.metric_result?.value).toBe(TRUTH.orders)
    await expect(orders.card.getByText('99,441', { exact: true })).toBeVisible()

    const growth = await ask(page, 'How much did revenue grow last quarter compared to the quarter before?')
    expect(growth.answer.refused, growth.answer.reason ?? '').toBe(false)
    expect(growth.answer.result?.metric_result?.value).toBeCloseTo(TRUTH.revenueGrowthQ2vsQ1_2018, 4)
    await expect(growth.card.getByText(/Apr 1, 2018 – Jun 30, 2018 vs Jan 1, 2018 – Mar 31, 2018/)).toBeVisible()

    const conversion = await ask(page, 'What is our website conversion rate?')
    expect(conversion.answer.refused).toBe(true)
    await expect(conversion.card.getByText('Can’t answer this from the data')).toBeVisible()
  })

  expect(problems, 'browser errors during the journey').toEqual([])
})
