<script setup>
import { computed } from 'vue'
import { parseJournal, toneOfLabel } from '../journalParse'
import MacroChart from './MacroChart.vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  row: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue'])

const open = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const parsed = computed(() => parseJournal(props.row?.content || ''))

const AXIS_LABEL = { color: '#8b9bb0', fontSize: 11 }
const SPLIT_LINE = { lineStyle: { color: 'rgba(148,176,210,0.08)' } }
const TOOLTIP = {
  backgroundColor: '#101820',
  borderColor: 'rgba(148,176,210,0.2)',
  textStyle: { color: '#e9f0f7' },
}

function deltaTone(delta) {
  const s = String(delta || '').trim()
  if (s.startsWith('+') && !/^\+0(\.0+)?%?$/.test(s)) return 'up'
  if (s.startsWith('-') && !/^-0(\.0+)?%?$/.test(s)) return 'down'
  return 'sub'
}

function barHeight(n) {
  return Math.max(200, n * 30 + 40) + 'px'
}

const gaugeOption = computed(() => {
  const f = parsed.value.fear
  if (f?.composite == null) return null
  return {
    backgroundColor: 'transparent',
    series: [{
      type: 'gauge',
      min: 0,
      max: 100,
      startAngle: 200,
      endAngle: -20,
      radius: '95%',
      center: ['50%', '62%'],
      axisLine: {
        lineStyle: {
          width: 14,
          color: [[0.25, '#e85d75'], [0.45, '#f0a35e'], [0.55, '#8b9bb0'], [0.75, '#9fd67a'], [1, '#3ecf8e']],
        },
      },
      pointer: { width: 4, length: '50%', itemStyle: { color: '#e9f0f7' } },
      axisTick: { show: false },
      splitLine: { length: 8, lineStyle: { color: '#0b0f14', width: 2 } },
      axisLabel: { color: '#8b9bb0', fontSize: 10, distance: 18 },
      title: { offsetCenter: [0, '58%'], color: '#8b9bb0', fontSize: 12 },
      detail: {
        offsetCenter: [0, '32%'],
        fontSize: 26,
        fontWeight: 600,
        color: '#e9f0f7',
        formatter: (v) => v.toFixed(1),
      },
      data: [{ value: f.composite, name: `六项等权 · ${f.compositeLabel || '—'}` }],
    }],
  }
})

const sectorPcrOption = computed(() => {
  const rows = parsed.value.derivatives?.sectors
  if (!rows?.length) return null
  const labels = rows.map((r) => `${r.symbol} ${r.name}`)
  return {
    backgroundColor: 'transparent',
    tooltip: {
      ...TOOLTIP,
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (ps) => {
        const r = rows[ps[0].dataIndex]
        return `${r.symbol}(${r.name})<br/>Put/Call ${r.pcr?.toFixed(2) ?? '—'}<br/>Call ${r.call ?? '—'}% · Put ${r.put ?? '—'}%`
      },
    },
    legend: { top: 0, textStyle: { color: '#8b9bb0' }, data: ['Call', 'Put'] },
    grid: { left: 110, right: 70, top: 28, bottom: 24 },
    xAxis: { type: 'value', max: 100, axisLabel: { ...AXIS_LABEL, formatter: '{value}%' }, splitLine: SPLIT_LINE },
    yAxis: { type: 'category', data: labels, inverse: true, axisLabel: AXIS_LABEL },
    series: [
      {
        name: 'Call', type: 'bar', stack: 'share', barMaxWidth: 16,
        itemStyle: { color: '#3ecf8e' },
        data: rows.map((r) => r.call ?? 0),
      },
      {
        name: 'Put', type: 'bar', stack: 'share', barMaxWidth: 16,
        itemStyle: { color: '#e85d75' },
        data: rows.map((r) => r.put ?? 0),
        label: {
          show: true,
          position: 'right',
          color: '#8b9bb0',
          fontSize: 11,
          formatter: (p) => `P/C ${rows[p.dataIndex].pcr?.toFixed(2) ?? '—'}`,
        },
      },
    ],
  }
})

