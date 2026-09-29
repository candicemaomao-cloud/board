<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { TIMEFRAMES } from '../config/strategies'
import { money, tone } from '../format'

const KINDS = [
  { key: 'ols', label: 'OLS 回归残差', hint: 'log(A) − β log(B)，β 用滚动最小二乘。适合利率–成长、股票对。' },
  { key: 'ratio', label: '对数比值', hint: 'log(A/B)，β=1。适合几乎同业、同波动的一对。' },
  { key: 'dollar', label: '名义中性', hint: '按期初价格归一后 A−B。适合 ETF 对、信用对。' },
]

const BT_RANGES = [
  { days: 0, label: '全部可得数据' },
  { days: 180, label: '近 6 个月' },
  { days: 365, label: '近 1 年' },
  { days: 730, label: '近 2 年' },
]
const BT_RANGES_INTRA = [
  { days: 0, label: '全部可得（约 1 个月，不是年线）' },
  { days: 5, label: '近 5 个交易日' },
  { days: 10, label: '近 10 个交易日' },
  { days: 20, label: '近 20 个交易日' },
]

const PRESETS = [
  { name: 'GOOG / META 同业', kind: 'ols', leg_a: 'GOOG', leg_b: 'META', lookback: 60, entry_z: 2, notes: '广告巨头相对定价，比 MU/GOOG 更接近一对。' },
  { name: 'NVDA / SOXX 对行业', kind: 'ols', leg_a: 'NVDA', leg_b: 'SOXX', lookback: 60, entry_z: 2, notes: '个股相对半导体行业 ETF 是否定价异常。' },
  { name: 'NVDA / QQQ 对基准', kind: 'ols', leg_a: 'NVDA', leg_b: 'QQQ', lookback: 60, entry_z: 2, notes: '必须按 β 配仓，1 美元对 1 美元是伪对冲。' },
  { name: 'QQQ / SPY 跨指数', kind: 'ols', leg_a: 'QQQ', leg_b: 'SPY', lookback: 60, entry_z: 2, notes: '成长相对大盘的相对价值。' },
  { name: 'QQQ / TLT 利率–成长', kind: 'ols', leg_a: 'QQQ', leg_b: 'TLT', lookback: 60, entry_z: 2, notes: '实际利率上行时成长股相对国债承压。' },
  { name: 'NVDA / MU 半导体', kind: 'ols', leg_a: 'NVDA', leg_b: 'MU', lookback: 60, entry_z: 2, notes: '同是半导体周期。' },
]

const HEDGE_SUGGEST = ['SOXX', 'SMH', 'SPY', 'QQQ', 'TLT', 'XLK', 'XLF', 'XLE', 'IWM']
const MAX_HEDGES = 2

const plans = ref([])
const screen = ref('list')
const editingId = ref(null)
const search = ref('')
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const ok = ref('')
const result = ref(null)
const runId = ref('')

const form = reactive(emptyForm())

function emptyForm() {
  return {
    name: '',
    notes: '',
    kind: 'ols',
    timeframe: '1d',
    leg_a: 'QQQ',
    leg_b: 'TLT',
    lookback: 60,
    entry_z: 2,
    exit_z: 0.5,
    stop_z: 3.5,
    beta: '',
    notional: 10000,
    bt_days: 0,
    macro_filter: false,
    factors: ['TLT'],
  }
}

function isFilePlan(row) {
  return row?.source === 'file' || String(row?.id || '').startsWith('u_')
}

const fileEditing = computed(() => isFilePlan(plans.value.find((p) => p.id === editingId.value) || result.value?.strategy))
const factorDraft = ref('')

function normSym(raw) {
  return String(raw || '')
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9.\-]/g, '')
}

function addFactorsFromText(text) {
  const parts = String(text || '')
    .split(/[\s,+/]+/)
    .map(normSym)
    .filter(Boolean)
  const a = normSym(form.leg_a)
  for (const s of parts) {
    if (!s || s === a) continue
    if (form.factors.includes(s)) continue
    if (form.factors.length >= MAX_HEDGES) break
    form.factors.push(s)
  }
  form.leg_b = form.factors[0] || ''
  factorDraft.value = ''
}

function removeFactor(i) {
  form.factors.splice(i, 1)
  form.leg_b = form.factors[0] || ''
}

function toggleSuggest(sym) {
  const s = normSym(sym)
  const a = normSym(form.leg_a)
  if (!s || s === a) return
  const i = form.factors.indexOf(s)
  if (i >= 0) form.factors.splice(i, 1)
  else if (form.factors.length < MAX_HEDGES) form.factors.push(s)
  form.leg_b = form.factors[0] || ''
}

