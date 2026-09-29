<script setup>
import { ref } from 'vue'
import { useChart } from '../useChart'

const props = defineProps({
  curve: { type: Array, default: () => [] },
})

const el = ref(null)
useChart(el, () => props.curve, (curve) => ({
  backgroundColor: 'transparent',
  tooltip: {
    trigger: 'axis',
    backgroundColor: '#101820',
    borderColor: 'rgba(148,176,210,0.2)',
    textStyle: { color: '#e9f0f7' },
  },
  grid: { left: 52, right: 18, top: 28, bottom: 32 },
  xAxis: {
    type: 'category',
    data: curve.map((d) => d.date.slice(5)),
    axisLine: { lineStyle: { color: 'rgba(148,176,210,0.2)' } },
    axisLabel: { color: '#8b9bb0' },
  },
  yAxis: {
    type: 'value',
    splitLine: { lineStyle: { color: 'rgba(148,176,210,0.08)' } },
    axisLabel: { color: '#8b9bb0' },
  },
  series: [
    {
      name: '当周盈亏',
      type: 'bar',
      data: curve.map((d) => ({
        value: d.daily_pnl,
        itemStyle: { color: d.daily_pnl >= 0 ? '#3ee0a0' : '#ff6b7a' },
      })),
    },
  ],
}))
</script>

<template>
  <div ref="el" class="chart"></div>
</template>