const sectorChangeOption = computed(() => {
  const rows = parsed.value.sector?.rows
  if (!rows?.length) return null
  return {
    backgroundColor: 'transparent',
    tooltip: {
      ...TOOLTIP,
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (ps) => {
        const r = rows[ps[0].dataIndex]
        return `${r.sector}：${r.pct > 0 ? '+' : ''}${r.pct.toFixed(2)}%（${r.count} 支）`
      },
    },
    grid: { left: 80, right: 60, top: 12, bottom: 24 },
    xAxis: { type: 'value', axisLabel: { ...AXIS_LABEL, formatter: '{value}%' }, splitLine: SPLIT_LINE },
    yAxis: { type: 'category', data: rows.map((r) => r.sector), inverse: true, axisLabel: AXIS_LABEL },
    series: [{
      type: 'bar',
      barMaxWidth: 16,
      data: rows.map((r) => ({ value: r.pct, itemStyle: { color: r.pct >= 0 ? '#3ecf8e' : '#e85d75' } })),
      label: {
        show: true,
        position: 'right',
        color: '#e9f0f7',
        fontSize: 11,
        formatter: (p) => `${p.value > 0 ? '+' : ''}${Number(p.value).toFixed(2)}%`,
      },
    }],
  }
})
</script>

<template>
  <el-dialog
    v-model="open"
    :title="`日志图表 · ${row?.log_date || ''}`"
    width="960px"
    top="4vh"
    destroy-on-close
    append-to-body
  >
    <p class="sub" v-if="!parsed.fear && !parsed.derivatives && !parsed.sector">
      这条日志里没有【恐慌指标】【衍生品三层】【行业分析】这三块内容，从恐慌指标页、股票列表分析页点「加入日志」即可写入。
    </p>

    <section class="panel jc-panel" v-if="parsed.fear">
      <h2>1. 恐慌指标 <span class="sub">{{ parsed.fear.date }}</span></h2>
      <div class="jc-fear">
        <MacroChart v-if="gaugeOption" :option="gaugeOption" height="220px" class="jc-gauge" />
        <div class="price-grid jc-cards">
          <div class="price-card" v-for="c in parsed.fear.cards" :key="c.title">
            <span>{{ c.title }}</span>
            <b>{{ c.value }} <small :class="toneOfLabel(c.label)">{{ c.label }}</small></b>
            <small v-if="c.delta" :class="deltaTone(c.delta)">较上期 {{ c.delta }}</small>
          </div>
        </div>
      </div>
    </section>

    <section class="panel jc-panel" v-if="parsed.derivatives">
      <h2>2. 衍生品三层</h2>
      <div class="price-grid">
        <div class="price-card" v-for="c in parsed.derivatives.cards" :key="c.title">
          <span>{{ c.title }}</span>
          <b>{{ c.value }} <small :class="toneOfLabel(c.label)">{{ c.label }}</small></b>
        </div>
      </div>
      <MacroChart
        v-if="sectorPcrOption"
        :option="sectorPcrOption"
        :height="barHeight(parsed.derivatives.sectors.length)"
        style="margin-top: 10px"
      />
    </section>

    <section class="panel jc-panel" v-if="parsed.sector">
      <h2>3. 行业分析 <span class="sub">{{ parsed.sector.period }}</span></h2>
      <MacroChart :option="sectorChangeOption" :height="barHeight(parsed.sector.rows.length)" />
    </section>
  </el-dialog>
</template>

<style scoped>
.jc-panel + .jc-panel { margin-top: 12px; }
.jc-panel h2 { display: flex; align-items: baseline; gap: 8px; }
.jc-fear {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 12px;
  align-items: center;
}
.jc-cards { margin-top: 0; grid-template-columns: repeat(3, minmax(0, 1fr)); }
.price-card small { font-size: 11px; font-weight: 500; }
.price-card > small { display: block; margin-top: 4px; }
@media (max-width: 760px) {
  .jc-fear { grid-template-columns: 1fr; }
  .jc-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
