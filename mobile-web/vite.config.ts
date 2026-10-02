import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const apiTarget = process.env.VITE_API_PROXY || 'http://127.0.0.1:8001'
const base = process.env.VITE_BASE || '/'

export default defineConfig({
  plugins: [react()],
  base,
  server: { port: 5174, host: true, proxy: { '/api': apiTarget } },
  preview: { port: 4174, host: true, proxy: { '/api': apiTarget } },
  build: { outDir: 'dist' },
})
