import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App.tsx'
import './index.css'

// Analytics results are deterministic for a given dataset (a Dataset row is immutable once
// created -- a re-upload makes a new row), so refetching on window focus buys nothing and just
// spends LLM budget on the dashboard/chat routes. Retries are off for the same reason: a 4xx
// from a validator or a refusal is a real answer, not a transient failure worth repeating.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, retry: false },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
