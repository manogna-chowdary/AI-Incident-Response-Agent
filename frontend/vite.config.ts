import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The backend is mounted at the root: it serves /health and /incidents with no
// path prefix. The client therefore asks for /api/... and the rewrite below
// strips that prefix on the way through, which keeps the browser on one origin
// (no CORS preflight) without the backend needing a prefix of its own.
const proxy = {
  '/api': {
    target: 'http://localhost:8000',
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/api/, ''),
  },
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: { proxy },
  preview: { proxy },
})
