<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../api'
import { isoDate, money } from '../format'
import { TIMEFRAMES as BASE_TFS } from '../config/strategies'

const props = defineProps({
  strategyId: { default: '' },
})

const TIMEFRAMES = [
  { key: '1d', label: '日' },
  { key: '1w', label: '周' },
  ...BASE_TFS.filter((row) => row.key !== '1d'),
]

const RANGES = [
  { key: '1d', label: '日', days: 1 },
  { key: '1w', label: '周', days: 7 },
  { key: '1m', label: '近 1 月', months: 1 },
  { key: '3m', label: '近 3 月', months: 3 },
  { key: '6m', label: '近 6 月', months: 6 },
  { key: '1y', label: '近 1 年', months: 12 },
]

function isWeekend(iso) {
  if (!iso) return false
  const day = new Date(`${iso}T00:00:00`).getDay()
  return day === 0 || day === 6
}

/** 周末改到最近交易日：start 偏向周一，end 偏向周五。 */
function toTradingDay(iso, { preferPrev = true } = {}) {
  if (!iso) return iso
  const d = new Date(`${iso}T00:00:00`)
  while (d.getDay() === 0 || d.getDay() === 6) {
    if (preferPrev) d.setDate(d.getDate() - 1)
    else d.setDate(d.getDate() + 1)
  }
  return isoDate(d)
}

function recentMonths() {
  const out = []
  const now = new Date()
  for (let i = 0; i < 8; i += 1) {
    const dt = new Date(now.getFullYear(), now.getMonth() - i, 1)
    const year = dt.getFullYear()
    const month = dt.getMonth() + 1
    const start = toTradingDay(`${year}-${String(month).padStart(2, '0')}-01`, {
      preferPrev: false,
    })
    const end = toTradingDay(isoDate(new Date(year, month, 0)), { preferPrev: true })
    out.push({
      key: `${year}-${String(month).padStart(2, '0')}`,
      label: `${month}月`,
      start,
      end,
    })
  }
  return out
}

const MONTHS = recentMonths()

function clampTradingDates() {
  form.start = toTradingDay(form.start, { preferPrev: false })
  form.end = toTradingDay(form.end, { preferPrev: true })
  if (form.start > form.end) form.start = form.end
}

function onStartChange() {
  form.range = 'custom'
  if (isWeekend(form.start)) form.start = toTradingDay(form.start, { preferPrev: false })
  if (form.start > form.end) form.end = form.start
}

function onEndChange() {
  form.range = 'custom'
  if (isWeekend(form.end)) form.end = toTradingDay(form.end, { preferPrev: true })
  if (form.start > form.end) form.start = form.end
}

const catalog = ref({ items: [], groups: [] })
const plans = ref([])
const loading = ref(false)
const error = ref('')
const result = ref(null)

function shiftMonths(n) {
  const dt = new Date()
  dt.setMonth(dt.getMonth() - n)
  return isoDate(dt)
}

function shiftDays(n) {
  const dt = new Date()
  dt.setDate(dt.getDate() - n)
  return isoDate(dt)
}

const form = reactive({
  symbol: 'TSLA',
  timeframe: '1d',
  range: '3m',
  start: toTradingDay(shiftMonths(3), { preferPrev: false }),
  end: toTradingDay(isoDate(), { preferPrev: true }),
  strategy_id: '',
  indicators: [],
  plan_ids: [],
})

const indicatorMap = computed(() =>
  Object.fromEntries((catalog.value.items || []).map((row) => [row.id, row])),
)

const planMap = computed(() =>
  Object.fromEntries((plans.value || []).map((row) => [String(row.id), row])),
)

const ranked = computed(() => {
  const items = [...(result.value?.items || [])]
  items.sort((a, b) => b.count - a.count)
  return items
})

const selectedCount = computed(() => form.indicators.length + form.plan_ids.length)

function pickRange(key) {
  const opt = RANGES.find((row) => row.key === key)
  form.range = key
  if (!opt) return
  form.end = isoDate()
  form.start = opt.days != null ? shiftDays(opt.days) : shiftMonths(opt.months)
  clampTradingDates()
}

function pickMonth(row) {
  form.range = row.key
  form.start = row.start
  form.end = row.end
  clampTradingDates()
}

