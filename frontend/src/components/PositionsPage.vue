<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { api } from '../api'
import { can } from '../auth'
import { LOSS_ALERT_PCT, LIVE_STOCKS } from '../config/liveStocks'
import { CLOSE_REASONS, OPEN_REASONS } from '../config/tradeReasons'
import { isoDate, money, pct, tone } from '../format'
import { armQuoteTimer, clearQuoteTimer } from '../marketHours'

const emit = defineEmits(['changed'])

const PLATFORM_OPTIONS = ['众安', '币安']
const STATUS_OPTIONS = ['未开始', '开始']
/** 常见虚拟币：录入时自动走币安行情 */
const CRYPTO_BASES = new Set([
  'BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'DOGE', 'ADA', 'AVAX', 'DOT', 'LINK',
  'MATIC', 'POL', 'ATOM', 'NEAR', 'APT', 'ARB', 'OP', 'SUI', 'PEPE', 'WIF',
  'TRX', 'LTC', 'BCH', 'UNI', 'AAVE', 'FIL', 'ICP', 'TON', 'SHIB',
])

const platform = ref('')
const rows = ref([])
const bookEquity = ref(0)
const loading = ref(true)
const error = ref('')
const modal = ref(null)
const editing = ref(null)
const quotes = ref({})
const quoteHint = ref('')
const quoteMarket = ref(null)
let quoteTimer

const plans = ref([])
const riskPortfolios = ref([])
const outlookTarget = ref('') // '' = 本笔单票；否则 risk portfolio id
const form = reactive({
  platform: '众安',
  status: '未开始',
  name: '',
  shares: '',
  open_price: '',
  fee: '',
  opened_on: isoDate(),
  expected_days: 21,
  notes: '',
  open_reason: '',
  strategy_id: '',
})

const outlook = ref(null)
const outlookLoading = ref(false)
const outlookError = ref('')

const closeForm = reactive({
  close_amount: '',
  fee: '',
  closed_on: isoDate(),
  notes: '',
  close_reason: '',
})

const editClosedForm = reactive({
  shares: '',
  open_price: '',
  opened_on: isoDate(),
  open_reason: '',
  close_amount: '',
  closed_on: isoDate(),
  close_reason: '',
  fee: '',
})

const computedAmount = computed(() => {
  const price = Number(form.open_price)
  const shares = Number(form.shares)
  if (!price || !shares) return 0
  return price * shares
})

const formSide = computed(() => {
  const plan = plans.value.find((row) => String(row.id) === String(form.strategy_id))
  return plan?.side === 'short' ? 'short' : 'long'
})

const selectedPortfolio = computed(() => {
  if (!outlookTarget.value) return null
  return riskPortfolios.value.find((r) => String(r.id) === String(outlookTarget.value)) || null
})

const portfolioLegSummary = computed(() => {
  const row = selectedPortfolio.value
  if (!row?.legs?.length) return []
  return row.legs.map((l) => ({
    symbol: l.symbol,
    side: l.side === 'short' ? '空' : '多',
    weight: l.weight,
  }))
})

