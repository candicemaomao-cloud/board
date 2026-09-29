<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { money } from '../format'

const props = defineProps({
  draft: { type: Object, default: null },
})
const emit = defineEmits(['saved', 'back-list'])

const windowDays = ref(252)
const confidence = ref(0.95)
const capital = ref('')
const horizonDays = ref(63)
const driftMode = ref('historical')
const lossLimit = ref(0.05)
const budgetModelId = ref('block_bootstrap')
const budgetModels = ref([])
const loading = ref(false)
const importing = ref(false)
const saving = ref(false)
const error = ref('')
const ok = ref('')
const data = ref(null)
const saveName = ref('')
const saveNotes = ref('')
const editingId = ref(null)
const budgetCapital = ref('10000')
const entryDate = ref('')
const budgetDays = ref(63)
const resetBudget = ref(true)

/** 预计持仓天数：同时驱动概率展望 horizon 与预算对比窗口 */
function syncHoldDays(days) {
  const n = Math.max(5, Math.min(Number(days) || 21, 126))
  horizonDays.value = n
  budgetDays.value = n
}

async function loadBudgetModels() {
  try {
    const res = await api.budgetModels()
    budgetModels.value = res.items || []
    if (res.default) budgetModelId.value = res.default
  } catch {
    /* 下拉可为空，测算仍可用 */
  }
}
onMounted(loadBudgetModels)

const legs = ref([
  { symbol: 'AAPL', weight: '40', amount: '', side: 'long', entry_price: '' },
  { symbol: 'MSFT', weight: '30', amount: '', side: 'long', entry_price: '' },
  { symbol: 'NVDA', weight: '30', amount: '', side: 'short', entry_price: '' },
])

function applyDraft(d) {
  if (!d) return
  editingId.value = d.id || null
  saveName.value = d.name || ''
  saveNotes.value = d.notes || ''
  budgetCapital.value = d.budget_capital != null ? String(d.budget_capital) : (budgetCapital.value || '10000')
  entryDate.value = d.entry_date || d.budget_start_date || ''
  const hold = Number(d.horizon_days || d.budget_days || 21) || 21
  syncHoldDays(hold)
  const mid = d.budget_model_id || d.budget?.outlook?.model_id || (d.budget_baseline && (() => {
    try { return JSON.parse(d.budget_baseline)?.outlook?.model_id } catch { return null }
  })())
  if (mid) budgetModelId.value = mid
  resetBudget.value = true
  if (d.loss_limit != null) lossLimit.value = Number(d.loss_limit) || 0.05
  if (d.window != null) windowDays.value = Number(d.window) || 252
  if (Array.isArray(d.legs) && d.legs.length) {
    legs.value = d.legs.map((l) => ({
      symbol: l.symbol || '',
      weight: l.weight != null && l.weight !== '' ? String(l.weight) : '',
      amount: l.amount != null && l.amount !== '' ? String(l.amount) : '',
      side: l.side === 'short' ? 'short' : 'long',
      entry_price: l.entry_price != null && l.entry_price !== '' ? String(l.entry_price) : '',
    }))
  }
  data.value = null
  error.value = ''
  ok.value = ''
}

watch(
  () => props.draft,
  (d) => applyDraft(d),
  { immediate: true, deep: true },
)
const CONF_OPTS = [
  { value: 0.9, label: '90%' },
  { value: 0.95, label: '95%' },
  { value: 0.99, label: '99%' },
]
const WINDOW_OPTS = [
  { value: 63, label: '近 3 月' },
  { value: 126, label: '近 6 月' },
  { value: 252, label: '近 1 年' },
]
const HOLD_OPTS = [
  { value: 10, label: '预计持仓 10 天' },
  { value: 21, label: '预计持仓 21 天' },
  { value: 30, label: '预计持仓 30 天' },
  { value: 42, label: '预计持仓 42 天' },
  { value: 63, label: '预计持仓 63 天' },
  { value: 126, label: '预计持仓 126 天' },
]

function pct(n, digits = 2) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  return `${(Number(n) * 100).toFixed(digits)}%`
}
function pctPoints(n, digits = 2) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  return `${Number(n).toFixed(digits)}%`
}
function num(n, digits = 2) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  return Number(n).toFixed(digits)
}
function px(n) {
  if (n == null || n === '') return '—'
  return money(n, { signed: false })
}

const port = computed(() => data.value?.portfolio || {})
const beta = computed(() => port.value?.beta || {})
const ratios = computed(() => port.value?.ratios || {})
const dd = computed(() => port.value?.drawdown || {})
const varBlock = computed(() => port.value?.var || {})
const forecast = computed(() => data.value?.forecast || {})
const boot = computed(() => forecast.value?.bootstrap || {})
const gbm = computed(() => forecast.value?.gbm || {})
const blockBoot = computed(() => forecast.value?.block_bootstrap || {})
const zeroDrift = computed(() => forecast.value?.zero_drift || null)
const garchT = computed(() => forecast.value?.garch_t || {})
const consistency = computed(() => forecast.value?.consistency || {})
const forecastModels = computed(() => [
  { key: 'Bootstrap', m: boot.value },
  { key: 'GBM', m: gbm.value },
  { key: 'Block', m: blockBoot.value },
  { key: 'GARCH-t', m: garchT.value },
])

