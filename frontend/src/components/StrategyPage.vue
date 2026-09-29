<script setup>
import { computed, reactive, ref } from 'vue'
import { api } from '../api'
import { TIMEFRAMES } from '../config/strategies'
import { money } from '../format'

const catalog = ref({ items: [], groups: [], code_template: '' })
const plans = ref([])
const indicatorMap = computed(() => Object.fromEntries((catalog.value.items || []).map((row) => [row.id, row])))

const symbol = ref('TSLA')
const timeframe = ref('1d')
const strategyId = ref('')
const loading = ref(false)
const error = ref('')
const calc = ref(null)

const editingId = ref(null)
const screen = ref('list')
const editKind = ref('formula') // formula | code
const search = ref('')
const catalogSearch = ref('')
const saving = ref(false)
const planError = ref('')
const planOk = ref('')
const listOk = ref('')
const listError = ref('')
const activeGroup = ref(0)
const planForm = reactive({
  name: '',
  notes: '',
  side: 'long',
  timeframe: '1d',
  join: 'and',
  groups: [emptyGroup()],
  targets: [],
  code: '',
})

function emptyGroup() {
  return { join: 'and', clauses: [{ id: 'golden', not: false }] }
}

function emptyFormula() {
  return { join: 'and', groups: [emptyGroup()], targets: [] }
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

function sideLabel(side) {
  return side === 'short' ? '做空' : '做多'
}

function isShort(plan) {
  return (plan?.side || 'long') === 'short'
}

function buyVerb(level) {
  const short = isShort(calc.value?.strategy || selectedPlan.value)
  if (level?.action === 'dip') return short ? '反弹卖' : '回踩买'
  if (level?.action === 'break') return short ? '跌破卖' : '突破买'
  return '参考'
}

function buyLevels(clause) {
  return (clause?.levels || []).filter((level) => level.action === 'break' || level.action === 'dip')
}

function buyGapText(item) {
  if (item?.gap == null) return ''
  const dist = px(Math.abs(item.gap))
  if (Math.abs(item.gap) < 1e-6) return '就在现价附近'
  if (item.gap > 0) return `现价上方 ${dist}`
  return `现价下方 ${dist}`
}

function targetName(id) {
  return (catalog.value.targets || []).find((row) => row.id === id)?.name || id
}

function clauseKind(clause) {
  return clause?.kind || 'indicator'
}

function clauseKey(clause) {
  return `${clauseKind(clause)}:${clause.id}`
}

function clauseName(clause) {
  let name
  if (clauseKind(clause) === 'strategy') {
    name = plans.value.find((row) => String(row.id) === String(clause.id))?.name || `指标${clause.id}`
  } else {
    name = indicatorMap.value[clause.id]?.name || clause.id
  }
  return clause.not ? `非${name}` : name
}

function groupText(group) {
  const parts = (group.clauses || []).map(clauseName)
  const text = parts.join(` ${joinLabel(group.join)} `)
  return parts.length > 1 ? `（${text}）` : text
}

function formulaText(formula) {
  const groups = formula?.groups || []
  if (!groups.length) return '—'
  let text = groups.map(groupText).join(` ${joinLabel(formula.join)} `)
  const targets = (formula?.targets || []).map((row) => targetName(row.id || row))
  if (targets.length) {
    const verb = planForm.side === 'short' ? '最优卖出' : '最优买入'
    text += ` · ${verb} ${targets.join(' / ')}`
  }
  return text
}

function isFilePlan(plan) {
  return plan?.source === 'file' || String(plan?.id || '').startsWith('s_')
}

const previewText = computed(() => formulaText(planForm))
const selectedPlan = computed(() => plans.value.find((row) => String(row.id) === String(strategyId.value)) || null)
const filteredPlans = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return plans.value
  return plans.value.filter((plan) => {
    const blob = `${plan.name || ''} ${plan.notes || ''} ${plan.text || ''} ${plan.file || ''}`.toLowerCase()
    return blob.includes(q)
  })
})
const combinablePlans = computed(() =>
  plans.value.filter((plan) => {
    if (isFilePlan(plan)) return false
    if (plan.kind === 'code' || plan.formula?.kind === 'code') return false
    if (editingId.value && plan.id === editingId.value) return false
    return !planUses(plan, editingId.value)
  }),
)

