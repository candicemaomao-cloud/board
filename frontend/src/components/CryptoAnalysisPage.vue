<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as echarts from 'echarts'
import { api } from '../api'
import { can } from '../auth'

const canWrite = computed(() => can('btn.crypto.write') || can('menu.cryptoAnalysis'))

const coins = ref([])
const capRank = ref([])
const volRank = ref([])
const loading = ref(false)
const rankLoading = ref(false)
const saving = ref(false)
const dialog = ref(false)
const editingId = ref(null)
const selectedId = ref(null)
const klineInterval = ref('1d')
const klineBars = ref([])
const klineLoading = ref(false)
const klineDialog = ref(false)
const klineEl = ref(null)
const techDialog = ref(false)
const techLoading = ref(false)
const techInterval = ref('1d')
const techData = ref(null)
const techGroup = ref('全部')
const techOnlyHit = ref(false)
const onchainDialog = ref(false)
const onchainLoading = ref(false)
const onchainData = ref(null)
const mainTab = ref('mine')
const searchQ = ref('')
const searchHits = ref([])
const searching = ref(false)

let klineChart = null

const form = reactive({
  symbol: '',
  name: '',
  coingecko_id: '',
  binance_symbol: '',
  notes: '',
})

const selected = computed(() => coins.value.find((c) => c.id === selectedId.value) || null)
const rankRows = computed(() => (mainTab.value === 'volume' ? volRank.value : capRank.value))
const isMine = computed(() => mainTab.value === 'mine')
const isRank = computed(() => mainTab.value === 'market_cap' || mainTab.value === 'volume')
const techPrices = computed(() => techData.value?.prices || {})
const techGroups = computed(() => {
  const list = techData.value?.groups || []
  return ['全部', ...list.map((g) => g.name)]
})
const techShown = computed(() => {
  let items = techData.value?.items || []
  if (techGroup.value !== '全部') items = items.filter((x) => x.group === techGroup.value)
  if (techOnlyHit.value) items = items.filter((x) => x.hit)
  return items
})
const oiBlock = computed(() => onchainData.value?.open_interest || null)
const fundBlock = computed(() => onchainData.value?.funding || null)
const flowItems = computed(() => onchainData.value?.flows?.items || [])
const whaleBlock = computed(() => onchainData.value?.whales || null)
const mempoolBlock = computed(() => onchainData.value?.mempool || null)
const sentimentBlock = computed(() => onchainData.value?.sentiment || null)
const sentimentCoin = computed(() => sentimentBlock.value?.coin || null)
const sentimentMarket = computed(() => sentimentBlock.value?.market || null)
const sentimentExchanges = computed(() => sentimentCoin.value?.exchanges || [])

function money(n) {
  if (n == null || n === '') return '—'
  const v = Number(n)
  if (!Number.isFinite(v)) return '—'
  if (Math.abs(v) >= 1e9) return `$${(v / 1e9).toFixed(2)}B`
  if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(2)}M`
  if (Math.abs(v) >= 1e3) return `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
  if (Math.abs(v) >= 1) return `$${v.toFixed(2)}`
  return `$${v.toPrecision(4)}`
}