/** 当前选用的预算模型结果（赚/亏） */
const budgetPick = computed(() => {
  const fc = forecast.value || {}
  const map = {
    block_bootstrap: fc.block_bootstrap,
    bootstrap: fc.bootstrap,
    gbm: fc.gbm,
    garch_t: fc.garch_t,
  }
  const block = map[budgetModelId.value] || fc.block_bootstrap || fc.bootstrap || {}
  const cap = Number(fc.capital ?? data.value?.capital ?? 0) || 0
  const med = block.median_value
  const pnl = med == null || !cap ? null : Number(med) - cap
  let verdict = '未知'
  if (pnl != null) {
    if (pnl > 0) verdict = '赚'
    else if (pnl < 0) verdict = '亏'
    else verdict = '平'
  }
  const meta = budgetModels.value.find((m) => m.id === budgetModelId.value)
  return {
    model_id: budgetModelId.value,
    label: meta?.label || budgetModelId.value,
    median_pnl: pnl,
    prob_profit: block.prob_profit,
    verdict,
    avoid: verdict === '亏',
  }
})

function addLeg() {
  legs.value.push({ symbol: '', weight: '', amount: '', side: 'long', entry_price: '' })
}
function removeLeg(i) {
  if (legs.value.length <= 1) return
  legs.value.splice(i, 1)
}

async function importPositions() {
  error.value = ''
  importing.value = true
  try {
    const rows = await api.positions({ status: '开始' })
    const open = (rows || []).filter((r) => r.status === '开始')
    if (!open.length) {
      error.value = '没有「开始」状态的持仓可导入'
      return
    }
    legs.value = open.map((r) => ({
      symbol: String(r.quote_symbol || r.name || '').toUpperCase(),
      weight: '',
      amount: String(Math.abs(Number(r.market_value || r.open_amount || 0)) || ''),
      side: Number(r.qty || r.quantity || r.open_qty || 0) < 0 || String(r.side || '').toLowerCase().includes('short')
        ? 'short'
        : 'long',
      entry_price: r.open_price != null && Number(r.open_price) > 0 ? String(r.open_price) : '',
    })).filter((x) => x.symbol)
    const daysList = open
      .map((r) => Number(r.expected_days) || 0)
      .filter((n) => n > 0)
    if (daysList.length) {
      const avg = Math.round(daysList.reduce((a, b) => a + b, 0) / daysList.length)
      syncHoldDays(avg)
      const dates = open.map((r) => r.opened_on).filter(Boolean).sort()
      if (dates.length && !entryDate.value) entryDate.value = dates[0]
    }
    if (!legs.value.length) {
      error.value = '持仓里没有有效代码'
    }
  } catch (e) {
    error.value = e.message
  } finally {
    importing.value = false
  }
}

function collectLegFields(row) {
  const symbol = String(row.symbol || '').trim().toUpperCase()
  if (!symbol) return null
  const weight = row.weight === '' || row.weight == null ? null : Number(row.weight)
  const amount = row.amount === '' || row.amount == null ? null : Number(row.amount)
  const entryRaw = row.entry_price === '' || row.entry_price == null ? null : Number(row.entry_price)
  const side = row.side === 'short' ? 'short' : 'long'
  if (weight != null && Number.isNaN(weight)) throw new Error(`${symbol} 权重不是数字`)
  if (amount != null && Number.isNaN(amount)) throw new Error(`${symbol} 金额不是数字`)
  if (entryRaw != null && (Number.isNaN(entryRaw) || entryRaw <= 0)) {
    throw new Error(`${symbol} 成本价无效`)
  }
  if (weight != null && weight < 0) throw new Error(`${symbol} 请用「做空」选择方向，权重填正数`)
  if (amount != null && amount < 0) throw new Error(`${symbol} 请用「做空」选择方向，金额填正数`)
  return { symbol, weight, amount, side, entry_price: entryRaw }
}