const filteredCatalogGroups = computed(() => {
  const q = catalogSearch.value.trim().toLowerCase()
  const groups = catalog.value.groups || []
  if (!q) return groups
  return groups
    .map((g) => ({
      ...g,
      items: (g.items || []).filter((row) => {
        const blob = `${row.id || ''} ${row.name || ''} ${row.hint || ''}`.toLowerCase()
        return blob.includes(q)
      }),
    }))
    .filter((g) => g.items.length)
})

function planUses(plan, targetId, seen = new Set()) {
  if (!targetId || !plan) return false
  if (seen.has(plan.id)) return false
  seen.add(plan.id)
  for (const group of plan.formula?.groups || []) {
    for (const clause of group.clauses || []) {
      if (clauseKind(clause) !== 'strategy') continue
      if (String(clause.id) === String(targetId)) return true
      const nested = plans.value.find((row) => String(row.id) === String(clause.id))
      if (nested && planUses(nested, targetId, seen)) return true
    }
  }
  return false
}

const priceCards = computed(() => {
  const p = calc.value?.prices
  if (!p) return []
  return [
    { label: '现价', value: p.last },
    { label: '支撑', value: p.support },
    { label: '压力', value: p.resistance },
    { label: '突破档', value: p.break_level },
    { label: '站稳档', value: p.hold_level },
    { label: 'SMA20', value: p.sma20 },
    { label: 'SMA50', value: p.sma50 },
    { label: 'EMA5', value: p.ema5 },
    { label: 'EMA10', value: p.ema10 },
    { label: 'EMA20', value: p.ema20 },
    { label: 'EMA144', value: p.ema144 },
    { label: 'EMA169', value: p.ema169 },
    { label: 'RSI', value: p.rsi },
    { label: 'MACD', value: p.macd },
    { label: 'ADX', value: p.adx },
    { label: 'ATR', value: p.atr },
    { label: 'KDJ K', value: p.stoch_k },
    { label: 'KDJ D', value: p.stoch_d },
    { label: 'KDJ J', value: p.kdj_j },
    { label: 'VWAP', value: p.vwap },
    { label: 'MFI', value: p.mfi },
    { label: '布林上轨', value: p.bb_upper },
    { label: '布林中轨', value: p.bb_middle },
    { label: '布林下轨', value: p.bb_lower },
    { label: 'Keltner 上', value: p.keltner_upper },
    { label: 'Keltner 下', value: p.keltner_lower },
    { label: '5 日高', value: p.donchian5_high },
    { label: '5 日低', value: p.donchian5_low },
    { label: 'Donchian 上', value: p.donchian_high },
    { label: 'Donchian 下', value: p.donchian_low },
    { label: '20日高', value: p.hi20 },
    { label: '50日高', value: p.hi50 },
    { label: '5日最低', value: p.low5 },
    { label: '10日最低', value: p.low10 },
    { label: '20日最低', value: p.low20 },
    { label: '5日最高', value: p.high5 },
    { label: '10日最高', value: p.high10 },
    { label: '20日最高', value: p.high20 },
    { label: '周线最低', value: p.week_low },
    { label: '月线最低', value: p.month_low },
    { label: '区间低', value: p.range_low },
    { label: '区间高', value: p.range_high },
  ]
})

async function loadCatalog() {
  try {
    catalog.value = await api.strategyIndicators()
  } catch {
    catalog.value = { items: [], groups: [] }
  }
}

async function loadPlans() {
  try {
    plans.value = await api.strategyPlans()
    if (!strategyId.value && plans.value.length) {
      strategyId.value = plans.value[0].id
    }
    if (strategyId.value && !plans.value.some((row) => String(row.id) === String(strategyId.value))) {
      strategyId.value = plans.value[0]?.id || ''
    }
  } catch {
    plans.value = []
  }
}

