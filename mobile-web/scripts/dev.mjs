import { spawn } from 'node:child_process'
import net from 'node:net'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const mobileDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const backendDir = path.resolve(mobileDir, '../backend')
const backendPython = path.join(backendDir, '.venv/bin/python')
const viteBin = path.join(mobileDir, 'node_modules/vite/bin/vite.js')

function portIsOpen(port, host = '127.0.0.1') {
  return new Promise((resolve) => {
    const socket = net.createConnection({ port, host })
    socket.setTimeout(500)
    socket.once('connect', () => {
      socket.destroy()
      resolve(true)
    })
    socket.once('timeout', () => {
      socket.destroy()
      resolve(false)
    })
    socket.once('error', () => resolve(false))
  })
}

async function waitForBackend() {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const response = await fetch('http://127.0.0.1:8001/api/health')
      if (response.ok) return
    } catch {
      // FastAPI imports a large service graph; keep waiting while it starts.
    }
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  throw new Error('FastAPI 启动超时，请检查后端日志。')
}

const children = []
let backendStartedHere = false

if (await portIsOpen(8001)) {
  console.log('[API] 已检测到 http://127.0.0.1:8001，直接复用现有 FastAPI。')
} else {
  console.log('[API] 正在启动 FastAPI：http://127.0.0.1:8001')
  const backend = spawn(
    backendPython,
    ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8001'],
    {
      cwd: backendDir,
      env: { ...process.env, PYTHONPATH: '.' },
      stdio: 'inherit',
    },
  )
  backendStartedHere = true
  children.push(backend)
  backend.once('exit', (code) => {
    if (code && code !== 0) console.error(`[API] FastAPI 已退出，退出码 ${code}`)
  })
  await waitForBackend()
  console.log('[API] FastAPI 健康检查通过。')
}

const vite = spawn(process.execPath, [viteBin, '--host', '0.0.0.0'], {
  cwd: mobileDir,
  env: process.env,
  stdio: 'inherit',
})
children.push(vite)

function shutdown(signal) {
  for (const child of children) {
    if (!child.killed) child.kill(signal)
  }
}

process.once('SIGINT', () => shutdown('SIGINT'))
process.once('SIGTERM', () => shutdown('SIGTERM'))

vite.once('exit', (code) => {
  if (backendStartedHere) shutdown('SIGTERM')
  process.exitCode = code ?? 0
})
