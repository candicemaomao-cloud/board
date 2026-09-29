<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { money } from '../format'

const mode = ref('symbols') // symbols | portfolio
const symbolsText = ref('AAPL')
const horizonDays = ref(21)
const capital = ref('10000')
const windowDays = ref(252)
const lossLimit = ref(0.05)
const hedgeOn = ref(true)
const hedgeBench = ref('SPY')
const loading = ref(false)
const error = ref('')
const data = ref(null)
const focusSym = ref(null)
const compareSide = ref('before') // before | after

const shocks = ref([])
const riskList = ref([])
const importId = ref('')

const legs = ref([
  { symbol: 'AAPL', weight: '60', side: 'long' },
  { symbol: 'SPY', weight: '40', side: 'short' },
])

const HOLD_OPTS = [
  { value: 10, label: '10 天' },
  { value: 21, label: '21 天' },
  { value: 30, label: '30 天' },
  { value: 42, label: '42 天' },
  { value: 63, label: '63 天' },
]
const WINDOW_OPTS = [
  { value: 63, label: '近 3 月' },
  { value: 126, label: '近 6 月' },
  { value: 252, label: '近 1 年' },
]
const LOSS_OPTS = [
  { value: 0.03, label: '亏损底线 3%' },
  { value: 0.05, label: '亏损底线 5%' },
  { value: 0.08, label: '亏损底线 8%' },
  { value: 0.10, label: '亏损底线 10%' },
  { value: 0.15, label: '亏损底线 15%' },
]

const FAMILY_LABEL = {
  base: '基准',
  market: '市场方向',
  crisis: '极端危机（全天候）',
}

function parseSymbols(raw) {
  return String(raw || '')
    .split(/[\s,，;；\n]+/)
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean)
}

function pct01(n, digits = 1) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  return `${(Number(n) * 100).toFixed(digits)}%`
}

function statusClass(s) {
  if (s === '覆盖') return 'up'
  if (s === '脆弱') return 'warn'
  return 'down'
}

function verdictClass(v) {
  if (v === '维数较满') return 'up'
  if (v === '维数不足') return 'warn'
  return 'down'
}

function addLeg() {
  legs.value.push({ symbol: '', weight: '', side: 'long' })
}

function removeLeg(i) {
  if (legs.value.length <= 1) return
  legs.value.splice(i, 1)
}

function collectLegs() {
  const out = []
  for (const row of legs.value) {
    const symbol = String(row.symbol || '').trim().toUpperCase()
    if (!symbol) continue
    const w = String(row.weight || '').trim()
    out.push({
      symbol,
      side: row.side === 'short' ? 'short' : 'long',
      weight: w === '' ? null : Number(w),
    })
  }
  if (!out.length) throw new Error('请至少填一条组合腿')
  return out
}

async function loadRiskList() {
  try {
    const res = await api.riskPortfolios()
    riskList.value = res.items || []
  } catch {
    riskList.value = []
  }
}

function importRiskPortfolio() {
  const row = riskList.value.find((x) => String(x.id) === String(importId.value))
  if (!row?.legs?.length) {
    error.value = '请选择有效风险组合'
    return
  }
  mode.value = 'portfolio'
  legs.value = row.legs.map((l) => ({
    symbol: l.symbol,
    weight: l.weight != null ? String(Math.abs(Number(l.weight) * (Math.abs(Number(l.weight)) <= 1 ? 100 : 1))) : '',
    side: l.side === 'short' ? 'short' : 'long',
  }))
  if (row.budget_capital) capital.value = String(row.budget_capital)
  if (row.loss_limit) lossLimit.value = Number(row.loss_limit)
  if (row.horizon_days) horizonDays.value = Number(row.horizon_days)
  if (row.window) windowDays.value = Number(row.window)
  error.value = ''
}

const shockGroups = computed(() => {
  const order = ['base', 'market', 'crisis']
  const map = {}
  for (const s of shocks.value) {
    const f = s.family || 'market'
    if (!map[f]) map[f] = []
    map[f].push(s)
  }
  return order.filter((k) => map[k]?.length).map((k) => ({
    key: k,
    label: FAMILY_LABEL[k] || k,
    items: map[k],
  }))
})

