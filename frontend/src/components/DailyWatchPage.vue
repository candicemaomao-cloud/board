<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../api'

const emit = defineEmits(['open-analysis', 'open-detail'])

const DIRECTIONS = ['多', '空', '横盘']
const SECTORS = ['银行', '科技', '半导体', '通信', '太空', '医疗', '可选消费','金融','能源','必需消费品','工业','公用事业','房地产','原材料','通讯服务', '其他']

const IND_TIMEFRAMES = [
  { key: '1d', label: '日' },
  { key: '4h', label: '4h' },
  { key: '30m', label: '30min' },
  { key: '5m', label: '5min' },
]

const rows = ref([])
const search = ref('')
const sectorFilter = ref('')
const screen = ref('list')
const editingId = ref(null)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const listOk = ref('')
const listError = ref('')
const lookupLoading = ref(false)
const refreshingMap = reactive({})

const indDialog = ref(false)
const indCatalog = ref({ items: [], groups: [] })
const indSymbol = ref('')
const indTimeframe = ref('1d')
const indLoading = ref(false)
const indError = ref('')
const indResult = ref(null)
const indGroup = ref('全部')
const indOnlyHit = ref(true)
const fngLoading = ref(false)
const fngError = ref('')
const fngData = ref(null)

const indMap = computed(() =>
  Object.fromEntries((indCatalog.value.items || []).map((row) => [row.id, row])),
)

const indItems = computed(() => {
  const flags = indResult.value?.flags || {}
  const items = Object.entries(flags).map(([id, hit]) => {
    const meta = indMap.value[id] || { id, name: id, group: '其他', hint: '' }
    return { ...meta, hit: !!hit }
  })
  items.sort(
    (a, b) =>
      Number(b.hit) - Number(a.hit) ||
      String(a.group).localeCompare(String(b.group)) ||
      String(a.name).localeCompare(String(b.name)),
  )
  return items
})

const indGroups = computed(() => {
  const names = [...new Set(indItems.value.map((x) => x.group || '其他'))]
  return ['全部', ...names]
})

const indShown = computed(() => {
  let items = indItems.value
  if (indGroup.value !== '全部') items = items.filter((x) => x.group === indGroup.value)
  if (indOnlyHit.value) items = items.filter((x) => x.hit)
  return items
})

const indHitCount = computed(() => indItems.value.filter((x) => x.hit).length)

const indPrices = computed(() => indResult.value?.prices || {})

function fngToneClass(toneOrValue) {
  const t = typeof toneOrValue === 'number' ? null : toneOrValue
  if (t === 'fear' || t === '极度恐惧' || t === '恐惧') return 'down'
  if (t === 'greed' || t === '贪婪' || t === '极度贪婪') return 'up'
  if (typeof toneOrValue === 'number') {
    if (toneOrValue <= 44) return 'down'
    if (toneOrValue >= 56) return 'up'
  }
  return ''
}

const form = reactive({
  symbol: '',
  prev_close: '',
  prev_open: '',
  prev_low: '',
  prev_high: '',
  current_price: '',
  gamma_low: '',
  gamma_high: '',
  regression_line: '',
  max_pain: '',
  short_entry_price: '',
  long_entry_price: '',
  is_potential: false,
  market_direction: '横盘',
  sector: '其他',
  events: '',
})

const filtered = computed(() => {
  const q = search.value.trim().toUpperCase()
  return rows.value.filter((row) => {
    if (q && !(row.symbol || '').includes(q)) return false
    if (sectorFilter.value && row.sector !== sectorFilter.value) return false
    return true
  })
})

function num(v) {
  return v === null || v === undefined || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(2)
}

