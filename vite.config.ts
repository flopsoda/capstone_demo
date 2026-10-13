import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // K Electronics backend: `uvicorn app.main:app --port 8000`
      '/api': 'http://localhost:8000',
      // Group backend: `uvicorn app.systems.group.app:app --port 8001`
      '/group-api': { target: 'http://localhost:8001', rewrite: (path) => path.replace(/^\/group-api/, '/api') },
    },
  },
})