const focused = computed(() => {
  const items = data.value?.items || []
  return items.find((x) => x.symbol === focusSym.value) || items[0] || null
})

const bundle = computed(() => {
  if (data.value?.mode !== 'portfolio') return focused.value
  return compareSide.value === 'after' && data.value?.after ? data.value.after : data.value?.before
})

const capitalNeed = computed(() => bundle.value?.capital_need || data.value?.gate || null)

function enableFamily(family, on) {
  for (const s of shocks.value) {
    if ((s.family || 'market') === family) s.enabled = on
  }
}

function shockPayload() {
  return shocks.value.map((s) => ({
    id: s.id,
    enabled: !!s.enabled,
    value: Number(s.value),
    vol_mult: s.showVol ? Number(s.vol_mult) : undefined,
    gap: s.showCrisis && s.gap !== '' ? Number(s.gap) : undefined,
    left_tail: s.showCrisis && s.left_tail !== '' ? Number(s.left_tail) : undefined,
  }))
}

async function loadDirections() {
  try {
    const res = await api.kakeyaDirections()
    shocks.value = (res.items || []).map((d) => ({
      id: d.id,
      label: d.label,
      kind: d.kind,
      family: d.family || 'market',
      unit: d.unit,
      description: d.description,
      enabled: !!d.enabled_default,
      value: d.default,
      vol_mult: d.default_vol != null ? d.default_vol : 1.8,
      gap: d.default_gap != null ? d.default_gap : '',
      left_tail: d.default_left_tail != null ? d.default_left_tail : '',
      showCrisis: d.kind === 'crisis_bundle',
      showVol: d.kind === 'combo_bear_vol' || d.kind === 'crisis_bundle',
    }))
  } catch (e) {
    error.value = e.message
  }
}

async function run() {
  error.value = ''
  const cap = Number(capital.value)
  if (!(cap > 0)) {
    error.value = '请填写本金'
    return
  }
  loading.value = true
  data.value = null
  try {
    const base = {
      horizon_days: Number(horizonDays.value) || 21,
      capital: cap,
      window: Number(windowDays.value) || 252,
      n_sims: 1200,
      loss_limit: Number(lossLimit.value) || 0.05,
      shocks: shockPayload(),
    }
    if (mode.value === 'portfolio') {
      data.value = await api.runKakeyaModel({
        ...base,
        legs: collectLegs(),
        hedge: !!hedgeOn.value,
        hedge_benchmark: String(hedgeBench.value || 'SPY').toUpperCase(),
      })
      compareSide.value = 'before'
    } else {
      const symbols = parseSymbols(symbolsText.value)
      if (!symbols.length) {
        error.value = '请填写股票代码'
        loading.value = false
        return
      }
      data.value = await api.runKakeyaModel({ ...base, symbols })
      if (data.value?.items?.length) focusSym.value = data.value.items[0].symbol
    }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadDirections()
  loadRiskList()
})
</script>