async function runLookup() {
  const code = form.symbol.trim().toUpperCase()
  if (!code) {
    error.value = '先填股票代码再查询'
    return
  }
  form.symbol = code
  error.value = ''
  lookupLoading.value = true
  try {
    const q = await api.lookupDailyWatch(code)
    form.prev_close = q.prev_close ?? ''
    form.prev_open = q.prev_open ?? ''
    form.prev_low = q.prev_low ?? ''
    form.prev_high = q.prev_high ?? ''
    form.current_price = q.current_price ?? ''
  } catch (e) {
    error.value = e.message
  } finally {
    lookupLoading.value = false
  }
}

function isUndervalued(row) {
  return row.regression_line != null && row.current_price != null && row.current_price < row.regression_line * 0.95
}

function rowClass(row) {
  if (isUndervalued(row)) return 'undervalued-line'
  if (row.is_potential) return 'potential-line'
  return ''
}

function directionTone(d) {
  if (d === '多') return 'up'
  if (d === '空') return 'down'
  return ''
}

function resetForm() {
  editingId.value = null
  error.value = ''
  Object.assign(form, {
    symbol: '',
    prev_close: '',
    prev_open: '',
    prev_low: '',
    prev_high: '',
    current_price: '',
    gamma_low: '',
    gamma_high: '',
    regression_line: '',
    max_pain: '',
    short_entry_price: '',
    long_entry_price: '',
    is_potential: false,
    market_direction: '横盘',
    sector: '其他',
    events: '',
  })
}

function fillForm(row) {
  Object.assign(form, {
    symbol: row.symbol || '',
    prev_close: row.prev_close ?? '',
    prev_open: row.prev_open ?? '',
    prev_low: row.prev_low ?? '',
    prev_high: row.prev_high ?? '',
    current_price: row.current_price ?? '',
    gamma_low: row.gamma_low ?? '',
    gamma_high: row.gamma_high ?? '',
    regression_line: row.regression_line ?? '',
    max_pain: row.max_pain ?? '',
    short_entry_price: row.short_entry_price ?? '',
    long_entry_price: row.long_entry_price ?? '',
    is_potential: !!row.is_potential,
    market_direction: row.market_direction || '横盘',
    sector: row.sector || '其他',
    events: row.events || '',
  })
}

function backToList() {
  screen.value = 'list'
  resetForm()
}

async function ensureIndCatalog() {
  if (indCatalog.value.items?.length) return
  try {
    indCatalog.value = await api.strategyIndicators()
  } catch {
    indCatalog.value = { items: [], groups: [] }
  }
}

async function loadFearGreed() {
  fngLoading.value = true
  fngError.value = ''
  try {
    fngData.value = await api.marketFearGreed({ limit: 30 })
  } catch (e) {
    fngData.value = null
    fngError.value = e.message || '恐惧贪婪加载失败'
  } finally {
    fngLoading.value = false
  }
}

async function runIndicators() {
  const code = indSymbol.value.trim().toUpperCase()
  if (!code) {
    indError.value = '没有股票代码'
    return
  }
  indSymbol.value = code
  indLoading.value = true
  indError.value = ''
  try {
    await ensureIndCatalog()
    indResult.value = await api.strategyCalc({
      symbol: code,
      timeframe: indTimeframe.value,
    })
  } catch (e) {
    indResult.value = null
    indError.value = e.message || '计算失败'
  } finally {
    indLoading.value = false
  }
}

async function openIndicators(row) {
  listOk.value = ''
  listError.value = ''
  indSymbol.value = (row.symbol || '').toUpperCase()
  indTimeframe.value = '1d'
  indGroup.value = '全部'
  indOnlyHit.value = true
  indResult.value = null
  indError.value = ''
  fngData.value = null
  fngError.value = ''
  indDialog.value = true
  await Promise.all([runIndicators(), loadFearGreed()])
}

function pickIndTf(key) {
  if (indTimeframe.value === key) return
  indTimeframe.value = key
}

watch(indTimeframe, () => {
  if (indDialog.value && indSymbol.value) runIndicators()
})

