<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'

const data = ref(null)
const loading = ref(false)
const error = ref('')
const ok = ref('')
const filter = ref('all')
const search = ref('')
const adding = ref('')

const progress = ref('')
let pollTimer

const missing = computed(() => (data.value?.pairs || []).filter((r) => r.status_label === '缺行情').length)
const scored = computed(() => (data.value?.pairs || []).filter((r) => r.ok).length)

const FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'green', label: '值得研究' },
  { key: 'yellow', label: '观察' },
  { key: 'red', label: '不适合' },
]

const rows = computed(() => {
  const list = data.value?.pairs || []
  const q = search.value.trim().toLowerCase()
  return list.filter((r) => {
    if (filter.value !== 'all' && r.status !== filter.value) return false
    if (!q) return true
    return `${r.leg_a} ${r.leg_b} ${r.group_label} ${r.relation}`.toLowerCase().includes(q)
  })
})

function light(status) {
  if (status === 'green') return '🟢'
  if (status === 'yellow') return '🟡'
  return '🔴'
}

function zClass(z) {
  if (z == null) return ''
  if (Math.abs(z) >= 2) return 'up'
  if (Math.abs(z) >= 1.5) return 'warn'
  return ''
}

async function loadCached() {
  try {
    data.value = await api.pairScanCached()
  } catch (e) {
    error.value = e.message
  }
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function ensurePoll() {
  if (!pollTimer) pollTimer = setInterval(tick, 800)
}

function applyResult(payload) {
  if (!payload?.pairs) return
  data.value = payload
  const miss = payload.pairs.filter((r) => r.status_label === '缺行情').length
  const okN = payload.pairs.filter((r) => r.ok).length
  if (okN === 0 && miss) {
    error.value = '行情没拉到，多半被限流。不用填代码，过一两分钟再点扫描。'
    ok.value = ''
  } else {
    error.value = ''
    ok.value = `扫完 ${payload.n_pairs || 0} 对 · 有数据 ${okN} 对`
  }
}

async function tick() {
  try {
    const st = await api.pairScanStatus()
    if (st.running) {
      loading.value = true
      progress.value = st.stage || `扫描中 ${st.done || 0}/${st.total || ''}`
      ensurePoll()
      return
    }
    stopPoll()
    loading.value = false
    progress.value = ''
    if (st.error) {
      error.value = st.error
      return
    }
    if (st.result?.pairs?.length) {
      applyResult(st.result)
    } else {
      await loadCached()
    }
  } catch (e) {
    error.value = e.message || '扫描失败'
    if (loading.value) ensurePoll()
    else {
      stopPoll()
      loading.value = false
    }
  }
}

async function scan() {
  if (loading.value) {
    ensurePoll()
    await tick()
    return
  }
  loading.value = true
  error.value = ''
  ok.value = ''
  progress.value = '开始扫描…'
  ensurePoll()
  try {
    await api.pairScan({ force: true })
    await tick()
  } catch (e) {
    loading.value = false
    progress.value = ''
    error.value = e.message || '扫描失败'
    stopPoll()
  }
}

onMounted(async () => {
  await loadCached()
  await tick()
})
onUnmounted(() => stopPoll())

async function addToStrategy(row) {
  adding.value = `${row.leg_a}/${row.leg_b}`
  error.value = ''
  ok.value = ''
  try {
    const created = await api.pairToStrategy({
      leg_a: row.leg_a,
      leg_b: row.leg_b,
      notes: `${row.relation} · ${row.why || ''}`,
    })
    ok.value = `已加到策略：${created.name}。去「套利策略」里计算。`
  } catch (e) {
    error.value = e.message || '加不进去'
  } finally {
    adding.value = ''
  }
}
</script>

<template>
  <p class="goal-lead">
    不用填股票代码。左侧行业池已经分好（支付、半导体、银行等），点「扫描配对」即可。
    不是找两只相关的股票：先要有稳定经济关系，再要价差平稳、会回归。
    MU/SPY、MU/GOOG 会作为反例出现。
  </p>
  <div class="range-row">
    <button class="btn btn-primary" type="button" @click="scan">
      {{ loading ? '扫描中…' : (rows.length ? '重新扫描' : '扫描配对') }}
    </button>
    <div class="pills">
      <button
        v-for="f in FILTERS"
        :key="f.key"
        class="pill"
        :class="{ active: filter === f.key }"
        type="button"
        @click="filter = f.key"
      >
        {{ f.label }}
      </button>
    </div>
    <div class="watch-edit" style="flex: 1; margin: 0">
      <input v-model="search" placeholder="筛选结果，可不填" />
    </div>
  </div>
  <p class="sub" v-if="progress">{{ progress }}</p>
  <div class="error" v-if="error">{{ error }}</div>
  <p class="ok-msg" v-else-if="ok">{{ ok }}</p>
  <p class="sub" v-if="data?.as_of">
    {{ data.as_of }} · {{ data.n_pairs || 0 }} 对 / {{ data.n_symbols || 0 }} 只
    · 有数据 {{ scored }} · 缺行情 {{ missing }}
    · 相关 ≥ {{ data.filters?.min_corr }} · 半衰期 {{ data.filters?.half_life?.[0] }}～{{ data.filters?.half_life?.[1] }} 天
  </p>
  <p class="sub" v-if="data?.note">{{ data.note }}</p>

  <section class="panel" style="margin-top: 12px">
    <div class="table-wrap" v-if="rows.length">
      <table>
        <thead>
          <tr>
            <th>配对</th>
            <th>关系</th>
            <th>协整</th>
            <th>Half-life</th>
            <th>β 稳定</th>
            <th>相关</th>
            <th>Z</th>
            <th>状态</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="`${r.leg_a}-${r.leg_b}`">
            <td>
              <b>{{ r.leg_a }} / {{ r.leg_b }}</b>
              <div class="sub">{{ r.group_label }}</div>
            </td>
            <td>
              {{ r.relation }}
              <div class="sub" v-if="r.counter">反例</div>
            </td>
            <td>{{ r.adf_pass ? '✅' : '❌' }}</td>
            <td class="mono">{{ r.half_life ?? '—' }}{{ r.half_life != null ? 'd' : '' }}</td>
            <td>{{ r.beta_stable || '—' }}</td>
            <td class="mono">{{ r.corr ?? '—' }}</td>
            <td class="mono" :class="zClass(r.z)">{{ r.z ?? '—' }}</td>
            <td>
              {{ light(r.status) }} {{ r.status_label }}
              <div class="sub">{{ r.why }}</div>
              <div class="sub" v-if="r.earnings">
                财报 {{ r.earnings.symbol }} {{ r.earnings.date }}
              </div>
            </td>
            <td class="pos-actions">
              <button
                class="icon-btn"
                type="button"
                :disabled="!!adding"
                @click="addToStrategy(r)"
              >
                {{ adding === `${r.leg_a}/${r.leg_b}` ? '…' : '加入策略' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="empty">
      {{ loading ? (progress || '正在拉行情并做协整检验…') : '还没有结果。不用填代码，点上面的「扫描配对」。' }}
    </div>
  </section>
</template>
