<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../api'
import { isoDate } from '../format'
import MacroChart from './MacroChart.vue'

function daysAgo(n) {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return isoDate(d)
}

const props = defineProps({ initialSymbol: { type: String, default: '' } })
const symbol = ref('AAPL')
const ma1 = ref(7)
const ma2 = ref(20)
const timeframe = ref('daily')
const priceMode = ref('price')
const excludeEvents = ref(false)
const showMA = ref(false)
const startDate = ref(daysAgo(60))
const endDate = ref(isoDate())
const loading = ref(false)
const error = ref('')
const data = ref(null)

const AXIS_LABEL = { color: '#8b9bb0', fontSize: 11 }
const AXIS_LINE = { lineStyle: { color: 'rgba(148,176,210,0.2)' } }
const SPLIT_LINE = { lineStyle: { color: 'rgba(148,176,210,0.08)' } }
const TOOLTIP_BASE = {
  trigger: 'axis',
  backgroundColor: '#101820',
  borderColor: 'rgba(148,176,210,0.2)',
  textStyle: { color: '#e9f0f7' },
}

async function run() {
  error.value = ''
  const code = symbol.value.trim().toUpperCase()
  if (!code) {
    error.value = '请输入股票代码'
    return
  }
  symbol.value = code
  loading.value = true
  try {
    data.value = await api.regressionScreener({
      symbol: code,
      ma1: ma1.value,
      ma2: ma2.value,
      start: startDate.value || undefined,
      end: endDate.value || undefined,
      timeframe: timeframe.value,
      price_mode: priceMode.value,
      exclude_events: excludeEvents.value,
    })
  } catch (e) {
    data.value = null
    error.value = e.message
  } finally {
    loading.value = false
  }
}
watch(() => props.initialSymbol, (value) => {
  if (value) { symbol.value = value; run() }
}, { immediate: true })

