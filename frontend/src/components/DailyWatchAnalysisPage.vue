<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { can } from '../auth'
import { isoDate } from '../format'
import AppendJournalDialog from './AppendJournalDialog.vue'
import MacroChart from './MacroChart.vue'

const emit = defineEmits(['back'])

const watches = ref([])
const selected = reactive({})
const mode = ref('day')
const asof = ref(isoDate())
const loading = ref(false)
const error = ref('')
const data = ref(null)
const hasRun = ref(false)
const journalOpen = ref(false)
const journalSnippet = ref('')

const AXIS_LABEL = { color: '#8b9bb0', fontSize: 11 }
const AXIS_LINE = { lineStyle: { color: 'rgba(148,176,210,0.2)' } }
const SPLIT_LINE = { lineStyle: { color: 'rgba(148,176,210,0.08)' } }
const TOOLTIP_BASE = {
  trigger: 'axis',
  backgroundColor: '#101820',
  borderColor: 'rgba(148,176,210,0.2)',
  textStyle: { color: '#e9f0f7' },
}

const selectedSymbols = computed(() => watches.value.filter((w) => selected[w.id]).map((w) => w.symbol))

function selectAll() {
  watches.value.forEach((w) => { selected[w.id] = true })
}
function selectNone() {
  watches.value.forEach((w) => { selected[w.id] = false })
}

async function loadWatches() {
  try {
    watches.value = await api.dailyWatches()
    selectAll()
  } catch (e) {
    error.value = e.message
  }
}

async function runAnalysis() {
  if (!selectedSymbols.value.length) {
    error.value = '至少勾选一支股票'
    return
  }
  hasRun.value = true
  error.value = ''
  loading.value = true
  try {
    data.value = await api.dailyWatchAnalysis({
      symbols: selectedSymbols.value.join(','),
      mode: mode.value,
      asof: asof.value || undefined,
    })
  } catch (e) {
    data.value = null
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function setMode(m) {
  mode.value = m
  if (hasRun.value) runAnalysis()
}

function onAsofChange() {
  if (hasRun.value) runAnalysis()
}

function pct(n) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  const v = Number(n) * 100
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
}
function px(n) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  return `$${Number(n).toFixed(2)}`
}
function toneOf(n) {
  if (n == null) return ''
  return n > 0 ? 'up' : n < 0 ? 'down' : ''
}

function chartHeight(n) {
  return Math.max(220, n * 28) + 'px'
}

const canAppendJournal = computed(
  () => mode.value === 'day' && can('btn.stock_journal.write') && !!data.value?.sector?.rows?.length,
)

function buildSectorSnippet() {
  const sector = data.value?.sector
  const rows = sector?.rows || []
  if (!rows.length) return ''
  const day = data.value?.asof || asof.value || isoDate()
  const lines = rows.map((r) => {
    const chg = pct(r.avg_pct_change)
    return `${r.sector} ${chg}（${r.count} 支）`
  })
  return `【行业分析 · ${sector.period_label || '日'}】${day}\n${lines.join('\n')}`
}

function openSectorJournal() {
  journalSnippet.value = buildSectorSnippet()
  if (!journalSnippet.value) return
  journalOpen.value = true
}

// ① 涨跌幅：横向 Bar，从上到下按涨幅从高到低排（inverse category axis）
const priceChangeOption = computed(() => {
  const rows = data.value?.price_change?.rows
  if (!rows || !rows.length) return null
  return {
    backgroundColor: 'transparent',
    tooltip: { ...TOOLTIP_BASE, valueFormatter: (v) => pct(v / 100) },
    grid: { left: 64, right: 24, top: 16, bottom: 32 },
    xAxis: {
      type: 'value', axisLabel: { ...AXIS_LABEL, formatter: '{value}%' }, splitLine: SPLIT_LINE,
    },
    yAxis: {
      type: 'category', data: rows.map((r) => r.symbol), axisLabel: AXIS_LABEL, axisLine: AXIS_LINE,
      inverse: true,
    },
    series: [{
      type: 'bar',
      barMaxWidth: 16,
      data: rows.map((r) => ({
        value: r.pct_change * 100,
        itemStyle: { color: r.pct_change >= 0 ? '#3ecf8e' : '#e85d75' },
      })),
    }],
  }
})

