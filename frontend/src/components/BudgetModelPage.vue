<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { money } from '../format'

const models = ref([])
const modelId = ref('block_bootstrap')
const symbolsText = ref('AAPL')
const horizonDays = ref(21)
const capital = ref('10000')
const windowDays = ref(252)
const driftMode = ref('historical')
const targetPct = ref('5')
const targetBuyPrice = ref('')
const loading = ref(false)
const entryLoading = ref(false)
const error = ref('')
const data = ref(null)
const entryData = ref(null)
const entryFocus = ref(null) // 当前展开回撤时间的代码

const HOLD_OPTS = [
  { value: 10, label: '10 天' },
  { value: 21, label: '21 天' },
  { value: 30, label: '30 天' },
  { value: 42, label: '42 天' },
  { value: 63, label: '63 天' },
  { value: 126, label: '126 天' },
]
const WINDOW_OPTS = [
  { value: 63, label: '近 3 月' },
  { value: 126, label: '近 6 月' },
  { value: 252, label: '近 1 年' },
]

const selectedMeta = computed(() => models.value.find((m) => m.id === modelId.value) || null)

function parseSymbols(raw) {
  return String(raw || '')
    .split(/[\s,，;；\n]+/)
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean)
}

function verdictClass(v) {
  if (v === '赚') return 'up'
  if (v === '亏') return 'down'
  return ''
}

function pct01(n, digits = 1) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  return `${(Number(n) * 100).toFixed(digits)}%`
}

async function loadModels() {
  try {
    const res = await api.budgetModels()
    models.value = res.items || []
    if (res.default) modelId.value = res.default
    else if (models.value.length) modelId.value = models.value[0].id
  } catch (e) {
    error.value = e.message
  }
}

