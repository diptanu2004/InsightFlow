import { expect, test, type Locator, type Page, type Response } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

import type { Answer, HydratedDashboard } from '../src/api/types'

/**
 * Ten real-world datasets through the whole product, in the browser, against the real stack and a real LLM.
 *
 * `olist-journey.spec.ts` is the sharp regression check: one dataset, hard assertions, fails the build.
 * This file is the opposite instrument -- a survey. Each dataset is a fresh signup → org → project →
 * upload → mapping review → dashboard → chat, and every step records what happened instead of stopping
 * at the first failure, because the point is to find out which of ten datasets break and how, not to
 * discover that the first one did. Numbers come from `e2e/ground-truth.json`, computed independently
 * with hand-written DuckDB over the raw CSVs (scripts in the run log), never read back from this system.
 *
 * Output: `e2e-report/<slug>.json` plus screenshots of the dashboard and the chat transcript.
 */

const DATASETS_DIR = process.env.DATASETS_DIR ?? path.resolve(import.meta.dirname, '../../test_datasets')
const REPORT_DIR = path.resolve(import.meta.dirname, '../e2e-report')
const PASSWORD = 'e2e-hunter2hunter2'

type Case = {
  slug: string
  dir: string
  files: string[]
  /** What this dataset is meant to prove, in the report next to what it actually did. */
  stresses: string
  /** `column→semantic_field` an analyst would confirm. Everything else awaiting review is rejected. */
  correct: string[]
  /** `column→semantic_field` an analyst would reject wherever it appears, auto-accepted or not. */
  wrong: string[]
  questions: string[]
  /** Ground-truth scalars the KPIs and answers are checked against, when the dataset can express them. */
  expect: { revenue?: number; orders?: number; customers?: number; aov?: number }
  /** Datasets with no summable revenue column: the honest outcome is a refusal, not a number. */
  revenueNotExpressible?: string
  /** Files the engine's CSV reader should reject before discovery runs at all. */
  uploadRejected?: boolean
}

const COMMON_WRONG = [
  // A postcode is not a place you can group by meaningfully, and it outnumbers every real region.
  'zip_code→region', 'PostalCode→region', 'BillingPostalCode→region', 'postalCode→region', 'POSTALCODE→region',
  'Postal Code→region', 'customer_zip_code_prefix→region',
]

// The shared config leaves actions untimed, which turns "the control isn't there" into a hang
// rather than a finding. Ten datasets can't afford that.
test.use({ actionTimeout: 30_000 })