// ② 回归线：散点 + 0 轴——横轴是"现价相对回归线偏离了百分之几"，0 是回归线本身，
// 点在 0 右边=在回归线上方，左边=下方，一眼就能看出谁明显偏离。
const regressionOption = computed(() => {
  const rows = data.value?.regression?.rows?.filter((r) => r.distance_pct != null)
  if (!rows || !rows.length) return null
  const symbols = rows.map((r) => r.symbol)
  return {
    backgroundColor: 'transparent',
    tooltip: {
      ...TOOLTIP_BASE, trigger: 'item',
      formatter: (p) => `${symbols[p.data[1]]}：${pct(p.data[0] / 100)}`,
    },
    grid: { left: 64, right: 24, top: 16, bottom: 32 },
    xAxis: {
      type: 'value', name: '现价偏离回归线', nameTextStyle: { color: '#8b9bb0' },
      axisLabel: { ...AXIS_LABEL, formatter: '{value}%' }, splitLine: SPLIT_LINE,
      axisLine: { show: true, lineStyle: { color: 'rgba(148,176,210,0.35)' } },
    },
    yAxis: {
      type: 'category', data: symbols, axisLabel: AXIS_LABEL, axisLine: AXIS_LINE,
      inverse: true, splitLine: { show: true, lineStyle: { color: 'rgba(148,176,210,0.06)' } },
    },
    series: [{
      type: 'scatter',
      symbolSize: 14,
      data: rows.map((r, i) => ({
        value: [Number((r.distance_pct * 100).toFixed(2)), i],
        itemStyle: { color: r.position === 'above' ? '#3ecf8e' : '#e85d75' },
      })),
      markLine: {
        symbol: 'none', silent: true,
        lineStyle: { color: 'rgba(148,176,210,0.5)', type: 'solid', width: 1.5 },
        label: { show: false },
        data: [{ xAxis: 0 }],
      },
    }],
  }
})

// ③ Max Pain：哑铃图（双点连线）——横轴是"相对现价的百分比"，左端点固定在 0
// (现价本身)，右端点是 Max Pain 相对现价的位置，中间一条粗线连起来，
// 一眼看出现价到 Max Pain 还有多少空间、往哪个方向走。
const maxPainOption = computed(() => {
  const rows = data.value?.max_pain?.rows?.filter((r) => r.distance_pct != null)
  if (!rows || !rows.length) return null
  const symbols = rows.map((r) => r.symbol)
  const bases = rows.map((r) => Math.min(0, r.distance_pct * 100))
  const spans = rows.map((r) => Math.abs(r.distance_pct * 100))
  return {
    backgroundColor: 'transparent',
    tooltip: {
      ...TOOLTIP_BASE, trigger: 'item',
      formatter: (p) => {
        const r = rows[p.dataIndex]
        if (p.seriesName === '现价') return `${r.symbol} 现价 ${px(r.current_price)}（基准 0%）`
        if (p.seriesName === 'Max Pain') return `${r.symbol} Max Pain ${px(r.max_pain)}（${pct(r.distance_pct)}${r.reached ? ' · 已到达' : ''}）`
        return ''
      },
    },
    grid: { left: 64, right: 24, top: 16, bottom: 32 },
    xAxis: {
      type: 'value', name: '相对现价的位置', nameTextStyle: { color: '#8b9bb0' },
      axisLabel: { ...AXIS_LABEL, formatter: '{value}%' }, splitLine: SPLIT_LINE,
      axisLine: { show: true, lineStyle: { color: 'rgba(148,176,210,0.35)' } },
    },
    yAxis: {
      type: 'category', data: symbols, axisLabel: AXIS_LABEL, axisLine: AXIS_LINE,
      inverse: true, splitLine: { show: true, lineStyle: { color: 'rgba(148,176,210,0.06)' } },
    },
    series: [
      {
        name: '_base', type: 'bar', stack: 'dumbbell', barMaxWidth: 3,
        itemStyle: { color: 'transparent' }, silent: true, tooltip: { show: false },
        data: bases,
      },
      {
        name: '_span', type: 'bar', stack: 'dumbbell', barMaxWidth: 3,
        itemStyle: { color: 'rgba(148,176,210,0.45)' }, silent: true, tooltip: { show: false },
        data: spans,
      },
      {
        name: '现价', type: 'scatter', symbolSize: 12,
        itemStyle: { color: '#7aa2ff' },
        data: rows.map((_, i) => [0, i]),
      },
      {
        name: 'Max Pain', type: 'scatter', symbolSize: 12,
        itemStyle: { color: '#f0b429' },
        data: rows.map((r, i) => [Number((r.distance_pct * 100).toFixed(2)), i]),
      },
    ],
    legend: { top: 0, textStyle: { color: '#8b9bb0' }, data: ['现价', 'Max Pain'] },
  }
})