async function run() {
  error.value = ''
  const symbols = parseSymbols(symbolsText.value)
  if (!symbols.length) {
    error.value = '请填写至少一个股票代码（可用逗号分隔多个）'
    return
  }
  const cap = Number(capital.value)
  if (!(cap > 0)) {
    error.value = '请填写本金'
    return
  }
  loading.value = true
  data.value = null
  try {
    data.value = await api.runBudgetModel({
      model_id: modelId.value,
      symbols,
      horizon_days: Number(horizonDays.value) || 21,
      capital: cap,
      window: Number(windowDays.value) || 252,
      n_sims: 2000,
      drift_mode: driftMode.value,
    })
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function runTargetEntry() {
  error.value = ''
  const symbols = parseSymbols(symbolsText.value)
  if (!symbols.length) {
    error.value = '请填写至少一个股票代码'
    return
  }
  const pct = Number(targetPct.value)
  if (!(pct > 0) || pct > 100) {
    error.value = '请填写目标收益率（例如 5 表示赚 5%）'
    return
  }
  entryLoading.value = true
  entryData.value = null
  entryFocus.value = null
  try {
    const body = {
      symbols,
      target_return: pct / 100,
      horizon_days: Number(horizonDays.value) || 21,
      window: Number(windowDays.value) || 252,
      n_sims: 3000,
      drift_mode: driftMode.value,
    }
    const px = String(targetBuyPrice.value || '').trim()
    if (px !== '') {
      const n = Number(px)
      if (!(n > 0)) {
        error.value = '目标买入价须大于 0'
        entryLoading.value = false
        return
      }
      body.target_buy_price = n
    }
    entryData.value = await api.budgetTargetEntry(body)
    if (entryData.value?.items?.length) entryFocus.value = entryData.value.items[0].symbol
  } catch (e) {
    error.value = e.message
  } finally {
    entryLoading.value = false
  }
}

function focusEntry(sym) {
  entryFocus.value = sym
}

const focusedEntry = computed(() => {
  const items = entryData.value?.items || []
  return items.find((x) => x.symbol === entryFocus.value) || items[0] || null
})

onMounted(loadModels)
</script>

<template>
  <p class="goal-lead">
    预算模型：可算持仓 N 天赚亏，也可按<strong>目标收益率</strong>反推最优买入价，并给出模拟期内回撤触及该价的概率。
    基于历史收益外推，不是估值点预测。
  </p>

  <section class="panel" style="margin-top: 12px">
    <div class="range-row" style="margin-bottom: 10px; flex-wrap: wrap; gap: 8px">
      <select v-model="modelId" class="strategy-select" style="min-width: 200px">
        <option v-for="m in models" :key="m.id" :value="m.id">{{ m.label || m.name }}</option>
      </select>
      <input
        v-model="symbolsText"
        class="strategy-select"
        style="width: 260px"
        placeholder="股票代码，多个用逗号：AAPL, MSFT"
      />
      <select v-model.number="horizonDays" class="strategy-select">
        <option v-for="opt in HOLD_OPTS" :key="opt.value" :value="opt.value">持仓 {{ opt.label }}</option>
      </select>
      <input v-model="capital" class="strategy-select" style="width: 110px" placeholder="本金$" />
      <select v-model.number="windowDays" class="strategy-select">
        <option v-for="opt in WINDOW_OPTS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
      </select>
      <select v-model="driftMode" class="strategy-select" title="带漂移会把牛市样本外推成偏多">
        <option value="historical">带样本漂移（偏多时常见）</option>
        <option value="zero">去漂移 μ=0（风险视角）</option>
      </select>
      <button class="btn btn-primary" type="button" :disabled="loading" @click="run">
        {{ loading ? '计算中…' : '算赚亏' }}
      </button>
    </div>
    <div class="range-row" style="margin-bottom: 8px; flex-wrap: wrap; gap: 8px">
      <label class="sub" style="display: inline-flex; align-items: center; gap: 6px">
        目标收益
        <input
          v-model="targetPct"
          class="strategy-select"
          style="width: 70px"
          type="number"
          min="0.1"
          max="100"
          step="0.5"
        />
        %
      </label>
      <label class="sub" style="display: inline-flex; align-items: center; gap: 6px">
        目标买点$
        <input
          v-model="targetBuyPrice"
          class="strategy-select"
          style="width: 100px"
          type="number"
          step="0.01"
          min="0"
          placeholder="可空手填"
        />
      </label>
      <button class="btn" type="button" :disabled="entryLoading" @click="runTargetEntry">
        {{ entryLoading ? '反推中…' : '反推买点 / 回撤时间' }}
      </button>
    </div>
    <p class="sub">不填「目标买点」则用模型最优价；填了就按你的价格算回撤触及概率与时间。</p>
    <p class="sub" v-if="selectedMeta">{{ selectedMeta.description }}</p>
    <div class="error" v-if="error" style="margin-top: 8px">{{ error }}</div>
  </section>

  <section class="panel" style="margin-top: 12px" v-if="entryData?.items?.length">
    <h2 style="margin: 0 0 8px; font-size: 16px">
      目标 {{ pct01(entryData.target_return) }} · 持仓 {{ entryData.horizon_days }} 天 · 买点与回撤时间
    </h2>
    <p class="sub">{{ entryData.definition }}</p>
    <div class="table-wrap" style="margin-top: 10px">
      <table>
        <thead>
          <tr>
            <th>代码</th>
            <th>现价</th>
            <th>目标买点</th>
            <th>需回撤</th>
            <th>期内触及</th>
            <th>永不触及</th>
            <th>首次触及中位</th>
            <th>P90 天数</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in entryData.items"
            :key="row.symbol"
            :class="{ active: focusedEntry?.symbol === row.symbol }"
            style="cursor: pointer"
            @click="focusEntry(row.symbol)"
          >
            <td>{{ row.symbol }}</td>
            <td class="mono">{{ money(row.spot_price, { signed: false }) }}</td>
            <td class="mono">
              <b>{{ money(row.optimal_buy_price, { signed: false }) }}</b>
              <div class="sub">
                {{ row.buy_price_source === 'user' ? '你指定' : '模型最优' }}
                <span v-if="row.buy_price_source === 'user'">
                  · 模型最优 {{ money(row.model_optimal_buy_price, { signed: false }) }}
                </span>
              </div>
            </td>
            <td class="mono">{{ pct01(row.pullback_from_spot_pct) }}</td>
            <td class="mono">{{ pct01(row.prob_touch_optimal) }}</td>
            <td class="mono">{{ pct01(row.prob_never_touch) }}</td>
            <td class="mono">{{ row.touch_days_median == null ? '—' : `${row.touch_days_median} 天` }}</td>
            <td class="mono">{{ row.touch_days_p90 == null ? '—' : `${row.touch_days_p90} 天` }}</td>
            <td class="sub">看时间 →</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="focusedEntry" style="margin-top: 14px">
      <h3 style="margin: 0 0 6px; font-size: 14px">
        {{ focusedEntry.symbol }} · 回撤到
        {{ money(focusedEntry.optimal_buy_price, { signed: false }) }} 的时间
      </h3>
      <p class="sub">{{ focusedEntry.time_note }}</p>
      <div class="range-row" style="gap: 16px; margin: 8px 0; flex-wrap: wrap">
        <div v-for="b in focusedEntry.touch_buckets" :key="b.label">
          <div class="sub">{{ b.label }}</div>
          <b class="mono">{{ pct01(b.prob) }}</b>
        </div>
        <div>
          <div class="sub">P25 / 中位 / P75</div>
          <b class="mono">
            {{ focusedEntry.touch_days_p25 ?? '—' }}
            / {{ focusedEntry.touch_days_median ?? '—' }}
            / {{ focusedEntry.touch_days_p75 ?? '—' }} 天
          </b>
        </div>
      </div>
      <div class="table-wrap" style="margin-top: 8px; max-height: 240px; overflow: auto">
        <table>
          <thead>
            <tr>
              <th>交易日</th>
              <th>累计触及概率</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in focusedEntry.cum_touch_by_day" :key="row.day">
              <td>T+{{ row.day }}</td>
              <td class="mono">
                {{ pct01(row.cum_prob) }}
                <span
                  style="display: inline-block; height: 8px; margin-left: 8px; background: #3d9a5f; vertical-align: middle"
                  :style="{ width: `${Math.max(2, Number(row.cum_prob || 0) * 120)}px` }"
                />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <p class="sub" style="margin-top: 8px" v-for="row in entryData.items" :key="`${row.symbol}-n`">
      {{ row.note }}
    </p>
  </section>

  <section class="panel" style="margin-top: 12px" v-if="data">
    <div class="range-row" style="align-items: baseline; gap: 16px; flex-wrap: wrap">
      <div>
        <div class="sub">结论</div>
        <b class="mono" style="font-size: 28px" :class="verdictClass(data.verdict)">{{ data.verdict }}</b>
      </div>
      <div>
        <div class="sub">中位盈亏 · {{ data.horizon_days }} 天</div>
        <b class="mono" :class="verdictClass(data.verdict)">{{ money(data.median_pnl) }}</b>
      </div>
      <div>
        <div class="sub">盈利概率</div>
        <b class="mono">{{ data.prob_profit == null ? '—' : pct01(data.prob_profit) }}</b>
      </div>
      <div>
        <div class="sub">5% ~ 95%</div>
        <b class="mono">{{ money(data.p5_pnl) }} ~ {{ money(data.p95_pnl) }}</b>
      </div>
      <div>
        <div class="sub">样本年化漂移 μ</div>
        <b class="mono">{{ data.mu_annual_approx == null ? '—' : pct01(data.mu_annual_approx) }}</b>
      </div>
      <div>
        <div class="sub">模型</div>
        <b>{{ data.model_label || data.model_id }}</b>
      </div>
    </div>
    <p class="sub" style="margin-top: 10px">{{ data.price_note }}</p>
    <p class="sub" v-if="data.drift_bias">{{ data.drift_bias }}</p>
    <p class="warn-msg" v-if="data.avoid_entry" style="margin-top: 10px">{{ data.hint }}</p>
    <p class="sub" v-else style="margin-top: 10px">{{ data.hint }}</p>
    <p class="sub" style="margin-top: 6px">{{ data.note }}</p>

    <div class="table-wrap" style="margin-top: 14px" v-if="data.per_symbol?.length">
      <table>
        <thead>
          <tr>
            <th>代码</th>
            <th>方向</th>
            <th>权重</th>
            <th>现价</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in data.per_symbol" :key="row.symbol">
            <td>{{ row.symbol }}</td>
            <td>{{ row.side === 'short' ? '空' : '多' }}</td>
            <td class="mono">{{ row.weight == null ? '—' : pct01(row.weight) }}</td>
            <td class="mono">{{ row.price == null ? '—' : money(row.price, { signed: false }) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