const CASES: Case[] = [
  {
    slug: 'chinook',
    dir: 'chinook',
    files: ['customers.csv', 'invoices.csv', 'invoice_lines.csv', 'tracks.csv', 'albums.csv', 'genres.csv'],
    stresses: 'PascalCase columns from a real relational DB; an invoice-shaped sale; a summable Total column',
    correct: ['CustomerId→customer_id', 'InvoiceId→order_id', 'Total→revenue', 'InvoiceDate→transaction_date',
      'BillingCountry→region', 'TrackId→product_id', 'UnitPrice→price'],
    wrong: ['InvoiceLineId→order_id', 'UnitPrice→revenue', 'SupportRepId→customer_id', 'AlbumId→order_id',
      'BillingCity→region', 'BillingState→region', 'City→region', 'State→region', 'Country→region',
      'Milliseconds→price', 'Bytes→price', 'GenreId→category', 'MediaTypeId→category', ...COMMON_WRONG],
    questions: ['What was total revenue?', 'How many orders were there in total?', 'How many customers are there?',
      'What is revenue by region?', 'What is our website conversion rate?'],
    expect: { revenue: 2328.6, orders: 412, customers: 59, aov: 5.6519 },
  },
  {
    slug: 'pizza_place_sales',
    dir: 'pizza_place_sales',
    files: ['orders.csv', 'order_details.csv', 'pizzas.csv', 'pizza_types.csv'],
    stresses: 'a normalized schema with no revenue column (revenue = quantity x price) and no customer identifier at all',
    correct: ['order_id→order_id', 'date→transaction_date', 'pizza_id→product_id', 'category→category', 'price→price'],
    wrong: ['price→revenue', 'quantity→revenue', 'order_details_id→order_id', 'size→category', 'name→category',
      'pizza_type_id→product_id', 'ingredients→category', ...COMMON_WRONG],
    questions: ['What was total revenue?', 'How many orders were there in total?', 'How many orders were there by category?',
      'How many customers do we have?', 'What is our website conversion rate?'],
    expect: { orders: 21350 },
    revenueNotExpressible: 'revenue is quantity x price across order_details and pizzas; ground truth 817,860.05',
  },
  {
    slug: 'classic_models',
    dir: 'classic_models',
    files: ['sales_data_sample.csv'],
    stresses: 'one denormalized file, SHOUTING column names, US m/d/Y dates, and a file that shipped as Windows-1252',
    correct: ['ORDERNUMBER→order_id', 'SALES→revenue', 'ORDERDATE→transaction_date', 'CUSTOMERNAME→customer_id',
      'PRODUCTCODE→product_id', 'PRODUCTLINE→category', 'COUNTRY→region', 'PRICEEACH→price'],
    wrong: ['MSRP→revenue', 'PRICEEACH→revenue', 'QUANTITYORDERED→revenue', 'ORDERLINENUMBER→order_id',
      'YEAR_ID→transaction_date', 'MONTH_ID→transaction_date', 'QTR_ID→transaction_date',
      'CITY→region', 'STATE→region', 'TERRITORY→region', 'DEALSIZE→category', 'STATUS→category', ...COMMON_WRONG],
    questions: ['What was total revenue?', 'How many orders were there in total?', 'What is revenue by category?',
      'How much did revenue grow last quarter compared to the quarter before?', 'What is our website conversion rate?'],
    expect: { revenue: 10032628.85, orders: 307, customers: 92, aov: 32679.5728 },
  },
  {
    slug: 'supermarket_sales',
    dir: 'supermarket_sales',
    files: ['supermarket_sales.csv'],
    stresses: 'spaces in column names, several plausible money columns side by side, and no customer identifier',
    correct: ['Invoice ID→order_id', 'Total→revenue', 'Date→transaction_date', 'Product line→category',
      'City→region', 'Unit price→price'],
    wrong: ['cogs→revenue', 'gross income→revenue', 'Tax 5%→revenue', 'Quantity→revenue', 'Unit price→revenue',
      'Branch→region', 'Customer type→customer_id', 'Gender→category', 'Payment→category', 'Rating→price',
      'gross margin percentage→price', ...COMMON_WRONG],
    questions: ['What was total revenue?', 'How many orders were there in total?', 'What is revenue by category?',
      'What is revenue by region?', 'What is our website conversion rate?'],
    expect: { revenue: 322966.75, orders: 1000, aov: 322.9667 },
  },
  {
    slug: 'bikestores',
    dir: 'bikestores',
    files: ['orders.csv', 'order_items.csv', 'customers.csv', 'products.csv', 'categories.csv', 'brands.csv', 'stores.csv'],
    stresses: 'seven related files, three candidate date columns, and revenue that only exists as list_price x quantity x (1-discount)',
    correct: ['order_id→order_id', 'customer_id→customer_id', 'order_date→transaction_date', 'product_id→product_id',
      'category_name→category', 'state→region', 'list_price→price'],
    wrong: ['list_price→revenue', 'discount→revenue', 'quantity→revenue', 'required_date→transaction_date',
      'shipped_date→transaction_date', 'model_year→transaction_date', 'item_id→order_id', 'staff_id→customer_id',
      'store_id→customer_id', 'brand_name→category', 'city→region', 'street→region', ...COMMON_WRONG],
    questions: ['What was total revenue?', 'How many orders were there in total?', 'How many customers are there?',
      'What is the number of orders by region?', 'What is our website conversion rate?'],
    expect: { orders: 1615, customers: 1445 },
    revenueNotExpressible: 'revenue is list_price x quantity x (1-discount) on order_items; ground truth 7,689,116.56',
  },
  {
    slug: 'jaffle_shop',
    dir: 'jaffle_shop',
    files: ['raw_customers.csv', 'raw_orders.csv', 'raw_payments.csv'],
    stresses: 'the dbt sample: bare `id` primary keys, and payment amounts stored in cents',
    correct: ['order_id→order_id', 'user_id→customer_id', 'order_date→transaction_date', 'amount→revenue'],
    wrong: ['id→order_id', 'id→customer_id', 'id→product_id', 'status→category', 'payment_method→category',
      'first_name→customer_id', 'last_name→customer_id', ...COMMON_WRONG],
    questions: ['What was total revenue?', 'How many orders were there in total?', 'How many customers are there?',
      'What is revenue by category?', 'What is our website conversion rate?'],
    expect: { revenue: 167200, orders: 99, customers: 62 },
  },
  {
    slug: 'northwind',
    dir: 'northwind',
    files: ['orders.csv', 'order_details.csv', 'products.csv', 'categories.csv', 'customers.csv'],
    stresses: 'the canonical demo schema: revenue is unitPrice x quantity x (1-discount), with a freight column sitting next to it',
    correct: ['orderID→order_id', 'customerID→customer_id', 'orderDate→transaction_date', 'productID→product_id',
      'categoryName→category', 'country→region', 'unitPrice→price'],
    wrong: ['freight→revenue', 'unitPrice→revenue', 'quantity→revenue', 'discount→revenue',
      'requiredDate→transaction_date', 'shippedDate→transaction_date', 'shipCountry→region', 'shipCity→region',
      'city→region', 'region→region', 'shipRegion→region', 'employeeID→customer_id', 'supplierID→customer_id',
      'productName→category', 'quantityPerUnit→category', ...COMMON_WRONG],
    questions: ['What was total revenue?', 'How many orders were there in total?', 'How many customers are there?',
      'What is the number of orders by region?', 'What is our website conversion rate?'],
    expect: { orders: 830, customers: 89 },
    revenueNotExpressible: 'revenue is unitPrice x quantity x (1-discount) on order_details; ground truth 1,265,793.04',
  },
  {
    slug: 'online_retail_ii',
    dir: 'online_retail_ii',
    files: ['online_retail_ii.csv'],
    stresses: '94 MB and 1.07 M rows in one file -- upload, profiling and query latency at a size the others never reach',
    correct: ['Invoice→order_id', 'Customer ID→customer_id', 'InvoiceDate→transaction_date', 'StockCode→product_id',
      'Country→region', 'Price→price'],
    wrong: ['Price→revenue', 'Quantity→revenue', 'Description→category', ...COMMON_WRONG],
    questions: ['What was total revenue?', 'How many orders were there in total?', 'How many customers are there?',
      'What is the number of orders by region?', 'What is our website conversion rate?'],
    expect: { orders: 53628, customers: 5942 },
    revenueNotExpressible: 'revenue is Quantity x Price on one line-item file; ground truth 19,287,250.57',
  },
  {
    slug: 'superstore',
    dir: 'superstore',
    files: ['superstore_orders.csv', 'superstore_people.csv', 'superstore_returns.csv'],
    stresses: 'a rerun of the Phase 8 finding: `region` mapped on two entities at once, and a false Customer Name → Person link',
    correct: ['Order ID→order_id', 'Customer ID→customer_id', 'Order Date→transaction_date', 'Product ID→product_id',
      'Category→category', 'Sales→revenue', 'Region→region'],
    wrong: ['Profit→revenue', 'Quantity→revenue', 'Discount→revenue', 'Ship Date→transaction_date',
      'Row ID→order_id', 'Customer Name→customer_id', 'Person→customer_id', 'City→region', 'State→region',
      'Country→region', 'Sub-Category→category', 'Segment→category', 'Ship Mode→category', ...COMMON_WRONG],
    questions: ['What was total revenue?', 'How many orders were there in total?', 'What is revenue by category?',
      'What is revenue by region?', 'What is our website conversion rate?'],
    expect: { revenue: 2297200.86, orders: 5009, customers: 793, aov: 458.6147 },
  },
  {
    slug: 'northwind_malformed',
    dir: 'northwind_malformed',
    // This copy really is named with a hyphen; keep it, it is what was downloaded.
    files: ['orders.csv', 'order-details.csv', 'products.csv', 'categories.csv', 'customers.csv'],
    stresses: 'unquoted commas inside addresses -- the upload must be refused with a message naming the file',
    correct: [],
    wrong: [],
    questions: [],
    expect: {},
    uploadRejected: true,
  },
]