async function refreshOutlook() {
  outlookError.value = ''
  outlook.value = null
  const days = Number(form.expected_days) || 0
  if (modal.value !== 'edit' || days < 1) {
    outlookError.value = '请填写预计持仓天数'
    return
  }

  const pid = outlookTarget.value ? Number(outlookTarget.value) : null
  const shares = Number(form.shares)
  const price = Number(form.open_price)
  const amount = shares > 0 && price > 0 ? shares * price : 0

  if (!pid) {
    const name = String(form.name || '').trim()
    if (!name || !(shares > 0) || !(price > 0)) {
      outlookError.value = '单票测算请先填代码、价格、数量'
      return
    }
  } else if (!(amount > 0) && !(Number(selectedPortfolio.value?.budget_capital) > 0)) {
    outlookError.value = '组合测算请填本笔金额，或给组合设预算本金'
    return
  }

  outlookLoading.value = true
  try {
    const body = {
      expected_days: Math.max(1, Math.min(days, 252)),
      n_sims: 2000,
      side: formSide.value,
      platform: form.platform,
    }
    if (pid) {
      body.risk_portfolio_id = pid
      if (amount > 0) {
        body.capital = amount
        body.shares = shares
        body.open_price = price
      }
    } else {
      body.name = String(form.name || '').trim()
      body.shares = shares
      body.open_price = price
    }
    outlook.value = await api.positionOutlook(body)
  } catch (e) {
    outlook.value = null
    outlookError.value = e.message || '测算失败'
  } finally {
    outlookLoading.value = false
  }
}
const displayRows = computed(() =>
  platform.value ? rows.value.filter((r) => r.platform === platform.value) : rows.value,
)
const plannedRows = computed(() => displayRows.value.filter((r) => r.status === '未开始'))
const openRows = computed(() => displayRows.value.filter((r) => r.status === '开始'))
const closedRows = computed(() => displayRows.value.filter((r) => r.status === '已结束'))
const allOpenRows = computed(() => rows.value.filter((r) => r.status === '开始'))
const liveMarket = computed(() =>
  allOpenRows.value.reduce((s, r) => s + Number(liveValue(r) ?? r.open_amount ?? 0), 0),
)
const invested = computed(() => allOpenRows.value.reduce((s, r) => s + Number(r.open_amount || 0), 0))
const market = computed(() => liveMarket.value)
const fees = computed(() => allOpenRows.value.reduce((s, r) => s + Number(r.fee || 0), 0))
const floating = computed(() =>
  allOpenRows.value.reduce((s, r) => s + Number(livePnlAmt(r) ?? r.pnl_amount ?? 0), 0),
)
const totalEquity = computed(() => bookEquity.value - invested.value - fees.value + liveMarket.value)
const available = computed(() => bookEquity.value - invested.value - fees.value)
const reasonStats = computed(() => {
  const groups = {}
  for (const row of rows.value.filter((r) => r.status === '已结束')) {
    const openName = row.open_reason || '未填开仓原因'
    const closeName = row.close_reason || '未填关仓原因'
    const key = `${openName}→${closeName}`
    if (!groups[key]) {
      groups[key] = { key, open_reason: openName, close_reason: closeName, count: 0, total: 0 }
    }
    groups[key].count += 1
    groups[key].total += Number(row.pnl_amount || 0)
  }
  return Object.values(groups).sort((a, b) => b.total - a.total)
})
const sections = computed(() => [
  { key: 'planned', title: '未开始', rows: plannedRows.value, kind: 'planned' },
  { key: 'open', title: '开始', rows: openRows.value, kind: 'open' },
  { key: 'closed', title: '已结束', rows: closedRows.value, kind: 'closed' },
])

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [list, goal, strategyList, riskList] = await Promise.all([
      api.positions(),
      api.settings(),
      api.strategyPlans().catch(() => []),
      api.riskPortfolios().catch(() => ({ items: [] })),
    ])
    rows.value = list
    bookEquity.value = Number(goal?.book_equity ?? goal?.equity ?? 0)
    plans.value = (strategyList || []).filter((p) => p.source !== 'file')
    riskPortfolios.value = riskList?.items || []
    await loadQuotes()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function resetForm() {
  Object.assign(form, {
    platform: '众安',
    status: '未开始',
    name: '',
    shares: '',
    open_price: '',
    fee: '',
    opened_on: isoDate(),
    expected_days: 21,
    notes: '',
    open_reason: '',
    strategy_id: '',
  })
  editing.value = null
  outlook.value = null
  outlookError.value = ''
  outlookTarget.value = ''
}

function openCreate() {
  resetForm()
  modal.value = 'edit'
}

function onNameChange() {
  const name = String(form.name || '').trim()
  if (!name) return
  if (looksLikeCrypto(name)) {
    form.name = compactSymbol(name) || name.toUpperCase()
    form.platform = '币安'
  }
}

function openEdit(row) {
  editing.value = row
  outlook.value = null
  outlookError.value = ''
  outlookTarget.value = ''
  Object.assign(form, {
    platform: row.platform,
    status: row.status === '开始' ? '开始' : '未开始',
    name: row.name,
    shares: row.shares,
    open_price: row.open_price,
    fee: row.fee ?? 0,
    opened_on: row.opened_on,
    expected_days: row.expected_days || 21,
    notes: row.notes || '',
    open_reason: row.open_reason || '',
    strategy_id: row.strategy_id || '',
  })
  modal.value = 'edit'
}