function onFactorKey(e) {
  if (e.key === 'Enter' || e.key === ',' || e.key === ' ') {
    e.preventDefault()
    addFactorsFromText(factorDraft.value)
  }
}

function applyPreset(p) {
  form.name = p.name
  form.notes = p.notes
  form.kind = p.kind
  form.leg_a = p.leg_a
  form.leg_b = p.leg_b
  form.lookback = p.lookback
  form.entry_z = p.entry_z
  form.exit_z = 0.5
  form.stop_z = 3.5
  form.timeframe = '1d'
  form.factors = p.leg_b ? [p.leg_b] : []
}

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return plans.value
  return plans.value.filter((row) =>
    `${row.name} ${row.leg_a} ${row.leg_b} ${(row.factors || []).join(' ')} ${row.file || ''}`.toLowerCase().includes(q),
  )
})

const kindHint = computed(() => {
  if ((form.factors || []).length >= 2 || form.timeframe === '5m' || form.timeframe === '30m') {
    return `优先两只标的（${form.leg_a || 'A'} vs 1 只对冲）。最多再加 1 只，合计 3 只。再多手续费太高。`
  }
  return KINDS.find((k) => k.key === form.kind)?.hint || ''
})
const btRanges = computed(() =>
  form.timeframe === '5m' || form.timeframe === '30m' ? BT_RANGES_INTRA : BT_RANGES,
)
const isResidual = computed(() => result.value?.mode === 'residual')

function tfLabel(key) {
  return TIMEFRAMES.find((row) => row.key === key)?.label || key
}

function sideHint(side, buy, sell) {
  if (side === 'long') return `买 ≤ ${buy ?? '—'}`
  if (side === 'short') return `卖 ≥ ${sell ?? '—'}`
  return '等待'
}

const recentTrades = computed(() =>
  [...(result.value?.backtest?.recent || [])].reverse(),
)

function signalClass(sig) {
  if (sig === 'long_spread') return 'up'
  if (sig === 'short_spread') return 'down'
  if (sig === 'blocked') return 'warn'
  return ''
}

function signalLabel(sig) {
  if (isResidual.value) {
    if (sig === 'long_spread') return '做多残差'
    if (sig === 'short_spread') return '做空残差'
    if (sig === 'blocked') return '趋势过滤'
    return '空仓'
  }
  if (sig === 'long_spread') return '做多价差'
  if (sig === 'short_spread') return '做空价差'
  if (sig === 'blocked') return '宏观过滤'
  if (result.value?.wait?.key === 'after_stop') return '止损后等待'
  if (result.value?.wait?.key === 'no_cross') return '等穿越'
  return '空仓'
}

function fillForm(row) {
  Object.assign(form, emptyForm(), {
    name: row.name || '',
    notes: row.notes || '',
    kind: row.kind || 'ols',
    timeframe: row.timeframe || '1d',
    leg_a: row.leg_a || '',
    leg_b: row.leg_b || '',
    lookback: row.lookback || 60,
    entry_z: row.entry_z ?? 2,
    exit_z: row.exit_z ?? 0.5,
    stop_z: row.stop_z ?? 3.5,
    beta: row.beta ?? '',
    notional: row.notional ?? 10000,
    bt_days: row.bt_days ?? 0,
    macro_filter: !!row.macro_filter,
    factors: (row.factors?.length ? [...row.factors] : row.leg_b ? [row.leg_b] : []).slice(0, MAX_HEDGES),
  })
  form.leg_b = form.factors[0] || form.leg_b
}

function body() {
  return {
    name: form.name,
    notes: form.notes || null,
    kind: form.kind,
    timeframe: form.timeframe,
    leg_a: form.leg_a,
    leg_b: form.factors[0] || form.leg_b,
    lookback: Number(form.lookback),
    entry_z: Number(form.entry_z),
    exit_z: Number(form.exit_z),
    stop_z: Number(form.stop_z),
    beta: form.beta === '' || form.beta == null ? null : Number(form.beta),
    notional: Number(form.notional),
    bt_days: Number(form.bt_days),
    macro_filter: !!form.macro_filter,
    factors: (form.factors || []).slice(0, MAX_HEDGES),
  }
}

async function loadPlans() {
  error.value = ''
  try {
    plans.value = await api.arbPlans()
  } catch (e) {
    error.value = e.message || '加载失败'
  }
}

function openNew() {
  editingId.value = null
  Object.assign(form, emptyForm())
  result.value = null
  ok.value = ''
  error.value = ''
  screen.value = 'edit'
}

function openEdit(row) {
  if (isFilePlan(row)) {
    error.value = `手写策略请改文件 backend/app/user_arbs/${row.file || ''}`
    return
  }
  editingId.value = row.id
  fillForm(row)
  result.value = null
  ok.value = ''
  error.value = ''
  screen.value = 'edit'
}