function px(n) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  return `$${Number(n).toFixed(2)}`
}
function zText(n) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  return Number(n).toFixed(2)
}
function compactUsd(n) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  const value = Number(n)
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  if (abs >= 1_000_000_000) return `${sign}$${(abs / 1_000_000_000).toFixed(1)}B`
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(1)}M`
  return `${sign}$${abs.toFixed(0)}`
}

const priceOption = computed(() => {
  const d = data.value
  if (!d || !d.dates?.length) return null
  const channel2Low = d.channel_2_low || []
  const channel2High = d.channel_2_high || []
  const channel1Low = d.channel_1_low || []
  const channel1High = d.channel_1_high || []
  const movingAverages = showMA.value ? [
    {
      name: `MA${d.ma1_window}`, type: 'line', data: d.ma1, showSymbol: false,
      lineStyle: { width: 1.5, color: '#f0b429', type: 'dashed' },
    },
    {
      name: `MA${d.ma2_window}`, type: 'line', data: d.ma2, showSymbol: false,
      lineStyle: { width: 1.5, color: '#7aa2ff', type: 'dashed' },
    },
  ] : []
  return {
    backgroundColor: 'transparent',
    tooltip: { ...TOOLTIP_BASE, valueFormatter: (v) => (v == null ? '—' : px(v)) },
    legend: {
      top: 0,
      textStyle: { color: '#8b9bb0' },
      data: ['收盘价', '回归线', ...(showMA.value ? [`MA${d.ma1_window}`, `MA${d.ma2_window}`] : [])],
    },
    grid: { left: 56, right: 20, top: 40, bottom: 32 },
    xAxis: {
      type: 'category', data: d.dates, boundaryGap: false,
      axisLabel: AXIS_LABEL, axisLine: AXIS_LINE, splitLine: { show: false },
    },
    yAxis: {
      type: 'value', scale: true, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE,
    },
    series: [
      { name: '2σ下界', type: 'line', data: channel2Low, stack: 'channel2', showSymbol: false, silent: true, lineStyle: { opacity: 0 }, areaStyle: { opacity: 0 } },
      { name: '±2稳健尺度', type: 'line', data: channel2High.map((v, i) => v - channel2Low[i]), stack: 'channel2', showSymbol: false, silent: true, lineStyle: { opacity: 0 }, areaStyle: { color: '#7aa2ff', opacity: 0.08 } },
      { name: '1σ下界', type: 'line', data: channel1Low, stack: 'channel1', showSymbol: false, silent: true, lineStyle: { opacity: 0 }, areaStyle: { opacity: 0 } },
      { name: '±1稳健尺度', type: 'line', data: channel1High.map((v, i) => v - channel1Low[i]), stack: 'channel1', showSymbol: false, silent: true, lineStyle: { opacity: 0 }, areaStyle: { color: '#82a5ff', opacity: 0.15 } },
      {
        name: '收盘价', type: 'line', data: d.closes, showSymbol: false,
        lineStyle: { width: 2, color: '#3ecf8e' },
        areaStyle: { color: '#3ecf8e', opacity: 0.08 },
        markLine: (d.event_dates || []).length
          ? {
              symbol: 'none', silent: true,
              lineStyle: { color: '#f0b429', type: 'dashed', width: 1 },
              label: { color: '#f0b429', formatter: '事件日', position: 'insideEndTop', fontSize: 10 },
              data: d.event_dates.map((ed) => ({ xAxis: ed })),
            }
          : undefined,
      },
      ...movingAverages,
      {
        name: '回归线', type: 'line', data: d.regression, showSymbol: false,
        lineStyle: { width: 1.5, color: '#e85d75', type: 'dashed' },
      },
    ],
  }
})

const deviationOption = computed(() => {
  const d = data.value
  if (!d?.deviation_history?.length) return null
  return {
    backgroundColor: 'transparent', tooltip: { ...TOOLTIP_BASE, valueFormatter: v => zText(v) },
    grid: { left: 56, right: 20, top: 18, bottom: 32 },
    xAxis: { type: 'category', data: d.dates, boundaryGap: false, axisLabel: AXIS_LABEL, axisLine: AXIS_LINE },
    yAxis: { type: 'value', axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
    series: [{ name: '稳健标准化偏离', type: 'line', data: d.deviation_history, showSymbol: false,
      lineStyle: { width: 2, color: '#c792ea' }, areaStyle: { color: '#c792ea', opacity: .07 },
      markLine: { symbol: 'none', silent: true, label: { color: '#7f91aa' }, lineStyle: { type: 'dashed', color: '#42526a' }, data: [-2, -1, 0, 1, 2].map(yAxis => ({ yAxis })) } }],
  }
})

const slopeOption = computed(() => {
  const d = data.value
  if (!d?.rolling_slope?.some(v => v != null)) return null
  return {
    backgroundColor: 'transparent', tooltip: { ...TOOLTIP_BASE, valueFormatter: v => v == null ? '—' : `${Number(v).toFixed(2)}%` },
    grid: { left: 56, right: 20, top: 18, bottom: 32 },
    xAxis: { type: 'category', data: d.dates, boundaryGap: false, axisLabel: AXIS_LABEL, axisLine: AXIS_LINE },
    yAxis: { type: 'value', axisLabel: { ...AXIS_LABEL, formatter: v => `${v}%` }, splitLine: SPLIT_LINE },
    series: [{ name: '滚动趋势', type: 'line', data: d.rolling_slope, showSymbol: false,
      lineStyle: { width: 2, color: '#f0b429' }, markLine: { symbol: 'none', data: [{ yAxis: 0 }], lineStyle: { color: '#42526a' }, label: { show: false } } }],
  }
})

function pctText(n) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  return `${Number(n) > 0 ? '+' : ''}${Number(n).toFixed(2)}%`
}

function financialBarOption(series, color) {
  if (!series || !series.periods?.length) return null
  return {
    backgroundColor: 'transparent',
    tooltip: { ...TOOLTIP_BASE, valueFormatter: (v) => compactUsd(v) },
    grid: { left: 56, right: 16, top: 16, bottom: 32 },
    xAxis: {
      type: 'category', data: series.periods,
      axisLabel: AXIS_LABEL, axisLine: AXIS_LINE,
    },
    yAxis: {
      type: 'value', axisLabel: { ...AXIS_LABEL, formatter: (v) => compactUsd(v) }, splitLine: SPLIT_LINE,
    },
    series: [{ type: 'bar', data: series.values, itemStyle: { color } }],
  }
}

const revenueOption = computed(() => financialBarOption(data.value?.revenue, '#3ecf8e'))
const grossProfitOption = computed(() => financialBarOption(data.value?.gross_profit, '#f0b429'))
</script>

<template>
  <p class="goal-lead">衡量价格相对所选区间趋势的位置、趋势稳定性及多周期一致性。低于趋势线表示低于该区间的价格趋势，不代表企业价值被低估。</p>

  <div class="screener-layout">
    <aside class="panel screener-side">
      <div class="field">
        <label>股票代码</label>
        <input v-model="symbol" placeholder="AAPL" @keydown.enter.prevent="run" />
      </div>
      <div class="field">
        <label>数据周期</label>
        <select v-model="timeframe"><option value="daily">日线</option><option value="weekly">周线</option></select>
      </div>
      <div class="field">
        <label>拟合空间</label>
        <select v-model="priceMode"><option value="price">价格</option><option value="log">对数价格</option></select>
      </div>
      <div class="field">
        <label>开始日期</label>
        <input v-model="startDate" type="date" />
      </div>
      <label class="reg-check"><input v-model="excludeEvents" type="checkbox" />拟合时排除事件日</label>
      <p class="sub reg-note">事件日默认保留。排除只影响当日样本，无法消除事件后的价格水平变化。</p>
      <details class="display-settings"><summary>显示设置</summary>
        <label class="reg-check"><input v-model="showMA" type="checkbox" />显示均线</label>
        <div v-if="showMA" class="field"><label>均线一：MA{{ ma1 }}</label><input v-model.number="ma1" type="range" min="2" max="200" step="1" /></div>
        <div v-if="showMA" class="field"><label>均线二：MA{{ ma2 }}</label><input v-model.number="ma2" type="range" min="2" max="200" step="1" /></div>
      </details>
      <div class="field">
        <label>结束日期</label>
        <input v-model="endDate" type="date" />
      </div>
      <button class="btn btn-primary" type="button" style="width: 100%" :disabled="loading" @click="run">
        {{ loading ? '分析中…' : '分析' }}
      </button>
      <div class="error" v-if="error">{{ error }}</div>
    </aside>

    <div class="screener-main">
      <p class="sub" v-if="!data && !loading">填好左边的参数，点分析。</p>
      <template v-if="data">
        <section class="panel">
          <h2>{{ data.symbol }} · 价格与回归通道</h2>
          <p class="sub">Theil–Sen 降低个别异常值对趋势的影响；MAD 采用 1.4826 正态一致性缩放。阴影带为历史残差的 ±1、±2 倍稳健尺度，不表示未来价格落入其中的概率。</p>
          <div class="price-grid metric-grid">
            <div class="price-card">
              <span>当前价</span>
              <b>{{ px(data.current_price) }}</b>
            </div>
            <div class="price-card">
              <span>当日趋势拟合值</span>
              <b>{{ px(data.predicted_price) }}</b>
            </div>
            <div class="price-card">
              <span>稳健标准化偏离</span>
              <b :class="data.latest_is_event ? '' : (data.z_score < 0 ? 'up' : data.z_score > 0 ? 'down' : '')">
                {{ zText(data.z_score) }}
              </b>
            </div>
          </div>
          <MacroChart v-if="priceOption" :option="priceOption" />
          <div class="warn-msg" v-if="data.latest_is_event" style="margin-top: 10px">
            ⚠️ 最新一期是事件日（单期涨跌幅明显异常），稳健标准化偏离的参考价值会降低；它可能反映财报或突发消息形成的新价格水平。
          </div>
          <p class="sub" style="margin-top: 10px">
            样本区间 {{ data.sample_start }} ~ {{ data.sample_end }}
            <template v-if="data.event_dates?.length"> · 检测到 {{ data.event_dates.length }} 个事件日：{{ data.event_dates.join('、') }}</template>
          </p>
        </section>

        <section class="panel" v-if="deviationOption">
          <h2>偏离度历史</h2>
          <p class="sub">观察偏离是在扩大还是收敛，以及同方向偏离已经持续多久。</p>
          <MacroChart :option="deviationOption" height="260px" />
        </section>

        <section class="panel">
          <h2>多周期对比</h2>
          <div class="table-wrap"><table><thead><tr><th>周期</th><th>趋势方向</th><th>每20期拟合变化率</th><th>当前偏离</th><th>稳健偏离</th><th>偏离持续</th></tr></thead><tbody>
            <tr v-for="row in data.period_comparison" :key="row.period"><td>{{ row.period }} {{ data.timeframe === 'weekly' ? '周' : '交易日' }}</td><template v-if="row.available"><td :class="row.direction === '上升' ? 'up' : row.direction === '下降' ? 'down' : ''">{{ row.direction }}</td><td>{{ pctText(row.change_per_20) }}</td><td>{{ pctText(row.deviation_pct) }}</td><td>{{ zText(row.robust_deviation) }}</td><td>{{ row.deviation_duration }} 期</td></template><td v-else colspan="5" class="sub">样本不足</td></tr>
          </tbody></table></div>
        </section>

        <section class="panel" v-if="slopeOption">
          <h2>滚动趋势变化</h2>
          <p class="sub">固定窗口逐期重新拟合后的每20期变化率，只使用当时及以前的数据。</p>
          <MacroChart :option="slopeOption" height="250px" />
        </section>

        <section class="panel">
          <h2>历史相似情况</h2>
          <p class="sub">寻找相同趋势方向且稳健偏离相差不超过 0.4 的历史状态；每个历史日期只使用当时及以前的数据拟合。</p>
          <div class="price-grid similar-grid"><div class="price-card"><span>相似样本</span><b>{{ data.similar_history.count }}</b></div><div class="price-card"><span>后续周期</span><b>{{ data.similar_history.horizon }} 期</b></div><div class="price-card"><span>平均收益</span><b>{{ pctText(data.similar_history.average_return) }}</b></div><div class="price-card"><span>上涨比例</span><b>{{ pctText(data.similar_history.positive_rate) }}</b></div><div class="price-card"><span>最差收益</span><b>{{ pctText(data.similar_history.worst_return) }}</b></div></div>
          <p v-if="data.similar_history.count < 10" class="warn-msg">相似样本少于 10 次，统计稳定性较低，仅作描述性参考。</p>
        </section>

        <div class="screener-fin-grid">
          <section class="panel" v-if="revenueOption">
            <h2>Total Revenue</h2>
            <MacroChart :option="revenueOption" />
          </section>
          <section class="panel" v-if="grossProfitOption">
            <h2>Gross Profit</h2>
            <MacroChart :option="grossProfitOption" />
          </section>
        </div>
        <p class="sub" v-if="!revenueOption && !grossProfitOption">没拉到这只股票的营收/毛利数据（可能不是 SEC 直接报送 US-GAAP 的标的）。</p>
      </template>
    </div>
  </div>
</template>

<style scoped>
.screener-main { display:grid;gap:14px; }.reg-check { display:flex;align-items:center;gap:8px;color:#b8c5d7;font-size:12px;margin:12px 0; }.reg-note { line-height:1.6; }.display-settings { margin:14px 0;padding:12px;border:1px solid var(--line);border-radius:9px; }.display-settings summary { cursor:pointer;color:#a9bad1;font-size:12px; }.display-settings .field { margin-top:12px; }.metric-grid { margin:16px 0 6px; }.similar-grid { grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); }
</style>