function onStrategyPick() {
  const plan = plans.value.find((row) => String(row.id) === String(form.strategy_id))
  if (plan && !form.open_reason.trim()) form.open_reason = plan.name
}

function openClose(row) {
  editing.value = row
  Object.assign(closeForm, {
    close_amount: row.open_amount ?? '',
    fee: row.fee ?? 0,
    closed_on: isoDate(),
    notes: '',
    close_reason: '',
  })
  modal.value = 'close'
}

async function save() {
  error.value = ''
  const payload = {
    platform: form.platform,
    status: form.status,
    name: form.name.trim(),
    shares: Number(form.shares),
    open_price: Number(form.open_price),
    fee: form.fee === '' ? 0 : Number(form.fee),
    expected_days: Math.max(1, Math.min(Number(form.expected_days) || 21, 252)),
    notes: form.notes || null,
    open_reason: form.open_reason.trim() || null,
    strategy_id: form.strategy_id ? Number(form.strategy_id) : null,
  }
  if (!editing.value) payload.opened_on = form.opened_on
  try {
    if (editing.value) {
      await api.updatePosition(editing.value.id, payload)
    } else {
      await api.createPosition(payload)
    }
    modal.value = null
    resetForm()
    await load()
    emit('changed')
  } catch (e) {
    error.value = e.message
  }
}

async function submitClose() {
  error.value = ''
  try {
    await api.closePosition(editing.value.id, {
      close_amount: Number(closeForm.close_amount),
      fee: closeForm.fee === '' ? 0 : Number(closeForm.fee),
      closed_on: closeForm.closed_on,
      notes: closeForm.notes || null,
      close_reason: closeForm.close_reason.trim() || null,
    })
    modal.value = null
    editing.value = null
    await load()
    emit('changed')
  } catch (e) {
    error.value = e.message
  }
}

function openEditClosed(row) {
  editing.value = row
  Object.assign(editClosedForm, {
    shares: row.shares,
    open_price: row.open_price,
    opened_on: row.opened_on,
    open_reason: row.open_reason || '',
    close_amount: row.close_amount,
    closed_on: row.closed_on || isoDate(),
    close_reason: row.close_reason || '',
    fee: row.fee ?? 0,
  })
  modal.value = 'editClosed'
}

async function submitEditClosed() {
  error.value = ''
  try {
    await api.editClosedPosition(editing.value.id, {
      shares: Number(editClosedForm.shares),
      open_price: Number(editClosedForm.open_price),
      opened_on: editClosedForm.opened_on,
      open_reason: editClosedForm.open_reason.trim() || null,
      close_amount: Number(editClosedForm.close_amount),
      closed_on: editClosedForm.closed_on,
      close_reason: editClosedForm.close_reason.trim() || null,
      fee: editClosedForm.fee === '' ? 0 : Number(editClosedForm.fee),
    })
    modal.value = null
    editing.value = null
    await load()
    emit('changed')
  } catch (e) {
    error.value = e.message
  }
}

async function start(row) {
  error.value = ''
  try {
    await api.updatePosition(row.id, { status: '开始' })
    await load()
    emit('changed')
  } catch (e) {
    error.value = e.message
  }
}

async function remove(row) {
  if (!confirm(`删除 ${row.name} 这笔持仓？`)) return
  try {
    await api.deletePosition(row.id)
    await load()
    emit('changed')
  } catch (e) {
    error.value = e.message
  }
}

function compactSymbol(raw) {
  return String(raw || '').replace(/[^A-Za-z0-9.]/g, '').toUpperCase()
}

function baseSymbol(raw) {
  return compactSymbol(raw).replace(/USDT$|USDC$/, '')
}

function sameSymbol(a, b) {
  const x = compactSymbol(a)
  const y = compactSymbol(b)
  if (!x || !y) return false
  if (x === y) return true
  return baseSymbol(x) === baseSymbol(y)
}

function looksLikeCrypto(raw) {
  const compact = compactSymbol(raw)
  if (!compact) return false
  if (compact.endsWith('USDT') || compact.endsWith('USDC')) return true
  return CRYPTO_BASES.has(compact)
}

