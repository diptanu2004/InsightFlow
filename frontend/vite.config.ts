import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The backend serves its routes at the root (`/projects/{id}`, `/auth/login`, `/organizations`),
// which would collide with this app's own client-side router paths. So the API is proxied under
// an `/api` prefix that's stripped on the way out: the UI keeps `/projects/...` for itself, and
// because requests are same-origin in dev, CORS_ALLOWED_ORIGINS can stay empty (fails closed, as
// Phase 6 intended). Pointing at a remote backend instead means setting VITE_API_BASE_URL to its
// origin and adding this app's origin to CORS_ALLOWED_ORIGINS there.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': {
        target: process.env.VITE_PROXY_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
