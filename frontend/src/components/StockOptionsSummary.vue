<script setup>
import { computed } from 'vue'
import MacroChart from './MacroChart.vue'

const props = defineProps({ data: { type: Object, required: true } })
const valid = v => v != null && v !== '' && Number.isFinite(Number(v))
const number = v => valid(v) ? Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 }) : '—'
const price = v => valid(v) ? `$${number(v)}` : '—'
const percent = v => valid(v) ? `${(Number(v) * 100).toFixed(2)}%` : '—'
const thin = computed(() => props.data.diagnostics?.thin || !valid(props.data.diagnostics?.total_call_oi) || !valid(props.data.diagnostics?.total_put_oi) || Number(props.data.diagnostics.total_call_oi) + Number(props.data.diagnostics.total_put_oi) < 10)
const ratio = computed(() => !thin.value && props.data.diagnostics.total_call_oi > 0 ? number(props.data.diagnostics.total_put_oi / props.data.diagnostics.total_call_oi) : '—')
const range = computed(() => props.data.expected_range || {})
const rangeValid = computed(() => valid(range.value.expected_low) && valid(range.value.expected_high) && valid(props.data.spot) && range.value.expected_high > range.value.expected_low)
const spotPosition = computed(() => Math.max(0, Math.min(100, (props.data.spot - range.value.expected_low) / (range.value.expected_high - range.value.expected_low) * 100)))
const move = value => valid(value) && props.data.spot > 0 ? `${value >= props.data.spot ? '+' : ''}${((value / props.data.spot - 1) * 100).toFixed(2)}%` : '—'
const compact = value => Math.abs(value) >= 1e9 ? `${(value / 1e9).toFixed(1)}B` : Math.abs(value) >= 1e6 ? `${(value / 1e6).toFixed(1)}M` : Math.abs(value) >= 1e3 ? `${(value / 1e3).toFixed(0)}K` : value
function base(yName) {
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: '#101820', borderColor: '#344359', textStyle: { color: '#e9f0f7' }, confine: true },
    legend: { top: 0, textStyle: { color: '#99aac1' } },
    grid: { left: 64, right: 28, top: 55, bottom: 40 },
    xAxis: { type: 'value', scale: true, axisLabel: { color: '#8294ae' }, splitLine: { show: false } },
    yAxis: { type: 'value', name: yName, nameTextStyle: { color: '#8294ae' }, axisLabel: { color: '#8294ae', formatter: compact }, splitLine: { lineStyle: { color: 'rgba(148,176,210,.09)' } } },
  }
}
function spotLine() {
  return { symbol: 'none', silent: true, lineStyle: { color: '#a0aec0', type: 'dashed' }, label: { color: '#a0aec0', position: 'insideEndTop', formatter: '现价' }, data: valid(props.data.spot) ? [{ xAxis: props.data.spot }] : [] }
}
const smile = computed(() => {
  const lines = [['calls', 'Call IV', '#79a0ff'], ['puts', 'Put IV', '#ed89b5']].map(([key, name, color]) => {
    const raw = props.data.volatility_smile?.[key]
    const points = (raw?.strikes || []).map((k, i) => [k, raw.ivs?.[i]]).filter(([k, v]) => valid(k) && valid(v) && v > 0).map(([k, v]) => [Number(k), v * 100]).sort((a, b) => a[0] - b[0])
    return { name, type: 'line', data: points, showSymbol: true, symbolSize: 4, itemStyle: { color }, lineStyle: { width: 2 } }
  })
  if (!lines.some(s => s.data.length)) return null
  lines.find(s => s.data.length).markLine = spotLine()
  return { ...base('隐含波动率 %'), series: lines }
})
const pain = computed(() => {
  const points = (props.data.max_pain?.curve || []).filter(r => valid(r.strike) && valid(r.loss)).map(r => [Number(r.strike), Number(r.loss)]).sort((a, b) => a[0] - b[0])
  if (thin.value || !points.some(p => p[1] > 0)) return null
  const markers = spotLine()
  if (valid(props.data.max_pain?.strike)) markers.data.push({ xAxis: props.data.max_pain.strike, label: { formatter: 'Max Pain', position: 'insideStartTop', color: '#e7bf75' }, lineStyle: { color: '#e7bf75' } })
  return { ...base('到期内在价值总额'), series: [{ name: '到期内在价值', type: 'line', data: points, showSymbol: false, itemStyle: { color: '#a796f4' }, areaStyle: { color: 'rgba(167,150,244,.12)' }, markLine: markers }] }
})
const oi = computed(() => {
  const d = props.data.itm_breakdown
  if (thin.value || !d || !['itm_call_oi', 'otm_call_oi', 'itm_put_oi', 'otm_put_oi'].every(k => valid(d[k]))) return null
  const option = base('持仓量 / 张')
  option.xAxis = { type: 'category', data: ['Call', 'Put'], axisLabel: { color: '#99aac1' } }
  option.series = [
    { name: '价内', type: 'bar', stack: 'oi', barMaxWidth: 64, data: [d.itm_call_oi, d.itm_put_oi], itemStyle: { color: '#79a0ff' } },
    { name: '价外 / 平值', type: 'bar', stack: 'oi', barMaxWidth: 64, data: [d.otm_call_oi, d.otm_put_oi], itemStyle: { color: '#425576', borderRadius: [5, 5, 0, 0] } },
  ]
  return option
})
const gamma = computed(() => {
  const rows = (props.data.gamma_by_strike || []).filter(r => valid(r.strike) && valid(r.gamma_exposure))
  if (thin.value || !rows.some(r => r.gamma_exposure !== 0)) return null
  const option = base('净 Gamma 敞口')
  option.xAxis = { type: 'category', data: rows.map(r => r.strike), axisLabel: { color: '#8294ae', hideOverlap: true } }
  option.series = [{ name: '净 Gamma', type: 'bar', data: rows.map(r => ({ value: r.gamma_exposure, itemStyle: { color: r.gamma_exposure >= 0 ? '#4dd6ad' : '#ed89a0' } })) }]
  return option
})
const charts = computed(() => [
  { title: '波动率微笑', subtitle: '不同行权价的 Call / Put 隐含波动率', option: smile.value, empty: '暂无有效 IV 数据，无法绘制波动率曲线。' },
  { title: '持仓结构', subtitle: '按价内、价外 / 平值拆分 Call 与 Put 持仓', option: oi.value, empty: '持仓数据不足，暂不展示分布。' },
  { title: 'Max Pain 曲线', subtitle: '不同行权价下的到期内在价值总额', option: pain.value, empty: '持仓数据不足或曲线不可用，暂不展示 Max Pain。' },
  { title: 'Gamma 敞口', subtitle: '按行权价展示正负净 Gamma 敞口', option: gamma.value, empty: '暂无足够的持仓及 Gamma 数据。' },
])
</script>