async function runCalc() {
  const code = symbol.value.trim().toUpperCase()
  if (!code) {
    error.value = '请输入股票代码'
    return
  }
  if (!strategyId.value) {
    error.value = '请先选择一条指标'
    return
  }
  symbol.value = code
  loading.value = true
  error.value = ''
  try {
    calc.value = await api.strategyCalc({
      symbol: code,
      timeframe: timeframe.value,
      strategy_id: strategyId.value,
    })
  } catch (e) {
    calc.value = null
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function pickTimeframe(key) {
  timeframe.value = key
  if (symbol.value.trim() && strategyId.value) runCalc()
}

function onRunStrategyPick() {
  const plan = selectedPlan.value
  if (plan?.timeframe) timeframe.value = plan.timeframe
  calc.value = null
}

function resetPlan() {
  editingId.value = null
  planError.value = ''
  planOk.value = ''
  editKind.value = 'formula'
  const seed = catalog.value.empty_formula || emptyFormula()
  planForm.name = ''
  planForm.notes = ''
  planForm.side = 'long'
  planForm.timeframe = '1d'
  planForm.join = seed.join || 'and'
  planForm.groups = JSON.parse(JSON.stringify(seed.groups || [emptyGroup()]))
  planForm.targets = JSON.parse(JSON.stringify(seed.targets || []))
  planForm.code = catalog.value.code_template || ''
  activeGroup.value = 0
}

function backToList() {
  screen.value = 'list'
  error.value = ''
  calc.value = null
  resetPlan()
}

function openNew(kind = 'formula') {
  listOk.value = ''
  listError.value = ''
  resetPlan()
  editKind.value = kind === 'code' ? 'code' : 'formula'
  if (editKind.value === 'code' && !planForm.code) {
    planForm.code = catalog.value.code_template || ''
  }
  screen.value = 'edit'
}

function openRun(plan) {
  listOk.value = ''
  listError.value = ''
  error.value = ''
  calc.value = null
  strategyId.value = plan.id
  timeframe.value = plan.timeframe || '1d'
  if (plan.symbol) symbol.value = plan.symbol
  screen.value = 'run'
  if (symbol.value.trim()) runCalc()
}

function editPlan(plan) {
  if (isFilePlan(plan)) {
    listError.value = `手写策略请改文件 backend/app/user_stocks/${plan.file || ''}`
    return
  }
  listOk.value = ''
  listError.value = ''
  editingId.value = plan.id
  planError.value = ''
  planOk.value = ''
  planForm.name = plan.name
  planForm.notes = plan.notes || ''
  planForm.side = plan.side || 'long'
  planForm.timeframe = plan.timeframe || '1d'
  if (plan.kind === 'code' || plan.formula?.kind === 'code') {
    editKind.value = 'code'
    planForm.code = plan.formula?.code || catalog.value.code_template || ''
    planForm.join = 'and'
    planForm.groups = [emptyGroup()]
    planForm.targets = []
  } else {
    editKind.value = 'formula'
    planForm.join = plan.formula?.join || 'and'
    planForm.groups = JSON.parse(JSON.stringify(plan.formula?.groups || [emptyGroup()]))
    planForm.targets = JSON.parse(JSON.stringify(plan.formula?.targets || []))
    planForm.code = catalog.value.code_template || ''
  }
  activeGroup.value = 0
  screen.value = 'edit'
}

function focusGroup(index) {
  if (index < 0 || index >= planForm.groups.length) return
  activeGroup.value = index
}

function addClause(group, clause) {
  if (group.clauses.some((row) => clauseKey(row) === clauseKey(clause))) return
  group.clauses.push({
    kind: clauseKind(clause),
    id: clause.id,
    not: false,
  })
}

function addTarget(id) {
  if (planForm.targets.some((row) => (row.id || row) === id)) return
  if (planForm.targets.length >= 2) planForm.targets.shift()
  planForm.targets.push({ id })
}

function removeTarget(index) {
  planForm.targets.splice(index, 1)
}

function addToActiveGroup(id) {
  const group = planForm.groups[activeGroup.value] || planForm.groups[0]
  if (group) addClause(group, { kind: 'indicator', id })
}

function addStrategyToActiveGroup(plan) {
  const group = planForm.groups[activeGroup.value] || planForm.groups[0]
  if (group) addClause(group, { kind: 'strategy', id: plan.id })
}

function removeClause(group, index) {
  group.clauses.splice(index, 1)
}

function addGroup() {
  planForm.groups.push({ join: 'and', clauses: [] })
  activeGroup.value = planForm.groups.length - 1
}

function removeGroup(index) {
  if (planForm.groups.length <= 1) return
  planForm.groups.splice(index, 1)
  if (activeGroup.value >= planForm.groups.length) {
    activeGroup.value = planForm.groups.length - 1
  } else if (activeGroup.value > index) {
    activeGroup.value -= 1
  }
}

function payload() {
  if (editKind.value === 'code') {
    return {
      name: planForm.name.trim(),
      notes: planForm.notes.trim() || null,
      side: planForm.side || 'long',
      timeframe: planForm.timeframe || '1d',
      formula: {
        kind: 'code',
        code: planForm.code || '',
      },
    }
  }
  return {
    name: planForm.name.trim(),
    notes: planForm.notes.trim() || null,
    side: planForm.side || 'long',
    timeframe: planForm.timeframe || '1d',
    formula: {
      join: planForm.join,
      groups: planForm.groups.map((group) => ({
        join: group.join,
        clauses: group.clauses.map((row) => ({
          kind: clauseKind(row),
          id: row.id,
          not: Boolean(row.not),
        })),
      })),
      targets: planForm.targets.map((row) => ({ id: row.id || row })),
    },
  }
}

async function savePlan() {
  planError.value = ''
  planOk.value = ''
  if (!planForm.name.trim()) {
    planError.value = '请填写指标名'
    return
  }
  saving.value = true
  try {
    const name = planForm.name.trim()
    if (editingId.value) {
      await api.updateStrategy(editingId.value, payload())
      await loadPlans()
      screen.value = 'list'
      resetPlan()
      listOk.value = `「${name}」已更新`
    } else {
      const created = await api.createStrategy(payload())
      await loadPlans()
      screen.value = 'list'
      resetPlan()
      listOk.value = `「${created.name}」已保存`
    }
  } catch (e) {
    planError.value = e.message
  } finally {
    saving.value = false
  }
}

async function copyPlan(plan) {
  listError.value = ''
  listOk.value = ''
  try {
    const created = await api.createStrategy({
      name: uniqueCopyName(plan.name),
      notes: plan.notes || null,
      side: plan.side || 'long',
      timeframe: plan.timeframe || '1d',
      formula: plan.formula,
    })
    await loadPlans()
    editPlan(created)
    planOk.value = `已复制为「${created.name}」，改完点更新`
  } catch (e) {
    listError.value = e.message
  }
}

function uniqueCopyName(base) {
  const names = new Set(plans.value.map((row) => row.name))
  const root = String(base || '指标').slice(0, 56)
  let name = `${root} 副本`
  if (!names.has(name)) return name.slice(0, 64)
  let n = 2
  while (names.has(`${root} 副本${n}`)) n += 1
  return `${root} 副本${n}`.slice(0, 64)
}

async function removePlan(plan) {
  if (isFilePlan(plan)) {
    listError.value = `手写策略请直接删 backend/app/user_stocks/${plan.file || ''}`
    return
  }
  if (!confirm(`删除指标「${plan.name}」？`)) return
  listError.value = ''
  listOk.value = ''
  try {
    await api.deleteStrategy(plan.id)
    if (editingId.value === plan.id || String(strategyId.value) === String(plan.id)) backToList()
    await loadPlans()
    listOk.value = `「${plan.name}」已删除`
  } catch (e) {
    listError.value = e.message
  }
}

loadCatalog()
loadPlans()
</script>

<template>
  <template v-if="screen === 'run'">
    <div class="range-row">
      <button class="btn" type="button" @click="backToList">返回列表</button>
    </div>
    <p class="goal-lead">
      输入股票代码。做多会给出买入价，做空会给出卖出价。
    </p>

    <div class="range-row">
      <div class="watch-edit" style="flex: 1; margin: 0">
        <input
          v-model="symbol"
          placeholder="TSLA / NVDA / SKHYUSDT"
          @keydown.enter.prevent="runCalc"
        />
        <select v-model="strategyId" class="strategy-select" @change="onRunStrategyPick">
          <option value="" disabled>选择指标</option>
          <option v-for="plan in plans" :key="plan.id" :value="plan.id">
            {{ plan.name }} · {{ sideLabel(plan.side) }}
          </option>
        </select>
        <button class="btn btn-primary" type="button" :disabled="loading" @click="runCalc">
          {{ loading ? '计算中…' : '计算' }}
        </button>
      </div>
      <div class="pills">
        <button
          v-for="tf in TIMEFRAMES"
          :key="tf.key"
          class="pill"
          :class="{ active: timeframe === tf.key }"
          type="button"
          @click="pickTimeframe(tf.key)"
        >
          {{ tf.label }}
        </button>
      </div>
    </div>

    <p class="sub" v-if="selectedPlan">
      {{ sideLabel(selectedPlan.side) }} · {{ tfLabel(selectedPlan.timeframe) }} · 公式：{{ selectedPlan.text }}
    </p>
    <div class="error" v-if="error">{{ error }}</div>
    <p class="sub" v-else-if="loading">正在计算…</p>
    <p class="sub" v-else-if="!plans.length">还没有指标。返回列表先添加一条。</p>
    <p class="sub" v-else-if="!calc">输入代码后点计算。</p>

    <template v-if="calc">
      <div class="match-row">
        <span class="match-badge" :class="isShort(calc.strategy || selectedPlan) ? 'down-dim' : 'up-dim'">
          {{ sideLabel((calc.strategy || selectedPlan)?.side) }}
        </span>
        <span class="match-badge" :class="calc.match ? 'up-dim' : 'down-dim'">
          {{ calc.match ? '命中' : '未命中' }}
        </span>
        <div>
          <b>{{ calc.strategy?.name || selectedPlan?.name }}</b>
          <div class="sub">
            {{ calc.symbol }} · {{ calc.source === 'binance' ? '币安' : 'TradingView' }} ·
            {{ TIMEFRAMES.find((t) => t.key === calc.timeframe)?.label || calc.timeframe }} ·
            {{ calc.bars }} 根 K 线
          </div>
          <div class="sub">{{ calc.explain?.text || selectedPlan?.text }}</div>
        </div>
      </div>

      <section class="panel" style="margin: 12px 0" v-if="calc.buys">
        <h2>{{ isShort(calc.strategy || selectedPlan) ? '建议卖出' : '建议买入' }}</h2>
        <p class="sub">{{ calc.buys.summary }}</p>
        <div class="buy-grid" v-if="calc.buys.items?.length">
          <div
            v-for="(item, idx) in calc.buys.items"
            :key="idx"
            class="buy-card"
            :class="item.action"
          >
            <span>{{ item.role }}</span>
            <b>{{ px(item.price) }}</b>
            <div class="sub">{{ item.why }}</div>
            <div class="sub" v-if="buyGapText(item)">{{ buyGapText(item) }}</div>
          </div>
        </div>
      </section>

      <section class="panel" style="margin: 12px 0">
        <h2>价格</h2>
        <div class="price-grid">
          <div v-for="card in priceCards" :key="card.label" class="price-card">
            <span>{{ card.label }}</span>
            <b>{{ px(card.value) }}</b>
          </div>
        </div>
        <div class="sub" style="margin-top: 10px" v-if="calc.prices?.recent_support?.length || calc.prices?.recent_resistance?.length">
          最近支撑 {{ (calc.prices.recent_support || []).map(px).join(' · ') || '—' }}
          · 最近压力 {{ (calc.prices.recent_resistance || []).map(px).join(' · ') || '—' }}
        </div>
      </section>

      <section class="panel" v-if="calc.explain">
        <h2>条件</h2>
        <div class="formula-groups">
          <div
            v-for="(group, gi) in calc.explain.groups"
            :key="gi"
            class="formula-group"
            :class="{ hit: group.hit, miss: !group.hit }"
          >
            <div class="formula-head">
              第 {{ gi + 1 }} 组 · {{ joinLabel(group.join) }} ·
              <b :class="group.hit ? 'hit-tag' : 'miss-tag'">{{ group.hit ? '成立' : '不成立' }}</b>
            </div>
            <div class="cond-list">
              <div
                v-for="clause in group.clauses"
                :key="clauseKey(clause) + String(clause.not)"
                class="cond-card"
                :class="clause.hit ? 'hit' : 'miss'"
              >
                <span class="cond-status">{{ clause.hit ? '成立' : '不成立' }}</span>
                <div>
                  <div class="cond-name">{{ clause.name }}</div>
                  <div class="cond-prices" v-if="buyLevels(clause).length">
                    <span v-for="(level, li) in buyLevels(clause)" :key="li" class="cond-px" :class="level.action">
                      {{ buyVerb(level) }} {{ level.label }}
                      <b>{{ px(level.price) }}</b>
                      <template v-if="level.action === 'break' && level.gap != null">
                        · 还差 {{ px(Math.abs(level.gap)) }}
                      </template>
                      <template v-else-if="level.action === 'dip' && level.gap != null">
                        · 近 {{ px(Math.abs(level.gap)) }}
                      </template>
                    </span>
                  </div>
                  <div class="cond-note" v-else-if="clause.note">{{ clause.note }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>
        <p class="sub" v-if="calc.explain.groups.length > 1">
          组与组之间用「{{ joinLabel(calc.explain.join) }}」连接。
        </p>
      </section>
    </template>
  </template>

  <template v-else-if="screen === 'edit'">
      <div class="range-row">
        <button class="btn" type="button" @click="backToList">返回列表</button>
      </div>
      <p class="goal-lead" v-if="editKind === 'code'">
        自己写 <span class="mono">compute(bars)</span>：入参是 K 线，返回值必须是
        <span class="mono">True</span> 或 <span class="mono">False</span>
        （也可返回与 bars 等长的 bool 列表，供指标验证逐根统计）。指标只属于当前登录用户。
      </p>
      <p class="goal-lead" v-else>
        先点左边某一组，再点右侧条件加进去。先选做多还是做空。最优价格加到「最佳买/卖点」，不算进命中。
      </p>

      <div class="grid-2" style="align-items: start" v-if="editKind === 'formula'">
        <section class="panel">
          <div class="formula-head" style="margin-bottom: 10px">
            <h2 style="margin: 0">{{ editingId ? '改指标' : '新指标（条件组合）' }}</h2>
          </div>
          <div class="form-grid" style="margin-top: 10px">
            <div class="field">
              <label>指标名</label>
              <input v-model="planForm.name" placeholder="例如 超跌金叉" />
            </div>
            <div class="field">
              <label>方向</label>
              <select v-model="planForm.side">
                <option value="long">做多</option>
                <option value="short">做空</option>
              </select>
            </div>
            <div class="field">
              <label>K线周期</label>
              <select v-model="planForm.timeframe">
                <option v-for="tf in TIMEFRAMES" :key="tf.key" :value="tf.key">{{ tf.label }}</option>
              </select>
            </div>
            <div class="field">
              <label>组间连接</label>
              <select v-model="planForm.join">
                <option value="and">且（每组都要成立）</option>
                <option value="or">或（任意一组成立）</option>
              </select>
            </div>
            <div class="field full">
              <label>备注</label>
              <input v-model="planForm.notes" placeholder="可选" />
            </div>
          </div>
          <p class="sub" style="margin-top: 10px">预览：{{ previewText }}</p>

          <div
            v-for="(group, gi) in planForm.groups"
            :key="gi"
            class="formula-group formula-group-edit"
            :class="{ active: activeGroup === gi }"
            style="margin-top: 12px"
            @click="focusGroup(gi)"
          >
            <div class="formula-head">
              <span>第 {{ gi + 1 }} 组</span>
              <span class="focus-tag" v-if="activeGroup === gi">当前</span>
              <select v-model="group.join" @click.stop>
                <option value="and">组内且</option>
                <option value="or">组内或</option>
              </select>
              <button class="icon-btn" type="button" :disabled="planForm.groups.length <= 1" @click.stop="removeGroup(gi)">
                删组
              </button>
            </div>
            <div class="clause-list">
              <span
                v-for="(clause, ci) in group.clauses"
                :key="ci"
                class="clause-chip"
                :class="{ 'strategy-chip': clauseKind(clause) === 'strategy' }"
                @click.stop
              >
                {{ clauseName(clause) }}
                <button class="chip-x" type="button" @click="clause.not = !clause.not">{{ clause.not ? '取消非' : '非' }}</button>
                <button class="chip-x" type="button" @click="removeClause(group, ci)">×</button>
              </span>
              <span class="sub" v-if="!group.clauses.length">点右侧指标或已保存组合加进这一组</span>
            </div>
          </div>

          <div class="formula-group target-box" style="margin-top: 12px">
            <div class="formula-head">
              <span>{{ planForm.side === 'short' ? '最佳卖出点' : '最佳买入点' }}</span>
              <span class="focus-tag">最优</span>
            </div>
            <p class="sub">点右侧「{{ planForm.side === 'short' ? '最优价格卖出' : '最优价格买入' }}」，最多 2 个。不参与命中判断。</p>
            <div class="clause-list" style="margin-top: 8px">
              <span
                v-for="(item, ti) in planForm.targets"
                :key="item.id || ti"
                class="clause-chip target-chip"
              >
                {{ targetName(item.id || item) }}
                <button class="chip-x" type="button" @click="removeTarget(ti)">×</button>
              </span>
              <span class="sub" v-if="!planForm.targets.length">还没选最优价格</span>
            </div>
          </div>
          <div class="modal-actions" style="margin-top: 12px; justify-content: flex-start">
            <button class="btn" type="button" @click="addGroup">加一组</button>
            <button class="btn btn-primary" type="button" :disabled="saving" @click="savePlan">
              {{ saving ? '保存中…' : editingId ? '更新' : '保存' }}
            </button>
          </div>
          <div class="error" v-if="planError">{{ planError }}</div>
          <div class="ok-msg" v-else-if="planOk">{{ planOk }}</div>
        </section>

        <section class="panel ind-panel">
          <h2>条件</h2>
          <p class="sub">点条件加到第 {{ activeGroup + 1 }} 组。最优价格加到左边最佳买/卖点。</p>
          <div class="ind-block">
            <div class="ind-label">{{ planForm.side === 'short' ? '最优价格卖出' : '最优价格买入' }}</div>
            <div class="clause-list">
              <button
                v-for="item in catalog.targets || []"
                :key="item.id"
                class="clause-chip btn-chip target-chip"
                type="button"
                :title="item.hint"
                @click="addTarget(item.id)"
              >
                {{ item.name }}
              </button>
            </div>
          </div>
          <div v-for="block in catalog.groups" :key="block.name" class="ind-block">
            <div class="ind-label">{{ block.name }}</div>
            <div class="clause-list">
              <button
                v-for="item in block.items"
                :key="item.id"
                class="clause-chip btn-chip"
                type="button"
                :title="item.hint"
                @click="addToActiveGroup(item.id)"
              >
                {{ item.name }}
              </button>
            </div>
          </div>
          <div class="ind-block">
            <div class="ind-label">已保存的指标</div>
            <div class="clause-list" v-if="combinablePlans.length">
              <button
                v-for="plan in combinablePlans"
                :key="plan.id"
                class="clause-chip btn-chip strategy-chip"
                type="button"
                :title="plan.text"
                @click="addStrategyToActiveGroup(plan)"
              >
                {{ plan.name }} · {{ sideLabel(plan.side) }}
              </button>
            </div>
            <p class="sub" v-else>先保存指标 1、指标 2，再在这里把它们组进指标 3。</p>
          </div>
        </section>
      </div>

      <section class="panel" v-else>
        <h2 style="margin-top: 0">{{ editingId ? '改代码指标' : '新代码指标' }}</h2>
        <div class="form-grid" style="margin-top: 10px">
          <div class="field">
            <label>指标名</label>
            <input v-model="planForm.name" placeholder="例如 Heikin-Ashi" />
          </div>
          <div class="field">
            <label>方向</label>
            <select v-model="planForm.side">
              <option value="long">做多</option>
              <option value="short">做空</option>
            </select>
          </div>
          <div class="field">
            <label>K线周期</label>
            <select v-model="planForm.timeframe">
              <option v-for="tf in TIMEFRAMES" :key="tf.key" :value="tf.key">{{ tf.label }}</option>
            </select>
          </div>
          <div class="field full">
            <label>备注</label>
            <input v-model="planForm.notes" placeholder="可选" />
          </div>
          <div class="field full">
            <label>compute(bars) → True / False</label>
            <textarea
              v-model="planForm.code"
              class="plan-box"
              style="min-height: 320px; font-family: var(--mono); font-size: 12px"
              spellcheck="false"
            />
          </div>
        </div>
        <div class="modal-actions" style="margin-top: 12px; justify-content: flex-start">
          <button class="btn btn-primary" type="button" :disabled="saving" @click="savePlan">
            {{ saving ? '保存中…' : editingId ? '更新' : '保存' }}
          </button>
        </div>
        <div class="error" v-if="planError">{{ planError }}</div>
        <div class="ok-msg" v-else-if="planOk">{{ planOk }}</div>
      </section>
    </template>

    <template v-else>
      <p class="goal-lead">
        上面是内置条件指标（只读，做条件组合时用）；下面是<strong>你自己的指标</strong>（按登录用户隔离）。
        可写代码函数返回 True/False，也可拼条件组合。统计命中请到「指标 · 指标验证」。
      </p>
      <div class="range-row">
        <div class="watch-edit" style="flex: 1; margin: 0">
          <input v-model="catalogSearch" placeholder="搜索内置条件…" />
        </div>
      </div>

      <section class="panel" style="margin-top: 12px">
        <h2 style="margin-top: 0">内置条件指标</h2>
        <p class="sub" style="margin-top: 0">共 {{ (catalog.items || []).length }} 个，做条件组合时可勾选。</p>
        <div v-for="block in filteredCatalogGroups" :key="block.name" class="ind-block" style="margin-top: 12px">
          <div class="ind-label">{{ block.name }}</div>
          <div class="clause-list">
            <span
              v-for="item in block.items"
              :key="item.id"
              class="clause-chip"
              :title="item.hint"
            >
              {{ item.name }}
            </span>
          </div>
        </div>
        <p class="sub" v-if="!filteredCatalogGroups.length">没有匹配的内置指标。</p>
      </section>

      <div class="range-row" style="margin-top: 16px">
        <div class="watch-edit" style="flex: 1; margin: 0">
          <input v-model="search" placeholder="搜索我的指标…" />
        </div>
        <button class="btn btn-primary" type="button" @click="openNew('code')">新增代码指标</button>
        <button class="btn" type="button" @click="openNew('formula')">新增条件组合</button>
      </div>
      <div class="error" v-if="listError">{{ listError }}</div>
      <p class="ok-msg" v-else-if="listOk">{{ listOk }}</p>

      <section class="panel" style="margin-top: 12px">
        <h2 style="margin-top: 0">我的指标</h2>
        <div class="table-wrap" v-if="filteredPlans.length">
          <table>
            <thead>
              <tr>
                <th>名字</th>
                <th>类型</th>
                <th>方向</th>
                <th>周期</th>
                <th>说明</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="plan in filteredPlans" :key="plan.id">
                <td>
                  <b>{{ plan.name }}</b>
                  <div class="sub" v-if="plan.source === 'file'">手写 · {{ plan.file }}{{ plan.symbol ? ' · ' + plan.symbol : '' }}</div>
                  <div class="sub" v-else-if="plan.notes">{{ plan.notes }}</div>
                  <div class="sub" v-if="plan.error" style="color: var(--down, #c44)">{{ plan.error }}</div>
                </td>
                <td>{{ plan.kind === 'code' || plan.formula?.kind === 'code' ? '代码' : (plan.source === 'file' ? '文件' : '条件') }}</td>
                <td>
                  <span class="match-badge" :class="plan.side === 'short' ? 'down-dim' : 'up-dim'">
                    {{ sideLabel(plan.side) }}
                  </span>
                </td>
                <td>{{ tfLabel(plan.timeframe) }}</td>
                <td>{{ plan.text }}</td>
                <td class="pos-actions">
                  <button
                    class="icon-btn"
                    type="button"
                    :disabled="!!plan.error"
                    @click="openRun(plan)"
                  >
                    试算
                  </button>
                  <template v-if="plan.source !== 'file'">
                    <button class="icon-btn" type="button" @click="editPlan(plan)">改</button>
                    <button class="icon-btn" type="button" @click="copyPlan(plan)">复制</button>
                    <button class="icon-btn" type="button" @click="removePlan(plan)">删除</button>
                  </template>
                  <button v-else class="icon-btn" type="button" @click="copyPlan(plan)">复制到页面</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="empty">
          {{ search.trim() ? '没有叫这个名字的指标。' : '还没有自己的指标。点「新增代码指标」写 compute 返回 True/False。' }}
        </div>
      </section>
    </template>
</template>