function num(n) {
  if (n == null || n === '') return '—'
  const v = Number(n)
  if (!Number.isFinite(v)) return '—'
  if (Math.abs(v) >= 1e9) return `${(v / 1e9).toFixed(2)}B`
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(2)}M`
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 })
}

function pct(n) {
  if (n == null || n === '') return '—'
  const v = Number(n)
  if (!Number.isFinite(v)) return '—'
  const sign = v > 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

function tone(n) {
  const v = Number(n)
  if (!Number.isFinite(v) || v === 0) return ''
  return v > 0 ? 'up' : 'down'
}

function biasClass(bias) {
  if (bias === '多头流入') return 'up'
  if (bias === '空头流入' || bias === '仓位撤离') return 'down'
  return 'muted'
}

function moodClass(toneOrValue) {
  if (toneOrValue === 'bullish' || toneOrValue === '偏多') return 'up'
  if (toneOrValue === 'bearish' || toneOrValue === '偏空') return 'down'
  const v = Number(toneOrValue)
  if (Number.isFinite(v)) {
    if (v >= 56) return 'up'
    if (v <= 44) return 'down'
  }
  return 'muted'
}

function pctPlain(n, digits = 2) {
  if (n == null || n === '') return '—'
  const v = Number(n)
  if (!Number.isFinite(v)) return '—'
  return `${v.toFixed(digits)}%`
}

function sparkPoints(arr) {
  const vals = (arr || []).map(Number).filter((x) => Number.isFinite(x))
  if (vals.length < 2) return ''
  const min = Math.min(...vals)
  const max = Math.max(...vals)
  const span = max - min || 1
  const w = 72
  const h = 28
  return vals
    .map((v, i) => {
      const x = (i / (vals.length - 1)) * w
      const y = h - ((v - min) / span) * h
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}

function resetForm() {
  editingId.value = null
  form.symbol = ''
  form.name = ''
  form.coingecko_id = ''
  form.binance_symbol = ''
  form.notes = ''
  searchQ.value = ''
  searchHits.value = []
}

async function loadCoins() {
  loading.value = true
  try {
    const data = await api.cryptoCoins({ with_quote: true })
    coins.value = data.items || []
    if (selectedId.value && !coins.value.some((c) => c.id === selectedId.value)) {
      selectedId.value = null
    }
  } catch (e) {
    ElMessage.error(e.message || '加载币列表失败')
  } finally {
    loading.value = false
  }
}

async function loadRankings() {
  rankLoading.value = true
  try {
    const [cap, vol] = await Promise.all([
      api.cryptoRankings({ kind: 'market_cap', limit: 30 }),
      api.cryptoRankings({ kind: 'volume', limit: 30 }),
    ])
    capRank.value = cap.items || []
    volRank.value = vol.items || []
  } catch (e) {
    ElMessage.error(e.message || '加载榜单失败')
  } finally {
    rankLoading.value = false
  }
}

async function loadKlines() {
  if (!selectedId.value) {
    klineBars.value = []
    return
  }
  klineLoading.value = true
  try {
    const data = await api.cryptoKlines(selectedId.value, {
      interval: klineInterval.value,
      limit: 90,
    })
    klineBars.value = data.bars || []
  } catch (e) {
    klineBars.value = []
    ElMessage.error(e.message || 'K线加载失败')
  } finally {
    klineLoading.value = false
  }
}

function buildKlineOption() {
  const bars = klineBars.value || []
  const cats = bars.map((b) => {
    const d = new Date((b.ts || 0) * 1000)
    return `${d.getMonth() + 1}/${d.getDate()}`
  })
  const ohlc = bars.map((b) => [b.open, b.close, b.low, b.high])
  return {
    backgroundColor: 'transparent',
    animation: false,
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#101820',
      borderColor: 'rgba(148,176,210,0.2)',
      textStyle: { color: '#e9f0f7' },
    },
    grid: { left: 56, right: 18, top: 28, bottom: 36 },
    xAxis: {
      type: 'category',
      data: cats,
      axisLine: { lineStyle: { color: 'rgba(148,176,210,0.2)' } },
      axisLabel: { color: '#8b9bb0' },
    },
    yAxis: {
      type: 'value',
      scale: true,
      splitLine: { lineStyle: { color: 'rgba(148,176,210,0.08)' } },
      axisLabel: { color: '#8b9bb0' },
    },
    series: [
      {
        type: 'candlestick',
        name: selected.value?.symbol || 'K',
        data: ohlc,
        itemStyle: {
          color: '#3ee0a0',
          color0: '#ff6b7a',
          borderColor: '#3ee0a0',
          borderColor0: '#ff6b7a',
        },
      },
    ],
  }
}

function disposeKlineChart() {
  if (klineChart) {
    klineChart.dispose()
    klineChart = null
  }
}

async function paintKline() {
  await nextTick()
  if (!klineEl.value) return
  if (!klineChart) klineChart = echarts.init(klineEl.value, null, { renderer: 'canvas' })
  klineChart.setOption(buildKlineOption(), true)
  klineChart.resize()
}

async function openKline(row) {
  selectedId.value = row.id
  klineBars.value = []
  klineInterval.value = '1d'
  klineDialog.value = true
  await loadKlines()
  await paintKline()
  // 等弹窗动画结束再 resize，避免空白
  setTimeout(() => klineChart?.resize(), 80)
}

function closeKline() {
  klineDialog.value = false
  disposeKlineChart()
}

async function loadTech() {
  if (!selectedId.value) {
    techData.value = null
    return
  }
  techLoading.value = true
  try {
    techData.value = await api.cryptoTech(selectedId.value, { interval: techInterval.value })
  } catch (e) {
    techData.value = null
    ElMessage.error(e.message || '指标计算失败')
  } finally {
    techLoading.value = false
  }
}

async function openTech(row) {
  selectedId.value = row.id
  techData.value = null
  techInterval.value = '1d'
  techGroup.value = '全部'
  techOnlyHit.value = false
  techDialog.value = true
  await loadTech()
}

async function loadOnchain() {
  const row = selected.value
  const sym = row?.binance_symbol || (row?.symbol ? `${row.symbol}USDT` : '')
  if (!sym) {
    onchainData.value = null
    return
  }
  onchainLoading.value = true
  try {
    onchainData.value = await api.cryptoOnchain({ symbol: sym })
  } catch (e) {
    onchainData.value = null
    ElMessage.error(e.message || '链上数据加载失败')
  } finally {
    onchainLoading.value = false
  }
}

async function openOnchain(row) {
  selectedId.value = row.id
  onchainData.value = null
  onchainDialog.value = true
  await loadOnchain()
}

function openNew() {
  resetForm()
  dialog.value = true
}

function openEdit(row) {
  editingId.value = row.id
  form.symbol = row.symbol || ''
  form.name = row.name || ''
  form.coingecko_id = row.coingecko_id || ''
  form.binance_symbol = row.binance_symbol || ''
  form.notes = row.notes || ''
  searchQ.value = ''
  searchHits.value = []
  dialog.value = true
}

function pickSearch(hit) {
  form.symbol = hit.symbol || ''
  form.name = hit.name || ''
  form.coingecko_id = hit.coingecko_id || ''
  form.binance_symbol = hit.binance_symbol || ''
  searchHits.value = []
}

let searchTimer
watch(searchQ, (q) => {
  clearTimeout(searchTimer)
  if (!q || q.trim().length < 1) {
    searchHits.value = []
    return
  }
  searchTimer = setTimeout(async () => {
    searching.value = true
    try {
      const data = await api.cryptoSearch(q.trim())
      searchHits.value = data.items || []
    } catch {
      searchHits.value = []
    } finally {
      searching.value = false
    }
  }, 350)
})

async function save() {
  if (!form.symbol.trim()) {
    ElMessage.warning('请填写币种代码')
    return
  }
  saving.value = true
  try {
    const body = {
      symbol: form.symbol.trim(),
      name: form.name.trim() || null,
      coingecko_id: form.coingecko_id.trim() || null,
      binance_symbol: form.binance_symbol.trim() || null,
      notes: form.notes.trim() || null,
    }
    if (editingId.value) {
      await api.updateCryptoCoin(editingId.value, body)
      ElMessage.success('已更新')
    } else {
      await api.createCryptoCoin(body)
      ElMessage.success('已添加')
    }
    dialog.value = false
    mainTab.value = 'mine'
    await loadCoins()
  } catch (e) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`删除 ${row.symbol}？`, '确认', { type: 'warning' })
    await api.deleteCryptoCoin(row.id)
    ElMessage.success('已删除')
    if (selectedId.value === row.id) {
      selectedId.value = null
      if (klineDialog.value) closeKline()
      if (techDialog.value) techDialog.value = false
      if (onchainDialog.value) onchainDialog.value = false
    }
    await loadCoins()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e.message || '删除失败')
  }
}

function addFromRank(row) {
  resetForm()
  form.symbol = row.symbol || ''
  form.name = row.name || ''
  form.coingecko_id = row.coingecko_id || ''
  form.binance_symbol = (row.symbol || '') + 'USDT'
  dialog.value = true
}

watch(klineInterval, async () => {
  if (!klineDialog.value) return
  await loadKlines()
  await paintKline()
})

watch(klineDialog, (open) => {
  if (!open) disposeKlineChart()
})

watch(techInterval, async () => {
  if (!techDialog.value) return
  await loadTech()
})

watch(techDialog, (open) => {
  if (!open) techData.value = null
})

watch(onchainDialog, (open) => {
  if (!open) onchainData.value = null
})

let refreshTimer
onMounted(async () => {
  await Promise.all([loadCoins(), loadRankings()])
  refreshTimer = setInterval(() => {
    loadCoins()
    loadRankings()
  }, 60_000)
})
onUnmounted(() => {
  clearInterval(refreshTimer)
  clearTimeout(searchTimer)
  disposeKlineChart()
})
</script>

<template>
  <p class="goal-lead">
    用上方 Tab 切换「我的币列表 / 市值榜 / 成交量榜」。列表里可点 K 线、技术指标、链上/情绪图标打开弹窗。
  </p>

  <div class="range-row">
    <div class="pills">
      <button class="pill" :class="{ active: mainTab === 'mine' }" type="button" @click="mainTab = 'mine'">
        我的币列表
      </button>
      <button class="pill" :class="{ active: mainTab === 'market_cap' }" type="button" @click="mainTab = 'market_cap'">
        市值榜
      </button>
      <button class="pill" :class="{ active: mainTab === 'volume' }" type="button" @click="mainTab = 'volume'">
        24h 成交量榜
      </button>
    </div>
    <div style="display: flex; gap: 8px">
      <el-button :loading="loading || rankLoading" @click="loadCoins(); loadRankings()">刷新</el-button>
      <el-button v-if="canWrite && isMine" type="primary" @click="openNew">新增币种</el-button>
    </div>
  </div>

  <section v-if="isMine" class="panel" style="margin-top: 12px">
    <h2 style="margin-top: 0">我的币列表</h2>
    <el-table :data="coins" v-loading="loading" stripe style="width: 100%">
      <el-table-column prop="symbol" label="币种" width="100">
        <template #default="{ row }">
          <b>{{ row.symbol }}</b>
          <div class="sub">{{ row.name }}</div>
        </template>
      </el-table-column>
      <el-table-column label="价格" width="120">
        <template #default="{ row }">
          <span class="mono">{{ money(row.price) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="24h" width="96">
        <template #default="{ row }">
          <span :class="tone(row.change_24h)">{{ pct(row.change_24h) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="7日" min-width="90">
        <template #default="{ row }">
          <svg v-if="row.sparkline?.length" width="72" height="28" class="spark">
            <polyline
              fill="none"
              :stroke="Number(row.change_24h) >= 0 ? 'var(--up)' : 'var(--down)'"
              stroke-width="1.6"
              :points="sparkPoints(row.sparkline)"
            />
          </svg>
          <span v-else class="sub">—</span>
        </template>
      </el-table-column>
      <el-table-column label="24h量" width="110">
        <template #default="{ row }">{{ money(row.volume_24h) }}</template>
      </el-table-column>
      <el-table-column label="市值" width="110">
        <template #default="{ row }">{{ money(row.market_cap) }}</template>
      </el-table-column>
      <el-table-column label="MA5" width="100">
        <template #default="{ row }">
          <span class="mono" :class="tone(row.price != null && row.ma5 != null ? row.price - row.ma5 : 0)">
            {{ money(row.ma5) }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="MA10" width="100">
        <template #default="{ row }">
          <span class="mono" :class="tone(row.price != null && row.ma10 != null ? row.price - row.ma10 : 0)">
            {{ money(row.ma10) }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="MA20" width="100">
        <template #default="{ row }">
          <span class="mono" :class="tone(row.price != null && row.ma20 != null ? row.price - row.ma20 : 0)">
            {{ money(row.ma20) }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="K线" width="64" align="center">
        <template #default="{ row }">
          <button class="kline-icon-btn" type="button" title="查看 K 线" @click="openKline(row)">
            <svg width="22" height="18" viewBox="0 0 22 18" aria-hidden="true">
              <path
                d="M4 2v14M4 5h3v6H4M11 1v16M11 4h3v8h-3M18 3v12M18 6h3v5h-3"
                fill="none"
                stroke="currentColor"
                stroke-width="1.6"
                stroke-linecap="round"
              />
            </svg>
          </button>
        </template>
      </el-table-column>
      <el-table-column label="指标" width="64" align="center">
        <template #default="{ row }">
          <button class="kline-icon-btn" type="button" title="计算技术指标" @click="openTech(row)">
            <svg width="20" height="18" viewBox="0 0 20 18" aria-hidden="true">
              <path
                d="M2 14h16M4 14V8M8 14V4M12 14v-5M16 14V6"
                fill="none"
                stroke="currentColor"
                stroke-width="1.6"
                stroke-linecap="round"
              />
              <circle cx="4" cy="8" r="1.4" fill="currentColor" />
              <circle cx="8" cy="4" r="1.4" fill="currentColor" />
              <circle cx="12" cy="9" r="1.4" fill="currentColor" />
              <circle cx="16" cy="6" r="1.4" fill="currentColor" />
            </svg>
          </button>
        </template>
      </el-table-column>
      <el-table-column label="链上" width="64" align="center">
        <template #default="{ row }">
          <button class="kline-icon-btn" type="button" title="链上 / 情绪数据" @click="openOnchain(row)">
            <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
              <path
                d="M3 5.5h12M3 9h12M3 12.5h12M5.5 3.5v11M9 3.5v11M12.5 3.5v11"
                fill="none"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linecap="round"
              />
            </svg>
          </button>
        </template>
      </el-table-column>
      <el-table-column v-if="canWrite" label="操作" width="110" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openEdit(row)">改</el-button>
          <el-button link type="danger" @click="remove(row)">删</el-button>
        </template>
      </el-table-column>
    </el-table>
    <p class="sub" v-if="!loading && !coins.length" style="margin-top: 12px">还没有币，点上方新增。</p>
  </section>

  <section v-else-if="isRank" class="panel" style="margin-top: 12px">
    <h2 style="margin-top: 0">{{ mainTab === 'volume' ? '24h 成交量榜' : '市值排名' }}</h2>
    <el-table :data="rankRows" v-loading="rankLoading" stripe style="width: 100%">
      <el-table-column prop="rank" label="#" width="56" />
      <el-table-column label="币种" min-width="140">
        <template #default="{ row }">
          <div style="display: flex; align-items: center; gap: 8px">
            <img v-if="row.image" :src="row.image" alt="" width="18" height="18" style="border-radius: 50%" />
            <div>
              <b>{{ row.symbol }}</b>
              <div class="sub">{{ row.name }}</div>
            </div>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="价格" width="120">
        <template #default="{ row }">
          <span class="mono">{{ money(row.price) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="24h" width="100">
        <template #default="{ row }">
          <span :class="tone(row.change_24h)">{{ pct(row.change_24h) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="市值" width="120">
        <template #default="{ row }">{{ money(row.market_cap) }}</template>
      </el-table-column>
      <el-table-column label="24h量" width="120">
        <template #default="{ row }">{{ money(row.volume_24h) }}</template>
      </el-table-column>
      <el-table-column v-if="canWrite" label="" width="88">
        <template #default="{ row }">
          <el-button link type="primary" @click="addFromRank(row)">加入</el-button>
        </template>
      </el-table-column>
    </el-table>
  </section>

  <el-dialog
    v-model="klineDialog"
    :title="selected ? `${selected.symbol} · K线` : 'K线'"
    width="760px"
    destroy-on-close
    @closed="disposeKlineChart"
  >
    <div class="range-row" style="margin-bottom: 8px">
      <p class="sub" style="margin: 0" v-if="selected">
        {{ money(selected.price) }}
        <span :class="tone(selected.change_24h)">{{ pct(selected.change_24h) }}</span>
        · 市值 {{ money(selected.market_cap) }} · 量 {{ money(selected.volume_24h) }}
      </p>
      <div class="pills">
        <button
          v-for="tf in [
            { key: '1h', label: '1h' },
            { key: '4h', label: '4h' },
            { key: '1d', label: '日' },
            { key: '1w', label: '周' },
          ]"
          :key="tf.key"
          class="pill"
          :class="{ active: klineInterval === tf.key }"
          type="button"
          @click="klineInterval = tf.key"
        >
          {{ tf.label }}
        </button>
      </div>
    </div>
    <div v-loading="klineLoading" class="crypto-kline">
      <div ref="klineEl" class="chart" style="height: 360px; width: 100%"></div>
    </div>
  </el-dialog>

  <el-dialog
    v-model="techDialog"
    :title="selected ? `${selected.symbol} · 技术指标` : '技术指标'"
    width="860px"
    destroy-on-close
  >
    <div class="range-row" style="margin-bottom: 10px">
      <div class="pills">
        <button
          v-for="tf in [
            { key: '5m', label: '5m' },
            { key: '30m', label: '30m' },
            { key: '1h', label: '1h' },
            { key: '4h', label: '4h' },
            { key: '1d', label: '1d' },
          ]"
          :key="tf.key"
          class="pill"
          :class="{ active: techInterval === tf.key }"
          type="button"
          @click="techInterval = tf.key"
        >
          {{ tf.label }}
        </button>
      </div>
      <el-checkbox v-model="techOnlyHit">只看命中</el-checkbox>
    </div>

    <div v-loading="techLoading">
      <div class="metric-grid" v-if="techData">
        <div><span class="sub">现价</span><div class="mono">{{ money(techPrices.last) }}</div></div>
        <div><span class="sub">RSI</span><div class="mono">{{ techPrices.rsi ?? '—' }}</div></div>
        <div><span class="sub">MACD</span><div class="mono">{{ money(techPrices.macd) }}</div></div>
        <div><span class="sub">信号线</span><div class="mono">{{ money(techPrices.macd_signal) }}</div></div>
        <div><span class="sub">EMA20</span><div class="mono">{{ money(techPrices.ema20) }}</div></div>
        <div><span class="sub">SMA50</span><div class="mono">{{ money(techPrices.sma50) }}</div></div>
        <div><span class="sub">SMA200</span><div class="mono">{{ money(techPrices.sma200) }}</div></div>
        <div><span class="sub">ADX</span><div class="mono">{{ techPrices.adx ?? '—' }}</div></div>
      </div>
      <p class="sub" v-if="techData" style="margin: 10px 0">
        命中 {{ techData.hit_count }} / {{ techData.total }} · 周期 {{ techData.interval }}
      </p>
      <div class="pills" style="margin-bottom: 10px; flex-wrap: wrap">
        <button
          v-for="g in techGroups"
          :key="g"
          class="pill"
          :class="{ active: techGroup === g }"
          type="button"
          @click="techGroup = g"
        >
          {{ g }}
        </button>
      </div>
      <el-table :data="techShown" stripe height="360" style="width: 100%">
        <el-table-column prop="group" label="分组" width="88" />
        <el-table-column prop="name" label="指标" min-width="140" />
        <el-table-column prop="hint" label="说明" min-width="160" />
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <span :class="row.hit ? 'up' : 'sub'">{{ row.hit ? '命中' : '未中' }}</span>
          </template>
        </el-table-column>
      </el-table>
      <p class="sub" v-if="!techLoading && !techData" style="margin-top: 12px">暂无结果。</p>
    </div>
  </el-dialog>

  <el-dialog
    v-model="onchainDialog"
    :title="selected ? `${selected.symbol} · 链上 / 情绪` : '链上 / 情绪'"
    width="820px"
    destroy-on-close
  >
    <div v-loading="onchainLoading" class="onchain-stack">
      <section class="onchain-block">
        <h3 class="onchain-title">
          情绪指标
          <span class="mood-sym" v-if="sentimentCoin?.symbol">· {{ sentimentCoin.symbol }}</span>
        </h3>
        <p class="onchain-note">
          {{ sentimentBlock?.note || '各交易所多空比独立统计，币安 / 欧意常有差异' }}
        </p>
        <p class="onchain-error" v-if="sentimentBlock?.error">{{ sentimentBlock.error }}</p>
        <template v-else-if="sentimentCoin">
          <div class="mood-overall">
            <div>
              <div class="onchain-label">综合判断</div>
              <div class="mood-overall-val" :class="moodClass(sentimentCoin.tone || sentimentCoin.overall)">
                {{ sentimentCoin.overall || '—' }}
              </div>
            </div>
            <div v-if="sentimentMarket && !sentimentMarket.error">
              <div class="onchain-label">全市场恐惧贪婪</div>
              <div class="onchain-value mono" :class="moodClass(sentimentMarket.value)">
                {{ sentimentMarket.value ?? '—' }}
                <span class="mood-fng">{{ sentimentMarket.label }}</span>
              </div>
            </div>
            <div v-else>
              <div class="onchain-label">说明</div>
              <div class="mood-sub" style="margin: 0">交易所各自统计，方向可能分歧</div>
            </div>
          </div>
          <p class="mood-reasons" v-if="sentimentCoin.reasons?.length">
            {{ sentimentCoin.reasons.join(' · ') }}
          </p>

          <div
            class="ex-block"
            v-for="ex in sentimentExchanges"
            :key="ex.exchange || ex.name"
          >
            <div class="ex-head">
              <h4 class="ex-title">{{ ex.name || ex.exchange }}</h4>
              <template v-if="!ex.error">
                <span class="ls-bias" :class="moodClass(ex.tone || ex.overall)">{{ ex.overall || '—' }}</span>
                <span class="ex-fund muted">
                  资金费率
                  <span class="mono" :class="tone(ex.funding_pct)">
                    {{ ex.funding_pct != null ? `${Number(ex.funding_pct).toFixed(4)}%` : '—' }}
                  </span>
                </span>
              </template>
            </div>
            <p class="onchain-error" v-if="ex.error">{{ ex.error }}</p>
            <div class="ls-grid" v-else>
              <div class="ls-card" v-for="m in ex.metrics || []" :key="`${ex.exchange}-${m.key}`">
                <div class="ls-head">
                  <span>{{ m.title }}</span>
                  <span class="ls-bias" :class="moodClass(m.tone || m.label)">{{ m.label || '—' }}</span>
                </div>
                <p class="onchain-error" v-if="m.error">{{ m.error }}</p>
                <template v-else>
                  <div class="ls-bar" aria-hidden="true">
                    <div class="ls-long" :style="{ width: `${m.long_pct ?? 50}%` }" />
                    <div class="ls-short" :style="{ width: `${m.short_pct ?? 50}%` }" />
                  </div>
                  <div class="ls-row">
                    <span class="up">做多 {{ pctPlain(m.long_pct) }}</span>
                    <span class="down">做空 {{ pctPlain(m.short_pct) }}</span>
                  </div>
                  <div class="ls-meta">
                    多空比
                    <span class="mono accent">{{ m.long_short_ratio ?? '—' }}</span>
                    <template v-if="m.long_chg_pct != null">
                      · 多头变动
                      <span class="mono" :class="tone(m.long_chg_pct)">{{ pct(m.long_chg_pct) }}</span>
                    </template>
                  </div>
                </template>
              </div>
            </div>
          </div>
        </template>
      </section>

      <section class="onchain-block">
        <h3 class="onchain-title">持仓 / 资金费率</h3>
        <p class="onchain-note">{{ oiBlock?.note || '合约未平仓量（交易所侧代理大户/杠杆持仓）' }}</p>
        <div class="onchain-metrics" v-if="oiBlock && !oiBlock.error">
          <div>
            <div class="onchain-label">未平仓量</div>
            <div class="onchain-value mono accent">{{ num(oiBlock.open_interest) }}</div>
          </div>
          <div>
            <div class="onchain-label">24h 变动</div>
            <div class="onchain-value mono" :class="tone(oiBlock.change_24h_pct)">
              {{ pct(oiBlock.change_24h_pct) }}
            </div>
          </div>
          <div>
            <div class="onchain-label">资金费率</div>
            <div class="onchain-value mono" :class="tone(fundBlock?.latest_pct)">
              {{ fundBlock?.latest_pct != null ? `${fundBlock.latest_pct}%` : '—' }}
            </div>
          </div>
        </div>
        <p class="onchain-error" v-else-if="oiBlock?.error">{{ oiBlock.error }}</p>
        <p class="onchain-foot" v-if="mempoolBlock && !mempoolBlock.error">
          <span class="accent">BTC 高度 {{ mempoolBlock.height ?? '—' }}</span>
          · 推荐手续费
          <span class="mono">
            {{ mempoolBlock.fees?.fastestFee ?? '—' }}/{{ mempoolBlock.fees?.halfHourFee ?? '—' }}/{{
              mempoolBlock.fees?.hourFee ?? '—'
            }}
            sat/vB
          </span>
        </p>
      </section>

      <section class="onchain-block">
        <h3 class="onchain-title">交易所活跃度（近似净流入）</h3>
        <p class="onchain-note">{{ onchainData?.flows?.note || '用合约成交额与未平仓变动近似方向' }}</p>
        <el-table :data="flowItems" stripe size="small" style="width: 100%">
          <el-table-column prop="symbol" label="币" width="72" />
          <el-table-column label="24h" width="88">
            <template #default="{ row }">
              <span :class="tone(row.change_24h)">{{ pct(row.change_24h) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="成交额" width="110">
            <template #default="{ row }">
              <span class="mono">{{ num(row.volume_24h) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="OI 变动" width="100">
            <template #default="{ row }">
              <span :class="tone(row.oi_change_24h)">{{ pct(row.oi_change_24h) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="方向" min-width="100">
            <template #default="{ row }">
              <span :class="biasClass(row.bias)">{{ row.bias }}</span>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <section class="onchain-block">
        <h3 class="onchain-title">大户异动（大额成交）</h3>
        <p class="onchain-note" v-if="whaleBlock">
          {{ whaleBlock.note }}
          · 买入 <span class="up mono">{{ num(whaleBlock.buy_quote) }}</span>
          / 卖出 <span class="down mono">{{ num(whaleBlock.sell_quote) }}</span>
          / 净
          <span class="mono" :class="tone(whaleBlock.net_quote)">{{ num(whaleBlock.net_quote) }}</span>
        </p>
        <el-table :data="whaleBlock?.items || []" stripe size="small" height="280" style="width: 100%">
          <el-table-column label="时间" width="160">
            <template #default="{ row }">
              {{ row.ts ? new Date(row.ts * 1000).toLocaleString() : '—' }}
            </template>
          </el-table-column>
          <el-table-column prop="side" label="方向" width="72">
            <template #default="{ row }">
              <span :class="row.side === '买入' ? 'up' : 'down'">{{ row.side }}</span>
            </template>
          </el-table-column>
          <el-table-column label="价格" width="110">
            <template #default="{ row }">
              <span class="mono">{{ num(row.price) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="数量" width="110">
            <template #default="{ row }">
              <span class="mono">{{ num(row.qty) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="金额(U)" min-width="120">
            <template #default="{ row }">
              <span class="mono accent">{{ num(row.quote) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="source" label="市场" width="88" />
        </el-table>
      </section>
    </div>
  </el-dialog>

  <el-dialog v-model="dialog" :title="editingId ? '改币种' : '新增币种'" width="520px" destroy-on-close>
    <el-form label-width="96px">
      <el-form-item label="搜索">
        <el-input v-model="searchQ" placeholder="BTC / ethereum…" :loading="searching" />
        <div class="clause-list" style="margin-top: 8px" v-if="searchHits.length">
          <button
            v-for="hit in searchHits"
            :key="hit.coingecko_id"
            class="clause-chip btn-chip"
            type="button"
            @click="pickSearch(hit)"
          >
            {{ hit.symbol }} · {{ hit.name }}
          </button>
        </div>
      </el-form-item>
      <el-form-item label="代码">
        <el-input v-model="form.symbol" placeholder="BTC" />
      </el-form-item>
      <el-form-item label="名称">
        <el-input v-model="form.name" placeholder="Bitcoin" />
      </el-form-item>
      <el-form-item label="CoinGecko">
        <el-input v-model="form.coingecko_id" placeholder="bitcoin" />
      </el-form-item>
      <el-form-item label="币安交易对">
        <el-input v-model="form.binance_symbol" placeholder="BTCUSDT" />
      </el-form-item>
      <el-form-item label="备注">
        <el-input v-model="form.notes" type="textarea" :rows="2" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialog = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.spark {
  display: block;
}
.crypto-kline {
  min-height: 360px;
}
.kline-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 28px;
  padding: 0;
  border: 1px solid rgba(148, 176, 210, 0.25);
  border-radius: 6px;
  background: transparent;
  color: #7aa2ff;
  cursor: pointer;
}
.kline-icon-btn:hover {
  border-color: #7aa2ff;
  background: rgba(122, 162, 255, 0.12);
}
.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px 14px;
}
.onchain-stack {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 200px;
}
.onchain-block {
  padding: 14px 16px;
  border: 1px solid rgba(148, 176, 210, 0.16);
  border-radius: 10px;
  background: rgba(16, 24, 32, 0.45);
}
.onchain-title {
  margin: 0;
  font-size: 16px;
  font-weight: 650;
  color: #e9f0f7;
}
.onchain-note {
  margin: 6px 0 12px;
  font-size: 13px;
  color: #8b9bb0;
}
.onchain-foot {
  margin: 12px 0 0;
  font-size: 13px;
  color: #8b9bb0;
}
.onchain-error {
  margin: 0;
  color: var(--down, #ff6b7a);
}
.onchain-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px 16px;
}
.mood-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
.mood-card {
  padding: 12px 14px;
  border-radius: 8px;
  background: rgba(10, 16, 24, 0.55);
  border: 1px solid rgba(148, 176, 210, 0.12);
}
.mood-label {
  margin-top: 4px;
  font-size: 14px;
  font-weight: 600;
}
.mood-sub {
  margin-top: 6px;
  font-size: 12px;
  color: #8b9bb0;
  line-height: 1.35;
}
.mood-sym {
  font-weight: 500;
  color: #7aa2ff;
}
.mood-overall {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px 16px;
  margin-bottom: 10px;
}
.mood-overall-val {
  font-size: 28px;
  font-weight: 700;
  line-height: 1.2;
}
.mood-fng {
  margin-left: 8px;
  font-size: 14px;
  font-weight: 500;
}
.mood-reasons {
  margin: 0 0 12px;
  font-size: 13px;
  color: #8b9bb0;
}
.ex-block {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid rgba(148, 176, 210, 0.12);
}
.ex-block:first-of-type {
  margin-top: 4px;
  padding-top: 0;
  border-top: none;
}
.ex-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 10px 14px;
  margin-bottom: 10px;
}
.ex-title {
  margin: 0;
  font-size: 15px;
  font-weight: 650;
  color: #e9f0f7;
}
.ex-fund {
  font-size: 12px;
}
.ls-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
.ls-card {
  padding: 12px 14px;
  border-radius: 8px;
  background: rgba(10, 16, 24, 0.55);
  border: 1px solid rgba(148, 176, 210, 0.12);
}
.ls-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  font-size: 13px;
  color: #c5d2e0;
}
.ls-bias {
  font-weight: 650;
}
.ls-bar {
  display: flex;
  height: 8px;
  border-radius: 999px;
  overflow: hidden;
  background: rgba(148, 176, 210, 0.12);
  margin-bottom: 8px;
}
.ls-long {
  background: rgba(63, 185, 120, 0.85);
}
.ls-short {
  background: rgba(240, 90, 110, 0.85);
}
.ls-row {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
}
.ls-meta {
  font-size: 12px;
  color: #8b9bb0;
}
.onchain-label {
  font-size: 12px;
  color: #8b9bb0;
  margin-bottom: 4px;
}
.onchain-value {
  font-size: 20px;
  font-weight: 600;
  color: #e9f0f7;
}
.accent {
  color: #7aa2ff;
}
.muted {
  color: #8b9bb0;
}
@media (max-width: 720px) {
  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .onchain-metrics,
  .mood-grid,
  .mood-overall,
  .ls-grid {
    grid-template-columns: 1fr;
  }
}
</style>
