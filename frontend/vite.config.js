import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8001',
      '/agent-api': { target: 'http://127.0.0.1:4001', rewrite: (path) => path.replace(/^\/agent-api/, '') },
    },
  },
})