function pxInd(n) {
  if (n == null || n === '') return '—'
  const v = Number(n)
  if (!Number.isFinite(v)) return '—'
  return Math.abs(v) >= 1 ? v.toFixed(2) : v.toFixed(4)
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
  fillForm(row)
  screen.value = 'edit'
}

function numOrNull(v) {
  return v === '' || v === null || v === undefined ? null : Number(v)
}

function payload() {
  return {
    symbol: form.symbol.trim(),
    prev_close: numOrNull(form.prev_close),
    prev_open: numOrNull(form.prev_open),
    prev_low: numOrNull(form.prev_low),
    prev_high: numOrNull(form.prev_high),
    current_price: numOrNull(form.current_price),
    gamma_low: numOrNull(form.gamma_low),
    gamma_high: numOrNull(form.gamma_high),
    regression_line: numOrNull(form.regression_line),
    max_pain: numOrNull(form.max_pain),
    short_entry_price: numOrNull(form.short_entry_price),
    long_entry_price: numOrNull(form.long_entry_price),
    is_potential: !!form.is_potential,
    market_direction: form.market_direction,
    sector: form.sector,
    events: form.events.trim() || null,
  }
}

async function load() {
  loading.value = !rows.value.length
  listError.value = ''
  try {
    rows.value = await api.dailyWatches()
  } catch (e) {
    listError.value = e.message
  } finally {
    loading.value = false
  }
}