function px(n) {
  if (n == null || n === '') return '—'
  return money(n, { signed: false, withSymbol: false })
}

function pct(n) {
  if (n == null) return '—'
  return `${(Number(n) * 100).toFixed(1)}%`
}

function picked(id) {
  return form.indicators.includes(id)
}

function toggle(id) {
  const idx = form.indicators.indexOf(id)
  if (idx >= 0) form.indicators.splice(idx, 1)
  else form.indicators.push(id)
}

function planPicked(id) {
  return form.plan_ids.some((x) => String(x) === String(id))
}

function togglePlan(id) {
  const idx = form.plan_ids.findIndex((x) => String(x) === String(id))
  if (idx >= 0) form.plan_ids.splice(idx, 1)
  else form.plan_ids.push(id)
}

function indicatorName(id) {
  return indicatorMap.value[id]?.name || id
}

function planName(id) {
  return planMap.value[String(id)]?.name || `指标${id}`
}

function tfLabel(key) {
  return TIMEFRAMES.find((row) => row.key === key)?.label || key
}

function sourceLabel(src) {
  if (src === 'binance') return '币安'
  if (src === 'yahoo') return 'Yahoo'
  return 'TradingView'
}

async function loadMeta() {
  try {
    const [cat, list] = await Promise.all([
      api.strategyIndicators(),
      api.strategyPlans().catch(() => []),
    ])
    catalog.value = cat
    plans.value = Array.isArray(list) ? list : []
    const id = props.strategyId || form.strategy_id
    if (props.strategyId) {
      form.strategy_id = props.strategyId
      if (!planPicked(props.strategyId)) form.plan_ids.push(props.strategyId)
    }
    const plan = (list || []).find((row) => String(row.id) === String(id))
    if (plan?.timeframe) form.timeframe = plan.timeframe
  } catch {
    catalog.value = { items: [], groups: [] }
  }
}

