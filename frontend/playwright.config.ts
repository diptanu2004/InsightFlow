import { defineConfig } from '@playwright/test'

// End-to-end only, against the real stack: Postgres/MinIO/Redis (docker compose), the backend on :8000,
// a discovery worker, `npm run dev` on :5173, and a real GROQ_API_KEY. Nothing is mocked -- see
// docs/hld.md §9. Deliberately not wired to `webServer`: the backend and worker need their own env.
export default defineConfig({
  testDir: './e2e',
  // One journey: real schema discovery over ~60 MB of Olist CSVs plus several LLM calls.
  timeout: 15 * 60_000,
  expect: { timeout: 30_000 },
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    // Numbers are asserted as rendered text, so the formatting locale is pinned.
    locale: 'en-US',
    viewport: { width: 1280, height: 1000 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
})