// ④ 行业涨跌：单列 Heatmap，颜色深浅代表涨跌幅，比柱状图更像一张"热力表"
const sectorOption = computed(() => {
  const rows = data.value?.sector?.rows
  if (!rows || !rows.length) return null
  const values = rows.map((r) => r.avg_pct_change * 100)
  const maxAbs = Math.max(1, ...values.map((v) => Math.abs(v)))
  return {
    backgroundColor: 'transparent',
    tooltip: {
      ...TOOLTIP_BASE, trigger: 'item',
      formatter: (p) => `${rows[p.data[1]].sector}：${pct(rows[p.data[1]].avg_pct_change)}（${rows[p.data[1]].count} 支）`,
    },
    grid: { left: 90, right: 60, top: 8, bottom: 8 },
    xAxis: { type: 'category', data: [''], show: false },
    yAxis: {
      type: 'category', data: rows.map((r) => r.sector), axisLabel: AXIS_LABEL,
      axisLine: { show: false }, splitArea: { show: false },
    },
    visualMap: {
      show: false, min: -maxAbs, max: maxAbs,
      inRange: { color: ['#e85d75', '#182028', '#3ecf8e'] },
    },
    series: [{
      type: 'heatmap',
      data: values.map((v, i) => [0, i, Number(v.toFixed(2))]),
      label: {
        show: true, color: '#e9f0f7', fontSize: 12,
        formatter: (p) => pct(p.data[2] / 100),
      },
      itemStyle: { borderColor: '#0b0f14', borderWidth: 2 },
    }],
  }
})

onMounted(loadWatches)
</script>