// --------------------------------------------------------------------------------------------
// Report

type Report = {
  slug: string
  stresses: string
  startedAt: string
  durations: Record<string, number>
  discovery: { status?: string; entities?: number; relationships?: number; error?: string }
  review: { autoAccepted: string[]; confirmed: string[]; rejected: string[]; leftUnreviewed: string[] }
  relationships: string[]
  dashboard: { title?: string; narrative?: string; components: string[]; rationales: string[]; error?: string }
  chat: { question: string; refused?: boolean; reason?: string; value?: unknown; rows?: number; interpreted?: string; explanation?: string; error?: string }[]
  findings: string[]
  browserProblems: string[]
}

function write(report: Report) {
  fs.mkdirSync(REPORT_DIR, { recursive: true })
  fs.writeFileSync(path.join(REPORT_DIR, `${report.slug}.json`), JSON.stringify(report, null, 2))
}

/** Never let one broken step hide the next one; the failure is the data we came for. */
async function step<T>(report: Report, name: string, body: () => Promise<T>): Promise<T | undefined> {
  const started = Date.now()
  try {
    return await body()
  } catch (error) {
    report.findings.push(`[${name}] ${error instanceof Error ? error.message.split('\n')[0] : String(error)}`)
    return undefined
  } finally {
    report.durations[name] = Math.round((Date.now() - started) / 1000)
  }
}