async function run() {
  error.value = ''
  result.value = null
  const code = form.symbol.trim().toUpperCase()
  if (!code) {
    error.value = '请输入股票代码'
    return
  }
  if (!form.indicators.length && !form.plan_ids.length && !form.strategy_id) {
    error.value = '请至少选一个指标（含「我的指标」）'
    return
  }
  clampTradingDates()
  loading.value = true
  try {
    result.value = await api.strategyScan({
      symbol: code,
      timeframe: form.timeframe,
      start: form.start,
      end: form.end,
      indicators: [...form.indicators],
      plan_ids: form.plan_ids.map((x) => Number(x)).filter((n) => Number.isFinite(n)),
      strategy_id: form.strategy_id || null,
    })
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

watch(
  () => props.strategyId,
  (id) => {
    if (id) form.strategy_id = id
  },
  { immediate: true },
)

watch(
  () => form.strategy_id,
  (id) => {
    const plan = plans.value.find((row) => String(row.id) === String(id))
    if (plan?.timeframe) form.timeframe = plan.timeframe
  },
)

onMounted(loadMeta)
</script>

<template>
  <p class="goal-lead">
    指标验证：选一只股票、一段时间、若干指标。按收盘 K 线逐根数：这段时间里每个指标成立了多少次。
  </p>

  <div class="range-row">
    <div class="watch-edit" style="flex: 1; margin: 0">
      <input
        v-model="form.symbol"
        placeholder="TSLA / NVDA / SKHYUSDT"
        @keydown.enter.prevent="run"
      />
      <button class="btn btn-primary" type="button" :disabled="loading" @click="run">
        {{ loading ? '统计中…' : '统计' }}
      </button>
    </div>
    <div class="pills">
      <button
        v-for="tf in TIMEFRAMES"
        :key="tf.key"
        class="pill"
        :class="{ active: form.timeframe === tf.key }"
        type="button"
        @click="form.timeframe = tf.key"
      >
        {{ tf.label }}
      </button>
    </div>
  </div>

  <div class="range-row" style="margin-top: 8px">
    <div class="pills">
      <button
        v-for="opt in RANGES"
        :key="opt.key"
        class="pill"
        :class="{ active: form.range === opt.key }"
        type="button"
        @click="pickRange(opt.key)"
      >
        {{ opt.label }}
      </button>
    </div>
    <div class="watch-edit scan-dates">
      <input v-model="form.start" type="date" @change="onStartChange" />
      <span class="sub">至</span>
      <input v-model="form.end" type="date" @change="onEndChange" />
    </div>
  </div>
  <div class="pills" style="margin-top: 8px">
    <button
      v-for="opt in MONTHS"
      :key="opt.key"
      class="pill"
      :class="{ active: form.range === opt.key }"
      type="button"
      @click="pickMonth(opt)"
    >
      {{ opt.label }}
    </button>
  </div>

  <p class="sub" style="margin-top: 8px">
    已选 {{ selectedCount }} 个指标：
    <template v-if="selectedCount">
      {{
        [
          ...form.plan_ids.map(planName),
          ...form.indicators.map(indicatorName),
        ].join('、')
      }}
    </template>
    <template v-else>还没选</template>
  </p>
  <div class="clause-list" v-if="selectedCount">
    <span v-for="id in form.plan_ids" :key="'p' + id" class="clause-chip">
      {{ planName(id) }}
      <button class="chip-x" type="button" @click="togglePlan(id)">×</button>
    </span>
    <span v-for="id in form.indicators" :key="id" class="clause-chip">
      {{ indicatorName(id) }}
      <button class="chip-x" type="button" @click="toggle(id)">×</button>
    </span>
  </div>

  <div class="error" v-if="error">{{ error }}</div>
  <p class="sub" v-else-if="loading">正在按历史 K 线逐根计算，日线大约几秒，更短周期会久一点。</p>

  <section class="panel" style="margin-top: 12px" v-if="result">
    <h2>{{ result.symbol }} · {{ tfLabel(result.timeframe) }}</h2>
    <p class="sub">
      你选了 {{ result.requested_start || form.start }} 至 {{ result.requested_end || form.end }}
      · 实际统计 {{ result.start }} 至 {{ result.end }}
      · {{ sourceLabel(result.source) }} · {{ result.bars }} 根
    </p>
    <p class="warn-msg" v-if="result.note">{{ result.note }}</p>
  </section>

  <section class="panel" style="margin-top: 12px" v-if="ranked.length">
    <h2>各指标次数</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>指标</th>
            <th>次数</th>
            <th>占比</th>
            <th>出现时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in ranked" :key="row.id">
            <td>
              <b>{{ row.name }}</b>
              <div class="sub">{{ row.group }}</div>
            </td>
            <td class="mono">{{ row.count }}</td>
            <td>{{ pct(row.rate) }}</td>
            <td>
              <div class="scan-times" v-if="row.dates?.length">
                <ol>
                  <li v-for="(hit, i) in row.dates" :key="hit.date + String(i)">
                    {{ hit.date }} · {{ px(hit.price) }}
                  </li>
                </ol>
              </div>
              <span v-else class="sub">没有触发</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>

  <section class="panel ind-panel" style="margin-top: 12px">
    <h2>要统计的指标</h2>
    <p class="sub">点一下加入，可多选。上方「我的指标」来自指标列表里你自己写的；下面是内置条件。</p>

    <div class="ind-block">
      <div class="ind-label">我的指标</div>
      <div class="clause-list" v-if="plans.length">
        <button
          v-for="plan in plans"
          :key="plan.id"
          class="clause-chip btn-chip strategy-chip"
          :class="{ picked: planPicked(plan.id) }"
          type="button"
          :title="plan.text || plan.notes || ''"
          @click="togglePlan(plan.id)"
        >
          {{ plan.name }}
          <small v-if="plan.kind === 'code'">代码</small>
        </button>
      </div>
      <p class="sub" v-else>还没有自己的指标。先到「指标 · 指标列表」新增。</p>
    </div>

    <div v-for="block in catalog.groups" :key="block.name" class="ind-block">
      <div class="ind-label">{{ block.name }}</div>
      <div class="clause-list">
        <button
          v-for="item in block.items"
          :key="item.id"
          class="clause-chip btn-chip"
          :class="{ picked: picked(item.id) }"
          type="button"
          :title="item.hint"
          @click="toggle(item.id)"
        >
          {{ item.name }}
        </button>
      </div>
    </div>
  </section>
</template>