<template>
  <div class="options-summary">
    <div class="options-meta"><span>{{ data.source || '期权链' }} <span class="meta-dot">/</span> 到期日 <b>{{ data.expiration || '—' }}</b></span><span class="expiry-badge">剩余 {{ number(data.days_to_exp) }} 天</span></div>
    <p v-if="data.source_asof" class="source-asof">数据快照 {{ data.source_asof }} · 来源时间 · 延迟行情，持仓非实时</p>
    <div class="option-stats">
      <div class="option-stat"><span>标的现价</span><strong>{{ price(data.spot) }}</strong><small>本次期权链快照</small></div>
      <div class="option-stat"><span>Max Pain</span><strong>{{ thin ? '—' : price(data.max_pain?.strike) }}</strong><small>{{ thin ? '持仓不足，暂不可用' : '到期内在价值总额最低点' }}</small></div>
      <div class="option-stat call"><span>Call IV</span><strong>{{ percent(data.iv?.call_iv) }}</strong><small>看涨期权隐含波动率</small></div>
      <div class="option-stat put"><span>Put IV</span><strong>{{ percent(data.iv?.put_iv) }}</strong><small>看跌期权隐含波动率</small></div>
      <div class="option-stat"><span>Put / Call 持仓比</span><strong>{{ ratio }}</strong><small>Call {{ number(data.diagnostics?.total_call_oi) }} · Put {{ number(data.diagnostics?.total_put_oi) }}</small></div>
    </div>
    <div class="expected-range">
      <div class="range-heading"><h3>隐含预期区间</h3><span>{{ data.iv?.skew_note || '暂无偏斜说明' }}</span></div>
      <template v-if="rangeValid">
        <div class="range-values"><div><small>预期下沿</small><strong>{{ price(range.expected_low) }}</strong><span>{{ move(range.expected_low) }}</span></div><div class="range-middle"><small>标的现价</small><strong>{{ price(data.spot) }}</strong></div><div><small>预期上沿</small><strong>{{ price(range.expected_high) }}</strong><span>{{ move(range.expected_high) }}</span></div></div>
        <div class="range-track" role="img" :aria-label="`预期区间 ${price(range.expected_low)} 至 ${price(range.expected_high)}，现价 ${price(data.spot)}`"><i :style="{ left: `${spotPosition}%` }"></i></div>
        <p class="range-caption">基于隐含波动率估算至到期日的价格区间。</p>
      </template><p v-else class="sub">暂无有效的隐含预期区间。</p>
    </div>
    <div v-if="thin" class="data-notice">持仓数据不足：Max Pain、持仓结构及 Gamma 图表暂不可用；有有效 IV 时仍展示波动率曲线。</div>
    <div class="option-chart-grid"><article v-for="item in charts" :key="item.title" class="option-chart-card"><h3>{{ item.title }}</h3><p class="chart-subtitle">{{ item.subtitle }}</p><MacroChart v-if="item.option" :option="item.option" height="280px" /><div v-else class="chart-empty"><span class="empty-symbol" aria-hidden="true">▥</span><p>{{ item.empty }}</p></div></article></div>
    <details v-if="data.warnings?.length" class="option-warnings"><summary>数据说明与限制 · {{ data.warnings.length }} 条</summary><ul><li v-for="warning in data.warnings" :key="warning">{{ warning }}</li></ul></details>
  </div>