<template>
  <div class="range-row">
    <button class="btn" type="button" @click="emit('back')">返回每日观察</button>
  </div>
  <p class="goal-lead">勾选要分析的股票，选日/周与锚定日期，跑 4 块分析：涨跌幅、回归线上下方、Max Pain 到达情况、行业涨跌。涨跌幅与行业按锚定日回看。</p>

  <section class="panel">
    <div class="range-row" style="align-items: center; flex-wrap: wrap; gap: 10px">
      <div class="pills" style="margin: 0">
        <button class="pill" type="button" :class="{ active: mode === 'day' }" @click="setMode('day')">日</button>
        <button class="pill" type="button" :class="{ active: mode === 'week' }" @click="setMode('week')">周</button>
      </div>
      <el-date-picker
        v-model="asof"
        type="date"
        value-format="YYYY-MM-DD"
        placeholder="锚定日期"
        :clearable="false"
        :disabled-date="(d) => d.getTime() > Date.now()"
        style="width: 160px"
        @change="onAsofChange"
      />
      <span class="sub">涨跌幅 / 行业按该日回看</span>
    </div>
    <div class="range-row" style="margin-top: 10px">
      <button class="btn" type="button" @click="selectAll">全选</button>
      <button class="btn" type="button" @click="selectNone">全不选</button>
      <span class="sub">已选 {{ selectedSymbols.length }} / {{ watches.length }}</span>
    </div>
    <div class="check-row" style="flex-wrap: wrap; margin-top: 10px; gap: 10px 16px">
      <label class="check" v-for="w in watches" :key="w.id">
        <input type="checkbox" v-model="selected[w.id]" /> {{ w.symbol }}
      </label>
    </div>
    <div class="modal-actions" style="margin-top: 12px; justify-content: flex-start">
      <button class="btn btn-primary" type="button" :disabled="loading" @click="runAnalysis">
        {{ loading ? '分析中…' : '分析' }}
      </button>
    </div>
    <div class="error" v-if="error">{{ error }}</div>
  </section>

  <template v-if="data">
    <section class="panel" style="margin-top: 12px" v-if="data.warnings?.length">
      <div v-for="(w, i) in data.warnings" :key="i" class="warn-msg">⚠️ {{ w }}</div>
    </section>

    <section class="panel" style="margin-top: 12px">
      <h2>1. 涨跌幅分析 · {{ data.price_change.period_label }}</h2>
      <MacroChart v-if="priceChangeOption" :option="priceChangeOption" :height="chartHeight(data.price_change.rows.length)" />
      <p class="sub" v-else>数据不够，算不出涨跌幅。</p>
    </section>

    <section class="panel" style="margin-top: 12px">
      <h2>2. 回归线分析</h2>
      <div class="price-grid">
        <div class="price-card">
          <span>在回归线上方</span>
          <b class="up">{{ data.regression.above_count }}</b>
        </div>
        <div class="price-card">
          <span>在回归线下方</span>
          <b class="down">{{ data.regression.below_count }}</b>
        </div>
        <div class="price-card" v-if="data.regression.unknown_count">
          <span>没有回归线数据</span>
          <b>{{ data.regression.unknown_count }}</b>
        </div>
      </div>
      <MacroChart v-if="regressionOption" :option="regressionOption" :height="chartHeight(data.regression.rows.filter(r => r.distance_pct != null).length)" />
      <div class="table-wrap" style="margin-top: 10px" v-if="data.regression.rows.some(r => r.position === 'unknown')">
        <p class="sub">没有回归线数据：{{ data.regression.rows.filter(r => r.position === 'unknown').map(r => r.symbol).join('、') }}</p>
      </div>
    </section>

    <section class="panel" style="margin-top: 12px">
      <h2>3. Max Pain 分析</h2>
      <p class="sub">现价跟 Max Pain 差距在 ±{{ (data.max_pain.reached_threshold_pct * 100).toFixed(0) }}% 以内算"已到达"（黄色）。</p>
      <div class="price-grid">
        <div class="price-card">
          <span>已到达 Max Pain</span>
          <b class="warn">{{ data.max_pain.reached_count }}</b>
        </div>
        <div class="price-card">
          <span>还没到达</span>
          <b>{{ data.max_pain.not_reached_count }}</b>
        </div>
        <div class="price-card" v-if="data.max_pain.unknown_count">
          <span>没有 Max Pain 数据</span>
          <b>{{ data.max_pain.unknown_count }}</b>
        </div>
      </div>
      <MacroChart v-if="maxPainOption" :option="maxPainOption" :height="chartHeight(data.max_pain.rows.filter(r => r.distance_pct != null).length)" />
    </section>

    <section class="panel" style="margin-top: 12px">
      <div class="range-row" style="align-items: center; justify-content: space-between; gap: 10px">
        <h2 style="margin: 0">4. 行业分析 · {{ data.sector.period_label }}</h2>
        <button
          v-if="canAppendJournal"
          class="btn"
          type="button"
          @click="openSectorJournal"
        >
          加入日志
        </button>
      </div>
      <MacroChart v-if="sectorOption" :option="sectorOption" :height="chartHeight(data.sector.rows.length)" />
      <p class="sub" v-if="data.sector.rows?.length">
        涨幅最猛：<b :class="toneOf(data.sector.rows[0].avg_pct_change)">{{ data.sector.rows[0].sector }}</b>
        {{ pct(data.sector.rows[0].avg_pct_change) }}（{{ data.sector.rows[0].count }} 支）
      </p>
    </section>
  </template>

  <AppendJournalDialog
    v-model="journalOpen"
    title="行业分析加入日志"
    :snippet="journalSnippet"
    :default-date="data?.asof || asof || isoDate()"
  />
</template>