function quoteItem(row) {
  const compact = compactSymbol(row.quote_symbol || row.name)
  const cfg = LIVE_STOCKS.find((s) => (
    sameSymbol(s.symbol, compact)
    || sameSymbol(s.symbol, row.name)
    || sameSymbol(s.symbol, row.quote_symbol)
  ))
  if (cfg) {
    return { symbol: compactSymbol(cfg.symbol), name: cfg.name || row.name, source: cfg.source }
  }
  if (row.quote_symbol && row.quote_source) {
    return { symbol: compactSymbol(row.quote_symbol), name: row.name, source: row.quote_source }
  }
  if (!compact) return null
  if (row.platform === '币安' || looksLikeCrypto(compact) || row.quote_source === 'binance') {
    const symbol = compact.endsWith('USDT') || compact.endsWith('USDC') ? compact : `${compact}USDT`
    return { symbol, name: row.name, source: 'binance' }
  }
  return { symbol: compact, name: row.name, source: 'tradingview' }
}

function rememberQuote(map, key, q) {
  if (key) map[String(key).toUpperCase()] = q
}

function findQuote(map, ...keys) {
  for (const key of keys) {
    const hit = map[String(key || '').toUpperCase()]
    if (hit?.price != null) return hit
  }
  const wanted = keys.map(baseSymbol).filter(Boolean)
  return Object.values(map).find((q) => {
    if (q?.price == null) return false
    return wanted.some((w) => sameSymbol(w, q.symbol) || sameSymbol(w, q.requested) || sameSymbol(w, q.name))
  }) || null
}

function liveQuote(row) {
  const item = quoteItem(row)
  const compact = compactSymbol(row.quote_symbol || row.name)
  return findQuote(
    quotes.value,
    quoteKey(row),
    item?.symbol,
    `${item?.source}:${item?.symbol}`,
    row.quote_symbol,
    row.name,
    compact,
    `${compact}USDT`,
    baseSymbol(compact),
  )
}

function quoteKey(row) {
  const item = quoteItem(row)
  if (!item) return ''
  return `${item.source}:${item.symbol}`
}

function livePrice(row) {
  return liveQuote(row)?.price ?? null
}

function liveValue(row) {
  const px = livePrice(row)
  if (px == null || !row.shares) return null
  return px * Number(row.shares)
}

function livePnlAmt(row) {
  const value = liveValue(row)
  if (value == null) return null
  return value - Number(row.open_amount || 0) - Number(row.fee || 0)
}

function livePnlPct(row) {
  const px = livePrice(row)
  const open = Number(row.open_price || 0)
  if (px == null || !open) return null
  return ((px - open) / open) * 100
}

function isLossAlert(row) {
  if (row.status === '已结束') return false
  const p = livePnlPct(row)
  return p != null && p <= -LOSS_ALERT_PCT
}

function displayPnl(row) {
  if (row.status === '已结束') return row.pnl_amount
  return livePnlAmt(row) ?? row.pnl_amount
}

function displayPnlPct(row) {
  if (row.status === '已结束') return row.pnl_pct == null ? null : row.pnl_pct * 100
  const live = livePnlPct(row)
  if (live != null) return live
  return row.pnl_pct == null ? null : row.pnl_pct * 100
}

async function loadQuotes() {
  const items = rows.value
    .filter((r) => r.status !== '已结束')
    .map(quoteItem)
    .filter((item) => item?.symbol)
  if (!items.length) {
    quotes.value = {}
    quoteHint.value = ''
    clearQuoteTimer(quoteTimer)
    return
  }
  try {
    const data = await api.quotes(items)
    const map = {}
    const list = data?.quotes || []
    quoteMarket.value = data?.market || null
    list.forEach((q, i) => {
      const item = items[i]
      rememberQuote(map, `${q.source || 'tradingview'}:${q.symbol}`, q)
      rememberQuote(map, q.symbol, q)
      rememberQuote(map, q.requested, q)
      rememberQuote(map, q.name, q)
      rememberQuote(map, baseSymbol(q.symbol), q)
      rememberQuote(map, baseSymbol(q.requested), q)
      if (item?.symbol) {
        rememberQuote(map, item.symbol, q)
        rememberQuote(map, `${item.source}:${item.symbol}`, q)
      }
    })
    quotes.value = map
    const missing = items.filter((item) => !findQuote(map, item.symbol, `${item.source}:${item.symbol}`, item.name))
    const sessionText = quoteMarket.value?.label ? `美股${quoteMarket.value.label}` : '行情'
    quoteHint.value = missing.length
      ? `未拉到：${missing.map((x) => x.symbol).join(', ')}`
      : `${sessionText}${quoteMarket.value?.live ? '已更新' : '，已停止刷新'} ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`
  } catch (e) {
    quoteHint.value = e.message || '行情拉取失败'
  }
  const always = items.some((item) => item.source === 'binance')
  clearQuoteTimer(quoteTimer)
  quoteTimer = armQuoteTimer(loadQuotes, quoteMarket.value, { always })
}

