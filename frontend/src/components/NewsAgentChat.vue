<script setup>
import { nextTick, ref } from 'vue'

const question = ref('')
const messages = ref([]) // { role: 'user'|'assistant', content, steps? }
const loading = ref(false)
const error = ref('')
const scrollEl = ref(null)

async function scrollToBottom() {
  await nextTick()
  if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight
}

async function send() {
  const text = question.value.trim()
  if (!text || loading.value) return
  error.value = ''
  messages.value.push({ role: 'user', content: text })
  question.value = ''
  loading.value = true
  scrollToBottom()

  try {
    const history = messages.value
      .slice(0, -1)
      .map((m) => ({ role: m.role === 'user' ? 'user' : 'assistant', content: m.content }))
    const res = await fetch('/agent-api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: text, history }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.error || `请求失败（HTTP ${res.status}）`)
    messages.value.push({ role: 'assistant', content: data.answer, steps: data.steps || [] })
  } catch (e) {
    error.value = e.message || '连不上 Agent 服务，先确认 news-agent-service 起了没有'
  } finally {
    loading.value = false
    scrollToBottom()
  }
}

function toolStepsOf(steps) {
  return (steps || []).filter((s) => s.type === 'tool_call')
}
</script>

<template>
  <div>
    <h2>智能聊天</h2>
    <p class="desc">问新闻相关的问题，Agent 会自己判断要不要查新闻/查股价，手写的 tool-calling 循环（不是 AgentExecutor），基于 LangChain.js + Claude。</p>

    <div class="agent-chat-box">
      <div class="agent-chat-log" ref="scrollEl">
        <p class="sub" v-if="!messages.length">试着问："最近有什么关于英伟达的新闻？" 或 "美联储降息对科技股有什么影响？"</p>
        <div v-for="(m, i) in messages" :key="i" class="agent-msg" :class="m.role">
          <div class="agent-msg-bubble">{{ m.content }}</div>
          <details v-if="m.role === 'assistant' && toolStepsOf(m.steps).length" class="agent-steps">
            <summary>看 Agent 调用了哪些工具（{{ toolStepsOf(m.steps).length }} 次）</summary>
            <div v-for="(s, j) in toolStepsOf(m.steps)" :key="j" class="agent-step-item">
              <code>{{ s.name }}</code>
              <span class="sub">{{ JSON.stringify(s.args) }}</span>
            </div>
          </details>
        </div>
        <div class="agent-msg assistant" v-if="loading">
          <div class="agent-msg-bubble agent-thinking">思考中…</div>
        </div>
      </div>
      <div class="error" v-if="error">{{ error }}</div>
      <div class="agent-chat-input">
        <input
          v-model="question"
          placeholder="问点什么…"
          :disabled="loading"
          @keydown.enter.prevent="send"
        />
        <button class="btn btn-primary" type="button" :disabled="loading || !question.trim()" @click="send">
          {{ loading ? '…' : '发送' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.agent-chat-box {
  margin-top: 10px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface);
  overflow: hidden;
}
.agent-chat-log {
  max-height: 420px;
  overflow-y: auto;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.agent-msg {
  display: flex;
  flex-direction: column;
}
.agent-msg.user {
  align-items: flex-end;
}
.agent-msg.assistant {
  align-items: flex-start;
}
.agent-msg-bubble {
  max-width: 80%;
  padding: 8px 12px;
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.55;
  white-space: pre-wrap;
}
.agent-msg.user .agent-msg-bubble {
  background: var(--accent-dim);
  color: var(--text);
}
.agent-msg.assistant .agent-msg-bubble {
  background: var(--surface-2);
  border: 1px solid var(--line);
  color: var(--text);
}
.agent-thinking {
  color: var(--muted);
  font-style: italic;
}
.agent-steps {
  margin-top: 4px;
  font-size: 12px;
  color: var(--muted);
}
.agent-steps summary {
  cursor: pointer;
}
.agent-step-item {
  margin: 4px 0 0 12px;
  display: flex;
  gap: 8px;
  align-items: baseline;
}
.agent-chat-input {
  display: flex;
  gap: 8px;
  padding: 10px;
  border-top: 1px solid var(--line);
}
.agent-chat-input input {
  flex: 1;
}
</style>
