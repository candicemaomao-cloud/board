<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { api } from '../api'
import { TIMEFRAMES } from '../config/strategies'
import { money } from '../format'
import { clearQuoteTimer } from '../marketHours'

const INTERVALS = [
  { sec: 30, label: '30秒' },
  { sec: 60, label: '1分钟' },
  { sec: 300, label: '5分钟' },
  { sec: 900, label: '15分钟' },
  { sec: 1800, label: '30分钟' },
  { sec: 3600, label: '60分钟' },
]

const plans = ref([])
const people = ref([])
const rows = ref([])
const nextRefresh = ref(null)
const loading = ref(false)
const refreshing = ref(false)
const saving = ref(false)
const toggling = ref(null)
const error = ref('')
const ok = ref('')
const listOk = ref('')
const listError = ref('')
const filter = ref('all')
const search = ref('')
const screen = ref('list')
const editingId = ref(null)
let refreshTimer

const form = reactive({
  symbol: '',
  join: 'and',
  notes: '',
  strategy_id: '',
  recipient_id: '',
  enabled: false,
  interval_sec: 30,
})

const filtered = computed(() => {
  const q = search.value.trim().toUpperCase()
  let list = rows.value
  if (filter.value === 'hit') list = list.filter((row) => row.last_hit)
  if (filter.value === 'miss') list = list.filter((row) => row.last_hit === false)
  if (q) list = list.filter((row) => String(row.symbol || '').toUpperCase().includes(q))
  return list
})

const hitCount = computed(() => rows.value.filter((row) => row.last_hit).length)
const onCount = computed(() => rows.value.filter(isOn).length)

function isOn(row) {
  return !!(row?.enabled ?? row?.push)
}

function intervalLabel(sec) {
  return INTERVALS.find((row) => row.sec === Number(sec))?.label || `${sec}秒`
}

function px(n) {
  if (n == null || n === '') return '—'
  return money(n, { signed: false, withSymbol: false })
}

function joinLabel(op) {
  return op === 'or' ? '或' : '且'
}

function tfLabel(key) {
  return TIMEFRAMES.find((row) => row.key === key)?.label || key || 'D线'
}

function resetForm() {
  editingId.value = null
  error.value = ''
  ok.value = ''
  form.symbol = ''
  form.join = 'and'
  form.notes = ''
  form.strategy_id = ''
  form.recipient_id = ''
  form.enabled = false
  form.interval_sec = 30
}

function backToList() {
  screen.value = 'list'
  resetForm()
}

function openNew() {
  listOk.value = ''
  listError.value = ''
  resetForm()
  screen.value = 'edit'
}

function editRow(row) {
  listOk.value = ''
  listError.value = ''
  editingId.value = row.id
  error.value = ''
  ok.value = ''
  form.symbol = row.symbol
  form.join = row.join || 'and'
  form.notes = row.notes || ''
  const token = (row.indicators || []).find((id) => String(id).startsWith('s:'))
  const sid = token ? Number(String(token).slice(2)) : NaN
  form.strategy_id = plans.value.some((item) => item.id === sid) ? sid : ''
  const rid = Number((row.recipient_ids || [])[0])
  form.recipient_id = people.value.some((item) => item.id === rid) ? rid : ''
  form.enabled = isOn(row)
  form.interval_sec = Number(row.interval_sec) || 30
  screen.value = 'edit'
}

function refreshHint() {
  if (!onCount.value) return '都关着。打开之后才会按间隔自动计算并推送。'
  if (!nextRefresh.value) return `已开 ${onCount.value} 条，后台按各自间隔计算。`
  const at = new Date(nextRefresh.value * 1000)
  const soon = at.getTime() <= Date.now() + 2000
  return soon
    ? `已开 ${onCount.value} 条，正在按间隔计算。`
    : `已开 ${onCount.value} 条，下次约 ${at.toLocaleString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`
}

function armRefresh() {
  clearQuoteTimer(refreshTimer)
  if (!onCount.value) return
  const wait = nextRefresh.value
    ? Math.min(Math.max(nextRefresh.value * 1000 - Date.now(), 3000), 30000)
    : 5000
  refreshTimer = setTimeout(() => loadList(), wait)
}

function applyPayload(data, { quiet = false } = {}) {
  rows.value = data.items || []
  nextRefresh.value = data.next_refresh || null
  if (!quiet) {
    if (data.notified) listOk.value = `已推送 ${data.notified} 条命中`
    if (data.notify_errors?.length) listError.value = data.notify_errors.join('；')
  }
  armRefresh()
}

async function loadMeta() {
  try {
    plans.value = (await api.strategyPlans()).filter((p) => p.source !== 'file')
  } catch {
    plans.value = []
  }
  try {
    const data = await api.recipients()
    people.value = data.items || []
  } catch {
    people.value = []
  }
}

async function loadList() {
  try {
    applyPayload(await api.alerts(), { quiet: true })
  } catch (e) {
    listError.value = e.message
  }
}

