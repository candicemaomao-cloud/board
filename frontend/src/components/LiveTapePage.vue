<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { money, pct, tone } from '../format'
import { armQuoteTimer, clearQuoteTimer } from '../marketHours'
import MacroChart from './MacroChart.vue'

const SECTOR_SNAPSHOT_KEY = 'live-sector-snapshot-v2'
const SECTOR_ETFS = [
  ['SOXX', '半导体'],
  ['XLK', '科技'],
  ['XLC', '通信'],
  ['XLY', '可选消费'],
  ['XLP', '必需消费'],
  ['XLF', '金融'],
  ['XLV', '医疗'],
  ['XLI', '工业'],
  ['XLE', '能源'],
  ['XLB', '材料'],
  ['XLRE', '地产'],
  ['XLU', '公用事业'],
]
const CALC_SESSIONS = new Set(['pre', 'regular'])

const stocks = ref([])
const sectorSnapshot = ref(readSnapshot())
const quotes = ref([])
const market = ref(null)
const loading = ref(false)
const saving = ref(false)
const syncing = ref(false)
const error = ref('')
const updatedAt = ref('')
const form = reactive({
  symbol: '',
  name: '',
  source: 'tradingview',
})
let timer

function vsEma(row, ema) {
  if (row.price == null || ema == null) return ''
  return tone(row.price - ema)
}

function sourceLabel(source) {
  if (source === 'binance') return '币安'
  if (source === 'yahoo') return 'Yahoo'
  return 'TradingView'
}

function readSnapshot() {
  try {
    return JSON.parse(localStorage.getItem(SECTOR_SNAPSHOT_KEY) || 'null')
  } catch {
    return null
  }
}

function computeSectors(rows) {
  const bySym = new Map(rows.map((r) => [String(r.symbol || '').toUpperCase(), r]))
  return SECTOR_ETFS.map(([symbol, sector]) => {
    const row = bySym.get(symbol)
    const v = Number(row?.day_pct)
    if (row?.day_pct == null || !Number.isFinite(v)) return null
    return { symbol, sector, pct: Math.round(v * 100) / 100 }
  })
    .filter(Boolean)
    .sort((a, b) => b.pct - a.pct)
}

function refreshSectorSnapshot() {
  const key = market.value?.key
  if (!CALC_SESSIONS.has(key)) {
    if (!sectorSnapshot.value && quotes.value.length) {
      sectorSnapshot.value = {
        session: key || 'closed',
        label: '无盘中快照，按当前报价',
        at: new Date().toISOString(),
        rows: computeSectors(quotes.value),
        fallback: true,
      }
    }
    return
  }
  const rows = computeSectors(quotes.value.filter((r) => CALC_SESSIONS.has(r.session)))
  if (!rows.length) return
  const snap = {
    session: key,
    label: market.value?.label || (key === 'pre' ? '盘前' : '盘中'),
    at: new Date().toISOString(),
    rows,
  }
  sectorSnapshot.value = snap
  localStorage.setItem(SECTOR_SNAPSHOT_KEY, JSON.stringify(snap))
}

const sectorLive = computed(() => CALC_SESSIONS.has(market.value?.key))
const sectorRows = computed(() => sectorSnapshot.value?.rows || [])
const sectorStatus = computed(() => {
  const snap = sectorSnapshot.value
  if (!snap) return ''
  const at = new Date(snap.at)
  const stamp = at.toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
  if (sectorLive.value) return `${snap.label}实时计算 · ${stamp}`
  if (snap.fallback) return `${market.value?.label || '休市'} · ${snap.label}`
  return `${market.value?.label || '休市'}，不再计算 · 展示 ${stamp} ${snap.label}快照`
})

const sectorOption = computed(() => {
  const rows = sectorRows.value
  if (!rows.length) return null
  const up = '#5fd4ae'
  const down = '#ff6b7a'
  return {
    grid: { left: 48, right: 16, top: 24, bottom: 44 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        const row = rows[params[0].dataIndex]
        return `<b>${row.sector}</b> ${row.symbol}<br/>${row.pct > 0 ? '+' : ''}${row.pct.toFixed(2)}%`
      },
    },
    xAxis: {
      type: 'category',
      data: rows.map((r) => `${r.sector}\n${r.symbol}`),
      axisLabel: { color: '#9aa6b2', interval: 0, rotate: 0, fontSize: 11, lineHeight: 14 },
      axisLine: { lineStyle: { color: '#3a4450' } },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#9aa6b2', formatter: '{value}%' },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
    },
    series: [
      {
        type: 'bar',
        barMaxWidth: 36,
        data: rows.map((r) => ({ value: r.pct, itemStyle: { color: r.pct >= 0 ? up : down } })),
        label: {
          show: true,
          position: 'top',
          color: '#cfd8e3',
          fontSize: 11,
          formatter: (p) => `${p.value > 0 ? '+' : ''}${Number(p.value).toFixed(2)}%`,
        },
      },
    ],
  }
})