</template>

<style scoped>
.source-asof { color: var(--muted); font-size: 12px; margin: 10px 0 0; }
.options-summary { margin-top: 16px; }
.options-meta { display: flex; justify-content: space-between; gap: 12px; align-items: center; color: var(--muted); font-size: 13px; flex-wrap: wrap; }
.options-meta b { color: var(--text); font-weight: 500; }.meta-dot { padding: 0 12px; opacity: .5; }.expiry-badge { padding: 6px 10px; border-radius: 20px; color: #a9bbef; background: rgba(121,160,255,.1); }
.option-stats { display: grid; grid-template-columns: repeat(5,minmax(0,1fr)); gap: 12px; margin: 18px 0; }
.option-stat { padding: 16px; background: var(--bg-elev, #0e151e); border: 1px solid var(--line); border-radius: 12px; min-width: 0; }
.option-stat span,.option-stat small { display: block; color: var(--muted); font-size: 12px; }.option-stat strong { display: block; margin: 10px 0; font-size: clamp(20px,2vw,28px); font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }.option-stat small { line-height: 1.6; }.call strong { color: #79a0ff; }.put strong { color: #ed89b5; }
.expected-range { padding: 20px 24px; border: 1px solid rgba(121,160,255,.2); background: linear-gradient(120deg,rgba(121,160,255,.07),rgba(77,214,173,.04)); border-radius: 14px; }
h3 { margin: 0; font-size: 15px; }.range-heading { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }.range-heading span { font-size: 12px; color: var(--muted); }
.range-values { display: flex; justify-content: space-between; margin: 24px 0 16px; gap: 12px; }.range-values div:last-child { text-align: right; }.range-values small { display: block; color: var(--muted); font-size: 12px; }.range-values strong { display: block; font-size: 22px; margin: 6px 0; font-variant-numeric: tabular-nums; }.range-values span { color: var(--muted); font-size: 12px; }.range-middle { text-align: center; }
.range-track { height: 8px; border-radius: 8px; background: linear-gradient(90deg,#8574c7,#7e9ff0,#52c6b7); position: relative; margin: 0 5px; }.range-track i { position: absolute; top: -4px; width: 4px; height: 16px; border-radius: 4px; background: #fff; transform: translateX(-50%); box-shadow: 0 0 0 4px rgba(255,255,255,.1); }.range-caption { color: var(--muted); font-size: 12px; margin: 16px 0 0; }
.data-notice { color: #d9b879; background: rgba(217,184,121,.07); border-left: 3px solid #b59762; border-radius: 6px; padding: 12px 16px; font-size: 13px; line-height: 1.6; margin-top: 16px; }
.option-chart-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 16px; margin-top: 18px; }.option-chart-card { padding: 20px 16px 8px; border: 1px solid var(--line); border-radius: 14px; background: rgba(5,12,20,.2); min-width: 0; }.chart-subtitle { font-size: 12px; color: var(--muted); margin: 8px 0 16px; }.chart-empty { min-height: 280px; display: flex; flex-direction: column; justify-content: center; align-items: center; text-align: center; gap: 10px; color: var(--muted); font-size: 13px; }.empty-symbol { font-size: 32px; opacity: .35; }.chart-empty p { max-width: 280px; line-height: 1.7; }
.option-warnings { margin-top: 16px; font-size: 12px; color: var(--muted); line-height: 1.8; }.option-warnings summary { cursor: pointer; padding: 8px 0; }.option-warnings li { margin-bottom: 6px; }
@media(max-width: 1100px) { .option-stats { grid-template-columns: repeat(3,minmax(0,1fr)); } }
@media(max-width: 760px) { .option-chart-grid { grid-template-columns: 1fr; }.option-stats { grid-template-columns: repeat(2,minmax(0,1fr)); }.option-stat:last-child { grid-column: 1 / -1; }.range-values strong { font-size: 16px; }.expected-range { padding: 16px; } }
</style>