onMounted(() => {
  load()
})

onUnmounted(() => {
  clearQuoteTimer(quoteTimer)
})

function rowClassName({ row }) {
  return isLossAlert(row) ? 'loss-line' : ''
}

function emptyText(kind) {
  if (kind === 'planned') return '还没有未开始的计划。点右上角记一笔。'
  if (kind === 'open') return '还没有已开始的持仓。'
  return '还没有已结束的持仓。'
}
</script>

<template>
  <div>
    <div class="range-row">
      <div class="pills">
        <button
          class="pill"
          :class="{ active: platform === '' }"
          @click="platform = ''"
        >
          全部平台
        </button>
        <button
          v-for="p in PLATFORM_OPTIONS"
          :key="p"
          class="pill"
          :class="{ active: platform === p }"
          @click="platform = p"
        >
          {{ p }}
        </button>
      </div>
      <button
        v-if="can('btn.positions.create')"
        class="btn btn-primary"
        type="button"
        @click="openCreate"
      >
        记一笔持仓
      </button>
    </div>

    <div class="pos-cash-row">
      <div class="pos-cash">
        <div class="label">账户总资产</div>
        <div class="value" :class="tone(totalEquity - bookEquity)">{{ money(totalEquity, { signed: false }) }}</div>
        <div class="sub">
          与看板同一笔 · 账面 {{ money(bookEquity, { signed: false }) }}
          + 持仓盈亏 {{ money(floating) }}
        </div>
      </div>
      <div class="pos-cash pos-cash-avail">
        <div class="label">可使用金额</div>
        <div class="value">{{ money(available, { signed: false }) }}</div>
        <div class="sub">总资产 − 实时持仓 {{ money(liveMarket, { signed: false }) }}</div>
      </div>
    </div>

    <div class="pos-stats">
      <div class="stat">
        <span>进行中</span>
        <b>{{ openRows.length }}</b>
      </div>
      <div class="stat">
        <span>开仓金额</span>
        <b>{{ money(invested, { signed: false }) }}</b>
      </div>
      <div class="stat">
        <span>实时金额</span>
        <b>{{ money(market, { signed: false }) }}</b>
      </div>
      <div class="stat">
        <span>手续费</span>
        <b>{{ money(fees, { signed: false }) }}</b>
      </div>
      <div class="stat">
        <span>浮动盈亏</span>
        <b :class="tone(floating)">{{ money(floating) }}</b>
      </div>
    </div>

    <div class="error" v-if="error && !modal && !loading">{{ error }}</div>
    <div class="sub" v-else-if="quoteHint">{{ quoteHint }}</div>

    <section class="panel" v-for="block in sections" :key="block.key" style="margin-bottom: 12px">
      <h2>{{ block.title }}{{ platform ? ' · ' + platform : '' }}</h2>
      <p class="goal-lead" v-if="block.kind === 'planned'">
        只用来算持仓金额，不占用可使用资金，持仓天数为 0。点「开始」才真正开仓。相对开仓价亏到 {{ LOSS_ALERT_PCT }}% 会画红线。
      </p>
      <p class="goal-lead" v-else-if="block.kind === 'open'">
        已占用资金。实时价约 30 秒刷新。相对开仓价亏到 {{ LOSS_ALERT_PCT }}% 会画红线。
      </p>
      <p class="goal-lead" v-else>从开仓日算到关仓日。盈亏已进交易日志。</p>

      <el-table
        :data="block.rows"
        v-loading="loading"
        stripe
        style="width: 100%"
        :row-class-name="rowClassName"
        :empty-text="emptyText(block.kind)"
      >
        <el-table-column label="平台" width="80">
          <template #default="{ row }">
            <span class="tag">{{ row.platform }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="代码" min-width="88" />
        <el-table-column prop="status" label="状态" width="80" />
        <el-table-column label="持仓天数" width="90">
          <template #default="{ row }">{{ row.holding_days }} 天</template>
        </el-table-column>
        <el-table-column label="预计持仓" width="90">
          <template #default="{ row }">{{ row.expected_days || 21 }} 天</template>
        </el-table-column>
        <el-table-column prop="shares" label="持仓数量" width="96" />
        <el-table-column label="开仓价格" width="110">
          <template #default="{ row }">{{ money(row.open_price, { signed: false }) }}</template>
        </el-table-column>
        <el-table-column label="实时价格" width="130">
          <template #default="{ row }">
            <template v-if="block.kind !== 'closed' && livePrice(row) != null">
              {{ money(livePrice(row), { signed: false }) }}
              <span
                v-if="liveQuote(row)?.session_label"
                class="session-tag"
                :class="liveQuote(row).session"
              >{{ liveQuote(row).session_label }}</span>
            </template>
            <span v-else-if="block.kind !== 'closed'" class="sub">{{ liveQuote(row)?.error || '—' }}</span>
            <span v-else class="sub">—</span>
          </template>
        </el-table-column>
        <el-table-column label="实时金额" width="120">
          <template #default="{ row }">
            <template v-if="block.kind === 'closed'">
              {{ row.close_amount == null ? '—' : money(row.close_amount, { signed: false }) }}
            </template>
            <template v-else-if="liveValue(row) != null">
              {{ money(liveValue(row), { signed: false }) }}
            </template>
            <span v-else class="sub">—</span>
          </template>
        </el-table-column>
        <el-table-column label="开仓金额" width="110">
          <template #default="{ row }">{{ money(row.open_amount, { signed: false }) }}</template>
        </el-table-column>
        <el-table-column label="手续费" width="90">
          <template #default="{ row }">{{ money(row.fee || 0, { signed: false }) }}</template>
        </el-table-column>
        <el-table-column label="关仓金额" width="110">
          <template #default="{ row }">
            {{ row.close_amount == null ? '—' : money(row.close_amount, { signed: false }) }}
          </template>
        </el-table-column>
        <el-table-column label="指标" min-width="100">
          <template #default="{ row }">{{ row.strategy_name || '—' }}</template>
        </el-table-column>
        <el-table-column label="开仓原因" min-width="110">
          <template #default="{ row }">{{ row.open_reason || '—' }}</template>
        </el-table-column>
        <el-table-column label="关仓原因" min-width="110">
          <template #default="{ row }">{{ row.close_reason || '—' }}</template>
        </el-table-column>
        <el-table-column label="盈亏" width="120">
          <template #default="{ row }">
            <span :class="tone(displayPnl(row))">{{ money(displayPnl(row)) }}</span>
            <div class="sub" v-if="displayPnlPct(row) != null">{{ pct(displayPnlPct(row)) }}</div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <div class="pos-actions">
              <template v-if="block.kind === 'planned'">
                <el-button v-if="can('btn.positions.edit')" link type="primary" @click="openEdit(row)">改</el-button>
                <el-button v-if="can('btn.positions.edit')" link type="success" @click="start(row)">开始</el-button>
                <el-button v-if="can('btn.positions.delete')" link type="danger" @click="remove(row)">删除</el-button>
              </template>
              <template v-else-if="block.kind === 'open'">
                <el-button v-if="can('btn.positions.edit')" link type="primary" @click="openEdit(row)">改</el-button>
                <el-button v-if="can('btn.positions.close')" link type="warning" @click="openClose(row)">平仓</el-button>
                <el-button v-if="can('btn.positions.delete')" link type="danger" @click="remove(row)">删除</el-button>
              </template>
              <template v-else>
                <el-button v-if="can('btn.positions.edit')" link type="primary" @click="openEditClosed(row)">改</el-button>
                <span class="sub">已进交易日志</span>
              </template>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <section class="panel" v-if="reasonStats.length || loading" style="margin-bottom: 12px">
      <h2>原因盈亏</h2>
      <p class="goal-lead">按「开仓原因 → 关仓原因」组合汇总已结束持仓。平仓时原因会写成交易日志标签，看板「策略标签盈亏」也能看到。</p>
      <el-table :data="reasonStats" v-loading="loading" stripe style="width: 100%" empty-text="暂无已结束持仓的原因统计">
        <el-table-column prop="open_reason" label="开仓原因" min-width="140" />
        <el-table-column prop="close_reason" label="关仓原因" min-width="140" />
        <el-table-column prop="count" label="笔数" width="80" />
        <el-table-column label="盈亏" min-width="120">
          <template #default="{ row }">
            <span :class="tone(row.total)">{{ money(row.total) }}</span>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <div v-if="modal" class="modal-mask" @click.self="modal = null">
      <div class="modal" v-if="modal === 'edit'">
        <h3>{{ editing ? '改持仓' : '记一笔持仓' }}</h3>
        <p class="goal-lead">股票选众安，虚拟币选币安（填 BTC / ETH 也会自动识别）。未开始不占资金；改成「开始」后占用可使用金额。</p>
        <div class="form-grid">
          <div class="field">
            <label>平台</label>
            <select v-model="form.platform">
              <option v-for="p in PLATFORM_OPTIONS" :key="p" :value="p">{{ p }}</option>
            </select>
          </div>
          <div class="field">
            <label>状态</label>
            <select v-model="form.status">
              <option v-for="s in STATUS_OPTIONS" :key="s" :value="s">{{ s }}</option>
            </select>
          </div>
          <div class="field">
            <label>开仓日期{{ editing ? '（开始后不能改）' : '' }}</label>
            <input v-model="form.opened_on" type="date" :disabled="Boolean(editing)" />
          </div>
          <div class="field">
            <label>预计持仓天数</label>
            <input
              v-model="form.expected_days"
              type="number"
              min="1"
              max="252"
              step="1"
              placeholder="21"
            />
          </div>
          <div class="field">
            <label>代码</label>
            <input
              v-model="form.name"
              placeholder="股票 NVDA，或虚拟币 BTC / ETH"
              @change="onNameChange"
              @blur="onNameChange"
            />
          </div>
          <div class="field">
            <label>持仓数量</label>
            <input v-model="form.shares" type="number" step="any" min="0" />
          </div>
          <div class="field">
            <label>开仓价格 ($)</label>
            <input v-model="form.open_price" type="number" step="any" min="0" />
          </div>
          <div class="field">
            <label>持仓金额（开仓价格 × 数量）</label>
            <input :value="computedAmount ? computedAmount.toFixed(2) : ''" disabled />
          </div>
          <div class="field full">
            <label>组合测算</label>
            <div class="range-row" style="gap: 8px; flex-wrap: wrap; align-items: center">
              <select v-model="outlookTarget" class="strategy-select" style="min-width: 200px">
                <option value="">本笔单票</option>
                <option v-for="r in riskPortfolios" :key="r.id" :value="String(r.id)">
                  {{ r.name }}
                </option>
              </select>
              <button class="btn" type="button" :disabled="outlookLoading" @click="refreshOutlook">
                {{ outlookLoading ? '测算中…' : '测算预计收益' }}
              </button>
            </div>
            <div class="sub" v-if="portfolioLegSummary.length" style="margin-top: 6px">
              <span v-for="(l, i) in portfolioLegSummary" :key="i" style="margin-right: 10px">
                {{ l.symbol }} {{ l.side
                }}<template v-if="l.weight != null"> {{ Number(l.weight) }}</template>
              </span>
            </div>
            <div v-if="outlookError" class="error" style="margin-top: 6px">{{ outlookError }}</div>
            <div v-else-if="outlook" class="outlook-box" style="margin-top: 8px" :class="{ avoid: outlook.avoid_entry }">
              <div class="outlook-row">
                <span>预计收益</span>
                <strong :class="tone(outlook.median_pnl)" style="font-size: 20px">
                  {{ money(outlook.median_pnl) }}
                </strong>
              </div>
            </div>
          </div>
          <div class="field">
            <label>手续费 ($)</label>
            <input v-model="form.fee" type="number" step="0.01" min="0" placeholder="0" />
          </div>
          <div class="field">
            <label>指标</label>
            <select v-model="form.strategy_id" @change="onStrategyPick">
              <option value="">不选指标</option>
              <option v-for="plan in plans" :key="plan.id" :value="plan.id">
                {{ plan.name }} · {{ plan.side === 'short' ? '做空' : '做多' }}
              </option>
            </select>
            <div class="sub" v-if="!plans.length">还没有指标，先到「指标」保存一条。</div>
          </div>
          <div class="field">
            <label>开仓原因</label>
            <input v-model="form.open_reason" list="open-reasons" placeholder="周线突破 / 回踩确认，后面按这个统计" />
            <datalist id="open-reasons">
              <option v-for="r in OPEN_REASONS" :key="r" :value="r" />
            </datalist>
          </div>
          <div class="field full">
            <label>备注</label>
            <textarea v-model="form.notes" />
          </div>
        </div>
        <div class="error" v-if="error">{{ error }}</div>
        <div class="modal-actions">
          <button class="btn btn-ghost" type="button" @click="modal = null">取消</button>
          <button class="btn btn-primary" type="button" @click="save">保存</button>
        </div>
      </div>

      <div class="modal" v-if="modal === 'close'">
        <h3>结束持仓 · {{ editing?.name }}</h3>
        <p class="goal-lead">填写关仓金额和关仓原因。原因会写进交易日志标签，方便按原因看盈亏。</p>
        <div class="form-grid">
          <div class="field">
            <label>关仓金额 ($)</label>
            <input v-model="closeForm.close_amount" type="number" step="0.01" />
          </div>
          <div class="field">
            <label>手续费 ($)</label>
            <input v-model="closeForm.fee" type="number" step="0.01" min="0" />
          </div>
          <div class="field">
            <label>关仓日期</label>
            <input v-model="closeForm.closed_on" type="date" />
          </div>
          <div class="field">
            <label>关仓原因</label>
            <input v-model="closeForm.close_reason" list="close-reasons" placeholder="止盈 / 止损 / 结构破坏" />
            <datalist id="close-reasons">
              <option v-for="r in CLOSE_REASONS" :key="r" :value="r" />
            </datalist>
          </div>
          <div class="field full">
            <label>备注</label>
            <textarea v-model="closeForm.notes" />
          </div>
        </div>
        <div class="error" v-if="error">{{ error }}</div>
        <div class="modal-actions">
          <button class="btn btn-ghost" type="button" @click="modal = null">取消</button>
          <button class="btn btn-primary" type="button" @click="submitClose">确认平仓</button>
        </div>
      </div>

      <div class="modal" v-if="modal === 'editClosed'">
        <h3>改开关仓记录 · {{ editing?.name }}</h3>
        <p class="goal-lead">已结束的持仓只能改开仓记录和关仓记录，平台/状态/指标不能改。盈亏会按新数字重新算，交易日志里的盈亏和日期也会同步更新。</p>
        <div class="form-grid">
          <div class="field full"><label style="font-weight: 600">开仓记录</label></div>
          <div class="field">
            <label>持仓数量</label>
            <input v-model="editClosedForm.shares" type="number" step="0.0001" />
          </div>
          <div class="field">
            <label>开仓价格 ($)</label>
            <input v-model="editClosedForm.open_price" type="number" step="0.01" />
          </div>
          <div class="field">
            <label>开仓日期</label>
            <input v-model="editClosedForm.opened_on" type="date" />
          </div>
          <div class="field">
            <label>开仓原因</label>
            <input v-model="editClosedForm.open_reason" list="open-reasons" placeholder="周线突破 / 回踩确认" />
          </div>
          <div class="field full"><label style="font-weight: 600">关仓记录</label></div>
          <div class="field">
            <label>关仓金额 ($)</label>
            <input v-model="editClosedForm.close_amount" type="number" step="0.01" />
          </div>
          <div class="field">
            <label>手续费 ($)</label>
            <input v-model="editClosedForm.fee" type="number" step="0.01" min="0" />
          </div>
          <div class="field">
            <label>关仓日期</label>
            <input v-model="editClosedForm.closed_on" type="date" />
          </div>
          <div class="field">
            <label>关仓原因</label>
            <input v-model="editClosedForm.close_reason" list="close-reasons" placeholder="止盈 / 止损 / 结构破坏" />
          </div>
        </div>
        <div class="error" v-if="error">{{ error }}</div>
        <div class="modal-actions">
          <button class="btn btn-ghost" type="button" @click="modal = null">取消</button>
          <button class="btn btn-primary" type="button" @click="submitEditClosed">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>