function armTimer() {
  clearQuoteTimer(timer)
  timer = armQuoteTimer(loadQuotes, market.value)
}

async function loadSymbols() {
  error.value = ''
  try {
    const data = await api.liveSymbols()
    stocks.value = data.items || []
  } catch (e) {
    error.value = e.message || '加载标的失败'
    stocks.value = []
  }
}

async function loadQuotes() {
  if (!stocks.value.length) {
    quotes.value = []
    return
  }
  loading.value = !quotes.value.length
  error.value = ''
  try {
    const data = await api.quotes(stocks.value)
    quotes.value = data?.quotes || []
    market.value = data?.market || null
    updatedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    refreshSectorSnapshot()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
    armTimer()
  }
}

async function syncFromStockList() {
  syncing.value = true
  error.value = ''
  try {
    const watches = await api.dailyWatches()
    const list = Array.isArray(watches) ? watches : watches?.items || []
    if (!list.length) {
      ElMessage.warning('股票列表是空的，先去「股票列表」加几只')
      return
    }
    const prev = new Map((stocks.value || []).map((x) => [String(x.symbol || '').toUpperCase(), x]))
    const items = []
    const seen = new Set()
    for (const row of list) {
      const symbol = String(row.symbol || '').trim().toUpperCase()
      if (!symbol || seen.has(symbol)) continue
      seen.add(symbol)
      const old = prev.get(symbol)
      items.push({
        symbol,
        name: old?.name || symbol,
        source: old?.source || 'tradingview',
      })
    }
    const data = await api.replaceLiveSymbols(items)
    stocks.value = data.items || items
    ElMessage.success(`已同步股票列表：${stocks.value.length} 只`)
    await loadQuotes()
  } catch (e) {
    ElMessage.error(e.message || '同步失败')
  } finally {
    syncing.value = false
  }
}

async function addSymbol() {
  const symbol = form.symbol.trim().toUpperCase()
  if (!symbol) {
    ElMessage.warning('请填写股票代码')
    return
  }
  saving.value = true
  try {
    const data = await api.addLiveSymbol({
      symbol,
      name: form.name.trim() || symbol,
      source: form.source,
    })
    stocks.value = data.items || []
    form.symbol = ''
    form.name = ''
    form.source = 'tradingview'
    ElMessage.success(`已添加 ${symbol}`)
    await loadQuotes()
  } catch (e) {
    ElMessage.error(e.message || '添加失败')
  } finally {
    saving.value = false
  }
}

async function removeSymbol(symbol) {
  saving.value = true
  try {
    const data = await api.removeLiveSymbol(symbol)
    stocks.value = data.items || []
    ElMessage.success(`已移除 ${symbol}`)
    await loadQuotes()
  } catch (e) {
    ElMessage.error(e.message || '删除失败')
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  await loadSymbols()
  await loadQuotes()
})

onUnmounted(() => {
  clearQuoteTimer(timer)
})
</script>