async function run() {
  error.value = ''
  ok.value = ''
  let payloadLegs
  try {
    payloadLegs = []
    for (const row of legs.value) {
      const leg = collectLegFields(row)
      if (leg) payloadLegs.push(leg)
    }
  } catch (e) {
    error.value = e.message
    return
  }
  if (!payloadLegs.length) {
    error.value = '请至少填一只股票代码'
    return
  }
  loading.value = true
  try {
    const capRaw = String(capital.value || '').trim()
    data.value = await api.portfolio({
      legs: payloadLegs,
      window: windowDays.value,
      confidence: confidence.value,
      capital: capRaw === '' ? null : Number(capRaw),
      horizon_days: horizonDays.value,
      n_sims: 4000,
      drift_mode: driftMode.value,
      loss_limit: lossLimit.value,
    })
  } catch (e) {
    data.value = null
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function collectLegsForSave() {
  const payloadLegs = []
  for (const row of legs.value) {
    const leg = collectLegFields(row)
    if (leg) payloadLegs.push(leg)
  }
  if (!payloadLegs.length) throw new Error('请至少填一只股票代码')
  return payloadLegs
}

async function saveToList() {
  error.value = ''
  ok.value = ''
  const name = String(saveName.value || '').trim()
  if (!name) {
    error.value = '保存前请填写组合名称'
    return
  }
  saving.value = true
  try {
    const cap = String(budgetCapital.value || '').trim()
    const ed = String(entryDate.value || '').trim()
    if (!ed) {
      error.value = '请填写持仓日期（用当天收盘价做概率展望预算）'
      saving.value = false
      return
    }
    if (cap === '' || Number(cap) <= 0) {
      error.value = '请填写预算本金'
      saving.value = false
      return
    }
    const saveLegs = collectLegsForSave()

    // 危机资本门禁：挂谷危机评分/可投比例过低时警告，确认后仍可保存
    let kakeyaNote = ''
    try {
      const k = await api.runKakeyaModel({
        legs: saveLegs.map((l) => ({
          symbol: l.symbol,
          weight: l.weight,
          amount: l.amount,
          side: l.side,
        })),
        hedge: true,
        hedge_benchmark: 'SPY',
        horizon_days: Number(horizonDays.value) || 21,
        capital: Number(cap),
        window: Number(windowDays.value) || 252,
        n_sims: 1000,
        loss_limit: Number(lossLimit.value) || 0.05,
      })
      const gate = k?.gate || k?.before?.capital_need
      const crisis = k?.before?.crisis_score
      if (gate && !gate.gate_ok) {
        const msg =
          (gate.gate_hint || '危机资本充实度不足') +
          `\n建议可投 ${((gate.investable_ratio || 0) * 100).toFixed(0)}%` +
          (crisis != null ? `；危机评分 ${(crisis * 100).toFixed(0)}%` : '') +
          '\n\n仍按展望中位预算保存？'
        if (!window.confirm(msg)) {
          error.value = '已取消保存：请先减仓/加对冲，或到「三维挂谷」核对危机管'
          saving.value = false
          return
        }
        kakeyaNote = `（已确认：可投约 ${((gate.investable_ratio || 0) * 100).toFixed(0)}%）`
      } else if (gate?.investable_ratio != null) {
        kakeyaNote = `；挂谷可投约 ${((gate.investable_ratio || 0) * 100).toFixed(0)}%`
      }
    } catch {
      // 挂谷失败不阻断保存
    }

    const body = {
      name,
      notes: String(saveNotes.value || '').trim() || null,
      legs: saveLegs,
      loss_limit: lossLimit.value,
      window: windowDays.value,
      horizon_days: horizonDays.value,
      enabled: false,
      budget_capital: Number(cap),
      entry_date: ed,
      budget_days: Number(budgetDays.value) || 30,
      reset_budget: !!resetBudget.value,
      budget_model_id: budgetModelId.value || 'block_bootstrap',
    }
    let row
    if (editingId.value) {
      row = await api.updateRiskPortfolio(editingId.value, body)
    } else {
      row = await api.createRiskPortfolio(body)
      editingId.value = row.id
    }
    const tgt = row?.budget_target_pnl
    ok.value =
      tgt != null
        ? `已保存：持仓日 ${row.entry_date || ed} 起算，预计持仓 ${horizonDays.value} 天，中位预算盈利 $${Number(tgt).toFixed(0)}${kakeyaNote}`
        : `已保存到风险组合列表${kakeyaNote}`
    emit('saved')
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

const corrSymbols = computed(() => data.value?.symbols || [])
const markowitz = computed(() => data.value?.markowitz || {})
const gmv = computed(() => markowitz.value?.gmv || null)
const tangency = computed(() => markowitz.value?.tangency || null)
const safeAlloc = computed(() => data.value?.safe_allocate || null)

const tab = ref('portfolio') // portfolio | credit
const creditYears = ref(10)
const creditNotional = ref('10000000000')
const creditStart = ref('A')
const creditLoading = ref(false)
const creditError = ref('')
const credit = ref(null)

async function runCredit() {
  creditError.value = ''
  creditLoading.value = true
  try {
    credit.value = await api.creditMigrate({
      years: creditYears.value,
      notional: Number(creditNotional.value) || 0,
      start: { [creditStart.value]: 1 },
    })
  } catch (e) {
    credit.value = null
    creditError.value = e.message
  } finally {
    creditLoading.value = false
  }
}

function applyWeights(kind) {
  const block = kind === 'gmv' ? gmv.value : tangency.value
  if (!block?.weights?.length) return
  // 使用可含空头的解析权重（非 long-only 投影）
  legs.value = block.weights.map((w) => {
    const pct = Number(w.weight_pct ?? 0)
    const prev = legs.value.find((x) => String(x.symbol || '').toUpperCase() === w.symbol)
    return {
      symbol: w.symbol,
      weight: String(Math.abs(pct).toFixed(2)),
      amount: '',
      side: pct < 0 ? 'short' : 'long',
      entry_price: prev?.entry_price || '',
    }
  })
}

function sideLabel(side) {
  return side === 'short' ? '做空' : '做多'
}

function applySafeWeights() {
  const block = safeAlloc.value
  if (!block?.weights?.length) return
  legs.value = block.weights
    .filter((w) => w.symbol !== 'CASH')
    .map((w) => {
      const prev = legs.value.find((x) => String(x.symbol || '').toUpperCase() === w.symbol)
      return {
        symbol: w.symbol,
        weight: String(Math.abs(Number(w.weight_pct || 0)).toFixed(2)),
        amount: '',
        side: w.side === 'short' ? 'short' : 'long',
        entry_price: prev?.entry_price || '',
      }
    })
  if (!legs.value.length) {
    error.value = '安全仓位几乎全是现金，请放宽亏损底线或换标的'
  }
}
</script>

<template>
  <p class="goal-lead">
    组合风险推演：四模型概率展望（含路径回撤）、模型一致性、马科维茨与协方差相关模拟。评级迁移见第二页签。
    测算完成后可保存到「风险组合」列表，开启后每日扫描危机系数。
  </p>

  <div class="range-row" style="margin-top: 8px; margin-bottom: 4px">
    <button class="btn" type="button" @click="emit('back-list')">← 返回风险组合列表</button>
    <input
      v-model="saveName"
      class="strategy-select"
      style="width: 160px"
      placeholder="组合名称（保存用）"
    />
    <input
      v-model="saveNotes"
      class="strategy-select"
      style="width: 160px"
      placeholder="备注（可选）"
    />
    <input
      v-model="budgetCapital"
      class="strategy-select"
      style="width: 110px"
      placeholder="预算本金$"
    />
    <input
      v-model="entryDate"
      class="strategy-select"
      style="width: 150px"
      type="date"
      title="持仓日期"
    />
    <select
      :value="horizonDays"
      class="strategy-select"
      style="width: 150px"
      title="预计持仓天数，用于计算展望收益"
      @change="syncHoldDays(Number($event.target.value))"
    >
      <option v-for="opt in HOLD_OPTS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
    </select>
    <label class="sub" style="display: inline-flex; align-items: center; gap: 4px">
      <input v-model="resetBudget" type="checkbox" />
      重算展望预算
    </label>
    <button class="btn btn-primary" type="button" :disabled="saving" @click="saveToList">
      {{ saving ? '保存中…' : editingId ? '更新到列表' : '保存到风险组合' }}
    </button>
  </div>
  <p class="sub">
    填「持仓日期」+「预计持仓天数」+本金后保存：按该日收盘为起点，用概率展望估算这 N 天的中位盈利，并与之后实际对比校准。
  </p>
  <p class="sub" v-if="ok" style="color: #3d9a5f">{{ ok }}</p>

  <div class="pills" style="margin-top: 8px">
    <button class="pill" type="button" :class="{ active: tab === 'portfolio' }" @click="tab = 'portfolio'">组合 / 马科维茨</button>
    <button class="pill" type="button" :class="{ active: tab === 'credit' }" @click="tab = 'credit'">评级迁移（马尔可夫）</button>
  </div>

  <template v-if="tab === 'portfolio'">
  <section class="panel" style="margin-top: 12px">
    <div class="range-row" style="margin-bottom: 10px">
      <button class="btn" type="button" :disabled="importing" @click="importPositions">
        {{ importing ? '导入中…' : '从持仓导入' }}
      </button>
      <button class="btn" type="button" @click="addLeg">+ 标的</button>
      <select v-model.number="windowDays" class="strategy-select">
        <option v-for="opt in WINDOW_OPTS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
      </select>
      <select v-model.number="confidence" class="strategy-select">
        <option v-for="opt in CONF_OPTS" :key="opt.value" :value="opt.value">置信度 {{ opt.label }}</option>
      </select>
      <select v-model="budgetModelId" class="strategy-select" title="预算模型：持仓 N 天赚亏">
        <option v-for="m in budgetModels" :key="m.id" :value="m.id">{{ m.label || m.name }}</option>
        <option v-if="!budgetModels.length" value="block_bootstrap">Block 概率展望</option>
      </select>
      <select
        :value="horizonDays"
        class="strategy-select"
        @change="syncHoldDays(Number($event.target.value))"
      >
        <option v-for="opt in HOLD_OPTS" :key="`hz-${opt.value}`" :value="opt.value">
          持仓 {{ opt.value }} 天
        </option>
      </select>
      <select v-model="driftMode" class="strategy-select">
        <option value="historical">带样本漂移</option>
        <option value="zero">去漂移 μ=0（风险视角）</option>
      </select>
      <select v-model.number="lossLimit" class="strategy-select">
        <option :value="0.03">亏损底线 3%</option>
        <option :value="0.05">亏损底线 5%</option>
        <option :value="0.08">亏损底线 8%</option>
        <option :value="0.10">亏损底线 10%</option>
      </select>
      <input
        v-model="capital"
        class="strategy-select"
        style="width: 140px"
        placeholder="组合本金(可选)"
      />
      <button class="btn btn-primary" type="button" :disabled="loading" @click="run">
        {{ loading ? '测算中…' : '测算组合' }}
      </button>
    </div>

    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>代码</th>
            <th>方向</th>
            <th>权重</th>
            <th>成本价</th>
            <th>金额</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, i) in legs" :key="i">
            <td>
              <input v-model="row.symbol" placeholder="AAPL" style="width: 110px" />
            </td>
            <td>
              <select v-model="row.side" class="strategy-select" style="width: 88px">
                <option value="long">做多</option>
                <option value="short">做空</option>
              </select>
            </td>
            <td>
              <input v-model="row.weight" placeholder="如 40" style="width: 90px" />
            </td>
            <td>
              <input v-model="row.entry_price" placeholder="如 346" style="width: 100px" />
            </td>
            <td>
              <input v-model="row.amount" placeholder="优先于权重" style="width: 120px" />
            </td>
            <td>
              <button class="btn" type="button" :disabled="legs.length <= 1" @click="removeLeg(i)">删</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p class="sub" style="margin-top: 8px">
      成本价用于危机系数：按你的买入价算浮亏，买入前的历史暴跌不计入。方向选做空后收益取反。权重/金额填正数，按毛敞口归一化。
    </p>
  </section>

  <div class="error" v-if="error" style="margin-top: 12px">{{ error }}</div>
  <p class="sub" v-else-if="loading">正在拉日线并对齐重叠交易日…</p>
  <p class="sub" v-else-if="!data">填好标的后点测算。</p>

  <template v-if="data">
    <section class="panel" style="margin-top: 12px">
      <h2>组合总览</h2>
      <p class="sub">
        重叠区间 {{ data.date_start }} ~ {{ data.date_end }} · {{ data.n_dates }} 个交易日 ·
        窗口 {{ data.window }} · 置信度 {{ (data.confidence * 100).toFixed(0) }}%
      </p>
      <div class="price-grid" style="margin-top: 8px">
        <div class="price-card" v-if="port.has_short || port.short_exposure > 0">
          <span>多 / 空 / 净敞口</span>
          <b style="font-size: 15px">{{ pct(port.long_exposure) }} / {{ pct(port.short_exposure) }} / {{ pct(port.net_exposure) }}</b>
          <small class="sub">毛敞口 {{ pct(port.gross_exposure) }}（按 |w| 归一）</small>
        </div>
        <div class="price-card">
          <span>组合年化波动</span>
          <b>{{ pct(port.vol_annual) }}</b>
          <small class="sub">日 {{ pct(port.vol_daily, 3) }}</small>
        </div>
        <div class="price-card">
          <span>年化收益（复利）</span>
          <b>{{ pct(port.ann_return_compound) }}</b>
          <small class="sub">线性 {{ pct(port.ann_return_linear) }}</small>
        </div>
        <div class="price-card">
          <span>分散化比率</span>
          <b>{{ num(port.diversification_ratio, 2) }}</b>
          <small class="sub">&gt;1 表示分散有效</small>
        </div>
        <div class="price-card">
          <span>Beta（{{ beta.benchmark || 'SPY' }}）</span>
          <b>{{ num(beta.beta, 2) }}</b>
          <small class="sub">相关 {{ num(beta.corr, 2) }}</small>
        </div>
      </div>
      <p class="sub" style="margin-top: 8px" v-if="Object.keys(data.dropped || {}).length">
        拉数失败已剔除：
        <span v-for="(msg, sym) in data.dropped" :key="sym">{{ sym }}（{{ msg }}） </span>
      </p>
    </section>

    <section class="panel" style="margin-top: 12px">
      <h2>成分权重与风险贡献</h2>
      <div class="table-wrap" style="margin-top: 8px">
        <table>
          <thead>
            <tr>
              <th>代码</th>
              <th>方向</th>
              <th>权重</th>
              <th>现价</th>
              <th>年化波动</th>
              <th>风险贡献</th>
              <th v-if="data.capital">名义金额</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="a in data.assets" :key="a.symbol">
              <td>{{ a.symbol }}</td>
              <td>{{ a.side_label || sideLabel(a.side) }}</td>
              <td class="mono">{{ pctPoints(a.weight_pct) }}</td>
              <td class="mono">{{ px(a.price) }}</td>
              <td class="mono">{{ pct(a.vol_annual) }}</td>
              <td class="mono">{{ pctPoints(a.risk_contribution_pct) }}</td>
              <td class="mono" v-if="data.capital">{{ px(a.amount) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="sub" style="margin-top: 8px">{{ data.note }}</p>
    </section>

    <section class="panel" style="margin-top: 12px">
      <h2>组合 VaR / CVaR（1 日）</h2>
      <div class="price-grid">
        <div class="price-card">
          <span>历史 VaR</span>
          <b>{{ pctPoints(varBlock.var_pct) }}</b>
          <small class="sub" v-if="port.var_dollar != null">约 {{ px(port.var_dollar) }}</small>
        </div>
        <div class="price-card">
          <span>历史 CVaR</span>
          <b>{{ pctPoints(varBlock.cvar_pct) }}</b>
          <small class="sub" v-if="port.cvar_dollar != null">约 {{ px(port.cvar_dollar) }}</small>
        </div>
        <div class="price-card">
          <span>最大回撤</span>
          <b>{{ pctPoints(dd.max_drawdown_pct) }}</b>
        </div>
        <div class="price-card">
          <span>夏普 / Sortino</span>
          <b>{{ num(ratios.sharpe, 2) }} / {{ num(ratios.sortino, 2) }}</b>
        </div>
      </div>
    </section>

    <section class="panel" style="margin-top: 12px" v-if="forecast.ok">
      <h2>预算模型 · {{ budgetPick.label }}（持仓 {{ forecast.horizon_days }} 天）</h2>
      <div class="range-row" style="gap: 20px; margin: 8px 0 12px; flex-wrap: wrap">
        <div>
          <div class="sub">结论</div>
          <b class="mono" style="font-size: 22px" :class="budgetPick.verdict === '赚' ? 'up' : budgetPick.verdict === '亏' ? 'down' : ''">
            {{ budgetPick.verdict }}
          </b>
        </div>
        <div>
          <div class="sub">中位盈亏</div>
          <b class="mono" :class="budgetPick.verdict === '赚' ? 'up' : budgetPick.verdict === '亏' ? 'down' : ''">
            {{ budgetPick.median_pnl == null ? '—' : money(budgetPick.median_pnl) }}
          </b>
        </div>
        <div>
          <div class="sub">盈利概率</div>
          <b class="mono">{{ pct(budgetPick.prob_profit) }}</b>
        </div>
      </div>
      <p class="warn-msg" v-if="budgetPick.avoid">中位展望为亏：样本外推偏空，建仓需谨慎。</p>
      <p class="sub">{{ forecast.note }}</p>
      <p class="sub">{{ forecast.disclaimer }}</p>
      <p class="sub" v-if="forecast.drift_bias" style="color: #f0b429">{{ forecast.drift_bias }}</p>
      <p class="sub">
        样本漂移：日 μ={{ num(forecast.mu_daily, 5) }}
        · 约年化 {{ pct(forecast.mu_annual_approx) }}
        · 日 σ={{ num(forecast.sigma_daily, 4) }}
        · 模式 {{ forecast.drift_mode === 'zero' ? '去漂移' : '保留历史均值' }}
      </p>
      <p class="sub" v-if="forecast.multivariate">已启用多资产协方差相关冲击（Σ=LL′），组合路径非独立加总。</p>

      <p class="sub" v-if="forecast.tagline"><i>{{ forecast.tagline }}</i></p>

      <div class="price-grid" style="margin-top: 8px" v-if="zeroDrift && forecast.drift_mode !== 'zero'">
        <div class="price-card">
          <span>对照：去漂移后 P(盈利)</span>
          <b>{{ pct(zeroDrift.prob_profit) }}</b>
          <small class="sub">Block μ=0 · 中位 {{ px(zeroDrift.median_value) }}</small>
        </div>
        <div class="price-card">
          <span>对照：去漂移 P(MDD&gt;10%)</span>
          <b>{{ pct(zeroDrift.prob_mdd_gt_10pct) }}</b>
          <small class="sub">CVaR5% {{ pct(zeroDrift.cvar_5pct_loss) }}</small>
        </div>
        <div class="price-card">
          <span>为何换票也偏多？</span>
          <b style="font-size: 14px">同处牛市样本</b>
          <small class="sub">多数美股近一年 μ&gt;0，外推就会一起看涨</small>
        </div>
      </div>
      <div class="price-grid" style="margin-top: 8px">
        <div class="price-card">
          <span>模型共识</span>
          <b>{{ consistency.label || '—' }}</b>
          <small class="sub">最大分歧 {{ pct(consistency.max_spread) }}</small>
        </div>
        <div class="price-card">
          <span>中位终值（Block）</span>
          <b>{{ px(blockBoot.median_value) }}</b>
          <small class="sub">90% 区间 {{ px(blockBoot.p05_value) }} ~ {{ px(blockBoot.p95_value) }}</small>
        </div>
        <div class="price-card">
          <span>路径风险 P(MDD&gt;10%)</span>
          <b>{{ pct(blockBoot.prob_mdd_gt_10pct) }}</b>
          <small class="sub">E[MDD] {{ pct(blockBoot.expected_mdd) }}</small>
        </div>
        <div class="price-card">
          <span>尾部 CVaR 5%</span>
          <b>{{ pct(blockBoot.cvar_5pct_loss) }}</b>
          <small class="sub">最差5%平均终值 {{ px(blockBoot.cvar_5pct_value) }}</small>
        </div>
        <div class="price-card">
          <span>回撤后最终盈利</span>
          <b>{{ pct(blockBoot.prob_mdd10_and_profit) }}</b>
          <small class="sub">P(MDD&gt;10% ∧ 期末盈利)</small>
        </div>
        <div class="price-card">
          <span>条件恢复率</span>
          <b>{{ pct(blockBoot.recovery_rate_mdd10) }}</b>
          <small class="sub">在 MDD&gt;10% 路径中最终仍盈利的比例</small>
        </div>
        <div class="price-card">
          <span>中位恢复时间</span>
          <b>{{ blockBoot.ttr_median_days != null ? (num(blockBoot.ttr_median_days, 0) + ' 日') : '—' }}</b>
          <small class="sub">
            P90 {{ blockBoot.ttr_p90_days != null ? num(blockBoot.ttr_p90_days, 0) + ' 日' : '—' }}
            · 未恢复 {{ pct(blockBoot.unrecovered_rate_mdd10) }}
          </small>
        </div>
        <div class="price-card">
          <span>P(期末亏损&gt;10%)</span>
          <b>{{ pct(blockBoot.prob_terminal_loss_10pct) }}</b>
          <small class="sub">Terminal Loss（终值口径，≠永久损失）</small>
        </div>
      </div>
      <p class="sub" style="margin-top: 8px">{{ consistency.summary }} {{ consistency.advice }}</p>

      <div class="table-wrap" style="margin-top: 12px">
        <table>
          <thead>
            <tr>
              <th>模型</th>
              <th>P(盈利)</th>
              <th>P(期末亏损&gt;10%)</th>
              <th>P(MDD&gt;10%)</th>
              <th>回撤后最终盈利</th>
              <th>条件恢复率</th>
              <th>CVaR5%</th>
              <th>中位TTR</th>
              <th>未恢复</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in forecastModels" :key="row.key">
              <td>{{ row.key }}</td>
              <td class="mono">{{ pct(row.m.prob_profit) }}</td>
              <td class="mono">{{ pct(row.m.prob_terminal_loss_10pct ?? row.m.prob_loss_10pct) }}</td>
              <td class="mono">{{ pct(row.m.prob_mdd_gt_10pct) }}</td>
              <td class="mono">{{ pct(row.m.prob_mdd10_and_profit) }}</td>
              <td class="mono">{{ pct(row.m.recovery_rate_mdd10) }}</td>
              <td class="mono">{{ pct(row.m.cvar_5pct_loss) }}</td>
              <td class="mono">{{ row.m.ttr_median_days != null ? num(row.m.ttr_median_days, 0) : '—' }}</td>
              <td class="mono">{{ pct(row.m.unrecovered_rate_mdd10) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="sub" style="margin-top: 8px">
        <b>口径区分：</b>
        {{ forecast.definitions?.prob_terminal_loss_10pct }}；
        {{ forecast.definitions?.prob_mdd_gt_10pct }}；
        {{ forecast.definitions?.prob_mdd10_and_profit }}；
        {{ forecast.definitions?.recovery_rate_mdd10 }}；
        {{ forecast.definitions?.ttr_median_days }}；
        {{ forecast.definitions?.cvar_5pct_loss }}。
        MDD 高但条件恢复率也高 → 路径波动大、部分为暂时性回撤；TTR 很长或未恢复比例高 → 体验上仍很痛苦。
      </p>

      <h3 style="margin: 14px 0 6px; font-size: 14px">路径分位扇形（Block Bootstrap，约 90% 终值落在 P5~P95）</h3>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>交易日</th>
              <th>P5</th>
              <th>P25</th>
              <th>P50</th>
              <th>P75</th>
              <th>P95</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in forecast.fan || []" :key="row.day">
              <td>T+{{ row.day }}</td>
              <td class="mono">{{ px(row.p05) }}</td>
              <td class="mono">{{ px(row.p25) }}</td>
              <td class="mono">{{ px(row.p50) }}</td>
              <td class="mono">{{ px(row.p75) }}</td>
              <td class="mono">{{ px(row.p95) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="sub" style="margin-top: 8px">
        四模型假设不同：Bootstrap=历史边际分布；GBM=连续正态；Block=保留波动聚集；GARCH-t=条件波动+厚尾。
        模拟 {{ forecast.n_sims }} 次 · 块长 {{ forecast.block_size }} 日。{{ forecast.tagline || '不预测一个价格，而是评估一组可能的结果。' }}
      </p>
    </section>


    <section class="panel" style="margin-top: 12px" v-if="safeAlloc?.ok">
      <h2>安全仓位（硬约束亏损底线）</h2>
      <p class="sub">{{ safeAlloc.note }}</p>
      <p class="sub">{{ safeAlloc.disclaimer }}</p>
      <div class="price-grid" style="margin-top: 8px">
        <div class="price-card">
          <span>亏损底线</span>
          <b>{{ pctPoints(safeAlloc.loss_limit_pct) }}</b>
          <small class="sub">硬约束 · 样本期 MDD</small>
        </div>
        <div class="price-card">
          <span>优化后历史 MDD</span>
          <b>{{ pctPoints(safeAlloc.hist_mdd_pct) }}</b>
          <small class="sub">安全垫 {{ pctPoints(safeAlloc.safety_margin_pct) }}</small>
        </div>
        <div class="price-card">
          <span>现金垫</span>
          <b>{{ pctPoints(safeAlloc.cash_weight_pct) }}</b>
          <small class="sub">用来卡死回撤上限</small>
        </div>
        <div class="price-card" v-if="safeAlloc.stats">
          <span>组合年化波动</span>
          <b>{{ pct(safeAlloc.stats.vol_annual) }}</b>
          <small class="sub">复利 {{ pct(safeAlloc.stats.ann_return_compound) }}</small>
        </div>
      </div>

      <div class="grid-2" style="margin-top: 10px">
        <div>
          <h3 style="margin: 0 0 6px; font-size: 14px">推荐仓位</h3>
          <div class="table-wrap">
            <table>
              <thead>
                <tr><th>代码</th><th>方向</th><th>权重</th></tr>
              </thead>
              <tbody>
                <tr v-for="w in safeAlloc.weights || []" :key="'safe'+w.symbol">
                  <td>{{ w.symbol }}</td>
                  <td>{{ w.symbol === 'CASH' ? '现金' : sideLabel(w.side) }}</td>
                  <td class="mono">{{ pctPoints(w.weight_pct) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <button class="btn btn-primary" type="button" style="margin-top: 8px" @click="applySafeWeights">
            填入上方并可用于再测算
          </button>
        </div>
        <div>
          <h3 style="margin: 0 0 6px; font-size: 14px">自动捕捉的反向对冲</h3>
          <p class="sub" v-if="safeAlloc.primary_hedge">
            主对冲对：
            <b>{{ safeAlloc.primary_hedge.a }}</b> ↔ <b>{{ safeAlloc.primary_hedge.b }}</b>
            · 相关 {{ num(safeAlloc.primary_hedge.corr, 2) }}
            <span v-if="safeAlloc.primary_hedge.corr < 0">（负相关，暴跌时互为缓冲）</span>
            <span v-else>（样本非负相关，主要靠降仓/现金）</span>
          </p>
          <div class="table-wrap" v-if="safeAlloc.hedge_pairs?.length">
            <table>
              <thead>
                <tr><th>资产对</th><th>相关</th><th>对冲分</th></tr>
              </thead>
              <tbody>
                <tr v-for="p in safeAlloc.hedge_pairs" :key="p.a+p.b">
                  <td>{{ p.a }} / {{ p.b }}</td>
                  <td class="mono">{{ num(p.corr, 2) }}</td>
                  <td class="mono">{{ num(p.hedge_score, 2) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p class="sub" style="margin-top: 8px" v-if="safeAlloc.stress_test">
            压力：{{ safeAlloc.stress_test.when }} ·
            A 均 {{ pct(safeAlloc.stress_test.avg_return_a, 2) }} ·
            B 均 {{ pct(safeAlloc.stress_test.avg_return_b, 2) }} ·
            组合 {{ pct(safeAlloc.stress_test.avg_return_portfolio, 2) }}
            <br />{{ safeAlloc.stress_test.interpretation }}
          </p>
        </div>
      </div>
      <p class="sub" style="margin-top: 8px">目标：{{ safeAlloc.objective }} · 可行解 {{ safeAlloc.feasible_count }} 组</p>
    </section>
    <section class="panel" style="margin-top: 12px" v-else-if="data && safeAlloc && !safeAlloc.ok">
      <h2>安全仓位</h2>
      <p class="sub">{{ safeAlloc.note }}</p>
    </section>

    <section class="panel" style="margin-top: 12px" v-if="markowitz.ok">
      <h2>马科维茨优化（Σ⁻¹）</h2>
      <p class="sub">{{ markowitz.note }}</p>
      <p class="sub" v-if="markowitz.formula">
        GMV：{{ markowitz.formula.gmv }} · 切线：{{ markowitz.formula.tangency }}
        · 条件数 {{ num(markowitz.condition_number, 1) }}
        <span v-if="markowitz.singular"> · 近奇异</span>
      </p>
      <div class="grid-2" style="margin-top: 8px">
        <div>
          <h3 style="margin: 0 0 6px; font-size: 14px">全局最小方差 GMV</h3>
          <p class="sub" v-if="gmv?.stats">
            年化波动 {{ pct(gmv.stats.vol_annual) }} · 复利收益 {{ pct(gmv.stats.ann_return_compound) }} ·
            夏普 {{ num(gmv.stats.sharpe, 2) }}
          </p>
          <div class="table-wrap">
            <table>
              <thead>
                <tr><th>代码</th><th>解析权重</th><th>多头投影</th></tr>
              </thead>
              <tbody>
                <tr v-for="w in gmv?.weights || []" :key="'g'+w.symbol">
                  <td>{{ w.symbol }}</td>
                  <td class="mono">{{ pctPoints(w.weight_pct) }}</td>
                  <td class="mono">{{ pctPoints(w.weight_long_only_pct) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <button class="btn" type="button" style="margin-top: 8px" @click="applyWeights('gmv')">
            用多头投影权重填入上方
          </button>
        </div>
        <div>
          <h3 style="margin: 0 0 6px; font-size: 14px">切线组合（最大夏普）</h3>
          <p class="sub" v-if="tangency?.stats">
            年化波动 {{ pct(tangency.stats.vol_annual) }} · 复利收益 {{ pct(tangency.stats.ann_return_compound) }} ·
            夏普 {{ num(tangency.stats.sharpe, 2) }}
          </p>
          <p class="sub" v-if="tangency?.note">{{ tangency.note }}</p>
          <div class="table-wrap">
            <table>
              <thead>
                <tr><th>代码</th><th>解析权重</th><th>多头投影</th></tr>
              </thead>
              <tbody>
                <tr v-for="w in tangency?.weights || []" :key="'t'+w.symbol">
                  <td>{{ w.symbol }}</td>
                  <td class="mono">{{ pctPoints(w.weight_pct) }}</td>
                  <td class="mono">{{ pctPoints(w.weight_long_only_pct) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <button class="btn" type="button" style="margin-top: 8px" @click="applyWeights('tangency')">
            用多头投影权重填入上方
          </button>
        </div>
      </div>
    </section>
    <section class="panel" style="margin-top: 12px" v-else-if="data && markowitz.note">
      <h2>马科维茨优化</h2>
      <p class="sub">{{ markowitz.note }}</p>
    </section>

    <section class="panel" style="margin-top: 12px">
      <h2>相关矩阵</h2>
      <div class="table-wrap" style="margin-top: 8px">
        <table>
          <thead>
            <tr>
              <th></th>
              <th v-for="s in corrSymbols" :key="s">{{ s }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in data.correlation" :key="row.symbol">
              <td>{{ row.symbol }}</td>
              <td v-for="s in corrSymbols" :key="s" class="mono">{{ num(row[s], 2) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </template>
  </template>

  <template v-if="tab === 'credit'">
    <section class="panel" style="margin-top: 12px">
      <h2>信用评级迁移 · 马尔可夫链</h2>
      <p class="sub">
        用示意转移矩阵 P 做 Pⁿ，预测 n 年后评级分布与落入 D（违约）的敞口。非官方标普/穆迪矩阵，仅教学演示。
      </p>
      <div class="range-row" style="margin-top: 8px">
        <select v-model="creditStart" class="strategy-select">
          <option v-for="r in ['AAA','AA','A','BBB','BB','B','CCC']" :key="r" :value="r">期初 {{ r }}</option>
        </select>
        <select v-model.number="creditYears" class="strategy-select">
          <option v-for="y in [1,2,3,5,10,15,20]" :key="y" :value="y">{{ y }} 年后</option>
        </select>
        <input v-model="creditNotional" class="strategy-select" style="width: 160px" placeholder="名义本金" />
        <button class="btn btn-primary" type="button" :disabled="creditLoading" @click="runCredit">
          {{ creditLoading ? '计算中…' : '预测迁移' }}
        </button>
      </div>
      <div class="error" v-if="creditError" style="margin-top: 8px">{{ creditError }}</div>
    </section>

    <template v-if="credit">
      <section class="panel" style="margin-top: 12px">
        <h2>预测结果（{{ credit.years }} 年）</h2>
        <div class="price-grid">
          <div class="price-card">
            <span>违约概率 → D</span>
            <b>{{ pct(credit.default_probability) }}</b>
          </div>
          <div class="price-card">
            <span>示意坏账敞口</span>
            <b>{{ px(credit.default_notional) }}</b>
            <small class="sub">名义 {{ px(credit.notional) }}</small>
          </div>
        </div>
        <p class="sub" style="margin-top: 8px">{{ credit.note }}</p>
        <div class="table-wrap" style="margin-top: 8px">
          <table>
            <thead>
              <tr>
                <th>评级</th>
                <th>期初权重</th>
                <th>{{ credit.years }} 年后</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in credit.ratings" :key="r">
                <td>{{ r }}</td>
                <td class="mono">{{ pct(credit.start_weights?.[r]) }}</td>
                <td class="mono">{{ pct(credit.end_weights?.[r]) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="panel" style="margin-top: 12px">
        <h2>1 年转移矩阵 P（示意）</h2>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>从 \ 到</th>
                <th v-for="r in credit.ratings" :key="r">{{ r }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in credit.transition_1y" :key="row.from">
                <td>{{ row.from }}</td>
                <td v-for="r in credit.ratings" :key="r" class="mono">{{ num(row[r], 3) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </template>
</template>