async function save() {
  error.value = ''
  if (!form.symbol.trim()) {
    error.value = '请填写股票代码'
    return
  }
  saving.value = true
  try {
    const wasEdit = !!editingId.value
    const saved = wasEdit
      ? await api.updateDailyWatch(editingId.value, payload())
      : await api.createDailyWatch(payload())
    await load()
    backToList()
    listOk.value = wasEdit ? `「${saved.symbol}」已更新` : `「${saved.symbol}」已加入股票列表`
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

async function remove(row) {
  if (!confirm(`从股票列表里删除「${row.symbol}」？`)) return
  listError.value = ''
  listOk.value = ''
  try {
    await api.deleteDailyWatch(row.id)
    rows.value = rows.value.filter((item) => item.id !== row.id)
    listOk.value = `「${row.symbol}」已删除`
  } catch (e) {
    listError.value = e.message
  }
}

function refreshKey(id, action) {
  return `${id}:${action}`
}

// 只负责发请求 + 更新那一行的数据，不碰 listError/listOk——单个点击和一键更新
// 共用这个，各自决定怎么展示结果（单个点击直接提示，一键更新攒成一份汇总）。
async function doRefresh(row, action, apiFn) {
  const key = refreshKey(row.id, action)
  if (refreshingMap[key]) return { ok: false, skipped: true }
  refreshingMap[key] = true
  try {
    const updated = await apiFn(row.id)
    const idx = rows.value.findIndex((r) => r.id === row.id)
    if (idx !== -1) rows.value[idx] = updated
    return { ok: true, data: updated }
  } catch (e) {
    return { ok: false, error: e.message }
  } finally {
    refreshingMap[key] = false
  }
}

async function refreshRow(row, action, apiFn, messageFn) {
  listError.value = ''
  listOk.value = ''
  const result = await doRefresh(row, action, apiFn)
  if (result.skipped) return
  if (result.ok) {
    listOk.value = messageFn ? messageFn(result.data) : `「${row.symbol}」已更新`
  } else {
    listError.value = `${row.symbol}：${result.error}`
  }
}

function refreshPrice(row) {
  return refreshRow(row, 'price', api.refreshDailyWatchPrice)
}
function refreshOptions(row) {
  return refreshRow(row, 'options', api.refreshDailyWatchOptions, (updated) => {
    const skewText = updated.skew_pct == null ? '' : `，偏斜 ${(updated.skew_pct * 100).toFixed(2)}%（正=空/负=多）`
    return `「${updated.symbol}」期权走向已更新为 ${updated.market_direction}${skewText}`
  })
}
function refreshRegression(row) {
  return refreshRow(row, 'regression', api.refreshDailyWatchRegression)
}

const bulkUpdating = ref(false)
const bulkProgress = reactive({ done: 0, total: 0 })

const ACTION_LABELS = { price: '股票', options: '期权', regression: '回归线' }

async function refreshAll() {
  if (bulkUpdating.value) return
  const targets = [...filtered.value]
  if (!targets.length) return
  const scope = sectorFilter.value || search.value.trim() ? '当前筛选出的' : '全部'
  if (!confirm(`一键更新${scope} ${targets.length} 支股票的价格/期权/回归线，可能要花一点时间，确定继续吗？`)) return

  bulkUpdating.value = true
  bulkProgress.done = 0
  bulkProgress.total = targets.length
  listError.value = ''
  listOk.value = ''
  const failures = []
  try {
    for (const row of targets) {
      const results = await Promise.all([
        doRefresh(row, 'price', api.refreshDailyWatchPrice),
        doRefresh(row, 'options', api.refreshDailyWatchOptions),
        doRefresh(row, 'regression', api.refreshDailyWatchRegression),
      ])
      const actions = ['price', 'options', 'regression']
      results.forEach((r, i) => {
        if (!r.ok && !r.skipped) failures.push(`${row.symbol}(${ACTION_LABELS[actions[i]]})：${r.error}`)
      })
      bulkProgress.done += 1
    }
  } finally {
    bulkUpdating.value = false
  }
  const okCount = targets.length - new Set(failures.map((f) => f.split('(')[0])).size
  if (failures.length) {
    const preview = failures.slice(0, 4).join('；')
    const rest = failures.length > 4 ? ` 等共 ${failures.length} 处失败` : ''
    listError.value = `一键更新完成，${okCount}/${targets.length} 支全部成功；${preview}${rest}`
  } else {
    listOk.value = `一键更新完成：${targets.length} 支股票已全部更新`
  }
}

onMounted(load)
</script>

<template>
  <template v-if="screen === 'edit'">
    <div class="range-row">
      <button class="btn" type="button" @click="backToList">返回列表</button>
    </div>
    <p class="goal-lead">股票代码填好、点查询，昨收/昨开/昨低/昨高/现价自动查（不用手动抄）；隐含区间、回归线、走向、Max Pain 可以点列表里的按钮自动更新，也能自己改；事件得自己写。</p>
    <section class="panel">
      <h2 style="margin: 0 0 10px">{{ editingId ? '改股票记录' : '新增股票' }}</h2>
      <div class="form-grid">
        <div class="field">
          <label>股票代码</label>
          <div class="range-row" style="gap: 8px; margin: 0">
            <input v-model="form.symbol" placeholder="MU / TSLA" :disabled="!!editingId" @keydown.enter.prevent="runLookup" style="flex: 1" />
            <button class="btn" type="button" :disabled="lookupLoading" @click="runLookup">
              {{ lookupLoading ? '查询中…' : '查询' }}
            </button>
          </div>
        </div>
        <div class="field">
          <label>市场走向</label>
          <select v-model="form.market_direction">
            <option v-for="d in DIRECTIONS" :key="d" :value="d">{{ d }}</option>
          </select>
        </div>
        <div class="field">
          <label>板块</label>
          <select v-model="form.sector">
            <option v-for="s in SECTORS" :key="s" :value="s">{{ s }}</option>
          </select>
        </div>
        <div class="field">
          <label>昨日收盘（自动查）</label>
          <input :value="form.prev_close" disabled placeholder="点查询自动填" />
        </div>
        <div class="field">
          <label>昨日开盘（自动查）</label>
          <input :value="form.prev_open" disabled placeholder="点查询自动填" />
        </div>
        <div class="field">
          <label>昨日最低（自动查）</label>
          <input :value="form.prev_low" disabled placeholder="点查询自动填" />
        </div>
        <div class="field">
          <label>昨日最高（自动查）</label>
          <input :value="form.prev_high" disabled placeholder="点查询自动填" />
        </div>
        <div class="field">
          <label>当前价格（自动查）</label>
          <input :value="form.current_price" disabled placeholder="点查询自动填" />
        </div>
        <div class="field">
          <label>回归线</label>
          <input v-model="form.regression_line" type="number" step="0.01" />
        </div>
        <div class="field">
          <label>Max Pain</label>
          <input v-model="form.max_pain" type="number" step="0.01" />
        </div>
        <div class="field">
          <label>隐含区间 · 下方</label>
          <input v-model="form.gamma_low" type="number" step="0.01" />
        </div>
        <div class="field">
          <label>隐含区间 · 上方</label>
          <input v-model="form.gamma_high" type="number" step="0.01" />
        </div>
        <div class="field">
          <label>空买入价</label>
          <input v-model="form.short_entry_price" type="number" step="0.01" placeholder="打算做空挂单的价格" />
        </div>
        <div class="field">
          <label>多买入价</label>
          <input v-model="form.long_entry_price" type="number" step="0.01" placeholder="打算做多挂单的价格" />
        </div>
        <div class="field">
          <label>潜力股票</label>
          <label class="check"><input v-model="form.is_potential" type="checkbox" /> 是（列表里这一行会飘绿）</label>
        </div>
        <div class="field full">
          <label>重大事件</label>
          <textarea v-model="form.events" rows="4" placeholder="财报日期 / 突发新闻 / 监管消息等" />
        </div>
      </div>
      <div class="error" v-if="error">{{ error }}</div>
      <div class="modal-actions" style="margin-top: 12px; justify-content: flex-start">
        <button class="btn btn-primary" type="button" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : editingId ? '更新' : '保存' }}
        </button>
      </div>
    </section>
  </template>

  <template v-else>
    <p class="goal-lead">一只股票一行，全部手动记录，每天看盘后自己更新，方便挂单前快速参考。</p>
    <div class="range-row">
      <div class="watch-edit" style="flex: 1; margin: 0">
        <input v-model="search" placeholder="搜索代码" />
      </div>
      <select v-model="sectorFilter">
        <option value="">全部板块</option>
        <option v-for="s in SECTORS" :key="s" :value="s">{{ s }}</option>
      </select>
      <button class="btn" type="button" :disabled="bulkUpdating || !filtered.length" title="逐支更新价格/期权/回归线，跟点每一行的按钮效果一样，删除和改不受影响" @click="refreshAll">
        {{ bulkUpdating ? `更新中 ${bulkProgress.done}/${bulkProgress.total}…` : '一键更新' }}
      </button>
      <button class="btn btn-primary" type="button" @click="openNew">添加</button>
      <button class="btn" type="button" @click="emit('open-analysis')">分析</button>
    </div>
    <div class="error" v-if="listError">{{ listError }}</div>
    <p class="ok-msg" v-else-if="listOk">{{ listOk }}</p>
    <p class="sub" v-else-if="loading">加载中…</p>

    <section class="panel" style="margin-top: 12px">
      <div class="table-wrap" v-if="filtered.length">
        <table>
          <thead>
            <tr>
              <th>代码</th>
              <th>板块</th>
              <th>昨收</th>
              <th>昨开</th>
              <th>昨低</th>
              <th>昨高</th>
              <th>现价</th>
              <th>隐含区间</th>
              <th>回归线</th>
              <th>Max Pain</th>
              <th>空买入价</th>
              <th>多买入价</th>
              <th>期权走向</th>
              <th>潜力</th>
              <th>重大事件</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in filtered" :key="row.id" :class="rowClass(row)" class="stock-detail-row" @click="emit('open-detail', row)">
              <td><button class="icon-btn" :aria-label="`${row.symbol} 详情`" @click.stop="emit('open-detail', row)"><b>{{ row.symbol }}</b></button></td>
              <td>{{ row.sector || '其他' }}</td>
              <td class="mono">{{ num(row.prev_close) }}</td>
              <td class="mono">{{ num(row.prev_open) }}</td>
              <td class="mono">{{ num(row.prev_low) }}</td>
              <td class="mono">{{ num(row.prev_high) }}</td>
              <td class="mono">{{ num(row.current_price) }}</td>
              <td class="mono">
                <template v-if="row.gamma_low != null || row.gamma_high != null">
                  {{ num(row.gamma_low) }} ~ {{ num(row.gamma_high) }}
                </template>
                <span v-else class="sub">—</span>
              </td>
              <td class="mono">{{ num(row.regression_line) }}</td>
              <td class="mono">{{ num(row.max_pain) }}</td>
              <td class="mono down">{{ num(row.short_entry_price) }}</td>
              <td class="mono up">{{ num(row.long_entry_price) }}</td>
              <td :class="directionTone(row.market_direction)">
                {{ row.market_direction }}
              </td>
              <td>{{ row.is_potential ? '★' : '—' }}</td>
              <td style="max-width: 260px; white-space: pre-wrap">{{ row.events || '—' }}</td>
              <td class="pos-actions" @click.stop>
                <button class="icon-btn icon-only" type="button" :title="`${row.symbol} 详情`" :aria-label="`${row.symbol} 详情`" @click="emit('open-detail', row)">
                  <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H5v18h14V8zM14 3v5h5M8 12h8M8 16h5" /></svg>
                </button>
                <button class="icon-btn icon-only" type="button" title="技术指标 / 恐惧贪婪" @click="openIndicators(row)">
                  <svg viewBox="1 2 18 14" aria-hidden="true">
                    <path
                      d="M2 14h16M4 14V8M8 14V4M12 14v-5M16 14V6"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="1.8"
                      stroke-linecap="round"
                    />
                    <circle cx="4" cy="8" r="1.5" fill="currentColor" />
                    <circle cx="8" cy="4" r="1.5" fill="currentColor" />
                    <circle cx="12" cy="9" r="1.5" fill="currentColor" />
                    <circle cx="16" cy="6" r="1.5" fill="currentColor" />
                  </svg>
                </button>
                <button
                  class="icon-btn icon-only"
                  type="button"
                  :disabled="refreshingMap[refreshKey(row.id, 'price')]"
                  title="更新股票：昨日OHLC和现价"
                  @click="refreshPrice(row)"
                >
                  <i class="iconfont icon-shuaxin" :class="{ spin: refreshingMap[refreshKey(row.id, 'price')] }"></i>
                </button>
                <button
                  class="icon-btn icon-only"
                  type="button"
                  :disabled="refreshingMap[refreshKey(row.id, 'options')]"
                  title="更新期权：按期权分析最近一期到期日更新走向/Max Pain/隐含区间"
                  @click="refreshOptions(row)"
                >
                  <i class="iconfont icon-qiquanjiagou" :class="{ spin: refreshingMap[refreshKey(row.id, 'options')] }"></i>
                </button>
                <button
                  class="icon-btn icon-only"
                  type="button"
                  :disabled="refreshingMap[refreshKey(row.id, 'regression')]"
                  title="更新回归线：按回归线页面的稳健回归算法更新趋势线预测价"
                  @click="refreshRegression(row)"
                >
                  <i class="iconfont icon-xianxinghuigui" :class="{ spin: refreshingMap[refreshKey(row.id, 'regression')] }"></i>
                </button>
                <button class="icon-btn icon-only" type="button" title="改" @click="editRow(row)">
                  <i class="iconfont icon-bianji"></i>
                </button>
                <button class="icon-btn icon-only" type="button" title="删除" @click="remove(row)">
                  <i class="iconfont icon-shanchu"></i>
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="empty">
        {{ search.trim() ? '没有这个代码。' : '还没有股票。点右上角添加。' }}
      </div>
    </section>
  </template>

  <el-dialog
    v-model="indDialog"
    :title="indSymbol ? `${indSymbol} · 技术指标` : '技术指标'"
    width="860px"
    destroy-on-close
  >
    <div class="range-row" style="margin-bottom: 10px">
      <div class="pills">
        <button
          v-for="tf in IND_TIMEFRAMES"
          :key="tf.key"
          class="pill"
          :class="{ active: indTimeframe === tf.key }"
          type="button"
          @click="pickIndTf(tf.key)"
        >
          {{ tf.label }}
        </button>
      </div>
      <el-checkbox v-model="indOnlyHit">只看命中</el-checkbox>
      <el-button link type="primary" :loading="indLoading || fngLoading" @click="() => Promise.all([runIndicators(), loadFearGreed()])">
        重新计算
      </el-button>
    </div>

    <div class="metric-grid" style="margin-bottom: 12px">
      <div>
        <span class="sub">现价</span>
        <div class="mono">{{ pxInd(indResult?.price ?? indPrices.last) }}</div>
      </div>
      <div>
        <span class="sub">RSI</span>
        <div class="mono">{{ indPrices.rsi ?? '—' }}</div>
      </div>
      <div>
        <span class="sub">MACD</span>
        <div class="mono">{{ pxInd(indPrices.macd) }}</div>
      </div>
      <div>
        <span class="sub">恐惧贪婪</span>
        <div class="mono" :class="fngToneClass(fngData?.tone || fngData?.value)">
          <template v-if="fngLoading">…</template>
          <template v-else-if="fngData">
            {{ fngData.value }}
            <span class="sub" style="margin-left: 4px">{{ fngData.label }}</span>
          </template>
          <template v-else>—</template>
        </div>
      </div>
    </div>
    <p class="sub" style="margin: 0 0 8px">
      <template v-if="fngData">
        CNN 美股全市场 · {{ fngData.date || '—' }}
        · 昨收 {{ fngData.previous_close ?? '—' }}
        · 一周前 {{ fngData.previous_1_week ?? '—' }}
        · 一月前 {{ fngData.previous_1_month ?? '—' }}
      </template>
      <template v-else-if="fngError">{{ fngError }}</template>
      <template v-else>打开弹窗时拉取恐惧贪婪指数</template>
    </p>

    <div class="error" v-if="indError" style="margin-bottom: 8px">{{ indError }}</div>
    <div v-loading="indLoading">
      <p class="sub" v-if="indResult" style="margin: 0 0 10px">
        命中 {{ indHitCount }} / {{ indItems.length }}
        <template v-if="indResult.asof"> · {{ indResult.asof }}</template>
      </p>
      <div class="pills" style="margin-bottom: 10px; flex-wrap: wrap">
        <button
          v-for="g in indGroups"
          :key="g"
          class="pill"
          :class="{ active: indGroup === g }"
          type="button"
          @click="indGroup = g"
        >
          {{ g }}
        </button>
      </div>
      <el-table :data="indShown" stripe height="360" style="width: 100%">
        <el-table-column prop="group" label="分组" width="88" />
        <el-table-column prop="name" label="指标" min-width="140" />
        <el-table-column prop="hint" label="说明" min-width="160" />
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <span :class="row.hit ? 'up' : 'sub'">{{ row.hit ? '命中' : '未中' }}</span>
          </template>
        </el-table-column>
      </el-table>
      <p class="sub" v-if="!indLoading && indResult && !indShown.length" style="margin-top: 12px">
        {{ indOnlyHit ? '当前没有命中的指标，可取消「只看命中」查看全部。' : '暂无指标结果。' }}
      </p>
    </div>
  </el-dialog>
</template>

<style scoped>
.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px 14px;
}
@media (max-width: 720px) {
  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>

<style scoped>
.stock-detail-row { cursor: pointer; }
.stock-detail-row:hover td { background-color: rgba(130,165,255,.06); }
</style>