async function load(force = false) {
  loading.value = !rows.value.length
  refreshing.value = true
  listError.value = ''
  try {
    applyPayload(await api.refreshAlerts(force))
  } catch (e) {
    listError.value = e.message
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

function payload() {
  const rid = Number(form.recipient_id)
  return {
    symbol: form.symbol.trim().toUpperCase(),
    join: form.join,
    notes: form.notes.trim() || null,
    indicators: form.strategy_id ? [`s:${form.strategy_id}`] : [],
    recipient_ids: rid ? [rid] : [],
    enabled: !!form.enabled,
    interval_sec: Number(form.interval_sec) || 30,
  }
}

async function save() {
  error.value = ''
  ok.value = ''
  if (!form.symbol.trim()) {
    error.value = '请输入股票代码'
    return
  }
  if (!form.strategy_id) {
    error.value = '请选择指标'
    return
  }
  if (!people.value.length) {
    error.value = '还没有推送人。去「推送人」先添加。'
    return
  }
  if (!form.recipient_id) {
    error.value = '请选择推送人'
    return
  }
  saving.value = true
  try {
    const wasEdit = !!editingId.value
    const saved = wasEdit
      ? await api.updateAlert(editingId.value, payload())
      : await api.createAlert(payload())
    const data = await api.alerts()
    applyPayload(data, { quiet: true })
    const code = saved.symbol
    backToList()
    listOk.value = wasEdit ? `「${code}」已更新` : `「${code}」已保存`
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

async function remove(row) {
  if (!confirm(`删除 ${row.symbol} 这条推送？`)) return
  listError.value = ''
  listOk.value = ''
  try {
    await api.deleteAlert(row.id)
    if (editingId.value === row.id) backToList()
    rows.value = rows.value.filter((item) => item.id !== row.id)
    listOk.value = `「${row.symbol}」已删除`
  } catch (e) {
    listError.value = e.message
  }
}

async function toggleRow(row) {
  toggling.value = row.id
  listError.value = ''
  listOk.value = ''
  try {
    const saved = await api.setAlertPush(row.id, { enabled: !isOn(row) })
    const idx = rows.value.findIndex((item) => item.id === row.id)
    if (idx >= 0) rows.value[idx] = { ...rows.value[idx], ...saved }
    if (isOn(saved)) {
      armRefresh()
      const test = saved.push_test
      if (test?.ok) {
        listOk.value = `「${row.symbol}」已开启，已发测试推送（首次仅为测试）`
      } else if (test?.error) {
        listError.value = `已开启，但测试推送失败：${test.error}`
        listOk.value = `「${row.symbol}」已开启，按 ${intervalLabel(saved.interval_sec)} 计算`
      } else {
        listOk.value = `「${row.symbol}」已开启，按 ${intervalLabel(saved.interval_sec)} 计算`
      }
    } else {
      armRefresh()
      listOk.value = `「${row.symbol}」已关闭`
    }
  } catch (e) {
    listError.value = e.message
  } finally {
    toggling.value = null
  }
}

async function changeInterval(row, value) {
  listError.value = ''
  try {
    const saved = await api.setAlertPush(row.id, { interval_sec: Number(value) })
    const idx = rows.value.findIndex((item) => item.id === row.id)
    if (idx >= 0) rows.value[idx] = { ...rows.value[idx], ...saved }
    armRefresh()
  } catch (e) {
    listError.value = e.message
  }
}

onMounted(() => {
  loadMeta()
  loadList()
})

onUnmounted(() => {
  clearQuoteTimer(refreshTimer)
})
</script>

<template>
  <template v-if="screen === 'edit'">
    <div class="range-row">
      <button class="btn" type="button" @click="backToList">返回列表</button>
    </div>
    <p class="goal-lead">填股票代码，选指标和推送人。打开之后按间隔自动计算，命中就推给这个人。</p>
    <section class="panel">
      <div class="formula-head" style="margin-bottom: 10px">
        <h2 style="margin: 0">{{ editingId ? '改推送' : '新推送' }}</h2>
      </div>
      <div class="form-grid" style="margin-top: 10px">
        <div class="field">
          <label>股票代码</label>
          <input v-model="form.symbol" placeholder="TSLA / NVDA" @keydown.enter.prevent="save" />
        </div>
        <div class="field">
          <label>指标</label>
          <select v-model="form.strategy_id" :disabled="!plans.length">
            <option value="">{{ plans.length ? '请选择指标' : '还没有指标' }}</option>
            <option v-for="item in plans" :key="item.id" :value="item.id">
              {{ item.name }} · {{ item.side === 'short' ? '做空' : '做多' }} · {{ tfLabel(item.timeframe) }}
            </option>
          </select>
          <div class="sub" v-if="!plans.length">去「指标」页保存后再来选。</div>
        </div>
        <div class="field">
          <label>推送人</label>
          <select v-model="form.recipient_id" :disabled="!people.length">
            <option value="">{{ people.length ? '请选择推送人' : '还没有推送人' }}</option>
            <option v-for="item in people" :key="item.id" :value="item.id">
              {{ item.name }}{{ item.channels?.length ? ` · ${item.channels.join('/')}` : '' }}
            </option>
          </select>
          <div class="sub" v-if="!people.length">去「推送人」先添加。</div>
        </div>
        <div class="field">
          <label>备注</label>
          <input v-model="form.notes" placeholder="可选" />
        </div>
        <div class="field">
          <label>开启</label>
          <label class="check" style="min-height: 42px">
            <input v-model="form.enabled" type="checkbox" />
            {{ form.enabled ? '开着，按间隔自动算并推送' : '关着，不自动算也不推' }}
          </label>
        </div>
        <div class="field">
          <label>计算间隔</label>
          <select v-model.number="form.interval_sec">
            <option v-for="item in INTERVALS" :key="item.sec" :value="item.sec">{{ item.label }}</option>
          </select>
        </div>
      </div>
      <div class="modal-actions" style="margin-top: 12px; justify-content: flex-start">
        <button class="btn btn-primary" type="button" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : editingId ? '更新' : '保存' }}
        </button>
      </div>
      <div class="error" v-if="error">{{ error }}</div>
      <div class="ok-msg" v-else-if="ok">{{ ok }}</div>
    </section>
  </template>

  <template v-else>
    <p class="goal-lead">按股票代码盯指标组合。打开一条后，按所选间隔自动计算，符合条件就推给推送人。</p>
    <div class="range-row">
      <div class="watch-edit" style="flex: 1; margin: 0">
        <input v-model="search" placeholder="搜索股票代码" />
      </div>
      <button class="btn" type="button" :disabled="refreshing" @click="load(true)">
        {{ refreshing ? '刷新中…' : '立刻刷新' }}
      </button>
      <button class="btn btn-primary" type="button" @click="openNew">添加</button>
    </div>
    <div class="pills" style="margin-top: 8px">
      <button class="pill" type="button" :class="{ active: filter === 'all' }" @click="filter = 'all'">
        全部 {{ rows.length }}
      </button>
      <button class="pill" type="button" :class="{ active: filter === 'hit' }" @click="filter = 'hit'">
        已命中 {{ hitCount }}
      </button>
      <button class="pill" type="button" :class="{ active: filter === 'miss' }" @click="filter = 'miss'">
        未命中 {{ rows.length - hitCount }}
      </button>
    </div>
    <p class="sub">{{ refreshHint() }}</p>
    <div class="error" v-if="listError">{{ listError }}</div>
    <p class="ok-msg" v-else-if="listOk">{{ listOk }}</p>
    <p class="sub" v-else-if="loading">正在按收盘价计算…</p>

    <section class="panel" style="margin-top: 12px">
      <div class="table-wrap" v-if="filtered.length">
        <table>
          <thead>
            <tr>
              <th>开启</th>
              <th>间隔</th>
              <th>代码</th>
              <th>指标</th>
              <th>推送人</th>
              <th>价格</th>
              <th>时间</th>
              <th>状态</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in filtered" :key="row.id">
              <td>
                <button
                  class="pill"
                  type="button"
                  :class="{ active: isOn(row) }"
                  :disabled="toggling === row.id"
                  @click="toggleRow(row)"
                >
                  {{ toggling === row.id ? '…' : isOn(row) ? '开' : '关' }}
                </button>
              </td>
              <td>
                <select
                  class="table-select"
                  :value="row.interval_sec || 30"
                  @change="changeInterval(row, $event.target.value)"
                >
                  <option v-for="item in INTERVALS" :key="item.sec" :value="item.sec">{{ item.label }}</option>
                </select>
              </td>
              <td>
                <b>{{ row.symbol }}</b>
                <div class="sub" v-if="row.notes">{{ row.notes }}</div>
              </td>
              <td>
                {{ joinLabel(row.join) }} · {{ (row.indicator_names || []).join('、') }}
                <div class="clause-list" style="margin-top: 6px" v-if="row.last_detail?.clauses?.length">
                  <span
                    v-for="clause in row.last_detail.clauses"
                    :key="clause.id"
                    class="clause-chip"
                    :class="clause.hit ? 'up-dim' : 'down-dim'"
                  >
                    {{ clause.hit ? '成立' : '不成立' }} {{ clause.name }}
                  </span>
                </div>
              </td>
              <td>{{ (row.recipient_names || []).join('、') || '—' }}</td>
              <td class="mono">{{ px(row.last_price) }}</td>
              <td>{{ row.last_asof || '—' }}</td>
              <td>
                <span v-if="row.last_error" class="error">{{ row.last_error }}</span>
                <template v-else>
                  <span class="match-badge" :class="row.last_hit ? 'up-dim' : 'down-dim'">
                    {{ row.last_hit ? '命中' : '未命中' }}
                  </span>
                </template>
              </td>
              <td class="pos-actions">
                <button class="icon-btn" type="button" @click="editRow(row)">改</button>
                <button class="icon-btn" type="button" @click="remove(row)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="empty">
        {{ search.trim() ? '没有这个代码的推送。' : '还没有推送。点右上角添加。' }}
      </div>
    </section>
  </template>
</template>