function openEditFromRun() {
  const id = result.value?.strategy?.id || editingId.value
  const row = plans.value.find((p) => p.id === id)
  if (isFilePlan(row || result.value?.strategy)) {
    error.value = `手写策略请改文件 backend/app/user_arbs/${row?.file || result.value?.strategy?.file || ''}`
    return
  }
  if (row) openEdit(row)
  else screen.value = 'edit'
}

function backToList() {
  screen.value = 'list'
  result.value = null
  loadPlans()
}

async function save() {
  saving.value = true
  error.value = ''
  ok.value = ''
  try {
    if (editingId.value) {
      const row = await api.updateArb(editingId.value, body())
      editingId.value = row.id
      ok.value = '已保存'
    } else {
      const row = await api.createArb(body())
      editingId.value = row.id
      ok.value = '已创建'
    }
    await loadPlans()
  } catch (e) {
    error.value = e.message || '保存失败'
  } finally {
    saving.value = false
  }
}

async function removePlan(row) {
  if (isFilePlan(row)) {
    error.value = `手写策略请直接删 backend/app/user_arbs/${row.file || ''}`
    return
  }
  if (!confirm(`删除套利策略「${row.name}」？`)) return
  error.value = ''
  try {
    await api.deleteArb(row.id)
    if (editingId.value === row.id) backToList()
    else await loadPlans()
  } catch (e) {
    error.value = e.message
  }
}

async function run(id) {
  loading.value = true
  error.value = ''
  ok.value = ''
  try {
    // 列表点「计算」用已保存的行；结果页/编辑页点计算则先写入当前表单，否则改止损不会进回测。
    // 手写 .py 不以表单为准，改文件后直接重跑。
    if (!id) {
      if (isFilePlan({ id: editingId.value }) || isFilePlan(result.value?.strategy)) {
        // skip save
      } else if (editingId.value) {
        const row = await api.updateArb(editingId.value, body())
        editingId.value = row.id
        fillForm(row)
      } else {
        const row = await api.createArb(body())
        editingId.value = row.id
        fillForm(row)
        await loadPlans()
      }
    }
    const target = id || editingId.value || runId.value
    if (!target) {
      error.value = '请先保存策略'
      return
    }
    result.value = null
    result.value = await api.runArb(target)
    runId.value = target
    screen.value = 'run'
  } catch (e) {
    error.value = e.message || '计算失败'
  } finally {
    loading.value = false
  }
}

async function openRun(row) {
  editingId.value = row.id
  fillForm(row)
  await run(row.id)
}

onMounted(() => loadPlans())
</script>