<template>
  <p class="goal-lead">
    在下面添加股票代码，或点「同步股票列表」一键导入「股票分析 · 股票列表」里的标的。行情来自 TradingView / 币安。
    <template v-if="market">
      当前美股 {{ market.label }}<template v-if="market.live">，约 30 秒刷新</template><template v-else>，不再请求</template>。
    </template>
    <span v-if="updatedAt">上次 {{ updatedAt }}</span>
  </p>

  <section class="panel" style="margin-bottom: 12px">
    <h2 style="margin: 0 0 10px">添加股票</h2>
    <div class="range-row" style="flex-wrap: wrap; gap: 8px">
      <el-input
        v-model="form.symbol"
        placeholder="代码，如 NVDA / SKHYUSDT"
        style="width: 180px"
        @keydown.enter.prevent="addSymbol"
      />
      <el-input v-model="form.name" placeholder="名称（可选）" style="width: 140px" @keydown.enter.prevent="addSymbol" />
      <el-select v-model="form.source" style="width: 140px">
        <el-option label="TradingView" value="tradingview" />
        <el-option label="币安" value="binance" />
        <el-option label="Yahoo" value="yahoo" />
      </el-select>
      <el-button type="primary" :loading="saving" @click="addSymbol">添加</el-button>
      <el-button type="success" plain :loading="syncing" @click="syncFromStockList">同步股票列表</el-button>
      <el-button :disabled="loading" @click="loadQuotes">刷新行情</el-button>
    </div>
    <div class="pills" style="margin-top: 10px" v-if="stocks.length">
      <span
        v-for="item in stocks"
        :key="item.symbol"
        class="pill"
        style="display: inline-flex; align-items: center; gap: 6px"
      >
        {{ item.symbol }}
        <button class="icon-btn" type="button" title="移除" :disabled="saving" @click="removeSymbol(item.symbol)">
          ×
        </button>
      </span>
    </div>
  </section>

  <div class="error" v-if="error">{{ error }}</div>
  <div class="sub" v-else-if="loading">拉取日线均线…</div>

  <div v-if="!stocks.length" class="panel">
    <h2>还没有播报标的</h2>
    <p class="empty">在上方输入代码后点「添加」，例如 NVDA、AAPL、SKHYUSDT。</p>
  </div>

  <section class="panel sector-panel" v-if="stocks.length">
    <div class="sector-head">
      <h2 style="margin: 0">行业涨跌幅</h2>
      <span class="sub">
        <span v-if="sectorLive" class="sector-live-dot"></span>
        {{ sectorStatus || '等待行情…' }}
      </span>
    </div>
    <p class="sub" style="margin: 4px 0 8px">
      取 SOXX 与 11 只 SPDR 行业 ETF（XLB–XLY）的今日涨跌幅；需在播报标的里。只在盘前 / 盘中随行情刷新重算；盘后与休市保留最后一次快照不再计算。
    </p>
    <MacroChart v-if="sectorOption" :option="sectorOption" height="300px" />
    <div v-else class="empty">{{ loading ? '计算中…' : '暂无可计算的行业数据。' }}</div>
    <div class="sector-chips" v-if="sectorRows.length">
      <span v-for="row in sectorRows" :key="row.symbol" class="sector-chip">
        <b>{{ row.sector }}</b>
        <span class="sub">{{ row.symbol }}</span>
        <span :class="tone(row.pct)">{{ pct(row.pct) }}</span>
      </span>
    </div>
  </section>

  <section class="panel" v-if="stocks.length">
    <div class="table-wrap">
      <table v-if="quotes.length">
        <thead>
          <tr>
            <th>名称</th>
            <th>代码</th>
            <th>来源</th>
            <th>现价</th>
            <th>今日</th>
            <th>本周</th>
            <th>EMA5</th>
            <th>EMA10</th>
            <th>EMA20</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in quotes" :key="row.symbol">
            <td>{{ row.name }}</td>
            <td>{{ row.symbol }}</td>
            <td>{{ sourceLabel(row.source) }}</td>
            <td>
              <template v-if="row.price != null">
                {{ money(row.price, { signed: false }) }}
                <span v-if="row.session_label" class="session-tag" :class="row.session">{{ row.session_label }}</span>
              </template>
              <span v-else class="sub">{{ row.error || '—' }}</span>
            </td>
            <td :class="tone(row.day_pct)">{{ pct(row.day_pct) }}</td>
            <td :class="tone(row.week_pct)">{{ pct(row.week_pct) }}</td>
            <td :class="vsEma(row, row.ema5)">{{ money(row.ema5, { signed: false }) }}</td>
            <td :class="vsEma(row, row.ema10)">{{ money(row.ema10, { signed: false }) }}</td>
            <td :class="vsEma(row, row.ema20)">{{ money(row.ema20, { signed: false }) }}</td>
            <td>
              <button class="icon-btn" type="button" :disabled="saving" @click="removeSymbol(row.symbol)">删</button>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-else-if="!loading" class="empty">还没有行情。</div>
    </div>
  </section>
</template>

<style scoped>
.sector-panel {
  margin-bottom: 12px;
}
.sector-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.sector-live-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #5fd4ae;
  margin-right: 4px;
  animation: sector-pulse 1.6s ease-in-out infinite;
}
@keyframes sector-pulse {
  50% {
    opacity: 0.3;
  }
}
.sector-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.sector-chip {
  display: inline-flex;
  gap: 6px;
  align-items: baseline;
  padding: 3px 8px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.04);
  font-size: 12px;
}
</style>