<template>
  <p class="goal-lead">
    「向所有方向旋转」→ 全天候压力管 + 危机资本充实度。
    组合模式可导入风险组合腿，并对比<strong>对冲前 / 去 β 后</strong>维数与可投本金。
  </p>

  <section class="panel" style="margin-top: 12px">
    <div class="range-row" style="margin-bottom: 10px; flex-wrap: wrap; gap: 8px">
      <select v-model="mode" class="strategy-select">
        <option value="symbols">单票扫描</option>
        <option value="portfolio">组合腿 + 对冲对比</option>
      </select>
      <template v-if="mode === 'symbols'">
        <input
          v-model="symbolsText"
          class="strategy-select"
          style="width: 220px"
          placeholder="代码，多个逗号分隔"
        />
      </template>
      <select v-model.number="horizonDays" class="strategy-select">
        <option v-for="opt in HOLD_OPTS" :key="opt.value" :value="opt.value">持仓 {{ opt.label }}</option>
      </select>
      <input v-model="capital" class="strategy-select" style="width: 110px" placeholder="本金$" />
      <select v-model.number="windowDays" class="strategy-select">
        <option v-for="opt in WINDOW_OPTS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
      </select>
      <select v-model.number="lossLimit" class="strategy-select">
        <option v-for="opt in LOSS_OPTS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
      </select>
      <button class="btn btn-primary" type="button" :disabled="loading" @click="run">
        {{ loading ? '扫描全方向…' : '跑挂谷覆盖' }}
      </button>
    </div>

    <div v-if="mode === 'portfolio'" style="margin-bottom: 10px">
      <div class="range-row" style="gap: 8px; flex-wrap: wrap; margin-bottom: 8px">
        <select v-model="importId" class="strategy-select" style="min-width: 180px">
          <option value="">从风险组合导入…</option>
          <option v-for="r in riskList" :key="r.id" :value="r.id">{{ r.name }}</option>
        </select>
        <button class="btn" type="button" @click="importRiskPortfolio">导入</button>
        <label class="sub" style="display: flex; align-items: center; gap: 6px">
          <input v-model="hedgeOn" type="checkbox" />
          对冲前后对比
        </label>
        <input
          v-model="hedgeBench"
          class="strategy-select"
          style="width: 80px"
          placeholder="基准"
          :disabled="!hedgeOn"
        />
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>代码</th>
              <th>权重</th>
              <th>方向</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(leg, i) in legs" :key="i">
              <td>
                <input v-model="leg.symbol" class="strategy-select" style="width: 100px" placeholder="代码" />
              </td>
              <td>
                <input v-model="leg.weight" class="strategy-select" style="width: 80px" placeholder="权重" />
              </td>
              <td>
                <select v-model="leg.side" class="strategy-select">
                  <option value="long">做多</option>
                  <option value="short">做空</option>
                </select>
              </td>
              <td>
                <button class="btn" type="button" @click="removeLeg(i)">删</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <button class="btn" type="button" style="margin-top: 6px" @click="addLeg">加腿</button>
    </div>

    <div class="error" v-if="error">{{ error }}</div>
  </section>

  <section class="panel" style="margin-top: 12px">
    <h2 style="margin: 0 0 8px; font-size: 15px">方向冲击配置</h2>
    <div class="range-row" style="gap: 8px; margin-bottom: 8px; flex-wrap: wrap">
      <button class="btn" type="button" @click="enableFamily('crisis', true)">全开极端危机</button>
      <button class="btn" type="button" @click="enableFamily('crisis', false)">关闭极端危机</button>
      <button class="btn" type="button" @click="enableFamily('market', true)">全开市场方向</button>
    </div>

    <div v-for="g in shockGroups" :key="g.key" style="margin-top: 12px">
      <h3 style="margin: 0 0 6px; font-size: 13px; color: var(--muted)">{{ g.label }}</h3>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>启用</th>
              <th>方向</th>
              <th>主冲击</th>
              <th>σ倍数</th>
              <th>缺口</th>
              <th>左尾×</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in g.items" :key="s.id">
              <td><input v-model="s.enabled" type="checkbox" /></td>
              <td>{{ s.label }}</td>
              <td>
                <input
                  v-model="s.value"
                  class="strategy-select"
                  style="width: 88px"
                  type="number"
                  step="0.01"
                  :disabled="!s.enabled"
                />
                <div class="sub">{{ s.unit }}</div>
              </td>
              <td>
                <input
                  v-if="s.showVol"
                  v-model="s.vol_mult"
                  class="strategy-select"
                  style="width: 70px"
                  type="number"
                  step="0.1"
                  :disabled="!s.enabled"
                />
                <span v-else class="sub">—</span>
              </td>
              <td>
                <input
                  v-if="s.showCrisis"
                  v-model="s.gap"
                  class="strategy-select"
                  style="width: 70px"
                  type="number"
                  step="0.01"
                  :disabled="!s.enabled"
                />
                <span v-else class="sub">—</span>
              </td>
              <td>
                <input
                  v-if="s.showCrisis"
                  v-model="s.left_tail"
                  class="strategy-select"
                  style="width: 70px"
                  type="number"
                  step="0.1"
                  :disabled="!s.enabled"
                />
                <span v-else class="sub">—</span>
              </td>
              <td class="sub" style="max-width: 280px">{{ s.description }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>

  <!-- 组合模式结果 -->
  <section class="panel" style="margin-top: 12px" v-if="data?.mode === 'portfolio'">
    <p class="sub">{{ data.definition }}</p>
    <div class="sub" v-if="data.hedge?.beta != null" style="margin-top: 6px">
      对冲基准 {{ data.hedge.benchmark }} · β={{ data.hedge.beta }} · {{ data.hedge.note }}
    </div>
    <div class="sub" v-if="data.hedge?.error" style="margin-top: 6px">{{ data.hedge.error }}</div>

    <div class="table-wrap" style="margin-top: 10px" v-if="data.legs?.length">
      <table>
        <thead>
          <tr>
            <th>腿</th>
            <th>方向</th>
            <th>权重</th>
            <th>现价</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="l in data.legs" :key="l.symbol + l.side">
            <td>{{ l.symbol }}</td>
            <td>{{ l.side === 'short' ? '做空' : '做多' }}</td>
            <td class="mono">{{ pct01(l.weight_abs) }}</td>
            <td class="mono">{{ money(l.spot_price, { signed: false }) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="range-row" style="gap: 8px; margin: 12px 0; flex-wrap: wrap" v-if="data.after">
      <button
        class="pill"
        type="button"
        :class="{ active: compareSide === 'before' }"
        @click="compareSide = 'before'"
      >
        对冲前 · {{ data.before?.verdict }}
      </button>
      <button
        class="pill"
        type="button"
        :class="{ active: compareSide === 'after' }"
        @click="compareSide = 'after'"
      >
        对冲后 · {{ data.after?.verdict }}
      </button>
    </div>

    <div v-if="data.compare" class="range-row" style="gap: 16px; flex-wrap: wrap; margin-bottom: 10px">
      <div>
        <div class="sub">维数 Δ</div>
        <b class="mono" :class="data.compare.dim_delta >= 0 ? 'up' : 'down'">
          {{ data.compare.dim_delta >= 0 ? '+' : '' }}{{ pct01(data.compare.dim_delta) }}
        </b>
      </div>
      <div v-if="data.compare.crisis_delta != null">
        <div class="sub">危机评分 Δ</div>
        <b class="mono" :class="data.compare.crisis_delta >= 0 ? 'up' : 'down'">
          {{ data.compare.crisis_delta >= 0 ? '+' : '' }}{{ pct01(data.compare.crisis_delta) }}
        </b>
      </div>
      <div>
        <div class="sub">可投比例 Δ</div>
        <b class="mono" :class="data.compare.investable_delta >= 0 ? 'up' : 'down'">
          {{ data.compare.investable_delta >= 0 ? '+' : '' }}{{ pct01(data.compare.investable_delta) }}
        </b>
      </div>
      <div class="sub" style="max-width: 360px">{{ data.compare.note }}</div>
    </div>
  </section>

  <!-- 单票 tabs -->
  <section class="panel" style="margin-top: 12px" v-if="data?.mode === 'symbols' && data?.items?.length">
    <p class="sub">{{ data.definition }}</p>
    <div class="range-row" style="gap: 8px; margin: 10px 0; flex-wrap: wrap">
      <button
        v-for="it in data.items"
        :key="it.symbol"
        class="pill"
        type="button"
        :class="{ active: focused?.symbol === it.symbol }"
        @click="focusSym = it.symbol"
      >
        {{ it.symbol }} · {{ it.verdict }}
      </button>
    </div>
  </section>

  <!-- 共用：评分 + 资本 + 方向表 -->
  <section class="panel" style="margin-top: 12px" v-if="bundle">
    <div class="range-row" style="gap: 20px; flex-wrap: wrap; margin-bottom: 10px">
      <div>
        <div class="sub">维数评分</div>
        <b class="mono" style="font-size: 22px" :class="verdictClass(bundle.verdict)">
          {{ pct01(bundle.dim_score) }}
        </b>
        <div class="sub">{{ bundle.covered_count }}/{{ bundle.direction_count }} 方向覆盖</div>
      </div>
      <div v-if="bundle.crisis_score != null">
        <div class="sub">危机方向评分</div>
        <b class="mono" style="font-size: 22px">{{ pct01(bundle.crisis_score) }}</b>
        <div class="sub">{{ bundle.crisis_covered }}/{{ bundle.crisis_count }} 危机管覆盖</div>
      </div>
      <div>
        <div class="sub">结论</div>
        <b :class="verdictClass(bundle.verdict)">{{ bundle.verdict }}</b>
      </div>
      <div>
        <div class="sub">短视界 {{ bundle.short_horizon_days }} 天</div>
        <b class="mono">{{ pct01(bundle.short_dim_score) }}</b>
      </div>
      <div>
        <div class="sub">多尺度一致性</div>
        <b class="mono">{{ pct01(bundle.scale_consistency) }}</b>
      </div>
      <div v-if="bundle.spot_price != null">
        <div class="sub">现价</div>
        <b class="mono">{{ money(bundle.spot_price, { signed: false }) }}</b>
      </div>
    </div>
    <p class="sub">{{ bundle.hint || bundle.label }}</p>

    <div
      v-if="capitalNeed"
      style="margin-top: 12px; padding: 10px 12px; border: 1px solid var(--border, #333); border-radius: 8px"
      :class="capitalNeed.gate_ok ? '' : 'warn'"
    >
      <div class="range-row" style="gap: 20px; flex-wrap: wrap">
        <div>
          <div class="sub">危机资本 · 建议可投</div>
          <b class="mono" style="font-size: 20px">{{ pct01(capitalNeed.investable_ratio) }}</b>
          <div class="sub">≈ {{ money(capitalNeed.investable_capital, { signed: false }) }}</div>
        </div>
        <div>
          <div class="sub">现金缓冲</div>
          <b class="mono">{{ pct01(capitalNeed.cash_buffer_ratio) }}</b>
        </div>
        <div>
          <div class="sub">最狠危机管</div>
          <b>{{ capitalNeed.worst_label || capitalNeed.worst_direction || '—' }}</b>
          <div class="sub">P05 亏损率 {{ pct01(capitalNeed.worst_p05_loss_frac) }}</div>
        </div>
      </div>
      <p class="sub" style="margin-top: 8px">{{ capitalNeed.note }}</p>
      <p class="sub" v-if="capitalNeed.gate_hint" style="margin-top: 4px">{{ capitalNeed.gate_hint }}</p>
      <p class="sub" style="margin-top: 4px">{{ capitalNeed.formula }}</p>
    </div>

    <div class="table-wrap" style="margin-top: 12px">
      <table>
        <thead>
          <tr>
            <th>族</th>
            <th>方向</th>
            <th>冲击</th>
            <th>状态</th>
            <th>中位盈亏</th>
            <th>5%分位</th>
            <th>盈利概率</th>
            <th>MDD P50</th>
            <th>MDD P90</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="d in bundle.directions" :key="d.id">
            <td class="sub">{{ FAMILY_LABEL[d.family] || d.family }}</td>
            <td>
              {{ d.label }}
              <div class="sub">{{ d.description }}</div>
            </td>
            <td class="mono">
              {{ d.value }}
              <div class="sub" v-if="d.vol_mult">σ×{{ d.vol_mult }}</div>
              <div class="sub" v-if="d.gap != null">缺口 {{ d.gap }}</div>
            </td>
            <td :class="statusClass(d.status)">{{ d.status }}</td>
            <td class="mono" :class="d.median_pnl >= 0 ? 'up' : 'down'">{{ money(d.median_pnl) }}</td>
            <td class="mono" :class="d.p05_pnl >= 0 ? 'up' : 'down'">{{ money(d.p05_pnl) }}</td>
            <td class="mono">{{ pct01(d.prob_profit) }}</td>
            <td class="mono">{{ pct01(d.mdd_p50) }}</td>
            <td class="mono">{{ pct01(d.mdd_p90) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