<template>
  <template v-if="screen === 'run'">
    <div class="range-row">
      <button class="btn" type="button" @click="backToList">返回列表</button>
      <button class="btn" type="button" v-if="!fileEditing" @click="openEditFromRun">改规则</button>
      <button class="btn btn-primary" type="button" :disabled="loading" @click="run()">
        {{ loading ? '计算中…' : '重新计算' }}
      </button>
    </div>
    <p class="goal-lead" v-if="fileEditing">
      这是手写策略，参数以
      <span class="mono">backend/app/user_arbs/{{ result?.strategy?.file || plans.find((p) => p.id === editingId)?.file }}</span>
      为准。改文件后点「重新计算」。
    </p>
    <p class="goal-lead" v-else-if="form.factors?.length">
      {{ form.leg_a }} vs {{ form.factors.join(' + ') }} · {{ tfLabel(form.timeframe) }}。
      对冲 {{ form.factors.length }} 只（优先 1，最多 2）。再多手续费太高。
    </p>
    <p class="goal-lead" v-else>
      赚的是相对价值 R_A − β R_B，不是猜明天涨跌。相关 ≠ 协整 ≠ 可套利。目标不是稳赚的一对，而是成本后仍有正期望、样本外还站得住。
    </p>
    <div class="error" v-if="error">{{ error }}</div>
    <section class="panel" style="margin-top: 12px" v-if="!fileEditing">
      <div class="form-grid">
        <div class="field">
          <label>周期（计算用这根 K 线）</label>
          <select v-model="form.timeframe">
            <option v-for="tf in TIMEFRAMES" :key="tf.key" :value="tf.key">{{ tf.label }}</option>
          </select>
        </div>
        <div class="field">
          <label>投入金额（美元，{{ form.leg_a || 'A' }}）</label>
          <input v-model.number="form.notional" type="number" min="100" step="100" />
        </div>
        <div class="field">
          <label>回测时间</label>
          <select v-model.number="form.bt_days">
            <option v-for="r in btRanges" :key="r.days" :value="r.days">{{ r.label }}</option>
          </select>
        </div>
        <div class="field">
          <label>开仓 |z|</label>
          <input v-model.number="form.entry_z" type="number" step="0.1" min="0.5" max="6" />
        </div>
        <div class="field">
          <label>平仓 |z|</label>
          <input v-model.number="form.exit_z" type="number" step="0.1" min="0.1" max="3" />
        </div>
        <div class="field">
          <label>止损 |z|</label>
          <input v-model.number="form.stop_z" type="number" step="0.1" min="1.5" max="12" />
        </div>
        <div class="field full">
          <label>对冲（优先 1 只，最多 2 只 · {{ tfLabel(form.timeframe) }}）</label>
          <div class="macro-chips" style="margin-bottom: 8px">
            <span v-for="(s, i) in form.factors" :key="s" class="clause-chip">
              {{ s }}
              <button class="chip-x" type="button" @click="removeFactor(i)">×</button>
            </span>
            <span class="sub" v-if="!form.factors.length">还没选对冲。下面点代码，或输入后回车。</span>
          </div>
          <input
            v-model="factorDraft"
            placeholder="输入代码回车，可加多个，例如 SPY QQQ SOXX"
            @keydown="onFactorKey"
            @blur="addFactorsFromText(factorDraft)"
          />
          <div class="macro-chips" style="margin-top: 8px">
            <button
              v-for="s in HEDGE_SUGGEST"
              :key="s"
              class="chip"
              type="button"
              :class="{ now: form.factors.includes(s) }"
              @click="toggleSuggest(s)"
            >
              {{ s }}
            </button>
          </div>
        </div>
      </div>
      <p class="sub" style="margin: 8px 0 0">
        改完点「重新计算」才会按新止损重跑。对冲优先 1 只（两标的），最多再加 1 只。
      </p>
    </section>
    <p class="sub" v-if="loading">正在拉 {{ tfLabel(form.timeframe) }} K 线并对齐…</p>

    <template v-if="result">
      <section class="panel">
        <div class="range-row" style="gap: 28px; flex-wrap: wrap; align-items: flex-start">
          <div>
            <div class="sub">信号</div>
            <b :class="signalClass(result.signal)" style="font-size: 22px">
              {{ signalLabel(result.signal) }}
            </b>
            <div class="sub">{{ result.action }}</div>
          </div>
          <div>
            <div class="sub">z 分数</div>
            <b class="mono" style="font-size: 36px" :class="signalClass(result.signal)">
              {{ result.z ?? '—' }}
            </b>
            <div class="sub">
              本次规则 开 ±{{ result.thresholds?.entry }} · 平 ±{{ result.thresholds?.exit }} · 停 ±{{ result.thresholds?.stop }}
            </div>
          </div>
          <div>
            <div class="sub">β / 相关</div>
            <b class="mono" style="font-size: 18px">{{ result.beta ?? '—' }}</b>
            <div class="sub" v-if="isResidual">{{ result.hedge }}</div>
            <div class="sub" v-else>收益相关 {{ result.corr ?? '—' }}</div>
          </div>
          <div>
            <div class="sub">投入金额</div>
            <b class="mono" style="font-size: 22px">{{ money(result.capital?.gross, { signed: false }) }}</b>
            <div class="sub">
              {{ result.leg_a?.symbol }} {{ money(result.capital?.notional_a, { signed: false }) }}
              <span v-if="!isResidual">
                · {{ result.leg_b?.symbol }} {{ money(result.capital?.notional_b, { signed: false }) }}
              </span>
            </div>
            <div class="sub">{{ result.capital?.note }}</div>
          </div>
          <div>
            <div class="sub">K 线周期</div>
            <b style="font-size: 22px">{{ result.timeframe_label || result.window?.tf_label || tfLabel(form.timeframe) }}</b>
            <div class="sub">{{ result.window?.start }} → {{ result.window?.end }}</div>
            <div class="sub">{{ result.window?.label }}</div>
            <div class="sub">
              {{ result.window?.trade_bars ?? '—' }} 根可交易
              · 热身 {{ result.window?.warmup ?? '—' }} 根
            </div>
          </div>
        </div>
        <p class="sub" style="margin: 10px 0 0">{{ result.hypothesis }}</p>
        <div
          class="jpy-alert"
          :class="result.quality?.tone || 'orange'"
          v-if="result.quality"
          style="margin-top: 12px"
        >
          <b>{{ result.quality.label }}</b>
          · {{ result.quality.detail }}
          <div class="sub" style="margin-top: 6px">{{ result.quality.hint }}</div>
        </div>
        <div
          class="jpy-alert"
          :class="(result.backtest?.total_with_open || 0) >= 0 ? 'agree' : 'red'"
          style="margin-top: 12px"
        >
          {{ result.backtest?.emoji }}
          <b>样本内{{ result.backtest?.call }}</b>
          · 已平仓 {{ money(result.backtest?.total_pnl) }}
          <span v-if="result.backtest?.open_position">
            · 未平浮动 {{ money(result.backtest?.open_pnl) }}
            · 合计 {{ money(result.backtest?.total_with_open) }}
          </span>
          · {{ result.leg_a?.symbol || 'A' }} 投入 {{ money(result.capital?.notional_a || result.notional, { signed: false }) }}
          · 相对本金 {{ result.backtest?.roi_pct != null ? `${result.backtest.roi_pct}%` : '—' }}
          · 本次止损 ±{{ result.thresholds?.stop }}
          <div class="sub" style="margin-top: 6px" v-if="result.backtest?.why">{{ result.backtest.why }}</div>
        </div>
        <div class="jpy-alert orange" v-if="result.macro_gate" style="margin-top: 8px">
          {{ result.macro_gate.label }} · {{ result.macro_gate.detail }}
        </div>
      </section>

      <section class="panel" style="margin-top: 12px" v-if="result.lab && !isResidual">
        <h2 style="margin: 0 0 4px; font-size: 15px">相对价值检验</h2>
        <p class="sub" style="margin: 0 0 10px">{{ result.lab.formula }}。{{ result.lab.goal }}</p>
        <div class="range-row" style="gap: 24px; flex-wrap: wrap">
          <div>
            <div class="sub">收益 β</div>
            <b class="mono">{{ result.lab.beta_returns ?? '—' }}</b>
            <div class="sub">本页配仓 β {{ result.lab.beta_used ?? '—' }}</div>
          </div>
          <div>
            <div class="sub">相关</div>
            <b class="mono">{{ result.lab.corr_all ?? '—' }}</b>
            <div class="sub">近窗 {{ result.lab.corr_window ?? '—' }}</div>
          </div>
          <div>
            <div class="sub">ADF 协整</div>
            <b :class="result.lab.adf?.pass ? 'up' : 'down'">{{ result.lab.adf?.pass ? '通过' : '未通过' }}</b>
            <div class="sub">t {{ result.lab.adf?.t ?? '—' }} / 5% {{ result.lab.adf?.crit_5 ?? '—' }}</div>
          </div>
          <div>
            <div class="sub">半衰期</div>
            <b>{{ result.lab.half_life ?? '—' }} 根</b>
          </div>
          <div>
            <div class="sub">残差漂移年化</div>
            <b :class="tone(result.lab.edge_ann)">{{ result.lab.edge_ann != null ? `${result.lab.edge_ann}%` : '—' }}</b>
            <div class="sub">偏大=有趋势</div>
          </div>
        </div>
        <div class="jpy-alert" :class="result.lab.adf?.pass ? 'yellow' : 'red'" style="margin-top: 12px">
          {{ result.lab.adf?.label || '相关 ≠ 协整 ≠ 可套利' }}
        </div>
        <div class="jpy-alert" :class="result.lab.pseudo_hedge ? 'orange' : 'agree'" style="margin-top: 8px">
          <b>{{ result.lab.pseudo_hedge ? '小心伪对冲' : '按 β 配仓' }}</b>
          · {{ result.lab.pseudo_note }}
        </div>
        <p class="sub" style="margin: 8px 0 0">{{ result.lab.edge_note }}</p>
      </section>

      <section class="panel" style="margin-top: 12px" v-if="!isResidual">
        <h2 style="margin: 0 0 4px; font-size: 15px">建议买卖价</h2>
        <p class="sub" style="margin: 0 0 10px">{{ result.levels?.note }}</p>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>标的</th>
                <th>现价</th>
                <th>做多价差（买 {{ result.leg_a?.symbol }}、卖 {{ result.leg_b?.symbol }}）</th>
                <th>做空价差（卖 {{ result.leg_a?.symbol }}、买 {{ result.leg_b?.symbol }}）</th>
                <th>回归中枢</th>
                <th>当前</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><b>{{ result.leg_a?.symbol }}</b></td>
                <td class="mono">{{ result.leg_a?.price }}</td>
                <td class="mono">买 ≤ {{ result.leg_a?.buy_below ?? '—' }}</td>
                <td class="mono">卖 ≥ {{ result.leg_a?.sell_above ?? '—' }}</td>
                <td class="mono">{{ result.leg_a?.mean ?? '—' }}</td>
                <td>{{ sideHint(result.leg_a?.side, result.leg_a?.buy_below, result.leg_a?.sell_above) }}</td>
              </tr>
              <tr>
                <td><b>{{ result.leg_b?.symbol }}</b></td>
                <td class="mono">{{ result.leg_b?.price }}</td>
                <td class="mono">卖 ≥ {{ result.leg_b?.sell_above ?? '—' }}</td>
                <td class="mono">买 ≤ {{ result.leg_b?.buy_below ?? '—' }}</td>
                <td class="mono">{{ result.leg_b?.mean ?? '—' }}</td>
                <td>{{ sideHint(result.leg_b?.side, result.leg_b?.buy_below, result.leg_b?.sell_above) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="panel" style="margin-top: 12px">
        <h2 style="margin: 0 0 4px; font-size: 15px">样本内回测</h2>
        <p class="sub" style="margin: 0 0 10px">{{ result.backtest?.note }}</p>
        <div class="range-row" style="gap: 24px; flex-wrap: wrap">
          <div>
            <div class="sub">毛利</div>
            <b :class="tone(result.backtest?.total_pnl)">{{ money(result.backtest?.total_pnl) }}</b>
          </div>
          <div>
            <div class="sub">成本后</div>
            <b :class="tone(result.backtest?.total_pnl_net)">{{ money(result.backtest?.total_pnl_net) }}</b>
            <div class="sub">拖累 {{ money(result.backtest?.cost_drag) }} · {{ result.backtest?.cost_bps }}bp</div>
          </div>
          <div>
            <div class="sub">相对投入</div>
            <b :class="tone(result.backtest?.roi_pct)">
              {{ result.backtest?.roi_pct != null ? `${result.backtest.roi_pct}%` : '—' }}
            </b>
          </div>
          <div>
            <div class="sub">最大回撤</div>
            <b :class="tone(result.backtest?.mdd)">{{ money(result.backtest?.mdd) }}</b>
            <div class="sub">{{ result.backtest?.mdd_pct != null ? `${result.backtest.mdd_pct}%` : '—' }}</div>
          </div>
          <div>
            <div class="sub">CVaR 5%</div>
            <b>{{ result.backtest?.cvar_5 != null ? `${result.backtest.cvar_5}%` : '—' }}</b>
          </div>
          <div>
            <div class="sub">样本内盈亏</div>
            <b :class="tone(result.backtest?.holdout?.in_sample?.pnl)">{{ money(result.backtest?.holdout?.in_sample?.pnl) }}</b>
            <div class="sub">{{ result.backtest?.holdout?.in_sample?.trades ?? 0 }} 笔 · 切 {{ result.backtest?.holdout?.split }}</div>
          </div>
          <div>
            <div class="sub">样本外盈亏</div>
            <b :class="tone(result.backtest?.holdout?.out_of_sample?.pnl)">{{ money(result.backtest?.holdout?.out_of_sample?.pnl) }}</b>
            <div class="sub">{{ result.backtest?.holdout?.out_of_sample?.trades ?? 0 }} 笔</div>
          </div>
          <div><div class="sub">交易次数</div><b>{{ result.backtest?.trades ?? '—' }}</b></div>
          <div><div class="sub">胜率</div><b>{{ result.backtest?.win_rate != null ? `${result.backtest.win_rate}%` : '—' }}</b></div>
          <div><div class="sub">平均盈利</div><b :class="tone(result.backtest?.avg_win)">{{ money(result.backtest?.avg_win) }}</b></div>
          <div><div class="sub">平均亏损</div><b :class="tone(result.backtest?.avg_loss)">{{ money(result.backtest?.avg_loss) }}</b></div>
          <div><div class="sub">平均持有</div><b>{{ result.backtest?.avg_bars ?? '—' }} 根</b></div>
          <div><div class="sub">策略夏普</div><b>{{ result.backtest?.sharpe ?? '—' }}</b></div>
        </div>
        <div class="table-wrap" style="margin-top: 12px" v-if="result.backtest?.recent?.length || result.backtest?.open_trade">
          <table>
            <thead>
              <tr>
                <th>方向</th>
                <th>开</th>
                <th>平</th>
                <th>{{ result.leg_a?.symbol || 'A' }} 开/平</th>
                <th v-if="!isResidual">{{ result.leg_b?.symbol || 'B' }} 开/平</th>
                <th>投入</th>
                <th>入 z</th>
                <th>出 z</th>
                <th>盈亏</th>
                <th>原因</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="result.backtest?.open_trade">
                <td>{{ isResidual ? (result.backtest.open_trade.side === 'long_spread' ? '多残差' : '空残差') : (result.backtest.open_trade.side === 'long_spread' ? '多价差' : '空价差') }}</td>
                <td class="mono">{{ result.backtest.open_trade.entry_date }}</td>
                <td class="sub">未平</td>
                <td class="mono">{{ result.backtest.open_trade.a_entry }} → {{ result.backtest.open_trade.a_exit }}</td>
                <td class="mono" v-if="!isResidual">{{ result.backtest.open_trade.b_entry }} → {{ result.backtest.open_trade.b_exit }}</td>
                <td class="sub">
                  <template v-if="isResidual">{{ money(result.backtest.open_trade.a_notional, { signed: false }) }}</template>
                  <template v-else>
                    {{ money(result.backtest.open_trade.a_notional, { signed: false }) }}
                    / {{ result.backtest.open_trade.a_shares }} 股
                    · {{ money(result.backtest.open_trade.b_notional, { signed: false }) }}
                    / {{ result.backtest.open_trade.b_shares }} 股
                  </template>
                </td>
                <td class="mono">{{ result.backtest.open_trade.entry_z }}</td>
                <td class="mono">{{ result.backtest.open_trade.exit_z }}</td>
                <td class="mono" :class="tone(result.backtest.open_trade.pnl)">{{ money(result.backtest.open_trade.pnl) }}</td>
                <td>持仓中</td>
              </tr>
              <tr v-for="(t, i) in recentTrades" :key="i">
                <td>{{ isResidual ? (t.side === 'long_spread' ? '多残差' : '空残差') : (t.side === 'long_spread' ? '多价差' : '空价差') }}</td>
                <td class="mono">{{ t.entry_date }}</td>
                <td class="mono">{{ t.exit_date }}</td>
                <td class="mono">{{ t.a_entry }} → {{ t.a_exit }}</td>
                <td class="mono" v-if="!isResidual">{{ t.b_entry }} → {{ t.b_exit }}</td>
                <td class="sub">
                  <template v-if="isResidual">{{ money(t.a_notional, { signed: false }) }}</template>
                  <template v-else>
                    {{ money(t.a_notional, { signed: false }) }} / {{ t.a_shares }} 股
                    · {{ money(t.b_notional, { signed: false }) }} / {{ t.b_shares }} 股
                  </template>
                </td>
                <td class="mono">{{ t.entry_z }}</td>
                <td class="mono">{{ t.exit_z }}</td>
                <td class="mono" :class="tone(t.pnl)">{{ money(t.pnl) }}</td>
                <td>{{ t.why === 'stop' ? '止损' : '回归' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </template>

  <template v-else-if="screen === 'edit'">
    <div class="range-row">
      <button class="btn" type="button" @click="backToList">返回列表</button>
      <button class="btn btn-primary" type="button" :disabled="saving" @click="save">
        {{ saving ? '保存中…' : '保存' }}
      </button>
      <button class="btn" type="button" :disabled="saving || !editingId" @click="run()">
        {{ loading ? '计算中…' : '计算当前信号' }}
      </button>
    </div>
    <p class="goal-lead">
      写清这一对为什么该回归：同业、股票 vs 行业 ETF、或股票 vs 基准。开仓看 z，配仓看 β。相关高也不等于协整。
    </p>
    <div class="error" v-if="error">{{ error }}</div>
    <p class="ok-msg" v-else-if="ok">{{ ok }}</p>

    <section class="panel">
      <h2 style="margin: 0 0 8px; font-size: 15px">{{ editingId ? '改套利策略' : '新套利策略' }}</h2>
      <div class="macro-chips" style="margin-bottom: 12px">
        <button
          v-for="p in PRESETS"
          :key="p.name"
          class="chip"
          type="button"
          @click="applyPreset(p)"
        >
          {{ p.name }}
        </button>
      </div>
      <div class="form-grid">
        <div class="field">
          <label>策略名</label>
          <input v-model="form.name" placeholder="例如 QQQ/TLT 残差" />
        </div>
        <div class="field">
          <label>对冲方式</label>
          <select v-model="form.kind">
            <option v-for="k in KINDS" :key="k.key" :value="k.key">{{ k.label }}</option>
          </select>
        </div>
        <div class="field">
          <label>周期</label>
          <select v-model="form.timeframe">
            <option v-for="tf in TIMEFRAMES" :key="tf.key" :value="tf.key">{{ tf.label }}</option>
          </select>
        </div>
        <div class="field">
          <label>标的（主仓）</label>
          <input v-model="form.leg_a" placeholder="QQQ" />
        </div>
        <div class="field">
          <label>滚动窗口</label>
          <input v-model.number="form.lookback" type="number" min="20" max="500" />
        </div>
        <div class="field">
          <label>开仓 |z|</label>
          <input v-model.number="form.entry_z" type="number" step="0.1" min="0.5" max="6" />
        </div>
        <div class="field">
          <label>平仓 |z|</label>
          <input v-model.number="form.exit_z" type="number" step="0.1" min="0.1" max="3" />
        </div>
        <div class="field">
          <label>止损 |z|</label>
          <input v-model.number="form.stop_z" type="number" step="0.1" min="1.5" max="12" />
        </div>
        <div class="field">
          <label>投入金额（美元，标的 A）</label>
          <input v-model.number="form.notional" type="number" min="100" step="100" />
        </div>
        <div class="field">
          <label>回测时间</label>
          <select v-model.number="form.bt_days">
            <option v-for="r in btRanges" :key="r.days" :value="r.days">{{ r.label }}</option>
          </select>
        </div>
        <div class="field" v-if="form.kind === 'dollar'">
          <label>固定 β（可选）</label>
          <input v-model="form.beta" type="number" step="0.01" placeholder="默认 1" />
        </div>
        <div class="field full">
          <label>对冲（优先 1 只，最多 2 只）</label>
          <div class="macro-chips" style="margin-bottom: 8px">
            <span v-for="(s, i) in form.factors" :key="s" class="clause-chip">
              {{ s }}
              <button class="chip-x" type="button" @click="removeFactor(i)">×</button>
            </span>
            <span class="sub" v-if="!form.factors.length">还没选对冲。点下面代码，或输入后回车。</span>
          </div>
          <input
            v-model="factorDraft"
            placeholder="输入代码回车，可加多个，例如 SPY QQQ SOXX"
            @keydown="onFactorKey"
            @blur="addFactorsFromText(factorDraft)"
          />
          <div class="macro-chips" style="margin-top: 8px">
            <button
              v-for="s in HEDGE_SUGGEST"
              :key="s"
              class="chip"
              type="button"
              :class="{ now: form.factors.includes(s) }"
              @click="toggleSuggest(s)"
            >
              {{ s }}
            </button>
          </div>
          <p class="sub" style="margin-top: 6px">
            优先两只标的（主仓 + 1 只对冲 ETF）。最多三只。再多手续费太高。
          </p>
        </div>
        <div class="field full">
          <label>备注 / 假设</label>
          <input v-model="form.notes" placeholder="为什么这一对该回归" />
        </div>
        <div class="field full">
          <label>
            <input type="checkbox" v-model="form.macro_filter" />
            宏观过滤：JPY Unwind 或 Macro Risk≥75 时不开新仓
          </label>
        </div>
      </div>
      <p class="sub" style="margin-top: 10px">{{ kindHint }}</p>
    </section>
  </template>

  <template v-else>
    <p class="goal-lead">
      优先两只标的：主仓 vs 一只行业 ETF。最多再加一只大盘，合计三只。再多手续费太高。
      手写策略放到 <span class="mono">backend/app/user_arbs/</span>，复制 <span class="mono">_template.py</span> 后去掉文件名开头的下划线。
    </p>
    <div class="range-row">
      <div class="watch-edit" style="flex: 1; margin: 0">
        <input v-model="search" placeholder="搜索名称或代码" />
      </div>
      <button class="btn btn-primary" type="button" @click="openNew">添加套利策略</button>
    </div>
    <div class="error" v-if="error">{{ error }}</div>

    <section class="panel" style="margin-top: 12px">
      <div class="table-wrap" v-if="filtered.length">
        <table>
          <thead>
            <tr>
              <th>名字</th>
              <th>周期</th>
              <th>标的</th>
              <th>规则</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in filtered" :key="row.id">
              <td>
                <b>{{ row.name }}</b>
                <div class="sub" v-if="row.source === 'file'">手写 · {{ row.file }}{{ row.notes ? ' · ' + row.notes : '' }}</div>
                <div class="sub" v-else-if="row.notes">{{ row.notes }}</div>
                <div class="sub" v-if="row.error" style="color: var(--down, #c44)">{{ row.error }}</div>
              </td>
              <td>{{ row.timeframe_label || tfLabel(row.timeframe) }}</td>
              <td class="mono">
                {{ row.factors?.length ? `${row.leg_a} vs ${row.factors.join('+')}` : `${row.leg_a} / ${row.leg_b}` }}
              </td>
              <td>{{ row.text }}</td>
              <td class="pos-actions">
                <button class="icon-btn" type="button" :disabled="!!row.error" @click="openRun(row)">计算</button>
                <template v-if="row.source !== 'file'">
                  <button class="icon-btn" type="button" @click="openEdit(row)">改</button>
                  <button class="icon-btn" type="button" @click="removePlan(row)">删除</button>
                </template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="empty">
        {{ search.trim() ? '没有匹配的策略。' : '还没有套利策略。点右上角添加，或在 backend/app/user_arbs/ 放手写 .py。' }}
      </div>
    </section>
  </template>
</template>