const isApi = (suffix: string) => (r: Response) => r.request().method() === 'POST' && r.url().endsWith(suffix)

const near = (actual: number, expected: number) => Math.abs(actual - expected) <= Math.max(0.01, Math.abs(expected) * 1e-6)

async function ask(page: Page, question: string): Promise<{ answer: Answer; card: Locator }> {
  const panel = page.locator('section', { hasText: 'Each question is answered on its own' })
  const turns = panel.locator('ol > li')
  const index = await turns.count()
  await panel.getByLabel('Question').fill(question)
  const [response] = await Promise.all([
    page.waitForResponse(isApi('/chat/ask'), { timeout: 240_000 }),
    panel.getByRole('button', { name: 'Ask' }).click(),
  ])
  const card = turns.nth(index)
  if (response.status() !== 200) throw new Error(`chat/ask ${response.status()}: ${(await response.text()).slice(0, 300)}`)
  await expect(card.getByText('Interpreting and computing…')).toHaveCount(0, { timeout: 30_000 })
  return { answer: (await response.json()) as Answer, card }
}

// --------------------------------------------------------------------------------------------

for (const kase of CASES) {
  test(`journey: ${kase.slug}`, async ({ page }, testInfo) => {
    testInfo.setTimeout(30 * 60_000)
    const report: Report = {
      slug: kase.slug,
      stresses: kase.stresses,
      startedAt: new Date().toISOString(),
      durations: {},
      discovery: {},
      review: { autoAccepted: [], confirmed: [], rejected: [], leftUnreviewed: [] },
      relationships: [],
      dashboard: { components: [], rationales: [] },
      chat: [],
      findings: [],
      browserProblems: [],
    }
    page.on('pageerror', (e) => report.browserProblems.push(`pageerror: ${e.message}`))
    page.on('console', (m) => {
      if (m.type() === 'error') report.browserProblems.push(`console: ${m.text()}`)
    })

    const stamp = Date.now()
    const shot = async (name: string) => {
      try {
        await page.screenshot({ path: path.join(REPORT_DIR, `${kase.slug}-${name}.png`), fullPage: true })
      } catch {
        /* a screenshot failure must never mask the finding it was meant to illustrate */
      }
    }

    await step(report, 'signup', async () => {
      await page.goto('/login')
      await page.getByRole('button', { name: 'Create one' }).click()
      await page.getByLabel('Email').fill(`e2e-${kase.slug}-${stamp}@example.com`)
      await page.getByLabel('Password').fill(PASSWORD)
      await page.getByRole('button', { name: 'Create account' }).click()
      await expect(page.getByRole('heading', { name: 'Organizations' })).toBeVisible()
      await page.getByLabel('New organization').fill(`E2E ${kase.slug} ${stamp}`)
      await page.getByRole('button', { name: 'Create' }).click()
      await page.getByRole('link', { name: new RegExp(`E2E ${kase.slug} ${stamp}`) }).click()
      await page.getByLabel('New project').fill(kase.slug)
      await page.getByRole('button', { name: 'Create' }).click()
      await page.getByRole('link', { name: new RegExp(kase.slug) }).click()
      await expect(page.getByText('No data yet')).toBeVisible()
    })

    const uploaded = await step(report, 'upload+discovery', async () => {
      await page.setInputFiles('#csv-files', kase.files.map((f) => path.join(DATASETS_DIR, kase.dir, f)))
      await page.getByRole('button', { name: 'Discover schema' }).click()

      if (kase.uploadRejected) {
        const alert = page.getByRole('alert').first()
        await expect(alert).toBeVisible({ timeout: 120_000 })
        const text = (await alert.textContent()) ?? ''
        report.discovery.error = text.trim()
        await shot('upload-rejected')
        if (!/can't be read/i.test(text)) report.findings.push(`upload rejection did not explain the CSV problem: ${text.trim()}`)
        return false
      }

      await expect(page.getByText(/Profiling columns|Discovery (complete|failed)/)).toBeVisible({ timeout: 300_000 })
      const outcome = page.getByText(/Discovery (complete|failed)/)
      await expect(outcome).toBeVisible({ timeout: 20 * 60_000 })
      const text = (await outcome.textContent()) ?? ''
      report.discovery.status = text.trim()
      if (/failed/.test(text)) {
        report.findings.push(`discovery failed: ${text.trim()}`)
        await shot('discovery-failed')
        return false
      }
      const m = text.match(/(\d+) entities, (\d+) relationships/)
      if (m) {
        report.discovery.entities = Number(m[1])
        report.discovery.relationships = Number(m[2])
      }
      return true
    })

    if (uploaded !== true) {
      write(report)
      // A rejected upload is this case's expected outcome; any other early stop is a finding in itself.
      expect.soft(report.findings, `${kase.slug}: journey stopped before the semantic model`).toEqual([])
      return
    }

    await step(report, 'review', async () => {
      await shot('semantic-model')
      type RowInfo = { index: number; entity: string; column: string; name: string; status: string }
      const rows: RowInfo[] = await page.$$eval('tbody tr', (trs) =>
        trs.map((r, index) => ({
          index,
          entity: (r.closest('div.overflow-hidden')?.querySelector('span')?.textContent ?? '').trim(),
          name: (r.children[0]?.textContent ?? '').trim(),
          column: (r.children[1]?.textContent ?? '').trim(),
          status: (r.children[3]?.textContent ?? '').trim().toLowerCase(),
        })),
      )

      // `.last()`: the project's own Data section also contains the word, and matching both would
      // count every relationship twice.
      report.relationships = await page
        .locator('section', { hasText: 'Relationships' })
        .last()
        .locator('li')
        .allTextContents()
        .then((xs) => xs.map((x) => x.replace(/\s+/g, ' ').trim()))

      const correct = new Set(kase.correct)
      const wrong = new Set(kase.wrong)
      const decisions: { row: RowInfo; decision: 'confirm' | 'reject'; label: string }[] = []

      for (const row of rows) {
        if (row.name === 'unmapped' || row.name === '') continue
        const key = `${row.column}→${row.name}`
        const label = `${row.entity}.${key}`
        if (row.status === 'auto') report.review.autoAccepted.push(label)
        if (wrong.has(key) && row.status !== 'rejected') {
          decisions.push({ row, decision: 'reject', label })
        } else if (row.status === 'needs review') {
          decisions.push({ row, decision: correct.has(key) ? 'confirm' : 'reject', label })
        }
      }

      let staged = 0
      for (const { row, decision, label } of decisions) {
        // The accessible name is SemanticModelView's aria-label, and the same pair recurs across
        // files, so the row index is what disambiguates -- not the label.
        const button = page
          .locator('tbody tr')
          .nth(row.index)
          .getByRole('button', { name: `${decision} ${row.column} as ${row.name}`, exact: true })
        if ((await button.count()) === 0) {
          report.findings.push(`no "${decision}" control for ${label} (status "${row.status}")`)
          continue
        }
        await button.first().click({ timeout: 15_000 })
        staged++
        ;(decision === 'confirm' ? report.review.confirmed : report.review.rejected).push(label)
      }

      if (staged > 0) {
        await page.getByRole('button', { name: 'Save as new version' }).click({ timeout: 30_000 })
        await expect(page.getByText(/earlier version/)).toBeVisible({ timeout: 120_000 })
      }
      const stillPending = await page.getByText(/mappings? needs? review/).count()
      if (stillPending > 0) {
        const left = await page.getByText(/mappings? needs? review/).first().textContent()
        report.review.leftUnreviewed.push((left ?? '').trim())
        report.findings.push(`mappings still awaiting review after saving: ${(left ?? '').trim()}`)
      }
      await shot('semantic-model-reviewed')
    })

    await step(report, 'dashboard', async () => {
      const [response] = await Promise.all([
        page.waitForResponse(isApi('/dashboard/generate'), { timeout: 8 * 60_000 }),
        page.getByRole('button', { name: /Generate dashboard|Regenerate/ }).click(),
      ])
      if (response.status() !== 200) {
        const body = (await response.text()).slice(0, 500)
        report.dashboard.error = `${response.status()}: ${body}`
        report.findings.push(`dashboard/generate ${response.status()}: ${body}`)
        await expect(page.getByRole('alert').first()).toBeVisible({ timeout: 30_000 })
        await shot('dashboard-error')
        return
      }
      const dashboard = (await response.json()) as HydratedDashboard
      report.dashboard.title = dashboard.title
      report.dashboard.narrative = dashboard.narrative ?? undefined
      const components = dashboard.components ?? []

      for (const { spec, result } of components) {
        const label = `${spec.type}:${spec.metric_name}${spec.dimension ? ` by ${spec.dimension}` : ''}`
        const value = result.shape === 'scalar' ? result.value : `${result.rows?.length ?? 0} rows${result.truncated ? ' (truncated)' : ''}`
        const caveats = result.caveats?.length ? ` caveats=${result.caveats.length}` : ''
        report.dashboard.components.push(`${label} = ${value}${caveats}`)
        if (spec.rationale) report.dashboard.rationales.push(`${label}: ${spec.rationale}`)

        if (result.shape !== 'scalar' || result.value == null) continue
        const expected = kase.expect[result.metric_name as keyof Case['expect']]
        if (expected === undefined) continue
        if (!near(result.value, expected)) {
          report.findings.push(`dashboard KPI ${result.metric_name} = ${result.value}, ground truth ${expected}`)
        }
        expect.soft(result.value, `${kase.slug} dashboard KPI ${result.metric_name}`).toBeCloseTo(expected, 2)
      }

      if (kase.revenueNotExpressible) {
        const revenueKpi = components.find((c) => c.result.metric_name === 'revenue' && c.result.shape === 'scalar')
        if (revenueKpi && revenueKpi.result.value != null) {
          report.findings.push(
            `dashboard shows revenue = ${revenueKpi.result.value} but this dataset has no revenue column (${kase.revenueNotExpressible})`,
          )
        }
      }
      if (components.length === 0) report.findings.push('dashboard generated with zero components')
      await shot('dashboard')
    })

    for (const question of kase.questions) {
      await step(report, `chat: ${question}`, async () => {
        const { answer, card } = await ask(page, question)
        const metric = answer.result?.metric_result
        // The whole <p>, not the "Interpreted as:" label span inside it.
        const interpreted = await card.locator('p', { hasText: 'Interpreted as:' }).last().textContent().catch(() => null)
        const entry: Report['chat'][number] = {
          question,
          refused: answer.refused,
          reason: answer.reason ?? undefined,
          value: metric?.shape === 'scalar' ? metric.value : undefined,
          rows: metric?.shape === 'grouped' ? metric.rows?.length : undefined,
          interpreted: (interpreted ?? '').replace(/\s+/g, ' ').trim() || undefined,
          explanation: answer.explanation ?? undefined,
        }
        report.chat.push(entry)

        if (/conversion rate/i.test(question) && !answer.refused) {
          report.findings.push(`"${question}" was answered instead of refused -- there is no session data in this dataset`)
        }
        if (/total revenue/i.test(question) && kase.revenueNotExpressible && !answer.refused && metric?.value != null) {
          report.findings.push(`"${question}" returned ${metric.value}; ${kase.revenueNotExpressible}`)
        }
        if (!answer.refused && metric?.shape === 'scalar' && metric.value != null) {
          const key = answer.result?.metric_result?.metric_name as keyof Case['expect']
          const expected = kase.expect[key]
          if (expected !== undefined && !near(metric.value, expected) && !answer.queries?.[0]?.time_filter) {
            report.findings.push(`"${question}" → ${key} = ${metric.value}, ground truth ${expected}`)
            expect.soft(metric.value, `${kase.slug} chat ${key}`).toBeCloseTo(expected, 2)
          }
        }
      })
    }

    await shot('chat')
    if (report.browserProblems.length > 0) report.findings.push(`browser errors: ${report.browserProblems.length}`)
    write(report)
    expect.soft(report.browserProblems, `${kase.slug}: browser errors`).toEqual([])
  })
}
