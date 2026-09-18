import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  // One .env, at the repo root. The backend and the frontend read the same file.
  const env = loadEnv(mode, '..', '')
  const backend = `http://localhost:${env.API_PORT || '8000'}`

  return {
    plugins: [react()],
    envDir: '..',
    server: {
      port: Number(env.WEB_PORT || 5173),
      // Fail rather than silently moving to another port — the proxy target
      // and the browser tab both assume this one.
      strictPort: true,
      // Same origin in dev: the browser never crosses an origin to reach the
      // API, so there is no CORS middleware and no preflight to debug.
      proxy: {
        '/health': backend,
        '/api': backend,
      },
    },
  }
})
