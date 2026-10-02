import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 本地前端可切到云端后端：设 VITE_API_PROXY=http://47.129.121.10 后重启 npm run dev
const apiTarget = process.env.VITE_API_PROXY || 'http://127.0.0.1:8001'
const agentTarget = process.env.VITE_AGENT_PROXY || (process.env.VITE_API_PROXY ? process.env.VITE_API_PROXY : 'http://127.0.0.1:4001')

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': apiTarget,
      '/agent-api': {
        target: agentTarget,
        rewrite: (path) => path.replace(/^\/agent-api/, ''),
      },
    },
  },
})
